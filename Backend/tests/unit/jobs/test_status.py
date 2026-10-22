"""Job statuses (merged plan, Phase 5.1 step 2)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jobs_core.status import JobStatus

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
    # The MONAI Label image runs 3.10 and must share these strings (J4).
    source = (BACKEND / "jobs_core" / "status.py").read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 10))
    # Syntax alone misses names added after 3.10; StrEnum is the one that tempts.
    assert "StrEnum" not in source.replace("not ``StrEnum``", "")
