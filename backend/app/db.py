import hashlib
import inspect
import logging
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, event
from sqlalchemy.schema import CreateTable
from sqlmodel import Session, SQLModel, create_engine

from app import models, seed  # noqa: F401  (importing models registers their tables)
from app.settings import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    path = get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _configure_connection)
    return engine


def _configure_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def fingerprint(engine: Engine) -> int:
    """32-bit hash of the table definitions and the seed data, kept in PRAGMA user_version."""
    ddl = "".join(str(CreateTable(t).compile(engine)) for t in SQLModel.metadata.sorted_tables)
    digest = hashlib.sha256((ddl + inspect.getsource(seed)).encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


def init_db() -> None:
    """Rebuild the database whenever the models or seed data changed.

    The database is disposable (docs/adr/0001-disposable-database.md): there are no
    migrations, so a changed schema means wiping the file and seeding it again.
    """
    engine = get_engine()
    expected = fingerprint(engine)
    path = get_settings().database_path
    if path.exists():
        with engine.connect() as connection:
            current = connection.exec_driver_sql("PRAGMA user_version").scalar()
        if current == expected:
            return
        log.warning("Models or seed data changed: wiping %s and re-seeding", path)
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    else:
        log.warning("Creating database %s from the models and seed data", path)

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed.seed(session)
        session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version = {expected}")
