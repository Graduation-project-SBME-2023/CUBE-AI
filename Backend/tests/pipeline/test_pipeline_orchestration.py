from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app as app_module
from models.plans import (
    ClinicalHypothesis,
    PlannerState,
    StudyPlan,
    StudyStep,
    TaskMessage,
)
from models.session import Session
from storage_paths import workspace, workspace_subpath


AGENT_ORDER = (
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
)


class FakeModelClient:
    async def create(self, *args, **kwargs):
        return None

    async def create_stream(self, *args, **kwargs):
        if False:
            yield None


class FakeGraphFlow:
    def __init__(self, participants, graph):
        self.participants = participants
        self.graph = graph

    async def run_stream(self, task, cancellation_token):
        yield app_module.TaskResult(messages=[], stop_reason="test complete")


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_run_pipeline_completes_mocked_full_agent_flow(monkeypatch):
    session = Session("pipeline-orchestration-test")
    workspace_dir = workspace(session.session_id)
    state_path = workspace_subpath(workspace_dir, "state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)

    task_messages = [
        TaskMessage(
            task_id=f"task-{index}",
            phase=f"phase-{index}",
            agent_id=agent_id,
            agent_title=agent_id,
            instructions=f"Execute {agent_id}",
        )
        for index, agent_id in enumerate(AGENT_ORDER)
    ]
    state = PlannerState(
        hypothesis=ClinicalHypothesis(title="Deterministic pipeline test"),
        plan=StudyPlan(
            steps=[
                StudyStep(
                    title=message.agent_title,
                    description=message.instructions,
                    module=message.agent_id,
                    task_message=message,
                )
                for message in task_messages
            ]
        ),
    )
    state_path.write_text(state.model_dump_json(), encoding="utf-8")

    builder = SimpleNamespace(
        get_participants=lambda: [SimpleNamespace(name=name) for name in AGENT_ORDER],
        build=lambda: "test-graph",
        get_pipeline_prompt=lambda fallback_hypothesis="": "compiled test prompt",
    )
    create_builder = AsyncMock(return_value=builder)
    monkeypatch.setattr(app_module.BasicPipelineBuilder, "create", create_builder)
    monkeypatch.setattr(app_module, "GraphFlow", FakeGraphFlow)
    monkeypatch.setattr(
        app_module,
        "create_model_client_from_config",
        lambda: FakeModelClient(),
    )

    monitor = SimpleNamespace(
        _broadcast=AsyncMock(),
        on_pipeline_complete=AsyncMock(),
        on_pipeline_error=AsyncMock(),
        on_agent_active=AsyncMock(),
        on_tool_result=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(app_module, "PipelineMonitor", lambda *args, **kwargs: monitor)
    send_ws = AsyncMock()
    monkeypatch.setattr(app_module.ws_manager, "send_ws_message", send_ws)

    await app_module.run_pipeline(session)

    create_builder.assert_awaited_once()
    call = create_builder.await_args
    assert [message.agent_id for message in call.kwargs["task_messages"]] == list(
        AGENT_ORDER
    )
    assert call.kwargs["session_id"] == session.session_id
    monitor.on_pipeline_complete.assert_awaited_once()
    send_ws.assert_any_await(
        "run_finished",
        "run_finished",
        session_id=session.session_id,
    )
