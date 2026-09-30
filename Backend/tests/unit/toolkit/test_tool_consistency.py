"""Manifest consistency rules (merged plan, Phase 1.1 step 2)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import pytest
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model

from agent_system.toolkit.tools.consistency import (
    ManifestRule,
    ManifestViolation,
    check_manifest,
    non_json_args,
)
from agent_system.toolkit.tools.spec import ToolSpec

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Port:
    """Stand-in for PortSpec (Phase 1.1 step 3): the rules only read ``param``."""

    artifact_type: str
    param: str | None = None


# Same shape as the real WorkspacePath in the tool packs.
WorkspacePath = Annotated[Optional[str], BeforeValidator(lambda value: value)]


class _Method(StrEnum):
    RF = "rf"
    LASSO = "lasso"


class _TrainParams(BaseModel):
    input_path: WorkspacePath = None
    fold: int = Field(default=5, ge=2)
    method: _Method = _Method.RF
    task: Literal["classification", "regression"] = "classification"
    labels: list[int | str] = []
    options: dict[str, Any] = {}
    spacing: tuple[float, ...] = ()
    holdout: Optional[float] = None


class _JobArgs(BaseModel):
    cohort_id: str
    params: _TrainParams


class _ReadArgs(BaseModel):
    patient_id: str


async def _handler(**_: Any) -> dict[str, Any]:
    return {"status": "ok"}


def _read_tool(**overrides: Any) -> ToolSpec:
    """A consistent main-visible read tool."""
    fields: dict[str, Any] = {
        "name": "get_patient",
        "description": "Read one patient.",
        "pack": "db_reads",
        "handler": _handler,
        "input_model": _ReadArgs,
        "side_effects": "none",
        "cost": "light",
        "execution": "inline",
        "visibility": {"main", "agent"},
        "read_only": True,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def _job_tool(**overrides: Any) -> ToolSpec:
    """A consistent graph-visible heavy job tool."""
    fields: dict[str, Any] = {
        "name": "radiomics_extract_features",
        "description": "Extract radiomics features.",
        "pack": "radiomics",
        "handler": _handler,
        "input_model": _JobArgs,
        "side_effects": "compute",
        "cost": "heavy",
        "execution": "job",
        "resources": {"cpu_heavy"},
        "isolation": "process",
        "data_scopes": {"phi_read", "fs_write", "db_write"},
        "visibility": {"graph", "agent"},
        "inputs": {"cohort": _Port("Cohort", param="cohort_id")},
        "outputs": {"features": _Port("FeatureTable")},
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def _rules(spec: ToolSpec) -> list[ManifestRule]:
    return [violation.rule for violation in check_manifest(spec)]


def test_consistent_tools_have_no_violations():
    assert check_manifest(_read_tool()) == []
    assert check_manifest(_job_tool()) == []


# --- side_effects="none" <=> read_only and no write scope


def test_side_effects_none_requires_read_only():
    assert _rules(_read_tool(read_only=False)) == [ManifestRule.SIDE_EFFECTS_READ_ONLY]


def test_side_effects_none_refuses_write_scopes():
    (violation,) = check_manifest(_read_tool(data_scopes={"db_write", "phi_read"}))

    assert violation.rule is ManifestRule.SIDE_EFFECTS_READ_ONLY
    assert "['db_write']" in violation.message


def test_read_only_without_write_scope_requires_side_effects_none():
    spec = _read_tool(side_effects="writes", visibility={"agent"})

    assert _rules(spec) == [ManifestRule.SIDE_EFFECTS_READ_ONLY]


@pytest.mark.parametrize("scope", ["phi_read", "network_egress"])
def test_read_scopes_do_not_count_as_writes(scope):
    assert check_manifest(_read_tool(data_scopes={scope})) == []


def test_a_writing_tool_needs_no_read_only_flag():
    spec = _read_tool(side_effects="writes", read_only=False, data_scopes={"db_write"})

    assert check_manifest(spec) == []


# --- cost="heavy" => execution="job"


def test_heavy_tool_must_be_a_job():
    spec = _job_tool(execution="inline", isolation="inline")

    assert _rules(spec) == [ManifestRule.HEAVY_NEEDS_JOB]


@pytest.mark.parametrize("cost", ["light", "medium"])
def test_lighter_tools_may_be_jobs(cost):
    assert check_manifest(_job_tool(cost=cost)) == []


# --- execution="job" => resources, JSON-only args


def test_job_needs_a_resource():
    assert _rules(_job_tool(resources=set())) == [ManifestRule.JOB_NEEDS_RESOURCES]


def test_job_args_must_be_json():
    class Args(BaseModel):
        cohort_id: str
        since: dt.datetime

    (violation,) = check_manifest(_job_tool(input_model=Args))

    assert violation.rule is ManifestRule.JOB_ARGS_JSON
    assert "since: datetime is not a JSON type" in violation.message


def test_job_args_from_a_raw_schema_are_not_type_checked():
    spec = _job_tool(
        input_model=None,
        input_schema={"type": "object", "properties": {"cohort_id": {"type": "string"}}},
    )

    assert check_manifest(spec) == []


def test_inline_tools_may_take_non_json_args():
    class Args(BaseModel):
        since: dt.datetime

    assert check_manifest(_read_tool(input_model=Args)) == []


# --- "main" visibility => inline and not heavy


def test_main_tool_must_be_inline():
    spec = _job_tool(cost="light", visibility={"main", "graph"})

    assert _rules(spec) == [ManifestRule.MAIN_INLINE_NOT_HEAVY]


def test_main_tool_must_not_be_heavy():
    spec = _read_tool(cost="heavy")

    assert sorted(_rules(spec)) == sorted(
        [ManifestRule.HEAVY_NEEDS_JOB, ManifestRule.MAIN_INLINE_NOT_HEAVY]
    )


def test_main_tool_may_be_medium():
    assert check_manifest(_read_tool(cost="medium")) == []


# --- "graph" visibility => output port, input params exist


def test_graph_tool_needs_an_output_port():
    assert _rules(_job_tool(outputs={})) == [ManifestRule.GRAPH_OUTPUT_PORT]


def test_graph_input_port_must_name_its_param():
    (violation,) = check_manifest(_job_tool(inputs={"cohort": _Port("Cohort")}))

    assert violation.rule is ManifestRule.GRAPH_INPUT_PARAM
    assert "'cohort' must name the argument" in violation.message


def test_graph_input_param_must_be_an_argument():
    (violation,) = check_manifest(_job_tool(inputs={"cohort": _Port("Cohort", param="cohort")}))

    assert violation.rule is ManifestRule.GRAPH_INPUT_PARAM
    assert "param 'cohort', which is not an argument" in violation.message


def test_graph_input_param_is_looked_up_in_a_raw_schema():
    schema = {"type": "object", "properties": {"cohort_id": {"type": "string"}}}
    spec = _job_tool(input_model=None, input_schema=schema)

    assert check_manifest(spec) == []
    missing = _job_tool(input_model=None, input_schema={"type": "object"})
    assert _rules(missing) == [ManifestRule.GRAPH_INPUT_PARAM]


def test_ports_are_not_checked_without_graph_visibility():
    spec = _job_tool(visibility={"agent"}, outputs={}, inputs={"cohort": _Port("Cohort")})

    assert check_manifest(spec) == []


# --- reporting


def test_all_violations_are_reported_together():
    spec = _job_tool(
        read_only=False,
        side_effects="none",
        resources=set(),
        outputs={},
        visibility={"main", "graph"},
    )

    assert sorted(_rules(spec)) == sorted(
        [
            ManifestRule.SIDE_EFFECTS_READ_ONLY,  # write scopes
            ManifestRule.SIDE_EFFECTS_READ_ONLY,  # not read_only
            ManifestRule.JOB_NEEDS_RESOURCES,
            ManifestRule.MAIN_INLINE_NOT_HEAVY,  # not inline
            ManifestRule.MAIN_INLINE_NOT_HEAVY,  # heavy
            ManifestRule.GRAPH_OUTPUT_PORT,
        ]
    )


def test_violation_str_names_tool_and_rule():
    violation = ManifestViolation("get_patient", ManifestRule.HEAVY_NEEDS_JOB, "boom")

    assert str(violation) == "get_patient: boom [heavy_needs_job]"


# --- non_json_args


type _Ids = list[str]
type _When = dt.datetime


class _Blob(Enum):
    ONE = object()


class _Tree(BaseModel):
    name: str
    children: list[_Tree] = []


_Tree.model_rebuild()


def test_json_native_args_pass():
    assert non_json_args(_JobArgs) == []

    class Aliased(BaseModel):
        ids: _Ids

    assert non_json_args(Aliased) == []


def test_self_referential_model_terminates():
    assert non_json_args(_Tree) == []


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (dt.datetime, "value: datetime is not a JSON type"),
        (uuid.UUID, "value: UUID is not a JSON type"),
        (Path, "value: Path is not a JSON type"),
        (bytes, "value: bytes is not a JSON type"),
        (set[str], "value: set[str] is not a JSON type"),
        (frozenset[int], "value: frozenset[int] is not a JSON type"),
        (list[dt.date], "value[]: date is not a JSON type"),
        (Optional[dt.date], "value: date is not a JSON type"),
        (dict[int, str], "value: dict keys must be str, not int"),
        (dict[str, bytes], "value{}: bytes is not a JSON type"),
        (dict, "value: bare dict has untyped keys"),
        (_When, "value: datetime is not a JSON type"),
        (_Blob, "value: enum _Blob has non-JSON values"),
        (Literal[1.5], "value: Literal with non-JSON values"),
    ],
)
def test_non_json_args_are_named(annotation, expected):
    args = create_model(
        "Args",
        __config__=ConfigDict(arbitrary_types_allowed=True),
        value=(annotation, ...),
    )

    (problem,) = non_json_args(args)
    assert problem.startswith(expected)


def test_nested_problems_carry_their_path():
    class Inner(BaseModel):
        when: dt.datetime

    class Args(BaseModel):
        params: Inner

    assert non_json_args(Args) == ["params.when: datetime is not a JSON type"]
