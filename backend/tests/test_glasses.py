import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import WebSocketDisconnect
from PIL import Image

from app import media, vectors
from app.glasses import triggers
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


GOAL_ANSWER = '{"triggers": [{"name": "Stool", "watch_for": "a wooden stool on the floor"}]}'


@pytest.mark.parametrize(
    "said",
    [
        "Remind me to not trip over a stool in the kitchen",
        "warn me about the stairs",
        "please tell me when my bus comes",
        "Can you alert me if I leave my keys",
    ],
)
def test_warn_and_remind_me_set_up_a_goal(said):
    assert triggers.is_goal(said)


@pytest.mark.parametrize("said", ["where are my keys?", "what did I tell Aino?", "warnings?"])
def test_other_questions_are_not_goals(said):
    assert not triggers.is_goal(said)


def test_saying_warn_me_makes_a_goal_with_triggers(client):
    said = "Warn me so I don't trip over the stool in the kitchen"
    use_models(chat=says(GOAL_ANSWER))

    reply = client.post("/api/glasses/command", json={"question": said}).json()

    assert reply["intent"] == "goal" and reply["dismiss_after_s"]
    assert "Watching for it" in texts(reply["screen"]) and "Stool" in texts(reply["screen"])
    (goal,) = client.get("/api/glasses/goals").json()
    assert goal["text"] == said and goal["enabled"]
    assert [(t["name"], t["watch_for"]) for t in goal["triggers"]] == [
        ("Stool", "a wooden stool on the floor")
    ]


def test_a_goal_the_model_cannot_write_triggers_for_saves_nothing(client):
    use_models(chat=says("Sorry, I can't help with that."))

    reply = client.post("/api/glasses/command", json={"question": "remind me"}).json()

    assert reply["intent"] == "goal"
    assert "another way" in texts(reply["screen"])
    assert client.get("/api/glasses/goals").json() == []


def test_a_goal_without_a_chat_model_says_so(client):
    reply = client.post("/api/glasses/command", json={"question": "warn me about the stairs"})

    assert "isn’t set up" in texts(reply.json()["screen"])
    assert client.get("/api/glasses/goals").json() == []


def test_the_reminder_routes_are_gone(client):
    assert client.get("/api/glasses/reminders").status_code == 404
    files = {"photo": ("w.jpg", JPEG, "image/jpeg")}
    assert client.post("/api/glasses/watch", files=files).status_code in (404, 405)


# --- The WebSocket (/api/glasses/ws) ---------------------------------------------------------


def test_socket_answers_ping_and_commands(client):
    use_models(chat=says(GOAL_ANSWER))  # the socket takes its models when it connects
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
        assert ws.receive_json()["reply"]["intent"] == "goal"
    assert [g["text"] for g in client.get("/api/glasses/goals").json()] == [
        "remind me not to trip over the stool"
    ]


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


def test_socket_no_longer_takes_watch_messages(client):
    with client.websocket_connect("/api/glasses/ws") as ws:
        ws.send_json({"type": "watch", "id": 5, "on": True})
        error = ws.receive_json()
        assert error["type"] == "error" and error["id"] == 5 and error["status"] == 422


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
    use_models(chat=says(GOAL_ANSWER))
    with client.websocket_connect("/api/glasses/ws", headers=aino) as ws:
        ws.send_json({"type": "command", "id": 1, "question": "remind me about the stool"})
        assert ws.receive_json()["reply"]["intent"] == "goal"

    assert len(client.get("/api/glasses/goals", headers=aino).json()) == 1
    assert client.get("/api/glasses/goals", headers=ville).json() == []


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


def demo_clip(db, title: str, frames: list[tuple[float, float]]) -> tuple[int, list[int]]:
    """A published demo clip stored as demo memories, as app/demo.py stores one: a frame per
    (seconds into the clip, how far its vector turns from the current view). Its id and the
    memories' ids."""
    from app.models import Showcase

    async def add(session):
        clip = Showcase(title=title, published=True)
        session.add(clip)
        await session.flush()
        end = datetime.now(UTC) - timedelta(hours=1)
        ids = []
        for at, turn in frames:
            memory = await add_memory(
                session,
                "image",
                [1.0, turn] + [0.0] * (WIDTH - 2),
                end - timedelta(seconds=frames[-1][0] - at),
                text=title,
                image_file=media.save_image(JPEG),
                collection="demo",
                owner_id=0,
            )
            memory.source = f"clip:{clip.id}:abc"
            session.add(memory)
            ids.append(memory.id)
        await session.commit()
        return clip.id, ids

    return db(add)


def ask_demo(client, clip_id: int, at_s: float, **headers) -> dict:
    body = {"question": "have I seen this one before", "demo_clip_id": clip_id, "demo_at_s": at_s}
    reply = client.post("/api/glasses/command", json=body, **headers)
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_recall_on_a_demo_clip_looks_in_its_earlier_frames_and_the_other_clips(client, db, guest):
    # At 0 s the thing in view now; at 100 s the current frame, with the look from 80 s on; a
    # frame still to come at 150 s. They all look alike, and so does a frame of another clip.
    clip, (early, _start, look_, now_, later) = demo_clip(
        db, "Walk", [(0, 0.0), (10, 0.1), (80, 0.0), (100, 0.0), (150, 0.0)]
    )
    _other, (kitchen,) = demo_clip(db, "Kitchen", [(0, 0.05)])
    use_models(chat=says('{"same": [1], "say": "The red mug, on the bench."}'))

    reply = ask_demo(client, clip, 110, headers=guest("aino"))  # a guest sees the demo

    shown = images(reply["screen"])
    assert f"/api/memories/{early}/image" in shown
    assert f"/api/memories/{kitchen}/image" in shown
    for skipped in (look_, now_, later):
        assert f"/api/memories/{skipped}/image" not in shown
    said = texts(reply["screen"])
    assert "Seen 1 minute ago" in said  # 100 s into the clip, against the frame at 0 s
    assert "in the demo “Kitchen”" in said


def test_recall_on_a_demo_clip_not_stored_yet_says_so(client, db):
    use_models(chat=says('{"same": [], "say": "x"}'))

    reply = ask_demo(client, 99, 5)

    assert reply["intent"] == "recall"
    assert "Not in memory yet" in texts(reply["screen"])
