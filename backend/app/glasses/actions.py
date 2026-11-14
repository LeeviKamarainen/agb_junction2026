"""What the glasses' commands, photos and watched frames do, shared by the HTTP routes
(app/routes/glasses.py) and the WebSocket (channel.py). All are coroutines: the database and the
model calls are awaited, so neither path ties up a thread while a model thinks.

Memory is the backend's own (app/routes/memories.py): photos become image memories, questions go
through the same question loop as /api/ask. When those models are not set or not reachable, the
glasses fall back to the glass-memory API (MEMORY_API_URL, remote_memory.py), which runs Gemma
itself. With neither, they still take and show photos and say memory search isn't set up.
"""

import logging
from datetime import UTC, datetime
from typing import Self

import anyio
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import Viewer
from app.glasses import memory, photos, reminders, remote_memory, screens
from app.glasses.intents import BUTTON_ACTIONS, interpret
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, UpstreamError

log = logging.getLogger(__name__)


class Command(BaseModel):
    """Exactly one of: what the wearer said (`text`), the action of the button they pressed, or
    the question they asked after pressing Ask (`question`, always a question about the photos).
    """

    text: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=100)
    question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def one_of(self) -> Self:
        if sum(field is not None for field in (self.text, self.action, self.question)) != 1:
            raise ValueError("send exactly one of text, action or question")
        return self


class WatchResult(BaseModel):
    """What one watched frame showed. `warning` is the screen to draw, when there is one."""

    seen: list[str]  # targets in view, including ones warned about less than a minute ago
    warning: GlassesReply | None = None
    # How the frame was checked ("chat model", the memory API's description), or why it wasn't.
    note: str


async def command(
    session: AsyncSession, viewer: Viewer, body: Command, chat: Chat | None, embed: Embedder | None
) -> GlassesReply:
    """What `viewer` said or pressed; everything it reaches is scoped to them (app/auth.py)."""
    if body.question is not None:
        question = body.question.strip()
        if not question:
            return screens.home()
        if reminders.is_reminder(question):
            return await _remind(session, question, chat, viewer)
        return await _ask(session, question, chat, embed, viewer)
    if body.action is not None:
        if body.action not in BUTTON_ACTIONS:
            raise HTTPException(422, f"unknown action {body.action!r}")
        name, text = ("ask" if body.action == "double_tap" else body.action), ""
    else:
        intent = interpret(body.text or "")
        name, text = intent.name, intent.text

    if name == "home":
        return screens.home()
    if name == "help":
        return screens.help_()
    if name == "take_photo":
        return screens.capture()
    if name == "ask":
        return screens.listen()
    if name == "gallery":
        return screens.gallery(viewer.owners)
    if name == "ask_memory":
        return await _ask(session, text, chat, embed, viewer)
    if name == "remind":
        return await _remind(session, text, chat, viewer)
    return screens.unknown(text)


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
    if chat is not None and embed is not None:
        try:
            answer = await memory.ask(session, question, chat, embed, viewer.owners)
            said = answer.answer or "I couldn’t find that in your memories."
            return screens.memory_answer(question, said)
        except UpstreamError as error:
            log.warning("Memory question failed, falling back to the glass-memory API: %s", error)
            if not remote_memory.configured():
                return screens.memory_problem(question, "I can’t reach the memory right now.")
    if remote_memory.configured():
        try:
            said, _stills = await remote_memory.ask(question, viewer.id, viewer.owners)
        except remote_memory.Unavailable:
            return screens.memory_problem(question, "I can’t reach the memory right now.")
        return screens.memory_answer(question, said)
    return screens.memory_problem(question, "Memory search isn’t set up yet.")


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
    if not description and remote_memory.configured():
        try:
            description = await remote_memory.describe(data, name)
        except remote_memory.Unavailable:
            pass
    if not description and (chat is not None or remote_memory.configured()):
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
    for reminder in due:
        reminder.last_warned_at = now
        session.add(reminder)
    await session.commit()
    first = due[0] if due else None
    return WatchResult(
        seen=[r.target for r in seen],
        warning=screens.warning(first.target, first.text) if first else None,
        note=note,
    )
