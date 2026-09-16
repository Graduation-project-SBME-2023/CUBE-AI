"""Canonical agent ids a plan step may name."""

from __future__ import annotations

# Mirrors Backend/agent_system/teams/execution_team.py: AGENT_FACTORIES.
CANONICAL_AGENT_TYPES: tuple[str, ...] = (
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
)


def is_known_agent_type(agent_type: str) -> bool:
    return agent_type in CANONICAL_AGENT_TYPES


def unknown_agent_types(agent_types: list[str]) -> list[str]:
    """Return the keys that no agent implements, preserving order."""
    seen: list[str] = []
    for agent_type in agent_types:
        if not is_known_agent_type(agent_type) and agent_type not in seen:
            seen.append(agent_type)
    return seen
