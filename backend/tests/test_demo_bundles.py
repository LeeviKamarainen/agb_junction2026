import io
import json
import zipfile
from datetime import UTC, datetime

from app import demo_bundles, media
from app.search import add_memory
from tests.test_frames import jpeg, unit

STRANGER = 999  # a user that is not the guest asking


def record(db, collection="demo-kitchen", colours=("red", "green", "blue"), owner=STRANGER):
    """Stores one still per colour, a minute apart, in `collection`; returns their ids."""

    async def work(session):
        ids = []
        for i, colour in enumerate(colours):
            name = media.save_image(jpeg(colour))
            memory = await add_memory(
                session,
                "image",
                unit(i),
                datetime(2026, 11, 15, 12, i, tzinfo=UTC),
                f"still {i}",
                name,
                collection=collection,
                owner_id=owner,
            )
            ids.append(memory.id)
        await session.commit()
        return ids

    return db(work)


def unpack(reply, folder):
    folder.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(reply.content)) as archive:
        archive.extractall(folder)
    return json.loads((folder / "manifest.json").read_text())


def test_a_collection_is_exported_as_a_zip_of_stills_with_their_embeddings(client, db, tmp_path):
    record(db)
    reply = client.get("/api/memories/export", params={"collection": "Demo-Kitchen"})

    assert reply.status_code == 200
    assert reply.headers["content-type"] == "application/zip"
    assert 'filename="demo-kitchen.zip"' in reply.headers["content-disposition"]
    manifest = unpack(reply, tmp_path / "out")
    assert manifest["collection"] == "demo-kitchen"
    assert [m["text"] for m in manifest["memories"]] == ["still 0", "still 1", "still 2"]
    assert [m["file"] for m in manifest["memories"]] == ["0001.jpg", "0002.jpg", "0003.jpg"]
    assert all((tmp_path / "out" / m["file"]).is_file() for m in manifest["memories"])


def test_an_empty_or_unseen_collection_is_not_exported(client, guest, db):
    record(db)
    assert client.get("/api/memories/export", params={"collection": "nothing"}).status_code == 404
    other = client.get(
        "/api/memories/export", params={"collection": "demo-kitchen"}, headers=guest()
    )
    assert other.status_code == 404  # someone else's stills are not yours to download
    assert client.get("/api/memories/export", params={"collection": "bad name!"}).status_code == 422


def test_a_bundle_in_the_repository_is_loaded_as_demo_memories_once(client, db, tmp_path):
    ids = record(db)
    manifest = unpack(
        client.get("/api/memories/export", params={"collection": "demo-kitchen"}),
        tmp_path / "bundles" / "demo-kitchen",
    )
    for id in ids:  # the database is rebuilt: the recorded memories are gone
        assert client.delete(f"/api/memories/{id}").status_code == 204
    assert client.get("/api/memories").json() == []

    db(lambda session: demo_bundles.load(session, tmp_path / "bundles"))
    db(
        lambda session: demo_bundles.load(session, tmp_path / "bundles")
    )  # a second start adds nothing

    loaded = client.get("/api/memories", params={"collection": "demo-kitchen"}).json()
    assert [m["text"] for m in loaded] == ["still 2", "still 1", "still 0"]  # newest first
    assert all(m["demo"] and m["image_url"] for m in loaded)
    assert loaded[-1]["created_at"].startswith("2026-11-15T12:00:00")
    stored = {v["text"]: v["embedding"] for v in client.get("/api/memories/vectors").json()}
    assert (
        stored["still 1"] == manifest["memories"][1]["embedding"]
    )  # the same vector, no model needed


def test_a_bundle_with_the_wrong_vector_width_or_model_is_skipped(client, db, tmp_path):
    record(db)
    root = tmp_path / "bundles"
    unpack(client.get("/api/memories/export", params={"collection": "demo-kitchen"}), root / "a")
    unpack(client.get("/api/memories/export", params={"collection": "demo-kitchen"}), root / "b")
    for name, change in (("a", {"embed_model": "some/other-model"}), ("b", None)):
        path = root / name / "manifest.json"
        manifest = json.loads(path.read_text())
        if change:
            manifest.update(change)
        else:
            manifest["memories"][1]["embedding"] = manifest["memories"][1]["embedding"][:8]
        path.write_text(json.dumps(manifest))
    for memory in client.get("/api/memories").json():
        client.delete(f"/api/memories/{memory['id']}")

    db(lambda session: demo_bundles.load(session, root))

    assert client.get("/api/memories").json() == []  # nothing half-loaded either


def test_the_demo_bundles_folder_may_be_missing(db, tmp_path):
    db(lambda session: demo_bundles.load(session, tmp_path / "does-not-exist"))
