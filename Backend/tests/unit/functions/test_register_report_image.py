"""`register_report_image` — binds a figure artifact to an `\\includegraphics`
key before compilation, closing the gap `list_report_figures` alone leaves:
knowing an artifact exists does not make it usable in LaTeX (the Application
API only materializes images named via `POST /reports/{report_id}/images`).
"""

from __future__ import annotations

import json

from unittest.mock import AsyncMock

import pytest

from tests.support.mcp_loader import load_mcp_tool

module = load_mcp_tool("latex.register_image")

pytestmark = pytest.mark.unit

REPORT_ID = "33333333-3333-4333-8333-333333333333"
ARTIFACT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_registers_the_artifact_under_the_given_image_key(monkeypatch):
    request = AsyncMock(
        return_value={"image_key": "roc.png", "file_id": ARTIFACT_ID}
    )
    monkeypatch.setattr(module.domain_api, "request", request)

    result = json.loads(
        await module.register_report_image(
            report_id=REPORT_ID, artifact_id=ARTIFACT_ID, image_key="roc.png"
        )
    )

    assert result == {"status": "ok", "image_key": "roc.png", "artifact_id": ARTIFACT_ID}
    request.assert_awaited_once()
    method, path = request.await_args.args
    assert method == "POST"
    assert path == f"/reports/{REPORT_ID}/images"
    assert request.await_args.kwargs["json"] == {
        "file_id": ARTIFACT_ID,
        "image_key": "roc.png",
    }


@pytest.mark.asyncio
async def test_returns_an_error_when_the_image_key_is_already_taken(monkeypatch):
    async def _fail(*args, **kwargs):
        raise module.domain_api.DomainApiError(
            f"POST /reports/{REPORT_ID}/images -> 409: Image key 'roc.png' is already "
            "registered for this report"
        )

    monkeypatch.setattr(module.domain_api, "request", _fail)

    result = json.loads(
        await module.register_report_image(
            report_id=REPORT_ID, artifact_id=ARTIFACT_ID, image_key="roc.png"
        )
    )

    assert result["status"] == "error"
    assert "409" in result["error_message"]
