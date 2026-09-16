"""Agent-type admission."""

from __future__ import annotations

import pytest

from application_api.core.agent_types import (
    CANONICAL_AGENT_TYPES,
    is_known_agent_type,
    unknown_agent_types,
)

pytestmark = pytest.mark.unit


def test_the_canonical_pipeline_order_is_recorded():
    assert CANONICAL_AGENT_TYPES == (
        "study_population_planner_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",
        "result_reporter_agent",
        "latex_reporter_agent",
    )


def test_known_keys_are_accepted():
    for agent_type in CANONICAL_AGENT_TYPES:
        assert is_known_agent_type(agent_type)


def test_a_hallucinated_agent_name_is_reported_not_dropped():
    unknown = unknown_agent_types(
        ["segmentation_agent", "statistics_agent", "modeling_agent"]
    )
    assert unknown == ["statistics_agent"]


def test_duplicates_are_reported_once_and_order_is_preserved():
    assert unknown_agent_types(["b_agent", "a_agent", "b_agent"]) == [
        "b_agent",
        "a_agent",
    ]


def test_the_same_agent_twice_is_legitimate():
    """Two distinct steps may name one agent and execute separately — runner identity is step_id, not the agent selector."""
    assert unknown_agent_types(["segmentation_agent", "segmentation_agent"]) == []


def test_an_empty_key_is_unknown():
    assert unknown_agent_types([""]) == [""]
    assert not is_known_agent_type("")
