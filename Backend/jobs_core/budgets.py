"""How long a waiter gives a job, phase by phase, and in total.

A job's wait has four phases, and each gets its own budget, so a long queue
cannot eat the time the job needs to run:

queue_s        waiting for a worker to start it (``math.inf`` = no limit)
run_s          running, once started
result_s       fetching the result once it ended
unavailable_s  retrying a backend that refuses for now (busy, queue full)

The outer budget, ``outer_s``, is the sum of the four plus ``margin_s``: the
inner budgets fire first, each with its own error, and the outer one is only a
backstop for a waiter whose inner bookkeeping went wrong. An outer budget
smaller than the sum would end the wait with the wrong error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

from .contract import JobRequest

DEFAULT_RESULT_S = 120.0
DEFAULT_UNAVAILABLE_S = 300.0
DEFAULT_MARGIN_S = 30.0


@dataclass(frozen=True)
class JobBudgets:
    """The four phase budgets of one wait plus a margin, in seconds."""

    queue_s: float
    run_s: float
    result_s: float
    unavailable_s: float
    margin_s: float = DEFAULT_MARGIN_S

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            # bool is an int; True seconds is a mistake, not a budget.
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f"{field.name} must be a number of seconds, got {value!r}")
            if math.isnan(value):
                raise ValueError(f"{field.name} must be a number, got nan")
        for name in ("queue_s", "run_s", "result_s", "unavailable_s"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)!r}")
        for name in ("run_s", "result_s", "unavailable_s"):
            if getattr(self, name) == math.inf:
                raise ValueError(f"{name} must be finite; only queue_s may be unlimited")
        if not 0 <= self.margin_s < math.inf:
            raise ValueError(f"margin_s must be >= 0 and finite, got {self.margin_s!r}")

    @property
    def outer_s(self) -> float:
        """Return the whole wait's budget: the four phases plus the margin.

        ``math.inf`` when the queue wait has no limit.
        """
        return self.queue_s + self.run_s + self.result_s + self.unavailable_s + self.margin_s

    @classmethod
    def for_request(
        cls,
        request: JobRequest,
        *,
        result_s: float = DEFAULT_RESULT_S,
        unavailable_s: float = DEFAULT_UNAVAILABLE_S,
        margin_s: float = DEFAULT_MARGIN_S,
    ) -> JobBudgets:
        """Return the budgets of a wait on ``request``.

        The queue and run budgets are the request's own (``queue_timeout_s``,
        ``None`` meaning no limit, and ``timeout_s``), so the waiter never gives
        up before the worker would; the other two are the waiter's.
        """
        queue_s = math.inf if request.queue_timeout_s is None else request.queue_timeout_s
        return cls(
            queue_s=queue_s,
            run_s=request.timeout_s,
            result_s=result_s,
            unavailable_s=unavailable_s,
            margin_s=margin_s,
        )
