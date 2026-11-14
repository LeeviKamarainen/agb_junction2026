"""The demo dataset: published demo clips stored as demo memories (app/demo.py)."""

import io
import tempfile
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from sqlmodel import select

from app import demo, vectors
from app.llm import UpstreamError
from app.models import Memory
from app.search import DEMO_OWNER, TABLE

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions


class Embed:
    """A fake image embedder: each image points along the next axis, so no two look alike."""

    def __init__(self):
        self.images = 0

    async def __call__(self, inputs, input_type="query"):
        vectors_ = []
        for _ in inputs:
            vector = [0.0] * WIDTH
            vector[self.images % WIDTH] = 1.0
            self.images += 1
            vectors_.append(vector)
        return vectors_


async def same(inputs, input_type="query"):
    """Every image embeds the same: a still scene."""
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in inputs]


async def broken(_inputs, input_type="query"):
    raise UpstreamError("down")


def jpeg(colour="green") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 20), colour).save(out, "JPEG")
    return out.getvalue()


def video(seconds: float, fps: int = 10) -> bytes:
    """An MP4 of `seconds`, its colour changing every frame."""
    path = Path(tempfile.mkdtemp()) / "source.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for i in range(round(seconds * fps)):
        writer.write(np.full((48, 64, 3), (i * 7) % 255, dtype=np.uint8))
    writer.release()
    data = path.read_bytes()
    path.unlink()
    return data


def published_images(client, title="Kitchen", count=3):
    clip = client.post("/api/demo/clips", json={"title": title}).json()
    files = [("files", (f"{i}.jpg", jpeg(), "image/jpeg")) for i in range(count)]
    assert client.post(f"/api/demo/clips/{clip['id']}/media", files=files).status_code == 200
    body = {**clip, "published": True, "image_seconds": 3}
    assert client.put(f"/api/demo/clips/{clip['id']}", json=body).status_code == 200
    return clip


def published_video(client, seconds: float, title="Walk"):
    clip = client.post("/api/demo/clips", json={"title": title, "kind": "video"}).json()
    files = {"files": ("walk.mp4", video(seconds), "video/mp4")}
    assert client.post(f"/api/demo/clips/{clip['id']}/media", files=files).status_code == 200
    body = {**clip, "published": True}
    assert client.put(f"/api/demo/clips/{clip['id']}", json=body).status_code == 200
    return clip


def sync(db, embed=None):
    embed = embed or Embed()

    async def work(session):
        return await demo.sync(session, embed)

    return db(work)


def demo_memories(db) -> list[Memory]:
    async def work(session):
        rows = await session.exec(
            select(Memory).where(Memory.collection == demo.COLLECTION).order_by(Memory.created_at)
        )
        return list(rows)

    return db(work)


def test_video_frames_come_at_the_glasses_rate(tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(video(3))

    times = [at for at, _jpeg in demo.video_frames(str(path))]

    assert times == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]


def test_a_published_image_sequence_becomes_demo_memories_everyone_sees(client, guest, db):
    published_images(client, count=3)
    client.post("/api/demo/clips", json={"title": "Draft"})  # unpublished: not stored

    assert sync(db) == 1

    stored = demo_memories(db)
    assert len(stored) == 3
    assert {m.owner_id for m in stored} == {DEMO_OWNER}
    assert all(m.kind == "image" and m.text == "Kitchen" for m in stored)
    # Dated in the clip's order, one image every image_seconds.
    gaps = [(b.created_at - a.created_at).total_seconds() for a, b in pairwise(stored)]
    assert gaps == [3, 3]
    seen = client.get("/api/memories", headers=guest()).json()
    assert sum(m["collection"] == "demo" and m["demo"] for m in seen) == 3
    assert sync(db) == 0  # nothing changed: nothing stored again


def test_a_video_is_stored_at_two_frames_a_second_skipping_a_still_scene(client, db):
    published_video(client, 3)

    sync(db)
    assert len(demo_memories(db)) == 6  # every frame differs

    published_video(client, 3, title="Still")
    sync(db, same)
    # The same picture throughout: only the first of the new clip's frames is kept.
    assert len(demo_memories(db)) == 7


def test_unpublished_or_deleted_clips_lose_their_demo_memories(client, db):
    kept = published_images(client, "Kept", count=1)
    gone = published_images(client, "Gone", count=2)
    sync(db)
    assert len(demo_memories(db)) == 3

    client.put(f"/api/demo/clips/{gone['id']}", json={**gone, "published": False})
    sync(db)
    assert [m.text for m in demo_memories(db)] == ["Kept"]

    client.delete(f"/api/demo/clips/{kept['id']}")
    sync(db)
    assert demo_memories(db) == []


def test_new_media_replaces_a_clips_memories(client, db):
    clip = published_images(client, count=1)
    sync(db)
    (first,) = demo_memories(db)

    files = [("files", ("more.jpg", jpeg("red"), "image/jpeg"))]
    client.post(f"/api/demo/clips/{clip['id']}/media", files=files)
    sync(db)

    stored = demo_memories(db)
    # SQLite may reuse the old ids: the new media's digest tells the rows apart.
    assert len(stored) == 2 and {m.source for m in stored} != {first.source}
    assert len({m.source for m in stored}) == 1


def test_a_failing_embedding_model_keeps_nothing_of_the_clip(client, db):
    published_images(client, count=2)
    try:
        sync(db, broken)
    except UpstreamError:
        pass
    else:
        raise AssertionError("sync should raise when embedding fails")
    assert demo_memories(db) == []
