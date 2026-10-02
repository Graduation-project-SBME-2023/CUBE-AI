"""`list_report_figures` — the LaTeX reporter's figure discovery.

Regression coverage for a real incident: `list_workspace_plots` globbed
`WORKSPACE_DIR` for `*.png`, but every figure the pipeline renders is
published as a `figure_png`/`figure_jpeg` artifact (bytes in PostgreSQL,
A13) under `TEMP_DIR`, never written under the workspace. The tool always
found zero plots, so no report ever included a figure. This queries the
artifact catalogue instead, which is where a figure actually lives.
"""

from __future__ import annotations

import json

from unittest.mock import AsyncMock

import pytest

from tests.support.mcp_loader import load_mcp_tool

module = load_mcp_tool("latex.list_plots")

pytestmark = pytest.mark.unit


def _artifact(file_id: str, filename: str, kind: str = "figure_png") -> dict:
    return {
        "file_id": file_id,
        "display_filename": filename,
        "logical_key": f"features/run/{filename}",
        "created_by_run_id": "22222222-2222-4222-8222-222222222222",
        "created_at": "2026-09-16T20:31:56Z",
        "kind": kind,
    }


@pytest.mark.asyncio
async def test_lists_png_and_jpeg_figures_from_the_artifact_catalogue(monkeypatch):
    async def _fake(kind: str | None = None):
        if kind == "figure_png":
            return [_artifact("aaaa", "feature_importance_plot.png")]
        if kind == "figure_jpeg":
            return [_artifact("bbbb", "roc.jpg", kind="figure_jpeg")]
        return []

    monkeypatch.setattr(module.domain_api, "list_session_artifacts", AsyncMock(side_effect=_fake))

    result = json.loads(await module.list_report_figures())

    assert result["count"] == 2
    filenames = {item["display_filename"] for item in result["figures"]}
    assert filenames == {"feature_importance_plot.png", "roc.jpg"}
    assert all("artifact_id" in item and "logical_key" in item for item in result["figures"])


@pytest.mark.asyncio
async def test_is_empty_when_no_figure_was_published(monkeypatch):
    monkeypatch.setattr(
        module.domain_api, "list_session_artifacts", AsyncMock(return_value=[])
    )

    result = json.loads(await module.list_report_figures())

    assert result == {"figures": [], "count": 0}


@pytest.mark.asyncio
async def test_returns_an_error_when_the_application_api_rejects_the_read(monkeypatch):
    async def _fail(kind=None):
        raise module.domain_api.DomainApiError("GET /artifacts/session/x -> 404")

    monkeypatch.setattr(module.domain_api, "list_session_artifacts", _fail)

    result = json.loads(await module.list_report_figures())

    assert result["status"] == "error"
    assert "404" in result["error_message"]
