"""The admin's stream wall: the newest frame of every client (app/glasses/feeds.py)."""

import io

import pytest
from PIL import Image

from app.glasses import feeds
from app.llm import get_chat, get_embedder
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder


def jpeg(colour="orange") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), colour).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def no_feeds():
    feeds.reset()
    yield
    feeds.reset()


@pytest.fixture
def no_models(client):
    """No embedding model: frames still reach the wall, then get a 503."""
    app.dependency_overrides[optional_embedder] = lambda: None
    app.dependency_overrides[optional_chat] = lambda: None
    yield
    app.dependency_overrides.clear()


def send(test_client, headers=None, **data):
    """Post a frame; `data` are the form fields, `client` among them."""
    files = {"frame": ("f.jpg", jpeg(), "image/jpeg")}
    return test_client.post("/api/glasses/frames", files=files, data=data, headers=headers)


def test_only_the_admin_sees_the_streams(client, guest, no_models):
    aino = guest("aino")
    send(client, aino, source="stream", stream_id="s1", client="live")

    assert client.get("/api/admin/streams", headers=aino).status_code == 403
    (feed,) = client.get("/api/admin/streams").json()
    assert client.get(f"/api/admin/streams/{feed['id']}/frame", headers=aino).status_code == 403
    client.cookies.clear()
    assert client.get("/api/admin/streams").status_code == 401


def test_each_client_and_stream_is_its_own_feed_with_its_newest_frame(client, guest, no_models):
    aino, ville = guest("aino"), guest("ville")
    for _ in range(3):
        reply = send(client, aino, source="stream", stream_id="s1", client="live", store="false")
        assert reply.status_code == 503  # no embedding model: the wall shows it anyway
    send(client, ville, source="stream", stream_id="s2", client="harness")
    send(client, ville, client="harness")  # a single photo

    listed = client.get("/api/admin/streams").json()

    by_client = {(f["client"], f["kind"]): f for f in listed}
    assert set(by_client) == {("live", "stream"), ("harness", "stream"), ("harness", "photo")}
    live = by_client[("live", "stream")]
    assert live["frames"] == 3 and live["demo"] is True and live["live"] is True
    assert live["user"].startswith("guest-") and live["stream_id"] == "s1"
    assert by_client[("harness", "stream")]["demo"] is False
    frame = client.get(f"/api/admin/streams/{live['id']}/frame")
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
    assert frame.headers["cache-control"] == "no-store"
    assert Image.open(io.BytesIO(frame.content)).size == (64, 36)


def test_a_client_that_does_not_name_itself_is_web_and_names_are_cleaned(client, no_models):
    send(client, source="stream", stream_id="a")
    send(client, source="stream", stream_id="b", client="  My App <script>  ")

    assert {f["client"] for f in client.get("/api/admin/streams").json()} == {"web", "myappscript"}


def test_socket_frames_are_the_glasses_app_unless_named(client, no_models):
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "memory_frame", "id": 1, "source": "stream", "stream_id": "g"})
        ws.send_bytes(jpeg())
        assert ws.receive_json()["status"] == 503
        header = {"type": "memory_frame", "id": 2, "source": "single", "client": "harness"}
        ws.send_json(header)
        ws.send_bytes(jpeg("blue"))
        assert ws.receive_json()["status"] == 503

    kinds = {(f["client"], f["kind"]) for f in client.get("/api/admin/streams").json()}
    assert kinds == {("glasses", "stream"), ("harness", "photo")}


def test_a_quiet_feed_is_dropped(client, no_models, monkeypatch):
    send(client, source="stream", stream_id="s", client="live")
    (feed,) = client.get("/api/admin/streams").json()
    clock = feeds.time.monotonic() + feeds.IDLE_S + 1
    monkeypatch.setattr(feeds.time, "monotonic", lambda: clock)

    assert client.get("/api/admin/streams").json() == []
    assert client.get(f"/api/admin/streams/{feed['id']}/frame").status_code == 404


def test_each_feed_shows_what_its_user_asked_and_set(client, guest, no_models):
    async def chat(_messages, _tools):
        return {"role": "assistant", "content": "Rye toast."}

    async def embed(inputs, input_type="query"):
        return [[1.0] + [0.0] * 767 for _ in inputs]

    async def writes_triggers(_messages, _tools):
        return {"role": "assistant", "content": '{"triggers": [{"watch_for": "a stool"}]}'}

    app.dependency_overrides[get_chat] = lambda: chat
    app.dependency_overrides[get_embedder] = lambda: embed
    app.dependency_overrides[optional_chat] = lambda: writes_triggers  # "remind me" makes a goal
    aino, ville = guest("aino"), guest("ville")
    send(client, aino, source="stream", stream_id="s1", client="live")
    send(client, ville, source="stream", stream_id="s2", client="live")
    command = "/api/glasses/command"
    client.post(command, json={"question": "where are my keys?"}, headers=aino)
    client.post(command, json={"question": "remind me about the stool"}, headers=aino)
    asked = client.post("/api/ask/stream", json={"question": "what did I eat?"}, headers=aino)
    assert asked.status_code == 200, asked.text

    listed = {f["stream_id"]: f for f in client.get("/api/admin/streams").json()}

    log = [(e["kind"], e["text"], e["via"]) for e in listed["s1"]["events"]]
    assert log == [
        ("question", "what did I eat?", "chat"),
        ("goal", "remind me about the stool", "glasses"),
        ("question", "where are my keys?", "glasses"),
    ]
    assert listed["s2"]["events"] == []  # ville's feed: nothing of aino's


def test_a_trigger_that_fires_shows_in_the_log(client, monkeypatch):
    from app.glasses import frames as frames_module
    from app.glasses.frames import FrameReply, GlassesMessage, TriggerMatch

    async def fired(*_args, **_kwargs):
        match = TriggerMatch(trigger_id=1, goal_id=1, name="Keys", distance=0.1, fired=True)
        message = GlassesMessage(type="trigger", title="Keys", text="Your keys are on the bench.")
        return FrameReply(stored=False, matches=[match], message=message)

    monkeypatch.setattr(frames_module, "receive", fired)
    app.dependency_overrides[optional_embedder] = lambda: object()
    try:
        assert send(client, source="stream", stream_id="s", client="live").status_code == 200
    finally:
        app.dependency_overrides.clear()

    (feed,) = client.get("/api/admin/streams").json()
    assert [(e["kind"], e["text"]) for e in feed["events"]] == [
        ("trigger", "Keys: Your keys are on the bench.")
    ]


def test_a_streams_full_page_logs_every_frame_and_the_chat_models_steps(client, guest):
    from app.glasses import frames as frames_module

    async def chat(_messages, tools):
        return {"role": "assistant", "content": "On the bench."}

    async def embed(inputs, input_type="query"):
        return [[1.0] + [0.0] * 767 for _ in inputs]

    frames_module.reset_streams()
    app.dependency_overrides[optional_embedder] = lambda: embed
    app.dependency_overrides[optional_chat] = lambda: chat
    app.dependency_overrides[get_chat] = lambda: chat
    app.dependency_overrides[get_embedder] = lambda: embed
    aino = guest("aino")
    try:
        for _ in range(2):
            reply = send(client, aino, source="stream", stream_id="s1", client="live")
            assert reply.status_code == 200, reply.text
        asked = client.post("/api/ask/stream", json={"question": "keys?"}, headers=aino)
        assert asked.status_code == 200
    finally:
        app.dependency_overrides.clear()
    (feed,) = client.get("/api/admin/streams").json()

    page = client.get(f"/api/admin/streams/{feed['id']}")

    assert page.status_code == 200
    log = page.json()["log"]
    kinds = [entry["kind"] for entry in log]
    assert kinds[:2] == ["answer", "question"]  # newest first
    frames = [entry for entry in log if entry["kind"] == "frame"]
    assert len(frames) == 2
    assert frames[-1]["text"].startswith("stored as memory #")  # the first frame of the stream
    assert frames[-1]["detail"]["stored"] is True and frames[-1]["ms"] is not None
    assert "not stored" in frames[0]["text"]  # the second is much like the first
    # The tiles on the wall get the short log only, without details.
    assert [e["kind"] for e in feed["events"]] == ["question"]
    assert client.get(f"/api/admin/streams/{feed['id']}", headers=aino).status_code == 403


def test_a_frame_that_cannot_be_embedded_is_logged_as_an_error(client, no_models):
    send(client, source="stream", stream_id="s", client="live")
    (feed,) = client.get("/api/admin/streams").json()

    (entry,) = client.get(f"/api/admin/streams/{feed['id']}").json()["log"]

    assert entry["kind"] == "error" and "EMBED_URL" in entry["text"]
    assert client.get("/api/admin/streams/nope").status_code == 404


def test_the_admin_asks_about_a_stream_as_its_user_with_the_newest_frame(client, guest, no_models):
    seen = []

    async def chat(messages, tools):
        if not messages[0]["content"].startswith("You answer questions"):  # writing triggers
            return {"role": "assistant", "content": '{"triggers": [{"watch_for": "a stool"}]}'}
        seen.append((messages, [tool["function"]["name"] for tool in tools or []]))
        if len(seen) == 1:
            call = {"name": "start_watching", "arguments": '{"goal": "warn me about the stool"}'}
            calls = [{"id": "c1", "function": call}]
            return {"role": "assistant", "content": "", "tool_calls": calls}
        return {"role": "assistant", "content": "Watching for the stool."}

    async def embed(inputs, input_type="query"):
        return [[1.0] + [0.0] * 767 for _ in inputs]

    app.dependency_overrides[get_chat] = lambda: chat
    app.dependency_overrides[get_embedder] = lambda: embed
    aino = guest("aino")
    send(client, aino, source="stream", stream_id="s1", client="live")
    (feed,) = client.get("/api/admin/streams").json()
    url = f"/api/admin/streams/{feed['id']}"

    assert (
        client.post(f"{url}/ask/stream", json={"question": "hi"}, headers=aino).status_code == 403
    )
    asked = client.post(f"{url}/ask/stream", json={"question": "watch for the stool"})

    assert asked.status_code == 200, asked.text
    messages, tools = seen[0]
    assert "start_watching" in tools
    assert "operator watching this user's live camera stream" in messages[0]["content"]
    parts = messages[1]["content"]
    assert parts[0]["text"] == "watch for the stool"
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    goals = client.get("/api/glasses/goals", headers=aino).json()
    assert [g["text"] for g in goals] == ["warn me about the stool"]  # the stream's user's
    assert client.get("/api/glasses/goals").json() == []  # not the admin's
    log = client.get(url).json()["log"]
    assert ("question", "watch for the stool", "admin") in [
        (e["kind"], e["text"], e["via"]) for e in log
    ]


def test_the_admin_lists_and_removes_a_streams_goals(client, guest, no_models):
    aino, ville = guest("aino"), guest("ville")
    draft = {"name": "Stool", "watch_for": "a bar stool"}
    goal = client.post(
        "/api/glasses/goals", json={"text": "the stool", "triggers": [draft]}, headers=aino
    ).json()
    theirs = client.post(
        "/api/glasses/goals", json={"text": "the bus", "triggers": [draft]}, headers=ville
    ).json()
    send(client, aino, source="stream", stream_id="s1", client="live")
    (feed,) = client.get("/api/admin/streams").json()
    url = f"/api/admin/streams/{feed['id']}/goals"

    assert client.get(url, headers=aino).status_code == 403
    assert [g["text"] for g in client.get(url).json()] == ["the stool"]
    assert client.delete(f"{url}/{theirs['id']}").status_code == 404  # ville's, not this user's
    assert client.delete(f"{url}/{goal['id']}").status_code == 204
    assert client.get(url).json() == []
    assert client.get("/api/admin/streams/nope/goals").status_code == 404


def test_a_stream_that_stopped_can_still_be_asked_about(client, guest, no_models, monkeypatch):
    aino = guest("aino")
    send(client, aino, source="stream", stream_id="s1", client="live")
    (feed,) = client.get("/api/admin/streams").json()
    monkeypatch.setattr(feeds, "IDLE_S", -1.0)
    assert client.get("/api/admin/streams").json() == []

    assert client.get(f"/api/admin/streams/{feed['id']}/goals").status_code == 200
