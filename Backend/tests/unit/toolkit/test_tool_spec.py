"""ToolSpec field checks and derived fields (merged plan, Phase 1.1 step 1).

The cross-field manifest rules belong to catalog.validate() and are tested there.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.toolkit.tools.manifest import (
    Cost,
    DataScope,
    Execution,
    IsolationLevel,
    Resource,
    SideEffects,
    TrustLevel,
    Visibility,
)
from agent_system.toolkit.tools.spec import ToolSpec

pytestmark = pytest.mark.unit


class _Args(BaseModel):
    patient_id: str


async def _handler(patient_id: str) -> dict[str, Any]:
    return {"status": "ok", "patient_id": patient_id}


def _spec(**overrides: Any) -> ToolSpec:
    fields: dict[str, Any] = {
        "name": "get_patient",
        "description": "Read one patient.",
        "pack": "db_reads",
        "handler": _handler,
        "input_model": _Args,
        "side_effects": "none",
        "cost": "light",
        "execution": "inline",
        "visibility": {"main", "agent"},
        "read_only": True,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def test_string_values_are_normalised_to_enums():
    spec = _spec(resources=["llm"], data_scopes={"phi_read"})

    assert spec.side_effects is SideEffects.NONE
    assert spec.cost is Cost.LIGHT
    assert spec.execution is Execution.INLINE
    assert spec.trust is TrustLevel.FIRST_PARTY
    assert spec.isolation is IsolationLevel.INLINE
    assert spec.visibility == frozenset({Visibility.MAIN, Visibility.AGENT})
    assert spec.resources == frozenset({Resource.LLM})
    assert spec.data_scopes == frozenset({DataScope.PHI_READ})


def test_spec_is_immutable():
    spec = _spec()

    with pytest.raises(AttributeError):
        spec.name = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        spec.inputs["cohort"] = object()  # type: ignore[index]


def test_copy_replace_builds_a_revalidated_copy():
    spec = _spec(data_scopes={"phi_read"})

    changed = copy.replace(spec, visibility={"agent"}, timeout_s=30)

    assert changed is not spec
    assert changed.visibility == frozenset({Visibility.AGENT})
    assert changed.timeout_s == 30
    assert changed.phi
    assert spec.visibility == frozenset({Visibility.MAIN, Visibility.AGENT})
    with pytest.raises(ValueError, match="at least one"):
        copy.replace(spec, visibility=set())


def test_ports_are_copied_not_aliased():
    ports: dict[str, Any] = {"cohort": object()}
    spec = _spec(inputs=ports)

    ports["masks"] = object()

    assert set(spec.inputs) == {"cohort"}


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        (set(), False),
        ({"db_write"}, False),
        ({"phi_read"}, True),
        ({"phi_write", "fs_write"}, True),
    ],
)
def test_phi_is_derived_from_data_scopes(scopes, expected):
    assert _spec(data_scopes=scopes).phi is expected


@pytest.mark.parametrize(
    ("resources", "queue"),
    [
        (set(), None),
        ({"llm"}, None),
        ({"orthanc_io"}, None),
        ({"cpu_heavy"}, "compute.cpu"),
        ({"highmem"}, "compute.highmem"),
        ({"gpu"}, "compute.gpu"),
        ({"cpu_heavy", "gpu"}, "compute.gpu"),
        ({"cpu_heavy", "highmem"}, "compute.highmem"),
    ],
)
def test_resource_class_is_derived_from_resources(resources, queue):
    assert _spec(resources=resources).resource_class == queue


def test_write_scopes_and_visible_to():
    spec = _spec(data_scopes={"db_write", "network_egress", "fs_write"})

    assert spec.write_scopes == frozenset({DataScope.DB_WRITE, DataScope.FS_WRITE})
    assert spec.visible_to("main")
    assert spec.visible_to(Visibility.AGENT)
    assert not spec.visible_to("graph")


def test_args_schema_from_model_or_raw_schema():
    assert _spec().args_schema()["properties"]["patient_id"]["type"] == "string"

    raw = {"type": "object", "properties": {"q": {"type": "string"}}}
    external = _spec(
        name="mcp__pubmed__search",
        input_model=None,
        input_schema=raw,
        execution="external",
        trust="third_party",
        isolation="container",
        visibility={"agent"},
    )
    assert external.args_schema() == raw


def test_spec_is_not_hashed_by_value():
    first, second = _spec(), _spec()

    assert first != second
    assert len({first, second}) == 2


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"name": ""}, ValueError, "must match"),
        ({"name": "has space"}, ValueError, "must match"),
        ({"name": "x" * 65}, ValueError, "must match"),
        ({"description": "  "}, ValueError, "description"),
        ({"pack": ""}, ValueError, "pack"),
        ({"version": 0}, ValueError, "version"),
        ({"version": True}, ValueError, "version"),
        ({"handler": "not callable"}, TypeError, "handler"),
        ({"input_model": None}, ValueError, "exactly one"),
        ({"input_schema": {"type": "object"}}, ValueError, "exactly one"),
        ({"input_model": dict}, TypeError, "input_model"),
        ({"output_model": object}, TypeError, "output_model"),
        ({"inputs": {"": object()}}, ValueError, "port names"),
        ({"execution": "remote"}, ValueError, "execution='remote'"),
        ({"cost": "free"}, ValueError, "cost="),
        ({"side_effects": "maybe"}, ValueError, "side_effects="),
        ({"trust": "friend"}, ValueError, "trust="),
        ({"isolation": "vm"}, ValueError, "isolation="),
        ({"resources": {"tpu"}}, ValueError, "resources="),
        ({"data_scopes": {"everything"}}, ValueError, "data_scopes="),
        ({"visibility": {"planner"}}, ValueError, "visibility="),
        ({"visibility": set()}, ValueError, "at least one"),
        ({"visibility": "main"}, TypeError, "not a string"),
        ({"ui": ""}, ValueError, "ui"),
        ({"on_result": 42}, TypeError, "on_result"),
        ({"timeout_s": 0}, ValueError, "timeout_s"),
        ({"queue_timeout_s": -1}, ValueError, "queue_timeout_s"),
        ({"priority": 10}, ValueError, "priority"),
        ({"priority": -1}, ValueError, "priority"),
        ({"max_attempts": 0}, ValueError, "max_attempts"),
    ],
)
def test_malformed_fields_are_refused(overrides, error, match):
    with pytest.raises(error, match=match):
        _spec(**overrides)


def test_manifest_fields_are_required():
    with pytest.raises(TypeError):
        ToolSpec(  # type: ignore[call-arg]
            name="get_patient",
            description="Read one patient.",
            pack="db_reads",
            handler=_handler,
            input_model=_Args,
        )
