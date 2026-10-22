"""The pure graph-change rule (application_api/core/graph_content.py) and the
execution rules (core/execution_rules.py), without a database."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from application_api.core.execution_rules import (
    ActivityFacts,
    LatestAttempt,
    RunOutputs,
    derive_activity,
    retry_verdict,
    unknown_output_tables,
    violated_expectations,
)
from application_api.core.graph_content import (
    PLAN_MODE,
    SUPERSEDES_COMPLETED,
    ChangeContent,
    LiveNode,
    node_columns,
    plan_apply,
)

pytestmark = pytest.mark.unit


def _def(title: str, **extra) -> dict:
    return {"node_id": str(uuid.uuid4()), "kind": "tool", "ref": "run_modeling", "ref_version": "1",
            "title": title, "params": {}, **extra}


def _live(defn: dict, position: int, run_status=None, holds=False, hold="expectation_violated",
          released=None) -> LiveNode:
    columns = node_columns(ChangeContent.model_validate({"v": 1, "nodes": [defn]}).nodes[0])
    return LiveNode(
        node_id=uuid.UUID(defn["node_id"]), position=position, kind=defn["kind"], ref=defn["ref"],
        ref_version=columns["target_version"], title=columns["title"], params=columns["params"],
        input_sources=columns["input_sources"], expectations=columns["expectations"],
        approval=columns["approval"], start_released_by_change_id=released,
        run_id=uuid.uuid4() if run_status else None, run_status=run_status, holds_downstream=holds,
        hold=hold if holds else None,
    )


def _apply(live, nodes, mode="auto", used=(), author="user", **content):
    change = ChangeContent.model_validate({"v": 1, "nodes": nodes, **content})
    return plan_apply(live, change, used_node_ids={uuid.UUID(i) for i in used}, execution_mode=mode,
                      author=author)


# ----------------------------------------------------------------------
# Content
# ----------------------------------------------------------------------
def test_content_digest_is_stable_and_normalised():
    a = _def("a")
    one = ChangeContent.model_validate({"v": 1, "nodes": [a]})
    two = ChangeContent.model_validate({"v": 1, "nodes": [{**a, "node_id": a["node_id"].upper()}]})
    assert one.digest() == two.digest()
    assert "seed_from" not in one.document()["nodes"][0]


@pytest.mark.parametrize(
    "content",
    [
        {"v": 1, "nodes": [{"node_id": "x"}]},  # not a UUID
        {"v": 2, "nodes": []},
        {"v": 1, "nodes": [], "rerun_node_ids": [str(uuid.uuid4())]},  # outside the sequence
        {"v": 1, "nodes": [], "on_running": "ignore"},
    ],
)
def test_malformed_content_is_refused(content):
    with pytest.raises(ValidationError):
        ChangeContent.model_validate(content)


def test_a_node_id_twice_and_a_seed_on_a_tool_are_refused():
    a = _def("a")
    with pytest.raises(ValidationError, match="DUPLICATE_NODE"):
        ChangeContent.model_validate({"v": 1, "nodes": [a, a]})
    seeded = {**a, "seed_from": {"run_id": str(uuid.uuid4()), "message_uuid": "m"}}
    with pytest.raises(ValidationError, match="BAD_SEED"):
        ChangeContent.model_validate({"v": 1, "nodes": [seeded], "rerun_node_ids": [a["node_id"]]})


# ----------------------------------------------------------------------
# plan_apply: the affected suffix
# ----------------------------------------------------------------------
def test_appending_affects_only_the_new_node():
    a, b = _def("a"), _def("b")
    plan = _apply([_live(a, 1, "completed")], [a, b])
    assert plan.affected == [uuid.UUID(b["node_id"])] and not plan.superseded and not plan.reasons


def test_an_insert_affects_the_suffix_after_it():
    """The A -> New -> B example: B is affected."""
    a, b, new = _def("a"), _def("b"), _def("new")
    plan = _apply([_live(a, 1, "completed"), _live(b, 2, "completed")], [a, new, b])
    assert plan.affected == [uuid.UUID(new["node_id"]), uuid.UUID(b["node_id"])]
    assert [n.node_id for n in plan.superseded] == [uuid.UUID(b["node_id"])]
    assert SUPERSEDES_COMPLETED in plan.reasons


def test_an_edit_reruns_the_node_and_everything_after_it():
    a, b, c = _def("a"), _def("b"), _def("c")
    live = [_live(a, 1, "completed"), _live(b, 2, "failed"), _live(c, 3)]
    plan = _apply(live, [a, {**b, "params": {"k": 1}}, c])
    assert plan.affected == [uuid.UUID(b["node_id"]), uuid.UUID(c["node_id"])]
    assert plan.reasons == set()  # superseding a failed run needs no approval
    assert plan.accepting == {uuid.UUID(b["node_id"])}


def test_removing_the_last_unstarted_node_needs_no_approval_even_in_plan_mode():
    a, b = _def("a"), _def("b")
    plan = _apply([_live(a, 1, "completed"), _live(b, 2)], [a], mode="plan")
    assert plan.affected == [uuid.UUID(b["node_id"])] and plan.reasons == set()


def test_plan_mode_and_a_running_node():
    a = _def("a")
    plan = _apply([_live(a, 1, "running")], [{**a, "title": "a2"}], mode="plan")
    assert PLAN_MODE in plan.reasons
    assert [e.code for e in plan.errors] == ["RUNNING_NODE_AFFECTED"]
    restart = _apply([_live(a, 1, "running")], [{**a, "title": "a2"}], on_running="restart")
    assert not restart.errors and len(restart.superseded) == 1


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda a, b: [{**a, "ref": "refit_model"}], "TARGET_IMMUTABLE"),
        (lambda a, b: [{**b, "input_sources": {"v": 1, "ports": {"x": {"node_id": a["node_id"], "port": "p"}}}}, a],
         "BAD_INPUT_SOURCE"),
    ],
)
def test_structural_errors(mutate, code):
    a, b = _def("a"), _def("b")
    plan = _apply([_live(a, 1)], mutate(a, b))
    assert code in [e.code for e in plan.errors]


def test_a_reused_node_id_and_a_bad_release():
    a, gone = _def("a"), _def("gone")
    plan = _apply([_live(a, 1)], [a, gone], used=[gone["node_id"]])
    assert [e.code for e in plan.errors] == ["DUPLICATE_NODE"]
    plan = _apply([_live(a, 1, "completed")], [a], release_hold_node_ids=[a["node_id"]])
    assert [e.code for e in plan.errors] == ["BAD_RELEASE"]
    plan = _apply([_live(a, 1, "completed", holds=True)], [a], release_hold_node_ids=[a["node_id"]], mode="plan")
    assert not plan.errors and len(plan.released) == 1 and PLAN_MODE in plan.reasons


# ----------------------------------------------------------------------
# D28: the approvals the user asked for
# ----------------------------------------------------------------------
def test_approval_is_part_of_the_definition():
    a = _def("a")
    plan = _apply([_live(a, 1, "completed")], [{**a, "approval": "after"}])
    assert plan.accepting == {uuid.UUID(a["node_id"])} and SUPERSEDES_COMPLETED in plan.reasons


def test_only_the_user_releases_an_approval_before_start():
    a = _def("a", approval="before")
    released = _apply([_live(a, 1)], [a], release_hold_node_ids=[a["node_id"]])
    assert not released.errors and released.start_releases == [uuid.UUID(a["node_id"])]
    by_main = _apply([_live(a, 1)], [a], author="main", release_hold_node_ids=[a["node_id"]])
    assert [e.code for e in by_main.errors] == ["USER_RELEASE_REQUIRED"]
    # Already released: nothing waits.
    again = _apply([_live(a, 1, released=uuid.uuid4())], [a], release_hold_node_ids=[a["node_id"]])
    assert [e.code for e in again.errors] == ["BAD_RELEASE"]
    # Added and released in one change, by the user.
    b = _def("b", approval="before")
    added = _apply([], [b], release_hold_node_ids=[b["node_id"]])
    assert added.start_releases == [uuid.UUID(b["node_id"])]


def test_only_the_user_releases_a_review_hold():
    a = _def("a", approval="after")
    held = _live(a, 1, "completed", holds=True, hold="user_review")
    assert [e.code for e in _apply([held], [a], author="main", release_hold_node_ids=[a["node_id"]]).errors] == [
        "USER_RELEASE_REQUIRED"]
    assert len(_apply([held], [a], release_hold_node_ids=[a["node_id"]]).released) == 1
    # The Main may still release an expectation hold.
    violated = _live(a, 1, "completed", holds=True)
    assert len(_apply([violated], [a], author="main", release_hold_node_ids=[a["node_id"]]).released) == 1


# ----------------------------------------------------------------------
# Outputs, expectations, retries, activity
# ----------------------------------------------------------------------
def test_outputs_reference_only_allowed_tables():
    outputs = RunOutputs.model_validate({"v": 1, "ports": {
        "a": {"type": "Cohort", "ref": {"table": "cohorts", "ids": ["1"]}},
        "b": {"type": "X", "ref": {"table": "sessions", "ids": ["1"]}},
    }})
    assert unknown_output_tables(outputs) == ["sessions"]


def test_expectations():
    outputs = {"ports": {"p": {"summary": {"n": 5, "auc": 0.8}}}}
    rules = {"v": 1, "rules": [{"metric": "n", "op": ">=", "value": 3}, {"metric": "auc", "op": ">", "value": 0.7}]}
    assert violated_expectations(rules, outputs) == []
    failing = {"v": 1, "rules": [{"metric": "n", "op": ">=", "value": 10}, {"metric": "missing", "op": "==", "value": 1}]}
    assert len(violated_expectations(failing, outputs)) == 2
    assert violated_expectations(None, outputs) == []


@pytest.mark.parametrize(
    "status,state,number,dispatched,policy,explicit,pending,expected",
    [
        ("failed", "failed", 1, True, {"max_attempts": 2}, False, None, ""),
        ("failed", "failed", 2, True, {"max_attempts": 2}, False, None, "MAX_ATTEMPTS"),
        ("cancelled", "cancelled", 1, True, {"max_attempts": 3}, False, None, "CANCELLED"),
        ("cancelled", "cancelled", 1, True, {"max_attempts": 3}, True, None, ""),
        ("completed", "succeeded", 1, True, {"max_attempts": 3}, True, None, "NOT_RETRYABLE"),
        ("failed", "interrupted", 1, True, {"max_attempts": 3}, False, None, "NEEDS_DECISION"),
        ("failed", "interrupted", 1, True, {"max_attempts": 3, "idempotent": True}, False, None, ""),
        ("failed", "interrupted", 1, False, {"max_attempts": 3}, False, None, ""),
        ("failed", "interrupted", 1, True, {"max_attempts": 3, "resumable": True}, False,
         [{"tool": "save_cohort", "idempotent": False}], "NEEDS_DECISION"),
        ("failed", "interrupted", 1, True, {"max_attempts": 3, "resumable": True}, False,
         [{"tool": "count_studies", "idempotent": True}], ""),
        ("failed", "interrupted", 1, True, {"max_attempts": 3, "resumable": True}, True,
         [{"tool": "save_cohort", "idempotent": False}], ""),
    ],
)
def test_retry_rules(status, state, number, dispatched, policy, explicit, pending, expected):
    verdict = retry_verdict(run_status=status, latest=LatestAttempt(number, state, dispatched),
                            policy=policy, explicit=explicit, pending_tools=pending)
    assert verdict.code == expected and verdict.allowed is (expected == "")


def test_a_lost_transcript_batch_is_never_retried_automatically():
    latest = LatestAttempt(1, "failed", True, "transcript_write_failed")
    policy = {"max_attempts": 3, "resumable": True}
    assert retry_verdict(run_status="failed", latest=latest, policy=policy, explicit=False).code == "NEEDS_DECISION"
    assert retry_verdict(run_status="failed", latest=latest, policy=policy, explicit=True).allowed


@pytest.mark.parametrize(
    "facts,expected",
    [
        (ActivityFacts("active", (), False, False, False), "idle"),
        (ActivityFacts("active", (), False, False, True), "awaiting_approval"),
        (ActivityFacts("active", (("running", None),), False, False, True), "running"),
        (ActivityFacts("paused", (("running", None),), False, False, False), "pausing"),
        (ActivityFacts("paused", (("suspended", "pause"),), False, False, False), "idle"),
        (ActivityFacts("active", (("suspended", "ask_user"),), False, False, False), "awaiting_user"),
        (ActivityFacts("active", (("suspended", "tool_approval"),), False, False, False), "awaiting_user"),
        (ActivityFacts("active", (("unknown", None),), False, False, False), "blocked"),
        (ActivityFacts("active", (), True, False, False), "blocked"),
        (ActivityFacts("active", (), False, True, False), "blocked"),
        (ActivityFacts("active", (), False, False, False, user_review_hold=True), "awaiting_user"),
        (ActivityFacts("active", (), False, False, True, awaiting_release=True), "awaiting_user"),
        (ActivityFacts("active", (), True, False, False, user_review_hold=True), "blocked"),
    ],
)
def test_derived_activity(facts, expected):
    assert derive_activity(facts) == expected
