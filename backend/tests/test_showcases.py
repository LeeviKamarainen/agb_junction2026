import io

import pytest
from PIL import Image

from app import showcase


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


def test_video_replacement_and_range_requests(client):
    item = draft(client, kind="video")
    video = b"\0\0\0\x18ftypisom" + b"\0" * 64
    first = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": ("one.mp4", video)}
    ).json()
    old = first["assets"][0]["name"]
    second = client.post(
        f"/api/demo/clips/{item['id']}/media", files={"files": ("two.mp4", video)}
    ).json()
    assert not (showcase.folder() / old).exists()
    assert len(second["assets"]) == 1
    saved = client.put(f"/api/demo/clips/{item['id']}", json={**second, "published": True}).json()
    response = client.get(saved["assets"][0]["url"], headers={"Range": "bytes=0-15"})
    assert response.status_code == 206 and response.content == video[:16]


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


def test_video_duration_is_updated_only_with_successful_media_upload(client, monkeypatch):
    item = draft(client, kind="video", duration_s=60)
    video = b"\0\0\0\x18ftypisom" + b"\0" * 64
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
