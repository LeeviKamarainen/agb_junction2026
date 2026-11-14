"""Frames from the glasses: each one is embedded, kept as a memory, and checked against triggers.

The phone sends a frame twice a second while streaming, and single photos when asked. Every frame
is embedded (EmbeddingGemma 2, the memories' space). A stream frame is only stored when the scene
changed since the last one stored from that stream, or a while has passed, so a still scene does
not flood the database; single photos are always stored. Then every enabled Trigger is compared
with the frame: the closest one within its max_distance, and out of its cooldown, fires, and the
chat model writes the message the glasses show (the trigger's own instruction when there is no
chat model). Most frames get an empty answer.
"""

import math
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session, col, select

from app import media, vectors
from app.llm import Chat, Embedder, UpstreamError
from app.models import Trigger
from app.search import add_memory, width_error

COLLECTION = "glasses"
TRIGGER_TABLE = "trigger_embedding"
# A stream frame is stored when it is at least this far from the last frame stored from the same
# stream, or that one is older than STORE_EVERY_S. Tune on real footage: 0 stores every frame.
SCENE_CHANGE = 0.05
STORE_EVERY_S = 10.0
MESSAGE_SYSTEM = (
    "You speak to someone wearing smart glasses, on a small see-through display. Write one or two "
    "short sentences, at most 30 words, plain text, no markdown. Speak to them directly."
)

Source = Literal["stream", "single"]


class TriggerMatch(BaseModel):
    trigger_id: int
    name: str
    distance: float  # cosine distance between the frame and the trigger's watch_for
    fired: bool


class GlassesMessage(BaseModel):
    """What the glasses show. `type` lets them draw the two kinds apart: "trigger" is something the
    backend noticed on its own (a trigger fired), "text_reply" an answer to the wearer."""

    type: Literal["text_reply", "trigger"]
    title: str
    text: str
    trigger_id: int | None = None  # set for "trigger"


class FrameReply(BaseModel):
    stored: bool  # whether the frame became a memory
    memory_id: int | None = None
    note: str | None = None  # why it was not stored, or what went wrong after embedding it
    message: GlassesMessage | None = None  # show this on the glasses; None for most frames
    matches: list[TriggerMatch] = []  # every enabled trigger, closest first, for tuning


# The last frame stored per stream: its vector and when. In memory, so it resets on restart, which
# only means the next frame of a stream is stored.
_last_stored: dict[str, tuple[list[float], float]] = {}
_lock = threading.Lock()


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / norms if norms else 1.0


def _should_store(source: Source, stream_id: str, vector: list[float], now: float) -> str | None:
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


def _trigger_vectors(session: Session, triggers: list[Trigger], embed: Embedder) -> dict:
    """Each trigger's watch_for vector, embedding the ones that have none yet."""
    connection = session.connection()
    ids = [t.id for t in triggers if t.id is not None]
    stored = vectors.stored(connection, TRIGGER_TABLE, ids)
    missing = [t for t in triggers if t.id not in stored]
    if missing:
        for trigger, vector in zip(missing, embed([t.watch_for for t in missing]), strict=True):
            assert trigger.id is not None
            vectors.upsert(connection, TRIGGER_TABLE, trigger.id, vector)
            stored[trigger.id] = vector
    return stored


def forget_trigger_vector(session: Session, trigger_id: int) -> None:
    """Call when a trigger's watch_for changes or it is deleted; it is embedded again on use."""
    vectors.delete(session.connection(), TRIGGER_TABLE, trigger_id)


def _message(chat: Chat | None, trigger: Trigger, jpeg_name: str) -> str:
    if chat is not None:
        try:
            reply = chat(
                [
                    {"role": "system", "content": MESSAGE_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"The glasses just saw this ({trigger.watch_for}). "
                                f"{trigger.instruction}",
                            },
                            {"type": "image_url", "image_url": {"url": media.data_url(jpeg_name)}},
                        ],
                    },
                ],
                None,
            )
            text = (reply.get("content") or "").strip()
            if text:
                return text
        except UpstreamError:
            pass
    return trigger.instruction


def receive(
    session: Session,
    jpeg: bytes,
    embed: Embedder,
    chat: Chat | None,
    source: Source = "single",
    stream_id: str = "",
    seen_at: datetime | None = None,
) -> FrameReply:
    """Embed one frame, store it when it is new enough, and fire at most one trigger.

    Raises ValueError for data that is not an image and UpstreamError when embedding fails.
    """
    name = media.save_image(jpeg)  # shrinks it; raises ValueError for what is not an image
    keep = False
    try:
        (vector,) = embed([media.data_url(name)])
        if problem := width_error(len(vector)):
            raise UpstreamError(problem)
        now = time.monotonic()
        moment = seen_at or datetime.now(UTC)
        reply = FrameReply(stored=False)

        if (why_not := _should_store(source, stream_id, vector, now)) is None:
            memory = add_memory(session, "image", vector, moment, "", name, collection=COLLECTION)
            keep = True
            reply.stored, reply.memory_id = True, memory.id
            if source == "stream":
                with _lock:
                    _last_stored[stream_id] = (vector, now)
        else:
            reply.note = why_not

        triggers = list(session.exec(select(Trigger).where(col(Trigger.enabled))))
        if triggers:
            try:
                trigger_vectors = _trigger_vectors(session, triggers, embed)
            except UpstreamError as error:
                reply.note = f"triggers not checked: {error}"
                trigger_vectors = {}
            ranked = sorted(
                (
                    (cosine_distance(vector, trigger_vectors[t.id]), t)
                    for t in triggers
                    if t.id in trigger_vectors
                ),
                key=lambda pair: pair[0],
            )
            fired: Trigger | None = None
            for distance, trigger in ranked:
                cooled = (
                    trigger.last_fired_at is None
                    or (moment - _aware(trigger.last_fired_at)).total_seconds()
                    >= trigger.cooldown_s
                )
                if fired is None and distance <= trigger.max_distance and cooled:
                    fired = trigger
                assert trigger.id is not None
                reply.matches.append(
                    TriggerMatch(
                        trigger_id=trigger.id,
                        name=trigger.name,
                        distance=round(distance, 4),
                        fired=trigger is fired,
                    )
                )
            if fired is not None:
                assert fired.id is not None
                fired.last_fired_at = moment
                session.add(fired)
                reply.message = GlassesMessage(
                    type="trigger",
                    title=fired.name,
                    text=_message(chat, fired, name),
                    trigger_id=fired.id,
                )
        session.commit()
        return reply
    finally:
        if not keep:
            media.delete(name)


def _aware(moment: datetime) -> datetime:
    """SQLite returns datetimes without their offset; they were stored in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def reset_streams() -> None:
    """Forget the last stored frame of every stream (for tests)."""
    with _lock:
        _last_stored.clear()
