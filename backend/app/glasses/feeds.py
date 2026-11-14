"""The live feeds: the newest frame each client is sending, for the admin's stream wall.

Every frame that reaches the backend (a memory frame or a watched frame, over HTTP or the
WebSocket) is recorded here once its faces are blurred, under the client that sent it: who
(the login), which app (`client`: "live" for the home page, "harness" for the glasses harness,
"glasses" for the Android app, as each says), what kind (a "stream", a "photo" or "watch"
frames) and which stream. Only the newest frame of each feed is kept, in memory: nothing is
written, a restart forgets them, and a feed with no frame for IDLE_S is dropped.
GET /api/admin/streams lists them for the admin (routes/streams.py).

Each feed also carries its user's log: the questions they sent, the goals and reminders they
set, and the triggers and warnings that fired for them (`note`), newest first, kept in memory
for EVENTS_S and at most MAX_EVENTS per user. A question or goal is the user's, not one app's,
so every feed of a user shows the same log. The wall's tiles show only those (SUMMARY); the
full page of one feed (GET /api/admin/streams/{id}) shows everything, verbose: what became of
each frame (`frame_result`: stored or why not, every trigger's score against its level, what
fired), the chat model's searches and answers, and errors, each with its JSON `detail`.
"""

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

Kind = Literal["stream", "photo", "watch"]
EventKind = Literal[
    "question", "answer", "search", "goal", "reminder", "trigger", "warning", "frame", "error"
]
# What the wall's tiles show; the full page shows every kind.
SUMMARY: set[str] = {"question", "goal", "reminder", "trigger", "warning"}

IDLE_S = 60.0  # a feed with no frame for this long is dropped
LIVE_S = 5.0  # a feed whose last frame is newer than this counts as live
MAX_FEEDS = 64  # beyond this the feed idle longest makes room
CLIENT_MAX = 40
EVENTS_S = 1800.0  # how long a user's log keeps an event
MAX_EVENTS = 300  # events kept per user
MAX_FRAME_LOG = 400  # frame results kept per feed: over three minutes at 2 fps
TILE_EVENTS = 20  # events a tile on the wall gets
PAGE_EVENTS = 500  # entries the full page gets


class FeedEvent(BaseModel):
    at: datetime
    kind: EventKind
    text: str
    via: str  # how it arrived: "chat", "glasses", "stream", "watch", "panel"
    ms: int | None = None  # how long the backend took, when measured
    detail: dict | None = None  # the full answer or request, as JSON, for the full page


class FeedPublic(BaseModel):
    id: str
    client: str
    user: str
    kind: Kind
    stream_id: str
    demo: bool  # checked against the triggers only, not stored (the home page's demo clips)
    started_at: datetime
    last_at: datetime
    live: bool
    frames: int
    fps: float  # frames a second over the last few seconds
    events: list[FeedEvent]  # the user's questions, goals, triggers..., newest first (SUMMARY)


class FeedDetail(FeedPublic):
    log: list[FeedEvent]  # everything: this feed's frame results and the user's log, newest first


@dataclass
class _Feed:
    id: str
    client: str
    user: str
    kind: Kind
    stream_id: str
    demo: bool
    started_at: datetime
    owner_id: int = 0
    jpeg: bytes = b""
    last_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: float = 0.0  # time.monotonic() of the newest frame
    frames: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=20))
    log: deque = field(default_factory=lambda: deque(maxlen=MAX_FRAME_LOG))  # FeedEvents


_lock = threading.Lock()
_feeds: dict[tuple, _Feed] = {}
_events: dict[int, deque] = {}  # owner id -> (time.monotonic(), FeedEvent), oldest first


def note(
    owner_id: int,
    kind: EventKind,
    text: str,
    via: str,
    detail: dict | None = None,
    ms: int | None = None,
) -> None:
    """Add to a user's log: a question they sent, a goal or reminder they set, a search or an
    answer of the chat model, a trigger or warning that fired for them."""
    event = FeedEvent(
        at=datetime.now(UTC), kind=kind, text=text.strip()[:500], via=via, ms=ms, detail=detail
    )
    with _lock:
        _events.setdefault(owner_id, deque(maxlen=MAX_EVENTS)).append((time.monotonic(), event))


def _log(owner_id: int, now: float) -> list[FeedEvent]:
    events = _events.get(owner_id)
    if not events:
        return []
    while events and now - events[0][0] > EVENTS_S:
        events.popleft()
    return [event for _, event in reversed(events)]


def frame_result(
    key: tuple,
    text: str,
    detail: dict | None = None,
    ms: int | None = None,
    kind: EventKind = "frame",
) -> None:
    """What became of a frame `record` returned `key` for: for its feed's full page."""
    event = FeedEvent(
        at=datetime.now(UTC), kind=kind, text=text[:500], via=key[1], ms=ms, detail=detail
    )
    with _lock:
        feed = _feeds.get(key)
        if feed is not None:
            feed.log.append(event)


def clean_client(name: str | None, default: str) -> str:
    """A client's name as sent, trimmed to something safe to show; `default` when none."""
    name = "".join(c for c in (name or "").strip().lower() if c.isalnum() or c in "-_")
    return name[:CLIENT_MAX] or default


def record(
    owner_id: int,
    user: str,
    client: str,
    kind: Kind,
    stream_id: str,
    jpeg: bytes,
    demo: bool = False,
) -> tuple:
    """Keep `jpeg` (faces already blurred) as the newest frame of its feed; the feed's key, for
    `frame_result`."""
    key = (owner_id, client, kind, stream_id)
    now, wall = time.monotonic(), datetime.now(UTC)
    with _lock:
        _prune(now)
        feed = _feeds.get(key)
        if feed is None:
            if len(_feeds) >= MAX_FEEDS:
                oldest = min(_feeds, key=lambda k: _feeds[k].last_seen)
                del _feeds[oldest]
            digest = hashlib.sha1(repr(key).encode()).hexdigest()[:12]
            feed = _Feed(digest, client, user, kind, stream_id, demo, wall, owner_id)
            _feeds[key] = feed
        feed.jpeg, feed.last_at, feed.last_seen, feed.demo = jpeg, wall, now, demo
        feed.frames += 1
        feed.recent.append(now)
    return key


def _prune(now: float) -> None:
    for key in [k for k, feed in _feeds.items() if now - feed.last_seen > IDLE_S]:
        del _feeds[key]


def _fps(feed: _Feed, now: float) -> float:
    times = [t for t in feed.recent if now - t <= LIVE_S]
    if len(times) < 2:
        return 0.0
    return round((len(times) - 1) / max(times[-1] - times[0], 1e-3), 1)


def _public(feed: _Feed, now: float) -> dict:
    return {
        "id": feed.id,
        "client": feed.client,
        "user": feed.user,
        "kind": feed.kind,
        "stream_id": feed.stream_id,
        "demo": feed.demo,
        "started_at": feed.started_at,
        "last_at": feed.last_at,
        "live": now - feed.last_seen <= LIVE_S,
        "frames": feed.frames,
        "fps": _fps(feed, now),
        # The tile's short log: the summary kinds, without the details.
        "events": [
            event.model_copy(update={"detail": None})
            for event in _log(feed.owner_id, now)
            if event.kind in SUMMARY
        ][:TILE_EVENTS],
    }


def active() -> list[FeedPublic]:
    """The feeds that sent a frame in the last IDLE_S, newest first."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        feeds = sorted(_feeds.values(), key=lambda feed: feed.last_seen, reverse=True)
        return [FeedPublic(**_public(feed, now)) for feed in feeds]


def detail(feed_id: str) -> FeedDetail | None:
    """One feed with its full log: its frame results and its user's log, newest first. None
    when there is no such feed (any more)."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        feed = next((feed for feed in _feeds.values() if feed.id == feed_id), None)
        if feed is None:
            return None
        log = sorted([*feed.log, *_log(feed.owner_id, now)], key=lambda e: e.at, reverse=True)
        return FeedDetail(**_public(feed, now), log=log[:PAGE_EVENTS])


def latest(feed_id: str) -> bytes | None:
    """The newest frame of a feed, or None when there is no such feed (any more)."""
    with _lock:
        _prune(time.monotonic())
        return next((feed.jpeg for feed in _feeds.values() if feed.id == feed_id), None)


def reset() -> None:
    """Forget every feed (tests)."""
    with _lock:
        _feeds.clear()
        _events.clear()
