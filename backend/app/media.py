"""Image files for memories: uploads are shrunk to JPEG and kept next to the database.

Files are named by a random UUID, not the memory id: the database is disposable and reuses ids
after a rebuild, while the media folder is kept.
"""

import base64
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.settings import get_settings

MAX_SIDE = 1024  # Gemma and CLIP both downscale further, so bigger only costs bytes
QUALITY = 85


def media_dir() -> Path:
    path = get_settings().database_path.parent / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(data: bytes) -> str:
    """Store an uploaded image as a JPEG at most MAX_SIDE wide or tall; returns its file name.

    Raises ValueError when the bytes are not an image Pillow can read.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)  # phone photos are often stored sideways
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Not an image that can be read (JPEG, PNG, WebP, ...)") from error
    image = image.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    name = f"{uuid.uuid4().hex}.jpg"
    image.save(media_dir() / name, "JPEG", quality=QUALITY)
    return name


def path(name: str) -> Path:
    return media_dir() / Path(name).name  # .name: never leave the folder


def data_url(name: str) -> str:
    """The image as a data: URL, which the chat and embedding servers read without fetching."""
    return "data:image/jpeg;base64," + base64.b64encode(path(name).read_bytes()).decode()


def delete(name: str) -> None:
    path(name).unlink(missing_ok=True)
