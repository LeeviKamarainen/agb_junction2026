"""Frames from the glasses: each one is embedded, kept as a memory, and checked against triggers.

The phone sends a frame twice a second while streaming, and single photos when asked. Every frame
is embedded (EmbeddingGemma 2, the memories' space). A stream frame is only stored when the scene
changed since the last one stored from that stream, or a while has passed, so a still scene does
not flood the database; single photos are always stored.

Then a stream frame is scored against the triggers of the sender's enabled goals (a Goal is what
they asked the glasses to watch out for; app/glasses/triggers.py): how far its similarity to a
trigger's text is above what the stream has just been showing. The closest trigger whose score
reaches its level fires for its goal: the chat model is shown the frame and what the wearer
asked, and writes the message the glasses show, or finds it a false alarm. Then nothing reaches
the wearer, and the trigger's level is raised above that score (up to scores.MAX_LEVEL). Without
a chat model, or when its answer cannot be read, no trigger says anything. After a firing the
stream is quiet for scores.SKIP_S, or for scores.READ_S once a message was shown, so the wearer
can read it. The level is measured from the trigger's own noise in the stream
(app/glasses/scores.py), so a trigger waits for a few seconds of frames before it can fire, and
a single photo fires nothing. Most frames get an empty answer.
"""

import json
import math
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

import anyio
from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import media, vectors
from app.glasses import scores
from app.llm import Chat, Embedder, UpstreamError
from app.models import Goal, Trigger
from app.search import add_memory, width_error

COLLECTION = "glasses"
TRIGGER_TABLE = "trigger_embedding"
# A stream frame is stored when it is at least this far from the last frame stored from the same
# stream, or that one is older than STORE_EVERY_S. Tune on real footage: 0 stores every frame.
SCENE_CHANGE = 0.05
STORE_EVERY_S = 10.0
MESSAGE_SYSTEM = (
    "You speak to someone wearing smart glasses, on a small see-through display. You are shown a "
    "still their camera just took, because it looked like something they asked you to watch for. "
    "Look for that thing in the still first. If it is not there, it was a false alarm and they "
    'must not be disturbed: answer {"say": null}. If it is there, answer {"say": "..."} with one '
    "or two short sentences for them, at most 30 words, plain text, spoken to them directly. "
    "Answer with that JSON only."
)
# After a false alarm the trigger's level goes this far above the score that caused it.
RAISE_BY = scores.MIN_SPREAD

Source = Literal["stream", "single"]


class TriggerMatch(BaseModel):
    trigger_id: int
    goal_id: int
    name: str
    distance: float  # cosine distance between the frame and the trigger's watch_for
    fired: bool
    # On a stream frame: the frame's score and the level it must reach (None while the level is
    # still being measured).
    score: float | None = None
    level: float | None = None


class GlassesMessage(BaseModel):
    """What the glasses show. `type` lets them draw the two kinds apart: "trigger" is something the
    backend noticed on its own (a trigger fired), "text_reply" an answer to the wearer."""

    type: Literal["text_reply", "trigger"]
    title: str
    text: str
    trigger_id: int | None = None  # set for "trigger": the one that fired,
    goal_id: int | None = None  # and the goal it belongs to


class FrameReply(BaseModel):
    stored: bool  # whether the frame became a memory
    memory_id: int | None = None
    note: str | None = None  # why it was not stored, or what went wrong after embedding it
    # Show this on the glasses; None for most frames, and when a trigger fired on a false alarm.
    message: GlassesMessage | None = None
    matches: list[TriggerMatch] = []  # every enabled trigger, closest first, for tuning


# The last frame stored per stream: its vector and when. In memory, so it resets on restart, which
# only means the next frame of a stream is stored.
_last_stored: dict[tuple[int, str], tuple[list[float], float]] = {}
_lock = threading.Lock()


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / norms if norms else 1.0


def _should_store(
    source: Source, stream_id: tuple[int, str], vector: list[float], now: float
) -> str | None:
    """None to store the frame, or why not."""
    if source == "single":
        return None
    with _lock:
        last = _last_stored.get(stream_id)
    if last is None or now - last[1] >= STORE_EVERY_S:
        return None
    if cosine_distance(vector, last[0]) >= SCENE_CHANGE:
        return None
    return "similar to the last frame stored from this stream"


async def _trigger_vectors(session: AsyncSession, triggers: list[Trigger], embed: Embedder) -> dict:
    """Each trigger's watch_for vector, embedding the ones that have none yet."""
    connection = await session.connection()
    ids = [t.id for t in triggers if t.id is not None]
    stored = await vectors.stored(connection, TRIGGER_TABLE, ids)
    missing = [t for t in triggers if t.id not in stored]
    if missing:
        for trigger, vector in zip(
            missing, await embed([t.watch_for for t in missing]), strict=True
        ):
            assert trigger.id is not None
            await vectors.upsert(connection, TRIGGER_TABLE, trigger.id, vector)
            stored[trigger.id] = vector
    return stored


async def forget_trigger_vector(session: AsyncSession, trigger_id: int) -> None:
    """Call when a trigger's watch_for changes or it is deleted; it is embedded again on use."""
    await vectors.delete(await session.connection(), TRIGGER_TABLE, trigger_id)
    scores.forget_trigger(trigger_id)


async def _message(chat: Chat, goal: Goal, trigger: Trigger, jpeg_name: str) -> str | None:
    """What to tell the wearer about the still that fired a trigger of the goal, or None when
    the chat model finds it a false alarm. Raises UpstreamError when the model fails and
    ValueError when its answer cannot be read."""
    reply = await chat(
        [
            {"role": "system", "content": MESSAGE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"The wearer asked: “{goal.text}” Watched for: "
                        f"{trigger.watch_for}. If the still shows what they asked about, tell "
                        "them what they need to know now.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": await anyio.to_thread.run_sync(media.data_url, jpeg_name)
                        },
                    },
                ],
            },
        ],
        None,
    )
    text = ((reply.get("content") or "") if isinstance(reply, dict) else "").strip()
    try:
        # The JSON, with whatever the model wrapped around it (a code fence) cut off.
        say = json.loads(text[text.find("{") : text.rfind("}") + 1])["say"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        # Plain words may be the model explaining that it sees nothing: not for the wearer.
        raise ValueError(f"the chat model did not answer with JSON: {text[:80]!r}") from error
    say = say.strip() if isinstance(say, str) else ""
    return say or None


async def receive(
    session: AsyncSession,
    jpeg: bytes,
    embed: Embedder,
    chat: Chat | None,
    source: Source = "single",
    stream_id: str = "",
    seen_at: datetime | None = None,
    owner_id: int = 0,
    collection: str = COLLECTION,
) -> FrameReply:
    """Embed one frame of `owner_id`'s, store it when it is new enough (in `collection`, a tag
    such as one recorded demo), and fire at most one trigger of their enabled goals.

    Raises ValueError for data that is not an image and UpstreamError when embedding fails.
    """
    name = await anyio.to_thread.run_sync(media.save_image, jpeg)
    keep = False
    try:
        (vector,) = await embed([await anyio.to_thread.run_sync(media.data_url, name)])
        if problem := width_error(len(vector)):
            raise UpstreamError(problem)
        now = time.monotonic()
        # The client's time dates the memory; the server's says when a trigger fired.
        moment = seen_at or datetime.now(UTC)
        server_now = datetime.now(UTC)
        reply = FrameReply(stored=False)

        if (why_not := _should_store(source, (owner_id, stream_id), vector, now)) is None:
            memory = await add_memory(
                session, "image", vector, moment, "", name, collection=collection, owner_id=owner_id
            )
            reply.stored, reply.memory_id = True, memory.id
        else:
            reply.note = why_not

        fired: Trigger | None = None
        fired_score = 0.0
        # Only the account's own goals: nobody's triggers fire on someone else's stream.
        goals = {
            goal.id: goal
            for goal in await session.exec(
                select(Goal).where(Goal.owner_id == owner_id, col(Goal.enabled))
            )
        }
        triggers = list(
            await session.exec(select(Trigger).where(col(Trigger.goal_id).in_(list(goals))))
        )
        trigger_vectors: dict = {}
        if triggers:
            try:
                trigger_vectors = await _trigger_vectors(session, triggers, embed)
            except UpstreamError as error:
                reply.note = f"triggers not checked: {error}"
                trigger_vectors = {}
        # Every stream frame goes into the stream's history, triggers or not, so that a trigger
        # made later has frames to measure its level on. In a thread: a trigger new to the
        # stream is scored on every frame kept, which takes a moment.
        stream = scores.stream(owner_id, stream_id, now) if source == "stream" else None
        stream_scores: dict[int, float] = {}
        if stream is not None:
            scored = {t.id: trigger_vectors[t.id] for t in triggers if t.id in trigger_vectors}
            stream_scores = await anyio.to_thread.run_sync(stream.add, now, vector, scored)
        if triggers:
            ranked = sorted(
                (
                    (cosine_distance(vector, trigger_vectors[t.id]), t)
                    for t in triggers
                    if t.id in trigger_vectors
                ),
                key=lambda pair: pair[0],
            )
            for distance, trigger in ranked:
                assert trigger.id is not None
                score = stream_scores.get(trigger.id)
                # A single photo has no stream to score it against, so it fires nothing.
                if score is not None and stream is not None:
                    if trigger.level is None:
                        trigger.level = stream.level(trigger.id, now, trigger.sigmas)
                        session.add(trigger)
                    if fired is None and now >= stream.quiet_until and trigger.level is not None:
                        if score >= trigger.level:
                            fired, fired_score = trigger, score
                reply.matches.append(
                    TriggerMatch(
                        trigger_id=trigger.id,
                        goal_id=trigger.goal_id,
                        name=trigger.name,
                        distance=round(distance, 4),
                        fired=trigger is fired,
                        score=None if score is None else round(score, 4),
                        level=None if trigger.level is None else round(trigger.level, 4),
                    )
                )
            if fired is not None and stream is not None:
                # Set before the chat model is asked, so that a frame arriving meanwhile cannot
                # fire as well.
                stream.quiet_until = now + scores.SKIP_S
                fired.last_fired_at = server_now
                session.add(fired)

        # Commit before asking the chat model, which can take a while: SQLite allows one writer, so
        # holding the transaction open would lock out every other write.
        await session.commit()
        keep = reply.stored
        if reply.stored and source == "stream":
            with _lock:
                _last_stored[(owner_id, stream_id)] = (vector, now)
        if fired is not None:
            assert fired.id is not None and fired.level is not None
            if chat is None:
                reply.note = f"{fired.name} fired, but there is no chat model to look at the frame"
                return reply
            try:
                text = await _message(chat, goals[fired.goal_id], fired, name)
            except (UpstreamError, ValueError) as error:
                reply.note = f"{fired.name} fired, but the chat model failed: {error}"
                return reply
            if text is None:
                reply.note = f"{fired.name} fired on a false alarm"
                # Read again: the trigger may have been edited or deleted while the model looked.
                current = await session.get(Trigger, fired.id, populate_existing=True)
                if current is not None and current.level is not None:
                    raised = max(current.level, fired_score + RAISE_BY)
                    current.level = min(raised, max(current.level, scores.MAX_LEVEL))
                    session.add(current)
                    await session.commit()
                    reply.note += f"; its level is now {current.level:.3f}"
            else:
                reply.message = GlassesMessage(
                    type="trigger",
                    title=fired.name,
                    text=text,
                    trigger_id=fired.id,
                    goal_id=fired.goal_id,
                )
                if stream is not None:
                    stream.quiet_until = time.monotonic() + scores.READ_S
        return reply
    finally:
        if not keep:
            await anyio.to_thread.run_sync(media.delete, name)


def reset_streams() -> None:
    """Forget the last stored frame of every stream, and their scores (for tests)."""
    with _lock:
        _last_stored.clear()
    scores.reset()
