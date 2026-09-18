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


SESSION_ID = "44444444-4444-4444-8444-444444444444"


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
    monkeypatch.setattr(
        module.BackendClient,
        "get_population_cohort",
        AsyncMock(return_value={"criteria": {"schema_version": 1, "labelling_mode": "manual"}}),
    )
    captured = capture_agent_init(module.PopulationPlannerAgent)
    discover = AsyncMock(return_value=named_tools(*BASE_TOOLS))
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    model_client = object()
    agent = await module.PopulationPlannerAgent.create(
        model_client=model_client,
        workspace_folder=str(workspace_dir),
        session_id=SESSION_ID,
    )

    assert isinstance(agent, module.PopulationPlannerAgent)
    assert captured["name"] == "study_population_planner_agent"
    assert captured["model_client"] is model_client
    assert captured["max_tool_iterations"] == 20
    assert "extract_cohort_labels" not in _tool_names(captured)
    assert "unrelated_tool" not in _tool_names(captured)

    server = discover.await_args.args[0]
    assert server.env["WORKSPACE_DIR"] == str(workspace_dir)
    assert server.env["SESSION_ID"] == SESSION_ID
    assert server.env["AGENT_ID"] == "study_population_planner_agent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_automatic_mode_enables_cohort_label_extraction(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    """the database-backed flow: the labelling mode is `cohorts.criteria.labelling_mode`, which the Population UI sets before the step starts — not config.json."""
    cohort = AsyncMock(
        return_value={"criteria": {"schema_version": 1, "labelling_mode": "automatic"}}
    )
    monkeypatch.setattr(module.BackendClient, "get_population_cohort", cohort)
    captured = capture_agent_init(module.PopulationPlannerAgent)
    monkeypatch.setattr(
        module,
        "mcp_server_tools",
        AsyncMock(return_value=named_tools(*BASE_TOOLS)),
    )

    await module.PopulationPlannerAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id=SESSION_ID,
    )

    assert "extract_cohort_labels" in _tool_names(captured)
    cohort.assert_awaited_once_with(SESSION_ID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_cohort_yet_keeps_the_uis_default_of_automatic(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    monkeypatch.setattr(
        module.BackendClient, "get_population_cohort", AsyncMock(return_value=None)
    )
    captured = capture_agent_init(module.PopulationPlannerAgent)
    monkeypatch.setattr(
        module, "mcp_server_tools", AsyncMock(return_value=named_tools(*BASE_TOOLS))
    )

    await module.PopulationPlannerAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id=SESSION_ID,
    )

    assert "extract_cohort_labels" in _tool_names(captured)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_cohort_that_cannot_be_read_does_not_enable_llm_labelling(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    """Manual is the safe default: an unreadable cohort must not silently turn
    on automatic label extraction."""
    monkeypatch.setattr(
        module.BackendClient,
        "get_population_cohort",
        AsyncMock(side_effect=RuntimeError("application api is down")),
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
        session_id=SESSION_ID,
    )

    assert "extract_cohort_labels" not in _tool_names(captured)


@pytest.mark.unit
def test_get_desired_tools_preserves_discovery_order(named_tools):
    discovered = named_tools("second", "ignored", "first")

    selected = module.PopulationPlannerAgent.get_desired_tools(
        discovered,
        ["first", "second"],
    )

    assert [tool.name for tool in selected] == ["second", "first"]

