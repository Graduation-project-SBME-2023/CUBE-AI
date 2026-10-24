"""Waiting on something that has no push channel: a pause-aware deadline,
retry backoff, and ``poll_until``.

``Deadline`` is a budget of seconds measured on a monotonic clock, so a change
of the wall clock never moves it, and time spent paused is given back.

``RetryPolicy`` is exponential backoff with full jitter, capped, where a
server's ``Retry-After`` wins. It also paces reconnects of a dropped stream.

``poll_until(probe, ...)`` calls ``probe()`` until it returns something other
than ``None``. Everything between two probes counts against the deadline (the
probe's own time too, not only the sleeps), and a probe always runs before the
deadline is checked, so a result that arrived in the last interval is still
returned. An error ``is_transient()`` accepts is retried with backoff instead
of ending the wait; any other error propagates at once.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class PollTimeout(TimeoutError):
    """The deadline passed before the probe returned a result."""


class Deadline:
    """A budget of unpaused seconds, measured on a monotonic clock.

    ``remaining()`` takes a reading: the time since the previous reading is
    subtracted unless ``paused()`` was true at either of the two readings. A
    pause is therefore seen only as finely as the readings are taken, and it is
    rounded in the waiter's favour: a pause can make a wait longer, never
    shorter. ``budget_s`` may be ``math.inf`` (no limit).
    """

    def __init__(
        self,
        budget_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        paused: Callable[[], bool] = lambda: False,
    ) -> None:
        if not budget_s > 0:
            raise ValueError(f"a deadline needs a budget > 0, got {budget_s!r}")
        self.budget_s = budget_s
        self._left = budget_s
        self._clock = clock
        self._paused = paused
        self._last = clock()
        self._was_paused = paused()

    def remaining(self) -> float:
        """Return the unpaused seconds left (0 or less once it expired)."""
        now = self._clock()
        is_paused = self._paused()
        if not (self._was_paused or is_paused):
            self._left -= now - self._last
        self._last, self._was_paused = now, is_paused
        return self._left

    def expired(self) -> bool:
        """Return True once the whole budget was spent."""
        return self.remaining() <= 0

    @property
    def paused(self) -> bool:
        """Whether the wait was paused at the latest reading."""
        return self._was_paused


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter, capped."""

    base_s: float = 1.0
    cap_s: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if not (0 < self.base_s <= self.cap_s < math.inf) or not self.multiplier >= 1:
            raise ValueError(
                "RetryPolicy needs 0 < base_s <= cap_s < inf and multiplier >= 1"
            )

    def delay(self, failures: int, retry_after: float | None = None) -> float:
        """Return the seconds to wait after the ``failures``-th failure in a row (from 1).

        A server's ``Retry-After`` wins over the backoff, still capped at ``cap_s``;
        a negative or non-finite one is ignored. Otherwise the ceiling grows as
        ``base_s * multiplier ** (failures - 1)`` up to ``cap_s``, and with jitter
        the delay is drawn uniformly below it, so many waiters do not retry together.
        """
        if retry_after is not None and 0 <= retry_after < math.inf:
            return min(retry_after, self.cap_s)
        exponent = max(failures - 1, 0)
        # Past this exponent the ceiling is the cap anyway; stopping here keeps
        # a long run of failures from overflowing the float.
        if self.multiplier > 1:
            exponent = min(exponent, math.ceil(math.log(self.cap_s / self.base_s, self.multiplier)))
        ceiling = min(self.cap_s, self.base_s * self.multiplier**exponent)
        return random.uniform(0, ceiling) if self.jitter else ceiling


async def poll_until[T](
    probe: Callable[[], Awaitable[T | None]],
    *,
    interval_s: float,
    timeout_s: float,
    retry: RetryPolicy | None = None,
    is_transient: Callable[[BaseException], bool] = lambda exc: False,
    paused: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> T:
    """Return the first non-``None`` result of ``probe()``.

    interval_s    the pause between probes that returned ``None``
    timeout_s     the budget of unpaused seconds (see ``Deadline``)
    retry         the backoff after a transient error (default ``RetryPolicy()``);
                  an error's ``retry_after`` attribute, when set, wins
    is_transient  which errors are retried; any other propagates at once
    paused        true while the wait should not use up its budget
    clock, sleep  injectable for tests

    Raises ``PollTimeout`` once the budget is spent and one last probe still
    returned ``None``. The sleep never runs past the deadline while unpaused.
    A probe that never returns is not interrupted: give it its own timeout.
    """
    if not interval_s > 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s!r}")
    retry = retry or RetryPolicy()
    deadline = Deadline(timeout_s, clock=clock, paused=paused)
    failures = 0
    while True:
        try:
            result = await probe()
        except Exception as exc:
            if not is_transient(exc):
                raise
            failures += 1
            wait_s = retry.delay(failures, getattr(exc, "retry_after", None))
        else:
            if result is not None:
                return result
            failures = 0
            wait_s = interval_s
        left = deadline.remaining()
        if left <= 0:
            raise PollTimeout(f"no result within {timeout_s:g} s")
        await sleep(wait_s if deadline.paused else min(wait_s, left))
