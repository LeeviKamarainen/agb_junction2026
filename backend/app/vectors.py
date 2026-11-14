"""Vector search in the same SQLite file, using the sqlite-vec extension.

Each vector table is a vec0 virtual table: `id` is the primary key of the row it describes,
`embedding` is a float32 vector and every row carries metadata that a search can filter on: a
`created_at` timestamp on every table, plus any extra columns the table declares. Add a table by
adding it to VECTOR_TABLES; init_db creates it next to the model tables, and the fingerprint
covers it, so changing a dimension or a column rebuilds the database like any other schema change
(docs/adr/0001-disposable-database.md).

Search is exact (brute force over every vector that passes the filters), which is fine up to some
hundreds of thousands of vectors: a 512-wide float32 vector is 2 KB.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlite_vec
from sqlalchemy import Connection


@dataclass(frozen=True)
class VectorTable:
    dimensions: int
    # Extra metadata columns beyond created_at: name -> sqlite-vec type (INTEGER, FLOAT, TEXT or
    # BOOLEAN). Metadata is filterable but cannot be NULL, so always give every column a value.
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Hit:
    id: int
    distance: float  # cosine distance: 0 is identical, 1 is unrelated, 2 is opposite
    created_at: datetime
    metadata: dict[str, Any]  # the extra columns only


# 512 is what CLIP ViT-B/32 produces (see memory/docs/decisions.md). note_embedding is for the
# example Note feature, so delete it together with Note. To keep more than a timestamp, declare it,
# e.g. VectorTable(512, {"source": "TEXT", "frame": "INTEGER"}).
VECTOR_TABLES: dict[str, VectorTable] = {
    "note_embedding": VectorTable(512),
    # One row per Memory (app/models.py), same id. kind is copied here so a search can filter on it.
    "memory_embedding": VectorTable(512, {"kind": "TEXT"}),
}


def load_extension(dbapi_connection) -> None:
    """Load sqlite-vec into a raw sqlite3 connection (called for every new connection)."""
    dbapi_connection.enable_load_extension(True)
    sqlite_vec.load(dbapi_connection)
    dbapi_connection.enable_load_extension(False)


def _create_statement(name: str, table: VectorTable) -> str:
    columns = [
        "id INTEGER PRIMARY KEY",
        f"embedding FLOAT[{table.dimensions}] distance_metric=cosine",
        "created_at INTEGER",  # unix seconds, UTC
        *(f"{column} {type_}" for column, type_ in table.metadata.items()),
    ]
    return f"CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING vec0({', '.join(columns)})"


def ddl() -> str:
    """The statements create_tables runs, for the database fingerprint."""
    return ";".join(_create_statement(name, table) for name, table in VECTOR_TABLES.items())


def create_tables(connection: Connection) -> None:
    for name, table in VECTOR_TABLES.items():
        connection.exec_driver_sql(_create_statement(name, table))


def _table(name: str) -> VectorTable:
    if name not in VECTOR_TABLES:  # names go into SQL, so only declared ones get through
        raise ValueError(f"Unknown vector table {name!r}")
    return VECTOR_TABLES[name]


def _serialize(name: str, vector: Sequence[float]) -> bytes:
    dimensions = _table(name).dimensions
    if len(vector) != dimensions:
        raise ValueError(f"{name} holds {dimensions}-wide vectors, got {len(vector)}")
    return sqlite_vec.serialize_float32(list(vector))


def _extra(name: str, values: Mapping[str, Any] | None) -> dict[str, Any]:
    """The extra metadata values, checked against the columns the table declares."""
    values = dict(values or {})
    declared = _table(name).metadata
    if unknown := values.keys() - declared.keys():
        raise ValueError(f"{name} has no metadata column {sorted(unknown)}")
    return values


def _to_unix(moment: datetime) -> int:
    if moment.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware, e.g. datetime.now(UTC)")
    return int(moment.timestamp())


def upsert(
    connection: Connection,
    table: str,
    id: int,
    vector: Sequence[float],
    created_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Store or replace the row for `id`. created_at defaults to now; it must be timezone-aware.

    `metadata` must give a value for every extra column the table declares.
    """
    blob = _serialize(table, vector)
    extra = _extra(table, metadata)
    if missing := _table(table).metadata.keys() - extra.keys():
        raise ValueError(f"{table} needs a value for metadata {sorted(missing)}")
    row = {
        "id": id,
        "embedding": blob,
        "created_at": _to_unix(created_at or datetime.now(UTC)),
        **extra,
    }
    # vec0 has no INSERT OR REPLACE, so replace by deleting first.
    connection.exec_driver_sql(f"DELETE FROM {table} WHERE id = ?", (id,))
    connection.exec_driver_sql(
        f"INSERT INTO {table}({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


def delete(connection: Connection, table: str, id: int) -> None:
    _table(table)
    connection.exec_driver_sql(f"DELETE FROM {table} WHERE id = ?", (id,))


def nearest(
    connection: Connection,
    table: str,
    vector: Sequence[float],
    k: int = 10,
    since: datetime | None = None,
    until: datetime | None = None,
    where: Mapping[str, Any] | None = None,
) -> list[Hit]:
    """The k closest rows, closest first, among those that pass the filters.

    `since` and `until` bound created_at (since inclusive, until exclusive, both timezone-aware);
    `where` keeps only rows whose extra metadata columns equal the given values.
    """
    blob = _serialize(table, vector)
    extra_columns = list(_table(table).metadata)
    conditions, params = ["embedding MATCH ?", "k = ?"], [blob, k]
    if since is not None:
        conditions.append("created_at >= ?")
        params.append(_to_unix(since))
    if until is not None:
        conditions.append("created_at < ?")
        params.append(_to_unix(until))
    for column, value in _extra(table, where).items():
        conditions.append(f"{column} = ?")
        params.append(value)

    columns = ", ".join(["id", "distance", "created_at", *extra_columns])
    rows = connection.exec_driver_sql(
        f"SELECT {columns} FROM {table} WHERE {' AND '.join(conditions)} ORDER BY distance",
        tuple(params),
    )
    return [
        Hit(
            id=row[0],
            distance=row[1],
            created_at=datetime.fromtimestamp(row[2], UTC),
            metadata=dict(zip(extra_columns, row[3:], strict=True)),
        )
        for row in rows
    ]
