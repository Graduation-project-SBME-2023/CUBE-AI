"""Manifest consistency rules (merged plan, Phase 1.1 step 2).

``ToolSpec.__post_init__`` checks each field on its own. The rules here relate
fields to each other, and ``catalog.validate()`` (Phase 1.3) runs them over
every registered tool and raises with all the violations at once:

- ``side_effects="none"`` <=> ``read_only=True`` and no write scope;
- ``cost="heavy"`` => ``execution="job"``;
- ``execution="job"`` => ``resources`` is not empty, and the args are JSON only;
- ``"main"`` in ``visibility`` => ``execution="inline"`` and ``cost`` is not heavy;
- ``"graph"`` in ``visibility`` => at least one output port, every output port
  has an ``extract`` (the Node Runner cannot record the artifact without one),
  and every input port's ``param`` is an argument of the tool.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAliasType, get_args, get_origin

from pydantic import BaseModel

from ..manifest import Cost, Execution, SideEffects, Visibility

if TYPE_CHECKING:
    from .spec import ToolSpec


class ManifestRule(StrEnum):
    SIDE_EFFECTS_READ_ONLY = "side_effects_read_only"
    HEAVY_NEEDS_JOB = "heavy_needs_job"
    JOB_NEEDS_RESOURCES = "job_needs_resources"
    JOB_ARGS_JSON = "job_args_json"
    MAIN_INLINE_NOT_HEAVY = "main_inline_not_heavy"
    GRAPH_OUTPUT_PORT = "graph_output_port"
    GRAPH_OUTPUT_EXTRACT = "graph_output_extract"
    GRAPH_INPUT_PARAM = "graph_input_param"


@dataclass(frozen=True)
class ManifestViolation:
    tool: str
    rule: ManifestRule
    message: str

    def __str__(self) -> str:
        return f"{self.tool}: {self.message} [{self.rule}]"


def check_manifest(spec: ToolSpec) -> list[ManifestViolation]:
    """Every consistency rule the spec breaks; empty when the manifest is consistent."""
    violations: list[ManifestViolation] = []

    def fail(rule: ManifestRule, message: str) -> None:
        violations.append(ManifestViolation(spec.name, rule, message))

    # side_effects="none" <=> read_only and no write scope
    write_scopes = sorted(scope.value for scope in spec.write_scopes)
    if spec.side_effects is SideEffects.NONE:
        if not spec.read_only:
            fail(ManifestRule.SIDE_EFFECTS_READ_ONLY, 'side_effects="none" requires read_only=True')
        if write_scopes:
            fail(
                ManifestRule.SIDE_EFFECTS_READ_ONLY,
                f'side_effects="none" contradicts write scopes {write_scopes}',
            )
    elif spec.read_only and not write_scopes:
        fail(
            ManifestRule.SIDE_EFFECTS_READ_ONLY,
            f'read_only=True with no write scope requires side_effects="none", '
            f'not "{spec.side_effects}"',
        )

    if spec.cost is Cost.HEAVY and spec.execution is not Execution.JOB:
        fail(
            ManifestRule.HEAVY_NEEDS_JOB,
            f'cost="heavy" requires execution="job", not "{spec.execution}"',
        )

    if spec.execution is Execution.JOB:
        if not spec.resources:
            fail(ManifestRule.JOB_NEEDS_RESOURCES, 'execution="job" requires at least one resource')
        # input_schema is JSON schema already; only a pydantic model can hold non-JSON types.
        if spec.input_model is not None:
            for problem in non_json_args(spec.input_model):
                fail(ManifestRule.JOB_ARGS_JSON, f"job args must be JSON only: {problem}")

    if Visibility.MAIN in spec.visibility:
        if spec.execution is not Execution.INLINE:
            fail(
                ManifestRule.MAIN_INLINE_NOT_HEAVY,
                f'"main" visibility requires execution="inline", not "{spec.execution}"',
            )
        if spec.cost is Cost.HEAVY:
            fail(ManifestRule.MAIN_INLINE_NOT_HEAVY, '"main" visibility forbids cost="heavy"')

    if Visibility.GRAPH in spec.visibility:
        if not spec.outputs:
            fail(ManifestRule.GRAPH_OUTPUT_PORT, '"graph" visibility requires at least one output port')
        for port_name, port in spec.outputs.items():
            if port.extract is None:
                fail(
                    ManifestRule.GRAPH_OUTPUT_EXTRACT,
                    f"output port {port_name!r} needs an extract function",
                )
        arg_names = _arg_names(spec)
        for port_name, port in spec.inputs.items():
            if port.param is None:
                fail(
                    ManifestRule.GRAPH_INPUT_PARAM,
                    f"input port {port_name!r} must name the argument that receives it (param)",
                )
            elif port.param not in arg_names:
                fail(
                    ManifestRule.GRAPH_INPUT_PARAM,
                    f"input port {port_name!r} names param {port.param!r}, "
                    f"which is not an argument of the tool",
                )

    return violations


def _arg_names(spec: ToolSpec) -> set[str]:
    if spec.input_model is not None:
        return set(spec.input_model.model_fields)
    properties = (spec.input_schema or {}).get("properties", {})
    return set(properties) if isinstance(properties, Mapping) else set()


# --- JSON-only args ---------------------------------------------------------
#
# A job's args are serialised into the JobRequest and hashed into its job id
# (Plan 5), so they must be exactly what json.loads produces. Types pydantic
# merely *encodes* as JSON (datetime, UUID, Path, bytes) are refused, and so are
# sets: their order is not stable, which would make the job id unstable.

_JSON_SCALARS: frozenset[object] = frozenset({str, int, float, bool, types.NoneType})
_UNION_ORIGINS: frozenset[object] = frozenset({typing.Union, types.UnionType})


def non_json_args(model: type[BaseModel]) -> list[str]:
    """Describe every argument of ``model`` that is not a plain JSON type."""
    problems: list[str] = []
    _check_model(model, "", frozenset(), problems)
    return problems


def _check_model(
    model: type[BaseModel], prefix: str, seen: frozenset[type], problems: list[str]
) -> None:
    if model in seen:  # self-referential model: this branch is already being checked
        return
    seen = seen | {model}
    for name, info in model.model_fields.items():
        _check_type(info.annotation, f"{prefix}{name}", seen, problems)


def _check_type(tp: Any, path: str, seen: frozenset[type], problems: list[str]) -> None:
    if tp is Any or tp in _JSON_SCALARS:
        return
    if isinstance(tp, TypeAliasType):  # `type X = ...`
        _check_type(tp.__value__, path, seen, problems)
        return

    origin, args = get_origin(tp), get_args(tp)
    if origin is Annotated:
        _check_type(args[0], path, seen, problems)
    elif origin in _UNION_ORIGINS:
        for arg in args:
            _check_type(arg, path, seen, problems)
    elif origin is Literal:
        if not all(isinstance(arg, (str, int, bool, types.NoneType)) for arg in args):
            problems.append(f"{path}: Literal with non-JSON values {args!r}")
    elif tp in (list, tuple) or origin in (list, tuple):
        # Both arrive as a JSON array; a bare list/tuple holds Any.
        for arg in args:
            if arg is not Ellipsis:
                _check_type(arg, f"{path}[]", seen, problems)
    elif tp is dict or origin is dict:
        if not args:
            problems.append(f"{path}: bare dict has untyped keys; use dict[str, ...]")
            return
        key, value = args
        if not _is_str_key(key):
            problems.append(f"{path}: dict keys must be str, not {_type_name(key)}")
        _check_type(value, f"{path}{{}}", seen, problems)
    elif isinstance(tp, type) and issubclass(tp, Enum):
        if not all(_is_json_scalar(member.value) for member in tp):
            problems.append(f"{path}: enum {tp.__name__} has non-JSON values")
    elif isinstance(tp, type) and issubclass(tp, BaseModel):
        _check_model(tp, f"{path}.", seen, problems)
    else:
        problems.append(f"{path}: {_type_name(tp)} is not a JSON type")


def _is_str_key(tp: Any) -> bool:
    if tp is str:
        return True
    if isinstance(tp, type) and issubclass(tp, StrEnum):
        return True
    if get_origin(tp) is Literal:
        return all(isinstance(arg, str) for arg in get_args(tp))
    return False


def _is_json_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool, types.NoneType))


def _type_name(tp: Any) -> str:
    return tp.__name__ if isinstance(tp, type) else repr(tp)
