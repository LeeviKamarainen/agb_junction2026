import io
import threading
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from fastapi import WebSocketDisconnect
from PIL import Image

from app import media, vectors
from app.glasses import reminders
from app.llm import UpstreamError
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder
from app.search import TABLE, add_memory

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions


def make_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), "orange").save(buffer, "JPEG")
    return buffer.getvalue()


JPEG = make_jpeg()


@pytest.fixture(autouse=True)
def no_models():
    """No models unless a test sets them, even when backend/.env has them."""
    app.dependency_overrides[optional_chat] = lambda: None
    app.dependency_overrides[optional_embedder] = lambda: None
    yield
    app.dependency_overrides.pop(optional_chat, None)
    app.dependency_overrides.pop(optional_embedder, None)


def use_models(chat=None, embed=None):
    if chat:
        app.dependency_overrides[optional_chat] = lambda: chat
    if embed:
        app.dependency_overrides[optional_embedder] = lambda: embed


async def fake_embed(texts, input_type="query"):
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in texts]


def says(text):
    """A chat model that always answers `text`, without calling tools."""

    async def chat(_messages, _tools):
        return {"role": "assistant", "content": text}

    return chat


async def broken(*_args):
    raise UpstreamError("the model server is down")


def buttons(node: dict) -> list[str]:
    """Every button action in a layout, depth first."""
    if node["type"] == "button":
        return [node["action"]]
    return [action for child in node.get("children", []) for action in buttons(child)]


def texts(node: dict) -> str:
    if node["type"] == "text":
        return node["value"]
    return " ".join(texts(child) for child in node.get("children", []))


def test_home_is_an_empty_display(client):
    reply = client.get("/api/glasses/home").json()

    assert reply["screen"] == {**reply["screen"], "type": "flexbox", "children": []}


def test_no_screen_has_buttons(client):
    """The wearer acts through the Neural Band, never on the display."""
    for body in [
        {"action": "ask"},
        {"question": "where are my keys?"},
        {"question": "remind me about the stool"},
        {"question": "banana"},
    ]:
        reply = client.post("/api/glasses/command", json=body).json()
        assert buttons(reply["screen"]) == [], body


def test_shown_screens_clear_themselves(client):
    reply = client.post("/api/glasses/command", json={"question": "where are my keys?"}).json()

    assert reply["dismiss_after_s"] >= 4


def test_ask_opens_the_microphone(client):
    reply = client.post("/api/glasses/command", json={"action": "ask"}).json()

    assert reply["intent"] == "ask"
    assert reply["client_action"] == "listen_question"


@pytest.mark.parametrize(
    "asked", ["what's this", "take a photo of my keys", "keys", "I can't find my wallet"]
)
def test_a_question_always_asks_the_memory(client, asked):
    reply = client.post("/api/glasses/command", json={"question": asked}).json()

    assert reply["intent"] == "ask_memory"


def test_an_empty_question_goes_home(client):
    reply = client.post("/api/glasses/command", json={"question": "  "}).json()

    assert reply["intent"] == "home"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"action": "ask", "question": "where?"},
        {"action": "explode"},
        # Words come as a question and go to the memory; nothing interprets them as commands.
        {"text": "where are my keys"},
        {"action": "help"},
        {"action": "home"},
        # The glasses take no photos: no capture button, no gallery.
        {"action": "take_photo"},
        {"action": "gallery"},
    ],
)
def test_bad_commands_are_rejected(client, body):
    assert client.post("/api/glasses/command", json=body).status_code == 422


def test_question_without_models_says_so(client):
    reply = client.post("/api/glasses/command", json={"question": "where are my keys?"}).json()

    assert reply["intent"] == "ask_memory"
    assert "isn’t set up" in reply["speech"]


def test_question_goes_through_the_question_loop(client):
    use_models(chat=says("On the kitchen table, at 09:12."), embed=fake_embed)

    reply = client.post("/api/glasses/command", json={"question": "Where are my keys?"}).json()

    assert reply["intent"] == "ask_memory"
    assert reply["speech"] == "On the kitchen table, at 09:12."
    assert "Where are my keys?" in texts(reply["screen"])


def test_unreachable_memory_is_spoken_not_raised(client):
    use_models(chat=broken, embed=fake_embed)

    reply = client.post("/api/glasses/command", json={"question": "where is the remote?"})

    assert reply.status_code == 200
    assert "can’t reach" in reply.json()["speech"]


def test_answers_are_plain_text_without_images(client):
    use_models(chat=says("On the desk: * a **blue** mug * your keys"), embed=fake_embed)

    reply = client.post("/api/glasses/command", json={"question": "what's on the desk"}).json()

    assert reply["speech"] == "On the desk: a blue mug, your keys"
    assert "image" not in str(reply["screen"])


def test_double_tap_opens_the_microphone(client):
    reply = client.post("/api/glasses/command", json={"action": "double_tap"}).json()

    assert reply["client_action"] == "listen_question"


@pytest.mark.parametrize(
    ("said", "target"),
    [
        ("Remind me to not trip over a stool in the kitchen", "stool"),
        ("warn me about the stairs", "stairs"),
        ("remind me when you see my keys", "keys"),
        ("remind me to watch out for wet floor signs", "wet floor"),
    ],
)
def test_reminder_target_by_rule(said, target):
    assert reminders.target_from(said) == target


def test_saying_remind_me_adds_a_reminder(client):
    said = "Remind me to not trip over a stool in the kitchen"
    reply = client.post("/api/glasses/command", json={"question": said}).json()

    assert reply["intent"] == "remind"
    assert "stool" in reply["speech"]
    listed = client.get("/api/glasses/reminders").json()
    assert [(r["target"], r["text"]) for r in listed] == [("stool", said)]


def test_reminder_without_an_object_asks_again(client):
    reply = client.post("/api/glasses/command", json={"question": "remind me"}).json()

    assert reply["intent"] == "remind"
    assert client.get("/api/glasses/reminders").json() == []
    assert client.post("/api/glasses/reminders", json={"text": "remind me"}).status_code == 422


def test_reminder_can_be_triggered_and_deleted(client):
    made = client.post("/api/glasses/reminders", json={"text": "warn me about the stool"}).json()

    warning = client.post(f"/api/glasses/reminders/{made['id']}/trigger").json()
    assert warning["intent"] == "warning"
    assert "Stool ahead" in texts(warning["screen"])

    assert client.delete(f"/api/glasses/reminders/{made['id']}").status_code == 204
    assert client.get("/api/glasses/reminders").json() == []


def watch(client):
    return client.post("/api/glasses/watch", files={"photo": ("w.jpg", JPEG, "image/jpeg")}).json()


def test_watch_warns_once_a_minute_when_the_object_is_in_view(client):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})
    client.post("/api/glasses/reminders", json={"text": "warn me about the dog"})
    use_models(chat=says("1"))  # the vision model sees item 1, the stool

    first, second = watch(client), watch(client)

    assert first["seen"] == ["stool"]
    assert first["warning"]["speech"] == "Careful: stool ahead."
    assert second["seen"] == ["stool"]
    assert second["warning"] is None  # warned less than a minute ago


def test_watch_sees_nothing_when_the_object_is_absent(client):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})
    use_models(chat=says("none"))

    assert watch(client)["seen"] == []


def test_watch_only_cools_down_the_reminder_it_shows(client):
    for target in ("stool", "dog"):
        client.post("/api/glasses/reminders", json={"text": f"warn me about the {target}"})
    use_models(chat=says("1, 2"))

    assert watch(client)["warning"]["speech"] == "Careful: stool ahead."
    assert watch(client)["warning"]["speech"] == "Careful: dog ahead."
    assert watch(client)["warning"] is None


def test_watch_without_a_vision_model_says_so(client):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})

    result = watch(client)

    assert result["seen"] == [] and result["warning"] is None
    assert "no vision model" in result["note"]


def test_watch_reports_an_unexpected_failure_instead_of_500(client, monkeypatch):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})

    async def odd_model(_messages, _tools):
        raise KeyError("choices")

    use_models(chat=odd_model)

    result = client.post("/api/glasses/watch", files={"photo": ("w.jpg", JPEG, "image/jpeg")})

    assert result.status_code == 200
    assert "check failed: KeyError" in result.json()["note"]


# --- The WebSocket (/api/glasses/ws) ---------------------------------------------------------


def send_jpeg(ws, kind: str, id: int, data: bytes = JPEG):
    ws.send_json({"type": kind, "id": id})
    ws.send_bytes(data)


def test_socket_answers_ping_and_commands(client):
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "ping", "id": 1})
        assert ws.receive_json() == {"type": "pong", "id": 1}

        ws.send_json({"type": "command", "id": 2, "action": "double_tap"})
        message = ws.receive_json()
        assert message["type"] == "reply" and message["id"] == 2
        assert message["reply"]["client_action"] == "listen_question"

        ws.send_json(
            {"type": "command", "id": 3, "question": "remind me not to trip over the stool"}
        )
        assert ws.receive_json()["reply"]["intent"] == "remind"
    assert [r["target"] for r in client.get("/api/glasses/reminders").json()] == ["stool"]


@pytest.mark.parametrize(
    "message",
    [
        {"type": "command", "id": 4, "action": "self_destruct"},
        {"type": "command", "id": 4, "question": "hi", "action": "ask"},
        {"type": "nonsense", "id": 4},
    ],
)
def test_socket_reports_bad_messages_and_stays_open(client, message):
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json(message)
        error = ws.receive_json()
        assert error["type"] == "error" and error["id"] == 4 and error["status"] == 422
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_socket_rejects_a_watched_frame_that_is_not_a_jpeg(client):
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "watch", "on": True})
        send_jpeg(ws, "frame", 6, b"not a jpeg")
        assert ws.receive_json() == {
            "type": "error",
            "id": 6,
            "status": 415,
            "detail": "frame must be a JPEG",
        }
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_socket_checks_frames_only_while_watching(client):
    with client.websocket_connect("/api/glasses/ws") as ws:
        send_jpeg(ws, "frame", 1)
        assert ws.receive_json()["status"] == 409


def test_socket_pushes_a_warning_when_the_object_is_in_view(client):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})
    use_models(chat=says("1"))
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "watch", "on": True})
        send_jpeg(ws, "frame", 1)

        result = ws.receive_json()
        assert result["type"] == "watch_result" and result["id"] == 1
        assert result["result"]["seen"] == ["stool"] and result["dropped"] == 0
        warning = ws.receive_json()
        assert warning["type"] == "reply" and warning["id"] is None
        assert warning["reply"]["speech"] == "Careful: stool ahead."

        send_jpeg(ws, "frame", 2)
        again = ws.receive_json()
        assert again["result"]["warning"] is None  # warned less than a minute ago
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"  # and no second warning before it


def test_socket_checks_only_the_latest_frame(client):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})
    checking, release, checks = threading.Event(), threading.Event(), []

    async def slow_model(_messages, _tools):
        checks.append(1)
        checking.set()
        assert await anyio.to_thread.run_sync(release.wait, 5)  # the test thread sets it
        return {"role": "assistant", "content": "none"}

    use_models(chat=slow_model)
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "watch", "on": True})
        send_jpeg(ws, "frame", 1)
        assert checking.wait(5)
        for id in (2, 3, 4):  # arrive while frame 1 is being checked
            send_jpeg(ws, "frame", id)
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"  # the socket kept reading meanwhile
        release.set()

        first, latest = ws.receive_json(), ws.receive_json()
        assert (first["id"], first["dropped"]) == (1, 0)
        assert (latest["id"], latest["dropped"]) == (4, 2)  # 2 and 3 were never checked
    assert len(checks) == 2


@pytest.mark.parametrize(
    "first",
    [
        {"type": "ping"},
        {"type": "auth", "token": "wrong"},
        {"type": "command", "id": 1, "action": "double_tap"},
    ],
)
def test_socket_refuses_a_client_that_is_not_logged_in(anonymous, first):
    with pytest.raises(WebSocketDisconnect) as refused:
        with anonymous.websocket_connect("/api/glasses/ws") as ws:
            ws.send_json(first)
            ws.receive_json()
    assert refused.value.code == 1008


def test_socket_refuses_a_wrong_bearer_header(anonymous):
    with pytest.raises(WebSocketDisconnect) as refused:
        with anonymous.websocket_connect(
            "/api/glasses/ws", headers={"Authorization": "Bearer wrong"}
        ) as ws:
            ws.receive_json()
    assert refused.value.code == 1008


@pytest.mark.parametrize("how", ["cookie", "header", "message"])
def test_socket_accepts_a_login(anonymous, guest, how):
    bearer = guest("aino")
    token = bearer["Authorization"].removeprefix("Bearer ")
    if how == "cookie":
        anonymous.cookies.set("session", token)
    headers = bearer if how == "header" else {}
    with anonymous.websocket_connect("/api/glasses/ws", headers=headers) as ws:
        if how == "message":
            ws.send_json({"type": "auth", "token": token})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_socket_commands_are_scoped_to_the_login(client, guest):
    aino, ville = guest("aino"), guest("ville")
    with client.websocket_connect("/api/glasses/ws", headers=aino) as ws:
        ws.send_json({"type": "command", "id": 1, "question": "remind me about the stool"})
        assert ws.receive_json()["reply"]["intent"] == "remind"

    assert len(client.get("/api/glasses/reminders", headers=aino).json()) == 1
    assert client.get("/api/glasses/reminders", headers=ville).json() == []


def test_socket_survives_a_refused_client_leaving_first(anonymous, guest):
    bearer = guest("aino")
    with anonymous.websocket_connect("/api/glasses/ws") as ws:
        ws.close()  # before sending its auth message
    with anonymous.websocket_connect("/api/glasses/ws", headers=bearer) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


# --- Déjà vu: "where do I know this from?" ---------------------------------------------------


def seen_before(db, hours: list[int], owner: int = 0) -> None:
    """One earlier still per entry, seen that many hours ago; the first is the most like the
    photo `fake_embed` gives, the next a little less, and so on."""

    async def add(session):
        for place, ago in enumerate(hours, 1):
            await add_memory(
                session,
                "image",
                [1.0, 0.1 * place] + [0.0] * (WIDTH - 2),
                datetime.now(UTC) - timedelta(hours=ago),
                image_file=media.save_image(JPEG),
                collection="glasses",
                owner_id=owner,
            )
        await session.commit()

    db(add)


def look(client, **headers) -> None:
    """Send the photo that the question is about."""
    use_models(embed=fake_embed)
    files = {"frame": ("a.jpg", JPEG, "image/jpeg")}
    assert client.post("/api/glasses/frames", files=files, **headers).json()["stored"]


def images(node: dict) -> list[str]:
    if node["type"] == "image":
        return [node["src"]]
    return [src for child in node.get("children", []) for src in images(child)]


def test_recall_shows_the_three_closest_stills_and_when_it_was_last_seen(client, db):
    seen_before(db, [5, 2, 3, 1])
    look(client)
    use_models(chat=says('```json\n{"same": [1, 2], "say": "Your mug, on the desk."}\n```'))

    reply = client.post("/api/glasses/command", json={"question": "Where do I know this from?"})

    reply = reply.json()
    assert reply["intent"] == "recall"
    assert images(reply["screen"]) == [f"/api/memories/{id}/image" for id in (1, 2, 3)]
    shown = texts(reply["screen"])
    assert "Seen 2 hours ago" in shown  # the newer of the two stills found the same
    assert "Same, 5 hours ago" in shown and "Same, 2 hours ago" in shown
    assert "Same, 3 hours ago" not in shown
    assert reply["speech"] == "You last saw this 2 hours ago. Your mug, on the desk."


def test_recall_says_no_when_the_model_finds_none_the_same(client, db):
    seen_before(db, [5, 2])
    look(client)
    use_models(chat=says('{"same": [], "say": "I haven’t seen this before; that was a vase."}'))

    reply = client.post("/api/glasses/command", json={"question": "have I seen this before"}).json()

    assert len(images(reply["screen"])) == 2
    assert "Not seen before" in texts(reply["screen"])
    assert "Same" not in texts(reply["screen"])
    assert reply["speech"] == "I haven’t seen this before; that was a vase."


@pytest.mark.parametrize(
    "asked", ["What did I see this morning?", "remember this is where I parked", "see this sign"]
)
def test_recall_leaves_other_questions_to_the_memory(client, asked):
    reply = client.post("/api/glasses/command", json={"question": asked}).json()

    assert reply["intent"] == "ask_memory"


def test_recall_skips_the_look_the_wearer_is_still_having(client, db):
    """Stills of the same view, stored one after the other up to now, are not "before"."""

    async def add(session):
        for seconds in (50, 40):  # the stream's stills of this look, older than GAP_S
            await add_memory(
                session,
                "image",
                [1.0] + [0.0] * (WIDTH - 1),
                datetime.now(UTC) - timedelta(seconds=seconds),
                image_file=media.save_image(JPEG),
                collection="glasses",
                owner_id=1,  # the admin, who asks
            )
        await session.commit()

    db(add)
    look(client)
    use_models(chat=says('{"same": [1], "say": "x"}'))

    reply = client.post("/api/glasses/command", json={"question": "have I seen this before"})

    assert images(reply.json()["screen"]) == []
    assert "Not seen before" in texts(reply.json()["screen"])


def test_recall_needs_a_still_from_just_now(client, db):
    seen_before(db, [5])
    use_models(chat=says('{"same": [1], "say": "x"}'))

    reply = client.post("/api/glasses/command", json={"question": "where do I know this one"})

    assert reply.json()["intent"] == "recall"
    assert "I need to see it" in texts(reply.json()["screen"])


def test_recall_does_not_take_words_for_an_answer(client, db):
    seen_before(db, [5])
    look(client)
    use_models(chat=says("Yes, that looks like the same mug."))

    reply = client.post("/api/glasses/command", json={"question": "do I know this from somewhere"})

    assert "I can’t reach the memory right now." in texts(reply.json()["screen"])


def test_recall_only_looks_in_the_askers_memories(client, db, guest):
    bearer = guest("aino")
    seen_before(db, [5], owner=1)  # the admin's own, not a demo memory
    look(client, headers=bearer)
    use_models(chat=says('{"same": [1], "say": "x"}'))

    reply = client.post(
        "/api/glasses/command", json={"question": "where do I know this from"}, headers=bearer
    ).json()

    assert images(reply["screen"]) == []
    assert "Not seen before" in texts(reply["screen"])
