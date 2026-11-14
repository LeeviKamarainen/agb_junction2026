"""Uploaded playback media, separate from private memories. Files survive database rebuilds,
so random file names prevent a reused demo id from inheriting earlier uploads."""

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app import faces
from app.settings import get_settings

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


def delete(names: list[str]) -> None:
    for name in names:
        (folder() / Path(name).name).unlink(missing_ok=True)
