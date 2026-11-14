import pytest
from fastapi.testclient import TestClient

from app.db import get_engine
from app.main import app
from app.settings import get_settings

ADMIN_PASSWORD = "admin-password"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the app at a fresh SQLite file for each test."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield path
    get_engine().dispose()
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
    """guest("name") registers a guest and returns headers that act as them (a bearer token),
    on any client: the bearer wins over the admin's cookie."""

    def register(name: str) -> dict[str, str]:
        before = anonymous.cookies.get("session")
        reply = anonymous.post(
            "/api/auth/register", json={"username": name, "password": "guest-password"}
        )
        assert reply.status_code == 201, reply.text
        # Registering logs in by cookie too: put back whoever the client was (the admin, or no one).
        anonymous.cookies.clear()
        if before:
            anonymous.cookies.set("session", before)
        return {"Authorization": f"Bearer {reply.json()['token']}"}

    return register
