"""The agent registry (merged plan, Phase 1.3 step 2).

Agents are kept apart from tools: an agent is never a tool, and the catalog
refuses a name used in both registries. The rules that relate agents to each
other (delegates, cycles, parents) are checked by ``catalog.validate()``.
"""

from __future__ import annotations

from ..registry import Registry, UnknownNameError
from .spec import AgentKind, AgentSpec


class UnknownAgentError(UnknownNameError):
    kind = "agent"


class AgentRegistry(Registry[AgentSpec]):
    item_type = AgentSpec
    key = "agent_type"
    unknown_error = UnknownAgentError

    def of_kind(self, kind: AgentKind | str) -> tuple[AgentSpec, ...]:
        kind = AgentKind(kind)
        return tuple(agent for agent in self if agent.kind is kind)

    def graph_agents(self) -> tuple[AgentSpec, ...]:
        """The agents that can be submitted as Agent Nodes."""
        return self.of_kind(AgentKind.EXECUTION)

    def control_agents(self) -> tuple[AgentSpec, ...]:
        """main_agent, composer and recovery_agent."""
        return self.of_kind(AgentKind.CONTROL)

    def children_of(self, agent_type: str) -> tuple[AgentSpec, ...]:
        """The internal agents ``agent_type`` owns (their ``parent``)."""
        self.get(agent_type)  # an unknown parent is an error, not an empty answer
        return tuple(agent for agent in self if agent.parent == agent_type)
