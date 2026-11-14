import pytest
from sqlmodel import select

from app import demo
from app.llm import UpstreamError
from app.models import Memory
from app.search import DEMO_OWNER, add_memory

WIDTH = 768


async def embed_all(texts):
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in texts]


async def broken(_texts):
    raise UpstreamError("down")


async def stored_texts(session) -> list[str]:
    rows = await session.exec(select(Memory).where(Memory.collection == demo.COLLECTION))
    return [memory.text for memory in rows]


def load(db, embed=embed_all) -> bool:
    async def work(session):
        return await demo.load_demo_memories(session, embed)

    return db(work)


def test_timeline_is_public_and_in_order(anonymous):
    reply = anonymous.get("/api/demo/timeline")
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["collection"] == "demo"
    assert body["searchable"] is False  # the tests have no embedding model
    moments = body["moments"]
    assert len(moments) == len(demo.MOMENTS)
    assert [m["index"] for m in moments] == list(range(len(moments)))
    times = [m["time"] for m in moments]
    assert times == sorted(times)
    assert all(m["time"].endswith("+02:00") for m in moments)


def test_dataset_is_written_in_time_order():
    assert demo.MOMENTS == demo.timeline()


def test_loading_stores_demo_memories_once(anonymous, db):
    assert load(db) is True
    assert load(db) is False

    async def owners(session):
        rows = await session.exec(select(Memory).where(Memory.collection == demo.COLLECTION))
        return [memory.owner_id for memory in rows]

    found = db(owners)
    assert len(found) == len(demo.MOMENTS)
    assert set(found) == {DEMO_OWNER}
    assert anonymous.get("/api/demo/timeline").json()["searchable"] is True


def test_guests_see_the_demo_memories(anonymous, db, guest):
    load(db)
    reply = anonymous.get("/api/memories", headers=guest())
    assert reply.status_code == 200
    assert sum(memory["collection"] == "demo" for memory in reply.json()) == len(demo.MOMENTS)


def test_a_changed_dataset_replaces_the_stored_copies(anonymous, db, monkeypatch):
    load(db)
    changed = [*demo.MOMENTS[:-1], demo.Moment("21:00", "Home", "Back home.")]
    monkeypatch.setattr(demo, "MOMENTS", changed)
    assert load(db) is True
    texts = db(stored_texts)
    assert "Back home." in texts
    assert len(texts) == len(changed)


def test_a_failing_embedder_keeps_what_was_stored(anonymous, db, monkeypatch):
    load(db)
    before = db(stored_texts)
    monkeypatch.setattr(demo, "MOMENTS", demo.MOMENTS[:3])
    with pytest.raises(UpstreamError):
        load(db, broken)
    assert db(stored_texts) == before


def test_loading_leaves_the_admins_demo_uploads_alone(anonymous, db):
    async def upload(session):
        await add_memory(
            session,
            "image",
            [0.0, 1.0] + [0.0] * (WIDTH - 2),
            text="A photo",
            image_file="photo.jpg",
            collection=demo.COLLECTION,
            owner_id=DEMO_OWNER,
        )
        await session.commit()

    db(upload)
    assert load(db) is True
    assert load(db) is False  # the upload does not make the dataset look stale
    texts = db(stored_texts)
    assert "A photo" in texts
    assert len(texts) == len(demo.MOMENTS) + 1
