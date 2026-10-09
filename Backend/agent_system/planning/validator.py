"""The Validator (merged plan, Phase 8.2; system §9).

``validate(state, content, catalog)`` checks the **whole** target sequence of
a change and returns only technical errors. It never suggests a strategy:
what to run, in what order, is the Main's. The Application API re-checks
the structural rules in T1/T2 (stable ids, one sequence, earlier sources) and
returns ``STALE_BASE``; the catalog-backed rules live only here.

| Code | When |
|---|---|
| ``UNKNOWN_REF`` | ``ref`` is not a tool / agent of the node's ``kind`` in the catalog |
| ``NOT_GRAPH_VISIBLE`` | the tool lacks ``graph`` visibility, or the agent is not ``execution`` |
| ``INVALID_PARAMS`` | the params fail the tool's input model (or the agent's ``task``) |
| ``MISSING_INPUT`` / ``AMBIGUOUS_INPUT`` / ``BAD_INPUT_SOURCE`` / ``TYPE_MISMATCH`` / ``UNKNOWN_PORT`` | D23, see ``inputs.py`` |
| ``DUPLICATE_NODE`` | a new ``node_id`` is already used, or appears twice |
| ``TARGET_IMMUTABLE`` | a live node changes its kind or ref (that is a new node) |
| ``BAD_EXPECTATION`` | the metric is not in the output type's summary model |
| ``UNKNOWN_RESOURCE`` | a declared pool is not in ``config/resources.yaml`` |
| ``RUNNING_NODE_AFFECTED`` | the change affects a running node and ``on_running="reject"`` |
| ``BAD_SEED`` | ``seed_from`` on a tool node, a non-``resumable`` agent, a node not rerun, or a run of another node |
| ``UNKNOWN_NODE`` / ``BAD_RELEASE`` | a rerun or release names a node outside the sequence / without a hold |

There is no ``CYCLE_DETECTED``, ``DANGLING_REF`` or ``REMOVE_WITH_DEPENDENTS``:
a removal just runs input resolution again, so a dependant that loses its only
candidate gets ``MISSING_INPUT``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from ..execution.affected import affected
from ..execution.resources import pool_names
from ..toolkit.agents.spec import AgentSpec
from ..toolkit.catalog import Catalog
from ..toolkit.manifest import Visibility
from ..toolkit.tools.spec import ToolSpec
from .graph_model import ChangeContent, GraphState, NodeDef, ValidationError
from .inputs import NodeSpec, node_spec, resolve_inputs


class AgentNodeParams(BaseModel):
    """An agent node's params: its task, rendered into the first message."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1)


def validate(
    state: GraphState,
    content: ChangeContent,
    catalog: Catalog,
    *,
    resource_pools: Optional[Collection[str]] = None,
) -> list[ValidationError]:
    """Every technical error of ``content`` applied to ``state``, in sequence order.
    ``resource_pools`` defaults to the pools of ``config/resources.yaml``."""
    pools = frozenset(pool_names() if resource_pools is None else resource_pools)
    errors: list[ValidationError] = []
    seen: set = set()
    for node in content.nodes:
        if node.node_id in seen:
            errors.append(_error("DUPLICATE_NODE", node, "the node_id appears twice in the sequence"))
            continue
        seen.add(node.node_id)
        errors.extend(_node_errors(state, content, node, catalog, pools))
    errors.extend(_change_errors(state, content))
    return errors


def _error(code: str, node: Optional[NodeDef], detail: str) -> ValidationError:
    return ValidationError(code=code, node=node.node_id if node else None, detail=detail)


def _node_errors(
    state: GraphState, content: ChangeContent, node: NodeDef, catalog: Catalog, pools: frozenset[str]
) -> Iterator[ValidationError]:
    live = state.node(node.node_id)
    if live is None and node.node_id in state.used_node_ids:
        yield _error("DUPLICATE_NODE", node, "the node_id belongs to a removed node; a new node needs a new id")
    if live is not None and (live.definition.kind, live.definition.ref) != (node.kind, node.ref):
        yield _error("TARGET_IMMUTABLE", node, f"the node runs {live.definition.kind} "
                     f"{live.definition.ref!r}; a different target is a new node")

    spec = node_spec(catalog, node)
    if spec is None:
        yield _error("UNKNOWN_REF", node, f"no {node.kind} named {node.ref!r} in the catalog")
        return
    if isinstance(spec, ToolSpec) and not spec.visible_to(Visibility.GRAPH):
        yield _error("NOT_GRAPH_VISIBLE", node, f"tool {node.ref!r} cannot be a node (no graph visibility)")
    if isinstance(spec, AgentSpec) and not spec.is_graph_node:
        yield _error("NOT_GRAPH_VISIBLE", node, f"agent {node.ref!r} is a {spec.kind} agent, not an execution agent")

    yield from (_error("INVALID_PARAMS", node, detail) for detail in _param_problems(spec, node.params))
    yield from resolve_inputs(content.nodes, node, catalog).errors
    yield from _expectation_errors(spec, node, catalog)
    for resource in sorted(str(r) for r in spec.resources if str(r) not in pools):
        yield _error("UNKNOWN_RESOURCE", node, f"{node.ref!r} declares pool {resource!r}, which "
                     "config/resources.yaml does not define")
    if node.seed_from is not None:
        yield from _seed_errors(state, content, spec, node)


def _param_problems(spec: NodeSpec, params: dict[str, Any]) -> list[str]:
    if isinstance(spec, AgentSpec):
        try:
            AgentNodeParams.model_validate(params)
        except PydanticValidationError as exc:
            return [_describe(error) for error in exc.errors()]
        return []

    # A tool's input ports fill their params from upstream outputs (the Node Runner).
    filled = {port.param for port in spec.inputs.values() if port.param}
    problems = [f"{name!r} is filled from an input port, not set in params"
                for name in sorted(filled & set(params))]
    problems += [f"{name!r} is not a parameter of {spec.name!r}"
                 for name in sorted(set(params) - spec.arg_names - filled)]
    if spec.input_model is not None:
        try:
            spec.input_model.model_validate(params)
        except PydanticValidationError as exc:
            # Port-filled arguments and unknown names are reported above.
            problems += [
                _describe(error) for error in exc.errors()
                if error["type"] != "extra_forbidden"
                and not (error["loc"] and error["loc"][0] in filled)
            ]
    else:
        required = set((spec.input_schema or {}).get("required", ()))
        problems += [f"{name!r} is required" for name in sorted(required - filled - set(params))]
    return problems


def _describe(error: dict[str, Any]) -> str:
    where = ".".join(str(part) for part in error["loc"]) or "params"
    return f"{where}: {error['msg']}"


def _expectation_errors(spec: NodeSpec, node: NodeDef, catalog: Catalog) -> Iterator[ValidationError]:
    for rule in node.expect.rules if node.expect else ():
        if rule.port is not None and rule.port not in spec.outputs:
            yield _error("UNKNOWN_PORT", node, f"expectation on {rule.metric!r} names output port "
                         f"{rule.port!r}, which {node.ref!r} does not have")
            continue
        ports = [spec.outputs[rule.port]] if rule.port else list(spec.outputs.values())
        metrics = set()
        for port in ports:
            if port.artifact_type in catalog.artifact_types:
                metrics |= set(catalog.artifact_types.get(port.artifact_type).summary_model.model_fields)
        if rule.metric not in metrics:
            where = f"port {rule.port!r}" if rule.port else "any output"
            yield _error("BAD_EXPECTATION", node, f"metric {rule.metric!r} is not in the summary of "
                         f"{where} of {node.ref!r} (known: {', '.join(sorted(metrics)) or 'none'})")


def _seed_errors(
    state: GraphState, content: ChangeContent, spec: NodeSpec, node: NodeDef
) -> Iterator[ValidationError]:
    seed = node.seed_from
    if not isinstance(spec, AgentSpec):
        yield _error("BAD_SEED", node, "seed_from is for agent nodes; a tool node has no checkpoints")
    elif not spec.resumable:
        yield _error("BAD_SEED", node, f"agent {node.ref!r} is not resumable")
    elif node.node_id not in content.rerun_node_ids:
        yield _error("BAD_SEED", node, "seed_from applies only to a node this change reruns")
    else:
        live = state.node(node.node_id)
        if live is None or seed.run_id not in live.run_ids:
            yield _error("BAD_SEED", node, f"run {seed.run_id} is not a run of this node")


def _change_errors(state: GraphState, content: ChangeContent) -> Iterator[ValidationError]:
    in_sequence = {node.node_id for node in content.nodes}
    for name in ("rerun_node_ids", "release_hold_node_ids"):
        for node_id in getattr(content, name):
            if node_id not in in_sequence:
                yield ValidationError(code="UNKNOWN_NODE", node=node_id,
                                      detail=f"{name} names a node outside the sequence")
    for node_id in content.release_hold_node_ids:
        live = state.node(node_id)
        if node_id in in_sequence and not (live and live.run and live.run.holds_downstream):
            yield ValidationError(code="BAD_RELEASE", node=node_id,
                                  detail="the node's current run holds nothing downstream")
    if content.on_running == "reject":
        for node_id in affected(state.sequence, content):
            live = state.node(node_id)
            if live is not None and live.status == "running":
                yield ValidationError(code="RUNNING_NODE_AFFECTED", node=node_id,
                                      detail="the node is running and the change affects it; "
                                      "resend with on_running='restart' to supersede it")
