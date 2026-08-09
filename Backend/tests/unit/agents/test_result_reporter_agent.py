from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.execution_agents import result_reporter_agent as module


EXPECTED_TOOLS = [
    "read_agent_task_context",
    "get_modeling_results",
    "scratchpad_register_agent",
    "scratchpad_write_note",
    "scratchpad_raise_flag",
    "scratchpad_write_summary",
    "scratchpad_set_status",
    "scratchpad_read_agent",
    "write_agent_output",
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_selects_reporting_tools_only(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    captured = capture_agent_init(module.ResultReporterAgent)
    discover = AsyncMock(
        return_value=named_tools(*EXPECTED_TOOLS, "latex_to_pdf")
    )
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    await module.ResultReporterAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id="session-report",
    )

    assert captured["name"] == "result_reporter_agent"
    assert captured["max_tool_iterations"] == 10
    assert [tool.name for tool in captured["tools"]] == EXPECTED_TOOLS
    server = discover.await_args.args[0]
    assert server.env["AGENT_ID"] == "result_reporter_agent"

