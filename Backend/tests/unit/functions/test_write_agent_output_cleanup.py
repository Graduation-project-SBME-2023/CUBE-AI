"""`write_agent_output` cleans up this run's scratch under temp/ once its
output is confirmed durable in Postgres.

Regression coverage: PyRadiomics/PyCaret write their working files (logs,
params, plots, train.csv) under `TEMP_DIR/<category>/<run_id>/` and
nothing ever deleted them, so every feature-extraction and modeling run left
scratch behind forever. None of it is the durable copy — measurements are
`radiomics_features` rows, metrics are `model_results` rows, plots are
`figure_png` artifacts — so once the agent's output write succeeds, there is
nothing left needing the run's temp directory.
"""

from __future__ import annotations

import json
import pytest

from agent_system.toolkit.context import use_context
from agent_system.toolpacks.bookkeeping import agent_output as module
from tests.support.toolkit_fixtures import make_context

pytestmark = pytest.mark.unit

RUN_ID = "44444444-4444-4444-8444-444444444444"


def _base_params(**overrides) -> dict:
    base = dict(agent_id="modeling_agent", agent_role="ml_modeling", status="completed")
    base.update(overrides)
    return module.WriteAgentOutputInput(**base)


@pytest.mark.asyncio
async def test_removes_this_runs_temp_dirs_after_a_successful_write(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "TEMP_DIR", tmp_path)

    modeling_dir = tmp_path / "modeling" / RUN_ID
    modeling_dir.mkdir(parents=True)
    (modeling_dir / "plots.png").write_text("png")
    feature_dir = tmp_path / "feature-extraction" / RUN_ID
    feature_dir.mkdir(parents=True)
    (feature_dir / "radiomics_extraction.log").write_text("log")
    # A different run's scratch must never be touched.
    other_run_dir = tmp_path / "modeling" / "other-run-id"
    other_run_dir.mkdir(parents=True)

    async def recorded(output):
        return {"run_id": RUN_ID, "node_id": "s1"}

    monkeypatch.setattr(module.domain_api, "record_run_output", recorded)

    with use_context(make_context(run_id=RUN_ID)):
        result = json.loads(await module.write_agent_output(_base_params()))

    assert result["status"] == "ok"
    assert not modeling_dir.exists()
    assert not feature_dir.exists()
    assert other_run_dir.exists()


@pytest.mark.asyncio
async def test_a_run_with_no_temp_scratch_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "TEMP_DIR", tmp_path)
    async def recorded(output):
        return {"run_id": RUN_ID, "node_id": "s1"}

    monkeypatch.setattr(module.domain_api, "record_run_output", recorded)

    with use_context(make_context(run_id=RUN_ID)):
        result = json.loads(await module.write_agent_output(_base_params()))

    assert result["status"] == "ok"

