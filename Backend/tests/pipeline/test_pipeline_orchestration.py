"""`run_pipeline` executes the session's approved runs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app as app_module
from models.session import Session


AGENT_ORDER = (
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
)

SESSION_ID = "44444444-4444-4444-8444-444444444444"


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


def _api_runs():
    return [
        {
            "agent_run_id": f"run-{order}",
            "step_id": f"step-{order}",
            "session_id": SESSION_ID,
            "status": "pending",
            "compiled_prompt": None,
            "output_summary": None,
            "step": {
                "step_id": f"step-{order}",
                "step_order": order,
                "agent_type": agent_id,
                "title": agent_id,
                "instructions": {
                    "task_message": {
                        "task_id": f"task-{order}",
                        "instructions": f"Execute {agent_id}",
                    }
                },
            },
        }
        for order, agent_id in enumerate(AGENT_ORDER, start=1)
    ]


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_run_pipeline_builds_the_graph_from_the_approved_runs(monkeypatch):
    session = Session(SESSION_ID)

    monkeypatch.setattr(
        app_module.BackendClient,
        "get_agent_runs",
        AsyncMock(return_value=_api_runs()),
    )
    monkeypatch.setattr(
        app_module.BackendClient,
        "get_session",
        AsyncMock(return_value={"prompt": "does bin width change AUC?"}),
    )
    monkeypatch.setattr(
        app_module.BackendClient,
        "get_agent_run_status",
        AsyncMock(return_value={"schema_version": 1, "agents": []}),
    )

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
    assert [run.agent_type for run in call.kwargs["runs"]] == list(AGENT_ORDER)
    assert [run.agent_run_id for run in call.kwargs["runs"]] == [
        f"run-{order}" for order in range(1, len(AGENT_ORDER) + 1)
    ]
    assert call.kwargs["session_id"] == session.session_id
    # Each node claims and closes its own run; the callbacks are wired, not
    # inferred from streamed events after the fact.
    assert callable(call.kwargs["on_start"])
    assert callable(call.kwargs["on_finish"])
    assert callable(call.kwargs["on_failure"])
    monitor.on_pipeline_complete.assert_awaited_once()
    send_ws.assert_any_await(
        "run_finished",
        "run_finished",
        session_id=session.session_id,
    )


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_run_pipeline_refuses_a_session_with_no_approved_runs(monkeypatch):
    """A draft plan owns no runs, so there is nothing to execute — and nothing to reconstruct from a file either."""
    session = Session(SESSION_ID)
    monkeypatch.setattr(
        app_module.BackendClient, "get_agent_runs", AsyncMock(return_value=[])
    )

    with pytest.raises(RuntimeError, match="no approved agent runs"):
        await app_module.run_pipeline(session)


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_run_pipeline_resumes_at_the_recorded_step(monkeypatch):
    session = Session(SESSION_ID)
    session.resume_from_step_id = "step-3"

    runs = _api_runs()
    runs[0]["status"] = "completed"
    runs[1]["status"] = "completed"
    monkeypatch.setattr(
        app_module.BackendClient, "get_agent_runs", AsyncMock(return_value=runs)
    )
    monkeypatch.setattr(
        app_module.BackendClient, "get_session", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        app_module.BackendClient,
        "get_agent_run_status",
        AsyncMock(return_value=None),
    )

    builder = SimpleNamespace(
        get_participants=lambda: [],
        build=lambda: "test-graph",
        get_pipeline_prompt=lambda fallback_hypothesis="": "compiled test prompt",
    )
    create_builder = AsyncMock(return_value=builder)
    monkeypatch.setattr(app_module.BasicPipelineBuilder, "create", create_builder)
    monkeypatch.setattr(app_module, "GraphFlow", FakeGraphFlow)
    monkeypatch.setattr(
        app_module, "create_model_client_from_config", lambda: FakeModelClient()
    )
    monkeypatch.setattr(
        app_module,
        "PipelineMonitor",
        lambda *args, **kwargs: SimpleNamespace(
            on_pipeline_complete=AsyncMock(),
            on_pipeline_error=AsyncMock(),
            on_agent_active=AsyncMock(),
            on_tool_result=AsyncMock(return_value=False),
        ),
    )
    monkeypatch.setattr(app_module.ws_manager, "send_ws_message", AsyncMock())

    await app_module.run_pipeline(session)

    assert create_builder.await_args.kwargs["start_step_id"] == "step-3"
