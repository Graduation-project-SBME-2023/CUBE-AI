from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agents.execution_agents import population_agent as module


BASE_TOOLS = (
    "get_unique_series_descriptions",
    "search_volumes_by_description",
    "validate_population_anatomy",
    "extract_cohort_labels",
    "write_inclusion_criteria",
    "generate_targets",
    "read_agent_task_context",
    "scratchpad_register_agent",
    "scratchpad_raise_flag",
    "scratchpad_write_summary",
    "scratchpad_set_status",
    "write_agent_output",
    "unrelated_tool",
)


def _tool_names(captured: dict) -> list[str]:
    return [tool.name for tool in captured["tools"]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_manual_mode_selects_only_population_tools(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    captured = capture_agent_init(module.PopulationPlannerAgent)
    discover = AsyncMock(return_value=named_tools(*BASE_TOOLS))
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    model_client = object()
    agent = await module.PopulationPlannerAgent.create(
        model_client=model_client,
        workspace_folder=str(workspace_dir),
        session_id="session-123",
    )

    assert isinstance(agent, module.PopulationPlannerAgent)
    assert captured["name"] == "study_population_planner_agent"
    assert captured["model_client"] is model_client
    assert captured["max_tool_iterations"] == 20
    assert "extract_cohort_labels" not in _tool_names(captured)
    assert "unrelated_tool" not in _tool_names(captured)

    server = discover.await_args.args[0]
    assert server.env["WORKSPACE_DIR"] == str(workspace_dir)
    assert server.env["SESSION_ID"] == "session-123"
    assert server.env["AGENT_ID"] == "study_population_planner_agent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_automatic_mode_enables_cohort_label_extraction(
    monkeypatch,
    workspace_dir,
    write_workspace_json,
    named_tools,
    capture_agent_init,
):
    write_workspace_json(
        workspace_dir,
        "config.json",
        {"labelling_mode": "automatic"},
    )
    captured = capture_agent_init(module.PopulationPlannerAgent)
    monkeypatch.setattr(
        module,
        "mcp_server_tools",
        AsyncMock(return_value=named_tools(*BASE_TOOLS)),
    )

    await module.PopulationPlannerAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id="session-automatic",
    )

    assert "extract_cohort_labels" in _tool_names(captured)


@pytest.mark.unit
def test_get_desired_tools_preserves_discovery_order(named_tools):
    discovered = named_tools("second", "ignored", "first")

    selected = module.PopulationPlannerAgent.get_desired_tools(
        discovered,
        ["first", "second"],
    )

    assert [tool.name for tool in selected] == ["second", "first"]

