"""The one mapping from a compute job's queue row and job dir to a ``JobStatus``.

Two sources describe a compute job and they can disagree: the queue row says
what the queue last recorded (``todo``, ``doing``, ``failed``...), the job dir
says what the job itself wrote. ``derive_status()`` decides between them, in
this order, and nothing else may map either source to a status:

1. a ``result.json`` written for this exact try wins, whatever the row says;
   one written for another try is ignored;
2. no row means the job is unknown (``None``);
3. ``todo`` is QUEUED;
4. ``doing`` is RUNNING (CANCELLING once an abort was asked for) only while the
   worker's heartbeat *and* the job's own heartbeat are fresh; either one stale
   is INTERRUPTED. Progress updates are never taken as a heartbeat;
5. ``cancelled`` (before it started) is TIMED_OUT ``queue_timeout`` when it had
   waited past its ``queue_timeout_s``, otherwise CANCELLED; ``aborted`` (while
   running) is CANCELLED;
6. ``failed`` or ``succeeded`` without a result is FAILED ``error``: a job that
   ended without saying how cannot be trusted to have succeeded;
7. any other row status is a ``JobProtocolError``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .contract import ErrorCode
from .job_dir import JobResultFile, read_process, read_request, read_result
from .status import JobProtocolError, JobStatus

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_TIMEOUT_S = 60.0


def heartbeat_timeout_s() -> float:
    """Return ``JOB_HEARTBEAT_TIMEOUT_S``: how old a heartbeat may be and still count as alive.

    Read when called, never at import. A missing, unparsable or non-positive
    value falls back to ``DEFAULT_HEARTBEAT_TIMEOUT_S``.
    """
    raw = os.environ.get("JOB_HEARTBEAT_TIMEOUT_S")
    if raw is None:
        return DEFAULT_HEARTBEAT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0 or value == float("inf"):
        logger.warning(
            "Ignoring JOB_HEARTBEAT_TIMEOUT_S=%r; using %s", raw, DEFAULT_HEARTBEAT_TIMEOUT_S
        )
        return DEFAULT_HEARTBEAT_TIMEOUT_S
    return value


class QueueRow(BaseModel):
    """One row of the job queue's status view: what the queue knows about one try.

    submission_key       the try (the queue's lock key)
    status               the queue's own word, not yet a ``JobStatus``:
                         todo, doing, succeeded, failed, cancelled, aborted
    queued_at            when it was put on the queue
    started_at           when a worker took it, once it did
    worker_heartbeat_at  the last heartbeat of the worker holding it, if any
    abort_requested      someone asked to stop it while it ran

    Other columns of the view are ignored, so the view can grow without
    breaking this reader.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    submission_key: str = Field(min_length=1)
    status: str
    queued_at: AwareDatetime
    started_at: AwareDatetime | None = None
    worker_heartbeat_at: AwareDatetime | None = None
    abort_requested: bool = False


@dataclass(frozen=True)
class DerivedStatus:
    """What ``derive_status()`` decided about one try.

    status      the ``JobStatus``
    error_code  set exactly when the status failed
    error       a one-line reason for a failure, or ``None``
    result      the ``result.json`` that decided it, when one did
    """

    status: JobStatus
    error_code: ErrorCode | None = None
    error: str | None = None
    result: JobResultFile | None = None


def _age_s(now: datetime, then: datetime) -> float:
    return (now - then).total_seconds()


def _is_fresh(now: datetime, beat: datetime | None, timeout_s: float) -> bool:
    # A beat slightly in the future (clocks of two machines) counts as fresh.
    return beat is not None and _age_s(now, beat) <= timeout_s


def _running(row: QueueRow, job_dir: Path, now: datetime, timeout_s: float) -> DerivedStatus:
    """Map a ``doing`` row: alive only while both heartbeats are fresh."""
    if not _is_fresh(now, row.worker_heartbeat_at, timeout_s):
        return DerivedStatus(
            JobStatus.INTERRUPTED,
            ErrorCode.INTERRUPTED,
            f"the worker's heartbeat is older than {timeout_s:g}s",
        )

    process = read_process(job_dir)
    if process is not None and process.submission_key == row.submission_key:
        job_beat: datetime | None = process.heartbeat_at
    else:
        # Before the child is launched there is no process.json of this try
        # yet; the start time stands in for its first heartbeat.
        job_beat = row.started_at
    if not _is_fresh(now, job_beat, timeout_s):
        return DerivedStatus(
            JobStatus.INTERRUPTED,
            ErrorCode.INTERRUPTED,
            f"the job's heartbeat is older than {timeout_s:g}s",
        )

    return DerivedStatus(JobStatus.CANCELLING if row.abort_requested else JobStatus.RUNNING)


def _cancelled_before_start(row: QueueRow, job_dir: Path, now: datetime) -> DerivedStatus:
    """Map a ``cancelled`` row that has no result: a queue timeout or a plain cancel.

    The queue timeout comes from ``request.json``; without a request of this
    try the wait cannot be judged, so it reads as a plain cancel.
    """
    request = read_request(job_dir)
    if (
        request is not None
        and request.submission_key == row.submission_key
        and request.queue_timeout_s is not None
        and _age_s(now, row.queued_at) >= request.queue_timeout_s
    ):
        return DerivedStatus(
            JobStatus.TIMED_OUT,
            ErrorCode.QUEUE_TIMEOUT,
            f"waited longer than {request.queue_timeout_s:g}s to start",
        )
    return DerivedStatus(JobStatus.CANCELLED, ErrorCode.CANCELLED, "cancelled before it started")


def derive_status(
    row: QueueRow | None,
    job_dir: Path,
    *,
    submission_key: str,
    attempt_id: str,
    now: datetime | None = None,
    timeout_s: float | None = None,
) -> DerivedStatus | None:
    """Return the status of one try of a compute job, or ``None`` if nothing knows it.

    row             the queue row of this try, or ``None`` if the queue has none
    job_dir         the job's directory (it may not exist yet)
    submission_key  the try asked about; ``row`` must be for the same one
    attempt_id      the run attempt asked about; a result of another attempt is ignored
    now             the time to judge heartbeats by (default: the current UTC time)
    timeout_s       the heartbeat timeout (default: ``heartbeat_timeout_s()``)

    The rules and their order are in the module docstring. Raises
    ``JobProtocolError`` for an unknown queue status or a broken job file, and
    ``ValueError`` when ``row`` is for another try (a caller's mistake).
    """
    if row is not None and row.submission_key != submission_key:
        raise ValueError(
            f"the queue row is for {row.submission_key!r}, not {submission_key!r}"
        )
    now = now or datetime.now(timezone.utc)
    timeout_s = heartbeat_timeout_s() if timeout_s is None else timeout_s

    result = read_result(job_dir)
    if result is not None:
        if result.belongs_to(submission_key, attempt_id):
            return DerivedStatus(result.status, result.error_code, result.error, result)
        logger.debug(
            "Ignoring %s in %s: written for %s / %s",
            "result.json",
            job_dir,
            result.submission_key,
            result.attempt_id,
        )

    if row is None:
        return None

    if row.status == "todo":
        return DerivedStatus(JobStatus.QUEUED)
    if row.status == "doing":
        return _running(row, job_dir, now, timeout_s)
    if row.status == "cancelled":
        return _cancelled_before_start(row, job_dir, now)
    if row.status == "aborted":
        return DerivedStatus(JobStatus.CANCELLED, ErrorCode.CANCELLED, "cancelled while it ran")
    if row.status in ("failed", "succeeded"):
        return DerivedStatus(
            JobStatus.FAILED,
            ErrorCode.ERROR,
            f"the queue says {row.status} but the job wrote no result",
        )
    raise JobProtocolError(f"unknown queue status {row.status!r} for {submission_key}")
