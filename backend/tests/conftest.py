import pytest
from fastapi.testclient import TestClient

from app.db import get_engine
from app.main import app
from app.settings import get_settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the app at a fresh SQLite file for each test."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield path
    get_engine().dispose()
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client(db_path):
    with TestClient(app) as test_client:  # runs the lifespan, so init_db() too
        yield test_client
