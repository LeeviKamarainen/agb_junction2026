"""One WebSocket per glasses client, at /api/glasses/ws: the live traffic between the phone (or
the browser harness) and the backend, so frames and commands skip the per-request overhead. The
HTTP routes do the same work one request at a time.

Messages are JSON text frames with a `type`. A frame is a small JSON header followed by the JPEG
as the next, binary, frame. Client to server:

- `command`: `action` or `question`, exactly as POST /api/glasses/command; answered with a `reply`
  carrying the same `id`.
- `memory_frame` + JPEG: as POST /api/glasses/frames, with `source` (stream/single),
  `stream_id`, optional `seen_at` and optional `collection` (a tag such as one recorded demo,
  default "glasses"); answered with `memory_result` containing a FrameReply.
  One may be in flight per connection; another gets 429, keeping camera uploads bounded. A
  trigger that fires on the frame comes back in that result's `message` (app/glasses/frames.py).
- `ping`: answered with a `pong`.

Server to client: `reply` (a GlassesReply to draw and speak), `memory_result`, `error` and
`pong`.

Commands run concurrently, memory frames one at a time beside them, each as a coroutine with its own
database session: the queries and model calls are awaited, so one slow client cannot stall the
others. One client runs at most WORKERS of them at a time; on disconnect, commands and frames
still running are cancelled.

Auth: the user's login (app/auth.py), as for every /api/glasses route. A browser sends its
`session` cookie with the handshake; a native client sends `Authorization: Bearer <token>`, or
`{"type": "auth", "token": ...}` as the first message, with the token /api/auth/login returned.
Without a valid login the socket is closed with 1008. Not a query parameter: uvicorn logs the URL.
Everything the socket does then is scoped to that user, like the HTTP routes.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

import anyio
from anyio.abc import TaskGroup
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app import auth
from app.auth import Viewer
from app.db import new_session
from app.glasses import actions, feeds, frames
from app.glasses.actions import Command
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder

log = logging.getLogger(__name__)

# The WebSocket close code for a refused token (policy violation).
REFUSED = 1008
# What the admin's stream wall calls a socket client that does not name itself: the Android app.
DEFAULT_CLIENT = "glasses"
# How long a client has to send its `auth` message.
AUTH_TIMEOUT_S = 10
# Commands and frames one client may have running at once.
WORKERS = 4


# --- Client to server --------------------------------------------------------


class CommandMessage(Command):
    type: Literal["command"]
    id: int | None = None


class MemoryFrameMessage(BaseModel):
    type: Literal["memory_frame"]
    id: int | None = None
    source: frames.Source = "single"
    stream_id: str = Field(default="", max_length=100)
    seen_at: datetime | None = None
    client: str = Field(default="", max_length=100)  # the app, for the admin's stream wall
    collection: str = Field(default="", max_length=40)  # tag for what is stored; blank: "glasses"


class PingMessage(BaseModel):
    type: Literal["ping"]
    id: int | None = None


class AuthMessage(BaseModel):
    """The first message, from a client that sent no cookie or bearer header: its login token.
    Ignored later."""

    type: Literal["auth"]
    token: str


ClientMessage = Annotated[
    CommandMessage | MemoryFrameMessage | PingMessage | AuthMessage,
    Field(discriminator="type"),
]
client_message: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# --- Server to client --------------------------------------------------------


class ReplyMessage(BaseModel):
    type: Literal["reply"] = "reply"
    id: int | None  # the command's
    reply: GlassesReply


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    id: int | None = None
    status: int  # as the HTTP route would answer: 413, 415, 422, 500…
    detail: str


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"
    id: int | None = None


class MemoryResultMessage(BaseModel):
    type: Literal["memory_result"] = "memory_result"
    id: int | None
    result: frames.FrameReply


ServerMessage = ReplyMessage | MemoryResultMessage | ErrorMessage | PongMessage


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
        self.memory_busy = False

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
            # A phone can keep this socket open longer than a temporary guest exists. Refuse
            # further work after expiry so it cannot recreate memories after the account sweep.
            expires = self.viewer.user.expires_at
            if expires is not None:
                expires = expires if expires.tzinfo else expires.replace(tzinfo=UTC)
                if datetime.now(UTC) >= expires:
                    await self.websocket.close(code=REFUSED, reason="login expired")
                    return
            text = event.get("text")
            if text is None:
                await self.send(
                    ErrorMessage(status=400, detail="a JPEG must follow a frame header")
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
                case CommandMessage():
                    tasks.start_soon(self.run_command, message)
                case MemoryFrameMessage():
                    data = await self.receive_jpeg(message.id)
                    if data is None:
                        continue
                    if self.memory_busy:
                        await self.send(
                            ErrorMessage(
                                id=message.id, status=429, detail="a memory frame is in flight"
                            )
                        )
                    else:
                        self.memory_busy = True
                        tasks.start_soon(self.run_memory_frame, message, data)

    async def receive_jpeg(self, id: int | None) -> bytes | None:
        """The binary frame after a header, or None (and an error sent) when text came instead."""
        event = await self.websocket.receive()
        if event["type"] == "websocket.disconnect":
            raise _Left
        data = event.get("bytes")
        if data is None:
            await self.send(ErrorMessage(id=id, status=400, detail="expected the JPEG next"))
        return data

    async def run_command(self, message: CommandMessage) -> None:
        body = Command(action=message.action, question=message.question)
        reply = await self.work(
            message.id, actions.command, self.viewer, body, self.chat, self.embed
        )
        if reply is not None:
            await self.send(ReplyMessage(id=message.id, reply=reply))

    async def run_memory_frame(self, message: MemoryFrameMessage, data: bytes) -> None:
        try:
            result = await self.work(
                message.id,
                actions.memory_frame,
                self.viewer,
                data,
                self.chat,
                self.embed,
                message.source,
                message.stream_id,
                message.seen_at,
                True,
                feeds.clean_client(message.client, DEFAULT_CLIENT),
                message.collection,
            )
            if result is not None:
                await self.send(MemoryResultMessage(id=message.id, result=result))
        finally:
            self.memory_busy = False

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
        try:
            await channel.receive(tasks)
        except _Left:
            pass
        tasks.cancel_scope.cancel()
