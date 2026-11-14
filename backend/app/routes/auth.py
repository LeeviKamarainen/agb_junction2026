"""Register, log in, log out and who am I. Registering makes a guest account and logs it in."""

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
    return UserPublic(id=user.id, username=user.username, role=user.role)


async def _logged_in(request: Request, response: Response, session, user: User) -> LoginResult:
    token = auth.start_session(session, user)
    await session.commit()
    # Secure behind HTTPS; Caddy says so in X-Forwarded-Proto. Plain http://localhost works too.
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=https,
        path="/",
    )
    return LoginResult(**_public(user).model_dump(), token=token)


@router.post("/register", response_model=LoginResult, status_code=201)
async def register(body: Credentials, request: Request, response: Response, session: SessionDep):
    """A new guest account, logged in. 409 when the username is taken."""
    try:
        name = auth.clean_username(body.username)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if (await session.exec(select(User).where(User.username == name))).first() is not None:
        raise HTTPException(409, "That username is taken")
    user = User(
        username=name,
        password_hash=await auth.hash_password_async(body.password),
        role="guest",
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return await _logged_in(request, response, session, user)


@router.post("/login", response_model=LoginResult)
async def login(body: Credentials, request: Request, response: Response, session: SessionDep):
    """401 for a wrong username or password, without saying which."""
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
