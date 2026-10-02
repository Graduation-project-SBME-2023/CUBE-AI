"""`write_agent_output` is a row on the agent's run, not a file."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from agent_system.toolpacks.bookkeeping import agent_output as module

API = "http://application-api.test"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("APP_API_URL", API)  # read per request
    monkeypatch.setattr(module, "SESSION_ID", SESSION_ID)
    monkeypatch.setattr(module, "RUN_ID", RUN_ID)
    monkeypatch.setattr(module, "AGENT_ID", "segmentation_agent")
    with respx.mock(assert_all_called=False) as router:
        yield router


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


def _run(agent_type: str, position: int, status: str = "completed", warnings=()):
    return {
        "run_id": RUN_ID,
        "node_id": f"node-{position}",
        "session_id": SESSION_ID,
        "status": "running",
        "node": {
            "node_id": f"node-{position}",
            "position": position,
            "target_name": agent_type,
            "title": agent_type,
            "params": {},
        },
        "scratchpad_warnings": {
            "schema_version": 1,
            "items": [{"level": "warning", "message": message} for message in warnings],
        },
        "scratchpad_summary": {
            "schema_version": 1,
            "agent_output": {
                "agent_role": "test_role",
                "status": status,
                "recorded_at": "2026-09-15T10:00:00+00:00",
                "inputs": {"session_id": "session-1"},
                "outputs": {
                    "files": {"artifact": "outputs/artifact.json"},
                    "metrics": {"count": 2},
                    "summary": "Completed deterministic test work.",
                },
                "errors": [],
            },
        },
    }


@pytest.mark.contract
@pytest.mark.asyncio
async def test_output_is_written_to_this_run_and_read_back(api):
    written = {}

    def _capture(request):
        written.update(json.loads(request.content))
        return httpx.Response(200, json=_run("segmentation_agent", 1))

    api.put(f"{API}/runs/{RUN_ID}/output").mock(side_effect=_capture)
    api.get(
        f"{API}/runs/session/{SESSION_ID}/by-target/segmentation_agent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "run": _run("segmentation_agent", 1),
                "nodes_sharing_this_target": 1,
            },
        )
    )

    confirmation = json.loads(
        await module.write_agent_output(_input("segmentation_agent"))
    )
    stored = json.loads(
        await module.read_agent_output(
            module.ReadAgentOutputInput(agent_id="segmentation_agent")
        )
    )

    assert confirmation["status"] == "ok"
    # The success result keeps its shape (plan.md: tool result shapes do not change).
    assert confirmation["agent_run_id"] == RUN_ID
    assert confirmation["step_id"] == "node-1"
    # The document goes to the run, and the agent cannot name someone else's.
    assert written["status"] == "completed"
    assert written["summary"] == "Completed deterministic test work."
    assert "agent_id" not in written
    assert stored["agent_id"] == "segmentation_agent"
    assert stored["outputs"]["files"]["artifact"] == "outputs/artifact.json"
    assert stored["outputs"]["metrics"]["count"] == 2
    assert stored["timestamp"].endswith("+00:00")
    assert stored["agent_run_id"] == RUN_ID
    assert stored["step_order"] == 1
    assert stored["steps_sharing_this_agent_type"] == 1


@pytest.mark.contract
@pytest.mark.asyncio
async def test_an_agent_with_no_run_cannot_write_an_output(api, monkeypatch):
    monkeypatch.setattr(module, "RUN_ID", "")

    result = json.loads(await module.write_agent_output(_input("segmentation_agent")))

    assert result["status"] == "error"
    assert "RUN_ID" in result["message"]


@pytest.mark.contract
@pytest.mark.asyncio
async def test_aggregate_status_prioritizes_failure_then_partial(api):
    runs = [
        _run("study_population_planner_agent", 1, "completed"),
        _run("feature_extraction_agent", 2, "partial"),
    ]
    route = api.get(f"{API}/runs/session/{SESSION_ID}").mock(
        return_value=httpx.Response(200, json=runs)
    )

    partial = json.loads(await module.read_all_agent_outputs())
    assert partial["agent_count"] == 2
    assert partial["pipeline_status"] == "has_partial"

    route.mock(
        return_value=httpx.Response(
            200, json=runs + [_run("modeling_agent", 3, "failed")]
        )
    )
    failed = json.loads(await module.read_all_agent_outputs())
    assert failed["agent_count"] == 3
    assert failed["pipeline_status"] == "has_failures"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_two_steps_sharing_an_agent_type_keep_separate_outputs(api):
    """The second step must not overwrite the first one's result."""
    first = _run("segmentation_agent", 1, "completed")
    second = _run("segmentation_agent", 3, "partial")
    api.get(f"{API}/runs/session/{SESSION_ID}").mock(
        return_value=httpx.Response(200, json=[first, second])
    )

    result = json.loads(await module.read_all_agent_outputs())

    assert result["agent_count"] == 2
    assert set(result["agents"]) == {"segmentation_agent", "segmentation_agent#3"}


@pytest.mark.contract
@pytest.mark.asyncio
async def test_a_step_with_no_output_yet_is_not_reported_as_one(api):
    pending = _run("modeling_agent", 2)
    pending["scratchpad_summary"] = None
    api.get(f"{API}/runs/session/{SESSION_ID}").mock(
        return_value=httpx.Response(200, json=[pending])
    )

    result = json.loads(await module.read_all_agent_outputs())

    assert result == {"agent_count": 0, "agents": {}, "pipeline_status": "no_outputs"}


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
