"""Job statuses, the terminal and active sets, and the strict status parsing."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jobs_core.status import (
    ACTIVE,
    TERMINAL,
    JobProtocolError,
    JobStatus,
    is_final,
    parse_status,
)

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[3]


def test_statuses_are_the_documented_set() -> None:
    assert [status.value for status in JobStatus] == [
        "QUEUED",
        "RUNNING",
        "CANCELLING",
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
        "CANCELLED",
        "INTERRUPTED",
    ]


def test_a_status_is_its_string() -> None:
    assert JobStatus.QUEUED == "QUEUED"
    assert json.dumps({"status": JobStatus.QUEUED}) == '{"status": "QUEUED"}'


def test_status_module_parses_as_python_3_10() -> None:
    # The MONAI Label image runs 3.10 and must share these strings.
    source = (BACKEND / "jobs_core" / "status.py").read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 10))
    # Syntax alone misses names added after 3.10; StrEnum is the one that tempts.
    assert "StrEnum" not in source.replace("not ``StrEnum``", "")


# --- TERMINAL / ACTIVE


def test_every_status_is_either_terminal_or_active() -> None:
    assert TERMINAL | ACTIVE == set(JobStatus)
    assert not TERMINAL & ACTIVE


def test_the_active_statuses_are_the_ones_still_moving() -> None:
    assert ACTIVE == {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING}


# --- is_final


@pytest.mark.parametrize("status", list(JobStatus))
def test_is_final_matches_the_terminal_set(status: JobStatus) -> None:
    assert is_final(status) is (status in TERMINAL)


@pytest.mark.parametrize("raw", ["SUCCEEDED", "QUEUED", "DONE", "", None, 1])
def test_is_final_refuses_anything_but_a_status(raw: object) -> None:
    # Even a known string: it must be parsed first, so an unknown one is
    # refused at the boundary and never read as final.
    with pytest.raises(TypeError, match="parse_status"):
        is_final(raw)  # type: ignore[arg-type]


# --- parse_status


@pytest.mark.parametrize("status", list(JobStatus))
def test_parse_status_reads_every_known_string(status: JobStatus) -> None:
    assert parse_status(status.value) is status
    assert parse_status(status) is status


@pytest.mark.parametrize("raw", ["DONE", "succeeded", "", " QUEUED", None, 3])
def test_parse_status_refuses_an_unknown_value(raw: object) -> None:
    with pytest.raises(JobProtocolError, match="unknown job status"):
        parse_status(raw)
