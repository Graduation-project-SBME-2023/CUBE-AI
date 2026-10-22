"""Job statuses (merged plan, Phase 5.1 step 2).

Kept Python 3.10-compatible (``str, Enum``, not ``StrEnum``): the MONAI Label
image runs 3.10 and must use the same status strings (Phase 15.2, J4).
``TERMINAL``, ``ACTIVE``, ``is_final()`` and ``derive_status()`` come with A2.
"""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    """Where a job is. The values are the strings MONAI's job store already writes."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
