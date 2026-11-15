"""Uploaded playback media, separate from private memories. Files survive database rebuilds,
so random file names prevent a reused demo id from inheriting earlier uploads.

The demos themselves survive a rebuild too, unlike the rest of the database
(docs/adr/0001-disposable-database.md): every change is also written to a manifest beside the
database file, and `restore` reads it back at startup when the table came up empty. Curating a
demo is hand work that a model change elsewhere should not undo.
"""

import io
import json
import logging
import uuid
from pathlib import Path

import anyio
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import faces
from app.models import Showcase
from app.settings import get_settings

log = logging.getLogger(__name__)

MAX_UPLOAD = 200 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_ASSETS = 100


def folder() -> Path:
    root = get_settings().database_path.parent / "showcase-media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(data: bytes, kind: str) -> tuple[str, str]:
    if kind == "video":
        if len(data) >= 16 and data[4:8] == b"ftyp":
            suffix, mime = "mp4", "video/mp4"
        elif data.startswith(b"\x1aE\xdf\xa3"):
            suffix, mime = "webm", "video/webm"
        else:
            raise ValueError("Upload an MP4 or WebM video")
        name = f"{uuid.uuid4().hex}.{suffix}"
        (folder() / name).write_bytes(data)
        return name, mime
    if len(data) > MAX_IMAGE:
        raise ValueError("Each image must be at most 15 MB")
    try:
        with Image.open(io.BytesIO(faces.anonymise(data))) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1600, 1600))
            name = f"{uuid.uuid4().hex}.jpg"
            image.save(folder() / name, "JPEG", quality=90)
        return name, "image/jpeg"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Upload a readable image") from error


def assets(item) -> list[dict]:
    """A Showcase's uploaded media, in order: name, label and content_type each."""
    return json.loads(item.assets_json)


def delete(names: list[str]) -> None:
    for name in names:
        (folder() / Path(name).name).unlink(missing_ok=True)


def manifest() -> Path:
    return get_settings().database_path.parent / "showcases.json"


def _write(text: str) -> None:
    draft = manifest().with_suffix(".tmp")
    draft.write_text(text)
    draft.replace(manifest())  # in one step, so a crash never leaves half a manifest


async def commit(session: AsyncSession) -> None:
    """Commit a change to the demos and write all of them to the manifest."""
    await session.commit()
    rows = [row.model_dump() for row in await session.exec(select(Showcase))]
    await anyio.to_thread.run_sync(_write, json.dumps(rows))


async def restore(session: AsyncSession) -> int:
    """At startup: bring the demos back from the manifest when the database was rebuilt, and
    return how many. With demos and no manifest yet (the first start with this code), write
    one."""
    if (await session.exec(select(Showcase))).first() is not None:
        if not manifest().is_file():
            await commit(session)
        return 0
    if not manifest().is_file():
        return 0
    try:
        rows = json.loads(manifest().read_text())
    except (OSError, json.JSONDecodeError):
        log.exception("The demo manifest %s cannot be read", manifest())
        return 0
    restored = 0
    for row in rows if isinstance(rows, list) else []:
        try:
            session.add(Showcase.model_validate(row))
            restored += 1
        except ValidationError:
            log.warning("Skipping a demo the manifest no longer describes: %r", row)
    await session.commit()
    if restored:
        log.warning("Restored %d demo(s) from %s after a database rebuild", restored, manifest())
    return restored
