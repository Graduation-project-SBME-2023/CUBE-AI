"""Job ids, submission keys, error codes and the job request (merged plan, Phase 5.1 step 1)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jobs_core.contract import (
    ERROR_CODES_BY_STATUS,
    ErrorCode,
    JobProgress,
    JobRecord,
    JobRequest,
    canonical_json,
    make_input_hash,
    make_job_id,
    make_submission_key,
)
from jobs_core.status import JobStatus

pytestmark = pytest.mark.unit


# --- ErrorCode


def test_error_codes_are_the_documented_set() -> None:
    assert {code.value for code in ErrorCode} == {
        "error",
        "timeout",
        "queue_timeout",
        "oom",
        "interrupted",
        "cancelled",
        "run_not_running",
        "resource_unavailable",
    }


def test_each_name_is_its_value_in_capitals() -> None:
    for code in ErrorCode:
        assert code.name == code.value.upper()
        assert re.fullmatch(r"[a-z_]+", code.value)


def test_an_error_code_is_its_string() -> None:
    assert ErrorCode.OOM == "oom"
    assert json.dumps({"code": ErrorCode.OOM}) == '{"code": "oom"}'
    assert ErrorCode("queue_timeout") is ErrorCode.QUEUE_TIMEOUT


def test_an_unknown_code_is_refused() -> None:
    with pytest.raises(ValueError):
        ErrorCode("gpu_exploded")


# --- canonical_json


def test_key_order_does_not_change_the_encoding() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_encoding_is_frozen() -> None:
    # Golden bytes: if this fails, every job id has changed.
    value = {"b": [1, 2.5, True, None], "a": "ص"}
    assert canonical_json(value) == '{"a":"ص","b":[1,2.5,true,null]}'.encode("utf-8")


def test_nan_is_refused() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_a_value_that_is_not_json_is_refused() -> None:
    with pytest.raises(TypeError):
        canonical_json({"path": Path("a.csv")})


# --- make_job_id


def test_job_id_is_the_documented_hash() -> None:
    expected = hashlib.sha256(b'run-1|train_classifier|{"folds":5}').hexdigest()
    assert make_job_id("run-1", "train_classifier", {"folds": 5}) == expected


def test_job_id_ignores_key_order() -> None:
    first = make_job_id("run-1", "train_classifier", {"folds": 5, "seed": 1})
    second = make_job_id("run-1", "train_classifier", {"seed": 1, "folds": 5})
    assert first == second


@pytest.mark.parametrize(
    "other",
    [
        ("run-2", "train_classifier", {"folds": 5}),
        ("run-1", "train_regressor", {"folds": 5}),
        ("run-1", "train_classifier", {"folds": 10}),
    ],
)
def test_job_id_changes_with_each_part(other: tuple[str, str, dict]) -> None:
    assert make_job_id(*other) != make_job_id("run-1", "train_classifier", {"folds": 5})


# --- make_submission_key


def test_submission_key_is_job_id_dot_attempt() -> None:
    assert make_submission_key("abc", 2) == "abc.2"


@pytest.mark.parametrize("attempt_number", [0, -1])
def test_attempt_numbers_start_at_one(attempt_number: int) -> None:
    with pytest.raises(ValueError):
        make_submission_key("abc", attempt_number)


# --- JobRequest

RUN_ID = "7d1f5c1e-0000-4000-8000-000000000001"


def _request_fields(**overrides: Any) -> dict[str, Any]:
    """A valid request; ids are recomputed unless an override sets them."""
    fields: dict[str, Any] = {
        "attempt": 1,
        "job_name": "train_classifier",
        "job_version": 1,
        "args": {"folds": 5, "target": "label"},
        "context": {"session_id": "s-1", "caller": "node", "run_id": RUN_ID},
        "resource_class": "compute.cpu",
        "timeout_s": 600.0,
        "queue_timeout_s": 60.0,
        "priority": 0,
    }
    fields.update(overrides)
    # Not setdefault: its default is computed even when the key is there.
    if "job_id" not in fields:
        fields["job_id"] = make_job_id(
            fields["context"]["run_id"], fields["job_name"], fields["args"]
        )
    if "submission_key" not in fields:
        fields["submission_key"] = make_submission_key(fields["job_id"], fields["attempt"])
    return fields


def test_a_valid_request_is_accepted() -> None:
    request = JobRequest(**_request_fields(attempt=2))
    assert request.submission_key == f"{request.job_id}.2"


def test_a_request_survives_a_json_round_trip() -> None:
    request = JobRequest(**_request_fields())
    assert JobRequest.model_validate_json(request.model_dump_json()) == request


def test_a_request_cannot_be_changed() -> None:
    request = JobRequest(**_request_fields())
    with pytest.raises(ValidationError):
        request.attempt = 2  # type: ignore[misc]


def test_queue_timeout_may_be_none() -> None:
    assert JobRequest(**_request_fields(queue_timeout_s=None)).queue_timeout_s is None


def test_a_request_without_queue_timeout_is_refused() -> None:
    # Required even though None is allowed: the builder must say "no limit" on purpose.
    fields = _request_fields()
    del fields["queue_timeout_s"]
    with pytest.raises(ValidationError):
        JobRequest(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown_field": 1},
        {"attempt": 0},
        {"attempt": "1"},  # strict: no conversion from a string
        {"attempt": True},  # strict: a bool is not a number
        {"job_version": 0},
        {"job_name": "toolpacks.modeling:train"},  # an import path, not a tool name
        {"job_name": "x" * 65},
        {"resource_class": "compute.tpu"},
        {"timeout_s": 0},
        {"timeout_s": float("inf")},
        {"queue_timeout_s": 0},
        {"priority": -1},
        {"priority": 10},
        {"job_id": "ABC"},
    ],
    ids=lambda overrides: next(iter(overrides)),
)
def test_a_bad_field_is_refused(overrides: dict[str, Any]) -> None:
    # Swapped in after the ids are computed: the helpers would refuse some of these first.
    fields = _request_fields() | overrides
    with pytest.raises(ValidationError) as caught:
        JobRequest(**fields)
    [field_name] = overrides
    assert {error["loc"][0] for error in caught.value.errors()} == {field_name}


@pytest.mark.parametrize(
    "args",
    [{"path": Path("a.csv")}, {"x": float("nan")}],
    ids=["path", "nan"],
)
def test_args_must_be_json(args: dict[str, Any]) -> None:
    # job_id is set by hand: make_job_id itself refuses these args.
    with pytest.raises(ValidationError, match="must be JSON only"):
        JobRequest(**_request_fields(args=args, job_id="0" * 64))


def test_context_must_be_json() -> None:
    context = {"run_id": RUN_ID, "workspace_dir": Path("/storage")}
    with pytest.raises(ValidationError, match="must be JSON only"):
        JobRequest(**_request_fields(context=context))


@pytest.mark.parametrize("run_id", [None, ""])
def test_context_must_name_the_run(run_id: str | None) -> None:
    context = {"session_id": "s-1", "run_id": run_id}
    with pytest.raises(ValidationError, match="context.run_id"):
        JobRequest(**_request_fields(context=context, job_id="0" * 64))


def test_job_id_must_match_the_args() -> None:
    stale_id = make_job_id(RUN_ID, "train_classifier", {"folds": 5, "target": "label"})
    with pytest.raises(ValidationError, match="job_id is not"):
        JobRequest(**_request_fields(args={"folds": 10, "target": "label"}, job_id=stale_id))


def test_submission_key_must_match_the_attempt() -> None:
    fields = _request_fields(attempt=2)
    fields["submission_key"] = make_submission_key(fields["job_id"], 1)
    with pytest.raises(ValidationError, match="submission_key is not"):
        JobRequest(**fields)


def test_context_job_id_must_be_this_job() -> None:
    context = {"session_id": "s-1", "run_id": RUN_ID, "job_id": "f" * 64}
    with pytest.raises(ValidationError, match="different job"):
        JobRequest(**_request_fields(context=context))


def test_request_input_hash_is_derived_not_stored() -> None:
    request = JobRequest(**_request_fields())
    assert request.input_hash == make_input_hash("train_classifier", 1, request.args)
    assert "input_hash" not in request.model_dump()


# --- make_input_hash


def test_input_hash_ignores_key_order() -> None:
    assert make_input_hash("t", 1, {"a": 1, "b": 2}) == make_input_hash("t", 1, {"b": 2, "a": 1})


@pytest.mark.parametrize(
    "other",
    [("u", 1, {"a": 1}), ("t", 2, {"a": 1}), ("t", 1, {"a": 2})],
    ids=["name", "version", "args"],
)
def test_input_hash_changes_with_each_part(other: tuple[str, int, dict]) -> None:
    assert make_input_hash(*other) != make_input_hash("t", 1, {"a": 1})


# --- JobProgress


def test_progress_has_the_toolkit_payload_fields_plus_seq() -> None:
    progress = JobProgress(seq=3, current=2, total=5, label="fold 2/5")
    assert progress.model_dump() == {"seq": 3, "current": 2, "total": 5, "label": "fold 2/5"}


@pytest.mark.parametrize(
    "overrides",
    [{"seq": -1}, {"current": -0.5}, {"total": -1}, {"current": float("nan")}],
    ids=["seq", "current", "total", "nan"],
)
def test_bad_progress_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobProgress(**({"seq": 0, "current": 0, "total": 1} | overrides))


# --- JobRecord

HASH = make_input_hash("train_classifier", 1, {"folds": 5})


def _record_fields(**overrides: Any) -> dict[str, Any]:
    """A queued record, in the shape it arrives in over HTTP: plain strings."""
    fields: dict[str, Any] = {
        "id": "a" * 64,
        "status": "QUEUED",
        "error_code": None,
        "attempt": 1,
        "input_hash": HASH,
        "queued_at": "2026-10-22T10:00:00+00:00",
    }
    return fields | overrides


def test_a_record_is_read_from_strings() -> None:
    record = JobRecord(**_record_fields())
    assert record.status is JobStatus.QUEUED
    assert record.queued_at.tzinfo is not None


def test_a_record_survives_a_json_round_trip() -> None:
    record = JobRecord(
        **_record_fields(
            status="RUNNING",
            started_at="2026-10-22T10:00:05+00:00",
            progress={"seq": 1, "current": 1, "total": 5, "label": None},
            host="worker-1",
        )
    )
    assert JobRecord.model_validate_json(record.model_dump_json()) == record


def test_a_finished_record_holds_its_result() -> None:
    record = JobRecord(
        **_record_fields(
            status="SUCCEEDED",
            started_at="2026-10-22T10:00:05+00:00",
            finished_at="2026-10-22T10:01:00+00:00",
            result={"status": "success", "auc": 0.91},
        )
    )
    assert record.result == {"status": "success", "auc": 0.91}


@pytest.mark.parametrize(
    ("status", "code"),
    [(status, code) for status, codes in ERROR_CODES_BY_STATUS.items() for code in codes],
)
def test_each_failed_status_takes_its_codes(status: JobStatus, code: ErrorCode) -> None:
    assert JobRecord(**_record_fields(status=status.value, error_code=code.value)).error_code is code


def test_every_error_code_belongs_to_a_status() -> None:
    assert set().union(*ERROR_CODES_BY_STATUS.values()) == set(ErrorCode)


@pytest.mark.parametrize("status", list(ERROR_CODES_BY_STATUS))
def test_a_failed_status_needs_a_code(status: JobStatus) -> None:
    with pytest.raises(ValidationError, match="needs an error_code"):
        JobRecord(**_record_fields(status=status.value))


@pytest.mark.parametrize(
    ("status", "code"),
    [("FAILED", "cancelled"), ("TIMED_OUT", "oom"), ("INTERRUPTED", "timeout")],
)
def test_a_code_from_another_status_is_refused(status: str, code: str) -> None:
    with pytest.raises(ValidationError, match="needs an error_code"):
        JobRecord(**_record_fields(status=status, error_code=code))


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "CANCELLING", "SUCCEEDED"])
def test_a_status_that_did_not_fail_carries_no_code(status: str) -> None:
    with pytest.raises(ValidationError, match="carries no error_code"):
        JobRecord(**_record_fields(status=status, error_code="error"))


def test_result_and_result_ref_are_exclusive() -> None:
    with pytest.raises(ValidationError, match="not both"):
        JobRecord(**_record_fields(status="SUCCEEDED", result={}, result_ref="result.bin"))


@pytest.mark.parametrize(
    ("times", "field_name"),
    [
        ({"started_at": "2026-10-22T09:59:00+00:00"}, "started_at"),
        (
            {"started_at": "2026-10-22T10:05:00+00:00", "finished_at": "2026-10-22T10:01:00+00:00"},
            "finished_at",
        ),
        ({"finished_at": "2026-10-22T09:00:00+00:00"}, "finished_at"),
    ],
    ids=["started-before-queued", "finished-before-started", "finished-before-queued"],
)
def test_times_go_forward(times: dict[str, str], field_name: str) -> None:
    with pytest.raises(ValidationError, match=f"{field_name} is before"):
        JobRecord(**_record_fields(**times))


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown_field": 1},
        {"id": ""},
        {"status": "succeeded"},  # the values are capitals, as MONAI writes them
        {"status": "PAUSED"},
        {"error_code": "gpu_exploded"},
        {"attempt": 0},
        {"attempt": "1"},
        {"input_hash": "xyz"},
        {"queued_at": "2026-10-22T10:00:00"},  # no time zone
        {"result": {"x": float("nan")}},
    ],
    ids=lambda overrides: f"{next(iter(overrides))}={next(iter(overrides.values()))!r}",
)
def test_a_bad_record_field_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as caught:
        JobRecord(**_record_fields(**overrides))
    [field_name] = overrides
    assert {error["loc"][0] for error in caught.value.errors()} == {field_name}
