"""The glasses' API: every response is a `GlassesReply` the client draws and speaks.

Clients: the browser harness (frontend /glasses) and, later, glasses-android. Memory is the
backend's own (app/routes/memories.py): photos become image memories, questions go through the
same question loop as /api/ask. When those models are not set or not reachable, the glasses fall
back to the glass-memory API (MEMORY_API_URL, remote_memory.py), which runs Gemma itself. With
neither, they still take and show photos and say memory search isn't set up.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from app.db import SessionDep
from app.glasses import memory, photos, reminders, remote_memory, screens
from app.glasses.intents import BUTTON_ACTIONS, interpret
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, UpstreamError, get_chat, get_embedder
from app.models import Reminder, ReminderCreate, ReminderPublic

router = APIRouter(prefix="/glasses", tags=["glasses"])
log = logging.getLogger(__name__)


def optional_chat() -> Chat | None:
    """The chat model, or None when LLM_URL is not set."""
    try:
        return get_chat()
    except HTTPException:
        return None


def optional_embedder() -> Embedder | None:
    """The embedding model, or None when EMBED_URL is not set."""
    try:
        return get_embedder()
    except HTTPException:
        return None


ChatDep = Annotated[Chat | None, Depends(optional_chat)]
EmbedderDep = Annotated[Embedder | None, Depends(optional_embedder)]


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


@router.get("/home", response_model=GlassesReply)
def home():
    return screens.home()


@router.post("/command", response_model=GlassesReply)
def command(body: Command, session: SessionDep, chat: ChatDep, embed: EmbedderDep):
    if body.question is not None:
        question = body.question.strip()
        if not question:
            return screens.home()
        if reminders.is_reminder(question):
            return _remind(session, question, chat)
        return _ask(session, question, chat, embed)
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
        return screens.gallery()
    if name == "ask_memory":
        return _ask(session, text, chat, embed)
    if name == "remind":
        return _remind(session, text, chat)
    return screens.unknown(text)


def _remind(session, text: str, chat: Chat | None) -> GlassesReply:
    reminder = reminders.add(session, text, chat)
    if reminder is None:
        return screens.reminder_unclear(text)
    return screens.reminder_added(reminder.target, reminder.text)


def _ask(session, question: str, chat: Chat | None, embed: Embedder | None) -> GlassesReply:
    if chat is not None and embed is not None:
        try:
            answer = memory.ask(session, question, chat, embed)
            said = answer.answer or "I couldn’t find that in your memories."
            return screens.memory_answer(question, said)
        except UpstreamError as error:
            log.warning("Memory question failed, falling back to the glass-memory API: %s", error)
            if not remote_memory.configured():
                return screens.memory_problem(question, "I can’t reach the memory right now.")
    if remote_memory.configured():
        try:
            said, _stills = remote_memory.ask(question)
        except remote_memory.Unavailable:
            return screens.memory_problem(question, "I can’t reach the memory right now.")
        return screens.memory_answer(question, said)
    return screens.memory_problem(question, "Memory search isn’t set up yet.")


@router.post(
    "/photos",
    response_model=GlassesReply,
    status_code=201,
    description=(
        "Saves the still. With a chat model the glasses describe it; with an embedding model it "
        "also becomes an image memory, captioned with that description."
    ),
)
def upload_photo(
    photo: Annotated[UploadFile, File(description="One JPEG still")],
    session: SessionDep,
    chat: ChatDep,
    embed: EmbedderDep,
):
    data = photo.file.read(photos.MAX_BYTES + 1)
    if len(data) > photos.MAX_BYTES:
        raise HTTPException(413, "photo is larger than 10 MB")
    if not photos.is_jpeg(data):
        raise HTTPException(415, "photo must be a JPEG")
    name = photos.save(data)

    description, problems, stored = "", [], False
    if chat is not None:
        try:
            description = memory.describe(chat, data)
        except UpstreamError as error:
            log.warning("Describing a photo failed: %s", error)
    if not description and remote_memory.configured():
        try:
            description = remote_memory.describe(data, name)
        except remote_memory.Unavailable:
            pass
    if not description and (chat is not None or remote_memory.configured()):
        problems.append("couldn’t describe it")
    if embed is not None:
        try:
            memory.remember(session, embed, data, description)
            stored = True
        except UpstreamError as error:
            log.warning("Storing a photo as a memory failed: %s", error)
            problems.append("couldn’t save it to memory")

    note = "; ".join(problems) or ("saved to memory" if stored else None)
    if description:
        return screens.photo_described(name, description, note)
    return screens.photo_saved(name, note)


@router.get(
    "/photos/{name}",
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}}}, 404: {}},
)
def get_photo(name: str):
    file = photos.path(name)
    if file is None:
        raise HTTPException(404, "no such photo")
    return FileResponse(file, media_type="image/jpeg")


@router.get(
    "/memory/{batch}/keyframes/{name}",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 404: {}, 502: {}},
)
def get_memory_keyframe(batch: str, name: str):
    """A still from the glass-memory API, fetched with the backend's token."""
    try:
        data = remote_memory.keyframe(batch, name)
    except remote_memory.Unavailable:
        raise HTTPException(502, "the memory API is unreachable") from None
    if data is None:
        raise HTTPException(404, "no such keyframe")
    return Response(data, media_type="image/jpeg")


@router.get("/reminders", response_model=list[ReminderPublic])
def list_reminders(session: SessionDep):
    return reminders.active(session)


@router.post("/reminders", response_model=ReminderPublic, status_code=201)
def add_reminder(body: ReminderCreate, session: SessionDep, chat: ChatDep):
    """What the wearer would say, e.g. "remind me not to trip over the stool"; 422 when no
    object can be found in it."""
    reminder = reminders.add(session, body.text, chat)
    if reminder is None:
        raise HTTPException(422, "no object to watch for in that text")
    return reminder


@router.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder(reminder_id: int, session: SessionDep):
    reminder = session.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(404, "no such reminder")
    session.delete(reminder)
    session.commit()


@router.post(
    "/reminders/{reminder_id}/trigger",
    response_model=GlassesReply,
    description="The warning as if the object were in view, for testing without a vision model.",
)
def trigger_reminder(reminder_id: int, session: SessionDep):
    reminder = session.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(404, "no such reminder")
    return screens.warning(reminder.target, reminder.text)


class WatchResult(BaseModel):
    """What one watched frame showed. `warning` is the screen to draw, when there is one."""

    seen: list[str]  # targets in view, including ones warned about less than a minute ago
    warning: GlassesReply | None = None
    # How the frame was checked ("chat model", the memory API's description), or why it wasn't.
    note: str


@router.post(
    "/watch",
    response_model=WatchResult,
    description=(
        "One frame from the camera while the glasses watch for reminders. Not stored. A target in "
        "view gives a warning, at most once a minute per reminder."
    ),
)
def watch(
    photo: Annotated[UploadFile, File(description="One JPEG frame")],
    session: SessionDep,
    chat: ChatDep,
):
    data = photo.file.read(photos.MAX_BYTES + 1)
    if len(data) > photos.MAX_BYTES:
        raise HTTPException(413, "frame is larger than 10 MB")
    if not photos.is_jpeg(data):
        raise HTTPException(415, "frame must be a JPEG")
    watched = reminders.active(session)
    try:
        found, note = reminders.detect(data, [r.target for r in watched], chat)
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
    session.commit()
    first = due[0] if due else None
    return WatchResult(
        seen=[r.target for r in seen],
        warning=screens.warning(first.target, first.text) if first else None,
        note=note,
    )
