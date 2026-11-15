"""Vector search in the same SQLite file, using the sqlite-vec extension.

Each vector table is a vec0 virtual table: `id` is the primary key of the row it describes,
`embedding` is a float32 vector and every row carries metadata that a search can filter on: a
`created_at` timestamp on every table, plus any extra columns the table declares. Add a table by
adding it to VECTOR_TABLES; init_db creates it next to the model tables, and the fingerprint
covers it, so changing a column carries the rows over like any other schema change, while
changing a dimension stops the start: vectors of another model cannot be compared with the
stored ones (docs/adr/0003-carry-rows-over.md).

Search is exact (brute force over every vector that passes the filters), which is fine up to some
hundreds of thousands of vectors: a 768-wide float32 vector is 3 KB.
"""

import json
import struct
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlite_vec
from sqlalchemy.ext.asyncio import AsyncConnection


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


# 768 is what EmbeddingGemma 2 produces (see memory/docs/decisions.md). note_embedding is for the
# example Note feature, so delete it together with Note. To keep more than a timestamp, declare it,
# e.g. VectorTable(768, {"source": "TEXT", "frame": "INTEGER"}).
VECTOR_TABLES: dict[str, VectorTable] = {
    "note_embedding": VectorTable(768),
    # One row per Memory (app/models.py), same id. The width must be EMBED_MODEL's: if the model
    # changes, change it here; the next start stops until the old vectors are wiped. kind,
    # collection and owner (Memory's owner_id) are copied here so a search can filter on them;
    # owner keeps users apart (app/auth.py).
    "memory_embedding": VectorTable(
        768, {"kind": "TEXT", "collection": "TEXT", "owner": "INTEGER"}
    ),
    # One row per Trigger, same id: its watch_for embedding.
    "trigger_embedding": VectorTable(768),
}


async def load_extension(driver_connection) -> None:
    """Load sqlite-vec into an aiosqlite connection (app/db.py runs it on every new one)."""
    await driver_connection.enable_load_extension(True)
    await driver_connection.load_extension(sqlite_vec.loadable_path())
    await driver_connection.enable_load_extension(False)


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


async def create_tables(connection: AsyncConnection) -> None:
    for name, table in VECTOR_TABLES.items():
        await connection.exec_driver_sql(_create_statement(name, table))


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


async def upsert(
    connection: AsyncConnection,
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
    await connection.exec_driver_sql(f"DELETE FROM {table} WHERE id = ?", (id,))
    await connection.exec_driver_sql(
        f"INSERT INTO {table}({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


async def delete(connection: AsyncConnection, table: str, id: int) -> None:
    _table(table)
    await connection.exec_driver_sql(f"DELETE FROM {table} WHERE id = ?", (id,))


async def nearest(
    connection: AsyncConnection,
    table: str,
    vector: Sequence[float],
    k: int = 10,
    since: datetime | None = None,
    until: datetime | None = None,
    where: Mapping[str, Any] | None = None,
    ids: Collection[int] | None = None,
) -> list[Hit]:
    """The k closest rows, closest first, among those that pass the filters.

    `since` and `until` bound created_at (since inclusive, until exclusive, both timezone-aware);
    `where` keeps only rows whose extra metadata columns equal the given values. A list, tuple or
    set as a value keeps rows equal to any of them (an empty one keeps none). `ids` keeps only
    those rows (an empty one keeps none): sqlite-vec picks the k closest among them, rather than
    dropping the others from the k closest.
    """
    blob = _serialize(table, vector)
    extra_columns = list(_table(table).metadata)
    conditions, params = ["embedding MATCH ?", "k = ?"], [blob, k]
    if ids is not None:
        if not ids:
            return []
        # A subquery, not a list: SQLite turns `id IN (?)` with one value into `id = ?`, which
        # sqlite-vec (0.1.9) applies only after taking the k closest, where an IN goes into the
        # search itself.
        conditions.append("id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(list(ids)))
    if since is not None:
        conditions.append("created_at >= ?")
        params.append(_to_unix(since))
    if until is not None:
        conditions.append("created_at < ?")
        params.append(_to_unix(until))
    for column, value in _extra(table, where).items():
        if isinstance(value, list | tuple | set | frozenset):
            if not value:
                return []
            conditions.append(f"{column} IN ({', '.join('?' * len(value))})")
            params.extend(value)
        else:
            conditions.append(f"{column} = ?")
            params.append(value)

    columns = ", ".join(["id", "distance", "created_at", *extra_columns])
    rows = await connection.exec_driver_sql(
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


async def stored(
    connection: AsyncConnection, table: str, ids: Collection[int] | None = None
) -> dict[int, list[float]]:
    """The stored vectors by id: those of `ids`, or every row (a full scan, for maps)."""
    _table(table)
    if ids is None:
        rows = await connection.exec_driver_sql(f"SELECT id, embedding FROM {table}")
    elif not ids:
        return {}  # SQL has no empty IN ()
    else:
        wanted = tuple(set(ids))
        rows = await connection.exec_driver_sql(
            f"SELECT id, embedding FROM {table} WHERE id IN ({', '.join('?' * len(wanted))})",
            wanted,
        )
    return {id: list(struct.unpack(f"<{len(blob) // 4}f", blob)) for id, blob in rows}
