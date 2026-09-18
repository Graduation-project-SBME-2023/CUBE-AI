from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.execution_agents import segmentation_agent as module
from storage_paths import workspace_subpath


@pytest.mark.unit
@pytest.mark.parametrize("session_id", [None, "", ".", "..", "a/b", r"a\b"])
def test_runtime_context_rejects_invalid_session_ids(workspace_dir, session_id):
    with pytest.raises(ValueError, match="session_id"):
        module._validated_runtime_context(str(workspace_dir), session_id)


@pytest.mark.unit
def test_runtime_context_requires_existing_workspace(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        module._validated_runtime_context(
            str(tmp_path / "missing"),
            "session-1",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_wires_guarded_segmentation_tool_and_context_tools(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    captured = capture_agent_init(module.SegmentationAgent)
    discover = AsyncMock(
        return_value=named_tools(
            "read_agent_task_context",
            "scratchpad_register_agent",
            "scratchpad_write_summary",
            "scratchpad_set_status",
            "scratchpad_raise_flag",
            "unrelated_tool",
        )
    )
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    await module.SegmentationAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id="session-seg",
    )

    names = [tool.name for tool in captured["tools"]]
    assert captured["name"] == "segmentation_agent"
    assert names == [
        "run_segmentation_with_guard",
        "read_agent_task_context",
        "scratchpad_register_agent",
        "scratchpad_write_summary",
        "scratchpad_set_status",
        "scratchpad_raise_flag",
    ]
    server = discover.await_args.args[0]
    assert server.env["AGENT_ID"] == "segmentation_agent"
    assert server.env["SESSION_ID"] == "session-seg"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_segmentation_tool_closes_module_and_writes_structured_output(
    monkeypatch,
    workspace_dir,
):
    """The structured result is a row on this step's run."""
    fake_module = SimpleNamespace(
        initialize=AsyncMock(),
        run=AsyncMock(
            return_value={
                "status": "completed",
                "segmentation_run_id": "seg-run-1",
                "segmentation_method": "cached",
                "targets": ["spleen"],
                "pair_count": 2,
                "accepted_count": 2,
                "reused_count": 2,
                "generated_count": 0,
                "failures": [],
            }
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        module, "SegmentationModule", lambda session_id, agent_run_id=None: fake_module
    )
    written = AsyncMock(return_value={})
    monkeypatch.setattr(module.BackendClient, "write_agent_output", written)

    result = await module.segmentation_tool(
        workspace_folder=str(workspace_dir),
        session_id="session-seg",
        agent_run_id="run-seg",
    )

    assert result["status"] == "completed"
    fake_module.initialize.assert_awaited_once()
    fake_module.close.assert_awaited_once()
    # The batch is run through PostgreSQL; the workspace keeps no manifest.
    assert not workspace_subpath(workspace_dir, "outputs").exists()
    run_id, output = written.await_args.args
    assert run_id == "run-seg"
    assert output["status"] == "completed"
    assert output["output_metrics"]["accepted_count"] == 2
    assert output["output_files"]["segmentation_run_id"] == "seg-run-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_segmentation_tool_refuses_to_run_without_a_run_to_record_on(
    monkeypatch,
    workspace_dir,
):
    fake_module = SimpleNamespace(
        initialize=AsyncMock(),
        run=AsyncMock(return_value={"status": "completed", "failures": []}),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        module, "SegmentationModule", lambda session_id, agent_run_id=None: fake_module
    )
    with pytest.raises(RuntimeError, match="agent_run_id"):
        await module.segmentation_tool(
            workspace_folder=str(workspace_dir),
            session_id="session-seg",
        )
