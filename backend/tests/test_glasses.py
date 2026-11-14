import io

import pytest
from PIL import Image

from app import vectors
from app.glasses import remote_memory
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
        ("banana", "unknown"),
        ("   ", "unknown"),
    ],
)
def test_interpret(said, intent):
    assert interpret(said).name == intent


def test_home_offers_the_main_actions(client):
    reply = client.get("/api/glasses/home").json()

    assert reply["screen"]["type"] == "flexbox"
    assert buttons(reply["screen"]) == ["take_photo", "gallery", "help"]


def test_saying_take_a_photo_asks_the_client_to_capture(client):
    reply = client.post("/api/glasses/command", json={"text": "take a picture"}).json()

    assert reply["intent"] == "take_photo"
    assert reply["client_action"] == "capture_photo"


def test_a_button_does_what_its_words_do(client):
    by_button = client.post("/api/glasses/command", json={"action": "help"}).json()
    by_voice = client.post("/api/glasses/command", json={"text": "help"}).json()

    assert by_button == by_voice


@pytest.mark.parametrize("body", [{}, {"text": "help", "action": "help"}, {"action": "explode"}])
def test_bad_commands_are_rejected(client, body):
    assert client.post("/api/glasses/command", json=body).status_code == 422


def test_uploaded_photo_is_shown_and_listed(client):
    upload = client.post("/api/glasses/photos", files={"photo": ("a.jpg", JPEG, "image/jpeg")})

    assert upload.status_code == 201
    image = upload.json()["screen"]["children"][1]
    assert image["type"] == "image"
    fetched = client.get(image["src"])
    assert fetched.status_code == 200
    assert fetched.content == JPEG

    gallery = client.post("/api/glasses/command", json={"text": "show my photos"}).json()
    assert "Latest 1 photos" in texts(gallery["screen"])


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
    assert "couldn’t describe it; couldn’t save it to memory" in texts(reply.json()["screen"])
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
    assert url in str(reply["screen"])


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
