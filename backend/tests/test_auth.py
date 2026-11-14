import io
import json

import pytest
from PIL import Image

from app import vectors
from app.llm import get_chat, get_embedder
from app.main import app
from app.routes.glasses import optional_chat, optional_embedder
from app.search import TABLE

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions


def fake_embed(texts):
    return [[1.0] + [0.0] * (WIDTH - 1) for _ in texts]


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def embedder():
    app.dependency_overrides[get_embedder] = lambda: fake_embed
    app.dependency_overrides[optional_chat] = lambda: None
    app.dependency_overrides[optional_embedder] = lambda: None
    yield
    app.dependency_overrides.clear()


def add_text(client, text, headers=None, **extra):
    reply = client.post("/api/memories/text", json={"text": text, **extra}, headers=headers or {})
    return reply


def add_image(client, text, headers=None, demo=False):
    return client.post(
        "/api/memories/image",
        files={"file": ("a.jpg", jpeg(), "image/jpeg")},
        data={"text": text, "demo": str(demo).lower()},
        headers=headers or {},
    )


def texts(client, headers=None):
    return {m["text"] for m in client.get("/api/memories", headers=headers or {}).json()}


# --- Accounts -------------------------------------------------------------------------------


def test_register_logs_in_as_a_guest(anonymous):
    reply = anonymous.post(
        "/api/auth/register", json={"username": " Aino ", "password": "long enough"}
    )

    assert reply.status_code == 201
    assert reply.json()["username"] == "aino"
    assert reply.json()["role"] == "guest"
    me = anonymous.get("/api/auth/me").json()  # the cookie it set
    assert me["username"] == "aino"


def test_taken_username_and_bad_input_are_refused(anonymous):
    body = {"username": "aino", "password": "long enough"}
    assert anonymous.post("/api/auth/register", json=body).status_code == 201
    assert anonymous.post("/api/auth/register", json=body).status_code == 409
    short = {"username": "aino2", "password": "short"}
    assert anonymous.post("/api/auth/register", json=short).status_code == 422
    odd = {"username": "a b!", "password": "long enough"}
    assert anonymous.post("/api/auth/register", json=odd).status_code == 422


def test_login_and_logout(anonymous):
    body = {"username": "aino", "password": "long enough"}
    anonymous.post("/api/auth/register", json=body)
    anonymous.post("/api/auth/logout")
    assert anonymous.get("/api/auth/me").status_code == 401
    assert anonymous.get("/api/auth/session").json() is None

    wrong = {"username": "aino", "password": "not the password"}
    assert anonymous.post("/api/auth/login", json=wrong).status_code == 401
    assert anonymous.post("/api/auth/login", json=body).status_code == 200
    assert anonymous.get("/api/auth/session").json()["username"] == "aino"


def test_the_admin_comes_from_the_settings(client):
    assert client.get("/api/auth/me").json() == {"id": 1, "username": "admin", "role": "admin"}


def test_bearer_token_works_without_cookies(anonymous, guest):
    headers = guest("aino")

    assert anonymous.get("/api/auth/me", headers=headers).json()["username"] == "aino"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/memories"),
        ("get", "/api/memories/collections"),
        ("get", "/api/memories/vectors"),
        ("get", "/api/memories/1/image"),
        ("delete", "/api/memories/1"),
        ("post", "/api/search"),
        ("post", "/api/ask"),
        ("post", "/api/ask/stream"),
        ("post", "/api/glasses/command"),
        ("get", "/api/glasses/reminders"),
    ],
)
def test_memory_routes_need_a_login(anonymous, method, path):
    assert getattr(anonymous, method)(path).status_code == 401


# --- Who sees what --------------------------------------------------------------------------


@pytest.fixture
def two_guests(client, guest):
    """A demo note from the admin, and a note each from guests aino and ville."""
    aino, ville = guest("aino"), guest("ville")
    assert add_text(client, "demo: keys on the hook", demo=True).status_code == 201
    assert add_text(client, "admin: private note").status_code == 201
    assert add_text(client, "aino: keys under the mat", aino).status_code == 201
    assert add_text(client, "ville: keys in the car", ville).status_code == 201
    return aino, ville


def test_guests_see_the_demo_memories_and_their_own(client, two_guests):
    aino, ville = two_guests

    assert texts(client, aino) == {"demo: keys on the hook", "aino: keys under the mat"}
    assert texts(client, ville) == {"demo: keys on the hook", "ville: keys in the car"}
    assert len(texts(client)) == 4  # the admin sees everything


def test_the_admin_sees_who_added_what(client, two_guests):
    owners = {m["text"]: (m["demo"], m["owner"]) for m in client.get("/api/memories").json()}

    assert owners["demo: keys on the hook"] == (True, None)
    assert owners["aino: keys under the mat"] == (False, "aino")


def test_search_only_reaches_your_memories(client, two_guests):
    aino, _ = two_guests
    body = {"user_query": "keys", "keywords": ["keys", "car", "private"]}

    found = {r["text"] for r in client.post("/api/search", json=body, headers=aino).json()}

    assert found == {"demo: keys on the hook", "aino: keys under the mat"}


def test_collections_count_only_what_you_see(client, two_guests):
    aino, _ = two_guests

    counts = client.get("/api/memories/collections", headers=aino).json()

    assert counts == [{"name": "manual", "count": 2}]


def test_another_guests_image_is_not_found(client, guest):
    aino, ville = guest("aino"), guest("ville")
    image = add_image(client, "aino's photo", aino).json()

    assert client.get(image["image_url"], headers=aino).status_code == 200
    assert client.get(image["image_url"], headers=ville).status_code == 404
    assert client.get(image["image_url"]).status_code == 200  # the admin
    assert client.delete(f"/api/memories/{image['id']}", headers=ville).status_code == 404


def test_only_the_admin_adds_and_deletes_demo_memories(client, guest):
    aino = guest("aino")
    assert add_image(client, "x", aino, demo=True).status_code == 403
    assert add_text(client, "x", aino, demo=True).status_code == 403

    demo = add_image(client, "demo photo", demo=True).json()
    assert demo["demo"] is True
    assert client.get(demo["image_url"], headers=aino).status_code == 200
    assert client.delete(f"/api/memories/{demo['id']}", headers=aino).status_code == 403
    assert client.delete(f"/api/memories/{demo['id']}").status_code == 204


# --- What Gemma is given --------------------------------------------------------------------


def searching_model(seen: list):
    """A chat model that searches for keys, then answers; `seen` collects every message it gets."""

    def chat(messages, tools):
        seen.extend(messages)
        if tools:
            arguments = {"user_query": "keys", "keywords": ["keys", "car", "private", "photo"]}
            call = {"id": "1", "function": {"name": "vector_search", "arguments": arguments}}
            return {"role": "assistant", "content": "", "tool_calls": [call]}
        return {"role": "assistant", "content": "Done."}

    return chat


def everything_said(messages) -> str:
    return json.dumps(messages)


def test_gemma_never_gets_another_guests_notes_or_photos(client, two_guests):
    aino, ville = two_guests
    add_image(client, "ville's photo of the car", ville)
    seen: list = []
    app.dependency_overrides[get_chat] = lambda: searching_model(seen)

    answer = client.post("/api/ask", json={"question": "where are my keys?"}, headers=aino)

    assert answer.status_code == 200
    said = everything_said(seen)
    assert "aino: keys under the mat" in said and "demo: keys on the hook" in said
    assert "ville" not in said and "admin: private note" not in said
    assert "image_url" not in said  # ville's photo was never attached
    sources = {s["text"] for s in answer.json()["sources"]}
    assert sources == {"demo: keys on the hook", "aino: keys under the mat"}


def test_the_streamed_answer_is_scoped_too(client, two_guests):
    aino, _ = two_guests
    seen: list = []
    app.dependency_overrides[get_chat] = lambda: searching_model(seen)

    reply = client.post("/api/ask/stream", json={"question": "keys?"}, headers=aino)

    assert reply.status_code == 200
    assert "ville" not in everything_said(seen)
    assert "ville" not in reply.text


# --- The glasses ----------------------------------------------------------------------------


def test_glasses_photos_and_reminders_are_per_user(client, guest):
    aino, ville = guest("aino"), guest("ville")
    files = {"photo": ("a.jpg", jpeg(), "image/jpeg")}
    assert client.post("/api/glasses/photos", files=files, headers=aino).status_code == 201

    aino_gallery = client.post("/api/glasses/command", json={"action": "gallery"}, headers=aino)
    ville_gallery = client.post("/api/glasses/command", json={"action": "gallery"}, headers=ville)
    assert "Latest 1 photos" in json.dumps(aino_gallery.json())
    assert "No photos yet" in json.dumps(ville_gallery.json())
    src = aino_gallery.json()["screen"]["children"][1]["children"][0]["src"]
    assert client.get(src, headers=aino).status_code == 200
    assert client.get(src, headers=ville).status_code == 404

    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"}, headers=aino)
    assert client.get("/api/glasses/reminders", headers=ville).json() == []
    assert len(client.get("/api/glasses/reminders", headers=aino).json()) == 1
