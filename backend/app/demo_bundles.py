"""Recorded demos that live in the repository, so a rebuilt database does not lose them.

The database is disposable (docs/adr/0001-disposable-database.md): a changed model wipes it,
memories and all. A demo recorded with the glasses app (frames stored under a tag, as a memory
collection) is therefore downloaded as a bundle, committed under `backend/demo-data/<tag>/`, and
loaded again by every start as demo memories that every user sees:

- `pack` makes the zip that `GET /api/memories/export?collection=<tag>` sends: `manifest.json` and
  the stills as `0001.jpg`, `0002.jpg`... in time order. The stills already have their faces
  blurred, and each memory's embedding travels with it, so loading needs no embedding model.
- `load` (called at startup, after the database is ready) adds every bundle that is not in the
  database yet. A bundle is skipped, with a warning, when it was made with another embedding model
  than the configured one or with vectors of another width: record it again.

This is not seed.py on purpose: changing seed.py wipes the database, adding a bundle must not.
"""

import base64
import io
import json
import logging
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import faces, media
from app.auth import DEMO_OWNER
from app.models import Memory
from app.search import add_memory, clean_collection, width_error
from app.settings import get_settings

log = logging.getLogger(__name__)

FOLDER = Path(__file__).resolve().parent.parent / "demo-data"
MANIFEST = "manifest.json"
VERSION = 1


class _Skip(Exception):
    """A bundle that cannot be used with this database; not an error."""


def _encode(vector: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()


def _decode(text: str) -> list[float]:
    blob = base64.b64decode(text)
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def pack(memories: list[Memory], vectors: dict[int, list[float]], collection: str) -> bytes:
    """The zip of `memories` (oldest first, as given) that are images with a stored vector."""
    entries = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:  # JPEGs do not compress
        for memory in memories:
            vector = vectors.get(memory.id or 0)
            if memory.kind != "image" or not memory.image_file or vector is None:
                continue
            path = media.path(memory.image_file)
            if not path.is_file():
                continue
            name = f"{len(entries) + 1:04d}.jpg"
            archive.write(path, name)
            entries.append(
                {
                    "file": name,
                    "created_at": memory.created_at.astimezone(UTC).isoformat(),
                    "text": memory.text,
                    "embedding": _encode(vector),
                }
            )
        manifest = {
            "version": VERSION,
            "collection": collection,
            "embed_model": get_settings().embed_model,
            "width": len(next(iter(vectors.values()), [])),
            "memories": entries,
        }
        archive.writestr(MANIFEST, json.dumps(manifest, indent=1))
    return buffer.getvalue()


async def load(session: AsyncSession, folder: Path | None = None) -> None:
    """Adds every bundle under `folder` (default `backend/demo-data`) not yet in the database."""
    root = folder or FOLDER
    if not root.is_dir():
        return
    for bundle in sorted(p for p in root.iterdir() if (p / MANIFEST).is_file()):
        try:
            added = await _load_bundle(session, bundle)
        except Exception:
            await session.rollback()
            log.exception("Demo bundle %s could not be loaded", bundle.name)
        else:
            if added:
                log.info("Loaded the demo bundle %s: %d stills", bundle.name, added)


async def _load_bundle(session: AsyncSession, bundle: Path) -> int:
    manifest = json.loads((bundle / MANIFEST).read_text(encoding="utf-8"))
    collection = clean_collection(manifest["collection"])
    exists = (
        await session.exec(
            select(func.count(col(Memory.id))).where(
                col(Memory.collection) == collection, col(Memory.owner_id) == DEMO_OWNER
            )
        )
    ).one()
    if exists:
        return 0  # already there (an ordinary restart)
    model = get_settings().embed_model
    if manifest.get("embed_model") not in (None, "", model):
        log.warning(
            "Skipping demo bundle %s: made with %s, but the embedding model is %s; record it again",
            bundle.name,
            manifest["embed_model"],
            model,
        )
        return 0
    memories = manifest["memories"]
    saved: list[str] = []
    try:
        for entry in memories:
            vector = _decode(entry["embedding"])
            if problem := width_error(len(vector)):
                log.warning("Skipping demo bundle %s: %s", bundle.name, problem)
                raise _Skip
            data = (bundle / Path(entry["file"]).name).read_bytes()
            # Already blurred when recorded; once more costs little and keeps the rule that nothing
            # is stored with a face in it, whatever was committed.
            data = await anyio.to_thread.run_sync(faces.anonymise, data)
            name = await anyio.to_thread.run_sync(media.save_image, data)
            saved.append(name)
            moment = datetime.fromisoformat(entry["created_at"])
            await add_memory(
                session,
                "image",
                vector,
                moment if moment.tzinfo else moment.replace(tzinfo=UTC),
                entry.get("text", ""),
                name,
                collection=collection,
                owner_id=DEMO_OWNER,
            )
        await session.commit()
    except _Skip:
        await _discard(session, saved)
        return 0
    except BaseException:
        await _discard(session, saved)
        raise
    return len(memories)


async def _discard(session: AsyncSession, saved: list[str]) -> None:
    """Undoes a half-loaded bundle: its rows and its image files."""
    await session.rollback()
    for name in saved:
        media.delete(name)
