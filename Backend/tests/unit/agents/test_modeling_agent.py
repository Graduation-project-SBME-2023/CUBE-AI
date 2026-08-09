from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.execution_agents import modeling_agent as module


EXPECTED_TOOLS = [
    "read_agent_task_context",
    "pycaret_train_classifier",
    "pycaret_run_inference",
    "scratchpad_register_agent",
    "scratchpad_write_note",
    "scratchpad_raise_flag",
    "scratchpad_write_summary",
    "scratchpad_set_status",
    "scratchpad_read_agent",
    "write_agent_output",
    "browse_workspace",
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_selects_exact_modeling_tool_contract(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    captured = capture_agent_init(module.ModelingAgent)
    discover = AsyncMock(
        return_value=named_tools(*EXPECTED_TOOLS, "unrelated_tool")
    )
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    await module.ModelingAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id="session-model",
    )

    assert captured["name"] == "modeling_agent"
    assert captured["max_tool_iterations"] == 20
    assert [tool.name for tool in captured["tools"]] == EXPECTED_TOOLS
    server = discover.await_args.args[0]
    assert server.env["WORKSPACE_DIR"] == str(workspace_dir)
    assert server.env["SESSION_ID"] == "session-model"
    assert server.env["AGENT_ID"] == "modeling_agent"


@pytest.mark.unit
def test_get_desired_tools_does_not_expose_unrequested_tools(named_tools):
    discovered = named_tools("pycaret_train_classifier", "admin_tool")

    selected = module.ModelingAgent.get_desired_tools(
        discovered,
        ["pycaret_train_classifier"],
    )

    assert [tool.name for tool in selected] == ["pycaret_train_classifier"]

