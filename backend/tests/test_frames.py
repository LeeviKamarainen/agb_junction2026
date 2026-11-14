import io

import pytest
from PIL import Image

from app import media, vectors
from app.glasses import frames, remote_memory
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

    def __call__(self, inputs):
        self.calls += 1
        return [
            self.image if i.startswith("data:image/") else self.texts.get(i, unit(WIDTH - 1))
            for i in inputs
        ]


@pytest.fixture
def scene(client, monkeypatch):
    monkeypatch.setattr(remote_memory, "configured", lambda: False)
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

    def chat(messages, tools):
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
    app.dependency_overrides[optional_chat] = lambda: lambda messages, tools: None  # not a dict
    add_trigger(client)

    body = send(client).json()

    assert body["message"]["text"] == "Say where the keys are."
    assert body["stored"] is True


def test_the_trigger_is_committed_as_fired_before_the_message_is_written(client, scene):
    fired_during_chat = []

    def chat(messages, tools):
        fired_during_chat.append(client.get("/api/glasses/triggers").json()[0]["last_fired_at"])
        return {"role": "assistant", "content": "Keys!"}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)

    assert send(client).json()["message"]["text"] == "Keys!"
    assert fired_during_chat[0] is not None
