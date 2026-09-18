"""`write_agent_output` cleans up this run's scratch under temp/ once its
output is confirmed durable in Postgres.

Regression coverage: PyRadiomics/PyCaret write their working files (logs,
params, plots, train.csv) under `TEMP_DIR/<category>/<agent_run_id>/` and
nothing ever deleted them, so every feature-extraction and modeling run left
scratch behind forever. None of it is the durable copy — measurements are
`radiomics_features` rows, metrics are `model_results` rows, plots are
`figure_png` artifacts — so once the agent's output write succeeds, there is
nothing left needing the run's temp directory.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from tests.support.mcp_loader import load_mcp_tool

module = load_mcp_tool("agent_output")

pytestmark = pytest.mark.unit

RUN_ID = "44444444-4444-4444-8444-444444444444"


def _base_params(**overrides) -> dict:
    base = dict(agent_id="modeling_agent", agent_role="ml_modeling", status="completed")
    base.update(overrides)
    return module.WriteAgentOutputInput(**base)


@pytest.mark.asyncio
async def test_removes_this_runs_temp_dirs_after_a_successful_write(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "AGENT_RUN_ID", RUN_ID)
    monkeypatch.setattr(module, "TEMP_DIR", tmp_path)

    modeling_dir = tmp_path / "modeling" / RUN_ID
    modeling_dir.mkdir(parents=True)
    (modeling_dir / "pycaret_classification.log").write_text("log")
    feature_dir = tmp_path / "feature-extraction" / RUN_ID
    feature_dir.mkdir(parents=True)
    (feature_dir / "radiomics_extraction.log").write_text("log")
    # A different run's scratch must never be touched.
    other_run_dir = tmp_path / "modeling" / "other-run-id"
    other_run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        module.httpx, "AsyncClient",
        lambda **_: _FakeClient({"agent_run_id": RUN_ID, "step_id": "s1"}),
    )

    result = json.loads(await module.write_agent_output(_base_params()))

    assert result["status"] == "ok"
    assert not modeling_dir.exists()
    assert not feature_dir.exists()
    assert other_run_dir.exists()


@pytest.mark.asyncio
async def test_a_run_with_no_temp_scratch_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "AGENT_RUN_ID", RUN_ID)
    monkeypatch.setattr(module, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        module.httpx, "AsyncClient",
        lambda **_: _FakeClient({"agent_run_id": RUN_ID, "step_id": "s1"}),
    )

    result = json.loads(await module.write_agent_output(_base_params()))

    assert result["status"] == "ok"


class _FakeResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def put(self, *args, **kwargs):
        return _FakeResponse(self._payload)
