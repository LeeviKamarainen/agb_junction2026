"""The glasses' API: every response is a `GlassesReply` the client draws and speaks.

Clients: the browser harness (frontend /glasses) and, later, glasses-android. The live traffic
(commands, frames, pushed warnings) also runs over one WebSocket per client at /ws
(app/glasses/channel.py); both call the same code in app/glasses/actions.py.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket
from sqlmodel import col, select

from app.auth import Viewer, ViewerDep
from app.db import SessionDep
from app.glasses import actions, channel, feeds, frames, reminders, screens, triggers
from app.glasses.actions import Command, WatchResult
from app.glasses.layout import GlassesReply
from app.llm import Chat, Embedder, UpstreamError, get_chat, get_embedder
from app.models import (
    Goal,
    GoalCreate,
    GoalPublic,
    GoalUpdate,
    Reminder,
    ReminderCreate,
    ReminderPublic,
    Trigger,
    TriggerDraft,
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
    feeds.note(viewer.id, "reminder", body.text, "panel")
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
        "One frame from the camera while the glasses watch for reminders, with every face in it "
        "blurred before the chat model sees it. Not stored. A target in view gives a warning, at "
        "most once a minute per reminder."
    ),
)
async def watch(
    photo: Annotated[UploadFile, File(description="One JPEG frame")],
    session: SessionDep,
    viewer: ViewerDep,
    chat: ChatDep,
    client: Annotated[str, Form(max_length=100)] = "",
):
    data = await photo.read(actions.MAX_FRAME + 1)
    return await actions.watch(session, viewer, data, chat, feeds.clean_client(client, "web"))


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
    store: Annotated[bool, Form()] = True,
    client: Annotated[str, Form(max_length=100)] = "",
    collection: Annotated[str, Form(max_length=40)] = "",
):
    """One frame from the glasses, with every face in it blurred first: embedded, stored as a
    memory in the glasses collection, and checked against the triggers.

    `source` is "stream" for frames of a running stream (twice a second; a frame much like the
    last one stored from the same `stream_id` is not stored again) or "single" for a photo the
    wearer took (always stored). Most answers carry no `message`; when a trigger fires,
    `message` is what to show on the glasses.

    `store=false` only checks the frame against the triggers: the home page sends the demo clips
    this way, since they are stored as demo memories already. `client` names the app sending it
    ("live", "harness", ...) for the admin's stream wall. `collection` tags what is stored (one
    recorded demo, say; a-z, 0-9, _ and -, up to 40), "glasses" when left out.
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
        store,
        feeds.clean_client(client, "web"),
        collection,
    )


async def _own_goal(session: SessionDep, viewer: Viewer, id: int) -> Goal:
    goal = await session.get(Goal, id)
    if goal is None or goal.owner_id != viewer.id:  # the admin's own only, too
        raise HTTPException(404, "No such goal")
    return goal


@router.get("/goals", response_model=list[GoalPublic])
async def list_goals(session: SessionDep, viewer: ViewerDep):
    """What you asked the glasses to watch out for, each with its triggers. Yours only."""
    return await triggers.of(session, viewer.id)


@router.post("/goals", response_model=GoalPublic, status_code=201)
async def create_goal(body: GoalCreate, session: SessionDep, viewer: ViewerDep, chat: ChatDep):
    """Say what the glasses should watch out for ("tell me when my bus comes"): Gemma writes the
    triggers, and the goal is saved with them, enabled. With `triggers` they are used as given.
    503 without a chat model; 502 when it fails or its answer holds no trigger."""
    drafts = body.triggers
    if drafts is None:
        if chat is None:
            raise HTTPException(503, "No chat model configured: set LLM_URL (and LLM_API_KEY)")
        try:
            drafts = await triggers.write(chat, body.text)
        except (UpstreamError, ValueError) as error:
            raise HTTPException(502, str(error)) from error
    goal = await triggers.add(session, viewer.id, body.text, drafts)
    feeds.note(viewer.id, "goal", body.text, "panel")
    return next(g for g in await triggers.of(session, viewer.id) if g.id == goal.id)


@router.put("/goals/{id}", response_model=GoalPublic)
async def update_goal(id: int, body: GoalUpdate, session: SessionDep, viewer: ViewerDep):
    """Switch a goal, and so all its triggers, on or off."""
    goal = await _own_goal(session, viewer, id)
    goal.enabled = body.enabled
    session.add(goal)
    await session.commit()
    return next(g for g in await triggers.of(session, viewer.id) if g.id == id)


@router.delete("/goals/{id}", status_code=204)
async def delete_goal(id: int, session: SessionDep, viewer: ViewerDep):
    """Remove a goal with all its triggers."""
    await triggers.remove(session, await _own_goal(session, viewer, id))


@router.get("/triggers", response_model=list[TriggerPublic])
async def list_triggers(session: SessionDep, viewer: ViewerDep):
    """Every trigger of your goals, with the level measured for it."""
    query = select(Trigger).where(Trigger.owner_id == viewer.id).order_by(col(Trigger.id))
    return (await session.exec(query)).all()


@router.put("/triggers/{id}", response_model=TriggerPublic)
async def update_trigger(id: int, body: TriggerDraft, session: SessionDep, viewer: ViewerDep):
    """Reword or retune one trigger of a goal."""
    trigger = await session.get(Trigger, id)
    if trigger is None or trigger.owner_id != viewer.id:
        raise HTTPException(404, "No such trigger")
    if body.watch_for != trigger.watch_for:
        await frames.forget_trigger_vector(session, id)
    if (body.watch_for, body.sigmas) != (trigger.watch_for, trigger.sigmas):
        trigger.level = None  # measured again on the next stream
    trigger.sqlmodel_update(body.model_dump())
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    return trigger


@router.delete("/triggers/{id}", status_code=204)
async def delete_trigger(id: int, session: SessionDep, viewer: ViewerDep):
    """Drop one trigger from its goal; a goal left with none goes with it."""
    trigger = await session.get(Trigger, id)
    if trigger is None or trigger.owner_id != viewer.id:
        raise HTTPException(404, "No such trigger")
    goal = await session.get(Goal, trigger.goal_id)
    others = await session.exec(
        select(Trigger).where(Trigger.goal_id == trigger.goal_id, Trigger.id != id)
    )
    await frames.forget_trigger_vector(session, id)
    await session.delete(trigger)
    if goal is not None and others.first() is None:
        await session.delete(goal)
    await session.commit()
