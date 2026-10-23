"""Job statuses: the closed set every backend reports, and which of them are final.

Kept Python 3.10-compatible (``str, Enum``, not ``StrEnum``, and no newer syntax):
the MONAI Label image runs 3.10 and must write the same status strings.
``derive_status()``, which maps a queue row and a job dir onto these statuses,
lives in ``derive.py`` because it needs the error codes of ``contract.py``.
"""

from __future__ import annotations

from enum import Enum


class JobProtocolError(Exception):
    """A backend reported something outside the contract: a status string this
    library does not know, or a job file that is not valid.

    Never read as "final": a waiter that gets one keeps waiting until its
    deadline, so a newer backend cannot end a job by inventing a status.
    """


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


# A final status never changes again; an active one still can.
# Every status is in exactly one of the two sets.
TERMINAL: frozenset[JobStatus] = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
    }
)
ACTIVE: frozenset[JobStatus] = frozenset(
    {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING}
)


def parse_status(value: object) -> JobStatus:
    """Return the ``JobStatus`` a backend's status string names.

    Raises ``JobProtocolError`` for anything else (an unknown string, ``None``,
    a number), so the caller cannot mistake it for a status it knows.
    """
    if isinstance(value, JobStatus):
        return value
    try:
        return JobStatus(value)
    except ValueError:
        raise JobProtocolError(f"unknown job status {value!r}") from None


def is_final(status: JobStatus) -> bool:
    """Return True when ``status`` is terminal: the job will not change again.

    Takes a ``JobStatus`` only. A raw string raises ``TypeError``, even a known
    one like ``"SUCCEEDED"``: strings from a backend go through
    ``parse_status()`` first, which is where an unknown one is refused.
    """
    if not isinstance(status, JobStatus):
        raise TypeError(
            f"is_final() takes a JobStatus, got {type(status).__name__}; "
            "use parse_status() on strings from a backend"
        )
    return status in TERMINAL
