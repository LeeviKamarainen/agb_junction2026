"""The glass-memory API (memory/ on the Gemma server) as the glasses' fallback memory.

It runs Gemma itself, so it works when the backend cannot reach the chat model directly. It
answers questions within one batch of stills, so the glasses keep one batch holding all their
photos and post it again whenever photos were added (state in `memory-batch.json` beside them).
Its keyframes reach clients through /api/glasses/memory/..., so the token stays here.
"""

import json
import re

import httpx

from app.glasses import photos
from app.settings import get_settings

BATCH = re.compile(r"^\d{8}T\d{12}$")
KEYFRAME = re.compile(r"^[\w.-]+\.jpg$")
MAX_FRAMES = 1000  # the API's limit per batch
# The API's first request loads CLIP; a batch also waits for Gemma's summary.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class Unavailable(Exception):
    pass


def configured() -> bool:
    settings = get_settings()
    return bool(settings.memory_api_url and settings.memory_api_token)


def _client() -> httpx.Client:
    settings = get_settings()
    assert settings.memory_api_url and settings.memory_api_token
    return httpx.Client(
        base_url=settings.memory_api_url.rstrip("/"),
        headers={"Authorization": f"Bearer {settings.memory_api_token}"},
        timeout=TIMEOUT,
    )


def _post_batch(client: httpx.Client, frames: list[tuple[str, bytes]]) -> dict:
    files = [("frames", (name, data, "image/jpeg")) for name, data in frames]
    reply = client.post("/batches", files=files, data={"every_s": "1.0"})
    reply.raise_for_status()
    return reply.json()


def describe(jpeg: bytes, name: str) -> str:
    """Gemma's description of one photo: the summary of a batch holding only it."""
    try:
        with _client() as client:
            return (_post_batch(client, [(name, jpeg)]).get("summary") or "").strip()
    except (httpx.HTTPError, ValueError) as error:
        raise Unavailable(str(error)) from error


def _state_file():
    return photos.photos_dir() / "memory-batch.json"


def _photo_batch(client: httpx.Client) -> str | None:
    """The id of a batch holding every glasses photo, posting a new one if photos were added."""
    names = sorted(photos.latest(MAX_FRAMES))
    if not names:
        return None
    try:
        state = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("photos") == names and BATCH.match(str(state.get("batch", ""))):
        return state["batch"]
    frames = [(name, file.read_bytes()) for name in names if (file := photos.path(name))]
    batch = _post_batch(client, frames)["batch"]
    _state_file().write_text(json.dumps({"batch": batch, "photos": names}))
    return batch


def ask(question: str) -> tuple[str, list[str]]:
    """Gemma's answer from the glasses' photos, and URLs of the stills it was given."""
    try:
        with _client() as client:
            batch = _photo_batch(client)
            if batch is None:
                return "I have no photos to look through yet.", []
            reply = client.post(f"/batches/{batch}/ask", json={"question": question, "k": 4})
            reply.raise_for_status()
            body = reply.json()
            return body["answer"], [keyframe_url(batch, name) for name in body["keyframes"]]
    except (httpx.HTTPError, KeyError, ValueError) as error:
        raise Unavailable(str(error)) from error


def keyframe(batch: str, name: str) -> bytes | None:
    if not configured() or not BATCH.match(batch) or not KEYFRAME.match(name):
        return None
    try:
        with _client() as client:
            response = client.get(f"/batches/{batch}/keyframes/{name}")
    except httpx.HTTPError as error:
        raise Unavailable(str(error)) from error
    return response.content if response.status_code == 200 else None


def keyframe_url(batch: str, name: str) -> str:
    return f"/api/glasses/memory/{batch}/keyframes/{name}"
