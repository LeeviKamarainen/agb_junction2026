"""Accounts, sessions and who may see which memories.

There is one admin, who sees every memory, and any number of guests, who register themselves and
see the demo memories (owner DEMO_OWNER) and the ones they added. A login is a random token: the
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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import anyio
from fastapi import Depends, HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import SessionDep
from app.models import AuthSession, User
from app.search import DEMO_OWNER  # memories every user sees; the admin adds them with demo=true
from app.settings import get_settings

log = logging.getLogger(__name__)

COOKIE = "session"
SESSION_DAYS = 30
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


def start_session(session: AsyncSession, user: User) -> str:
    """A new login for `user`; returns the token. The caller commits."""
    assert user.id is not None
    token = secrets.token_urlsafe(32)
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
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        await session.delete(row)
        await session.commit()
        return None
    return await session.get(User, row.user_id)


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
