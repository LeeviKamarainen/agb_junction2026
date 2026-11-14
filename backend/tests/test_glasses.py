import io

import pytest
from PIL import Image

from app import vectors
from app.glasses import reminders, remote_memory
from app.glasses.intents import interpret
from app.llm import UpstreamError
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder
from app.search import TABLE

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions


def make_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), "orange").save(buffer, "JPEG")
    return buffer.getvalue()


JPEG = make_jpeg()


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    """No models or memory API unless a test sets them, even when backend/.env has them."""
    monkeypatch.setattr(remote_memory, "configured", lambda: False)
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


def fake_embed(texts):
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in texts]


def says(text):
    """A chat model that always answers `text`, without calling tools."""
    return lambda _messages, _tools: {"role": "assistant", "content": text}


def broken(*_args):
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


@pytest.mark.parametrize(
    ("said", "intent"),
    [
        ("Take a photo", "take_photo"),
        ("What am I looking at?", "take_photo"),
        ("describe this", "take_photo"),
        ("snap a picture please", "take_photo"),
        ("Show my photos", "gallery"),
        ("open the gallery", "gallery"),
        ("Where did I leave my keys?", "ask_memory"),
        ("what was on the table", "ask_memory"),
        ("go back", "home"),
        ("help", "help"),
        ("Remind me not to trip over the stool", "remind"),
        ("remind me to take a photo of the bill", "remind"),
        ("banana", "unknown"),
        ("   ", "unknown"),
    ],
)
def test_interpret(said, intent):
    assert interpret(said).name == intent


def test_home_is_an_empty_display(client):
    reply = client.get("/api/glasses/home").json()

    assert reply["screen"] == {**reply["screen"], "type": "flexbox", "children": []}


def test_no_screen_has_buttons(client):
    """The wearer acts through the glasses and the Neural Band, never on the display."""
    client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})
    for body in [
        {"action": "help"},
        {"action": "gallery"},
        {"action": "ask"},
        {"question": "where are my keys?"},
        {"question": "remind me about the stool"},
        {"text": "banana"},
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


@pytest.mark.parametrize("asked", ["what's this", "take a photo of my keys", "keys"])
def test_a_question_always_asks_the_memory(client, asked):
    reply = client.post("/api/glasses/command", json={"question": asked}).json()

    assert reply["intent"] == "ask_memory"


def test_an_empty_question_goes_home(client):
    reply = client.post("/api/glasses/command", json={"question": "  "}).json()

    assert reply["intent"] == "home"


def test_saying_take_a_photo_asks_the_client_to_capture(client):
    reply = client.post("/api/glasses/command", json={"text": "take a picture"}).json()

    assert reply["intent"] == "take_photo"
    assert reply["client_action"] == "capture_photo"


def test_a_button_does_what_its_words_do(client):
    by_button = client.post("/api/glasses/command", json={"action": "help"}).json()
    by_voice = client.post("/api/glasses/command", json={"text": "help"}).json()

    assert by_button == by_voice


@pytest.mark.parametrize(
    "body",
    [{}, {"text": "help", "action": "help"}, {"text": "a", "question": "b"}, {"action": "explode"}],
)
def test_bad_commands_are_rejected(client, body):
    assert client.post("/api/glasses/command", json=body).status_code == 422


def test_uploaded_photo_is_shown_and_listed(client):
    upload = client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})

    assert upload.status_code == 201
    assert "Photo saved" in texts(upload.json()["screen"])

    gallery = client.post("/api/glasses/command", json={"text": "show my photos"}).json()
    assert "Latest 1 photos" in texts(gallery["screen"])
    image = gallery["screen"]["children"][1]["children"][0]
    fetched = client.get(image["src"])
    assert fetched.status_code == 200
    assert fetched.content == JPEG


def test_photo_is_described_and_becomes_a_memory(client):
    use_models(chat=says("A desk with a laptop and keys."), embed=fake_embed)

    reply = client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})

    assert reply.status_code == 201
    assert reply.json()["intent"] == "photo_described"
    assert reply.json()["speech"] == "A desk with a laptop and keys."
    assert "Saved to memory" in texts(reply.json()["screen"])
    memories = client.get("/api/memories").json()
    assert [(m["kind"], m["text"], m["collection"]) for m in memories] == [
        ("image", "A desk with a laptop and keys.", "glasses")
    ]


def test_photo_is_kept_when_the_models_are_down(client):
    use_models(chat=broken, embed=broken)

    reply = client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})

    assert reply.status_code == 201
    assert reply.json()["intent"] == "photo_saved"
    assert "Couldn’t describe it; couldn’t save it to memory" in texts(reply.json()["screen"])
    assert client.get("/api/memories").json() == []


def test_non_jpeg_photo_is_rejected(client):
    upload = client.post("/api/glasses/photos", files={"photo": ("a.png", b"\x89PNG", "image/png")})

    assert upload.status_code == 415


@pytest.mark.parametrize("name", ["../app.db", "nope.jpg", "20260101T000000000000.jpg"])
def test_unknown_photo_is_404(client, name):
    assert client.get(f"/api/glasses/photos/{name}").status_code == 404


def test_question_without_models_says_so(client):
    reply = client.post("/api/glasses/command", json={"text": "where are my keys?"}).json()

    assert reply["intent"] == "ask_memory"
    assert "isn’t set up" in reply["speech"]


def test_question_goes_through_the_question_loop(client):
    use_models(chat=says("On the kitchen table, at 09:12."), embed=fake_embed)

    reply = client.post("/api/glasses/command", json={"text": "Where are my keys?"}).json()

    assert reply["intent"] == "ask_memory"
    assert reply["speech"] == "On the kitchen table, at 09:12."
    assert "Where are my keys?" in texts(reply["screen"])


def test_unreachable_memory_is_spoken_not_raised(client):
    use_models(chat=broken, embed=fake_embed)

    reply = client.post("/api/glasses/command", json={"text": "where is the remote?"})

    assert reply.status_code == 200
    assert "can’t reach" in reply.json()["speech"]


def test_photo_falls_back_to_the_memory_api(client, monkeypatch):
    use_models(chat=broken)
    monkeypatch.setattr(remote_memory, "configured", lambda: True)
    monkeypatch.setattr(remote_memory, "describe", lambda jpeg, name: "A watering can.")

    reply = client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})

    assert reply.json()["intent"] == "photo_described"
    assert reply.json()["speech"] == "A watering can."
    assert "couldn’t" not in texts(reply.json()["screen"])


def test_question_falls_back_to_the_memory_api(client, monkeypatch):
    use_models(chat=broken, embed=fake_embed)
    monkeypatch.setattr(remote_memory, "configured", lambda: True)
    url = "/api/glasses/memory/20261114T120000000000/keyframes/a.jpg"
    monkeypatch.setattr(remote_memory, "ask", lambda question: ("By the door.", [url]))

    reply = client.post("/api/glasses/command", json={"text": "where is the can?"}).json()

    assert reply["speech"] == "By the door."
    assert url not in str(reply["screen"])  # answers are text only


def test_answers_are_plain_text_without_images(client):
    use_models(chat=says("On the desk: * a **blue** mug * your keys"), embed=fake_embed)
    client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})

    reply = client.post("/api/glasses/command", json={"question": "what's on the desk"}).json()

    assert reply["speech"] == "On the desk: a blue mug, your keys"
    assert "image" not in str(reply["screen"])


def test_memory_api_batch_holds_every_photo(client, monkeypatch):
    posted = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, path, **kwargs):
            class Reply:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/batches":
                        posted.append(len(kwargs["files"]))
                        return {"batch": f"20261114T12000000000{len(posted)}", "summary": "x"}
                    return {"answer": "On the table.", "keyframes": ["0.jpg"]}

            return Reply()

    monkeypatch.setattr(remote_memory, "configured", lambda: True)
    monkeypatch.setattr(remote_memory, "_client", FakeClient)
    for _ in range(2):
        client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})
    posted.clear()  # the two one-photo batches that described them

    assert remote_memory.ask("where?")[0] == "On the table."
    assert remote_memory.ask("again?")[0] == "On the table."
    assert posted == [2]  # one batch with both photos, reused for the second question


@pytest.mark.parametrize("path", ["../x/keyframes/a.jpg", "20261114T120000000000/keyframes/a.png"])
def test_bad_keyframe_paths_are_404(client, path):
    assert client.get(f"/api/glasses/memory/{path}").status_code == 404


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
    reply = client.post("/api/glasses/command", json={"text": "remind me"}).json()

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


def test_watch_falls_back_to_the_memory_apis_description(client, monkeypatch):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})
    monkeypatch.setattr(remote_memory, "configured", lambda: True)
    monkeypatch.setattr(remote_memory, "describe", lambda jpeg, name: "Two stools by a counter.")

    result = watch(client)

    assert result["seen"] == ["stool"]
    assert result["warning"] is not None


def test_watch_without_a_vision_model_says_so(client):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})

    result = watch(client)

    assert result["seen"] == [] and result["warning"] is None
    assert "no vision model" in result["note"]


def test_watch_reports_an_unexpected_failure_instead_of_500(client, monkeypatch):
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"})

    def odd_model(_messages, _tools):
        raise KeyError("choices")

    use_models(chat=odd_model)

    result = client.post("/api/glasses/watch", files={"photo": ("w.jpg", JPEG, "image/jpeg")})

    assert result.status_code == 200
    assert "check failed: KeyError" in result.json()["note"]
