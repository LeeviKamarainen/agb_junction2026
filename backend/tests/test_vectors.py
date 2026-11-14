from datetime import UTC, datetime, timedelta

import pytest

from app import vectors
from app.db import get_engine, init_db

TABLE = "note_embedding"
WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions
NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def unit(axis: int) -> list[float]:
    """A vector along one axis, so the distance between two of them is easy to reason about."""
    return [1.0 if i == axis else 0.0 for i in range(WIDTH)]


def test_vector_tables_are_created_with_the_database(db_path):
    init_db()

    with get_engine().connect() as connection:
        names = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master")}
    assert TABLE in names


def test_nearest_returns_closest_first(db_path):
    init_db()
    with get_engine().begin() as connection:
        vectors.upsert(connection, TABLE, 1, unit(0))
        vectors.upsert(connection, TABLE, 2, unit(1))
        vectors.upsert(connection, TABLE, 3, [1.0, 1.0] + [0.0] * (WIDTH - 2))

        hits = vectors.nearest(connection, TABLE, unit(0), k=3)

    assert [hit.id for hit in hits] == [1, 3, 2]
    assert hits[0].distance == pytest.approx(0.0, abs=1e-6)  # identical
    assert hits[2].distance == pytest.approx(1.0, abs=1e-6)  # orthogonal


def test_timestamp_defaults_to_now_and_round_trips(db_path):
    init_db()
    before = datetime.now(UTC).replace(microsecond=0)
    with get_engine().begin() as connection:
        vectors.upsert(connection, TABLE, 1, unit(0))
        vectors.upsert(connection, TABLE, 2, unit(1), created_at=NOON)

        hits = {hit.id: hit for hit in vectors.nearest(connection, TABLE, unit(0))}

    assert hits[2].created_at == NOON
    assert before <= hits[1].created_at <= datetime.now(UTC)
    assert hits[1].metadata == {}


def test_since_and_until_filter_before_ranking(db_path):
    init_db()
    with get_engine().begin() as connection:
        # The identical vector is the oldest, so a time filter must skip it, not just reorder.
        vectors.upsert(connection, TABLE, 1, unit(0), created_at=NOON - timedelta(hours=2))
        close = [1.0, 1.0] + [0.0] * (WIDTH - 2)  # nearer to unit(0) than unit(2) is
        vectors.upsert(connection, TABLE, 2, close, created_at=NOON)
        vectors.upsert(connection, TABLE, 3, unit(2), created_at=NOON + timedelta(hours=2))

        recent = vectors.nearest(connection, TABLE, unit(0), since=NOON)
        window = vectors.nearest(
            connection, TABLE, unit(0), since=NOON - timedelta(hours=1), until=NOON
        )
        top_one = vectors.nearest(connection, TABLE, unit(0), k=1, since=NOON)

    assert sorted(hit.id for hit in recent) == [2, 3]
    assert window == []  # 1 is too old and until is exclusive, so 2 at NOON is out too
    assert [hit.id for hit in top_one] == [2]  # k applies after the filter


def test_naive_timestamps_are_rejected(db_path):
    init_db()
    with get_engine().begin() as connection:
        with pytest.raises(ValueError, match="timezone-aware"):
            vectors.upsert(connection, TABLE, 1, unit(0), created_at=datetime(2026, 11, 14))
        with pytest.raises(ValueError, match="timezone-aware"):
            vectors.nearest(connection, TABLE, unit(0), since=datetime(2026, 11, 14))


def test_extra_metadata_is_stored_returned_and_filterable(db_path, monkeypatch):
    monkeypatch.setitem(
        vectors.VECTOR_TABLES,
        "frame_embedding",
        vectors.VectorTable(WIDTH, {"source": "TEXT", "frame": "INTEGER"}),
    )
    init_db()
    with get_engine().begin() as connection:
        vectors.upsert(connection, "frame_embedding", 1, unit(0), NOON, {"source": "a", "frame": 7})
        vectors.upsert(connection, "frame_embedding", 2, unit(0), NOON, {"source": "b", "frame": 8})

        hits = vectors.nearest(connection, "frame_embedding", unit(0), where={"source": "b"})

        with pytest.raises(ValueError, match="needs a value"):
            vectors.upsert(connection, "frame_embedding", 3, unit(0), NOON, {"source": "c"})
        with pytest.raises(ValueError, match="no metadata column"):
            vectors.nearest(connection, "frame_embedding", unit(0), where={"nope": 1})

    assert [(hit.id, hit.metadata) for hit in hits] == [(2, {"source": "b", "frame": 8})]


def test_upsert_replaces_and_delete_removes(db_path):
    init_db()
    with get_engine().begin() as connection:
        vectors.upsert(connection, TABLE, 1, unit(0), created_at=NOON)
        vectors.upsert(connection, TABLE, 1, unit(1), created_at=NOON + timedelta(hours=1))

        (hit,) = vectors.nearest(connection, TABLE, unit(1), k=5)
        assert hit.distance == pytest.approx(0.0, abs=1e-6)  # the old vector is gone
        assert hit.created_at == NOON + timedelta(hours=1)

        vectors.delete(connection, TABLE, 1)
        assert vectors.nearest(connection, TABLE, unit(1), k=5) == []


def test_vectors_survive_a_restart_and_a_schema_change_wipes_them(db_path):
    init_db()
    with get_engine().begin() as connection:
        vectors.upsert(connection, TABLE, 1, unit(0))

    init_db()  # unchanged: data is kept
    with get_engine().connect() as connection:
        assert len(vectors.nearest(connection, TABLE, unit(0))) == 1

    with get_engine().begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version = 1")  # as if built from older code
    init_db()
    with get_engine().connect() as connection:
        assert vectors.nearest(connection, TABLE, unit(0)) == []


def test_wrong_width_and_unknown_table_are_rejected(db_path):
    init_db()
    with get_engine().begin() as connection:
        with pytest.raises(ValueError, match="512-wide"):
            vectors.upsert(connection, TABLE, 1, [1.0, 2.0])
        with pytest.raises(ValueError, match="Unknown vector table"):
            vectors.nearest(connection, "sqlite_master; DROP TABLE note", unit(0))
