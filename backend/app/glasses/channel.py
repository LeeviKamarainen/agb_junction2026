"""One WebSocket per glasses client, at /api/glasses/ws: the live traffic between the phone (or
the browser harness) and the backend, so the server can push warnings and frames skip the
per-request overhead. The HTTP routes do the same work one request at a time.

Messages are JSON text frames with a `type`. A photo or frame is a small JSON header followed by
the JPEG as the next, binary, frame. Client to server:

- `command`: `text`, `action` or `question`, exactly as POST /api/glasses/command; answered with a
  `reply` carrying the same `id`.
- `photo` + JPEG: as POST /api/glasses/photos; answered with a `reply`.
- `watch` with `on`: start or stop watching for reminders.
- `frame` + JPEG, while watching: as POST /api/glasses/watch; answered with a `watch_result`.
  The latest frame wins: while one is being checked only the newest arrival waits, and the ones
  it replaced are counted in the next result's `dropped`, so a slow model never builds a queue.
- `ping`: answered with a `pong`.

Server to client: `reply` (a GlassesReply to draw and speak), `watch_result`, `error` and `pong`.
A warning is pushed as a `reply` without an `id`, after the `watch_result` that found it.

Commands and photos run concurrently, frames one at a time beside them, each as a coroutine with
its own database session: the queries and model calls are awaited, so one slow client cannot
stall the others. One client runs at most WORKERS of them at a time; on disconnect, commands and
frames still running are cancelled, while photos finish and are stored.

Auth: the user's login (app/auth.py), as for every /api/glasses route. A browser sends its
`session` cookie with the handshake; a native client sends `Authorization: Bearer <token>`, or
`{"type": "auth", "token": ...}` as the first message, with the token /api/auth/login returned.
Without a valid login the socket is closed with 1008. Not a query parameter: uvicorn logs the URL.
Everything the socket does then is scoped to that user, like the HTTP routes.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

import anyio
from anyio.abc import TaskGroup
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app import auth
from app.auth import Viewer
from app.db import new_session
from app.glasses import actions
from app.glasses.actions import Command, WatchResult
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder

log = logging.getLogger(__name__)

# The WebSocket close code for a refused token (policy violation).
REFUSED = 1008
# How long a client has to send its `auth` message.
AUTH_TIMEOUT_S = 10
# Commands, photos and frames one client may have running at once.
WORKERS = 4


# --- Client to server --------------------------------------------------------


class CommandMessage(Command):
    type: Literal["command"]
    id: int | None = None


class PhotoMessage(BaseModel):
    """The header of a photo; the JPEG is the next, binary, frame."""

    type: Literal["photo"]
    id: int | None = None


class FrameMessage(BaseModel):
    """The header of a watched frame; the JPEG is the next, binary, frame."""

    type: Literal["frame"]
    id: int | None = None


class WatchMessage(BaseModel):
    type: Literal["watch"]
    on: bool


class PingMessage(BaseModel):
    type: Literal["ping"]
    id: int | None = None


class AuthMessage(BaseModel):
    """The first message, from a client that sent no cookie or bearer header: its login token.
    Ignored later."""

    type: Literal["auth"]
    token: str


ClientMessage = Annotated[
    CommandMessage | PhotoMessage | FrameMessage | WatchMessage | PingMessage | AuthMessage,
    Field(discriminator="type"),
]
client_message: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# --- Server to client --------------------------------------------------------


class ReplyMessage(BaseModel):
    type: Literal["reply"] = "reply"
    id: int | None  # the command's or photo's; None for a pushed warning
    reply: GlassesReply


class WatchResultMessage(BaseModel):
    type: Literal["watch_result"] = "watch_result"
    id: int | None  # the checked frame's
    result: WatchResult
    dropped: int  # frames replaced by newer ones while the previous check ran
    ms: int  # how long the check took


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    id: int | None = None
    status: int  # as the HTTP route would answer: 413, 415, 422, 500…
    detail: str


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"
    id: int | None = None


ServerMessage = ReplyMessage | WatchResultMessage | ErrorMessage | PongMessage


async def _viewer_for(token: str | None) -> Viewer | None:
    async with new_session() as session:
        user = await auth.user_for(session, token)
        return Viewer(user) if user else None


async def authenticated(websocket: WebSocket) -> Viewer | None:
    """Who logged in: the bearer header or session cookie, or else a first `auth` message."""
    header = websocket.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else None
    given = given or websocket.cookies.get(auth.COOKIE)
    if given:
        return await _viewer_for(given)
    with anyio.move_on_after(AUTH_TIMEOUT_S):
        event = await websocket.receive()
        text = event.get("text")
        if text is None:
            return None
        try:
            message = client_message.validate_json(text)
        except ValidationError:
            return None
        if isinstance(message, AuthMessage):
            return await _viewer_for(message.token)
    return None


class _Left(Exception):
    """The client disconnected."""


class Channel:
    def __init__(
        self, websocket: WebSocket, viewer: Viewer, chat: Chat | None, embed: Embedder | None
    ):
        self.websocket = websocket
        self.viewer = viewer  # every action runs with this user's scope
        self.chat = chat
        self.embed = embed
        self.send_lock = anyio.Lock()
        self.running = anyio.Semaphore(WORKERS)
        self.watching = False
        # The newest frame not yet checked, and how many it and its predecessors replaced.
        self.frame: tuple[int | None, bytes] | None = None
        self.dropped = 0
        self.frame_ready = anyio.Event()

    async def send(self, message: ServerMessage) -> None:
        async with self.send_lock:
            try:
                await self.websocket.send_text(message.model_dump_json())
            except (WebSocketDisconnect, RuntimeError):
                pass  # the client left; the receive loop ends the connection

    async def receive(self, tasks: TaskGroup) -> None:
        """Reads messages until the client disconnects."""
        while True:
            event = await self.websocket.receive()
            if event["type"] == "websocket.disconnect":
                return
            text = event.get("text")
            if text is None:
                await self.send(
                    ErrorMessage(status=400, detail="a JPEG must follow a photo or frame header")
                )
                continue
            try:
                message = client_message.validate_json(text)
            except ValidationError as error:
                await self.send(ErrorMessage(id=_id_of(text), status=422, detail=str(error)))
                continue

            match message:
                case PingMessage():
                    await self.send(PongMessage(id=message.id))
                case AuthMessage():
                    pass
                case WatchMessage():
                    self.watching = message.on
                    if not message.on:
                        self.frame, self.dropped = None, 0
                case CommandMessage():
                    tasks.start_soon(self.run_command, message)
                case PhotoMessage():
                    data = await self.receive_jpeg(message.id)
                    if data is not None:
                        tasks.start_soon(self.run_photo, message.id, data)
                case FrameMessage():
                    data = await self.receive_jpeg(message.id)
                    if data is None:
                        pass
                    elif not self.watching:
                        await self.send(
                            ErrorMessage(id=message.id, status=409, detail="send watch on first")
                        )
                    else:
                        self.offer_frame(message.id, data)

    async def receive_jpeg(self, id: int | None) -> bytes | None:
        """The binary frame after a header, or None (and an error sent) when text came instead."""
        event = await self.websocket.receive()
        if event["type"] == "websocket.disconnect":
            raise _Left
        data = event.get("bytes")
        if data is None:
            await self.send(ErrorMessage(id=id, status=400, detail="expected the JPEG next"))
        return data

    def offer_frame(self, id: int | None, data: bytes) -> None:
        if self.frame is not None:
            self.dropped += 1
        self.frame = (id, data)
        self.frame_ready.set()

    async def check_frames(self) -> None:
        """Checks the newest frame, then the newest that arrived meanwhile, and so on."""
        while True:
            await self.frame_ready.wait()
            self.frame_ready = anyio.Event()
            if self.frame is None:
                continue
            (id, data), self.frame = self.frame, None
            dropped, self.dropped = self.dropped, 0
            started = time.perf_counter()
            result = await self.work(id, actions.watch, self.viewer, data, self.chat)
            if result is None:
                continue
            ms = round((time.perf_counter() - started) * 1000)
            await self.send(WatchResultMessage(id=id, result=result, dropped=dropped, ms=ms))
            if result.warning is not None and self.watching:
                await self.send(ReplyMessage(id=None, reply=result.warning))

    async def run_command(self, message: CommandMessage) -> None:
        body = Command(text=message.text, action=message.action, question=message.question)
        reply = await self.work(
            message.id, actions.command, self.viewer, body, self.chat, self.embed
        )
        if reply is not None:
            await self.send(ReplyMessage(id=message.id, reply=reply))

    async def run_photo(self, id: int | None, data: bytes) -> None:
        # Shielded: a photo the wearer took is saved, described and stored even when the client
        # disconnects meanwhile, so a later question still finds it.
        with anyio.CancelScope(shield=True):
            reply = await self.work(id, actions.photo, self.viewer, data, self.chat, self.embed)
        if reply is not None:
            await self.send(ReplyMessage(id=id, reply=reply))

    async def work[T](self, id: int | None, work: Callable[..., Awaitable[T]], *args) -> T | None:
        """Awaits `work(session, *args)` with a session of its own; a failure becomes an `error`
        message, not a closed socket."""
        try:
            async with self.running, new_session() as session:
                return await work(session, *args)
        except HTTPException as error:
            await self.send(ErrorMessage(id=id, status=error.status_code, detail=error.detail))
        except Exception as error:
            log.exception("A glasses message failed")
            await self.send(
                ErrorMessage(id=id, status=500, detail=f"{type(error).__name__}: {error}")
            )
        return None


def _id_of(text: str) -> int | None:
    """The `id` of a message that failed validation, so the client can match the error."""
    try:
        id = TypeAdapter(dict).validate_json(text).get("id")
    except ValidationError:
        return None
    return id if isinstance(id, int) else None


async def serve(websocket: WebSocket, chat: Chat | None, embed: Embedder | None) -> None:
    # Refused after accepting: a browser only learns the close code of an opened socket.
    await websocket.accept()
    try:
        viewer = await authenticated(websocket)
    except WebSocketDisconnect:
        return
    if viewer is None:
        try:
            await websocket.close(code=REFUSED, reason="log in first")
        except (WebSocketDisconnect, RuntimeError):
            pass  # it left first
        return
    channel = Channel(websocket, viewer, chat, embed)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(channel.check_frames)
        try:
            await channel.receive(tasks)
        except _Left:
            pass
        tasks.cancel_scope.cancel()
