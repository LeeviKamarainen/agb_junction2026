from sqlmodel import Session, select

from app import demo
from app.db import get_engine
from app.llm import UpstreamError
from app.models import Memory
from app.search import DEMO_OWNER

WIDTH = 768


def embed_all(texts):
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in texts]


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


def test_loading_stores_demo_memories_once(anonymous):
    with Session(get_engine()) as session:
        assert demo.load_demo_memories(session, embed_all) is True
        assert demo.load_demo_memories(session, embed_all) is False
        stored = session.exec(select(Memory).where(Memory.collection == "demo")).all()
    assert len(stored) == len(demo.MOMENTS)
    assert {memory.owner_id for memory in stored} == {DEMO_OWNER}
    assert anonymous.get("/api/demo/timeline").json()["searchable"] is True


def test_guests_see_the_demo_memories(anonymous, guest):
    with Session(get_engine()) as session:
        demo.load_demo_memories(session, embed_all)
    reply = anonymous.get("/api/memories", headers=guest())
    assert reply.status_code == 200
    assert sum(memory["collection"] == "demo" for memory in reply.json()) == len(demo.MOMENTS)


def test_a_changed_dataset_replaces_the_stored_copies(anonymous, monkeypatch):
    with Session(get_engine()) as session:
        demo.load_demo_memories(session, embed_all)
        changed = [*demo.MOMENTS[:-1], demo.Moment("21:00", "Home", "Back home.")]
        monkeypatch.setattr(demo, "MOMENTS", changed)
        assert demo.load_demo_memories(session, embed_all) is True
        texts = [m.text for m in session.exec(select(Memory).where(Memory.collection == "demo"))]
    assert "Back home." in texts
    assert len(texts) == len(changed)


def test_a_failing_embedder_keeps_what_was_stored(anonymous, monkeypatch):
    with Session(get_engine()) as session:
        demo.load_demo_memories(session, embed_all)
        before = len(demo.MOMENTS)

        def broken(_texts):
            raise UpstreamError("down")

        monkeypatch.setattr(demo, "MOMENTS", demo.MOMENTS[:3])
        try:
            demo.load_demo_memories(session, broken)
        except UpstreamError:
            pass
        count = len(session.exec(select(Memory).where(Memory.collection == "demo")).all())
    assert count == before  # the ones stored before, untouched
