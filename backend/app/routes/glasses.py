"""The glasses' API: every response is a `GlassesReply` the client draws and speaks.

Clients: the browser harness (frontend /glasses) and, later, glasses-android. The live traffic
(commands, frames, pushed warnings) also runs over one WebSocket per client at /ws
(app/glasses/channel.py); both call the same code in app/glasses/actions.py.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket
from sqlmodel import col, select

from app.auth import ViewerDep
from app.db import SessionDep
from app.glasses import actions, channel, frames, reminders, screens
from app.glasses.actions import Command, WatchResult
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, get_chat, get_embedder
from app.models import (
    Reminder,
    ReminderCreate,
    ReminderPublic,
    Trigger,
    TriggerCreate,
    TriggerPublic,
)

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
async def socket(websocket: WebSocket, chat: ChatDep, embed: EmbedderDep):
    """The live channel; its protocol is in app/glasses/channel.py."""
    await channel.serve(websocket, chat, embed)


@router.get("/home", response_model=GlassesReply)
async def home():
    return screens.home()


@router.post("/command", response_model=GlassesReply)
async def command(
    body: Command, session: SessionDep, viewer: ViewerDep, chat: ChatDep, embed: EmbedderDep
):
    return await actions.command(session, viewer, body, chat, embed)


@router.get("/reminders", response_model=list[ReminderPublic])
async def list_reminders(session: SessionDep, viewer: ViewerDep):
    """Your reminders; the admin's list holds everyone's."""
    return await reminders.active(session, None if viewer.is_admin else viewer.id)


@router.post("/reminders", response_model=ReminderPublic, status_code=201)
async def add_reminder(body: ReminderCreate, session: SessionDep, viewer: ViewerDep, chat: ChatDep):
    """What the wearer would say, e.g. "remind me not to trip over the stool"; 422 when no
    object can be found in it."""
    reminder = await reminders.add(session, body.text, chat, viewer.id)
    if reminder is None:
        raise HTTPException(422, "no object to watch for in that text")
    return reminder


@router.delete("/reminders/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: int, session: SessionDep, viewer: ViewerDep):
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None or not viewer.can_change(reminder.owner_id):
        raise HTTPException(404, "no such reminder")
    await session.delete(reminder)
    await session.commit()


@router.post(
    "/reminders/{reminder_id}/trigger",
    response_model=GlassesReply,
    description="The warning as if the object were in view, for testing without a vision model.",
)
async def trigger_reminder(reminder_id: int, session: SessionDep, viewer: ViewerDep):
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None or not viewer.can_change(reminder.owner_id):
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
async def watch(
    photo: Annotated[UploadFile, File(description="One JPEG frame")],
    session: SessionDep,
    viewer: ViewerDep,
    chat: ChatDep,
):
    return await actions.watch(session, viewer, await photo.read(actions.MAX_FRAME + 1), chat)


@router.post("/frames", response_model=frames.FrameReply)
async def upload_frame(
    frame: Annotated[UploadFile, File(description="One image, usually a JPEG")],
    session: SessionDep,
    viewer: ViewerDep,
    chat: ChatDep,
    embed: EmbedderDep,
    source: Annotated[frames.Source, Form()] = "single",
    stream_id: Annotated[str, Form(max_length=100)] = "",
    seen_at: Annotated[datetime | None, Form()] = None,
):
    """One frame from the glasses: embedded, stored as a memory in the glasses collection, and
    checked against the triggers.

    `source` is "stream" for frames of a running stream (twice a second; a frame much like the
    last one stored from the same `stream_id` is not stored again) or "single" for a photo the
    wearer took (always stored). Most answers carry no `message`; when a trigger fires,
    `message` is what to show on the glasses.
    """
    return await actions.memory_frame(
        session,
        viewer,
        await frame.read(actions.MAX_FRAME + 1),
        chat,
        embed,
        source,
        stream_id,
        seen_at,
    )


@router.get("/triggers", response_model=list[TriggerPublic])
async def list_triggers(session: SessionDep, viewer: ViewerDep):
    query = select(Trigger).order_by(col(Trigger.id))
    if viewer.owners is not None:
        query = query.where(col(Trigger.owner_id).in_(viewer.owners))
    return (await session.exec(query)).all()


@router.post("/triggers", response_model=TriggerPublic, status_code=201)
async def create_trigger(body: TriggerCreate, session: SessionDep, viewer: ViewerDep):
    trigger = Trigger.model_validate(body, update={"owner_id": viewer.id})
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    return trigger


@router.put("/triggers/{id}", response_model=TriggerPublic)
async def update_trigger(id: int, body: TriggerCreate, session: SessionDep, viewer: ViewerDep):
    trigger = await session.get(Trigger, id)
    if trigger is None or not viewer.can_change(trigger.owner_id):
        raise HTTPException(404, "No such trigger")
    if body.watch_for != trigger.watch_for:
        await frames.forget_trigger_vector(session, id)
    trigger.sqlmodel_update(body.model_dump())
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    return trigger


@router.delete("/triggers/{id}", status_code=204)
async def delete_trigger(id: int, session: SessionDep, viewer: ViewerDep):
    trigger = await session.get(Trigger, id)
    if trigger is None or not viewer.can_change(trigger.owner_id):
        raise HTTPException(404, "No such trigger")
    await frames.forget_trigger_vector(session, id)
    await session.delete(trigger)
    await session.commit()
