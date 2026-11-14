"""Accounts, sessions and who may see which memories.

There is one admin, who logs in with a password and sees every memory, and any number of
temporary guests: one click makes a guest with a generated name and no password, which sees the
demo memories (owner DEMO_OWNER) and the ones it added. A guest expires after GUEST_MINUTES, and
purge_expired_guests then deletes it with everything it added. A login is a random token: the
browser keeps it in an HttpOnly cookie, other clients (the glasses' phone app) send it as
`Authorization: Bearer <token>`. Only its sha256 is stored.

The scope is applied where memories are read: listing, images, vector search and keyword search
(app/search.py). The chat model only sees what vector_search returns, so a guest's question can
never bring another guest's notes or stills into the model's context.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, col, select

from app import media, vectors
from app.db import SessionDep
from app.models import AuthSession, Memory, Reminder, Trigger, User
from app.search import (
    DEMO_OWNER,  # memories every user sees; the admin adds them with demo=true
    TABLE,
)
from app.settings import get_settings

log = logging.getLogger(__name__)

COOKIE = "session"
SESSION_DAYS = 30  # the admin's logins
GUEST_MINUTES = 60  # a guest account, its login and its memories
USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # about 50 ms and 16 MB per hash


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=32, **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(got, expected)


def clean_username(name: str) -> str:
    """Lower case and trimmed. Raises ValueError unless it is 3 to 32 of a-z, 0-9, _ . and -."""
    cleaned = name.strip().lower()
    if not USERNAME.fullmatch(cleaned):
        raise ValueError(
            "A username is 3 to 32 of a-z, 0-9, _ . and -, starting with a letter or digit"
        )
    return cleaned


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)  # SQLite drops the offset


def start_session(session: Session, user: User) -> str:
    """A new login for `user`, ending when a guest account does; returns the token. The caller
    commits."""
    assert user.id is not None
    token = secrets.token_urlsafe(32)
    if user.expires_at is not None:
        expires = _aware(user.expires_at)
    else:
        expires = datetime.now(UTC) + timedelta(days=SESSION_DAYS)
    session.add(AuthSession(token_hash=_token_hash(token), user_id=user.id, expires_at=expires))
    return token


def end_session(session: Session, token: str) -> None:
    row = session.get(AuthSession, _token_hash(token))
    if row is not None:
        session.delete(row)


def token_of(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def user_for(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = session.get(AuthSession, _token_hash(token))
    if row is None:
        return None
    now = datetime.now(UTC)
    if _aware(row.expires_at) <= now:
        session.delete(row)
        session.commit()
        return None
    user = session.get(User, row.user_id)
    if user is not None and user.expires_at is not None and _aware(user.expires_at) <= now:
        purge_expired_guests(session)
        return None
    return user


def create_guest(session: Session) -> User:
    """A new temporary guest: a generated name, no password, GUEST_MINUTES to live. Deletes the
    guests that already expired first. Commits."""
    purge_expired_guests(session)
    now = datetime.now(UTC)
    while True:
        # The username also lets the wearer reopen this temporary account on another device.
        name = f"guest-{secrets.token_hex(8)}"
        if session.exec(select(User).where(User.username == name)).first() is None:
            break
    guest = User(
        username=name,
        password_hash="!",  # guests return by username, never through the admin password login
        role="guest",
        created_at=now,
        expires_at=now + timedelta(minutes=GUEST_MINUTES),
    )
    session.add(guest)
    session.flush()
    assert guest.id is not None
    # Photos outlive database rebuilds, which restart integer IDs. A new guest cannot own
    # files already on disk under its newly assigned ID.
    _delete_guest_files(guest.id)
    session.commit()
    session.refresh(guest)
    return guest


def _delete_guest_files(user_id: int) -> None:
    from app.glasses import photos  # here: app.glasses imports this module

    try:
        shutil.rmtree(photos.photos_dir() / str(user_id))
    except FileNotFoundError:
        pass
    (photos.photos_dir() / f"memory-batch-{user_id}.json").unlink(missing_ok=True)


def purge_expired_guests(session: Session) -> int:
    """Deletes every expired guest with its logins, memories (and their image files), reminders,
    glasses photos and memory-API batch. Returns how many. Commits."""
    now = datetime.now(UTC)
    expired = [
        user
        for user in session.exec(
            select(User).where(User.role == "guest", col(User.expires_at).is_not(None))
        )
        if user.expires_at is not None and _aware(user.expires_at) <= now
    ]
    files: list[str] = []
    for user in expired:
        assert user.id is not None
        for memory in session.exec(select(Memory).where(Memory.owner_id == user.id)):
            vectors.delete(session.connection(), TABLE, memory.id or 0)
            if memory.image_file:
                files.append(memory.image_file)
            session.delete(memory)
        for trigger in session.exec(select(Trigger).where(Trigger.owner_id == user.id)):
            vectors.delete(session.connection(), "trigger_embedding", trigger.id or 0)
            session.delete(trigger)
        for reminder in session.exec(select(Reminder).where(Reminder.owner_id == user.id)):
            session.delete(reminder)
        for login in session.exec(select(AuthSession).where(AuthSession.user_id == user.id)):
            session.delete(login)
        session.delete(user)
    session.commit()
    for name in files:  # after the commit: a failed delete never leaves rows without files
        media.delete(name)
    for user in expired:
        assert user.id is not None
        _delete_guest_files(user.id)
    if expired:
        log.info("Deleted %d expired guest account(s)", len(expired))
    return len(expired)


@dataclass(frozen=True)
class Viewer:
    """The logged-in user and what they may see."""

    user: User

    @property
    def id(self) -> int:
        assert self.user.id is not None
        return self.user.id

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    @property
    def owners(self) -> list[int] | None:
        """The owner ids whose memories this user sees; None for everything (the admin)."""
        return None if self.is_admin else [DEMO_OWNER, self.id]

    def can_see(self, owner_id: int) -> bool:
        return self.is_admin or owner_id in (DEMO_OWNER, self.id)

    def can_change(self, owner_id: int) -> bool:
        """Delete or edit: the admin anything, a guest only their own."""
        return self.is_admin or owner_id == self.id


def optional_viewer(request: Request, session: SessionDep) -> Viewer | None:
    user = user_for(session, token_of(request))
    return Viewer(user) if user else None


def viewer(found: Annotated[Viewer | None, Depends(optional_viewer)]) -> Viewer:
    if found is None:
        raise HTTPException(401, "Log in first", headers={"WWW-Authenticate": "Bearer"})
    return found


ViewerDep = Annotated[Viewer, Depends(viewer)]
OptionalViewerDep = Annotated[Viewer | None, Depends(optional_viewer)]


def ensure_admin(session: Session) -> None:
    """Create the admin account, or reset its password to ADMIN_PASSWORD, on every start."""
    settings = get_settings()
    name = clean_username(settings.admin_username)
    admin = session.exec(select(User).where(User.username == name)).first()
    password = settings.admin_password
    if admin is not None:
        if password and not verify_password(password, admin.password_hash):
            admin.password_hash = hash_password(password)
        admin.role = "admin"
        session.add(admin)
        session.commit()
        return
    if not password:
        password = secrets.token_urlsafe(12)
        log.warning(
            "No ADMIN_PASSWORD set: created admin %r with password %s (set ADMIN_PASSWORD to "
            "choose one)",
            name,
            password,
        )
    admin = User(
        username=name,
        password_hash=hash_password(password),
        role="admin",
        created_at=datetime.now(UTC),
    )
    session.add(admin)
    session.commit()
