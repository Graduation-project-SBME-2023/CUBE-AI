"""segmentation_agent and its two internal agents, as loaded from agentpacks/
(merged plan, Phases 3.2 and 3.3)."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from pydantic import ValidationError

from agent_system.agents.execution_agents.segmentation import contracts
from agent_system.agents.execution_agents.segmentation.clinical_target_policy import (
    CLINICAL_TARGET_POLICY,
)
from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.agents.spec import AgentKind
from agent_system.toolkit.artifacts.builtin import MASK_SET
from agent_system.toolkit.catalog import build
from tests.support.toolkit_fixtures import make_context

pytestmark = pytest.mark.unit

RUN_ID = "7d1f4c2e-0000-4000-8000-000000000004"
OTHER_RUN_ID = "7d1f4c2e-0000-4000-8000-000000000099"
BATCH_ID = "9e1b0000-0000-4000-8000-0000000000b1"
OUTPUT = {
    "segmentation_batch_id": BATCH_ID,
    "n_masks": 22,
    "n_failed": 2,
    "review_status": "submitted",
}


@functools.cache
def _catalog():
    return build()


@pytest.fixture(scope="module")
def spec():
    return _catalog().agents.get("segmentation_agent")


# --- the parent


def test_the_spec(spec):
    assert spec.kind is AgentKind.EXECUTION
    assert spec.inputs["cohort"].artifact_type == "Cohort"
    assert spec.outputs["masks"].artifact_type == "MaskSet"
    assert spec.delegates == ("clinical_target_intent_agent", "mask_resolution_agent")
    assert "run_segmentation_with_guard" in spec.allowed_tools
    # The tool records this step's output; the agent never writes its own.
    assert "write_agent_output" not in spec.allowed_tools
    assert spec.max_tool_iterations == 6


def test_the_review_happens_only_when_the_user_asked_for_it(spec):
    """D28: no fixed gate; the agent asks with ask_user when the node
    instructions say so, and the batch is accepted automatically otherwise."""
    assert not hasattr(spec, "gates")
    assert "ask_user" not in spec.allowed_tools  # implicit, never listed
    assert "`ask_user` with `form`: `segmentation_review`" in spec.system_prompt
    assert "Otherwise the batch is accepted automatically" in spec.system_prompt
    assert spec.gate_timeout_s is None  # a radiologist's review has no deadline
    assert spec.resumable is True  # the suspended node resumes its saved session
    assert spec.skills == ("segmentation_choices",)


def test_the_prompt_keeps_the_clinical_decisions_in_the_tool(spec):
    assert "never to\nchoose a model yourself" in spec.system_prompt
    assert "workspace_folder" not in spec.system_prompt


# --- the internal agents


@pytest.mark.parametrize(
    ("name", "contract", "tools"),
    [
        ("clinical_target_intent_agent", contracts.ClinicalTargetIntentPlan, ()),
        ("mask_resolution_agent", contracts.TargetCandidateDecisionPlan, ("list_models",)),
    ],
)
def test_the_internal_agents(name, contract, tools):
    agent = _catalog().agents.get(name)

    assert agent.kind is AgentKind.INTERNAL
    assert agent.parent == "segmentation_agent"
    assert agent.allowed_tools == tools
    # The segmentation module's own model, not a copy of it.
    assert agent.output_contract is contract
    assert agent.resumable is False  # its transcript is stored with its parent's
    assert agent.max_completion_retries == 1  # the planner's one retry
    assert not agent.delegates and not agent.inputs and not agent.outputs


def test_the_internal_agents_carry_the_clinical_policy_verbatim():
    """The policy's fingerprint is stored with every cached segmentation
    decision, so the policy an agent reads must be the policy that is hashed."""
    for name in ("clinical_target_intent_agent", "mask_resolution_agent"):
        assert CLINICAL_TARGET_POLICY in _catalog().agents.get(name).system_prompt, name
    threshold = f"{contracts.REUSE_CONFIDENCE_THRESHOLD:.2f}"
    assert threshold in _catalog().agents.get("mask_resolution_agent").system_prompt


def test_the_internal_prompts_are_the_planners_until_phase_9_7():
    """planner.py keeps its own copy until Phase 9.7 runs these agents on the
    engine; until then the two must not drift apart."""
    pytest.importorskip("autogen_agentchat")
    from agent_system.agents.execution_agents.segmentation import planner

    agents = _catalog().agents
    assert agents.get("clinical_target_intent_agent").system_prompt == planner.INTENT_SYSTEM_MESSAGE.strip()
    assert agents.get("mask_resolution_agent").system_prompt == planner.CANDIDATE_SYSTEM_MESSAGE.strip()


# --- output contract and port


@pytest.mark.parametrize(
    "change",
    [{"segmentation_batch_id": "batch-1"}, {"n_masks": -1}, {"review_status": "approved"}, {"masks_dir": "/x"}],
)
def test_the_output_contract_refuses(spec, change):
    with pytest.raises(ValidationError):
        spec.output_contract.model_validate(OUTPUT | change)


def test_extract_masks(spec):
    result = AgentResult(status=AgentStatus.COMPLETED, output=OUTPUT)

    (artifact,) = spec.outputs["masks"].extract_artifacts(result)

    assert dict(artifact.ref) == {"segmentation_batch_id": BATCH_ID}
    assert dict(artifact.summary) == {"n_masks": 22, "n_failed": 2, "review_status": "submitted"}
    MASK_SET.validate_artifact(artifact)


# --- completion check


def _run(status="completed", batch=BATCH_ID, accepted=22, failures=2):
    agent_output = {
        "status": status,
        "outputs": {"files": {"segmentation_batch_id": batch}, "metrics": {"accepted_count": accepted}},
        "errors": [],
    }
    warnings = [{"message": f"volume {i} failed", "source": "agent_output"} for i in range(failures)]
    warnings.append({"message": "a note from the agent", "source": "flag"})
    return {
        "run_id": RUN_ID,
        "scratchpad_summary": {"agent_output": agent_output} if status else {},
        "scratchpad_warnings": {"items": warnings},
    }


def _batch(**fields):
    return {"segmentation_batch_id": BATCH_ID, "created_by_run_id": RUN_ID, "review_status": "submitted"} | fields


def _check(spec, fake_domain_api, run=None, batch=None, output=OUTPUT):
    fake_domain_api.get(f"/runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run or _run()))
    route = fake_domain_api.get(f"/segmentation-batches/{BATCH_ID}")
    if batch == 404:
        route.mock(return_value=httpx.Response(404, json={"detail": "not found"}))
    else:
        route.mock(return_value=httpx.Response(200, json=batch or _batch()))
    parsed = spec.output_contract.model_validate(output) if output is not None else None
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))


def test_complete_when_the_reviewed_batch_matches_the_tools_record(spec, fake_domain_api):
    assert _check(spec, fake_domain_api) == []


@pytest.mark.parametrize(
    ("run", "batch", "output", "expected"),
    [
        (_run(status=None), None, OUTPUT, ["call run_segmentation_with_guard"]),
        (_run(status="failed"), None, OUTPUT, ["call run_segmentation_with_guard"]),
        (_run(batch=OTHER_RUN_ID), None, OUTPUT, ["recorded batch"]),
        (_run(accepted=20), None, OUTPUT, ["accepted 20 masks, not 22"]),
        (_run(failures=0), None, OUTPUT, ["recorded 0 failures, not 2"]),
        (None, 404, OUTPUT, ["is not a batch of this run"]),
        (None, _batch(created_by_run_id=OTHER_RUN_ID), OUTPUT, ["is not a batch of this run"]),
        (None, _batch(review_status="pending"), OUTPUT, ["review status is pending, not submitted"]),
        (None, _batch(review_status="pending"), OUTPUT | {"review_status": "pending"}, ["not reviewed yet"]),
        (_run(accepted=0, failures=0), None, OUTPUT | {"n_masks": 0, "n_failed": 0}, ["no mask was accepted"]),
    ],
)  # fmt: skip
def test_what_is_wrong_is_named(spec, fake_domain_api, run, batch, output, expected):
    issues = _check(spec, fake_domain_api, run, batch, output)

    assert len(issues) == len(expected), issues
    for issue, text in zip(issues, expected):
        assert text in issue


def test_without_a_structured_answer(spec, fake_domain_api):
    (issue,) = _check(spec, fake_domain_api, output=None)

    assert "structured format" in issue
