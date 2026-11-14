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

Commands and photos run concurrently, frames one at a time beside them; every model call is
blocking httpx, so each runs in a worker thread with its own database session and one slow client
cannot stall the others.

Commands and photos from one client run at most WORKERS at a time; on disconnect the socket
closes at once and the threads still running finish on their own.

Auth: with GLASSES_TOKEN set, connect with `Authorization: Bearer <token>` (native clients) or
send `{"type": "auth", "token": ...}` as the first message (browsers cannot set headers), else
the socket is closed with 1008. Not a query parameter: uvicorn logs the URL. Without the setting
the socket is open, as the HTTP routes are.
"""

import logging
import secrets
import time
from collections.abc import Callable
from typing import Annotated, Literal

import anyio
from anyio.abc import TaskGroup
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlmodel import Session

from app.db import get_engine
from app.glasses import actions
from app.glasses.actions import Command, WatchResult
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder
from app.settings import get_settings

log = logging.getLogger(__name__)

# The WebSocket close code for a refused token (policy violation).
REFUSED = 1008
# How long a client has to send its `auth` message.
AUTH_TIMEOUT_S = 10
# Worker threads one client may hold for commands, photos and frames.
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
    """The first message, when GLASSES_TOKEN is set and no bearer header came; ignored later."""

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


def _matches(given: str, expected: str) -> bool:
    return secrets.compare_digest(given.encode(), expected.encode())


async def authenticated(websocket: WebSocket) -> bool:
    """The bearer header, or else the first message, carries GLASSES_TOKEN (when it is set)."""
    expected = get_settings().glasses_token
    if not expected:
        return True
    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return _matches(header[7:].strip(), expected)
    with anyio.move_on_after(AUTH_TIMEOUT_S):
        event = await websocket.receive()
        text = event.get("text")
        if text is None:
            return False
        try:
            message = client_message.validate_json(text)
        except ValidationError:
            return False
        return isinstance(message, AuthMessage) and _matches(message.token, expected)
    return False


def _in_session[T](work: Callable[..., T], *args) -> T:
    with Session(get_engine()) as session:
        return work(session, *args)


class _Left(Exception):
    """The client disconnected."""


class Channel:
    def __init__(self, websocket: WebSocket, chat: Chat | None, embed: Embedder | None):
        self.websocket = websocket
        self.chat = chat
        self.embed = embed
        self.send_lock = anyio.Lock()
        self.limiter = anyio.CapacityLimiter(WORKERS)
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
            result = await self.work(id, actions.watch, data, self.chat)
            if result is None:
                continue
            ms = round((time.perf_counter() - started) * 1000)
            await self.send(WatchResultMessage(id=id, result=result, dropped=dropped, ms=ms))
            if result.warning is not None and self.watching:
                await self.send(ReplyMessage(id=None, reply=result.warning))

    async def run_command(self, message: CommandMessage) -> None:
        body = Command(text=message.text, action=message.action, question=message.question)
        reply = await self.work(message.id, actions.command, body, self.chat, self.embed)
        if reply is not None:
            await self.send(ReplyMessage(id=message.id, reply=reply))

    async def run_photo(self, id: int | None, data: bytes) -> None:
        reply = await self.work(id, actions.photo, data, self.chat, self.embed)
        if reply is not None:
            await self.send(ReplyMessage(id=id, reply=reply))

    async def work[T](self, id: int | None, work: Callable[..., T], *args) -> T | None:
        """Runs `work(session, *args)` in a worker thread with its own session; a failure becomes
        an `error` message, not a closed socket."""
        try:
            return await anyio.to_thread.run_sync(
                _in_session, work, *args, limiter=self.limiter, abandon_on_cancel=True
            )
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
        ok = await authenticated(websocket)
    except WebSocketDisconnect:
        return
    if not ok:
        try:
            await websocket.close(code=REFUSED, reason="wrong or missing token")
        except (WebSocketDisconnect, RuntimeError):
            pass  # it left first
        return
    channel = Channel(websocket, chat, embed)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(channel.check_frames)
        try:
            await channel.receive(tasks)
        except _Left:
            pass
        tasks.cancel_scope.cancel()
