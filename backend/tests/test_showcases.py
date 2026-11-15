import io
import json
import struct
import subprocess

import pytest
from PIL import Image
from sqlmodel import select

from app import demo, showcase, vectors
from app.models import Memory, Showcase
from app.search import TABLE

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions
# How a phone or a browser records a video.
CODECS = {
    "mp4": ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"],
    "webm": ["-c:v", "libvpx", "-deadline", "realtime", "-c:a", "libopus"],
}


def recording(folder, container="mp4", size="64x48") -> bytes:
    """A 1 s video with sound as a phone or a browser makes one: 30 frames, 1/30 and 1/40 s apart
    by turns (phones record at a varying rate), and in an MP4 the index after the media."""
    path = folder / f"recording.{container}"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y"]
        + ["-f", "lavfi", "-i", f"testsrc=duration=1:size={size}:rate=30"]
        + ["-f", "lavfi", "-i", "sine=duration=1"]
        + ["-vf", "setpts='(floor(N/2)*7/120+mod(N,2)/30)/TB'"]
        + ["-fps_mode", "passthrough", "-enc_time_base:v", "1:90000"]
        + [*CODECS[container], str(path)],
        check=True,
    )
    return path.read_bytes()


def probe(path) -> dict:
    """ffprobe's account of a video: "format", and each stream by its type, with its packets
    (frames, for the video) counted."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_format", "-show_streams"]
        + ["-of", "json", str(path)],
        check=True,
        capture_output=True,
    )
    found = json.loads(done.stdout)
    return {"format": found["format"], **{s["codec_type"]: s for s in found["streams"]}}


def index_first(data: bytes) -> bool:
    """Whether an MP4's index (the `moov` box) comes before its media (`mdat`), which a browser
    needs to start playing before it has the whole file."""
    boxes, at = [], 0
    while at + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[at : at + 8])
        if size == 1:  # a 64-bit size follows
            (size,) = struct.unpack(">Q", data[at + 8 : at + 16])
        boxes.append(kind)
        if size < 8:  # 0: the box runs to the end of the file
            break
        at += size
    return boxes.index(b"moov") < boxes.index(b"mdat")


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "green").save(out, "JPEG")
    return out.getvalue()


def draft(client, **extra):
    response = client.post("/api/demo/clips", json={"title": "A day", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, id, data=None):
    return client.post(
        f"/api/demo/clips/{id}/media",
        files=[
            ("files", ("first.jpg", data or jpeg(), "image/jpeg")),
            ("files", ("second.jpg", jpeg(), "image/jpeg")),
        ],
    )


def test_only_admin_can_manage_and_drafts_are_private(client, guest):
    item = draft(client)
    token = guest()
    assert client.get("/api/demo/manage", headers=token).status_code == 403
    assert client.post("/api/demo/clips", headers=token, json={"title": "No"}).status_code == 403
    assert client.put(f"/api/demo/clips/{item['id']}", headers=token, json=item).status_code == 403
    assert client.delete(f"/api/demo/clips/{item['id']}", headers=token).status_code == 403
    assert (
        client.post(
            f"/api/demo/clips/{item['id']}/media", headers=token, files={"files": ("x.jpg", jpeg())}
        ).status_code
        == 403
    )
    assert client.get("/api/demo/clips").json() == []
    body = upload(client, item["id"]).json()
    url = body["assets"][0]["url"]
    assert client.get(url).status_code == 200
    assert client.get(url, headers=token).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 404
    assert client.get("/api/demo/manage").status_code == 401


def test_publish_reorder_media_and_remove_files(client):
    item = draft(client)
    assert (
        client.put(f"/api/demo/clips/{item['id']}", json={**item, "published": True}).status_code
        == 422
    )
    body = upload(client, item["id"]).json()
    names = [a["name"] for a in body["assets"]]
    saved = client.put(
        f"/api/demo/clips/{item['id']}",
        json={
            **body,
            "published": True,
            "asset_order": names[::-1],
            "image_seconds": 2,
        },
    )
    assert saved.status_code == 200, saved.text
    assert [a["name"] for a in saved.json()["assets"]] == names[::-1]
    client.cookies.clear()
    public = client.get("/api/demo/clips").json()
    assert len(public) == 1 and public[0]["title"] == "A day"
    assert client.get(public[0]["assets"][0]["url"]).headers["content-type"] == "image/jpeg"


def test_invalid_batch_is_atomic_and_bad_order_rejected(client):
    item = draft(client)
    response = client.post(
        f"/api/demo/clips/{item['id']}/media",
        files=[
            ("files", ("valid.jpg", jpeg())),
            ("files", ("bad.jpg", b"not-image")),
        ],
    )
    assert response.status_code == 415
    assert list(showcase.folder().iterdir()) == []
    body = upload(client, item["id"]).json()
    assert (
        client.put(
            f"/api/demo/clips/{item['id']}", json={**body, "asset_order": ["other.jpg"]}
        ).status_code
        == 422
    )
    assert (
        client.put(f"/api/demo/clips/{item['id']}", json={**body, "kind": "video"}).status_code
        == 422
    )
    assert client.delete(f"/api/demo/clips/{item['id']}").status_code == 204
    assert list(showcase.folder().iterdir()) == []


def test_video_replacement_and_range_requests(client, converted, tmp_path):
    item = draft(client, kind="video")
    video = recording(tmp_path)
    first = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": ("one.mp4", video)}
    ).json()
    old = first["assets"][0]["name"]
    second = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": ("two.mp4", video)}
    ).json()
    converted(item["id"])
    assert not (showcase.folder() / old).exists()  # nor put back by the worker re-encoding it
    assert len(second["assets"]) == 1
    saved = client.put(f"/api/demo/clips/{item['id']}", json={**second, "published": True}).json()
    stored = (showcase.folder() / saved["assets"][0]["name"]).read_bytes()
    response = client.get(saved["assets"][0]["url"], headers={"Range": "bytes=0-15"})
    assert response.status_code == 206 and response.content == stored[:16]


@pytest.mark.parametrize(
    ("container", "size", "stored"),
    [("mp4", "900x1600", (720, 1280)), ("webm", "320x240", (320, 240))],
)
def test_an_uploaded_video_is_played_only_once_re_encoded_for_a_browser(
    client, converted, tmp_path, container, size, stored
):
    """Stored as it came and re-encoded in the background (its faces blurred: test_faces.py),
    in place: an MP4 of H.264 and AAC whatever came in, its index first, at most 1280 px on the
    long side but never enlarged, as long as it was, and with the marker that says it is done.
    Until then it is neither played nor published."""
    source = recording(tmp_path, container, size)
    sent = probe(tmp_path / f"recording.{container}")
    assert container != "mp4" or not index_first(source)  # else faststart would prove nothing
    item = draft(client, kind="video")

    reply = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": (f"clip.{container}", source)}
    )

    assert reply.status_code == 200, reply.text
    assert reply.json()["converting"] is True
    (asset,) = reply.json()["assets"]
    assert asset["name"].endswith(".mp4") and asset["content_type"] == "video/mp4"
    done = converted(item["id"])
    assert done["assets"] == [asset] and done["conversion_error"] is None
    path = showcase.folder() / asset["name"]
    assert list(showcase.folder().iterdir()) == [path]  # no draft left beside it
    assert index_first(path.read_bytes())
    got = probe(path)
    assert got["format"]["tags"]["comment"] == showcase.WEB_MARKER
    assert (got["video"]["codec_name"], got["video"]["pix_fmt"]) == ("h264", "yuv420p")
    assert got["video"]["color_space"] == "bt709"  # labelled, so players need not guess
    assert (got["video"]["width"], got["video"]["height"]) == stored
    assert got["video"]["avg_frame_rate"] == f"{showcase.RATE}/1"
    assert float(got["format"]["duration"]) == pytest.approx(
        float(sent["format"]["duration"]), abs=1 / showcase.RATE
    )
    assert got["audio"]["codec_name"] == "aac"
    assert client.get(asset["url"]).headers["content-type"] == "video/mp4"


def test_a_video_deleted_while_it_is_re_encoded_stays_deleted(anonymous, tmp_path, monkeypatch):
    """The admin may delete a clip, or give it another video, while its video is re-encoded: the
    new file is then dropped, not put back where no clip has it."""
    path = showcase.folder() / "deleted.mp4"
    path.write_bytes(recording(tmp_path))
    frames = showcase._frames

    def deleted_meanwhile(stream):
        yield from frames(stream)
        path.unlink()  # after the last frame, while the encoder still reads the sound

    monkeypatch.setattr(showcase, "_frames", deleted_meanwhile)

    assert anonymous.portal.call(showcase.convert_stored) == []
    assert list(showcase.folder().iterdir()) == []  # neither the video nor its draft


def test_without_ffmpeg_the_worker_fails_loudly(anonymous, tmp_path, monkeypatch):
    """A missing ffprobe or ffmpeg is an error the worker logs and retries, not a video taken for
    deleted and skipped, which would leave every video unplayable without a word."""
    (showcase.folder() / "stored.mp4").write_bytes(recording(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))  # where neither can be found

    with pytest.raises(FileNotFoundError):
        anonymous.portal.call(showcase.convert_stored)


def test_a_ten_bit_video_is_re_encoded_too(client, converted, tmp_path):
    """An iPhone records HDR video as 10-bit HEVC, whose frames ffmpeg would hand over with two
    bytes a colour unless asked for one."""
    path = tmp_path / "hdr.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y"]
        + ["-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=30"]
        + ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error"]
        + [str(path)],
        check=True,
    )
    assert probe(path)["video"]["pix_fmt"] == "yuv420p10le"
    item = draft(client, kind="video")

    reply = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": ("hdr.mp4", path.read_bytes())}
    )

    assert reply.status_code == 200, reply.text
    done = converted(item["id"])
    assert done["conversion_error"] is None
    got = probe(showcase.folder() / done["assets"][0]["name"])
    assert (got["video"]["codec_name"], got["video"]["pix_fmt"]) == ("h264", "yuv420p")


def test_a_video_that_fails_unforeseen_does_not_hold_up_the_rest(anonymous, tmp_path, monkeypatch):
    """Something unforeseen in one video (the face detector failing on it, say) leaves that one
    unplayable, with the reason, and the videos after it are re-encoded still."""
    data = recording(tmp_path)
    first, second = showcase.folder() / "a.mp4", showcase.folder() / "b.mp4"  # in this order
    first.write_bytes(data)
    second.write_bytes(data)
    encode = showcase.encode

    def failing_on_the_first(source, target, form):
        if source == first:
            raise RuntimeError("the detector broke")
        return encode(source, target, form)

    monkeypatch.setattr(showcase, "encode", failing_on_the_first)

    assert anonymous.portal.call(showcase.convert_stored) == ["b.mp4"]
    assert showcase.failure("a.mp4") == "It could not be re-encoded: the detector broke"
    assert showcase.playable("b.mp4")


def test_a_video_ffmpeg_cannot_read_is_refused_and_not_kept(client):
    item = draft(client, kind="video")
    broken = b"\0\0\0\x18ftypisom" + b"\0" * 64  # an MP4's first box, and then nothing

    reply = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": ("broken.mp4", broken)}
    )

    assert reply.status_code == 422
    assert reply.json()["detail"].startswith("The video cannot be read: ")
    assert list(showcase.folder().iterdir()) == []  # neither the upload nor a half-made video
    assert client.get("/api/demo/manage").json()[0]["assets"] == []


async def same(inputs, input_type="query"):
    """A fake embedding model to which every image looks the same."""
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in inputs]


def stored_before(name: str, title: str, published: bool):
    """Adds a video clip as it was stored before videos were re-encoded; returns its id."""

    async def add(session):
        assets = [{"name": name, "label": f"{title}.mp4", "content_type": "video/mp4"}]
        clip = Showcase(
            title=title, kind="video", published=published, assets_json=json.dumps(assets)
        )
        session.add(clip)
        await session.commit()
        return clip.id

    return add


def test_videos_stored_before_are_re_encoded_in_place(client, converted, db, tmp_path, caplog):
    """A video stored as it was uploaded, before videos were re-encoded, is not played until it
    is redone, under its own name, so its clip and the clip's demo memories stay as they are. One
    done already is left alone. One ffmpeg cannot read is never played, the admin sees why, and
    it does not hold up the rest."""

    async def memories(session):
        rows = await session.exec(select(Memory).where(Memory.collection == demo.COLLECTION))
        return sorted((m.id, m.source, m.image_file) for m in rows)

    now = draft(client, kind="video", title="Uploaded now")
    files = {"files": ("now.mp4", recording(tmp_path))}
    assert client.post(f"/api/demo/clips/{now['id']}/media", files=files).status_code == 200
    new = showcase.folder() / converted(now["id"])["assets"][0]["name"]
    untouched = new.stat()
    old = showcase.folder() / "1-recorded-before.mp4"
    old.write_bytes(recording(tmp_path))  # as the phone made it
    broken = showcase.folder() / "0-broken.mp4"  # first in the order
    broken.write_bytes(b"\0\0\0\x18ftypisom")
    before = db(stored_before(old.name, "Recorded before", published=True))
    unreadable = db(stored_before(broken.name, "Broken", published=False))
    media = f"/api/demo/clips/{before}/media/{old.name}"
    assert client.get("/api/demo/clips").json() == [] and client.get(media).status_code == 404
    assert db(lambda session: demo.sync(session, same)) == 1  # its frames are blurred as stored
    stored = db(memories)

    names = client.portal.call(showcase.convert_stored)

    assert names == [old.name]
    assert index_first(old.read_bytes()) and showcase.web_ready(old)
    assert (new.stat().st_ino, new.stat().st_mtime_ns) == (untouched.st_ino, untouched.st_mtime_ns)
    assert [c["title"] for c in client.get("/api/demo/clips").json()] == ["Recorded before"]
    assert client.get(media).status_code == 200
    assert db(lambda session: demo.sync(session, same)) == 0  # nothing to store anew
    assert db(memories) == stored
    shown = next(c for c in client.get("/api/demo/manage").json() if c["id"] == unreadable)
    assert shown["converting"] is False and shown["conversion_error"]
    assert broken.read_bytes() == b"\0\0\0\x18ftypisom" and broken.name in caplog.text
    publish = client.put(f"/api/demo/clips/{unreadable}", json={**shown, "published": True})
    assert publish.status_code == 422
    assert publish.json()["detail"].startswith("The video could not be converted: ")


@pytest.mark.parametrize(
    "body", [{"title": "X", "kind": "no"}, {"title": ""}, {"title": "X", "image_seconds": 0}]
)
def test_invalid_config_rejected(client, body):
    assert client.post("/api/demo/clips", json=body).status_code == 422


def test_order_is_atomic_and_removing_last_asset_unpublishes(client, guest):
    first, second = draft(client, title="First"), draft(client, title="Second")
    all_ids = [c["id"] for c in client.get("/api/demo/manage").json()]
    order = [
        second["id"],
        first["id"],
        *[id for id in all_ids if id not in (first["id"], second["id"])],
    ]
    assert client.post("/api/demo/order", json={"ids": [first["id"]]}).status_code == 422
    assert client.post("/api/demo/order", headers=guest(), json={"ids": order}).status_code == 403
    assert [c["id"] for c in client.post("/api/demo/order", json={"ids": order}).json()] == order
    item = upload(client, first["id"]).json()
    assert (
        client.put(f"/api/demo/clips/{item['id']}", json={**item, "published": True}).status_code
        == 200
    )
    for asset in item["assets"]:
        response = client.delete(f"/api/demo/clips/{item['id']}/media/{asset['name']}")
        assert response.status_code == 200
    assert response.json()["published"] is False
    assert client.get("/api/demo/clips").json() == []


def test_limits_and_bad_video_leave_no_files(client, monkeypatch):
    item = draft(client, kind="video")
    assert (
        client.post(
            f"/api/demo/clips/{item['id']}/media", files={"files": ("x.mp4", b"not-video")}
        ).status_code
        == 415
    )
    monkeypatch.setattr(showcase, "MAX_UPLOAD", 10)
    assert (
        client.post(
            f"/api/demo/clips/{item['id']}/media", files={"files": ("x.mp4", b"x" * 11)}
        ).status_code
        == 413
    )
    assert list(showcase.folder().iterdir()) == []


def test_video_duration_is_updated_only_with_successful_media_upload(client, tmp_path):
    item = draft(client, kind="video", duration_s=60)
    video = recording(tmp_path)
    response = client.post(
        f"/api/demo/clips/{item['id']}/media",
        data={"video_duration_s": "4"},
        files={"files": ("video.mp4", video)},
    )
    assert response.status_code == 200 and response.json()["duration_s"] == 4
    assert (
        client.post(
            f"/api/demo/clips/{item['id']}/media",
            data={"video_duration_s": "2"},
            files={"files": ("bad.mp4", b"invalid")},
        ).status_code
        == 415
    )
    current = next(c for c in client.get("/api/demo/manage").json() if c["id"] == item["id"])
    assert current["duration_s"] == 4
    assert current["assets"] == response.json()["assets"]


def test_cues_are_saved_in_time_order_and_kept_when_left_out(client):
    item = draft(client, kind="video")
    assert item["cues"] == []
    cues = [
        {"id": "b", "at_s": 9, "text": "Stop watching", "stays": False, "stops": "a"},
        {"id": "a", "at_s": 2.5, "text": "Tell me when my bus comes", "until_s": 8, "answer": "Ok"},
    ]
    url = f"/api/demo/clips/{item['id']}"
    saved = client.put(url, json={**item, "cues": cues})
    assert saved.status_code == 200, saved.text
    assert [c["id"] for c in saved.json()["cues"]] == ["a", "b"]
    assert saved.json()["cues"][0] == {
        **cues[1],
        "stays": True,
        "stops": None,
        "kind": "prompt",
    }
    assert saved.json()["cues"][1] == {
        **cues[0],
        "until_s": None,
        "answer": "",
        "kind": "prompt",
    }
    without = {k: v for k, v in item.items() if k != "cues"}
    assert client.put(url, json=without).json()["cues"] == saved.json()["cues"]
    assert client.put(url, json={**item, "cues": [cues[0], cues[0]]}).status_code == 422
    assert client.put(url, json={**item, "cues": [{**cues[0], "text": ""}]}).status_code == 422


def test_demos_come_back_from_the_manifest_after_a_database_rebuild(client, db):
    from sqlmodel import delete

    from app.models import Showcase

    item = upload(client, draft(client, title="Kept")["id"]).json()
    cue = {"id": "a", "at_s": 1, "text": "Where are my keys?", "answer": "On the table."}
    saved = client.put(f"/api/demo/clips/{item['id']}", json={**item, "cues": [cue]}).json()

    async def rebuilt(session):  # what a wipe leaves: no rows, the files and the manifest
        await session.exec(delete(Showcase))
        await session.commit()
        return await showcase.restore(session)

    assert client.get("/api/demo/manage").json() == [saved]
    assert db(rebuilt) == 1
    assert client.get("/api/demo/manage").json() == [saved]
    assert client.get(saved["assets"][0]["url"]).status_code == 200
    # Deleting the last demo empties the manifest, so it does not come back.
    assert client.delete(f"/api/demo/clips/{item['id']}").status_code == 204
    assert db(showcase.restore) == 0
    assert client.get("/api/demo/manage").json() == []
