"""The execution graph is built from approved runs, one node per step."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.run_context import planned_runs
from teams import execution_team as module


@dataclass
class FakeAgent:
    name: str
    description: str = "fake"


class FakeMessageFilterAgent:
    def __init__(self, name, wrapped_agent, filter):
        self.name = name
        self.wrapped_agent = wrapped_agent
        self.filter = filter
        self.description = "filtered"


class FakeRunBoundAgent:
    def __init__(self, name, wrapped_agent, run, *, on_start, on_finish, on_failure):
        self.name = name
        self.wrapped_agent = wrapped_agent
        self.run = run
        self.on_start = on_start
        self.on_finish = on_finish
        self.on_failure = on_failure

    @property
    def filter(self):
        return getattr(self.wrapped_agent, "filter", None)


@pytest.fixture
def isolated_builder(monkeypatch):
    created: list[tuple[str, dict]] = []

    def make_factory(agent_id):
        async def _factory(**kwargs):
            created.append((agent_id, kwargs))
            return FakeAgent(kwargs.get("node_name") or agent_id)

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
    monkeypatch.setattr(module, "RunBoundAgent", FakeRunBoundAgent)
    monkeypatch.setattr(module, "setup_logging", lambda: None)

    def _init(self, runs=None):
        self.runs = runs or []
        self.entry_run = None
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


def _runs(*agent_ids: str, statuses: dict[int, str] | None = None):
    statuses = statuses or {}
    return planned_runs(
        [
            {
                "agent_run_id": f"run-{order}",
                "step_id": f"step-{order}",
                "session_id": "11111111-1111-4111-8111-111111111111",
                "status": statuses.get(order, "pending"),
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
            for order, agent_id in enumerate(agent_ids, start=1)
        ]
    )


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_creates_one_node_per_approved_run(isolated_builder):
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
        runs=_runs(*agent_order),
        model_client="model",
        session_id="session-full",
    )

    assert [agent_id for agent_id, _ in isolated_builder] == list(agent_order)
    assert len(builder.recorded_nodes) == len(agent_order)
    assert len(builder.recorded_edges) == len(agent_order) - 1
    # Every node carries the run it will claim, and the factory was told which
    # run and step it belongs to so its MCP subprocess writes the right row.
    for (_, kwargs), node in zip(isolated_builder, builder.recorded_nodes):
        assert kwargs["agent_run_id"] == node.run.agent_run_id
        assert kwargs["step_id"] == node.run.step_id
        assert kwargs["node_name"] == node.name


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_two_steps_with_the_same_agent_type_are_two_nodes(isolated_builder):
    """The second step must not be de-duplicated away."""
    builder = await module.BasicPipelineBuilder.create(
        workspace_folder="workspace",
        runs=_runs(
            "study_population_planner_agent",
            "segmentation_agent",
            "segmentation_agent",
            "result_reporter_agent",
        ),
        model_client="model",
        session_id="session-1",
    )

    assert [agent_id for agent_id, _ in isolated_builder] == [
        "study_population_planner_agent",
        "segmentation_agent",
        "segmentation_agent",
        "result_reporter_agent",
    ]
    names = [node.name for node in builder.recorded_nodes]
    assert len(set(names)) == 4
    # Each node filters on the NODE before it, so the two segmentation steps
    # stay ordered rather than collapsing onto one source.
    assert builder.recorded_nodes[2].filter.per_source[0].source == names[1]
    assert builder.recorded_nodes[3].filter.per_source[0].source == names[2]


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_resumes_at_a_step_not_at_an_agent_name(isolated_builder):
    runs = _runs(
        "study_population_planner_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",
        statuses={1: "completed", 2: "completed"},
    )

    builder = await module.BasicPipelineBuilder.create(
        workspace_folder="workspace",
        runs=runs,
        model_client="model",
        session_id="session-1",
        start_step_id="step-3",
    )

    assert [agent_id for agent_id, _ in isolated_builder] == [
        "feature_extraction_agent",
        "modeling_agent",
    ]
    assert [run.step_id for run in builder.runs] == ["step-3", "step-4"]
    assert builder.entry_run.step_id == "step-3"


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_rejects_a_session_with_no_approved_runs(isolated_builder):
    with pytest.raises(ValueError, match="no approved"):
        await module.BasicPipelineBuilder.create(runs=[])


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_rejects_an_agent_type_with_no_implementation(isolated_builder):
    """Admission already rejects these; reaching the runner means the mirrored
    agent list drifted, and a step must never be silently skipped."""
    with pytest.raises(ValueError, match="no implementation"):
        await module.BasicPipelineBuilder.create(runs=_runs("unknown_agent"))


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_builder_rejects_invalid_resume_target(isolated_builder):
    with pytest.raises(ValueError, match="Cannot resume"):
        await module.BasicPipelineBuilder.create(
            runs=_runs("segmentation_agent", "modeling_agent"),
            start_step_id="step-99",
        )


@pytest.mark.pipeline
@pytest.mark.asyncio
async def test_entry_prompt_comes_from_the_first_step(isolated_builder):
    builder = await module.BasicPipelineBuilder.create(
        runs=_runs("study_population_planner_agent", "segmentation_agent"),
        session_id="session-1",
    )

    assert (
        builder.get_pipeline_prompt(fallback_hypothesis="raw prompt")
        == "Execute study_population_planner_agent"
    )
