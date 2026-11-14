"""Stills from the glasses (or the harness webcam), kept as JPEG files beside the database.

They live outside SQLite on purpose: the database is wiped whenever the models change, the photos
are not. On the VM that folder is on the `db` volume, so photos survive deploys.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from app.settings import get_settings

MAX_BYTES = 10 * 1024 * 1024
NAME = re.compile(r"^\d{8}T\d{12}\.jpg$")


def photos_dir() -> Path:
    folder = get_settings().database_path.parent / "glasses-photos"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def save(data: bytes) -> str:
    """Stores one JPEG and returns its file name, which sorts by time."""
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".jpg"
    (photos_dir() / name).write_bytes(data)
    return name


def path(name: str) -> Path | None:
    if not NAME.match(name):
        return None
    file = photos_dir() / name
    return file if file.is_file() else None


def latest(count: int) -> list[str]:
    return sorted((p.name for p in photos_dir().glob("*.jpg") if NAME.match(p.name)), reverse=True)[
        :count
    ]


def url(name: str) -> str:
    return f"/api/glasses/photos/{name}"


def taken_at(name: str) -> datetime:
    return datetime.strptime(name.removesuffix(".jpg"), "%Y%m%dT%H%M%S%f").replace(tzinfo=UTC)
