"""The admin's stream wall: the frames clients are sending right now (app/glasses/feeds.py).

A stream's full page also has a chat: the admin asks Gemma about the stream, as its user would,
over that user's memories and with the newest frame attached, and changes what the user's
glasses watch out for (their goals and triggers).
"""

import base64
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse

from app import agent
from app.auth import DEMO_OWNER, Viewer, ViewerDep
from app.db import SessionDep, new_session
from app.glasses import feeds, triggers
from app.llm import Chat, Embedder, UpstreamError, get_chat, get_embedder
from app.models import Goal, GoalPublic

router = APIRouter(prefix="/admin/streams", tags=["admin"])
ChatDep = Annotated[Chat, Depends(get_chat)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


def _admin(viewer: Viewer) -> None:
    if not viewer.is_admin:
        raise HTTPException(403, "Only the admin sees the streams")


@router.get("", response_model=list[feeds.FeedPublic])
async def list_streams(viewer: ViewerDep):
    """Every client that sent a frame in the last minute, newest first: who, which app, what
    kind of frames and how fast. Faces are blurred in every frame, as everywhere."""
    _admin(viewer)
    return feeds.active()


@router.get("/{feed_id}", response_model=feeds.FeedDetail)
async def stream_detail(feed_id: str, viewer: ViewerDep):
    """One feed with its full log, newest first: what became of each frame (stored or why not,
    every trigger's score against its level, what fired), the user's questions, the chat
    model's searches and answers, goals and errors, each with its JSON
    `detail`. 404 once the feed has gone quiet."""
    _admin(viewer)
    found = feeds.detail(feed_id)
    if found is None:
        raise HTTPException(404, "No such stream, or it stopped")
    return found


@router.get(
    "/{feed_id}/frame",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def latest_frame(feed_id: str, viewer: ViewerDep) -> Response:
    """The newest frame of a feed, as a JPEG. 404 once the feed has gone quiet."""
    _admin(viewer)
    jpeg = feeds.latest(feed_id)
    if jpeg is None:
        raise HTTPException(404, "No such stream, or it stopped")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _owner(feed_id: str) -> int:
    owner_id = feeds.owner_of(feed_id)
    if owner_id is None:
        raise HTTPException(404, "No such stream")
    return owner_id


@router.post(
    "/{feed_id}/ask/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
async def ask_about_stream(
    feed_id: str, body: agent.AskRequest, viewer: ViewerDep, chat: ChatDep, embed: EmbedderDep
):
    """Ask Gemma about a stream as its user would, as /api/ask/stream does: it searches that
    user's memories (and the demo's), sees the stream's newest frame while the stream runs, and
    can start or stop watching for something on the user's glasses. Newline-delimited JSON, one
    Step per line. Works for a while after the stream stopped, without the frame; 404 for a
    stream the backend does not know."""
    _admin(viewer)
    owner_id = _owner(feed_id)
    jpeg = feeds.latest(feed_id)
    frame = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode() if jpeg else None
    feeds.note(owner_id, "question", body.question, "admin")

    async def lines() -> AsyncIterator[str]:
        # Its own session: the request's would close before the stream ends.
        async with new_session() as session:
            try:
                async for step in agent.run(
                    session,
                    body.question,
                    chat,
                    embed,
                    [DEMO_OWNER, owner_id],
                    collections=body.collections,
                    owner_id=owner_id,
                    live_frame=frame,
                ):
                    yield step.model_dump_json() + "\n"
            except UpstreamError as error:
                yield agent.error_line(str(error))
            except Exception as error:  # the stream has started, so no status code is left
                yield agent.error_line(f"{type(error).__name__}: {error}")

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.get("/{feed_id}/goals", response_model=list[GoalPublic])
async def stream_goals(feed_id: str, session: SessionDep, viewer: ViewerDep):
    """What the stream's user has the glasses watching out for, each goal with its triggers."""
    _admin(viewer)
    return await triggers.of(session, _owner(feed_id))


@router.delete("/{feed_id}/goals/{goal_id}", status_code=204)
async def remove_stream_goal(feed_id: str, goal_id: int, session: SessionDep, viewer: ViewerDep):
    """Stop the stream's user's glasses watching for one goal: it goes with all its triggers."""
    _admin(viewer)
    owner_id = _owner(feed_id)
    goal = await session.get(Goal, goal_id)
    if goal is None or goal.owner_id != owner_id:
        raise HTTPException(404, "No such goal of this stream's user")
    text = goal.text
    await triggers.remove(session, goal)
    feeds.note(owner_id, "goal", f"stopped: {text}", "admin")
