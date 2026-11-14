"""What the glasses' commands, photos and watched frames do, shared by the HTTP routes
(app/routes/glasses.py) and the WebSocket (channel.py). All are coroutines: the database and the
model calls are awaited, so neither path ties up a thread while a model thinks.

Memory is the backend's own (app/routes/memories.py): photos become image memories, questions go
through the same question loop as /api/ask. Without the chat and embedding models the glasses
still take and show photos, and say memory search isn't set up.
"""

import logging
from datetime import UTC, datetime
from typing import Self

import anyio
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import Viewer
from app.glasses import frames, memory, photos, reminders, screens
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, UpstreamError

log = logging.getLogger(__name__)

MAX_FRAME = 10 * 1024 * 1024


async def memory_frame(
    session: AsyncSession,
    viewer: Viewer,
    data: bytes,
    chat: Chat | None,
    embed: Embedder | None,
    source: frames.Source = "single",
    stream_id: str = "",
    seen_at: datetime | None = None,
) -> frames.FrameReply:
    """The memory-mode upload, shared by HTTP and the Android socket."""
    if embed is None:
        raise HTTPException(503, "No embedding model configured: set EMBED_URL")
    if len(data) > MAX_FRAME:
        raise HTTPException(413, "frame is larger than 10 MB")
    when = seen_at if seen_at is None or seen_at.tzinfo else seen_at.replace(tzinfo=UTC)
    try:
        return await frames.receive(
            session, data, embed, chat, source, stream_id, when, viewer.id, viewer.owners
        )
    except ValueError as error:
        raise HTTPException(415, str(error)) from error
    except UpstreamError as error:
        raise HTTPException(502, str(error)) from error


# The wearer's buttons and gestures: the glasses' capture button takes a photo, "gallery" shows the
# newest ones, and the Neural Band's double index-finger tap ("double_tap", or "ask") opens the
# microphone for a question.
ACTIONS = {"take_photo", "gallery", "ask", "double_tap"}


class Command(BaseModel):
    """Exactly one of: the action of a button or gesture (`action`), or what the wearer said
    (`question`). What they said goes to the memory as it is, unless it starts "remind me…" or
    "warn me…", which makes it a reminder (reminders.py).
    """

    action: str | None = Field(default=None, max_length=100)
    question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def one_of(self) -> Self:
        if (self.action is None) == (self.question is None):
            raise ValueError("send exactly one of action or question")
        return self


class WatchResult(BaseModel):
    """What one watched frame showed. `warning` is the screen to draw, when there is one."""

    seen: list[str]  # targets in view, including ones warned about less than a minute ago
    warning: GlassesReply | None = None
    # How the frame was checked ("chat model"), or why it wasn't.
    note: str


async def command(
    session: AsyncSession, viewer: Viewer, body: Command, chat: Chat | None, embed: Embedder | None
) -> GlassesReply:
    """What `viewer` said or pressed; everything it reaches is scoped to them (app/auth.py)."""
    if body.action is not None:
        if body.action not in ACTIONS:
            raise HTTPException(422, f"unknown action {body.action!r}")
        if body.action == "take_photo":
            return screens.capture()
        if body.action == "gallery":
            return screens.gallery(viewer.owners)
        return screens.listen()
    question = (body.question or "").strip()
    if not question:
        return screens.home()
    if reminders.is_reminder(question):
        return await _remind(session, question, chat, viewer)
    return await _ask(session, question, chat, embed, viewer)


async def _remind(
    session: AsyncSession, text: str, chat: Chat | None, viewer: Viewer
) -> GlassesReply:
    reminder = await reminders.add(session, text, chat, viewer.id)
    if reminder is None:
        return screens.reminder_unclear(text)
    return screens.reminder_added(reminder.target, reminder.text)


async def _ask(
    session: AsyncSession, question: str, chat: Chat | None, embed: Embedder | None, viewer: Viewer
) -> GlassesReply:
    if chat is None or embed is None:
        return screens.memory_problem(question, "Memory search isn’t set up yet.")
    try:
        answer = await memory.ask(session, question, chat, embed, viewer.owners)
    except UpstreamError as error:
        log.warning("Memory question failed: %s", error)
        return screens.memory_problem(question, "I can’t reach the memory right now.")
    said = answer.answer or "I couldn’t find that in your memories."
    return screens.memory_answer(question, said)


def check_jpeg(data: bytes, what: str) -> None:
    """413 or 415 for a still the glasses would not have sent."""
    if len(data) > photos.MAX_BYTES:
        raise HTTPException(413, f"{what} is larger than 10 MB")
    if not photos.is_jpeg(data):
        raise HTTPException(415, f"{what} must be a JPEG")


async def photo(
    session: AsyncSession, viewer: Viewer, data: bytes, chat: Chat | None, embed: Embedder | None
) -> GlassesReply:
    """Saves the still, describes it with the chat model and stores it as an image memory."""
    check_jpeg(data, "photo")
    name = await anyio.to_thread.run_sync(photos.save, data, viewer.id)

    description, problems, stored = "", [], False
    if chat is not None:
        try:
            description = await memory.describe(chat, data)
        except UpstreamError as error:
            log.warning("Describing a photo failed: %s", error)
    if not description and chat is not None:
        problems.append("couldn’t describe it")
    if embed is not None:
        try:
            await memory.remember(session, embed, data, description, viewer.id)
            stored = True
        except UpstreamError as error:
            log.warning("Storing a photo as a memory failed: %s", error)
            problems.append("couldn’t save it to memory")

    note = "; ".join(problems) or ("saved to memory" if stored else None)
    if description:
        return screens.photo_described(name, description, note)
    return screens.photo_saved(name, note)


async def watch(
    session: AsyncSession, viewer: Viewer, data: bytes, chat: Chat | None
) -> WatchResult:
    """Checks one frame for every reminder's object; never raises for a model's failure."""
    check_jpeg(data, "frame")
    watched = await reminders.active(session, viewer.id)  # your own, even for the admin
    try:
        found, note = await reminders.detect(data, [r.target for r in watched], chat)
    except reminders.Unavailable as error:
        return WatchResult(seen=[], note=str(error))
    except Exception as error:  # a frame a second: one bad answer must not break the loop
        log.exception("Checking a watched frame failed")
        return WatchResult(seen=[], note=f"check failed: {type(error).__name__}: {error}")
    seen = [watched[i] for i in found]
    now = datetime.now(UTC)
    due = [r for r in seen if reminders.due(r, now)]
    first = due[0] if due else None
    if first is not None:
        first.last_warned_at = now
        session.add(first)
    await session.commit()
    return WatchResult(
        seen=[r.target for r in seen],
        warning=screens.warning(first.target, first.text) if first else None,
        note=note,
    )
