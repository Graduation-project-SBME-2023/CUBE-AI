"""AgentRegistry (merged plan, Phase 1.3 step 2)."""

from __future__ import annotations

from typing import Any

import pytest

from agent_system.toolkit.agents.registry import AgentRegistry, UnknownAgentError
from agent_system.toolkit.agents.spec import AgentKind, AgentSpec

pytestmark = pytest.mark.unit


def _agent(agent_type: str, kind: str, parent: str | None = None, **extra: Any) -> AgentSpec:
    return AgentSpec(
        agent_type=agent_type,
        description=f"{agent_type}.",
        kind=kind,
        system_prompt="Prompt.",
        parent=parent,
        **extra,
    )


AGENTS = [
    _agent("main_agent", "control"),
    _agent("composer", "control"),
    _agent("segmentation_agent", "execution"),
    _agent("clinical_target_intent_agent", "internal", parent="segmentation_agent"),
    _agent("mask_resolution_agent", "internal", parent="segmentation_agent"),
    _agent("literature_review_agent", "execution"),
    _agent("pico_analyst", "internal", parent="literature_review_agent"),
]


def _registry() -> AgentRegistry:
    return AgentRegistry(AGENTS)


def _types(agents: tuple[AgentSpec, ...]) -> list[str]:
    return [agent.agent_type for agent in agents]


def test_register_and_get_by_agent_type():
    registry = _registry()

    assert registry.get("segmentation_agent") is AGENTS[2]
    assert "pico_analyst" in registry
    assert len(registry) == 7
    assert registry.names() == {agent.agent_type for agent in AGENTS}


def test_unknown_agent():
    with pytest.raises(UnknownAgentError, match="unknown agent 'modeling_agent'"):
        _registry().get("modeling_agent")


def test_duplicate_agent_types_are_refused():
    registry = _registry()
    registry.register(AGENTS[0])  # the same object again is harmless

    with pytest.raises(ValueError, match="agent 'main_agent' is already registered"):
        registry.register(_agent("main_agent", "control"))


def test_only_agent_specs_are_registered():
    with pytest.raises(TypeError, match="AgentSpec expected"):
        AgentRegistry().register(object())  # type: ignore[arg-type]


def test_graph_and_control_agents():
    registry = _registry()

    assert _types(registry.graph_agents()) == ["segmentation_agent", "literature_review_agent"]
    assert _types(registry.control_agents()) == ["main_agent", "composer"]
    assert _types(registry.of_kind("internal")) == [
        "clinical_target_intent_agent",
        "mask_resolution_agent",
        "pico_analyst",
    ]
    assert registry.of_kind(AgentKind.EXECUTION) == registry.graph_agents()


def test_of_kind_refuses_an_unknown_kind():
    with pytest.raises(ValueError):
        _registry().of_kind("planner")


def test_children_of():
    registry = _registry()

    assert _types(registry.children_of("segmentation_agent")) == [
        "clinical_target_intent_agent",
        "mask_resolution_agent",
    ]
    assert _types(registry.children_of("literature_review_agent")) == ["pico_analyst"]
    assert registry.children_of("main_agent") == ()


def test_children_of_an_unknown_agent():
    with pytest.raises(UnknownAgentError):
        _registry().children_of("modeling_agent")
