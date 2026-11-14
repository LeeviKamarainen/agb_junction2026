"""Stills from the glasses (or the harness webcam), kept as JPEG files beside the database.

They live outside SQLite on purpose: the database is wiped whenever the models change, the photos
are not. On the VM that folder is on the `db` volume, so photos survive deploys.

Each user's photos are in a subfolder named by their user id, so a guest only ever lists or opens
their own. Photos taken before there were accounts stay at the top level, seen by the admin only.
`owners` follows app.auth.Viewer.owners: a list of user ids, or None for everyone (the admin).
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


def _folders(owners: list[int] | None) -> list[Path]:
    root = photos_dir()
    if owners is None:
        return [root, *(p for p in root.iterdir() if p.is_dir() and p.name.isdigit())]
    return [root / str(owner) for owner in owners]


def save(data: bytes, owner: int) -> str:
    """Stores one JPEG in `owner`'s folder and returns its file name, which sorts by time."""
    folder = photos_dir() / str(owner)
    folder.mkdir(exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".jpg"
    (folder / name).write_bytes(data)
    return name


def path(name: str, owners: list[int] | None) -> Path | None:
    """The photo called `name` in one of `owners`' folders, or None."""
    if not NAME.match(name):
        return None
    for folder in _folders(owners):
        if (file := folder / name).is_file():
            return file
    return None


def latest(count: int, owners: list[int] | None) -> list[str]:
    """The newest `count` photo names in `owners`' folders."""
    names = {p.name for f in _folders(owners) if f.is_dir() for p in f.glob("*.jpg")}
    return sorted((n for n in names if NAME.match(n)), reverse=True)[:count]


def url(name: str) -> str:
    return f"/api/glasses/photos/{name}"


def taken_at(name: str) -> datetime:
    return datetime.strptime(name.removesuffix(".jpg"), "%Y%m%dT%H%M%S%f").replace(tzinfo=UTC)
