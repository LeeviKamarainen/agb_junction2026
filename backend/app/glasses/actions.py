"""What the glasses' commands, memory frames and watched frames do, shared by the HTTP routes
(app/routes/glasses.py) and the WebSocket (channel.py). All are coroutines: the database and the
model calls are awaited, so neither path ties up a thread while a model thinks.

Questions go through the same question loop as /api/ask (app.agent), over the asker's memories.
Without the chat and embedding models the glasses say memory search isn't set up.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Self

import anyio
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app import agent, faces
from app.auth import Viewer
from app.glasses import dejavu, feeds, frames, reminders, screens
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, UpstreamError
from app.search import clean_collection

log = logging.getLogger(__name__)

MAX_FRAME = 10 * 1024 * 1024
JPEG_START = b"\xff\xd8\xff"  # the first bytes of every JPEG file


async def memory_frame(
    session: AsyncSession,
    viewer: Viewer,
    data: bytes,
    chat: Chat | None,
    embed: Embedder | None,
    source: frames.Source = "single",
    stream_id: str = "",
    seen_at: datetime | None = None,
    store: bool = True,
    client: str = "web",
    collection: str = "",
) -> frames.FrameReply:
    """The memory-mode upload, shared by HTTP and the Android socket. Every face in the frame is
    blurred first, so nothing stores, embeds or shows one. The blurred frame also becomes the
    newest of its client's feed on the admin's stream wall (feeds.py), even when it cannot be
    embedded. `collection` tags what is stored (a recorded demo, say); blank is the usual glasses
    collection."""
    try:
        tag = clean_collection(collection) if collection.strip() else frames.COLLECTION
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if len(data) > MAX_FRAME:
        raise HTTPException(413, "frame is larger than 10 MB")
    when = seen_at if seen_at is None or seen_at.tzinfo else seen_at.replace(tzinfo=UTC)
    try:
        data = await anyio.to_thread.run_sync(faces.anonymise, data)
    except ValueError as error:
        raise HTTPException(415, str(error)) from error
    kind: feeds.Kind = "stream" if source == "stream" else "photo"
    key = feeds.record(viewer.id, viewer.user.username, client, kind, stream_id, data, not store)
    if embed is None:
        feeds.frame_result(key, "No embedding model configured: set EMBED_URL", kind="error")
        raise HTTPException(503, "No embedding model configured: set EMBED_URL")
    started = time.perf_counter()
    try:
        reply = await frames.receive(
            session, data, embed, chat, source, stream_id, when, viewer.id, store, tag
        )
    except (ValueError, UpstreamError) as error:
        feeds.frame_result(key, str(error), kind="error")
        status = 415 if isinstance(error, ValueError) else 502
        raise HTTPException(status, str(error)) from error
    ms = round((time.perf_counter() - started) * 1000)
    feeds.frame_result(key, _frame_summary(reply), reply.model_dump(mode="json"), ms)
    fired = next((match for match in reply.matches if match.fired), None)
    if reply.message is not None:
        title = reply.message.title or (fired.name if fired else "Trigger")
        feeds.note(viewer.id, "trigger", f"{title}: {reply.message.text}", "stream")
    elif fired is not None:
        feeds.note(
            viewer.id, "trigger", f"{fired.name} fired: {reply.note or 'no message'}", "stream"
        )
    return reply


def _frame_summary(reply: frames.FrameReply) -> str:
    """One line on what became of a frame, for the stream wall's full page."""
    parts = [f"stored as memory #{reply.memory_id}" if reply.stored else "not stored"]
    fired = next((m for m in reply.matches if m.fired), None)
    if fired is not None:
        parts.append(f"fired {fired.name}")
    elif reply.matches:
        best = reply.matches[0]
        if best.score is not None and best.level is not None:
            parts.append(f"closest {best.name}: score {best.score:.3f} / level {best.level:.3f}")
        elif best.score is not None:
            parts.append(f"closest {best.name}: score {best.score:.3f}, level being measured")
        else:
            parts.append(f"closest {best.name}: distance {best.distance:.3f}")
    else:
        parts.append("no triggers")
    if reply.message is not None:
        parts.append(f"says “{reply.message.text}”")
    if reply.note:
        parts.append(reply.note)
    return " · ".join(parts)


# The wearer's gesture: the Neural Band's double index-finger tap ("double_tap", or "ask") opens
# the microphone for a question.
ACTIONS = {"ask", "double_tap"}


class Command(BaseModel):
    """Exactly one of: the action of a button or gesture (`action`), or what the wearer said
    (`question`). What they said goes to the memory as it is, unless it starts "remind me…" or
    "warn me…", which makes it a reminder (reminders.py).
    """

    action: str | None = Field(default=None, max_length=100)
    question: str | None = Field(default=None, max_length=500)
    # The home page plays a demo clip instead of a camera: which, and how many seconds into it.
    # Déjà vu then asks about the clip's frame there (dejavu.DemoView).
    demo_clip_id: int | None = None
    demo_at_s: float | None = Field(default=None, ge=0)

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
        return screens.listen()
    question = (body.question or "").strip()
    if not question:
        return screens.home()
    if reminders.is_reminder(question):
        feeds.note(viewer.id, "reminder", question, "glasses")
        return await _remind(session, question, chat, viewer)
    feeds.note(viewer.id, "question", question, "glasses")
    if dejavu.is_recall(question):
        view = (
            dejavu.DemoView(body.demo_clip_id, body.demo_at_s or 0.0)
            if body.demo_clip_id is not None
            else None
        )
        return await _recall(session, question, chat, viewer, view)
    return await _ask(session, question, chat, embed, viewer)


async def _remind(
    session: AsyncSession, text: str, chat: Chat | None, viewer: Viewer
) -> GlassesReply:
    reminder = await reminders.add(session, text, chat, viewer.id)
    if reminder is None:
        return screens.reminder_unclear(text)
    return screens.reminder_added(reminder.target, reminder.text)


async def _recall(
    session: AsyncSession,
    question: str,
    chat: Chat | None,
    viewer: Viewer,
    view: dejavu.DemoView | None = None,
) -> GlassesReply:
    """Déjà vu (dejavu.py): where the wearer has seen what is in front of them."""
    if chat is None:
        return screens.memory_problem(question, "Memory search isn’t set up yet.")
    try:
        found = await dejavu.recall(session, chat, viewer, view)
    except (UpstreamError, ValueError) as error:
        log.warning("Déjà vu failed: %s", error)
        return screens.memory_problem(question, "I can’t reach the memory right now.")
    if found is None:
        return screens.recall_demo_not_ready() if view else screens.recall_needs_still()
    return screens.recall(found)


async def _ask(
    session: AsyncSession, question: str, chat: Chat | None, embed: Embedder | None, viewer: Viewer
) -> GlassesReply:
    if chat is None or embed is None:
        return screens.memory_problem(question, "Memory search isn’t set up yet.")
    try:
        answer = await agent.answer(
            session, question, chat, embed, viewer.owners, owner_id=viewer.id
        )
    except UpstreamError as error:
        log.warning("Memory question failed: %s", error)
        return screens.memory_problem(question, "I can’t reach the memory right now.")
    said = answer.answer or "I couldn’t find that in your memories."
    return screens.memory_answer(question, said)


def check_jpeg(data: bytes, what: str) -> None:
    """413 or 415 for a still the glasses would not have sent."""
    if len(data) > MAX_FRAME:
        raise HTTPException(413, f"{what} is larger than 10 MB")
    if not data.startswith(JPEG_START):
        raise HTTPException(415, f"{what} must be a JPEG")


async def watch(
    session: AsyncSession, viewer: Viewer, data: bytes, chat: Chat | None, client: str = "web"
) -> WatchResult:
    """Checks one frame for every reminder's object; never raises for a model's failure. Every
    face in the frame is blurred before the chat model sees it, or the stream wall shows it."""
    check_jpeg(data, "frame")
    try:
        data = await anyio.to_thread.run_sync(faces.anonymise, data)
    except ValueError as error:
        raise HTTPException(415, str(error)) from error
    key = feeds.record(viewer.id, viewer.user.username, client, "watch", "", data)
    started = time.perf_counter()
    result = await _check(session, viewer, data, chat)
    seen = ", ".join(result.seen) or "nothing watched for"
    text = f"{'warning · ' if result.warning else ''}{seen} in view · {result.note}"
    ms = round((time.perf_counter() - started) * 1000)
    feeds.frame_result(key, text, result.model_dump(mode="json", exclude={"warning"}), ms)
    return result


async def _check(
    session: AsyncSession, viewer: Viewer, data: bytes, chat: Chat | None
) -> WatchResult:
    """Which of the viewer's reminders' objects the (blurred) frame shows."""
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
        feeds.note(viewer.id, "warning", f"{first.target}: {first.text}", "watch")
    await session.commit()
    return WatchResult(
        seen=[r.target for r in seen],
        warning=screens.warning(first.target, first.text) if first else None,
        note=note,
    )
