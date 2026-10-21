"""Job ids and submission keys (merged plan, Phase 5.1 step 1)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from jobs_core.contract import ErrorCode, canonical_json, make_job_id, make_submission_key

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
