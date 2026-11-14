import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlmodel import Session, select

from app import media, vectors
from app.db import get_engine
from app.glasses import photos
from app.llm import get_chat, get_embedder
from app.main import app
from app.models import Memory, Reminder, User
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


def test_a_guest_session_needs_no_name_or_password(anonymous):
    reply = anonymous.post("/api/auth/guest")

    assert reply.status_code == 201
    body = reply.json()
    assert body["username"].startswith("guest-") and body["role"] == "guest"
    left = datetime.fromisoformat(body["expires_at"]) - datetime.now(UTC)
    assert timedelta(minutes=29) < left <= timedelta(minutes=30)
    assert anonymous.get("/api/auth/me").json()["username"] == body["username"]  # the cookie
    assert "Max-Age=1" in reply.headers["set-cookie"]  # ~1800 s, as long as the account


def test_every_guest_is_a_new_account(anonymous):
    first = anonymous.post("/api/auth/guest").json()
    second = anonymous.post("/api/auth/guest").json()

    assert first["username"] != second["username"]


def test_there_is_no_register_and_guests_cannot_log_in_again(anonymous):
    name = anonymous.post("/api/auth/guest").json()["username"]
    body = {"username": "someone", "password": "long enough"}

    assert anonymous.post("/api/auth/register", json=body).status_code in (404, 405)
    for password in ["", "!", "anything"]:
        attempt = {"username": name, "password": password}
        assert anonymous.post("/api/auth/login", json=attempt).status_code in (401, 422)


def test_logout(anonymous):
    anonymous.post("/api/auth/guest")
    anonymous.post("/api/auth/logout")

    assert anonymous.get("/api/auth/me").status_code == 401
    assert anonymous.get("/api/auth/session").json() is None


def expire_guests():
    with Session(get_engine()) as session:
        for user in session.exec(select(User).where(User.role == "guest")):
            user.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.add(user)
        session.commit()


def test_an_expired_guest_is_logged_out_and_deleted_with_its_memories(client, guest):
    aino = guest("aino")
    image = add_image(client, "aino's photo", aino).json()
    add_text(client, "aino: keys under the mat", aino)
    files = {"photo": ("a.jpg", jpeg(), "image/jpeg")}
    assert client.post("/api/glasses/photos", files=files, headers=aino).status_code == 201
    client.post("/api/glasses/reminders", json={"text": "warn me about the stool"}, headers=aino)
    with Session(get_engine()) as session:
        stored = session.get(Memory, image["id"])
        assert stored is not None and stored.image_file
        image_file, owner = media.path(stored.image_file), stored.owner_id

    expire_guests()

    assert client.get("/api/auth/me", headers=aino).status_code == 401
    assert texts(client) == set()  # the admin sees nothing left
    assert not image_file.exists()
    assert not (photos.photos_dir() / str(owner)).exists()
    with Session(get_engine()) as session:
        assert session.exec(select(User).where(User.role == "guest")).first() is None
        assert session.exec(select(Reminder)).first() is None
        assert vectors.stored(session.connection(), TABLE) == {}


def test_starting_a_guest_sweeps_the_expired_ones(anonymous, guest):
    guest("old")
    expire_guests()

    anonymous.post("/api/auth/guest")

    with Session(get_engine()) as session:
        assert len(session.exec(select(User).where(User.role == "guest")).all()) == 1


def test_the_admin_comes_from_the_settings(client):
    me = client.get("/api/auth/me").json()
    assert (me["username"], me["role"], me["expires_at"]) == ("admin", "admin", None)


def test_bearer_token_works_without_cookies(anonymous, guest):
    headers = guest("aino")

    assert anonymous.get("/api/auth/me", headers=headers).json()["username"].startswith("guest-")


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
    demo, owner = owners["aino: keys under the mat"]
    assert not demo and owner.startswith("guest-")


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
