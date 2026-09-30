"""error_result and RunAccepted (merged plan, Phase 1.1 step 7)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_system.toolkit.results import NodeStatus, RunAccepted, error_result, is_error_result

pytestmark = pytest.mark.unit


# --- error_result


def test_error_envelope():
    assert error_result("not_found", "Patient p1 does not exist.") == {
        "status": "error",
        "error_code": "not_found",
        "error_message": "Patient p1 does not exist.",
    }


def test_error_extra_fields_are_appended():
    result = error_result("legacy_model_format", "Use refit_model.", model_result_id="m1")

    assert result["model_result_id"] == "m1"
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("code", ["", "NotFound", "not-found", "1_error", None])
def test_error_code_is_snake_case(code):
    with pytest.raises(ValueError, match="error code"):
        error_result(code, "message")


@pytest.mark.parametrize("message", ["", "   ", None])
def test_error_needs_a_message(message):
    with pytest.raises(ValueError, match="message"):
        error_result("timeout", message)


@pytest.mark.parametrize("key", ["status", "error_code", "error_message"])
def test_extra_fields_cannot_override_the_envelope(key):
    with pytest.raises(ValueError, match="override the envelope"):
        error_result("timeout", "Took too long.", **{key: "x"})


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (error_result("timeout", "Took too long."), True),
        ({"status": "error", "message": "legacy shape"}, False),
        ({"status": "ok"}, False),
        ("Error: plain text", False),
        (None, False),
    ],
)
def test_is_error_result(result, expected):
    assert is_error_result(result) is expected


# --- RunAccepted


def test_accepted_run():
    accepted = RunAccepted(node_id="n1", run_id="r1", status="queued", graph_version=4)

    assert accepted.status is NodeStatus.QUEUED
    assert accepted.to_result() == {
        "node_id": "n1",
        "run_id": "r1",
        "status": "queued",
        "graph_version": 4,
        "approval_id": None,
    }


def test_awaiting_approval_carries_the_approval_and_no_run():
    accepted = RunAccepted(
        node_id="n1", status="awaiting_approval", graph_version=4, approval_id="a1"
    )

    assert accepted.run_id is None
    assert accepted.to_result()["approval_id"] == "a1"


def test_a_cache_hit_is_accepted_as_done():
    assert RunAccepted(node_id="n1", run_id="r0", status="done", graph_version=2).status == "done"


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"status": "awaiting_approval"}, "needs an approval_id"),
        ({"status": "running", "approval_id": "a1"}, "only set while awaiting approval"),
        ({"status": "finished"}, "status"),
        ({"graph_version": -1}, "graph_version"),
        ({"node_id": ""}, "node_id"),
        ({"session_id": "s1"}, "session_id"),
    ],
)
def test_malformed_run_accepted_is_refused(fields, match):
    base = {"node_id": "n1", "run_id": "r1", "status": "running", "graph_version": 1}

    with pytest.raises(ValidationError, match=match):
        RunAccepted(**(base | fields))


def test_run_accepted_is_immutable():
    accepted = RunAccepted(node_id="n1", status="pending", graph_version=1)

    with pytest.raises(ValidationError):
        accepted.status = NodeStatus.RUNNING  # type: ignore[misc]


def test_node_statuses_match_the_plan():
    assert {status.value for status in NodeStatus} == {
        "pending", "ready", "awaiting_approval", "queued", "running", "waiting_for_user",
        "done", "failed", "cancelled", "invalidated", "held", "removed",
    }
