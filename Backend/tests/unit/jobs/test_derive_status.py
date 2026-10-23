"""derive_status: one table row per rule, plus the cases where two tries share a job dir."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from jobs_core.contract import ErrorCode, JobRequest, make_job_id, make_submission_key
from jobs_core.derive import (
    DEFAULT_HEARTBEAT_TIMEOUT_S,
    DerivedStatus,
    QueueRow,
    derive_status,
    heartbeat_timeout_s,
)
from jobs_core.job_dir import PROCESS_FILE, REQUEST_FILE, write_result
from jobs_core.status import JobProtocolError, JobStatus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
TIMEOUT_S = 30.0
FRESH = NOW - timedelta(seconds=5)
STALE = NOW - timedelta(seconds=TIMEOUT_S + 1)

RUN_ID = "7d1f5c1e-0000-4000-8000-000000000001"
ARGS = {"folds": 5}
JOB_ID = make_job_id(RUN_ID, "train_classifier", ARGS)
KEY = make_submission_key(JOB_ID, 1)
OTHER_KEY = make_submission_key(JOB_ID, 2)
ATTEMPT_ID = "attempt-1"


def _row(status: str, **overrides: Any) -> QueueRow:
    fields: dict[str, Any] = {
        "submission_key": KEY,
        "status": status,
        "queued_at": NOW - timedelta(minutes=10),
        "started_at": NOW - timedelta(minutes=1),
        "worker_heartbeat_at": FRESH,
    }
    fields.update(overrides)
    return QueueRow(**fields)


def _request(attempt: int = 1, queue_timeout_s: float | None = 300.0) -> JobRequest:
    return JobRequest(
        job_id=JOB_ID,
        submission_key=make_submission_key(JOB_ID, attempt),
        attempt=attempt,
        job_name="train_classifier",
        job_version=1,
        args=ARGS,
        context={"run_id": RUN_ID},
        resource_class="compute.cpu",
        timeout_s=600.0,
        queue_timeout_s=queue_timeout_s,
        priority=0,
    )


def _process(heartbeat_at: datetime, submission_key: str = KEY) -> dict[str, Any]:
    return {
        "submission_key": submission_key,
        "host": "worker-1",
        "pid": 4242,
        "pgid": 4242,
        "heartbeat_at": heartbeat_at.isoformat(),
    }


@dataclass
class Case:
    """One rule: the queue row, the files in the job dir, and the expected answer."""

    row: QueueRow | None
    expected: tuple[JobStatus, ErrorCode | None] | None
    process: dict[str, Any] | None = None
    request: JobRequest | None = None
    result: dict[str, Any] | None = field(default=None)


SUCCEEDED_RESULT = {"status": JobStatus.SUCCEEDED, "result": {"auc": 0.8}}
OOM_RESULT = {"status": JobStatus.FAILED, "error_code": ErrorCode.OOM, "error": "MemoryError"}

CASES: dict[str, Case] = {
    # 1. result.json of this try wins, whatever the row says
    "result wins over doing": Case(
        _row("doing"), (JobStatus.SUCCEEDED, None), result=SUCCEEDED_RESULT
    ),
    "result wins over todo": Case(
        _row("todo"), (JobStatus.SUCCEEDED, None), result=SUCCEEDED_RESULT
    ),
    "result wins over failed": Case(
        _row("failed"), (JobStatus.FAILED, ErrorCode.OOM), result=OOM_RESULT
    ),
    "result wins without a row": Case(None, (JobStatus.SUCCEEDED, None), result=SUCCEEDED_RESULT),
    # 2. nothing knows it
    "no row, no result": Case(None, None),
    # 3. todo
    "todo is queued": Case(
        _row("todo", started_at=None, worker_heartbeat_at=None), (JobStatus.QUEUED, None)
    ),
    # 4. doing
    "doing with both heartbeats fresh": Case(
        _row("doing"), (JobStatus.RUNNING, None), process=_process(FRESH)
    ),
    "doing with an abort asked for": Case(
        _row("doing", abort_requested=True), (JobStatus.CANCELLING, None), process=_process(FRESH)
    ),
    "doing before the child is launched": Case(
        _row("doing", started_at=FRESH), (JobStatus.RUNNING, None)
    ),
    "doing, worker heartbeat stale": Case(
        _row("doing", worker_heartbeat_at=STALE),
        (JobStatus.INTERRUPTED, ErrorCode.INTERRUPTED),
        process=_process(FRESH),
    ),
    "doing, no worker heartbeat": Case(
        _row("doing", worker_heartbeat_at=None),
        (JobStatus.INTERRUPTED, ErrorCode.INTERRUPTED),
        process=_process(FRESH),
    ),
    "doing, job heartbeat stale": Case(
        _row("doing"), (JobStatus.INTERRUPTED, ErrorCode.INTERRUPTED), process=_process(STALE)
    ),
    "doing, never launched and started long ago": Case(
        _row("doing", started_at=STALE), (JobStatus.INTERRUPTED, ErrorCode.INTERRUPTED)
    ),
    "doing, stale abort is still interrupted": Case(
        _row("doing", abort_requested=True),
        (JobStatus.INTERRUPTED, ErrorCode.INTERRUPTED),
        process=_process(STALE),
    ),
    # 5. cancelled before it started / aborted while it ran
    "cancelled within its queue timeout": Case(
        _row("cancelled", queued_at=NOW - timedelta(seconds=10), started_at=None),
        (JobStatus.CANCELLED, ErrorCode.CANCELLED),
        request=_request(queue_timeout_s=300.0),
    ),
    "cancelled past its queue timeout": Case(
        _row("cancelled", queued_at=NOW - timedelta(seconds=301), started_at=None),
        (JobStatus.TIMED_OUT, ErrorCode.QUEUE_TIMEOUT),
        request=_request(queue_timeout_s=300.0),
    ),
    "cancelled with no queue timeout": Case(
        _row("cancelled", queued_at=NOW - timedelta(days=1), started_at=None),
        (JobStatus.CANCELLED, ErrorCode.CANCELLED),
        request=_request(queue_timeout_s=None),
    ),
    "cancelled with no request.json": Case(
        _row("cancelled", queued_at=NOW - timedelta(days=1), started_at=None),
        (JobStatus.CANCELLED, ErrorCode.CANCELLED),
    ),
    "aborted while it ran": Case(_row("aborted"), (JobStatus.CANCELLED, ErrorCode.CANCELLED)),
    # 6. ended without a result
    "failed without a result": Case(_row("failed"), (JobStatus.FAILED, ErrorCode.ERROR)),
    "succeeded without a result": Case(_row("succeeded"), (JobStatus.FAILED, ErrorCode.ERROR)),
}


def _prepare(job_dir: Path, case: Case) -> None:
    if case.process is not None:
        (job_dir / PROCESS_FILE).write_text(json.dumps(case.process), encoding="utf-8")
    if case.request is not None:
        (job_dir / REQUEST_FILE).write_text(case.request.model_dump_json(), encoding="utf-8")
    if case.result is not None:
        write_result(
            job_dir, submission_key=KEY, attempt_id=ATTEMPT_ID, finished_at=NOW, **case.result
        )


def _derive(job_dir: Path, row: QueueRow | None) -> DerivedStatus | None:
    return derive_status(
        row, job_dir, submission_key=KEY, attempt_id=ATTEMPT_ID, now=NOW, timeout_s=TIMEOUT_S
    )


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_rule(tmp_path: Path, case: Case) -> None:
    _prepare(tmp_path, case)

    derived = _derive(tmp_path, case.row)

    if case.expected is None:
        assert derived is None
    else:
        assert derived is not None
        assert (derived.status, derived.error_code) == case.expected
        # A failed status always says why; a moving or successful one never does.
        assert (derived.error is not None) == (derived.error_code is not None)


def test_the_deciding_result_is_returned(tmp_path: Path) -> None:
    written = write_result(
        tmp_path,
        submission_key=KEY,
        attempt_id=ATTEMPT_ID,
        status=JobStatus.SUCCEEDED,
        finished_at=NOW,
        result={"auc": 0.8},
    )

    derived = _derive(tmp_path, _row("doing"))
    assert derived is not None and derived.result == written


# --- two tries in one job dir


@pytest.mark.parametrize(
    ("submission_key", "attempt_id"),
    [(OTHER_KEY, ATTEMPT_ID), (KEY, "attempt-2"), (OTHER_KEY, "attempt-2")],
    ids=["other key", "other attempt id", "both other"],
)
def test_a_result_of_another_try_is_ignored(
    tmp_path: Path, submission_key: str, attempt_id: str
) -> None:
    write_result(
        tmp_path,
        submission_key=submission_key,
        attempt_id=attempt_id,
        status=JobStatus.SUCCEEDED,
        finished_at=NOW,
        result={"auc": 0.8},
    )

    assert _derive(tmp_path, _row("todo")) == DerivedStatus(JobStatus.QUEUED)
    assert _derive(tmp_path, None) is None


def test_a_stale_process_file_of_another_try_does_not_interrupt_this_one(tmp_path: Path) -> None:
    # The earlier try's child died long ago; this try was just taken by a worker.
    (tmp_path / PROCESS_FILE).write_text(
        json.dumps(_process(STALE, submission_key=OTHER_KEY)), encoding="utf-8"
    )

    derived = _derive(tmp_path, _row("doing", started_at=FRESH))
    assert derived == DerivedStatus(JobStatus.RUNNING)


def test_a_request_of_another_try_does_not_decide_a_queue_timeout(tmp_path: Path) -> None:
    (tmp_path / REQUEST_FILE).write_text(
        _request(attempt=2, queue_timeout_s=1.0).model_dump_json(), encoding="utf-8"
    )

    derived = _derive(tmp_path, _row("cancelled", started_at=None))
    assert derived is not None and derived.status is JobStatus.CANCELLED


def test_progress_is_not_a_heartbeat(tmp_path: Path) -> None:
    # Fresh progress, stale heartbeat: a job can report progress from a thread
    # while its supervisor loop is stuck, so only the heartbeat counts.
    (tmp_path / "progress.json").write_text(
        json.dumps({"seq": 9, "current": 3, "total": 4, "label": None}), encoding="utf-8"
    )
    (tmp_path / PROCESS_FILE).write_text(json.dumps(_process(STALE)), encoding="utf-8")

    derived = _derive(tmp_path, _row("doing"))
    assert derived is not None and derived.status is JobStatus.INTERRUPTED


# --- refusals


def test_an_unknown_queue_status_is_a_protocol_error(tmp_path: Path) -> None:
    with pytest.raises(JobProtocolError, match="unknown queue status 'paused'"):
        _derive(tmp_path, _row("paused"))


def test_a_broken_result_file_is_a_protocol_error(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text("{truncated", encoding="utf-8")

    with pytest.raises(JobProtocolError):
        _derive(tmp_path, _row("doing"))


def test_a_row_of_another_try_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="queue row is for"):
        _derive(tmp_path, _row("todo", submission_key=OTHER_KEY))


def test_a_job_dir_that_does_not_exist_yet_is_read_as_empty(tmp_path: Path) -> None:
    assert _derive(tmp_path / "missing", _row("todo")) == DerivedStatus(JobStatus.QUEUED)


def test_extra_view_columns_are_ignored() -> None:
    row = QueueRow.model_validate(
        {
            "submission_key": KEY,
            "status": "todo",
            "queued_at": NOW,
            "queue": "compute.cpu",
            "worker_id": 7,
        }
    )
    assert row.status == "todo"


# --- the heartbeat timeout setting


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_HEARTBEAT_TIMEOUT_S),
        ("45", 45.0),
        ("2.5", 2.5),
        ("0", DEFAULT_HEARTBEAT_TIMEOUT_S),
        ("-3", DEFAULT_HEARTBEAT_TIMEOUT_S),
        ("soon", DEFAULT_HEARTBEAT_TIMEOUT_S),
        ("nan", DEFAULT_HEARTBEAT_TIMEOUT_S),
        ("inf", DEFAULT_HEARTBEAT_TIMEOUT_S),
    ],
)
def test_heartbeat_timeout_setting(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float
) -> None:
    if raw is None:
        monkeypatch.delenv("JOB_HEARTBEAT_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("JOB_HEARTBEAT_TIMEOUT_S", raw)

    assert heartbeat_timeout_s() == expected
