"""Déjà vu: the wearer asks "where do I know this from?" about what is in front of them.

What is in front of them is their newest still (a photo, or a frame of the stream), which is
already a memory with a vector. The stills most like it among the memories from before it are
looked up in embedding space, and the chat model is shown all of them and says which really
show the same thing. The nearest stills are only the most alike, not the same (measured in
memory/notebooks/RECALL-1: a place never visited still scores 0.7 to 0.9), so the model is told
to be strict, and the wearer hears a plain no when none is.

On the home page the view can play a demo clip instead of a camera (`demo.DemoView`). Its frames
are demo memories already (app/demo.py), so what is in front of the wearer is the clip's memory at
the point it is playing, and "before" is only that clip's earlier frames, outside the current
look. With a camera, "before" is only the wearer's own stills: each looks at one memory.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import demo, media, vectors
from app.auth import Viewer
from app.llm import Chat
from app.models import Memory
from app.search import NEAR_COPY_S, TABLE

FRESH_S = 30.0  # the newest still counts as what the wearer is looking at for this long
# "Before" ends GAP_S ahead of the current look at the thing, so the look does not find itself.
# The look is the run of newest stills, back to LOOK_BACK_S, that are within SAME_VIEW (cosine
# distance) of the current one: a stream stores a still every few seconds for as long as the
# wearer keeps looking. Not tuned on footage yet.
GAP_S = 30.0
SAME_VIEW = 0.15
LOOK_BACK_S = 600.0
SHOWN = 3

# The question ends with the thing in view: "where do I know this from?", "have I seen this one
# before", not "what did I see this morning". frontend/src/app/memory/trigger-editor.tsx has a
# copy for its prompt box.
_ASKS = re.compile(
    r"deja vu|déjà vu|\b(know|remember|recogni[sz]e|seen|see) (this|that)( one| thing| place)?"
    r"( before| already| from( somewhere| before)?)?\W*$",
    re.IGNORECASE,
)
SYSTEM = (
    "You help someone wearing smart glasses who asks where they know the thing in front of them "
    "from. You get the still their camera took just now, then the earlier stills from their "
    "memory that look most like it, numbered, each with how long ago it was seen. Decide for "
    "each earlier still whether it shows the very same object or place as the current still: "
    "the same individual thing, not another of its kind or colour, and not a room that only "
    "looks alike. Be strict: when in doubt, it is not the same. Answer with JSON only: "
    '{"same": [the numbers of the earlier stills that show the same thing], "say": "..."}. '
    'When some are the same, "say" names the thing and where it was in the most recent of '
    "them, in one short sentence of at most 25 words, spoken to them directly; leave out how "
    'long ago, they are told that. When none is, "same" is [] and "say" tells them plainly '
    "that you have not seen this before, and in a few words what the closest still shows instead."
)


@dataclass(frozen=True)
class Still:
    memory_id: int
    ago: str  # "2 hours ago"
    same: bool  # the chat model finds it the same thing as the current still


@dataclass(frozen=True)
class Recall:
    stills: list[Still]  # the most alike earlier stills, closest first
    say: str
    last_seen: str | None  # when the newest still found the same was seen; None when none was


def is_recall(text: str) -> bool:
    return bool(_ASKS.search(text))


def ago(seconds: float) -> str:
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return "moments ago"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def recall(
    session: AsyncSession, chat: Chat, viewer: Viewer, view: demo.DemoView | None = None
) -> Recall | None:
    """Where the wearer has seen what their newest still shows, or what the demo clip they are
    watching shows now (`view`). None when there is no such still: none from the last FRESH_S,
    or the clip is not stored as demo memories (yet). Raises UpstreamError when the chat model
    fails and ValueError when its answer cannot be read."""
    now = datetime.now(UTC)
    connection = await session.connection()

    def label(moment: datetime) -> str:
        """When it was seen, as the wearer hears it: on the clip's own clock for a demo clip."""
        return ago(max(0.0, ((now if view is None else current_seen) - moment).total_seconds()))

    if view is None:
        current = (
            await session.exec(
                select(Memory)
                .where(Memory.owner_id == viewer.id, Memory.kind == "image")
                .where(col(Memory.image_file).is_not(None))
                .order_by(col(Memory.created_at).desc())
                .limit(1)
            )
        ).first()
        if current is None or current.id is None or current.image_file is None:
            return None
        current_seen = _aware(current.created_at)
        if (now - current_seen).total_seconds() > FRESH_S:
            return None
        if not media.path(current.image_file).exists():
            return None
        vector = (await vectors.stored(connection, TABLE, [current.id])).get(current.id)
        if not vector:
            return None
        # When the current look began: the wearer's own newest stills, while they show the same
        # view.
        recent = await vectors.nearest(
            connection,
            TABLE,
            vector,
            1000,
            since=current_seen - timedelta(seconds=LOOK_BACK_S),
            where={"kind": "image", "owner": viewer.id},
        )
        began = current_seen
        for hit in sorted(recent, key=lambda hit: hit.created_at, reverse=True):
            if hit.distance > SAME_VIEW:
                break
            began = min(began, hit.created_at)
        until: datetime | None = began - timedelta(seconds=GAP_S)
        where: dict = {"kind": "image", "owner": viewer.id}
        before: set[int] | None = None
    else:
        clip = await demo.clip_frames(session, view.clip_id)
        # The frame on the view: the clip's last memory up to where it is playing (a frame much
        # like the one before it was not stored, so the one before stands for it).
        shown = [(at, memory) for at, memory in clip if at <= view.at_s + 0.25]
        if not shown or shown[-1][1].image_file is None:
            return None
        current_at, current = shown[-1]
        current_seen = _aware(current.created_at)
        vector = (await vectors.stored(connection, TABLE, [current.id or 0])).get(current.id or 0)
        if not vector or not media.path(current.image_file or "").exists():
            return None
        # The clip's frames before the current look at the thing (`GAP_S` ahead of it).
        before = demo.frames_until(clip, current_at - GAP_S)
        until = None
        where = {"kind": "image"}
        if viewer.owners is not None:
            where["owner"] = viewer.owners

    hits = await vectors.nearest(
        connection, TABLE, vector, SHOWN * 5, until=until, where=where, ids=before
    )
    earlier = []  # (memory id, file, when it was seen, label), closest first, one per glance
    for hit in hits:
        if hit.id == current.id or len(earlier) >= SHOWN:
            continue
        memory = await session.get(Memory, hit.id)
        if (
            memory is not None
            and memory.image_file
            and media.path(memory.image_file).exists()
            # A stream stores several stills of one glance: show the closest of them only.
            and all(
                abs((hit.created_at - at).total_seconds()) > NEAR_COPY_S for _, _, at, _ in earlier
            )
        ):
            earlier.append((hit.id, memory.image_file, hit.created_at, label(hit.created_at)))
    if not earlier:
        return Recall([], "I have nothing from before to compare this with.", None)

    async def image(name: str) -> dict:
        url = await anyio.to_thread.run_sync(media.data_url, name)
        return {"type": "image_url", "image_url": {"url": url}}

    assert current.image_file is not None
    content = [{"type": "text", "text": "The current still:"}, await image(current.image_file)]
    for number, (_, name, _, when) in enumerate(earlier, 1):
        content += [{"type": "text", "text": f"Earlier still {number}, seen {when}:"}]
        content += [await image(name)]
    reply = await chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}], None
    )
    text = ((reply.get("content") or "") if isinstance(reply, dict) else "").strip()
    try:
        # The JSON, with whatever the model wrapped around it (a code fence) cut off.
        answer = json.loads(text[text.find("{") : text.rfind("}") + 1])
        same = {n for n in answer["same"] if isinstance(n, int) and 1 <= n <= len(earlier)}
        say = str(answer.get("say") or "").strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"the chat model did not answer with JSON: {text[:80]!r}") from error
    # The most recent of the stills found the same.
    last = max((earlier[n - 1] for n in same), key=lambda still: still[2], default=None)
    return Recall(
        [Still(id, when, n in same) for n, (id, _, _, when) in enumerate(earlier, 1)],
        say or ("" if same else "I haven’t seen this before."),
        last[3] if last else None,
    )
