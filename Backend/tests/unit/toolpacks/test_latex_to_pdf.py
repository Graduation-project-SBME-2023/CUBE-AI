"""latex_to_pdf as a graph tool (merged plan, Phase 2.4): its failures are error
results, and its report output port reads the published version."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_system.toolkit.tools.registry import spec_of
from agent_system.toolpacks.reporting.latex_to_pdf import latex_to_pdf
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

REPORT = "44444444-4444-4444-8444-444444444444"
ARGS = {"latex_text": r"\documentclass{article}", "output_filename": "r.pdf", "report_id": REPORT}


def _publish(fake_domain_api, response):
    fake_domain_api.post(f"/reports/{REPORT}/publish").mock(return_value=response)
    return call_tool(latex_to_pdf, ARGS, make_context(run_id="r1"))


def test_a_publish_yields_the_report_artifact(fake_domain_api):
    result = _publish(fake_domain_api, httpx.Response(200, content=b"%PDF-1.7"))

    assert json.loads(result)["status"] == "success"
    [artifact] = spec_of(latex_to_pdf).outputs["report"].extract_artifacts(result)
    assert dict(artifact.ref) == {"report_id": REPORT}
    assert dict(artifact.summary) == {"source_version": 2, "has_pdf": True}


def test_a_compile_error_is_an_error_result_with_the_logs(fake_domain_api):
    detail = {"detail": {"stdout": "! Undefined control sequence", "stderr": ""}}
    result = _publish(fake_domain_api, httpx.Response(422, json=detail))

    assert result["error_code"] == "latex_compile_error"
    assert result["stdout"] == "! Undefined control sequence"


def test_a_version_conflict_keeps_the_current_version(fake_domain_api):
    detail = {"detail": {"code": "version_conflict", "message": "stale", "current_version": 3}}
    result = _publish(fake_domain_api, httpx.Response(409, json=detail))

    assert (result["error_code"], result["current_version"]) == ("version_conflict", 3)


def test_an_unusable_api_code_becomes_publish_failed(fake_domain_api):
    detail = {"detail": {"code": "Bad Code!", "message": "x"}}
    result = _publish(fake_domain_api, httpx.Response(409, json=detail))

    assert result["error_code"] == "publish_failed"
