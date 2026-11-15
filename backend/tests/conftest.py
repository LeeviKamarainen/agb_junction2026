import time

import anyio
import pytest
from fastapi.testclient import TestClient

from app.db import get_engine, new_session
from app.main import app
from app.settings import get_settings

ADMIN_PASSWORD = "admin-password"
# A test's video is re-encoded in well under a second; this only stops a stuck worker from
# hanging the test run.
CONVERSION_TIMEOUT_S = 60


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the app at a fresh SQLite file for each test."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    # No real models, whatever backend/.env says: the startup demo loader would call them.
    monkeypatch.setenv("EMBED_URL", "")
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield path
    anyio.run(get_engine().dispose)
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def anonymous(db_path):
    """A client nobody has logged in on."""
    with TestClient(app) as test_client:  # runs the lifespan, so init_db() too
        yield test_client


@pytest.fixture
def client(anonymous):
    """Logged in as the admin (a session cookie), who sees every memory."""
    reply = anonymous.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert reply.status_code == 200, reply.text
    return anonymous


@pytest.fixture
def guest(anonymous):
    """guest(label) starts a new temporary guest and returns headers that act as it (a bearer
    token), on any client: the bearer wins over the admin's cookie. The label only names it in
    the test; guests get generated names."""

    def start(_label: str = "") -> dict[str, str]:
        before = anonymous.cookies.get("session")
        reply = anonymous.post("/api/auth/guest")
        assert reply.status_code == 201, reply.text
        # Starting a guest logs in by cookie too: put back whoever the client was.
        anonymous.cookies.clear()
        if before:
            anonymous.cookies.set("session", before)
        return {"Authorization": f"Bearer {reply.json()['token']}"}

    return start


@pytest.fixture
def converted(client):
    """converted(clip_id) waits until the background worker has re-encoded the clip's uploaded
    video (app/showcase.py), and returns the clip as the admin's list shows it then."""

    def wait(clip_id: int) -> dict:
        deadline = time.monotonic() + CONVERSION_TIMEOUT_S
        while True:
            clips = client.get("/api/demo/manage").json()
            clip = next(clip for clip in clips if clip["id"] == clip_id)
            if not clip["converting"]:
                return clip
            assert time.monotonic() < deadline, f"clip {clip_id} is still being converted"
            time.sleep(0.05)

    return wait


@pytest.fixture
def db(anonymous):
    """Runs `await work(session)` with a session of its own, on the app's event loop (the test
    client's), so the test and the app share one connection pool: `db(lambda s: s.get(...))`."""

    async def in_session(work):
        async with new_session() as session:
            return await work(session)

    return lambda work: anonymous.portal.call(in_session, work)


@pytest.fixture
def anyio_backend():
    return "asyncio"
