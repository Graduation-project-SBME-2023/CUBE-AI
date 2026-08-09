from __future__ import annotations

from dataclasses import dataclass

import pytest

from models.plans import TaskMessage
from teams import execution_team as module


@dataclass
class FakeAgent:
    name: str


class FakeMessageFilterAgent:
    def __init__(self, name, wrapped_agent, filter):
        self.name = name
        self.wrapped_agent = wrapped_agent
        self.filter = filter


@pytest.fixture
def isolated_builder(monkeypatch):
    created: list[tuple[str, dict]] = []

    def make_factory(agent_id):
        async def _factory(**kwargs):
            created.append((agent_id, kwargs))
            return FakeAgent(agent_id)

        return _factory

    factories = {
        agent_id: make_factory(agent_id)
        for agent_id in (
            "study_population_planner_agent",
            "segmentation_agent",
            "feature_extraction_agent",
            "modeling_agent",
            "result_reporter_agent",
            "latex_reporter_agent",
        )
    }
    monkeypatch.setattr(module, "AGENT_FACTORIES", factories)
    monkeypatch.setattr(module, "MessageFilterAgent", FakeMessageFilterAgent)
    monkeypatch.setattr(module, "setup_logging", lambda: None)

    def _init(self, task_messages=None):
        self.task_messages = task_messages or []
        self.recorded_nodes = []
        self.recorded_edges = []

    def _add_node(self, node):
        self.recorded_nodes.append(node)

    def _add_edge(self, source, target):
        self.recorded_edges.append((source, target))

    monkeypatch.setattr(module.BasicPipelineBuilder, "__init__", _init)
    monkeypatch.setattr(module.BasicPipelineBuilder, "add_node", _add_node)
    monkeypatch.setattr(module.BasicPipelineBuilder, "add_edge", _add_edge)
    return created


def _messages(*agent_ids: str) -> list[TaskMessage]:
    return [
        TaskMessage(task_id=f"task-{index}", agent_id=agent_id)
        for index, agent_id in enumerate(agent_ids)
    ]


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_creates_complete_six_agent_pipeline(isolated_builder):
    agent_order = (
        "study_population_planner_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",
        "result_reporter_agent",
        "latex_reporter_agent",
    )

    builder = await module.BasicPipelineBuilder.create(
        workspace_folder="workspace",
        task_messages=_messages(*agent_order),
        model_client="model",
        session_id="session-full",
    )

    assert [agent_id for agent_id, _ in isolated_builder] == list(agent_order)
    assert [node.name for node in builder.recorded_nodes] == list(agent_order)
    assert len(builder.recorded_edges) == len(agent_order) - 1
    for index, node in enumerate(builder.recorded_nodes[1:], start=1):
        assert node.filter.per_source[0].source == agent_order[index - 1]


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_honors_planner_order_deduplicates_and_filters_unknown(
    isolated_builder,
):
    task_messages = _messages(
        "study_population_planner_agent",
        "unknown_agent",
        "segmentation_agent",
        "segmentation_agent",
        "result_reporter_agent",
    )

    builder = await module.BasicPipelineBuilder.create(
        workspace_folder="workspace",
        task_messages=task_messages,
        model_client="model",
        session_id="session-1",
    )

    assert [agent_id for agent_id, _ in isolated_builder] == [
        "study_population_planner_agent",
        "segmentation_agent",
        "result_reporter_agent",
    ]
    assert [node.name for node in builder.recorded_nodes] == [
        "study_population_planner_agent",
        "segmentation_agent",
        "result_reporter_agent",
    ]
    assert len(builder.recorded_edges) == 2
    assert builder.recorded_nodes[1].wrapped_agent.name == "segmentation_agent"
    assert builder.recorded_nodes[1].filter.per_source[0].source == (
        "study_population_planner_agent"
    )
    assert builder.recorded_nodes[2].filter.per_source[0].source == (
        "segmentation_agent"
    )


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_resume_starts_at_requested_agent(isolated_builder):
    builder = await module.BasicPipelineBuilder.create(
        workspace_folder="workspace",
        task_messages=_messages(
            "study_population_planner_agent",
            "segmentation_agent",
            "feature_extraction_agent",
            "modeling_agent",
        ),
        model_client="model",
        session_id="session-1",
        start_agent_id="feature_extraction_agent",
    )

    assert [agent_id for agent_id, _ in isolated_builder] == [
        "feature_extraction_agent",
        "modeling_agent",
    ]
    assert isinstance(builder.recorded_nodes[0], FakeAgent)
    assert builder.recorded_nodes[1].filter.per_source[0].source == (
        "feature_extraction_agent"
    )
    assert [message.agent_id for message in builder.task_messages] == [
        "feature_extraction_agent",
        "modeling_agent",
    ]


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_rejects_empty_or_unknown_plan(isolated_builder):
    with pytest.raises(ValueError, match="no valid task messages"):
        await module.BasicPipelineBuilder.create(
            task_messages=_messages("unknown_agent"),
        )


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_rejects_invalid_resume_target(isolated_builder):
    with pytest.raises(ValueError, match="Cannot resume"):
        await module.BasicPipelineBuilder.create(
            task_messages=_messages("segmentation_agent", "modeling_agent"),
            start_agent_id="feature_extraction_agent",
        )
