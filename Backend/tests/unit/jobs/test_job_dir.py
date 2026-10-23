"""The job dir readers and the atomic result write."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobs_core import job_dir
from jobs_core.contract import ErrorCode, JobProgress
from jobs_core.job_dir import (
    PROCESS_FILE,
    PROGRESS_FILE,
    REQUEST_FILE,
    RESULT_FILE,
    ProcessInfo,
    read_process,
    read_progress,
    read_request,
    read_result,
    write_result,
)
from jobs_core.status import JobProtocolError, JobStatus

pytestmark = pytest.mark.unit

FINISHED = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
KEY = "a" * 64 + ".1"


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _succeed(directory: Path, **overrides: object) -> job_dir.JobResultFile:
    fields: dict[str, object] = {
        "submission_key": KEY,
        "attempt_id": "attempt-1",
        "status": JobStatus.SUCCEEDED,
        "finished_at": FINISHED,
        "result": {"rows": 3},
    }
    fields.update(overrides)
    return write_result(directory, **fields)  # type: ignore[arg-type]


# --- write_result


def test_a_written_result_reads_back_the_same(tmp_path: Path) -> None:
    written = _succeed(tmp_path)

    assert read_result(tmp_path) == written
    stored = json.loads((tmp_path / RESULT_FILE).read_text(encoding="utf-8"))
    assert stored["submission_key"] == KEY
    assert stored["attempt_id"] == "attempt-1"
    assert stored["status"] == "SUCCEEDED"


def test_a_result_is_stamped_with_its_try(tmp_path: Path) -> None:
    result = _succeed(tmp_path)

    assert result.belongs_to(KEY, "attempt-1")
    assert not result.belongs_to(KEY, "attempt-2")
    assert not result.belongs_to("b" * 64 + ".1", "attempt-1")


def test_a_failed_result_carries_its_code(tmp_path: Path) -> None:
    _succeed(
        tmp_path,
        status=JobStatus.FAILED,
        error_code=ErrorCode.OOM,
        error="MemoryError",
        result=None,
    )

    result = read_result(tmp_path)
    assert result is not None
    assert (result.status, result.error_code) == (JobStatus.FAILED, ErrorCode.OOM)


def test_writing_again_replaces_the_result_and_leaves_no_temp_file(tmp_path: Path) -> None:
    _succeed(tmp_path)
    _succeed(tmp_path, submission_key="b" * 64 + ".2", attempt_id="attempt-2")

    result = read_result(tmp_path)
    assert result is not None and result.belongs_to("b" * 64 + ".2", "attempt-2")
    assert sorted(path.name for path in tmp_path.iterdir()) == [RESULT_FILE]


def test_a_failed_rename_keeps_the_old_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _succeed(tmp_path)

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(job_dir.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        _succeed(tmp_path, attempt_id="attempt-2")

    assert read_result(tmp_path) == old
    assert sorted(path.name for path in tmp_path.iterdir()) == [RESULT_FILE]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": JobStatus.RUNNING}, "terminal status"),
        ({"status": JobStatus.QUEUED}, "terminal status"),
        ({"status": JobStatus.FAILED}, "needs an error_code"),
        ({"status": JobStatus.FAILED, "error_code": ErrorCode.TIMEOUT}, "needs an error_code"),
        ({"error_code": ErrorCode.ERROR}, "carries no error_code"),
        ({"result_ref": "big.json"}, "not both"),
        ({"result": None, "result_ref": "../elsewhere.json"}, "plain file name"),
        ({"result": {"x": float("nan")}}, "JSON only"),
        ({"finished_at": datetime(2026, 10, 23, 12, 0)}, "timezone"),
        ({"attempt_id": ""}, "at least 1 character"),
    ],
)
def test_a_bad_result_is_refused_before_touching_the_disk(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _succeed(tmp_path, **overrides)

    assert list(tmp_path.iterdir()) == []


def test_the_temp_file_is_hidden_next_to_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A rename is atomic only within one file system, so the temp file must
    # sit in the job dir itself; the dot keeps it out of a normal listing.
    seen: list[Path] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        seen.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(job_dir.os, "replace", spy)
    _succeed(tmp_path)

    assert seen[0].parent == tmp_path
    assert seen[0].name.startswith(".result.json.")
    assert seen[0].name.endswith(".tmp")


# --- readers


@pytest.mark.parametrize("reader", [read_request, read_progress, read_process, read_result])
def test_a_missing_file_reads_as_none(tmp_path: Path, reader: object) -> None:
    assert reader(tmp_path) is None  # type: ignore[operator]


@pytest.mark.parametrize(
    ("name", "reader"),
    [
        (REQUEST_FILE, read_request),
        (PROGRESS_FILE, read_progress),
        (PROCESS_FILE, read_process),
        (RESULT_FILE, read_result),
    ],
)
@pytest.mark.parametrize("content", ["{not json", '{"unexpected": 1}', ""])
def test_a_broken_file_is_a_protocol_error(
    tmp_path: Path, name: str, reader: object, content: str
) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")

    with pytest.raises(JobProtocolError, match=name):
        reader(tmp_path)  # type: ignore[operator]


def test_a_result_with_an_unknown_status_is_a_protocol_error(tmp_path: Path) -> None:
    _succeed(tmp_path)
    stored = json.loads((tmp_path / RESULT_FILE).read_text(encoding="utf-8"))
    _write(tmp_path / RESULT_FILE, {**stored, "status": "DONE"})

    with pytest.raises(JobProtocolError):
        read_result(tmp_path)


def test_process_and_progress_files_read_back(tmp_path: Path) -> None:
    _write(
        tmp_path / PROCESS_FILE,
        {
            "submission_key": KEY,
            "host": "worker-1",
            "pid": 4242,
            "pgid": 4242,
            "heartbeat_at": "2026-10-23T12:00:00+00:00",
        },
    )
    _write(tmp_path / PROGRESS_FILE, {"seq": 2, "current": 1, "total": 4, "label": "fold 1"})

    assert read_process(tmp_path) == ProcessInfo(
        submission_key=KEY, host="worker-1", pid=4242, pgid=4242, heartbeat_at=FINISHED
    )
    assert read_progress(tmp_path) == JobProgress(seq=2, current=1, total=4, label="fold 1")
