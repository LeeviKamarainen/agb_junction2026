import io

import anyio
import pytest
from PIL import Image

from app import media, vectors
from app.glasses import frames
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder
from app.search import TABLE

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions


def unit(*axes: int) -> list[float]:
    return [1.0 if i in axes else 0.0 for i in range(WIDTH)]


def jpeg(colour="orange") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), colour).save(buffer, "JPEG")
    return buffer.getvalue()


class Scene:
    """A fake embedder: every image embeds as `image`; known texts point along their own axis."""

    def __init__(self):
        self.image = unit(0)
        self.texts = {"a set of keys": unit(0), "a dog": unit(1)}
        self.calls = 0

    async def __call__(self, inputs):
        self.calls += 1
        return [
            self.image if i.startswith("data:image/") else self.texts.get(i, unit(WIDTH - 1))
            for i in inputs
        ]


@pytest.fixture
def scene(client):
    frames.reset_streams()
    fake = Scene()
    app.dependency_overrides[optional_embedder] = lambda: fake
    app.dependency_overrides[optional_chat] = lambda: None
    # The seeded example trigger is in every fresh database; start from none.
    for trigger in client.get("/api/glasses/triggers").json():
        client.delete(f"/api/glasses/triggers/{trigger['id']}")
    yield fake
    app.dependency_overrides.clear()


def send(client, source="single", stream_id="", colour="orange"):
    return client.post(
        "/api/glasses/frames",
        files={"frame": ("f.jpg", jpeg(colour), "image/jpeg")},
        data={"source": source, "stream_id": stream_id},
    )


def add_trigger(client, **fields):
    body = {
        "name": "Keys",
        "watch_for": "a set of keys",
        "instruction": "Say where the keys are.",
        "max_distance": 0.2,
        "cooldown_s": 60,
        **fields,
    }
    response = client.post("/api/glasses/triggers", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_single_frame_becomes_a_glasses_memory_with_no_message(client, scene):
    reply = send(client)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["stored"] is True and body["message"] is None and body["matches"] == []
    (memory,) = client.get("/api/memories").json()
    assert memory["id"] == body["memory_id"]
    assert memory["collection"] == "glasses" and memory["image_url"]


def test_a_still_stream_is_stored_once_but_singles_always(client, scene):
    first = send(client, "stream", "s1").json()
    again = send(client, "stream", "s1").json()
    other_stream = send(client, "stream", "s2").json()
    single = send(client).json()

    assert first["stored"] and other_stream["stored"] and single["stored"]
    assert not again["stored"] and "similar" in again["note"]
    assert len(client.get("/api/memories").json()) == 3
    assert len(list(media.media_dir().iterdir())) == 3  # the skipped frame's file is gone


def test_a_changed_scene_in_a_stream_is_stored(client, scene):
    send(client, "stream", "s1")
    scene.image = unit(1)

    assert send(client, "stream", "s1").json()["stored"] is True


def test_a_matching_trigger_fires_once_per_cooldown(client, scene):
    trigger = add_trigger(client)
    add_trigger(client, name="Dog", watch_for="a dog", instruction="Say hi to the dog.")

    first = send(client, "stream", "s1").json()
    second = send(client, "stream", "s1").json()

    assert first["message"] == {
        "type": "trigger",
        "title": "Keys",
        "text": "Say where the keys are.",  # no chat model: the instruction itself
        "trigger_id": trigger["id"],
    }
    assert [(m["name"], m["fired"]) for m in first["matches"]] == [("Keys", True), ("Dog", False)]
    assert first["matches"][0]["distance"] == pytest.approx(0.0, abs=1e-6)
    assert first["matches"][1]["distance"] == pytest.approx(1.0, abs=1e-6)
    assert second["message"] is None  # cooling down
    listed = client.get("/api/glasses/triggers").json()
    assert listed[0]["last_fired_at"] is not None and listed[1]["last_fired_at"] is None


def test_trigger_vectors_are_embedded_once(client, scene):
    add_trigger(client)
    send(client)
    calls = scene.calls
    send(client)

    assert scene.calls == calls + 1  # only the frame this time


def test_the_chat_model_writes_the_message(client, scene):
    seen = []

    async def chat(messages, tools):
        seen.append(messages)
        return {"role": "assistant", "content": "Your keys are on the hall table."}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)

    body = send(client).json()

    assert body["message"]["text"] == "Your keys are on the hall table."
    user = seen[0][-1]["content"]
    assert "Say where the keys are." in user[0]["text"]
    assert user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_far_or_disabled_triggers_do_not_fire(client, scene):
    add_trigger(client, watch_for="a dog")
    add_trigger(client, name="Off", enabled=False)

    body = send(client).json()

    assert body["message"] is None
    assert [m["name"] for m in body["matches"]] == ["Keys"]  # disabled ones are not checked


def test_editing_watch_for_embeds_it_again(client, scene):
    trigger = add_trigger(client, watch_for="a dog")
    assert send(client).json()["message"] is None

    edited = client.put(
        f"/api/glasses/triggers/{trigger['id']}",
        json={**trigger, "watch_for": "a set of keys"},
    )

    assert edited.status_code == 200
    assert send(client).json()["message"]["title"] == "Keys"


def test_trigger_crud_errors(client, scene):
    assert (
        client.put(
            "/api/glasses/triggers/999", json={"name": "x", "watch_for": "x", "instruction": "x"}
        ).status_code
        == 404
    )
    assert client.delete("/api/glasses/triggers/999").status_code == 404
    assert client.post("/api/glasses/triggers", json={"name": ""}).status_code == 422


def test_frames_need_an_image_and_an_embedding_model(client, scene):
    not_image = client.post(
        "/api/glasses/frames", files={"frame": ("f.txt", b"hello", "text/plain")}
    )
    app.dependency_overrides[optional_embedder] = lambda: None
    no_model = send(client)

    assert not_image.status_code == 415
    assert no_model.status_code == 503
    assert client.get("/api/memories").json() == []


def test_the_seeded_example_trigger_exists(client):
    names = [t["name"] for t in client.get("/api/glasses/triggers").json()]
    assert names == ["Keys"]


def test_a_chat_model_answering_oddly_still_shows_the_instruction(client, scene):
    async def odd_chat(messages, tools):
        return None

    app.dependency_overrides[optional_chat] = lambda: odd_chat  # not a dict
    add_trigger(client)

    body = send(client).json()

    assert body["message"]["text"] == "Say where the keys are."
    assert body["stored"] is True


def test_the_trigger_is_committed_as_fired_before_the_message_is_written(client, scene):
    fired_during_chat = []

    async def chat(messages, tools):
        response = await anyio.to_thread.run_sync(lambda: client.get("/api/glasses/triggers"))
        fired_during_chat.append(response.json()[0]["last_fired_at"])
        return {"role": "assistant", "content": "Keys!"}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)

    assert send(client).json()["message"]["text"] == "Keys!"
    assert fired_during_chat[0] is not None


def send_socket(ws, id=1, source="single", stream_id="", data=None, **extra):
    ws.send_json(
        {"type": "memory_frame", "id": id, "source": source, "stream_id": stream_id, **extra}
    )
    ws.send_bytes(jpeg() if data is None else data)
    return ws.receive_json()


def test_socket_memory_frames_store_and_fire_triggers(client, scene):
    trigger = add_trigger(client)
    with client.websocket_connect("/api/glasses/ws") as ws:
        first = send_socket(ws, source="stream", stream_id="phone")
        assert first["type"] == "memory_result" and first["id"] == 1
        assert first["result"]["stored"]
        assert first["result"]["message"]["trigger_id"] == trigger["id"]
        second = send_socket(ws, id=2, source="stream", stream_id="phone")
        assert not second["result"]["stored"]
        assert second["result"]["message"] is None
        single = send_socket(ws, id=3, seen_at="2026-11-14T12:00:00")
        assert single["result"]["stored"]
    assert len(client.get("/api/memories").json()) == 2


@pytest.mark.parametrize(
    "data,status", [(b"not an image", 415), (b"x" * (10 * 1024 * 1024 + 1), 413)]
)
def test_socket_memory_frame_errors_do_not_close_the_connection(client, scene, data, status):
    with client.websocket_connect("/api/glasses/ws") as ws:
        error = send_socket(ws, data=data)
        assert error["type"] == "error" and error["status"] == status and error["id"] == 1
        assert send_socket(ws, id=2)["result"]["stored"]


def test_socket_memory_frame_needs_an_embedder(client, scene):
    app.dependency_overrides[optional_embedder] = lambda: None
    with client.websocket_connect("/api/glasses/ws") as ws:
        assert send_socket(ws)["status"] == 503


def test_socket_memory_frames_do_not_queue_and_ping_stays_responsive(client, scene):
    import threading

    checking, release = threading.Event(), threading.Event()

    async def slow(inputs):
        checking.set()
        assert await anyio.to_thread.run_sync(release.wait, 5)
        return await scene(inputs)

    app.dependency_overrides[optional_embedder] = lambda: slow
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "memory_frame", "id": 1})
        ws.send_bytes(jpeg())
        assert checking.wait(5)
        try:
            assert send_socket(ws, id=2)["status"] == 429
            ws.send_json({"type": "ping", "id": 3})
            assert ws.receive_json() == {"type": "pong", "id": 3}
        finally:
            release.set()
        assert ws.receive_json()["result"]["stored"]


def test_frames_and_triggers_belong_to_the_login_over_http_and_socket(client, guest, scene):
    aino, ville = guest("aino"), guest("ville")
    trigger = client.post(
        "/api/glasses/triggers",
        headers=aino,
        json={"name": "Private", "watch_for": "a set of keys", "instruction": "Private message"},
    ).json()
    assert client.get("/api/glasses/triggers", headers=ville).json() == []
    assert client.delete(f"/api/glasses/triggers/{trigger['id']}", headers=ville).status_code == 404
    assert (
        client.put(
            f"/api/glasses/triggers/{trigger['id']}",
            headers=ville,
            json={"name": "Stolen", "watch_for": "a dog", "instruction": "changed"},
        ).status_code
        == 404
    )

    with client.websocket_connect("/api/glasses/ws", headers=aino) as ws:
        result = send_socket(ws, source="stream", stream_id="same")["result"]
        assert result["message"]["title"] == "Private"
    assert client.get("/api/memories", headers=ville).json() == []
    assert len(client.get("/api/memories", headers=aino).json()) == 1
    # Deduplication is per owner even when two clients choose the same stream id.
    other = client.post(
        "/api/glasses/frames",
        headers=ville,
        files={"frame": ("f.jpg", jpeg(), "image/jpeg")},
        data={"source": "stream", "stream_id": "same"},
    ).json()
    assert other["stored"] and other["message"] is None
    assert other["matches"] == []


def test_an_open_socket_refuses_work_after_its_guest_expires(client, guest, scene, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from fastapi import WebSocketDisconnect

    from app.glasses import channel

    class Later:
        @staticmethod
        def now(tz):
            return datetime.now(UTC) + timedelta(minutes=61)

    with client.websocket_connect("/api/glasses/ws", headers=guest()) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        monkeypatch.setattr(channel, "datetime", Later)
        ws.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as expired:
            ws.receive_json()
        assert expired.value.code == 1008
