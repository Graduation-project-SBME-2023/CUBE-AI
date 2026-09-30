"""The manifest vocabulary shared by ToolSpec, AgentSpec, the sandbox and the jobs."""

from __future__ import annotations

import pytest

from agent_system.toolkit.tools.manifest import (
    PHI_SCOPES,
    RESOURCE_QUEUES,
    WRITE_SCOPES,
    DataScope,
    Resource,
    Visibility,
    coerce_enum,
    coerce_enum_set,
)

pytestmark = pytest.mark.unit


def test_coerce_enum_accepts_member_or_string():
    assert coerce_enum(Resource, "gpu", "resources") is Resource.GPU
    assert coerce_enum(Resource, Resource.GPU, "resources") is Resource.GPU


def test_coerce_enum_names_the_field_and_the_allowed_values():
    with pytest.raises(ValueError, match=r"visibility='planner' is not one of: main, graph"):
        coerce_enum(Visibility, "planner", "visibility")


def test_coerce_enum_set_dedupes_and_refuses_a_bare_string():
    assert coerce_enum_set(Visibility, ["main", Visibility.MAIN, "agent"], "visibility") == (
        frozenset({Visibility.MAIN, Visibility.AGENT})
    )
    with pytest.raises(TypeError, match="not a string"):
        coerce_enum_set(Visibility, "main", "visibility")


def test_scope_sets():
    assert PHI_SCOPES == {DataScope.PHI_READ, DataScope.PHI_WRITE}
    assert DataScope.NETWORK_EGRESS not in WRITE_SCOPES
    assert DataScope.PHI_READ not in WRITE_SCOPES


def test_queue_map_lists_each_resource_once():
    resources = [resource for resource, _ in RESOURCE_QUEUES]

    assert len(resources) == len(set(resources))
    assert all(queue.startswith("compute.") for _, queue in RESOURCE_QUEUES)
