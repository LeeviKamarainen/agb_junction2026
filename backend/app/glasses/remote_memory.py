"""The glass-memory API (memory/ on the Gemma server) as the glasses' fallback memory.

It runs Gemma itself, so it works when the backend cannot reach the chat model directly. It
answers questions within one batch of stills, so each user has one batch holding the photos they
may see (app.auth.Viewer.owners), posted again whenever photos were added (state in
`memory-batch-<user>.json` beside them). Gemma never gets another user's photos in a batch.
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


def _state_file(user: int):
    return photos.photos_dir() / f"memory-batch-{user}.json"


def current_batch(user: int) -> str | None:
    """The batch last posted for `user`: the only one whose keyframes they may fetch."""
    try:
        batch = str(json.loads(_state_file(user).read_text()).get("batch", ""))
    except (OSError, ValueError):
        return None
    return batch if BATCH.match(batch) else None


def _photo_batch(client: httpx.Client, user: int, owners: list[int] | None) -> str | None:
    """The id of a batch holding the photos `user` may see, posting a new one if they changed."""
    names = sorted(photos.latest(MAX_FRAMES, owners))
    if not names:
        return None
    try:
        state = json.loads(_state_file(user).read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("photos") == names and BATCH.match(str(state.get("batch", ""))):
        return state["batch"]
    frames = [(n, file.read_bytes()) for n in names if (file := photos.path(n, owners))]
    batch = _post_batch(client, frames)["batch"]
    _state_file(user).write_text(json.dumps({"batch": batch, "photos": names}))
    return batch


def ask(question: str, user: int, owners: list[int] | None) -> tuple[str, list[str]]:
    """Gemma's answer from the photos `user` may see, and URLs of the stills it was given."""
    try:
        with _client() as client:
            batch = _photo_batch(client, user, owners)
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
