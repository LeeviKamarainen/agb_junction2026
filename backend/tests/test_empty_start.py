from sqlmodel import select

from app.models import Memory, Showcase


def test_startup_has_no_sample_memories_or_demo_drafts(client, db):
    async def rows(session):
        return list(await session.exec(select(Memory))), list(await session.exec(select(Showcase)))

    memories, demos = db(rows)
    assert memories == []
    assert demos == []
    assert client.get("/api/demo/timeline").status_code == 404
