import pytest

from app.glasses.scores import (
    BASELINE_S,
    MIN_FRAMES,
    MIN_SPREAD,
    NOISE_S,
    Stream,
    similarity,
)

KEYS, DOG = [1.0, 0.0], [0.0, 1.0]


def seeing(amount: float) -> list[float]:
    """A frame whose similarity to KEYS is `amount`."""
    return [amount, (1 - amount**2) ** 0.5]


def test_similarity_is_the_cosine():
    assert similarity(KEYS, KEYS) == pytest.approx(1)
    assert similarity(KEYS, DOG) == pytest.approx(0)
    assert similarity([3.0, 4.0], [6.0, 8.0]) == pytest.approx(1)
    assert similarity([0.0, 0.0], KEYS) == 0


def test_a_score_is_the_similarity_above_the_median_just_before():
    stream = Stream()
    first = stream.add(0, seeing(0.6), {1: KEYS})
    same = stream.add(1, seeing(0.6), {1: KEYS})
    appears = stream.add(2, seeing(0.8), {1: KEYS})

    assert first == {1: 0.0}  # nothing before it to compare with
    assert same[1] == pytest.approx(0)
    assert appears[1] == pytest.approx(0.2)


def test_something_that_stays_in_view_stops_scoring():
    stream = Stream()
    for second in range(10):
        stream.add(second, seeing(0.6), {1: KEYS})
    scores = [stream.add(10 + second, seeing(0.8), {1: KEYS})[1] for second in range(25)]

    assert scores[0] == pytest.approx(0.2)
    assert scores[-1] == pytest.approx(0)  # the last BASELINE_S were all 0.8


def test_frames_older_than_the_baseline_do_not_count():
    stream = Stream()
    stream.add(0, seeing(0.9), {1: KEYS})
    late = stream.add(BASELINE_S + 1, seeing(0.6), {1: KEYS})

    assert late[1] == 0.0  # the only earlier frame is too old


def test_a_level_needs_enough_frames_and_sits_above_the_noise():
    stream = Stream()
    noise = [0.60, 0.62, 0.58, 0.61, 0.59, 0.60, 0.63, 0.57, 0.60, 0.61, 0.59]
    for second, amount in enumerate(noise):
        assert stream.level(1, second, 3) is None or second >= MIN_FRAMES
        stream.add(second, seeing(amount), {1: KEYS})
    now = len(noise)

    quiet, loose = stream.level(1, now, 3), stream.level(1, now, 2)
    scores = [score for _, _, score in stream.series[1]]
    assert quiet is not None and loose is not None
    assert max(scores) < quiet  # three spreads clear every score seen
    assert loose < quiet
    assert stream.add(now, seeing(0.8), {1: KEYS})[1] > quiet  # and a real sighting clears it


def test_frames_that_do_not_change_still_give_a_level_above_zero():
    stream = Stream()
    for second in range(MIN_FRAMES):
        stream.add(second, seeing(0.6), {1: KEYS})

    assert stream.level(1, MIN_FRAMES, 3) == pytest.approx(3 * MIN_SPREAD)
    assert stream.add(MIN_FRAMES, seeing(0.6), {1: KEYS})[1] < 3 * MIN_SPREAD  # does not fire


def test_a_trigger_new_to_the_stream_is_scored_on_the_frames_kept():
    stream = Stream()
    for second in range(MIN_FRAMES + 2):
        stream.add(second, seeing(0.6 + 0.01 * (second % 3)), {})
    now = MIN_FRAMES + 2

    assert stream.level(1, now, 3) is None  # never scored
    stream.add(now, seeing(0.6), {1: KEYS})

    assert len(stream.series[1]) == now + 1
    assert stream.level(1, now, 3) is not None  # from the frames before it was made


def test_old_frames_and_dropped_triggers_are_forgotten():
    stream = Stream()
    stream.add(0, seeing(0.6), {1: KEYS, 2: DOG})
    stream.add(NOISE_S + BASELINE_S + 1, seeing(0.6), {1: KEYS})

    assert len(stream.frames) == 1 and len(stream.series[1]) == 1
    assert 2 not in stream.series
