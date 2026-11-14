"""The database: SQLite through aiosqlite, so reads and writes are coroutines.

Every query is awaited (`await session.exec(...)`, `await session.commit()`); aiosqlite runs it
on one thread per connection, so the event loop keeps serving other requests meanwhile. SQLite
still allows one writer at a time: WAL lets reads go on during a write, and a second write waits
up to busy_timeout. Sessions keep their objects after commit (expire_on_commit=False), because an
async session cannot reload an expired attribute lazily.
"""

import hashlib
import inspect
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app import models, seed, vectors  # noqa: F401  (importing models registers their tables)
from app.settings import get_settings

log = logging.getLogger(__name__)


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


def _configure_connection(dbapi_connection, _connection_record) -> None:
    # The aiosqlite connection's own methods are coroutines; run_async awaits one from here.
    dbapi_connection.run_async(vectors.load_extension)
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with new_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def fingerprint(dialect: Dialect) -> int:
    """32-bit hash of the table definitions and the seed data, kept in PRAGMA user_version."""
    ddl = "".join(
        str(CreateTable(t).compile(dialect=dialect)) for t in SQLModel.metadata.sorted_tables
    )
    digest = hashlib.sha256((ddl + vectors.ddl() + inspect.getsource(seed)).encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


async def init_db() -> None:
    """Rebuild the database whenever the models or seed data changed.

    The database is disposable (docs/adr/0001-disposable-database.md): there are no
    migrations, so a changed schema means wiping the file and seeding it again.
    """
    engine = get_engine()
    expected = fingerprint(engine.dialect)
    path = get_settings().database_path
    if path.exists():
        async with engine.connect() as connection:
            current = (await connection.exec_driver_sql("PRAGMA user_version")).scalar()
        if current == expected:
            return
        log.warning("Models or seed data changed: wiping %s and re-seeding", path)
        await engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    else:
        log.warning("Creating database %s from the models and seed data", path)

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
        await vectors.create_tables(connection)
    async with new_session() as session:
        seed.seed(session)
        await session.commit()
    async with engine.begin() as connection:
        await connection.exec_driver_sql(f"PRAGMA user_version = {expected}")
