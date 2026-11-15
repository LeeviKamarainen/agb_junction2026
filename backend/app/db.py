"""The database: SQLite through aiosqlite, so reads and writes are coroutines.

Every query is awaited (`await session.exec(...)`, `await session.commit()`); aiosqlite runs it
on one thread per connection, so the event loop keeps serving other requests meanwhile. SQLite
still allows one writer at a time: WAL lets reads go on during a write, and a second write waits
up to busy_timeout. Sessions keep their objects after commit (expire_on_commit=False), because an
async session cannot reload an expired attribute lazily.

There are no migrations: when the schema changes, init_db copies the rows into a database built
from the current models (docs/adr/0003-carry-rows-over.md).
"""

import hashlib
import logging
import os
import re
import sqlite3
import urllib.parse
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import aiosqlite
from fastapi import Depends
from sqlalchemy import Table, event
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app import models, vectors  # noqa: F401  (importing models registers their tables)
from app.settings import get_settings

log = logging.getLogger(__name__)

# Old databases kept beside the new one after schema changes. An assumption: a carry-over that
# lost something is noticed within a few schema-changing deploys, and the schema changed about
# ten times in the first two days, so five reach back about a day. Each is a whole database, but
# images live in the media folder, not in it.
BACKUPS_KEPT = 5
# Beside a database: its write-ahead log, the log's shared-memory index, and a rollback journal.
SIDECARS = ("-wal", "-shm", "-journal")


@lru_cache
def get_engine() -> AsyncEngine:
    path = get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    event.listen(engine.sync_engine, "connect", _configure_connection)
    return engine


def new_session() -> AsyncSession:
    """A session of its own, for work outside a request: `async with new_session() as s:`."""
    return AsyncSession(get_engine(), expire_on_commit=False)


def _configure_connection(dbapi_connection, connection_record) -> None:
    _load_vectors(dbapi_connection, connection_record)
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _load_vectors(dbapi_connection, _connection_record) -> None:
    # The aiosqlite connection's own methods are coroutines; run_async awaits one from here.
    dbapi_connection.run_async(vectors.load_extension)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with new_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def fingerprint(dialect: Dialect) -> int:
    """32-bit hash of the table definitions, kept in PRAGMA user_version."""
    ddl = "".join(
        str(CreateTable(t).compile(dialect=dialect)) for t in SQLModel.metadata.sorted_tables
    )
    digest = hashlib.sha256((ddl + vectors.ddl()).encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


class CarryOverError(RuntimeError):
    """The rows cannot all go to the new schema: the start stops and the old file stays."""


async def init_db() -> None:
    """Create the database, or carry its rows over when the models or vector tables changed.

    The new database is built in a file of its own and only then put in place of the old one, so
    whatever fails leaves the old file as it was, for the previous version of the app to find
    after a revert (docs/adr/0003-carry-rows-over.md). Nothing else may open the database
    meanwhile: SQLite finds a database's WAL by its name, so a connection kept open across the
    swap would mix the old file's pages into the new one.
    """
    engine = get_engine()
    expected = fingerprint(engine.dialect)
    path = get_settings().database_path
    new = path.with_name(f"{path.name}.new")
    if not path.exists():
        log.warning("Creating an empty database %s", path)
        await _build(new, expected, engine.dialect)
        for leftover in _sidecars(path):  # SQLite would replay them onto the new database
            log.warning("Removing %s, left behind by a database that is gone", leftover)
            leftover.unlink()
        new.replace(path)
        return

    async with engine.connect() as connection:
        current = (await connection.exec_driver_sql("PRAGMA user_version")).scalar()
        if current == expected:
            return
        # Moves every committed write into the file itself, which is all the copy reads.
        await connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    await engine.dispose()
    _check_unused(path)
    checked = path.stat()
    carried = await _build(new, expected, engine.dialect, old=path)
    backup = path.with_name(f"{path.name}.{datetime.now(UTC):%Y%m%dT%H%M%SZ}.{current}.bak")
    # A second name for the old file, then one atomic rename onto the first: at every moment the
    # database's name holds either the whole old or the whole new database.
    try:
        _check_unused(path, checked)
        os.link(path, backup)
    except BaseException:
        _remove(new)
        raise
    try:
        new.replace(path)
    except BaseException:
        backup.unlink()  # still a second name for the database itself, not a backup
        _remove(new)
        raise
    log.warning(
        "The schema changed: carried the rows of %s over (%s)%s%s; the old file is kept as %s",
        path,
        ", ".join(f"{table} {rows}" for table, rows in carried.rows.items()),
        f"; new columns: {', '.join(carried.added)}" if carried.added else "",
        f"; dropped: {', '.join(carried.dropped)}" if carried.dropped else "",
        backup,
    )
    _prune_backups(path)


def _check_unused(path: Path, checked: os.stat_result | None = None) -> None:
    """Stop unless nothing else has the database open, nor opened or replaced it since `checked`.

    The last connection to close a database deletes its -wal and -shm, so once this process has
    closed its own, either of them means another connection.
    """
    replaced = checked is not None and _identity(path.stat()) != _identity(checked)
    if replaced or _sidecars(path):
        raise CarryOverError(
            f"The schema changed, but something else has {path} open, or opened or replaced it "
            "during the start, so its rows cannot be carried over safely; the file stays as it "
            "is. Stop whatever else uses it and start again."
        )


def _identity(stat: os.stat_result) -> tuple[int, ...]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _sidecars(database: Path) -> list[Path]:
    """The files SQLite keeps beside a database while it is open or after a crash."""
    beside = (Path(f"{database}{suffix}") for suffix in SIDECARS)
    return [path for path in beside if path.exists()]


@dataclass
class _CarryOver:
    """What a schema change does with the old database's rows, worked out before any copy."""

    # Per table: the INSERT ... SELECT that copies it and its parameters (the new columns' values).
    copies: dict[str, tuple[str, tuple[Any, ...]]] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)  # per table, in the old database
    added: list[str] = field(default_factory=list)  # columns only the new schema has
    dropped: list[str] = field(default_factory=list)  # tables and columns only the old one has
    problems: list[str] = field(default_factory=list)  # what cannot be carried, and what to do


async def _build(new: Path, expected: int, dialect: Dialect, old: Path | None = None) -> _CarryOver:
    """Write the current schema to `new`, with the rows of `old` when given.

    Removes `new` again on any failure, so only a complete database is ever moved into place.
    """
    _remove(new)  # left behind by a start that was killed half-way
    try:
        await _create_schema(new)
        async with aiosqlite.connect(new, uri=True, isolation_level=None) as connection:
            carried = await _carry_over(connection, old, dialect) if old else _CarryOver()
            await connection.execute(f"PRAGMA main.user_version = {expected}")
        return carried
    except BaseException:
        _remove(new)
        raise


async def _create_schema(path: Path) -> None:
    # Without _configure_connection's WAL, the finished database is this one file, ready to move.
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    event.listen(engine.sync_engine, "connect", _load_vectors)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
            await vectors.create_tables(connection)
    finally:
        await engine.dispose()


async def _carry_over(connection: aiosqlite.Connection, old: Path, dialect: Dialect) -> _CarryOver:
    """Copy the rows of `old` into the new database `connection` is open on, in one transaction."""
    await vectors.load_extension(connection)  # the old vec0 tables are read through it too
    # Read-only, so nothing here can change the old file; immutable, so SQLite puts no -wal or
    # -shm beside it, which is safe because init_db saw that it has no WAL left to read.
    uri = f"file:{urllib.parse.quote(str(old))}?mode=ro&immutable=1"
    await connection.execute("ATTACH DATABASE ? AS old", (uri,))
    carried = await _plan(connection, dialect)
    if carried.problems:
        raise CarryOverError(
            f"The schema changed, but the rows of {old} cannot all be carried over to it, so the "
            "start stops and the file stays as it was:\n"
            + "".join(f"- {problem}\n" for problem in carried.problems)
            + "Deploy again once that is fixed. To give up this data instead, make prod-reset-db "
            "(locally make reset-db) deletes it for good, as no backup of it exists: copy the "
            "file first if it may be needed. See docs/adr/0003-carry-rows-over.md."
        )
    # Off during the copy and checked after it, so the error names every table with rows that
    # refer to nothing, where an enforced key would stop at the first such row.
    await connection.execute("PRAGMA foreign_keys = OFF")
    await connection.execute("BEGIN")
    for table, (statement, parameters) in carried.copies.items():
        try:
            cursor = await connection.execute(statement, parameters)
        except sqlite3.Error as error:  # a constraint the old rows break, or a full disk...
            broken = isinstance(error, sqlite3.IntegrityError)
            raise CarryOverError(
                f"The schema changed, but copying {table} from {old} failed: {error}. "
                + (
                    "Fix those rows in the old file or relax the constraint in app/models.py. "
                    if broken
                    else ""
                )
                + "The start stops and the file stays as it was. "
                "See docs/adr/0003-carry-rows-over.md."
            ) from error
        if cursor.rowcount != carried.rows[table]:
            raise CarryOverError(
                f"Copying {table} from {old} wrote {cursor.rowcount} of its "
                f"{carried.rows[table]} rows; the start stops and the file stays as it was"
            )
    broken = Counter(
        (table, parent)
        for table, _rowid, parent, _key in await connection.execute_fetchall(
            "PRAGMA main.foreign_key_check"
        )
    )
    if broken:
        raise CarryOverError(
            f"The schema changed, but rows of {old} refer to rows that do not exist ("
            + ", ".join(
                f"{_rows(n)} of {table} to {parent}" for (table, parent), n in broken.items()
            )
            + "): delete them from the old file or drop the foreign key in app/models.py. The "
            "start stops and the file stays as it was. See docs/adr/0003-carry-rows-over.md."
        )
    await connection.execute("COMMIT")
    return carried


async def _plan(connection: aiosqlite.Connection, dialect: Dialect) -> _CarryOver:
    carried = _CarryOver()
    old_tables: dict[str, str | None] = dict(
        await connection.execute_fetchall(
            "SELECT name, sql FROM old.sqlite_master WHERE type = 'table'"
        )
    )
    # Parents before children, the order enforced foreign keys would need.
    for table in SQLModel.metadata.sorted_tables:
        if table.name in old_tables:
            await _plan_table(connection, carried, table, dialect)
    for name in vectors.VECTOR_TABLES:
        if name in old_tables:
            await _plan_vectors(connection, carried, name, old_tables[name] or "")

    current = SQLModel.metadata.tables.keys() | vectors.VECTOR_TABLES.keys()
    virtual = [name for name, sql in old_tables.items() if _vec0_columns(sql or "") is not None]
    carried.dropped += [
        name
        for name in old_tables
        if name not in current
        and not name.startswith("sqlite_")  # SQLite's own
        and not any(name.startswith(f"{table}_") for table in virtual)  # vec0's storage
    ]
    return carried


async def _plan_table(
    connection: aiosqlite.Connection, carried: _CarryOver, table: Table, dialect: Dialect
) -> None:
    old = await _declared_types(connection, "old", table.name)
    new = await _declared_types(connection, "main", table.name)
    rows = carried.rows[table.name] = await _count(connection, table.name)
    columns, values, parameters = [], [], []
    for column in table.columns:
        name = f"{table.name}.{column.name}"
        if column.name in old:
            # Length aside (VARCHAR(60) is VARCHAR(100) to SQLite): SQLite would store a value of
            # another type as it is, and the app would read back what the model does not expect.
            if rows and _type_name(old[column.name]) != _type_name(new[column.name]):
                carried.problems.append(
                    f"{name} was {old[column.name]} and is now {new[column.name]}, and "
                    f"{table.name} has {_rows(rows)} whose values may not fit: keep its type, or "
                    "rename it so that it starts from its default"
                )
            columns.append(column.name)
            values.append(_quote(column.name))
            continue
        carried.added.append(name)
        default = column.default
        if column.server_default is not None:
            pass  # left out of the INSERT, so SQLite writes the new table's DEFAULT
        elif default is not None and default.is_scalar and default.arg is not None:
            # SQLite's own type: the generic DateTime has no processor and would skip SQLAlchemy's
            # storage format.
            process = column.type.dialect_impl(dialect).bind_processor(dialect)
            columns.append(column.name)
            values.append("?")
            parameters.append(process(default.arg) if process else default.arg)
        elif column.nullable:
            pass  # left out of the INSERT, so NULL
        elif rows:
            factory = default is not None and default.is_callable
            carried.problems.append(
                f"{name} is new and NOT NULL without a fixed default"
                + (" (a default_factory is not run for old rows)" if factory else "")
                + f", and {table.name} has {_rows(rows)} with no value for it: give it a "
                "default or make it optional (`| None`) in app/models.py"
            )
    carried.dropped += [f"{table.name}.{column}" for column in old if column not in table.c]
    carried.copies[table.name] = (
        f"INSERT INTO main.{_quote(table.name)} ({', '.join(map(_quote, columns))}) "
        f"SELECT {', '.join(values)} FROM old.{_quote(table.name)}",
        tuple(parameters),
    )


async def _plan_vectors(
    connection: aiosqlite.Connection, carried: _CarryOver, name: str, old_sql: str
) -> None:
    ((new_sql,),) = await connection.execute_fetchall(
        "SELECT sql FROM main.sqlite_master WHERE name = ?", (name,)
    )
    old, new = _vec0_columns(old_sql), _vec0_columns(new_sql)
    assert new is not None, new_sql  # made by vectors.create_tables a moment ago
    rows = carried.rows[name] = await _count(connection, name)
    if old is None:
        carried.problems.append(f"{name} is not a vec0 table in the old file: rename one of them")
        return
    for column, type_ in new.items():
        if column not in old:
            carried.added.append(f"{name}.{column}")
            if rows:
                carried.problems.append(
                    f"{name}.{column} is new and vec0 cannot leave it NULL, and {name} has "
                    f"{_rows(rows)} with no value for it: take it out of vectors.VECTOR_TABLES"
                )
        elif rows and old[column] != type_ and column == "embedding":
            carried.problems.append(
                f"{name} holds {old[column]} vectors and the new schema {type_}: the stored ones "
                f"({_rows(rows)}) come from another embedding model and cannot be compared with "
                "new ones. Keep the old width, or start over with the new model by giving up "
                "this data (below); published demo clips come back and are embedded again"
            )
        elif rows and old[column] != type_:
            carried.problems.append(
                f"{name}.{column} was {old[column]} and is now {type_}, which vec0 cannot take "
                f"for its {_rows(rows)}: keep its type"
            )
    shared = ", ".join(_quote(column) for column in new if column in old)
    carried.dropped += [f"{name}.{column}" for column in old if column not in new]
    carried.copies[name] = (
        f"INSERT INTO main.{_quote(name)} ({shared}) SELECT {shared} FROM old.{_quote(name)}",
        (),
    )


async def _declared_types(
    connection: aiosqlite.Connection, schema: str, table: str
) -> dict[str, str]:
    """Column name -> declared type, e.g. VARCHAR(60)."""
    return dict(
        await connection.execute_fetchall(
            "SELECT name, type FROM pragma_table_info(?, ?)", (table, schema)
        )
    )


async def _count(connection: aiosqlite.Connection, table: str) -> int:
    ((rows,),) = await connection.execute_fetchall(f"SELECT count(*) FROM old.{_quote(table)}")
    return rows


def _type_name(declared: str) -> str:
    return declared.split("(")[0].strip().upper()


def _rows(count: int) -> str:
    return "1 row" if count == 1 else f"{count} rows"


def _vec0_columns(sql: str) -> dict[str, str] | None:
    """Column name -> type (FLOAT[768], TEXT...) of a vec0 table, from its CREATE statement.

    PRAGMA table_info gives no types for a virtual table, so its statement is the only record of
    an old table's vector width.
    """
    match = re.search(r"USING\s+vec0\s*\((.*)\)\s*$", sql, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    definitions = (part.split() for part in match[1].split(","))
    # A table option such as chunk_size=8 is one word: it describes no column.
    return {words[0]: words[1].upper() for words in definitions if len(words) > 1}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _remove(database: Path) -> None:
    for suffix in ("", *SIDECARS):
        Path(f"{database}{suffix}").unlink(missing_ok=True)


def _prune_backups(path: Path) -> None:
    named = re.compile(rf"{re.escape(path.name)}\.\d{{8}}T\d{{6}}Z\.-?\d+\.bak")
    # The time comes first in the name, so sorting by name puts the oldest first.
    backups = sorted(p for p in path.parent.iterdir() if named.fullmatch(p.name))
    for stale in backups[:-BACKUPS_KEPT]:
        stale.unlink()
