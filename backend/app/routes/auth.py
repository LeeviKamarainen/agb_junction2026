"""Start a guest session, log in (the admin), log out, and who am I."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select

from app import auth
from app.auth import OptionalViewerDep, ViewerDep
from app.db import SessionDep
from app.models import Credentials, LoginResult, User, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


def _public(user: User) -> UserPublic:
    assert user.id is not None
    return UserPublic(
        id=user.id, username=user.username, role=user.role, expires_at=user.expires_at
    )


def _seconds_left(user: User) -> int:
    if user.expires_at is None:
        return auth.SESSION_DAYS * 24 * 3600
    expires = user.expires_at if user.expires_at.tzinfo else user.expires_at.replace(tzinfo=UTC)
    return max(0, int((expires - datetime.now(UTC)).total_seconds()))


async def _logged_in(request: Request, response: Response, session, user: User) -> LoginResult:
    token = auth.start_session(session, user)
    await session.commit()
    # Secure behind HTTPS; Caddy says so in X-Forwarded-Proto. Plain http://localhost works too.
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=_seconds_left(user),
        httponly=True,
        samesite="lax",
        secure=https,
        path="/",
    )
    return LoginResult(**_public(user).model_dump(), token=token)


@router.post("/guest", response_model=LoginResult, status_code=201)
async def guest(request: Request, response: Response, session: SessionDep):
    """A new temporary guest account, logged in: a generated name, no password. It and everything
    it adds are deleted after 30 minutes (auth.GUEST_MINUTES)."""
    return await _logged_in(request, response, session, await auth.create_guest(session))


@router.post("/login", response_model=LoginResult)
async def login(body: Credentials, request: Request, response: Response, session: SessionDep):
    """The admin's login (guests have no password). 401 for a wrong username or password,
    without saying which."""
    name = body.username.strip().lower()
    user = (await session.exec(select(User).where(User.username == name))).first()
    if user is None or not await auth.verify_password_async(body.password, user.password_hash):
        raise HTTPException(401, "Wrong username or password")
    return await _logged_in(request, response, session, user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: SessionDep):
    if token := auth.token_of(request):
        await auth.end_session(session, token)
        await session.commit()
    response.delete_cookie(auth.COOKIE, path="/")


@router.get("/me", response_model=UserPublic)
async def me(viewer: ViewerDep):
    """The logged-in user; 401 when nobody is."""
    return _public(viewer.user)


@router.get("/session", response_model=UserPublic | None)
async def current(viewer: OptionalViewerDep):
    """The logged-in user, or null: for pages that show a login link instead of failing."""
    return _public(viewer.user) if viewer else None
