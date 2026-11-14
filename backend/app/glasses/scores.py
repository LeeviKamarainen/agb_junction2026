"""How much a trigger stands out in a stream right now, and the level worth firing at.

A frame's plain similarity to a trigger's text says little: the floor moves with the scene (a
road raises every road-related trigger), so no fixed threshold holds. Two things fix that, both
measured on recorded footage in memory/notebooks/TRIG-1:

- The score of a frame is its similarity minus the median similarity over the BASELINE_S before
  it: what has just come into view. Something that stays in view stops scoring as the median
  catches up.
- A trigger's level is set from its own noise: the median of its scores over the NOISE_S before,
  plus a number of spreads (robust standard deviations). It needs no labels and fits the scene
  the wearer is in.

One Stream holds the recent frames of one camera stream and each trigger's scores on them, in
memory: after a restart a stream starts from nothing, and a trigger waits MIN_FRAMES frames for
its level.
"""

import math
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from statistics import median

BASELINE_S = 20.0
NOISE_S = 60.0
MIN_FRAMES = 10  # scores a level is measured on, at least
# The least a spread counts as. On real footage a trigger's spread is 0.008 and up; frames that
# do not change at all (a covered lens) give 0, and a level of 0 would fire on every frame.
MIN_SPREAD = 0.005
IDLE_S = 600.0  # a stream that sent nothing for this long is forgotten
# After a trigger fires, nothing fires in the stream for this long: with two frames a second the
# chat model is asked at most every 2.5 s, which gives it time to answer.
SKIP_S = 2.0
# The highest level worth having: on real footage a clear, close view scores 0.10 to 0.20, so a
# trigger above this would hardly fire again.
MAX_LEVEL = 0.15

Vector = Sequence[float]


def similarity(a: Vector, b: Vector) -> float:
    """Cosine similarity: 1 is identical, 0 is unrelated."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norms if norms else 0.0


class Stream:
    def __init__(self) -> None:
        self.frames: deque[tuple[float, Vector]] = deque()  # (when, vector), oldest first
        # Per trigger id, one (when, similarity, score) per frame in `frames`.
        self.series: dict[int, deque[tuple[float, float, float]]] = {}
        self.seen = 0.0
        self.quiet_until = 0.0  # no trigger fires before this, see SKIP_S
        # `add` runs in a worker thread, `level` and `forget` on the event loop.
        self._lock = threading.Lock()

    def add(self, now: float, vector: Vector, triggers: Mapping[int, Vector]) -> dict[int, float]:
        """Take in the frame seen at `now` (seconds on any steady clock) and give its score for
        each trigger. A trigger new to the stream is first scored on the frames kept, so it has
        a history at once."""
        with self._lock:
            self.seen = now
            oldest = now - NOISE_S - BASELINE_S
            while self.frames and self.frames[0][0] < oldest:
                self.frames.popleft()
            for id in [id for id in self.series if id not in triggers]:
                del self.series[id]
            scores = {}
            for id, wanted in triggers.items():
                series = self.series.get(id)
                if series is None:
                    series = self.series[id] = deque()
                    for when, kept in self.frames:
                        _append(series, when, similarity(kept, wanted))
                while series and series[0][0] < oldest:
                    series.popleft()
                scores[id] = _append(series, now, similarity(vector, wanted))
            self.frames.append((now, vector))
            return scores

    def level(self, id: int, now: float, spreads: float) -> float | None:
        """The level for a trigger from its noise before `now`, or None while there are fewer
        than MIN_FRAMES frames to measure it on."""
        with self._lock:
            past = [
                score for when, _, score in self.series.get(id, ()) if now - NOISE_S <= when < now
            ]
        if len(past) < MIN_FRAMES:
            return None
        middle = median(past)
        # 1.4826 times the median distance from the median is one standard deviation for normal
        # noise, and a real sighting among the scores hardly moves it.
        spread = 1.4826 * median(abs(score - middle) for score in past)
        return middle + spreads * max(spread, MIN_SPREAD)

    def forget(self, id: int) -> None:
        with self._lock:
            self.series.pop(id, None)


def _append(series: deque[tuple[float, float, float]], when: float, value: float) -> float:
    before = [s for w, s, _ in series if when - BASELINE_S <= w < when]
    score = value - median(before) if before else 0.0
    series.append((when, value, score))
    return score


_streams: dict[tuple[int, str], Stream] = {}
_lock = threading.Lock()


def stream(owner_id: int, stream_id: str, now: float) -> Stream:
    """The stream's history, started when it is new; idle streams are dropped on the way."""
    with _lock:
        for key in [key for key, kept in _streams.items() if now - kept.seen > IDLE_S]:
            del _streams[key]
        return _streams.setdefault((owner_id, stream_id), Stream())


def forget_trigger(id: int) -> None:
    """Call when a trigger's text changes: its scores so far were for the old text."""
    with _lock:
        for kept in _streams.values():
            kept.forget(id)


def reset() -> None:
    """Forget every stream (for tests)."""
    with _lock:
        _streams.clear()
