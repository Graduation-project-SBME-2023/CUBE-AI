from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests.support.mcp_loader import load_mcp_tool

module = load_mcp_tool("agent_output")


@pytest.fixture
def isolated_outputs(monkeypatch, tmp_path):
    output_dir = tmp_path / "outputs"
    monkeypatch.setattr(module, "OUTPUTS_DIR", str(output_dir))
    return output_dir


def _input(agent_id: str, status: str = "completed"):
    return module.WriteAgentOutputInput(
        agent_id=agent_id,
        agent_role="test_role",
        status=status,
        inputs={"session_id": "session-1"},
        output_files={"artifact": "outputs/artifact.json"},
        output_metrics={"count": 2},
        output_summary="Completed deterministic test work.",
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_write_and_read_agent_output_round_trip(isolated_outputs):
    confirmation = json.loads(
        await module.write_agent_output(_input("segmentation_agent"))
    )
    stored = json.loads(
        await module.read_agent_output(
            module.ReadAgentOutputInput(agent_id="segmentation_agent")
        )
    )

    assert confirmation["status"] == "ok"
    assert stored["agent_id"] == "segmentation_agent"
    assert stored["status"] == "completed"
    assert stored["outputs"]["files"]["artifact"] == "outputs/artifact.json"
    assert stored["outputs"]["metrics"]["count"] == 2
    assert stored["timestamp"].endswith("+00:00")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_aggregate_status_prioritizes_failure_then_partial(isolated_outputs):
    await module.write_agent_output(_input("population_agent", "completed"))
    await module.write_agent_output(_input("feature_agent", "partial"))

    partial = json.loads(await module.read_all_agent_outputs())
    assert partial["agent_count"] == 2
    assert partial["pipeline_status"] == "has_partial"

    await module.write_agent_output(_input("modeling_agent", "failed"))
    failed = json.loads(await module.read_all_agent_outputs())
    assert failed["agent_count"] == 3
    assert failed["pipeline_status"] == "has_failures"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_aggregate_skips_corrupt_output_files(isolated_outputs):
    isolated_outputs.mkdir(parents=True)
    (isolated_outputs / "broken_output.json").write_text(
        "{not-json",
        encoding="utf-8",
    )

    result = json.loads(await module.read_all_agent_outputs())

    assert result == {
        "agent_count": 0,
        "agents": {},
        "pipeline_status": "no_outputs",
    }


@pytest.mark.contract
def test_output_contract_forbids_unknown_fields_and_invalid_status():
    with pytest.raises(ValidationError):
        module.WriteAgentOutputInput(
            agent_id="agent",
            agent_role="role",
            status="unknown",
        )

    with pytest.raises(ValidationError):
        module.WriteAgentOutputInput(
            agent_id="agent",
            agent_role="role",
            status="completed",
            unexpected=True,
        )
