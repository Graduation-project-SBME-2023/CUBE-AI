"""result_reporter_agent, as loaded from agentpacks/ (merged plan, Phase 3.2)."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from pydantic import ValidationError

from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.artifacts.builtin import RESULT_SUMMARY
from agent_system.toolkit.catalog import build
from agent_system.toolkit.context import use_context
from agent_system.toolkit.manifest import Resource
from agent_system.toolkit.ports import PortExtractError
from tests.support.toolkit_fixtures import make_context

pytestmark = pytest.mark.unit

RUN_ID = "7d1f4c2e-0000-4000-8000-000000000001"
FINDINGS = {
    "decision": "inconclusive",
    "justification": "The cohort is small and the metrics are not a generalisation estimate.",
    "key_findings": ["AUC 0.81 on 5-fold CV", "Top feature: glcm_Contrast"],
    "primary_metric": "auc",
    "primary_metric_value": 0.81,
    "confidence": "low",
    "limitations": ["12 subjects"],
}


@functools.cache
def _spec():
    return build().agents.get("result_reporter_agent")


@pytest.fixture(scope="module")
def spec():
    return _spec()


def test_the_spec(spec):
    assert spec.kind == "execution"
    assert spec.allowed_tools == (
        "get_modeling_results",
        "scratchpad_write_note",
        "scratchpad_raise_flag",
        "scratchpad_write_summary",
        "write_agent_output",
    )
    assert {name: port.artifact_type for name, port in spec.inputs.items()} == {
        "modeling": "ModelingOutput",
        "features": "FeatureTable",
        "cohort": "Cohort",
        "masks": "MaskSet",
    }
    assert spec.inputs["modeling"].required
    assert not any(spec.inputs[name].required for name in ("features", "cohort", "masks"))
    assert spec.outputs["summary"].artifact_type == "ResultSummary"
    assert (spec.max_tool_iterations, spec.max_completion_retries) == (10, 2)
    assert spec.resources == frozenset({Resource.LLM})
    assert spec.resumable is True
    assert not spec.delegates
    assert spec.skills == ("reporting",)
    assert spec.ui == "report_output"


def test_the_prompt_starts_from_the_node_inputs(spec):
    assert "Node inputs" in spec.system_prompt
    assert "scratchpad_read_agent" not in spec.system_prompt
    assert "scratchpad_read_report" not in spec.system_prompt  # removed in Phase 0.2


# --- output contract


def test_the_output_contract(spec):
    assert spec.output_contract.model_validate(FINDINGS).decision == "inconclusive"


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "maybe"},
        {"confidence": "certain"},
        {"key_findings": []},
        {"justification": ""},
        {"verdict": "accepted"},
    ],
)
def test_the_output_contract_refuses_a_bad_judgement(spec, change):
    with pytest.raises(ValidationError):
        spec.output_contract.model_validate(FINDINGS | change)


# --- the summary port


def _findings():
    return _spec().output_contract.model_validate(FINDINGS)



def _result(output=FINDINGS) -> AgentResult:
    return AgentResult(status=AgentStatus.COMPLETED, output=output)


def test_extract_summary_points_at_this_run(spec):
    port = spec.outputs["summary"]

    with use_context(make_context(run_id=RUN_ID)):
        (artifact,) = port.extract_artifacts(_result())

    assert dict(artifact.ref) == {"run_id": RUN_ID}
    assert dict(artifact.summary) == {"n_findings": 2}
    RESULT_SUMMARY.validate_artifact(artifact)  # the built-in type accepts it


def test_extract_summary_needs_the_run(spec):
    with use_context(make_context()), pytest.raises((ValueError, PortExtractError), match="run"):
        spec.outputs["summary"].extract_artifacts(_result())


def test_extract_summary_refuses_an_output_outside_the_contract(spec):
    with use_context(make_context(run_id=RUN_ID)), pytest.raises(ValidationError):
        spec.outputs["summary"].extract_artifacts(_result({"decision": "accepted"}))


# --- completion check


def _run(summary=None, agent_output=None) -> dict:
    recorded = {}
    if summary is not None:
        recorded["scratchpad_summary"] = {"content": summary}
    if agent_output is not None:
        recorded["agent_output"] = agent_output
    return {"run_id": RUN_ID, "scratchpad_summary": recorded}


def _check(spec, fake_domain_api, run=None, status_code=200) -> list[str]:
    fake_domain_api.get(f"/runs/{RUN_ID}").mock(
        return_value=httpx.Response(status_code, json=run if run is not None else {})
    )
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), _findings()))


def test_complete_once_the_judgement_and_the_output_are_recorded(spec, fake_domain_api):
    run = _run("Inconclusive: 12 subjects.", {"status": "completed"})

    assert _check(spec, fake_domain_api, run) == []


def test_partial_output_is_enough(spec, fake_domain_api):
    run = _run("Inconclusive.", {"status": "partial"})

    assert _check(spec, fake_domain_api, run) == []


@pytest.mark.parametrize(
    ("run", "missing"),
    [
        (_run(), ["scratchpad_write_summary", "write_agent_output"]),
        (_run("   ", {"status": "completed"}), ["scratchpad_write_summary"]),
        (_run("Done.", {"status": "failed"}), ["write_agent_output"]),
        (_run("Done."), ["write_agent_output"]),
    ],
)
def test_what_is_missing_is_named(spec, fake_domain_api, run, missing):
    issues = _check(spec, fake_domain_api, run)

    assert len(issues) == len(missing)
    for issue, tool in zip(issues, missing):
        assert tool in issue


def test_outside_a_node_there_is_nothing_to_check(spec):
    issues = asyncio.run(spec.completion_check(make_context(), _findings()))

    assert issues == ["this agent is not running inside a node; there is no run to record on"]


def test_an_unreachable_api_fails_the_attempt_instead_of_asking_the_model(spec, fake_domain_api):
    with pytest.raises(Exception, match="internal error"):
        _check(spec, fake_domain_api, {}, status_code=503)
