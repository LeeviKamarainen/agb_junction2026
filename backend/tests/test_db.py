"""init_db: creating the database, leaving it alone, and carrying its rows over to a new schema
(docs/adr/0003-carry-rows-over.md).

Each "old" database is today's schema, changed with raw SQL and stamped with an older version, so
these tests keep working as the models grow.
"""

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import ColumnDefault
from sqlmodel import SQLModel, select

import app.db
from app import vectors
from app.db import BACKUPS_KEPT, CarryOverError, fingerprint, get_engine, init_db, new_session
from app.models import AuthSession, Goal, Memory, Showcase, Trigger, User
from app.settings import get_settings

pytestmark = pytest.mark.anyio

NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
EMBEDDING = vectors.VECTOR_TABLES["memory_embedding"]
CUES = '[{"at_s": 2.5, "text": "where are my keys?"}]'


def unit(axis: int, width: int = EMBEDDING.dimensions) -> list[float]:
    """A vector along one axis: distance 0 to itself and 1 to any other axis."""
    return [1.0 if i == axis else 0.0 for i in range(width)]


async def sql(*statements: str) -> None:
    async with get_engine().begin() as connection:
        for statement in statements:
            await connection.exec_driver_sql(statement)


async def table_names() -> set[str]:
    async with get_engine().connect() as connection:
        rows = await connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        return {row[0] for row in rows}


async def column_names(table: str) -> set[str]:
    async with get_engine().connect() as connection:
        rows = await connection.exec_driver_sql(f"SELECT name FROM pragma_table_info('{table}')")
        return {row[0] for row in rows}


async def user_version() -> int:
    async with get_engine().connect() as connection:
        return (await connection.exec_driver_sql("PRAGMA user_version")).scalar()


def files_beside(path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir())


async def add_rows() -> None:
    """A guest with a login, two memories with embeddings, a goal with a trigger, a demo clip."""
    async with new_session() as session:
        session.add(User(id=5, username="guest-5", password_hash="-", created_at=NOON))
        await session.flush()  # before its login, which refers to it
        session.add_all(
            [
                AuthSession(token_hash="hash-of-token", user_id=5, expires_at=NOON),
                Memory(
                    id=7,
                    kind="image",
                    created_at=NOON,
                    text="keys on the table",
                    image_file="a1.jpg",
                    collection="glasses",
                    owner_id=5,
                ),
                Memory(id=42, kind="text", created_at=NOON, text="bus at 8", owner_id=5),
                Goal(id=3, owner_id=5, text="tell me when my bus comes"),
                Trigger(id=9, owner_id=5, goal_id=3, name="Bus", watch_for="a bus at the stop"),
                Showcase(id=2, title="Morning", cues_json=CUES),
            ]
        )
        await session.commit()
    async with get_engine().begin() as connection:
        for id, axis in [(7, 0), (42, 1)]:
            await vectors.upsert(
                connection,
                "memory_embedding",
                id,
                unit(axis),
                NOON,
                {"kind": "image", "collection": "glasses", "owner": 5},
            )


async def old_database(*changes: str) -> None:
    """Today's schema and rows, changed by `changes` as older code would have left them, closed
    so that the file holds everything."""
    await init_db()
    await add_rows()
    await sql(*changes, "PRAGMA user_version = 1")
    await get_engine().dispose()


async def test_a_new_database_has_the_current_schema(db_path):
    await init_db()

    assert SQLModel.metadata.tables.keys() | vectors.VECTOR_TABLES.keys() <= await table_names()
    assert await user_version() == fingerprint(get_engine().dialect)


async def test_an_unchanged_schema_leaves_the_file_alone(db_path):
    # A bug here would rebuild production on every restart.
    await init_db()
    await sql("CREATE TABLE leftover (x INTEGER)")  # a rebuilt file would not have it

    await init_db()

    assert "leftover" in await table_names()
    assert not [name for name in files_beside(db_path) if name.endswith((".bak", ".new"))]


async def test_a_schema_change_carries_the_rows_over(db_path, caplog):
    # The old schema had no memory.collection, which defaults to "manual" now, nor the optional
    # trigger.level, and it had a memory.mood column and a note table, which the models no longer
    # have.
    await old_database(
        "DROP INDEX ix_memory_collection",
        "ALTER TABLE memory DROP COLUMN collection",
        'ALTER TABLE "trigger" DROP COLUMN level',
        "ALTER TABLE memory ADD COLUMN mood VARCHAR",
        "UPDATE memory SET mood = 'calm'",
        "CREATE TABLE note (id INTEGER PRIMARY KEY, text VARCHAR)",
        "INSERT INTO note VALUES (1, 'gone')",
    )
    caplog.clear()

    await init_db()

    assert await user_version() == fingerprint(get_engine().dialect)
    async with new_session() as session:
        memories = (await session.exec(select(Memory).order_by(Memory.id))).all()
        user = await session.get(User, 5)
        login = await session.get(AuthSession, "hash-of-token")
        goal = await session.get(Goal, 3)
        trigger = await session.get(Trigger, 9)
        showcase = await session.get(Showcase, 2)
    assert [(m.id, m.text, m.image_file, m.owner_id, m.collection) for m in memories] == [
        (7, "keys on the table", "a1.jpg", 5, "manual"),
        (42, "bus at 8", None, 5, "manual"),
    ]
    assert user.username == "guest-5" and login.user_id == 5
    assert goal.text == "tell me when my bus comes"
    assert (trigger.goal_id, trigger.watch_for, trigger.level) == (3, "a bus at the stop", None)
    assert showcase.cues_json == CUES
    assert "mood" not in await column_names("memory")
    assert "note" not in await table_names()

    async with get_engine().connect() as connection:
        hits = await vectors.nearest(connection, "memory_embedding", unit(0), where={"owner": 5})
    assert [hit.id for hit in hits] == [7, 42]
    assert hits[0].distance == pytest.approx(0.0, abs=1e-6)
    assert hits[0].created_at == NOON
    assert hits[0].metadata == {"kind": "image", "collection": "glasses", "owner": 5}

    # The old file itself is the backup, named with its version.
    (backup,) = db_path.parent.glob(f"{db_path.name}.*.1.bak")
    old = sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True)
    try:
        assert old.execute("PRAGMA user_version").fetchone() == (1,)
        assert old.execute("SELECT mood FROM memory").fetchall() == [("calm",), ("calm",)]
    finally:
        old.close()
    assert f"{db_path.name}.new" not in files_beside(db_path)
    (message,) = [record.getMessage() for record in caplog.records]
    assert "memory 2" in message and "memory_embedding 2" in message and str(backup) in message


async def test_a_new_required_column_needs_no_default_while_its_table_is_empty(db_path):
    await init_db()  # no triggers, so none lacks a goal_id
    await sql(
        "DROP INDEX ix_trigger_goal_id",
        'ALTER TABLE "trigger" DROP COLUMN goal_id',
        "PRAGMA user_version = 1",
    )
    await get_engine().dispose()

    await init_db()

    assert "goal_id" in await column_names("trigger")


async def new_required_column(_monkeypatch) -> None:
    # trigger.goal_id is NOT NULL without a default, and the old database has a trigger.
    await old_database("DROP INDEX ix_trigger_goal_id", 'ALTER TABLE "trigger" DROP COLUMN goal_id')


async def changed_type(_monkeypatch) -> None:
    # SQLite would store "high" in the FLOAT column as it is, where the app expects a number.
    await old_database(
        'ALTER TABLE "trigger" DROP COLUMN sigmas',
        'ALTER TABLE "trigger" ADD COLUMN sigmas TEXT',
        "UPDATE \"trigger\" SET sigmas = 'high'",
    )


async def column_became_required(_monkeypatch) -> None:
    # memory.text is NOT NULL now; the old schema allowed none, and its memories have none.
    await old_database(
        "ALTER TABLE memory DROP COLUMN text", "ALTER TABLE memory ADD COLUMN text VARCHAR"
    )


async def row_that_refers_to_nothing(_monkeypatch) -> None:
    await old_database()
    connection = sqlite3.connect(get_settings().database_path)  # foreign keys are off here
    connection.execute("DELETE FROM user WHERE id = 5")  # its login stays
    connection.commit()
    connection.close()


async def changed_width(monkeypatch) -> None:
    # Built while memory_embedding held 512 numbers, as with another embedding model.
    with monkeypatch.context() as patch:
        patch.setitem(
            vectors.VECTOR_TABLES, "memory_embedding", vectors.VectorTable(512, EMBEDDING.metadata)
        )
        await init_db()
        async with get_engine().begin() as connection:
            metadata = {"kind": "image", "collection": "glasses", "owner": 5}
            await vectors.upsert(connection, "memory_embedding", 7, unit(0, 512), NOON, metadata)
    await get_engine().dispose()


async def new_vector_column(monkeypatch) -> None:
    # Built before memory_embedding had an owner, which vec0 cannot leave NULL.
    with monkeypatch.context() as patch:
        patch.setitem(
            vectors.VECTOR_TABLES,
            "memory_embedding",
            vectors.VectorTable(EMBEDDING.dimensions, {"kind": "TEXT", "collection": "TEXT"}),
        )
        await init_db()
        async with get_engine().begin() as connection:
            metadata = {"kind": "image", "collection": "glasses"}
            await vectors.upsert(connection, "memory_embedding", 7, unit(0), NOON, metadata)
    await get_engine().dispose()


@pytest.mark.parametrize(
    "make_old_database, message",
    [
        (new_required_column, r"trigger\.goal_id is new and NOT NULL"),
        (changed_type, r"trigger\.sigmas was TEXT and is now FLOAT"),
        (column_became_required, r"NOT NULL constraint failed: memory\.text"),
        (row_that_refers_to_nothing, r"1 row of authsession to user"),
        (changed_width, r"memory_embedding holds FLOAT\[512\] vectors"),
        (new_vector_column, r"memory_embedding\.owner is new"),
    ],
)
async def test_what_cannot_be_carried_over_stops_the_start_and_leaves_the_file(
    db_path, monkeypatch, make_old_database, message
):
    await make_old_database(monkeypatch)
    before = db_path.read_bytes()

    with pytest.raises(CarryOverError, match=message):
        await init_db()

    assert db_path.read_bytes() == before
    # No half-built new database, no backup, and no -wal or -shm beside the old file.
    assert files_beside(db_path) == [db_path.name]


async def test_a_database_open_elsewhere_is_not_carried_over(db_path):
    await old_database()
    other = sqlite3.connect(db_path)  # another process's connection, as far as SQLite can tell
    try:
        other.execute("SELECT count(*) FROM memory").fetchall()
        before = db_path.read_bytes()

        with pytest.raises(CarryOverError, match="something else has"):
            await init_db()

        assert db_path.read_bytes() == before
    finally:
        other.close()
    assert not [name for name in files_beside(db_path) if name.endswith((".bak", ".new"))]


def read_it(path: Path, opened: list[sqlite3.Connection]) -> None:
    # Reading it, as another process would, puts a -wal and -shm beside it; left open, the
    # connection would still use the old file after the swap.
    connection = sqlite3.connect(path)
    connection.execute("SELECT count(*) FROM memory").fetchall()
    opened.append(connection)


def replace_it(path: Path, _opened: list[sqlite3.Connection]) -> None:
    # As make prod-reset-db or restoring a backup would; with a copy, so the bytes stay the same.
    shutil.copy(path, f"{path}.copy")
    Path(f"{path}.copy").replace(path)


@pytest.mark.parametrize("meddle", [read_it, replace_it])
async def test_a_database_used_during_the_carry_over_is_not_replaced(db_path, monkeypatch, meddle):
    await old_database()
    before = db_path.read_bytes()
    create_schema, opened = app.db._create_schema, []

    async def create_schema_and_meddle(path: Path) -> None:
        await create_schema(path)
        meddle(db_path, opened)

    monkeypatch.setattr(app.db, "_create_schema", create_schema_and_meddle)
    try:
        with pytest.raises(CarryOverError, match="opened or replaced it"):
            await init_db()

        assert db_path.read_bytes() == before
    finally:
        for connection in opened:
            connection.close()
    assert files_beside(db_path) == [db_path.name]


async def test_a_failed_swap_leaves_the_old_file_and_no_backup(db_path, monkeypatch):
    await old_database()
    before = db_path.read_bytes()
    replace = Path.replace

    def replace_all_but_the_new_database(self: Path, target: Path) -> Path:
        if self.name.endswith(".new"):
            raise OSError("cannot rename")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", replace_all_but_the_new_database)
    with pytest.raises(OSError, match="cannot rename"):
        await init_db()

    assert db_path.read_bytes() == before
    # A backup here would be a second name for the database itself, changing along with it.
    assert files_beside(db_path) == [db_path.name]


async def test_a_new_database_does_not_take_over_a_deleted_ones_wal(db_path):
    # Left beside it, the WAL of a database deleted without it would be replayed onto the new one.
    gone = db_path.with_name("gone.db")
    writer = sqlite3.connect(gone)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE t (x)")
    writer.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(1000)])
    writer.commit()
    shutil.copy(f"{gone}-wal", f"{db_path}-wal")
    writer.close()

    await init_db()

    assert SQLModel.metadata.tables.keys() <= await table_names()
    async with get_engine().connect() as connection:
        assert (await connection.exec_driver_sql("PRAGMA integrity_check")).scalar() == "ok"


async def test_a_new_columns_default_is_stored_as_sqlalchemy_stores_it(db_path, monkeypatch):
    # A datetime stored any other way reads back unlike the rows SQLAlchemy wrote.
    monkeypatch.setattr(User.__table__.c.expires_at, "default", ColumnDefault(NOON))
    await old_database("DROP INDEX ix_user_expires_at", "ALTER TABLE user DROP COLUMN expires_at")

    await init_db()

    async with get_engine().connect() as connection:
        stored = await connection.exec_driver_sql("SELECT expires_at FROM user WHERE id = 5")
        assert stored.scalar() == "2026-11-14 12:00:00.000000"


async def test_a_schema_change_keeps_only_the_newest_backups(db_path):
    await old_database()
    days = [datetime(2000, 1, 1, tzinfo=UTC) + timedelta(days=n) for n in range(BACKUPS_KEPT)]
    older = [f"{db_path.name}.{day:%Y%m%dT%H%M%SZ}.1.bak" for day in days]
    for name in [*older, f"{db_path.name}.mine.bak"]:
        db_path.with_name(name).write_bytes(b"")

    await init_db()

    backups = sorted(p.name for p in db_path.parent.glob(f"{db_path.name}.*Z.*.bak"))
    assert len(backups) == BACKUPS_KEPT
    assert backups[:-1] == older[1:]  # the oldest went; the new one sorts last
    assert db_path.with_name(f"{db_path.name}.mine.bak").exists()  # not a name init_db gives
