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

import anyio
from fastapi import Depends, HTTPException, Request
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

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


# scrypt is CPU work on purpose (about 50 ms): the async helpers run it off the event loop.
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


async def hash_password_async(password: str) -> str:
    return await anyio.to_thread.run_sync(hash_password, password)


async def verify_password_async(password: str, stored: str) -> bool:
    return await anyio.to_thread.run_sync(verify_password, password, stored)


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


def start_session(session: AsyncSession, user: User) -> str:
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


async def end_session(session: AsyncSession, token: str) -> None:
    row = await session.get(AuthSession, _token_hash(token))
    if row is not None:
        await session.delete(row)


def token_of(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


async def user_for(session: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    row = await session.get(AuthSession, _token_hash(token))
    if row is None:
        return None
    now = datetime.now(UTC)
    if _aware(row.expires_at) <= now:
        await session.delete(row)
        await session.commit()
        return None
    user = await session.get(User, row.user_id)
    if user is not None and user.expires_at is not None and _aware(user.expires_at) <= now:
        await purge_expired_guests(session)
        return None
    return user


async def create_guest(session: AsyncSession) -> User:
    """A new temporary guest: a generated name, no password, GUEST_MINUTES to live. Deletes the
    guests that already expired first. Commits."""
    await purge_expired_guests(session)
    now = datetime.now(UTC)
    while True:
        name = f"guest-{secrets.token_hex(8)}"
        if (await session.exec(select(User).where(User.username == name))).first() is None:
            break
    guest = User(
        username=name,
        password_hash="!",  # guests return by username, never through the admin password login
        role="guest",
        created_at=now,
        expires_at=now + timedelta(minutes=GUEST_MINUTES),
    )
    session.add(guest)
    await session.flush()
    assert guest.id is not None
    await anyio.to_thread.run_sync(_delete_guest_files, guest.id)
    await session.commit()
    await session.refresh(guest)
    return guest


def _delete_files(media_files: list[str], user_ids: list[int]) -> None:
    for name in media_files:
        media.delete(name)
    for user_id in user_ids:
        _delete_guest_files(user_id)


def _delete_guest_files(user_id: int) -> None:
    from app.glasses import photos  # here: app.glasses imports this module

    try:
        shutil.rmtree(photos.photos_dir() / str(user_id))
    except FileNotFoundError:
        pass


async def purge_expired_guests(session: AsyncSession) -> int:
    """Deletes every expired guest with its logins, memories (and their image files), reminders
    and glasses photos. Returns how many. Commits."""
    now = datetime.now(UTC)
    guests = await session.exec(
        select(User).where(User.role == "guest", col(User.expires_at).is_not(None))
    )
    expired = [u for u in guests if u.expires_at is not None and _aware(u.expires_at) <= now]
    files: list[str] = []
    connection = await session.connection()
    for user in expired:
        assert user.id is not None
        for memory in await session.exec(select(Memory).where(Memory.owner_id == user.id)):
            await vectors.delete(connection, TABLE, memory.id or 0)
            if memory.image_file:
                files.append(memory.image_file)
            await session.delete(memory)
        for trigger in await session.exec(select(Trigger).where(Trigger.owner_id == user.id)):
            await vectors.delete(connection, "trigger_embedding", trigger.id or 0)
            await session.delete(trigger)
        for reminder in await session.exec(select(Reminder).where(Reminder.owner_id == user.id)):
            await session.delete(reminder)
        logins = await session.exec(select(AuthSession).where(AuthSession.user_id == user.id))
        for login in logins:
            await session.delete(login)
        await session.delete(user)
    await session.commit()
    # After the commit, so a failed delete never leaves rows without files; off the event loop,
    # since a guest's photo folder can hold many files.
    ids = [user.id for user in expired if user.id is not None]
    await anyio.to_thread.run_sync(_delete_files, files, ids)
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


async def optional_viewer(request: Request, session: SessionDep) -> Viewer | None:
    user = await user_for(session, token_of(request))
    return Viewer(user) if user else None


def viewer(found: Annotated[Viewer | None, Depends(optional_viewer)]) -> Viewer:
    if found is None:
        raise HTTPException(401, "Log in first", headers={"WWW-Authenticate": "Bearer"})
    return found


ViewerDep = Annotated[Viewer, Depends(viewer)]
OptionalViewerDep = Annotated[Viewer | None, Depends(optional_viewer)]


async def ensure_admin(session: AsyncSession) -> None:
    """Create the admin account, or reset its password to ADMIN_PASSWORD, on every start."""
    settings = get_settings()
    name = clean_username(settings.admin_username)
    admin = (await session.exec(select(User).where(User.username == name))).first()
    password = settings.admin_password
    if admin is not None:
        if password and not await verify_password_async(password, admin.password_hash):
            admin.password_hash = await hash_password_async(password)
        admin.role = "admin"
        session.add(admin)
        await session.commit()
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
        password_hash=await hash_password_async(password),
        role="admin",
        created_at=datetime.now(UTC),
    )
    session.add(admin)
    await session.commit()
