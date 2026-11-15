"""The demo dataset: the admin's published demo clips (app/showcase.py), as demo memories.

Every published clip is stored as demo memories (owner DEMO_OWNER, collection "demo") that every
user can ask about: a video as its frames at FPS, the way the glasses stream (a frame much like
the last one kept, and less than frames.STORE_EVERY_S later, is skipped), an image sequence as
one memory per image. Every face is blurred first, as for any image the backend receives. Each
memory's `source` names its clip and the clip's media, so a clip whose media changed, or that was
unpublished or deleted, has its old memories replaced or removed.

`sync` does that for all clips. The backend runs it in the background after each start and
whenever the admin changes a clip (`request_sync`), since embedding a video takes a while.
"""

import asyncio
import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import cv2
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import faces, media, showcase, vectors
from app.glasses.frames import SCENE_CHANGE, STORE_EVERY_S, cosine_distance
from app.llm import Embedder, UpstreamError
from app.models import Memory, Showcase
from app.search import DEMO_OWNER, TABLE, add_memory, width_error

log = logging.getLogger(__name__)

COLLECTION = "demo"
FPS = 2  # as the glasses stream
MAX_FRAMES = 1200  # ten minutes of video at FPS; the rest of a longer video is left out
BATCH = 8  # images per call to the embedding model
_PREFIX = "clip:"

# Made by the running worker (`worker`), so it belongs to the app's event loop.
_wanted: asyncio.Event | None = None


def request_sync() -> None:
    """Ask the background worker to run `sync` soon: a clip changed."""
    if _wanted is not None:
        _wanted.set()


async def worker(session_factory, embed: Embedder, retry_s: float) -> None:
    """Runs `sync` once now and again whenever `request_sync` is called; after a failure (the
    embedding model is down during a deploy, say) again in `retry_s` seconds."""
    global _wanted
    _wanted = asyncio.Event()
    _wanted.set()
    try:
        while True:
            await _wanted.wait()
            _wanted.clear()
            try:
                async with session_factory() as session:
                    await sync(session, embed)
            except Exception:
                log.exception("Storing the demo clips failed; trying again in %d s", retry_s)
                await asyncio.sleep(retry_s)
                _wanted.set()
    finally:
        _wanted = None


def clip_prefix(clip_id: int) -> str:
    """How the `source` of every memory of a clip starts, whatever its media."""
    return f"{_PREFIX}{clip_id}:"


def clip_of(source: str) -> int | None:
    """The clip a memory with this `source` is a frame of; None for any other memory."""
    if not source.startswith(_PREFIX):
        return None
    clip_id, _digest = source.removeprefix(_PREFIX).split(":")
    return int(clip_id)


def _aware(moment: datetime) -> datetime:
    # SQLite gives times back without their offset; every time stored here is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def clip_starts(session: AsyncSession, sources: Iterable[str]) -> dict[str, datetime]:
    """When each clip among `sources` (memories' `source`; others are skipped) starts, by source:
    its earliest frame, as `_store_clip` always keeps a clip's first frame. Over all of the
    clip's frames, for a caller that has only some of them (a limit or a filter cut the rest)."""
    clips = {source for source in sources if clip_of(source) is not None}
    if not clips:
        return {}
    rows = await session.exec(
        select(Memory.source, func.min(col(Memory.created_at)))
        .where(Memory.owner_id == DEMO_OWNER, col(Memory.source).in_(clips))
        .group_by(col(Memory.source))
    )
    return {source: _aware(start) for source, start in rows}


def frame_at_s(frame: Memory, starts: dict[str, datetime]) -> float:
    """How many seconds into its clip a clip frame was taken: `starts` holds each clip's start,
    by source."""
    return (_aware(frame.created_at) - starts[frame.source]).total_seconds()


async def clip_frames(session: AsyncSession, clip_id: int) -> list[tuple[float, Memory]]:
    """A clip's frames, oldest first, each with how many seconds into the clip it was taken."""
    frames = list(
        await session.exec(
            select(Memory)
            .where(Memory.owner_id == DEMO_OWNER)
            .where(col(Memory.source).startswith(clip_prefix(clip_id)))
            .order_by(col(Memory.created_at), col(Memory.id))
        )
    )
    # All of the clip's frames are here, oldest first: the first of a source is its start, with
    # no second read that a clip being replaced meanwhile could leave without it.
    starts: dict[str, datetime] = {}
    for frame in frames:
        starts.setdefault(frame.source, _aware(frame.created_at))
    return [(frame_at_s(frame, starts), frame) for frame in frames]


@dataclass(frozen=True)
class DemoView:
    """Someone watches a demo clip instead of a camera: which, and how far into it, in seconds."""

    clip_id: int
    at_s: float


def source(clip: Showcase) -> str:
    """What a clip's memories carry: its id and a digest of its media, so new media is noticed."""
    digest = hashlib.sha1(clip.assets_json.encode()).hexdigest()[:12]
    assert clip.id is not None
    return f"{clip_prefix(clip.id)}{digest}"


def video_frames(path: str, every_s: float = 1 / FPS, limit: int = MAX_FRAMES):
    """(seconds into the video, JPEG bytes) every `every_s` seconds. Raises ValueError for a
    file OpenCV cannot read."""
    video = cv2.VideoCapture(path)
    if not video.isOpened():
        raise ValueError(f"Cannot read the video {path}")
    try:
        rate = video.get(cv2.CAP_PROP_FPS) or 30.0
        index, taken, next_at = 0, 0, 0.0
        while taken < limit:
            ok, frame = video.read()
            if not ok:
                break
            at = index / rate
            index += 1
            if at + 1e-6 < next_at:
                continue
            next_at += every_s
            # As small as it will be stored (media.save_image): every frame is held until stored.
            height, width = frame.shape[:2]
            if (scale := media.MAX_SIDE / max(height, width)) < 1:
                frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                taken += 1
                yield at, jpeg.tobytes()
    finally:
        video.release()


def _stills(clip: Showcase) -> list[tuple[float, bytes]]:
    """(seconds into the clip, image bytes) of a clip's media, in order."""
    files = [showcase.folder() / a["name"] for a in showcase.assets(clip)]
    files = [file for file in files if file.is_file()]
    if clip.kind == "video":
        return [still for file in files[:1] for still in video_frames(str(file))]
    return [(i * clip.image_seconds, file.read_bytes()) for i, file in enumerate(files)]


async def _store_clip(session: AsyncSession, clip: Showcase, embed: Embedder) -> int:
    """Store a clip as demo memories and commit; the number stored. On any failure nothing of
    the clip is kept, its image files included."""
    stills = await anyio.to_thread.run_sync(_stills, clip)
    # The clip "happened" just now: its last frame is the latest moment.
    end = datetime.now(UTC)
    length = stills[-1][0] if stills else 0.0
    caption = clip.title if not clip.description else f"{clip.title}: {clip.description}"
    tag = source(clip)
    stored = 0
    last: tuple[list[float], float] | None = None  # the vector and time of the last one kept
    saved: list[str] = []  # every image file written, to delete if the clip is not stored
    try:
        for start in range(0, len(stills), BATCH):
            batch = stills[start : start + BATCH]
            names = []
            for _at, data in batch:
                blurred = await anyio.to_thread.run_sync(faces.anonymise, data)
                names.append(await anyio.to_thread.run_sync(media.save_image, blurred))
                saved.append(names[-1])
            urls = [await anyio.to_thread.run_sync(media.data_url, name) for name in names]
            embeddings = await embed(urls)
            for (at, _data), name, vector in zip(batch, names, embeddings, strict=True):
                if problem := width_error(len(vector)):
                    raise UpstreamError(problem)
                # As a stream is stored: skip a frame much like the last one kept, unless the
                # last one is old.
                if (
                    clip.kind == "video"
                    and last is not None
                    and at - last[1] < STORE_EVERY_S
                    and cosine_distance(vector, last[0]) < SCENE_CHANGE
                ):
                    media.delete(name)
                    continue
                memory = await add_memory(
                    session,
                    "image",
                    vector,
                    created_at=end - timedelta(seconds=length - at),
                    text=caption,
                    image_file=name,
                    collection=COLLECTION,
                    owner_id=DEMO_OWNER,
                )
                memory.source = tag
                session.add(memory)
                last = (vector, at)
                stored += 1
        await session.commit()
    except BaseException:
        with anyio.CancelScope(shield=True):
            await session.rollback()
        for name in saved:
            media.delete(name)
        raise
    return stored


async def _remove(session: AsyncSession, memories: list[Memory]) -> None:
    for memory in memories:
        assert memory.id is not None
        await vectors.delete(await session.connection(), TABLE, memory.id)
        await session.delete(memory)
        if memory.image_file:
            media.delete(memory.image_file)


async def sync(session: AsyncSession, embed: Embedder) -> int:
    """Make the demo memories match the published clips; the number of clips stored anew.

    Raises app.llm.UpstreamError when the embedding model fails; a clip stored before that stays.
    """
    clips = list(
        await session.exec(
            select(Showcase).where(col(Showcase.published)).order_by(col(Showcase.position))
        )
    )
    wanted = {source(clip): clip for clip in clips if showcase.assets(clip)}
    rows = await session.exec(
        select(Memory).where(Memory.owner_id == DEMO_OWNER, col(Memory.source).startswith(_PREFIX))
    )
    have: dict[str, list[Memory]] = {}
    for memory in rows:
        have.setdefault(memory.source, []).append(memory)
    stale = [memory for tag, rows in have.items() if tag not in wanted for memory in rows]
    if stale:
        await _remove(session, stale)
        await session.commit()
        log.info("Removed %d demo memories of changed or unpublished clips", len(stale))
    added = 0
    for tag, clip in wanted.items():
        if tag in have:
            continue
        count = await _store_clip(session, clip, embed)
        log.info("Stored demo clip %s (%r) as %d memories", clip.id, clip.title, count)
        added += 1
    return added
