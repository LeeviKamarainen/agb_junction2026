import pytest
from sqlmodel import SQLModel

from app.db import fingerprint, get_engine, init_db

pytestmark = pytest.mark.anyio


async def table_names() -> set[str]:
    async with get_engine().connect() as connection:
        rows = await connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        return {row[0] for row in rows}


async def user_version() -> int:
    async with get_engine().connect() as connection:
        return (await connection.exec_driver_sql("PRAGMA user_version")).scalar()


async def test_unchanged_models_keep_existing_data(db_path):
    # A bug here would wipe production data on every restart.
    await init_db()
    async with get_engine().begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE leftover (x INTEGER)")

    await init_db()

    assert "leftover" in await table_names()


async def test_changed_models_wipe_and_rebuild_the_database(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE leftover (x INTEGER)")
        await connection.exec_driver_sql("PRAGMA user_version = 1")  # as if built from older models

    await init_db()

    assert "leftover" not in await table_names()
    assert set(SQLModel.metadata.tables) <= await table_names()
    assert await user_version() == fingerprint(get_engine().dialect)
