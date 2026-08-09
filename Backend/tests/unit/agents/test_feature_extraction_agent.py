from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.execution_agents import feature_extraction_agent as module


EXPECTED_TOOLS = [
    "read_agent_task_context",
    "radiomics_extract_features",
    "radiomics_analyze_importance",
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
async def test_create_requires_workspace_and_session(workspace_dir):
    with pytest.raises(ValueError, match="workspace_folder"):
        await module.FeatureExtractionAgent.create(
            model_client=object(),
            workspace_folder=None,
            session_id="session-1",
        )

    with pytest.raises(ValueError, match="session_id"):
        await module.FeatureExtractionAgent.create(
            model_client=object(),
            workspace_folder=str(workspace_dir),
            session_id=None,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_selects_exact_feature_tool_contract(
    monkeypatch,
    workspace_dir,
    named_tools,
    capture_agent_init,
):
    captured = capture_agent_init(module.FeatureExtractionAgent)
    discover = AsyncMock(
        return_value=named_tools(*EXPECTED_TOOLS, "unrelated_tool")
    )
    monkeypatch.setattr(module, "mcp_server_tools", discover)

    await module.FeatureExtractionAgent.create(
        model_client=object(),
        workspace_folder=str(workspace_dir),
        session_id="session-features",
        max_tool_iterations=17,
    )

    assert captured["name"] == "feature_extraction_agent"
    assert captured["max_tool_iterations"] == 17
    assert [tool.name for tool in captured["tools"]] == EXPECTED_TOOLS
    server = discover.await_args.args[0]
    assert server.env["AGENT_ID"] == "feature_extraction_agent"

