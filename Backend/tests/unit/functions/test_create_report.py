"""`create_report` — the MCP tool the latex_reporter_agent was missing.

`latex_to_pdf` requires an existing `report_id` (its own docstring says so) but no
tool ever created one, so every manuscript run invented an id and failed with 404.
This is the regression coverage for the fix: the tool resolves the run/session from
the MCP environment and posts to `POST /reports/`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.support.mcp_loader import load_mcp_tool

module = load_mcp_tool("latex.create_report")

pytestmark = pytest.mark.unit

RUN_ID = "22222222-2222-4222-8222-222222222222"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
REPORT_ID = "33333333-3333-4333-8333-333333333333"


@pytest.mark.asyncio
async def test_creates_the_report_with_the_bound_run_and_session(monkeypatch):
    monkeypatch.setattr(module.domain_api, "require_run", lambda: RUN_ID)
    monkeypatch.setattr(module.domain_api, "require_session", lambda: SESSION_ID)
    request = AsyncMock(
        return_value={"report_id": REPORT_ID, "source_version": 1}
    )
    monkeypatch.setattr(module.domain_api, "request", request)

    result = await module.create_report(source_text="\\documentclass{article}", title="A Study")

    import json

    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["report_id"] == REPORT_ID
    assert payload["expected_version"] == 1

    request.assert_awaited_once()
    method, path = request.await_args.args
    assert method == "POST"
    assert path == "/reports/"
    body = request.await_args.kwargs["json"]
    assert body["session_id"] == SESSION_ID
    assert body["created_by_run_id"] == RUN_ID
    assert body["source_format"] == "latex"
    assert body["title"] == "A Study"


@pytest.mark.asyncio
async def test_returns_an_error_when_not_bound_to_a_run(monkeypatch):
    def _raise():
        raise module.domain_api.DomainApiError("RUN_ID is not set")

    monkeypatch.setattr(module.domain_api, "require_run", _raise)

    result = await module.create_report(source_text="\\documentclass{article}")

    import json

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "RUN_ID" in payload["error_message"]


@pytest.mark.asyncio
async def test_returns_an_error_when_the_application_api_rejects_the_write(monkeypatch):
    monkeypatch.setattr(module.domain_api, "require_run", lambda: RUN_ID)
    monkeypatch.setattr(module.domain_api, "require_session", lambda: SESSION_ID)

    async def _fail(*args, **kwargs):
        raise module.domain_api.DomainApiError("POST /reports/ -> 422: bad payload")

    monkeypatch.setattr(module.domain_api, "request", _fail)

    result = await module.create_report(source_text="\\documentclass{article}")

    import json

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "422" in payload["error_message"]
