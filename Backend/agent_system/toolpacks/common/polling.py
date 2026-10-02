"""``poll_until`` and ``RetryPolicy`` (merged plan, Phase 2.4, S12).

Phase 5.1 puts these in ``Backend/jobs_core/polling.py``; they live here with
the same signature until then, so the cohort gate (S12) does not wait for the
job platform.

``poll_until(probe, ...)`` calls ``probe()`` until it returns something other
than ``None``. The deadline is monotonic and pause-aware: time during which
``paused()`` is true does not count against it. An error ``is_transient()``
accepts is retried with the policy's backoff (or the error's ``retry_after``)
instead of ending the wait; any other error propagates at once.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class PollTimeout(TimeoutError):
    """The deadline passed before the probe returned a result."""


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter, capped."""

    base_s: float = 1.0
    cap_s: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if not (0 < self.base_s <= self.cap_s) or self.multiplier < 1:
            raise ValueError("RetryPolicy needs 0 < base_s <= cap_s and multiplier >= 1")

    def delay(self, failures: int, retry_after: float | None = None) -> float:
        """Seconds to wait after the ``failures``-th consecutive failure (1-based).
        A server's ``Retry-After`` wins, still capped."""
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, self.cap_s)
        ceiling = min(self.cap_s, self.base_s * self.multiplier ** max(failures - 1, 0))
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
    """The first non-``None`` result of ``probe()``; ``PollTimeout`` after
    ``timeout_s`` of unpaused waiting."""
    if interval_s <= 0 or timeout_s <= 0:
        raise ValueError("interval_s and timeout_s must be > 0")
    retry = retry or RetryPolicy()
    waited = 0.0  # unpaused seconds spent so far
    failures = 0
    while True:
        try:
            result = await probe()
        except Exception as exc:
            if not is_transient(exc):
                raise
            failures += 1
            pause_s = retry.delay(failures, getattr(exc, "retry_after", None))
        else:
            if result is not None:
                return result
            failures = 0
            pause_s = interval_s
        if waited >= timeout_s:
            raise PollTimeout(f"no result within {timeout_s:g} s")
        pause_s = min(pause_s, timeout_s - waited)
        started = clock()
        await sleep(pause_s)
        if not paused():
            waited += clock() - started
