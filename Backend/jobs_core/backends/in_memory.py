"""``InMemoryJobClient``: a ``JobClient`` that runs nothing, for tests.

It follows every ``submit`` rule of a real backend (see ``jobs_core.client``),
but no job ever moves by itself: the test moves it with the driver methods
(``start``, ``report``, ``finish``, ``report_raw_status``), between calls or
from the ``on_get`` hook, which runs before every ``get`` and so lets a test
script what a waiter sees on each read. ``unavailable`` and ``queue_capacity``
make it refuse the way a busy backend does.

It serves both kinds of request: a ``reusable`` one re-attaches to an earlier
try of its job, as MONAI does; any other one runs again, as a compute job does.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..budgets import DEFAULT_UNAVAILABLE_S, JobBudgets
from ..client import (
    BackendUnavailable,
    CancelSignal,
    JobConflict,
    JobNotFound,
    NoResult,
    ProgressListener,
    QueueFull,
    submit_with_retry,
    utc_now,
    wait_by_polling,
)
from ..contract import ErrorCode, JobProgress, JobRecord, JobRequest
from ..polling import RetryPolicy
from ..status import ACTIVE, JobStatus, is_final, parse_status


@dataclass
class _Try:
    request: JobRequest
    record: JobRecord
    raw_status: str | None = None  # set by report_raw_status: what get() reads instead


def _replace(record: JobRecord, **changes: Any) -> JobRecord:
    # JobRecord is frozen; building a new one re-runs its checks.
    fields = {name: getattr(record, name) for name in JobRecord.model_fields}
    fields.update(changes)
    return JobRecord(**fields)


class InMemoryJobClient:
    """A scriptable ``JobClient`` kept in a dict.

    queue_capacity   refuse a submit with ``QueueFull`` while this many tries are queued
    retry_after_s    the ``Retry-After`` hint it gives when it refuses
    poll_interval_s  how often ``wait`` reads the state
    clock, sleep     the monotonic clock and the sleep the waits use (fakes in tests)
    now              the wall clock stamped on records
    """

    def __init__(
        self,
        *,
        queue_capacity: int | None = None,
        retry_after_s: float | None = None,
        poll_interval_s: float = 1.0,
        retry: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        now: Callable[[], datetime] = utc_now,
        host: str = "in-memory",
    ) -> None:
        self.queue_capacity = queue_capacity
        self.retry_after_s = retry_after_s
        self.poll_interval_s = poll_interval_s
        self.retry = retry or RetryPolicy(jitter=False)
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self.host = host
        # True: every call refuses with BackendUnavailable, as a backend that is down.
        self.unavailable = False
        # Runs before every get(), with the try's own key: the test's script.
        self.on_get: Callable[[str], None] | None = None
        self.cancel_requests: list[str] = []
        self._tries: dict[str, _Try] = {}
        self._aliases: dict[str, str] = {}  # a reusing try's key -> the try it re-attached to

    # --- JobClient

    async def submit(
        self, request: JobRequest, *, unavailable_s: float = DEFAULT_UNAVAILABLE_S
    ) -> JobRecord:
        return await submit_with_retry(
            lambda: self._submit_once(request),
            unavailable_s=unavailable_s,
            retry=self.retry,
            clock=self.clock,
            sleep=self.sleep,
        )

    async def get(self, submission_key: str) -> JobRecord:
        self._refuse_if_unavailable()
        key = self._resolve(submission_key)
        if self.on_get is not None:
            self.on_get(key)
        entry = self._entry(key)
        if entry.raw_status is not None:
            parse_status(entry.raw_status)  # raises JobProtocolError for an unknown one
        return entry.record

    async def wait(
        self,
        submission_key: str,
        *,
        budgets: JobBudgets,
        on_progress: ProgressListener | None = None,
        cancel: CancelSignal | None = None,
    ) -> JobRecord:
        self._entry(self._resolve(submission_key))  # an unknown key fails at once
        return await wait_by_polling(
            self,
            submission_key,
            budgets=budgets,
            on_progress=on_progress,
            cancel=cancel,
            interval_s=self.poll_interval_s,
            retry=self.retry,
            clock=self.clock,
            sleep=self.sleep,
            now=self.now,
        )

    async def result(self, submission_key: str) -> dict[str, Any]:
        record = self._entry(self._resolve(submission_key)).record
        if record.status is not JobStatus.SUCCEEDED or record.result is None:
            raise NoResult(f"{submission_key} has no result: it is {record.status.value}")
        return dict(record.result)

    async def cancel(self, submission_key: str) -> JobRecord:
        self._refuse_if_unavailable()
        key = self._resolve(submission_key)
        entry = self._entry(key)
        self.cancel_requests.append(key)
        if entry.record.status is JobStatus.QUEUED:
            self.finish(key, JobStatus.CANCELLED, error_code=ErrorCode.CANCELLED, error="cancelled")
        elif entry.record.status is JobStatus.RUNNING:
            entry.record = _replace(entry.record, status=JobStatus.CANCELLING)
        return entry.record

    async def purge(self, submission_key: str) -> None:
        key = self._aliases.get(submission_key, submission_key)
        self._tries.pop(key, None)
        for alias in [alias for alias, target in self._aliases.items() if target == key]:
            del self._aliases[alias]
        self._aliases.pop(submission_key, None)

    async def list(self, run_id: str, *, active_only: bool = False) -> list[JobRecord]:
        return [
            entry.record
            for entry in self._tries.values()
            if entry.request.context.get("run_id") == run_id
            and (not active_only or entry.record.status in ACTIVE)
        ]

    # --- driver methods, for tests

    def start(self, submission_key: str, *, host: str | None = None) -> JobRecord:
        """Move a queued try to RUNNING."""
        entry = self._entry(self._resolve(submission_key))
        if entry.record.status is not JobStatus.QUEUED:
            raise ValueError(f"only a queued try can start; it is {entry.record.status.value}")
        entry.record = _replace(
            entry.record, status=JobStatus.RUNNING, started_at=self.now(), host=host or self.host
        )
        return entry.record

    def report(
        self, submission_key: str, current: int | float, total: int, label: str | None = None
    ) -> JobRecord:
        """Record a progress update; its ``seq`` is one more than the last."""
        entry = self._entry(self._resolve(submission_key))
        seq = entry.record.progress.seq + 1 if entry.record.progress else 0
        progress = JobProgress(seq=seq, current=current, total=total, label=label)
        entry.record = _replace(entry.record, progress=progress)
        return entry.record

    def finish(
        self,
        submission_key: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: ErrorCode | None = None,
        error: str | None = None,
    ) -> JobRecord:
        """End a try with a terminal status (and its code, for a failure)."""
        if not is_final(status):
            raise ValueError(f"finish needs a terminal status, got {status.value}")
        entry = self._entry(self._resolve(submission_key))
        entry.raw_status = None
        entry.record = _replace(
            entry.record,
            status=status,
            error_code=error_code,
            error=error,
            result=result,
            finished_at=self.now(),
        )
        return entry.record

    def report_raw_status(self, submission_key: str, raw: str | None) -> None:
        """Make ``get`` read ``raw`` as the status (``None`` stops it), as a backend
        that answers with a status string outside the contract."""
        self._entry(self._resolve(submission_key)).raw_status = raw

    # --- internals

    def _refuse_if_unavailable(self) -> None:
        if self.unavailable:
            raise BackendUnavailable("the backend is unavailable", retry_after=self.retry_after_s)

    def _resolve(self, submission_key: str) -> str:
        return self._aliases.get(submission_key, submission_key)

    def _entry(self, key: str) -> _Try:
        try:
            return self._tries[key]
        except KeyError:
            raise JobNotFound(f"no job with submission key {key!r}") from None

    async def _submit_once(self, request: JobRequest) -> JobRecord:
        self._refuse_if_unavailable()
        return self._admit(request)

    def _admit(self, request: JobRequest) -> JobRecord:
        same_job = [entry for entry in self._tries.values() if entry.record.id == request.job_id]
        for entry in same_job:
            if entry.record.input_hash != request.input_hash:
                raise JobConflict(
                    f"job {request.job_id} was submitted before with other inputs "
                    f"(attempt {entry.record.attempt}); a run cannot mix two versions"
                )

        key = self._resolve(request.submission_key)
        if key in self._tries:
            return self._tries[key].record

        if request.reusable:
            reusable = [
                entry
                for entry in same_job
                if entry.record.status is JobStatus.SUCCEEDED or entry.record.status in ACTIVE
            ]
            if reusable:
                latest = max(reusable, key=lambda entry: entry.record.attempt)
                self._aliases[request.submission_key] = latest.request.submission_key
                return latest.record

        if self.queue_capacity is not None:
            queued = sum(entry.record.status is JobStatus.QUEUED for entry in self._tries.values())
            if queued >= self.queue_capacity:
                raise QueueFull(
                    f"{queued} jobs are queued (capacity {self.queue_capacity})",
                    retry_after=self.retry_after_s,
                )

        record = JobRecord(
            id=request.job_id,
            status=JobStatus.QUEUED,
            error_code=None,
            attempt=request.attempt,
            input_hash=request.input_hash,
            queued_at=self.now(),
        )
        self._tries[request.submission_key] = _Try(request=request, record=record)
        return record
