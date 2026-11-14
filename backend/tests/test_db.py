from sqlmodel import SQLModel

from app.db import fingerprint, get_engine, init_db


def table_names() -> set[str]:
    with get_engine().connect() as connection:
        rows = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}


def user_version() -> int:
    with get_engine().connect() as connection:
        return connection.exec_driver_sql("PRAGMA user_version").scalar()


def test_unchanged_models_keep_existing_data(db_path):
    # A bug here would wipe production data on every restart.
    init_db()
    with get_engine().begin() as connection:
        connection.exec_driver_sql("CREATE TABLE leftover (x INTEGER)")

    init_db()

    assert "leftover" in table_names()


def test_changed_models_wipe_and_rebuild_the_database(db_path):
    init_db()
    with get_engine().begin() as connection:
        connection.exec_driver_sql("CREATE TABLE leftover (x INTEGER)")
        connection.exec_driver_sql("PRAGMA user_version = 1")  # as if built from older models

    init_db()

    assert "leftover" not in table_names()
    assert set(SQLModel.metadata.tables) <= table_names()
    assert user_version() == fingerprint(get_engine())
