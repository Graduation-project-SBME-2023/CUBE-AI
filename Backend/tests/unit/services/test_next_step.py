"""The Application API's next-step rule: what the Executor does next in a session."""

from __future__ import annotations

import pytest

from application_api.core.execution_rules import ActiveAttemptFacts, NextStep, NodeFacts, next_step

pytestmark = pytest.mark.unit


def facts(node_id: str, run_status=None, *, holds=False, approval="none", released=False) -> NodeFacts:
    return NodeFacts(node_id=node_id, approval=approval, start_released=released,
                     run_status=run_status, holds=holds)


def suspended(kind: str, *, answered=False, node_id="a") -> ActiveAttemptFacts:
    return ActiveAttemptFacts(state="suspended", suspension_kind=kind, answered=answered, node_id=node_id)


def test_the_first_node_without_a_completed_run_starts_next():
    nodes = [facts("a", "completed"), facts("b"), facts("c")]
    assert next_step("active", nodes, None) == NextStep("start", "b")


def test_nothing_happens_in_a_paused_session_or_an_empty_graph():
    assert next_step("paused", [facts("a")], None) is None
    assert next_step("active", [], None) is None
    assert next_step("active", [facts("a", "completed")], None) is None  # every node is done


def test_an_active_attempt_blocks_every_start():
    running = ActiveAttemptFacts(state="running", suspension_kind=None, answered=False, node_id=None)
    assert next_step("active", [facts("a")], running) is None


def test_a_hold_or_a_failed_run_stops_the_graph():
    assert next_step("active", [facts("a", "completed", holds=True), facts("b")], None) is None
    assert next_step("active", [facts("a", "failed"), facts("b")], None) is None
    assert next_step("active", [facts("a", "cancelled"), facts("b")], None) is None


def test_a_node_the_user_must_release_waits_for_the_release():
    assert next_step("active", [facts("a", approval="before")], None) == NextStep("await_release", "a")
    released = [facts("a", approval="before", released=True)]
    assert next_step("active", released, None) == NextStep("start", "a")


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        (suspended("ask_user"), None),  # not answered yet
        (suspended("ask_user", answered=True), NextStep("resume", "a")),
        (suspended("tool_approval", answered=True), NextStep("resume", "a")),
        (suspended("pause"), NextStep("resume", "a")),  # the session is active again
        (suspended("ask_user", answered=True, node_id=None), None),  # its run was superseded
    ],
)
def test_a_suspended_attempt_resumes_once_it_may_continue(active, expected):
    assert next_step("active", [facts("a")], active) == expected


def test_a_paused_session_resumes_nothing():
    assert next_step("paused", [facts("a")], suspended("pause")) is None
