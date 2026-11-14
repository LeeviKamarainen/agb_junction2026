"""The glasses' API: every response is a `GlassesReply` the client draws and speaks.

Clients: the browser harness (frontend /glasses) and, later, glasses-android. The live traffic
(commands, photos, watched frames, pushed warnings) also runs over one WebSocket per client at
/ws (app/glasses/channel.py); both call the same code in app/glasses/actions.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, Response

from app.db import SessionDep
from app.glasses import actions, channel, photos, reminders, remote_memory, screens
from app.glasses.actions import Command, WatchResult
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, get_chat, get_embedder
from app.models import Reminder, ReminderCreate, ReminderPublic

router = APIRouter(prefix="/glasses", tags=["glasses"])


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


@router.websocket("/ws")
async def socket(websocket: WebSocket, chat: ChatDep, embed: EmbedderDep, token: str | None = None):
    """The live channel; its protocol is in app/glasses/channel.py."""
    await channel.serve(websocket, chat, embed, token)


@router.get("/home", response_model=GlassesReply)
def home():
    return screens.home()


@router.post("/command", response_model=GlassesReply)
def command(body: Command, session: SessionDep, chat: ChatDep, embed: EmbedderDep):
    return actions.command(session, body, chat, embed)


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
    return actions.photo(session, photo.file.read(photos.MAX_BYTES + 1), chat, embed)


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
    return actions.watch(session, photo.file.read(photos.MAX_BYTES + 1), chat)
