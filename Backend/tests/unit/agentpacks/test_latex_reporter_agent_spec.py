"""latex_reporter_agent, as loaded from agentpacks/ (merged plan, Phase 3.2)."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from pydantic import ValidationError

from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.artifacts.builtin import REPORT_DOCUMENT
from agent_system.toolkit.catalog import build
from tests.support.toolkit_fixtures import make_context

pytestmark = pytest.mark.unit

RUN_ID = "7d1f4c2e-0000-4000-8000-000000000002"
OTHER_RUN_ID = "7d1f4c2e-0000-4000-8000-000000000099"
REPORT_ID = "5b0c7a10-0000-4000-8000-0000000000aa"
PUBLISHED = {
    "report_id": REPORT_ID,
    "source_version": 3,
    "title": "Radiomics of the spleen",
    "template": "short_study",
    "figures": ["roc_curve.png"],
}


@functools.cache
def _spec():
    return build().agents.get("latex_reporter_agent")


@pytest.fixture(scope="module")
def spec():
    return _spec()


def test_the_spec(spec):
    assert spec.kind == "execution"
    assert {name: (port.artifact_type, port.required) for name, port in spec.inputs.items()} == {
        "summary": ("ResultSummary", True),
        "literature": ("LiteratureContext", False),
    }
    assert spec.outputs["report"].artifact_type == "ReportDocument"
    # The legacy prompt asked for a flag it could not raise, and for model
    # tables it could not read.
    assert {"scratchpad_raise_flag", "get_modeling_results", "latex_to_pdf"} <= set(spec.allowed_tools)
    assert "read_agent_task_context" not in spec.allowed_tools
    assert (spec.max_tool_iterations, spec.max_completion_retries) == (25, 2)
    assert spec.ui == "latex_output"


def test_the_prompt_has_no_contradiction_about_the_scratchpad(spec):
    assert "You do not write to the scratchpad" not in spec.system_prompt
    assert "modeling_agent" not in spec.system_prompt


# --- output contract


@pytest.mark.parametrize(
    "change",
    [
        {"report_id": "report-1"},  # the legacy runs invented ids like this
        {"source_version": 1},  # created, never compiled: no PDF
        {"title": ""},
        {"pdf_path": "/tmp/x.pdf"},
    ],
)
def test_the_output_contract_refuses(spec, change):
    with pytest.raises(ValidationError):
        spec.output_contract.model_validate(PUBLISHED | change)


# --- the report port


def test_extract_report(spec):
    result = AgentResult(status=AgentStatus.COMPLETED, output=PUBLISHED)

    (artifact,) = spec.outputs["report"].extract_artifacts(result)

    assert dict(artifact.ref) == {"report_id": REPORT_ID}
    assert dict(artifact.summary) == {"source_version": 3, "has_pdf": True}
    REPORT_DOCUMENT.validate_artifact(artifact)


# --- completion check


def _check(spec, fake_domain_api, report=None, agent_status="completed", output=PUBLISHED):
    route = fake_domain_api.get(f"/reports/{REPORT_ID}")
    if report is None:
        route.mock(return_value=httpx.Response(404, json={"detail": "Report not found"}))
    else:
        route.mock(return_value=httpx.Response(200, json=report))
    recorded = {"agent_output": {"status": agent_status}} if agent_status else {}
    fake_domain_api.get(f"/runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json={"run_id": RUN_ID, "scratchpad_summary": recorded})
    )
    parsed = spec.output_contract.model_validate(output) if output is not None else None
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))


def _report(**fields):
    return {"report_id": REPORT_ID, "created_by_run_id": RUN_ID, "source_version": 3} | fields


def test_complete_when_the_published_report_is_this_runs(spec, fake_domain_api):
    assert _check(spec, fake_domain_api, _report()) == []


@pytest.mark.parametrize(
    ("report", "agent_status", "expected"),
    [
        (None, "completed", "does not exist"),
        (_report(created_by_run_id=OTHER_RUN_ID), "completed", "not created by this run"),
        (_report(source_version=1), "completed", "has no PDF yet"),
        (_report(source_version=4), "completed", "is at version 4, not 3"),
        (_report(), None, "write_agent_output"),
        (_report(), "failed", "write_agent_output"),
    ],
)
def test_what_is_wrong_is_named(spec, fake_domain_api, report, agent_status, expected):
    (issue,) = _check(spec, fake_domain_api, report, agent_status)

    assert expected in issue


def test_without_a_structured_answer(spec, fake_domain_api):
    (issue,) = _check(spec, fake_domain_api, _report(), output=None)

    assert "structured format" in issue


def test_an_unreachable_api_fails_the_attempt(spec, fake_domain_api):
    fake_domain_api.get(f"/reports/{REPORT_ID}").mock(return_value=httpx.Response(503))
    parsed = spec.output_contract.model_validate(PUBLISHED)

    with pytest.raises(Exception, match="internal error"):
        asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))
