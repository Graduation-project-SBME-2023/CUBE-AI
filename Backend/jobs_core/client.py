"""``JobClient``: the one interface every job backend offers, and the rules they share.

A caller (the job runtime, the segmentation client, the Node Runner) holds a
``JobClient`` and never knows which backend is behind it. Every method takes
the ``submission_key`` of one try (``JobRequest.submission_key``), and every
backend applies the same ``submit`` rules:

1. the same ``submission_key`` again returns the record it already has;
2. the same ``job_id`` with a different ``input_hash`` is a ``JobConflict``
   (the tool changed version inside one run, and two versions' results must
   not mix);
3. a ``reusable`` request whose ``job_id`` already has an earlier try that
   succeeded or is still active returns that try's record, so a resumed run
   re-attaches instead of computing again (``get`` and the other methods then
   answer for the new key with that record);
4. anything else (a non-reusable job, or an earlier try that failed) runs
   again under the new key;
5. a backend that refuses for now (``BackendUnavailable``: busy, queue full)
   is retried with backoff, honouring its ``Retry-After``, for up to
   ``unavailable_s``; then ``submit`` raises ``JobUnavailable``.

``wait`` ends with a terminal record. When the waiter itself gives up (a
phase budget ran out, the backend stayed unavailable, or kept reporting
something it does not know), it cancels the job and returns the last record it
had, marked with the waiter's verdict (see ``wait_by_polling``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Protocol

from .budgets import DEFAULT_UNAVAILABLE_S, JobBudgets
from .contract import ErrorCode, JobProgress, JobRecord, JobRequest
from .polling import Deadline, PollTimeout, RetryPolicy, poll_until
from .status import JobProtocolError, JobStatus, is_final

logger = logging.getLogger(__name__)

ProgressListener = Callable[[JobProgress], Awaitable[object] | object]


class JobError(Exception):
    """Base of the errors a ``JobClient`` raises."""


class JobNotFound(JobError, LookupError):
    """No try with this submission key is known to the backend."""


class JobConflict(JobError):
    """The same job id was submitted with a different input hash."""


class NoResult(JobError):
    """``result()`` was asked for a try that did not succeed (or has not ended)."""


class BackendUnavailable(JobError):
    """The backend refuses for now; trying again later may work.

    ``retry_after`` is the backend's own hint in seconds (an HTTP
    ``Retry-After``), or ``None``.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QueueFull(BackendUnavailable):
    """The queue (or the session's share of it) is full."""


class JobUnavailable(JobError):
    """The backend stayed unavailable for the whole ``unavailable_s`` budget."""

    error_code = ErrorCode.RESOURCE_UNAVAILABLE


class CancelSignal(Protocol):
    """Anything that says whether the caller wants the job stopped, such as the
    toolkit's ``CancelToken``."""

    @property
    def cancelled(self) -> bool: ...


class JobClient(Protocol):
    """One job backend: the compute queue, MONAI, or the in-memory fake."""

    async def submit(
        self, request: JobRequest, *, unavailable_s: float = DEFAULT_UNAVAILABLE_S
    ) -> JobRecord:
        """Start ``request`` (or find it), following the rules in the module docstring.

        Raises ``JobConflict`` or ``JobUnavailable``.
        """
        ...

    async def get(self, submission_key: str) -> JobRecord:
        """Return the try's current record.

        Raises ``JobNotFound``, ``BackendUnavailable`` (worth retrying), or
        ``JobProtocolError`` when the backend reports something outside the contract.
        """
        ...

    async def wait(
        self,
        submission_key: str,
        *,
        budgets: JobBudgets,
        on_progress: ProgressListener | None = None,
        cancel: CancelSignal | None = None,
    ) -> JobRecord:
        """Return the try's record once it is terminal, or the waiter's verdict.

        ``on_progress`` gets each new ``JobProgress`` once, in order. Once
        ``cancel`` says so, the job is cancelled and the wait goes on until the
        backend confirms. Raises ``JobNotFound`` if the try is unknown.
        """
        ...

    async def result(self, submission_key: str) -> dict:
        """Return the JSON result of a try that succeeded.

        Raises ``NoResult`` for any other try and ``JobNotFound`` for an unknown one.
        """
        ...

    async def cancel(self, submission_key: str) -> JobRecord:
        """Ask the backend to stop the try and return its record.

        A queued try ends as CANCELLED at once; a running one becomes CANCELLING
        until it stops; a terminal one is left as it is.
        """
        ...

    async def purge(self, submission_key: str) -> None:
        """Forget the try and delete what it left behind. Unknown keys are ignored."""
        ...

    async def list(self, run_id: str, *, active_only: bool = False) -> list[JobRecord]:
        """Return the records of the run's tries, only the active ones if asked."""
        ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def submit_with_retry(
    submit_once: Callable[[], Awaitable[JobRecord]],
    *,
    unavailable_s: float,
    retry: RetryPolicy | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> JobRecord:
    """Return ``submit_once()``'s record, retrying it while the backend is unavailable.

    A ``BackendUnavailable`` is retried with backoff (its ``retry_after`` wins);
    once ``unavailable_s`` has passed and one last try was still refused,
    raises ``JobUnavailable``. Any other error propagates at once.
    """
    retry = retry or RetryPolicy()
    try:
        return await poll_until(
            submit_once,
            interval_s=retry.base_s,
            timeout_s=unavailable_s,
            retry=retry,
            is_transient=lambda exc: isinstance(exc, BackendUnavailable),
            clock=clock,
            sleep=sleep,
        )
    except PollTimeout:
        raise JobUnavailable(
            f"the backend refused the job for longer than {unavailable_s:g}s"
        ) from None


async def _notify(listener: ProgressListener, progress: JobProgress) -> None:
    # A broken listener must not make the waiter abandon a job that still runs.
    try:
        outcome = listener(progress)
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:
        logger.exception("The progress listener failed on update %s", progress.seq)


async def _cancel_quietly(client: JobClient, submission_key: str) -> None:
    try:
        await client.cancel(submission_key)
    except Exception:
        logger.warning("Could not cancel %s", submission_key, exc_info=True)


def with_verdict(
    record: JobRecord,
    status: JobStatus,
    error_code: ErrorCode,
    error: str,
    now: datetime,
) -> JobRecord:
    """Return ``record`` ended by the waiter: a terminal status, its code and reason.

    The result is cleared (a try the waiter gave up on has none), and
    ``finished_at`` is ``now`` unless that is earlier than the record's own times.
    """
    fields = {name: getattr(record, name) for name in JobRecord.model_fields}
    earliest = record.started_at or record.queued_at
    fields.update(
        status=status,
        error_code=error_code,
        error=error,
        result=None,
        result_ref=None,
        finished_at=max(now, earliest),
    )
    return JobRecord(**fields)


async def wait_by_polling(
    client: JobClient,
    submission_key: str,
    *,
    budgets: JobBudgets,
    on_progress: ProgressListener | None = None,
    cancel: CancelSignal | None = None,
    interval_s: float = 2.0,
    retry: RetryPolicy | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    now: Callable[[], datetime] = utc_now,
) -> JobRecord:
    """Implement ``JobClient.wait`` by reading ``client.get`` every ``interval_s``.

    For a backend with no push channel (MONAI) and for the in-memory fake.
    The budgets are deadlines, each on its own clock:

    queue_s        from the first read that saw the try QUEUED
    run_s          from the first read that saw it RUNNING or CANCELLING
    unavailable_s  from the first of an unbroken run of ``BackendUnavailable``
    outer_s        from the start of the wait, around everything

    When one runs out, the waiter cancels the try and returns the last record
    it read, marked: TIMED_OUT ``queue_timeout`` (queue), TIMED_OUT ``timeout``
    (run or outer), FAILED ``resource_unavailable`` (unavailable), or FAILED
    ``protocol_error`` when the latest read was a ``JobProtocolError``. A
    protocol error alone never ends the wait: the job may still be running.
    If no read ever succeeded there is no record to mark, so the error itself
    (``JobProtocolError`` or ``JobUnavailable``) is raised after the cancel.
    """
    retry = retry or RetryPolicy()
    outer = Deadline(budgets.outer_s, clock=clock)
    phase: JobStatus | None = None
    phase_deadline: Deadline | None = None
    unavailable: Deadline | None = None
    last: JobRecord | None = None
    last_seq = -1
    failures = 0
    cancel_sent = False

    async def give_up(status: JobStatus, code: ErrorCode, error: str) -> JobRecord:
        await _cancel_quietly(client, submission_key)
        if last is None:
            if code is ErrorCode.RESOURCE_UNAVAILABLE:
                raise JobUnavailable(error)
            raise JobProtocolError(error)
        logger.warning("Gave up on %s: %s", submission_key, error)
        return with_verdict(last, status, code, error, now())

    while True:
        protocol_error: JobProtocolError | None = None
        try:
            record = await client.get(submission_key)
        except JobProtocolError as exc:
            logger.warning("Protocol error while waiting on %s: %s", submission_key, exc)
            protocol_error = exc
            failures = 0
            unavailable = None
            wait_s = interval_s
        except BackendUnavailable as exc:
            failures += 1
            unavailable = unavailable or Deadline(budgets.unavailable_s, clock=clock)
            if unavailable.expired():
                return await give_up(
                    JobStatus.FAILED,
                    ErrorCode.RESOURCE_UNAVAILABLE,
                    f"the backend was unavailable for longer than {budgets.unavailable_s:g}s",
                )
            wait_s = retry.delay(failures, exc.retry_after)
        else:
            failures = 0
            unavailable = None
            last = record
            if on_progress is not None and record.progress and record.progress.seq > last_seq:
                last_seq = record.progress.seq
                await _notify(on_progress, record.progress)
            if is_final(record.status):
                return record
            current = JobStatus.QUEUED if record.status is JobStatus.QUEUED else JobStatus.RUNNING
            if current is not phase:
                phase = current
                budget = budgets.queue_s if current is JobStatus.QUEUED else budgets.run_s
                phase_deadline = Deadline(budget, clock=clock)
            wait_s = interval_s

        if cancel is not None and cancel.cancelled and not cancel_sent:
            cancel_sent = True
            await _cancel_quietly(client, submission_key)

        phase_over = phase_deadline is not None and phase_deadline.expired()
        if phase_over or outer.expired():
            if protocol_error is not None:
                return await give_up(
                    JobStatus.FAILED,
                    ErrorCode.PROTOCOL_ERROR,
                    f"the backend kept reporting outside the contract: {protocol_error}",
                )
            if phase is JobStatus.QUEUED and phase_over:
                return await give_up(
                    JobStatus.TIMED_OUT,
                    ErrorCode.QUEUE_TIMEOUT,
                    f"still queued after {budgets.queue_s:g}s",
                )
            return await give_up(
                JobStatus.TIMED_OUT,
                ErrorCode.TIMEOUT,
                f"still running after the waiter's budget ({budgets.run_s:g}s to run, "
                f"{budgets.outer_s:g}s in all)",
            )
        await sleep(wait_s)
