"""The pause-aware deadline, the retry backoff, and poll_until."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator

import pytest

from jobs_core.polling import Deadline, PollTimeout, RetryPolicy, poll_until

pytestmark = pytest.mark.unit


class FakeTime:
    """A monotonic clock that moves only when the poller sleeps or a probe takes time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Transient(Exception):
    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after


def _probe(*outcomes):
    """A probe returning (or raising) each outcome in turn."""
    queue = list(outcomes)

    async def probe():
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return probe


async def _none():
    return None


def _poll(probe, time: FakeTime, **kwargs):
    defaults = dict(interval_s=2.0, timeout_s=10.0, clock=time.clock, sleep=time.sleep)
    return asyncio.run(poll_until(probe, **(defaults | kwargs)))


def _flags(*values: bool) -> Iterator[bool]:
    """``paused`` readings in order, then False for ever."""
    yield from values
    while True:
        yield False


# --- Deadline


def test_a_deadline_spends_clock_time() -> None:
    time = FakeTime()
    deadline = Deadline(10.0, clock=time.clock)

    time.now = 4.0
    assert deadline.remaining() == 6.0
    time.now = 10.0
    assert deadline.expired()


def test_time_paused_at_either_reading_is_given_back() -> None:
    time = FakeTime()
    flags = _flags(False, True, True, False, False)  # the first is the start reading
    deadline = Deadline(10.0, clock=time.clock, paused=lambda: next(flags))

    time.now = 3.0
    assert deadline.remaining() == 10.0  # paused at this reading
    time.now = 6.0
    assert deadline.remaining() == 10.0  # paused at both readings
    time.now = 8.0
    assert deadline.remaining() == 10.0  # paused at the previous reading
    time.now = 9.0
    assert deadline.remaining() == 9.0  # unpaused at both: counts


def test_a_deadline_does_not_follow_the_wall_clock() -> None:
    # The default clock is monotonic; a fake clock going backwards (as a wall
    # clock can) would add time, which is why the wall clock is never used.
    deadline = Deadline(10.0)
    assert deadline._clock.__name__ == "monotonic"


def test_an_unlimited_deadline_never_expires() -> None:
    time = FakeTime()
    deadline = Deadline(math.inf, clock=time.clock)
    time.now = 1e12
    assert not deadline.expired()


@pytest.mark.parametrize("budget", [0, -1, math.nan])
def test_a_deadline_needs_a_positive_budget(budget: float) -> None:
    with pytest.raises(ValueError, match="budget > 0"):
        Deadline(budget)


# --- poll_until


def test_it_returns_the_first_result() -> None:
    time = FakeTime()
    assert _poll(_probe(None, None, "done"), time) == "done"
    assert time.sleeps == [2.0, 2.0]


def test_a_result_already_there_needs_no_sleep() -> None:
    time = FakeTime()
    assert _poll(_probe("done"), time) == "done"
    assert time.sleeps == []


def test_a_transient_error_does_not_end_the_wait() -> None:
    time = FakeTime()
    result = _poll(
        _probe(None, Transient(), "done"),
        time,
        is_transient=lambda exc: isinstance(exc, Transient),
        retry=RetryPolicy(base_s=1.0, jitter=False),
    )
    assert result == "done"
    assert time.sleeps == [2.0, 1.0]  # the interval, then the retry backoff


def test_the_backoff_grows_and_resets_after_a_success() -> None:
    time = FakeTime()
    _poll(
        _probe(Transient(), Transient(), None, Transient(), "done"),
        time,
        timeout_s=100.0,
        is_transient=lambda exc: True,
        retry=RetryPolicy(base_s=1.0, jitter=False),
    )
    assert time.sleeps == [1.0, 2.0, 2.0, 1.0]


def test_any_other_error_ends_it_at_once() -> None:
    with pytest.raises(ValueError):
        _poll(_probe(ValueError("bad request")), FakeTime())


def test_a_retry_after_wins_over_the_backoff() -> None:
    time = FakeTime()
    _poll(
        _probe(Transient(retry_after=7.0), "done"),
        time,
        is_transient=lambda exc: True,
        retry=RetryPolicy(base_s=1.0, jitter=False),
    )
    assert time.sleeps == [7.0]


def test_it_times_out_after_a_last_probe() -> None:
    time = FakeTime()
    calls = []

    async def probe():
        calls.append(time.now)
        return None

    with pytest.raises(PollTimeout):
        _poll(probe, time, interval_s=4.0, timeout_s=10.0)
    assert calls == [0.0, 4.0, 8.0, 10.0]  # never sleeps past the deadline


def test_a_result_in_the_last_probe_wins_over_the_deadline() -> None:
    time = FakeTime()
    # The third probe runs at the deadline exactly: its result is returned.
    assert _poll(_probe(None, None, "done"), time, interval_s=5.0, timeout_s=10.0) == "done"
    assert time.now == 10.0


def test_the_probe_time_counts_against_the_deadline() -> None:
    time = FakeTime()
    calls = 0

    async def slow_probe():
        nonlocal calls
        calls += 1
        time.now += 3.0  # a slow call: 3 s each
        return None

    with pytest.raises(PollTimeout):
        _poll(slow_probe, time, interval_s=2.0, timeout_s=10.0)
    # Probes end at 3, 8 and 13; counting only the sleeps would have let it
    # run on to 28 s.
    assert calls == 3
    assert time.now == 13.0


def test_paused_time_does_not_count() -> None:
    time = FakeTime()
    # Readings: the start, then one after each probe (at 0, 5, 10, 15, 20 s).
    flags = _flags(False, True, True, False)

    with pytest.raises(PollTimeout):
        _poll(_none, time, interval_s=5.0, timeout_s=10.0, paused=lambda: next(flags))
    # Paused at the readings at 0 and 5 s: 0-5 and 5-10 are given back, and
    # 10-15 and 15-20 spend the 10 s. Without the pause it would end at 10 s.
    assert time.sleeps == [5.0, 5.0, 5.0, 5.0]
    assert time.now == 20.0


def test_a_paused_wait_sleeps_its_full_interval() -> None:
    time = FakeTime()
    flags = _flags(False, True, False, False)

    with pytest.raises(PollTimeout):
        _poll(_none, time, interval_s=8.0, timeout_s=10.0, paused=lambda: next(flags))
    # While paused, the sleep is not cut short to the (frozen) remaining time.
    assert time.sleeps == [8.0, 8.0, 2.0]


@pytest.mark.parametrize("interval", [0, -1, math.nan])
def test_a_bad_interval_is_refused(interval: float) -> None:
    with pytest.raises(ValueError, match="interval_s"):
        _poll(_none, FakeTime(), interval_s=interval)


def test_a_bad_timeout_is_refused() -> None:
    with pytest.raises(ValueError, match="budget > 0"):
        _poll(_none, FakeTime(), timeout_s=0)


# --- RetryPolicy


@pytest.mark.parametrize("failures", [1, 2, 3, 10])
def test_the_backoff_is_capped_and_jittered(failures: int) -> None:
    policy = RetryPolicy(base_s=1.0, cap_s=5.0)
    ceiling = min(5.0, 2.0 ** (failures - 1))
    assert all(0 <= policy.delay(failures) <= ceiling for _ in range(50))
    assert RetryPolicy(base_s=1.0, cap_s=5.0, jitter=False).delay(failures) == ceiling


def test_a_long_run_of_failures_stays_at_the_cap() -> None:
    # 2.0 ** 5000 overflows a float; the exponent stops once the cap is reached.
    assert RetryPolicy(base_s=1.0, cap_s=30.0, jitter=False).delay(5000) == 30.0
    assert RetryPolicy(base_s=1.0, cap_s=1.0, jitter=False).delay(5000) == 1.0
    assert RetryPolicy(base_s=2.0, cap_s=30.0, multiplier=1.0, jitter=False).delay(5000) == 2.0


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [(7.0, 7.0), (0.0, 0.0), (90.0, 30.0), (-1.0, 1.0), (math.inf, 1.0), (math.nan, 1.0)],
)
def test_retry_after_is_honoured_capped_or_ignored(retry_after: float, expected: float) -> None:
    policy = RetryPolicy(base_s=1.0, cap_s=30.0, jitter=False)
    assert policy.delay(1, retry_after) == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_s": 0},
        {"base_s": 10, "cap_s": 5},
        {"cap_s": math.inf},
        {"multiplier": 0.5},
        {"multiplier": math.nan},
    ],
)
def test_a_bad_policy_is_refused(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)
