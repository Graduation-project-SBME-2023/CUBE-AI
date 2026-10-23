"""study_population_planner_agent, as loaded from agentpacks/ (merged plan, Phase 3.2)."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from pydantic import ValidationError

from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.artifacts.builtin import COHORT
from agent_system.toolkit.catalog import build
from agent_system.toolkit.manifest import Gate
from tests.support.toolkit_fixtures import TEST_SESSION_ID, make_context

pytestmark = pytest.mark.unit

RUN_ID = "7d1f4c2e-0000-4000-8000-000000000005"
OTHER_RUN_ID = "7d1f4c2e-0000-4000-8000-000000000099"
COHORT_ID = "c0c0c0c0-0000-4000-8000-0000000000c1"
OUTPUT = {
    "cohort_id": COHORT_ID,
    "cohort_size": 2,
    "modality": "CT",
    "inclusion_criteria": ["Contrast-enhanced abdominal CT", "Adults"],
    "exclusion_criteria": ["Splenectomy"],
}


@functools.cache
def _spec():
    return build().agents.get("study_population_planner_agent")


@pytest.fixture(scope="module")
def spec():
    return _spec()


def test_the_spec(spec):
    assert spec.kind == "execution"
    assert spec.inputs == {}  # it starts from the hypothesis, not from another node
    assert spec.outputs["cohort"].artifact_type == "Cohort"
    assert spec.gates == frozenset({Gate.COHORT_APPROVAL})
    assert spec.human_in_the_loop is True
    assert (spec.max_tool_iterations, spec.max_completion_retries) == (20, 2)


def test_the_tool_list(spec):
    assert spec.allowed_tools[:6] == (
        "write_inclusion_criteria",  # the criteria come before the data
        "get_unique_series_descriptions",
        "search_volumes_by_description",
        "validate_population_anatomy",
        "extract_cohort_labels",  # refuses by itself outside automatic mode
        "generate_targets",
    )
    assert not spec.has_dynamic_tools


def test_the_prompt(spec):
    prompt = spec.system_prompt
    # The anti-bias order and the region rule are kept ...
    assert prompt.index("write_inclusion_criteria") < prompt.index("get_unique_series_descriptions")
    assert "never substitute another region" in prompt
    # ... the retired handshake is gone: generate_targets no longer returns it.
    assert "SILENT_RETRY" not in prompt
    assert "manual_labelling" in prompt


@pytest.mark.parametrize(
    "change",
    [{"cohort_size": 0}, {"cohort_id": "population"}, {"inclusion_criteria": []}, {"uids": ["1.2.3"]}],
)
def test_the_output_contract_refuses(spec, change):
    with pytest.raises(ValidationError):
        spec.output_contract.model_validate(OUTPUT | change)


def test_extract_cohort(spec):
    (artifact,) = spec.outputs["cohort"].extract_artifacts(
        AgentResult(status=AgentStatus.COMPLETED, output=OUTPUT)
    )

    assert dict(artifact.ref) == {"cohort_id": COHORT_ID}
    assert dict(artifact.summary) == {"size": 2, "frozen": True}
    COHORT.validate_artifact(artifact)


# --- completion check: the durable handoff


def _cohort(**fields):
    return {
        "cohort_id": COHORT_ID,
        "created_by_run_id": RUN_ID,
        "selection_summary": {"anatomy_validation": {"anatomy_validated": True}},
        "members": [{"volume_series_instance_uid": "1.2.3"}, {"volume_series_instance_uid": "1.2.4"}],
        "frozen_at": "2026-10-10T10:00:00Z",
    } | fields


def _run(summary="Selected 2 volumes.", status="completed"):
    recorded = {}
    if summary is not None:
        recorded["scratchpad_summary"] = {"content": summary}
    if status is not None:
        recorded["agent_output"] = {"status": status}
    return {"run_id": RUN_ID, "scratchpad_summary": recorded}


def _check(spec, fake_domain_api, cohort=None, rows=2, run=None, output=OUTPUT):
    route = fake_domain_api.get(f"/cohorts/session/{TEST_SESSION_ID}/population")
    if cohort == 404:
        route.mock(return_value=httpx.Response(404, json={"detail": "no cohort"}))
    else:
        route.mock(return_value=httpx.Response(200, json=cohort or _cohort()))
    fake_domain_api.get(path__regex=r"^/cohorts/[^/]+/analysis-rows$").mock(
        return_value=httpx.Response(200, json=[{"target": 1}] * rows)
    )
    fake_domain_api.get(f"/runs/{RUN_ID}").mock(return_value=httpx.Response(200, json=run or _run()))
    parsed = spec.output_contract.model_validate(output) if output is not None else None
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))


def test_complete_once_the_approved_cohort_is_handed_off(spec, fake_domain_api):
    assert _check(spec, fake_domain_api) == []


@pytest.mark.parametrize(
    ("cohort", "rows", "run", "expected"),
    [
        (404, 2, None, ["no population cohort"]),
        (_cohort(created_by_run_id=OTHER_RUN_ID), 2, None, ["no population cohort"]),
        (_cohort(cohort_id="c0c0c0c0-0000-4000-8000-0000000000c9"), 2, None, ["not " + COHORT_ID]),
        (_cohort(selection_summary={}), 2, None, ["validate_population_anatomy"]),
        (_cohort(members=[]), 2, None, ["the cohort is empty"]),
        (_cohort(frozen_at=None), 2, None, ["has not approved the cohort"]),
        (None, 0, None, ["no labelled volume"]),
        (None, 3, None, ["has 3 labelled volumes, not 2"]),
        (None, 2, _run(summary=None), ["scratchpad_write_summary"]),
        (None, 2, _run(status="partial"), ["write_agent_output"]),
    ],
)  # fmt: skip
def test_what_is_missing_is_named(spec, fake_domain_api, cohort, rows, run, expected):
    issues = _check(spec, fake_domain_api, cohort, rows, run)

    assert len(issues) == len(expected), issues
    for issue, text in zip(issues, expected):
        assert text in issue


def test_the_run_status_is_not_the_agents_to_set(spec, fake_domain_api):
    """The legacy handoff refused a run whose scratchpad status was not
    'done'; the Node Runner sets that now, so the check no longer reads it."""
    run = _run() | {"scratchpad_notes": {"status": "in_progress"}}

    assert _check(spec, fake_domain_api, run=run) == []
