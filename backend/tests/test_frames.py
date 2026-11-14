import io
import json

import anyio
import pytest
from PIL import Image

from app import media, vectors
from app.glasses import frames, scores
from app.llm import UpstreamError
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
        self.say: str | None = "I see your keys."  # what the chat model finds; None: false alarm
        self.asked = 0

    async def chat(self, messages, tools):
        self.asked += 1
        return {"role": "assistant", "content": json.dumps({"say": self.say})}

    async def __call__(self, inputs, input_type="query"):
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
    app.dependency_overrides[optional_chat] = lambda: fake.chat
    yield fake
    app.dependency_overrides.clear()


def send(client, source="single", stream_id="", colour="orange", headers=None):
    return client.post(
        "/api/glasses/frames",
        headers=headers,
        files={"frame": ("f.jpg", jpeg(colour), "image/jpeg")},
        data={"source": source, "stream_id": stream_id},
    )


class Clock:
    """Stands in for `time` in app.glasses.frames, so a test can let seconds pass."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def toward_keys(amount: float) -> list[float]:
    """An image whose similarity to "a set of keys" (axis 0) is `amount`."""
    vector = unit(2)
    vector[0], vector[2] = amount, (1 - amount**2) ** 0.5
    return vector


def warm_up(client, scene, stream_id="s1", headers=None, clock=None):
    """Stream the quiet frames a trigger's level is measured on (the first of them is stored),
    then put the keys in view for the next frame. A test that stops the clock passes it, so
    that the frames are half a second apart."""
    for i in range(scores.MIN_FRAMES):
        scene.image = toward_keys(0.60 + 0.01 * (i % 3))
        assert send(client, "stream", stream_id, headers=headers).status_code == 200
        if clock is not None:
            clock.now += 0.5
    scene.image = unit(0)


def add_trigger(
    client, headers=None, enabled=True, instruction="Say where the keys are.", **fields
):
    """A goal (its text is `instruction`) with one trigger, which is returned."""
    trigger = {"name": "Keys", "watch_for": "a set of keys", **fields}
    response = client.post(
        "/api/glasses/goals", headers=headers, json={"text": instruction, "triggers": [trigger]}
    )
    assert response.status_code == 201, response.text
    goal = response.json()
    if not enabled:
        off = client.put(
            f"/api/glasses/goals/{goal['id']}", headers=headers, json={"enabled": False}
        )
        assert off.status_code == 200, off.text
    return goal["triggers"][0]


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


def test_a_message_is_shown_and_the_wearer_gets_five_seconds_to_read_it(client, scene, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    trigger = add_trigger(client)
    add_trigger(client, name="Dog", watch_for="a dog", instruction="Say hi to the dog.")
    warm_up(client, scene, clock=clock)

    first = send(client, "stream", "s1").json()
    clock.now += scores.SKIP_S + 0.5
    still_reading = send(client, "stream", "s1").json()
    clock.now += scores.READ_S - scores.SKIP_S
    later = send(client, "stream", "s1").json()

    assert first["message"] == {
        "type": "trigger",
        "title": "Keys",
        "text": "I see your keys.",
        "trigger_id": trigger["id"],
        "goal_id": trigger["goal_id"],
    }
    assert [(m["name"], m["fired"]) for m in first["matches"]] == [("Keys", True), ("Dog", False)]
    assert first["matches"][0]["distance"] == pytest.approx(0.0, abs=1e-6)
    assert first["matches"][1]["distance"] == pytest.approx(1.0, abs=1e-6)
    assert still_reading["message"] is None and not still_reading["matches"][0]["fired"]
    assert later["message"] is not None  # still new against the last 20 seconds
    assert scene.asked == 2
    listed = client.get("/api/glasses/triggers").json()
    assert listed[0]["last_fired_at"] is not None and listed[1]["last_fired_at"] is None


def test_after_a_false_alarm_the_stream_is_quiet_for_two_seconds_only(client, scene, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    add_trigger(client)
    warm_up(client, scene, clock=clock)
    scene.image, scene.say = toward_keys(0.70), None

    alarm = send(client, "stream", "s1").json()
    scene.image, scene.say = unit(0), "I see your keys."  # now they do come into view
    clock.now += 0.5
    too_soon = send(client, "stream", "s1").json()
    clock.now += scores.SKIP_S
    seen = send(client, "stream", "s1").json()

    assert alarm["message"] is None and alarm["matches"][0]["fired"]
    assert too_soon["message"] is None and not too_soon["matches"][0]["fired"]
    assert seen["message"]["text"] == "I see your keys."


def test_a_false_alarm_reaches_nobody_and_raises_the_level(client, scene, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    add_trigger(client)
    warm_up(client, scene, clock=clock)
    scene.image = toward_keys(0.70)  # something that looks a little like keys
    scene.say = None  # the chat model looks at the frame and does not find them

    alarm = send(client, "stream", "s1").json()
    clock.now += scores.SKIP_S + 0.5
    same_again = send(client, "stream", "s1").json()

    assert alarm["message"] is None and alarm["matches"][0]["fired"]
    assert "false alarm" in alarm["note"]
    score, measured = alarm["matches"][0]["score"], alarm["matches"][0]["level"]
    raised = client.get("/api/glasses/triggers").json()[0]["level"]
    assert measured < score < raised == pytest.approx(score + frames.RAISE_BY, abs=1e-3)
    # The same look scores no higher, so the chat model is not asked about it again.
    assert not same_again["matches"][0]["fired"] and scene.asked == 1


@pytest.mark.parametrize(
    "answer",
    ['```json\n{"say": null}\n```', '{"say": ""}', "null", "", "I do not see any keys here."],
)
def test_nothing_but_a_message_in_json_reaches_the_wearer(client, scene, answer):
    async def chat(messages, tools):
        return {"role": "assistant", "content": answer}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)
    warm_up(client, scene)

    assert send(client, "stream", "s1").json()["message"] is None


def test_without_a_working_chat_model_a_firing_says_nothing(client, scene, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    add_trigger(client)
    warm_up(client, scene, clock=clock)

    async def down(messages, tools):
        raise UpstreamError("The chat model answered 500")

    app.dependency_overrides[optional_chat] = lambda: down
    failed = send(client, "stream", "s1").json()
    clock.now += scores.SKIP_S + 0.5
    app.dependency_overrides[optional_chat] = lambda: None
    missing = send(client, "stream", "s1").json()

    assert failed["message"] is None and "chat model failed" in failed["note"]
    assert missing["message"] is None and "no chat model" in missing["note"]
    assert failed["stored"]  # the frame is a memory all the same
    # Nobody looked at the frame, so the level stays where it was.
    measured = failed["matches"][0]["level"]
    assert client.get("/api/glasses/triggers").json()[0]["level"] == pytest.approx(
        measured, abs=1e-4
    )


def test_plain_words_are_not_taken_for_a_false_alarm(client, scene):
    async def chat(messages, tools):
        return {"role": "assistant", "content": "I do not see any keys here."}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)
    warm_up(client, scene)

    body = send(client, "stream", "s1").json()

    assert body["message"] is None and "did not answer with JSON" in body["note"]
    # Nobody said it was a false alarm, so the level is the one measured.
    assert client.get("/api/glasses/triggers").json()[0]["level"] == pytest.approx(
        body["matches"][0]["level"], abs=1e-4
    )


def test_a_false_alarm_never_raises_the_level_out_of_reach(client, scene):
    add_trigger(client)
    warm_up(client, scene)
    scene.say = None

    alarm = send(client, "stream", "s1").json()  # the keys fill the view: a score near 0.4

    assert alarm["matches"][0]["score"] > scores.MAX_LEVEL
    assert client.get("/api/glasses/triggers").json()[0]["level"] == scores.MAX_LEVEL


def test_a_trigger_deleted_while_the_chat_model_looks_is_no_error(client, scene):
    trigger = add_trigger(client)
    warm_up(client, scene)

    async def chat(messages, tools):
        gone = await anyio.to_thread.run_sync(
            lambda: client.delete(f"/api/glasses/triggers/{trigger['id']}")
        )
        assert gone.status_code == 204
        return {"role": "assistant", "content": '{"say": null}'}

    app.dependency_overrides[optional_chat] = lambda: chat

    reply = send(client, "stream", "s1")

    assert reply.status_code == 200 and reply.json()["message"] is None


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
        return {"role": "assistant", "content": '{"say": "Your keys are on the hall table."}'}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)
    warm_up(client, scene)

    body = send(client, "stream", "s1").json()

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
    warm_up(client, scene)
    assert send(client, "stream", "s1").json()["message"] is None

    edited = client.put(
        f"/api/glasses/triggers/{trigger['id']}",
        json={**trigger, "watch_for": "a set of keys"},
    )

    assert edited.status_code == 200
    assert send(client, "stream", "s1").json()["message"]["title"] == "Keys"


def test_trigger_crud_errors(client, scene):
    assert (
        client.put("/api/glasses/triggers/999", json={"name": "x", "watch_for": "x"}).status_code
        == 404
    )
    assert client.delete("/api/glasses/triggers/999").status_code == 404
    assert client.post("/api/glasses/triggers", json={"name": "x"}).status_code == 405


def test_frames_need_an_image_and_an_embedding_model(client, scene):
    not_image = client.post(
        "/api/glasses/frames", files={"frame": ("f.txt", b"hello", "text/plain")}
    )
    app.dependency_overrides[optional_embedder] = lambda: None
    no_model = send(client)

    assert not_image.status_code == 415
    assert no_model.status_code == 503
    assert client.get("/api/memories").json() == []


def test_a_chat_model_answering_oddly_disturbs_nobody(client, scene):
    async def odd_chat(messages, tools):
        return None

    app.dependency_overrides[optional_chat] = lambda: odd_chat  # not a dict
    add_trigger(client)
    warm_up(client, scene)

    body = send(client, "stream", "s1").json()

    assert body["message"] is None
    assert body["stored"] is True


def test_the_trigger_is_committed_as_fired_before_the_message_is_written(client, scene):
    fired_during_chat = []

    async def chat(messages, tools):
        response = await anyio.to_thread.run_sync(lambda: client.get("/api/glasses/triggers"))
        fired_during_chat.append(response.json()[0]["last_fired_at"])
        return {"role": "assistant", "content": '{"say": "Keys!"}'}

    app.dependency_overrides[optional_chat] = lambda: chat
    add_trigger(client)
    warm_up(client, scene)

    assert send(client, "stream", "s1").json()["message"]["text"] == "Keys!"
    assert fired_during_chat[0] is not None


def test_a_trigger_measures_its_level_then_fires_on_what_comes_into_view(
    client, scene, monkeypatch
):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    trigger = add_trigger(client)
    assert trigger["level"] is None and trigger["sigmas"] == 3

    def frame(amount):
        scene.image = toward_keys(amount)
        clock.now += 0.5
        (match,) = send(client, "stream", "s1").json()["matches"]
        return match

    # Ten quiet frames: scored, but no level yet, so nothing can fire.
    quiet = [frame(0.60 + 0.01 * (i % 3)) for i in range(10)]
    assert all(m["level"] is None and not m["fired"] for m in quiet)

    # The eleventh has ten before it: the level is measured and kept on the trigger.
    still_quiet = frame(0.61)
    level = client.get("/api/glasses/triggers").json()[0]["level"]
    assert still_quiet["level"] == pytest.approx(level, abs=1e-4) and 0 < level < 0.1
    assert not still_quiet["fired"]

    # The keys come into view: still far from the text itself, but well over its own noise.
    seen = frame(0.80)
    assert seen["fired"] and seen["score"] == pytest.approx(0.19, abs=0.02)
    assert seen["distance"] == pytest.approx(0.2, abs=1e-3)


def test_a_single_photo_fires_nothing(client, scene):
    add_trigger(client)

    body = send(client).json()  # identical to the trigger's text, but there is no stream

    assert body["message"] is None
    assert body["matches"][0]["score"] is None and not body["matches"][0]["fired"]


def test_frames_before_a_trigger_exists_count_toward_its_level(client, scene, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    for i in range(12):
        scene.image = toward_keys(0.60 + 0.01 * (i % 3))
        clock.now += 0.5
        send(client, "stream", "s1")

    add_trigger(client)
    scene.image = toward_keys(0.80)
    clock.now += 0.5
    (match,) = send(client, "stream", "s1").json()["matches"]

    assert match["level"] is not None and match["fired"]


def test_editing_a_trigger_measures_its_level_again(client, scene, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames, "time", clock)
    trigger = add_trigger(client)
    for i in range(12):
        scene.image = toward_keys(0.60 + 0.01 * (i % 3))
        clock.now += 0.5
        send(client, "stream", "s1")
    assert client.get("/api/glasses/triggers").json()[0]["level"] is not None

    renamed = client.put(f"/api/glasses/triggers/{trigger['id']}", json={**trigger, "name": "K"})
    edited = client.put(f"/api/glasses/triggers/{trigger['id']}", json={**trigger, "sigmas": 2})

    assert renamed.json()["level"] is not None  # the name does not change what is measured
    assert edited.json()["level"] is None


def send_socket(ws, id=1, source="single", stream_id="", data=None, **extra):
    ws.send_json(
        {"type": "memory_frame", "id": id, "source": source, "stream_id": stream_id, **extra}
    )
    ws.send_bytes(jpeg() if data is None else data)
    return ws.receive_json()


def test_socket_memory_frames_store_and_fire_triggers(client, scene):
    trigger = add_trigger(client)
    warm_up(client, scene, "phone")
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
    # The first frame of the warm-up, the frame that fired and the single photo.
    assert len(client.get("/api/memories").json()) == 3


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

    async def slow(inputs, input_type="query"):
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
    trigger = add_trigger(client, headers=aino, name="Private")
    assert client.get("/api/glasses/triggers", headers=ville).json() == []
    assert client.delete(f"/api/glasses/triggers/{trigger['id']}", headers=ville).status_code == 404
    assert (
        client.put(
            f"/api/glasses/triggers/{trigger['id']}",
            headers=ville,
            json={"name": "Stolen", "watch_for": "a dog"},
        ).status_code
        == 404
    )

    warm_up(client, scene, "same", headers=aino)
    with client.websocket_connect("/api/glasses/ws", headers=aino) as ws:
        result = send_socket(ws, source="stream", stream_id="same")["result"]
        assert result["message"]["title"] == "Private"
    assert client.get("/api/memories", headers=ville).json() == []
    assert len(client.get("/api/memories", headers=aino).json()) == 2  # warm-up and the frame
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
