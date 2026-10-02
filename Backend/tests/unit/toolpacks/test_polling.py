"""toolpacks/common/polling.py: poll_until and RetryPolicy (merged plan,
Phase 2.4, S12; moves to jobs_core in Phase 5.1)."""

from __future__ import annotations

import asyncio

import pytest

from agent_system.toolpacks.common.polling import PollTimeout, RetryPolicy, poll_until

pytestmark = pytest.mark.unit


class FakeTime:
    """A monotonic clock that only moves when the poller sleeps."""

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


def _poll(probe, time: FakeTime, **kwargs):
    defaults = dict(interval_s=2.0, timeout_s=10.0, clock=time.clock, sleep=time.sleep)
    return asyncio.run(poll_until(probe, **(defaults | kwargs)))


def test_it_returns_the_first_result():
    time = FakeTime()
    assert _poll(_probe(None, None, "done"), time) == "done"
    assert time.sleeps == [2.0, 2.0]


def test_a_transient_error_does_not_end_the_wait():
    time = FakeTime()
    result = _poll(
        _probe(None, Transient(), "done"),
        time,
        is_transient=lambda exc: isinstance(exc, Transient),
        retry=RetryPolicy(base_s=1.0, jitter=False),
    )
    assert result == "done"
    assert time.sleeps == [2.0, 1.0]  # the interval, then the retry backoff


def test_any_other_error_ends_it_at_once():
    with pytest.raises(ValueError):
        _poll(_probe(ValueError("bad request")), FakeTime())


def test_a_retry_after_wins_over_the_backoff():
    time = FakeTime()
    _poll(
        _probe(Transient(retry_after=7.0), "done"),
        time,
        is_transient=lambda exc: True,
        retry=RetryPolicy(base_s=1.0, jitter=False),
    )
    assert time.sleeps == [7.0]


def test_it_times_out_after_a_last_probe():
    time = FakeTime()
    calls = []

    async def probe():
        calls.append(time.now)
        return None

    with pytest.raises(PollTimeout):
        _poll(probe, time, interval_s=4.0, timeout_s=10.0)
    assert calls == [0.0, 4.0, 8.0, 10.0]  # never sleeps past the deadline


def test_paused_time_does_not_count():
    time = FakeTime()
    paused = iter([True, True, False, False, False, False])

    with pytest.raises(PollTimeout):
        _poll(lambda: _none(), time, interval_s=5.0, timeout_s=10.0, paused=lambda: next(paused))
    # Two paused sleeps did not count: 2 more were needed to spend the 10 s.
    assert time.sleeps == [5.0, 5.0, 5.0, 5.0]


async def _none():
    return None


@pytest.mark.parametrize("failures", [1, 2, 3, 10])
def test_the_backoff_is_capped_and_jittered(failures):
    policy = RetryPolicy(base_s=1.0, cap_s=5.0)
    ceiling = min(5.0, 2.0 ** (failures - 1))
    assert all(0 <= policy.delay(failures) <= ceiling for _ in range(50))
    assert RetryPolicy(base_s=1.0, cap_s=5.0, jitter=False).delay(failures) == ceiling


def test_a_bad_policy_is_refused():
    with pytest.raises(ValueError):
        RetryPolicy(base_s=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_s=10, cap_s=5)
