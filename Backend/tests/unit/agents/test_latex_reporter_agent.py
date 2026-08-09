from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.execution_agents import reporter_agent as module


EXPECTED_TOOLS = [
    "read_agent_task_context",
    "scratchpad_read_report",
    "read_all_agent_outputs",
    "list_latex_templates",
    "get_latex_template",
    "read_literature_context",
    "write_agent_output",
    "latex_to_pdf",
    "list_workspace_plots",
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_selects_latex_reporting_tools_only(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    captured = capture_agent_init(module.ReporterAgent)
    discover = AsyncMock(
        return_value=named_tools(*EXPECTED_TOOLS, "database_delete")
    )
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    await module.ReporterAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id="session-latex",
    )

    assert captured["name"] == "latex_reporter_agent"
    assert captured["max_tool_iterations"] == 20
    assert [tool.name for tool in captured["tools"]] == EXPECTED_TOOLS
    server = discover.await_args.args[0]
    assert server.env["AGENT_ID"] == "latex_reporter_agent"

