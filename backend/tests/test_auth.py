import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlmodel import select

from app import media, vectors
from app.llm import get_chat, get_embedder
from app.main import app
from app.models import Goal, Memory, Trigger, User
from app.routes.glasses import optional_chat, optional_embedder
from app.search import TABLE

WIDTH = vectors.VECTOR_TABLES[TABLE].dimensions


async def fake_embed(texts, input_type="query"):
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
    assert timedelta(minutes=59) < left <= timedelta(minutes=60)
    assert anonymous.get("/api/auth/me").json()["username"] == body["username"]  # the cookie
    assert "Max-Age=3" in reply.headers["set-cookie"]  # ~3600 s, as long as the account


def test_every_guest_is_a_new_account(anonymous):
    first = anonymous.post("/api/auth/guest").json()
    second = anonymous.post("/api/auth/guest").json()

    assert first["username"] != second["username"]


def test_there_is_no_register_and_guests_cannot_use_the_admin_login(anonymous):
    name = anonymous.post("/api/auth/guest").json()["username"]
    body = {"username": "someone", "password": "long enough"}

    assert anonymous.post("/api/auth/register", json=body).status_code in (404, 405)
    for password in ["", "!", "anything"]:
        attempt = {"username": name, "password": password}
        assert anonymous.post("/api/auth/login", json=attempt).status_code in (401, 422)


def test_a_guest_can_return_by_username_with_the_same_memories_and_expiry(anonymous, db):
    original = anonymous.post("/api/auth/guest").json()
    note = add_text(anonymous, "My keys are on the desk").json()
    # Time already spent in the account is not added back when it is reopened.
    expires = datetime.now(UTC) + timedelta(minutes=20)

    async def work(session):
        user = await session.get(User, original["id"])
        user.expires_at = expires
        session.add(user)
        await session.commit()

    db(work)

    anonymous.post("/api/auth/logout")

    response = anonymous.post(
        "/api/auth/guest/login", json={"username": f"  {original['username'].upper()}  "}
    )

    assert response.status_code == 200, response.text
    returned = response.json()
    assert returned["id"] == original["id"] and returned["role"] == "guest"
    assert returned["token"] != original["token"]
    assert datetime.fromisoformat(returned["expires_at"]).replace(tzinfo=UTC) == expires
    assert "Max-Age=1" in response.headers["set-cookie"]  # only the remaining ~1200 s
    assert anonymous.get("/api/auth/me").json()["id"] == original["id"]
    assert [m["id"] for m in anonymous.get("/api/memories").json()] == [note["id"]]
    anonymous.cookies.clear()
    assert (
        anonymous.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {returned['token']}"}
        ).json()["id"]
        == original["id"]
    )


def test_guest_username_login_rejects_unknown_expired_and_admin_accounts(anonymous, db):
    original = anonymous.post("/api/auth/guest").json()
    expire_guests(db)
    anonymous.cookies.clear()
    for username in (original["username"], "guest-unknown", "admin", "   "):
        response = anonymous.post("/api/auth/guest/login", json={"username": username})
        assert response.status_code == 401
        assert response.json() == {"detail": "Guest account not found or expired"}
        assert "set-cookie" not in response.headers
    assert anonymous.get("/api/auth/me").status_code == 401

    async def work(session):
        assert (await session.exec(select(User).where(User.role == "guest"))).first() is None

    db(work)


def test_guest_username_login_validates_its_input(anonymous):
    for body in ({}, {"username": ""}, {"username": "x" * 65}):
        assert anonymous.post("/api/auth/guest/login", json=body).status_code == 422


def test_logout(anonymous):
    anonymous.post("/api/auth/guest")
    anonymous.post("/api/auth/logout")

    assert anonymous.get("/api/auth/me").status_code == 401
    assert anonymous.get("/api/auth/session").json() is None


def expire_guests(db):
    async def expire(session):
        for user in await session.exec(select(User).where(User.role == "guest")):
            user.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.add(user)
        await session.commit()

    db(expire)


def test_an_expired_guest_is_logged_out_and_deleted_with_its_memories(client, guest, db):
    aino = guest("aino")
    image = add_image(client, "aino's photo", aino).json()
    add_text(client, "aino: keys under the mat", aino)
    goal = {
        "text": "warn me about the stool",
        "triggers": [{"name": "Stool", "watch_for": "stool"}],
    }
    assert client.post("/api/glasses/goals", json=goal, headers=aino).status_code == 201
    stored = db(lambda session: session.get(Memory, image["id"]))
    assert stored is not None and stored.image_file
    image_file = media.path(stored.image_file)

    expire_guests(db)

    assert client.get("/api/auth/me", headers=aino).status_code == 401
    assert texts(client) == set()  # the admin sees nothing left
    assert not image_file.exists()

    async def left(session):
        guests = (await session.exec(select(User).where(User.role == "guest"))).first()
        goals = (await session.exec(select(Goal))).first()
        triggers = (await session.exec(select(Trigger))).first()
        stored = await vectors.stored(await session.connection(), TABLE)
        return guests, goals, triggers, stored

    assert db(left) == (None, None, None, {})


def test_starting_a_guest_sweeps_the_expired_ones(anonymous, guest, db):
    guest("old")
    expire_guests(db)

    anonymous.post("/api/auth/guest")

    async def guests(session):
        return (await session.exec(select(User).where(User.role == "guest"))).all()

    assert len(db(guests)) == 1


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
        ("get", "/api/glasses/goals"),
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


def test_the_map_shows_only_what_you_see(client, two_guests):
    aino, _ = two_guests

    for params in ({}, {"after_id": 0}, {"after_id": 0, "collection": "manual"}):
        body = client.get("/api/memories/vectors", params=params, headers=aino).json()
        assert {m["text"] for m in body} == {"demo: keys on the hook", "aino: keys under the mat"}


def test_the_map_can_show_only_your_own(client, two_guests):
    aino, _ = two_guests

    # A guest leaves out the demo memories; the admin, everyone else's too.
    for headers, own in ((aino, "aino: keys under the mat"), ({}, "admin: private note")):
        for params in ({"mine": True}, {"mine": True, "collection": "manual"}):
            body = client.get("/api/memories/vectors", params=params, headers=headers).json()
            assert [m["text"] for m in body] == [own]


def test_the_map_of_a_demo_clip_is_never_your_own(client):
    reply = client.get("/api/memories/vectors", params={"clip": 1, "mine": True})

    assert reply.status_code == 422


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

    async def chat(messages, tools):
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


def notes_given(client, path: str, body: dict, headers: dict) -> set[str]:
    """Which of the `two_guests` notes Gemma was given when asked through `path`."""
    seen: list = []
    app.dependency_overrides[get_chat] = lambda: searching_model(seen)
    reply = client.post(path, json=body, headers=headers)
    assert reply.status_code == 200, reply.text
    notes = ("demo: keys on the hook", "admin: private note", "aino: keys under", "ville: keys")
    return {note for note in notes if note in everything_said(seen)}


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask/stream"])
def test_mine_keeps_a_question_to_your_own_memories(client, two_guests, path):
    aino, _ = two_guests
    body = {"question": "where are my keys?", "mine": True}

    # A guest leaves out the demo memories; the admin, everyone else's too.
    assert notes_given(client, path, body, aino) == {"aino: keys under"}
    assert notes_given(client, path, body, {}) == {"admin: private note"}


def test_a_guest_can_end_itself_at_once_and_the_admin_cannot(client, guest):
    token = guest()
    assert client.get("/api/auth/me", headers=token).json()["role"] == "guest"
    assert client.delete("/api/auth/guest", headers=token).status_code == 204
    assert client.get("/api/auth/me", headers=token).status_code == 401
    assert client.delete("/api/auth/guest").status_code == 403
    assert client.get("/api/auth/me").json()["role"] == "admin"
