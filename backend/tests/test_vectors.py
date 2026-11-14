from datetime import UTC, datetime, timedelta

import pytest

from app import vectors
from app.db import get_engine, init_db

TABLE = "note_embedding"
WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions
NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.anyio


def unit(axis: int) -> list[float]:
    """A vector along one axis, so the distance between two of them is easy to reason about."""
    return [1.0 if i == axis else 0.0 for i in range(WIDTH)]


async def test_vector_tables_are_created_with_the_database(db_path):
    await init_db()

    async with get_engine().connect() as connection:
        names = {
            row[0] for row in await connection.exec_driver_sql("SELECT name FROM sqlite_master")
        }
    assert TABLE in names


async def test_nearest_returns_closest_first(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        await vectors.upsert(connection, TABLE, 1, unit(0))
        await vectors.upsert(connection, TABLE, 2, unit(1))
        await vectors.upsert(connection, TABLE, 3, [1.0, 1.0] + [0.0] * (WIDTH - 2))

        hits = await vectors.nearest(connection, TABLE, unit(0), k=3)

    assert [hit.id for hit in hits] == [1, 3, 2]
    assert hits[0].distance == pytest.approx(0.0, abs=1e-6)  # identical
    assert hits[2].distance == pytest.approx(1.0, abs=1e-6)  # orthogonal


async def test_timestamp_defaults_to_now_and_round_trips(db_path):
    await init_db()
    before = datetime.now(UTC).replace(microsecond=0)
    async with get_engine().begin() as connection:
        await vectors.upsert(connection, TABLE, 1, unit(0))
        await vectors.upsert(connection, TABLE, 2, unit(1), created_at=NOON)

        hits = {hit.id: hit for hit in await vectors.nearest(connection, TABLE, unit(0))}

    assert hits[2].created_at == NOON
    assert before <= hits[1].created_at <= datetime.now(UTC)
    assert hits[1].metadata == {}


async def test_since_and_until_filter_before_ranking(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        # The identical vector is the oldest, so a time filter must skip it, not just reorder.
        await vectors.upsert(connection, TABLE, 1, unit(0), created_at=NOON - timedelta(hours=2))
        close = [1.0, 1.0] + [0.0] * (WIDTH - 2)  # nearer to unit(0) than unit(2) is
        await vectors.upsert(connection, TABLE, 2, close, created_at=NOON)
        await vectors.upsert(connection, TABLE, 3, unit(2), created_at=NOON + timedelta(hours=2))

        recent = await vectors.nearest(connection, TABLE, unit(0), since=NOON)
        window = await vectors.nearest(
            connection, TABLE, unit(0), since=NOON - timedelta(hours=1), until=NOON
        )
        top_one = await vectors.nearest(connection, TABLE, unit(0), k=1, since=NOON)

    assert sorted(hit.id for hit in recent) == [2, 3]
    assert window == []  # 1 is too old and until is exclusive, so 2 at NOON is out too
    assert [hit.id for hit in top_one] == [2]  # k applies after the filter


async def test_naive_timestamps_are_rejected(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        with pytest.raises(ValueError, match="timezone-aware"):
            await vectors.upsert(connection, TABLE, 1, unit(0), created_at=datetime(2026, 11, 14))
        with pytest.raises(ValueError, match="timezone-aware"):
            await vectors.nearest(connection, TABLE, unit(0), since=datetime(2026, 11, 14))


async def test_extra_metadata_is_stored_returned_and_filterable(db_path, monkeypatch):
    monkeypatch.setitem(
        vectors.VECTOR_TABLES,
        "frame_embedding",
        vectors.VectorTable(WIDTH, {"source": "TEXT", "frame": "INTEGER"}),
    )
    await init_db()
    async with get_engine().begin() as connection:
        await vectors.upsert(
            connection, "frame_embedding", 1, unit(0), NOON, {"source": "a", "frame": 7}
        )
        await vectors.upsert(
            connection, "frame_embedding", 2, unit(0), NOON, {"source": "b", "frame": 8}
        )

        hits = await vectors.nearest(connection, "frame_embedding", unit(0), where={"source": "b"})

        with pytest.raises(ValueError, match="needs a value"):
            await vectors.upsert(connection, "frame_embedding", 3, unit(0), NOON, {"source": "c"})
        with pytest.raises(ValueError, match="no metadata column"):
            await vectors.nearest(connection, "frame_embedding", unit(0), where={"nope": 1})

    assert [(hit.id, hit.metadata) for hit in hits] == [(2, {"source": "b", "frame": 8})]


async def test_upsert_replaces_and_delete_removes(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        await vectors.upsert(connection, TABLE, 1, unit(0), created_at=NOON)
        await vectors.upsert(connection, TABLE, 1, unit(1), created_at=NOON + timedelta(hours=1))

        (hit,) = await vectors.nearest(connection, TABLE, unit(1), k=5)
        assert hit.distance == pytest.approx(0.0, abs=1e-6)  # the old vector is gone
        assert hit.created_at == NOON + timedelta(hours=1)

        await vectors.delete(connection, TABLE, 1)
        assert await vectors.nearest(connection, TABLE, unit(1), k=5) == []


async def test_vectors_survive_a_restart_and_a_schema_change_wipes_them(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        await vectors.upsert(connection, TABLE, 1, unit(0))

    await init_db()  # unchanged: data is kept
    async with get_engine().connect() as connection:
        assert len(await vectors.nearest(connection, TABLE, unit(0))) == 1

    async with get_engine().begin() as connection:
        await connection.exec_driver_sql("PRAGMA user_version = 1")  # as if built from older code
    await init_db()
    async with get_engine().connect() as connection:
        assert await vectors.nearest(connection, TABLE, unit(0)) == []


async def test_wrong_width_and_unknown_table_are_rejected(db_path):
    await init_db()
    async with get_engine().begin() as connection:
        with pytest.raises(ValueError, match=f"{WIDTH}-wide"):
            await vectors.upsert(connection, TABLE, 1, [1.0, 2.0])
        with pytest.raises(ValueError, match="Unknown vector table"):
            await vectors.nearest(connection, "sqlite_master; DROP TABLE note", unit(0))


async def test_a_list_in_where_matches_any_of_its_values(db_path, monkeypatch):
    monkeypatch.setitem(
        vectors.VECTOR_TABLES, "frame_embedding", vectors.VectorTable(WIDTH, {"source": "TEXT"})
    )
    await init_db()
    async with get_engine().begin() as connection:
        for id, source in [(1, "a"), (2, "b"), (3, "c")]:
            await vectors.upsert(
                connection, "frame_embedding", id, unit(0), NOON, {"source": source}
            )

        some = await vectors.nearest(
            connection, "frame_embedding", unit(0), where={"source": ["a", "c"]}
        )
        none = await vectors.nearest(connection, "frame_embedding", unit(0), where={"source": []})
        stored = await vectors.stored(connection, "frame_embedding", {2, 3})

    assert sorted(hit.id for hit in some) == [1, 3]
    assert none == []
    assert stored.keys() == {2, 3} and stored[2] == pytest.approx(unit(0))
