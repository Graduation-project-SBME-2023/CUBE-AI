"""Strict input resolution (merged plan, Phase 8.3 step 1; Appendix P D23).

For each input port of a node, the **candidates** are the output ports, with
a compatible artifact type, of the live nodes earlier in the sequence:

- exactly one candidate -> it is bound;
- none -> ``MISSING_INPUT`` for a required port; an optional port stays empty;
- more than one -> the node's ``input_sources`` entry for that port, which must
  be one of the candidates, or ``AMBIGUOUS_INPUT`` listing them.

Nothing is picked by distance or by type closeness. The Validator and the
Node Runner (before T4) both call ``resolve_inputs``, so a pin always matches
what was validated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from ..toolkit.agents.spec import AgentSpec
from ..toolkit.catalog import Catalog, UnknownRefError
from ..toolkit.ports import PortSpec
from ..toolkit.tools.spec import ToolSpec
from .graph_model import InputSource, NodeDef, ValidationError

type NodeSpec = ToolSpec | AgentSpec


def node_spec(catalog: Catalog, node: NodeDef) -> Optional[NodeSpec]:
    """The catalog entry a node runs, or None when its ref or kind does not match one."""
    try:
        spec = catalog.resolve(node.ref)
    except UnknownRefError:
        return None
    wanted = ToolSpec if node.kind == "tool" else AgentSpec
    return spec if isinstance(spec, wanted) else None


def compatible(catalog: Catalog, produced: str, expected: str) -> bool:
    try:
        return catalog.artifact_types.is_compatible(produced, expected)
    except LookupError:  # an unregistered type; catalog.validate() reports it
        return produced == expected


@dataclass
class Resolution:
    """The bound source of each port, or the errors that keep it unbound."""

    bound: dict[str, InputSource] = field(default_factory=dict)
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _label(sequence: Sequence[NodeDef], source: InputSource) -> str:
    index = next((i for i, node in enumerate(sequence) if node.node_id == source.node_id), None)
    where = f"node {index + 1} ({sequence[index].ref})" if index is not None else str(source.node_id)
    return f"{where}.{source.port}"


def candidates(
    sequence: Sequence[NodeDef], index: int, port: PortSpec, catalog: Catalog
) -> list[InputSource]:
    """Every compatible output port of the nodes before ``index``, in sequence order."""
    found = []
    for upstream in sequence[:index]:
        spec = node_spec(catalog, upstream)
        for name, output in (spec.outputs.items() if spec else ()):
            if compatible(catalog, output.artifact_type, port.artifact_type):
                found.append(InputSource(node_id=upstream.node_id, port=name))
    return found


def resolve_inputs(
    sequence: Sequence[NodeDef], node: NodeDef | UUID, catalog: Catalog
) -> Resolution:
    """The D23 rule for one node of ``sequence`` (the whole target sequence)."""
    node_id = node if isinstance(node, UUID) else node.node_id
    index = next(i for i, item in enumerate(sequence) if item.node_id == node_id)
    target = sequence[index]
    resolution = Resolution()
    spec = node_spec(catalog, target)
    if spec is None:  # UNKNOWN_REF is the Validator's
        return resolution

    def fail(code: str, detail: str, found: Sequence[InputSource] = ()) -> None:
        resolution.errors.append(
            ValidationError(code=code, node=node_id, detail=detail, candidates=list(found))
        )

    named: Mapping[str, InputSource] = target.input_sources.ports if target.input_sources else {}
    for port_name in sorted(set(named) - set(spec.inputs)):
        fail("UNKNOWN_PORT", f"{target.ref} has no input port {port_name!r}")

    earlier = {item.node_id: item for item in sequence[:index]}
    for port_name, port in spec.inputs.items():
        found = candidates(sequence, index, port, catalog)
        source = named.get(port_name)
        what = f"input {port_name!r} ({port.artifact_type})"
        if source is not None:
            upstream = earlier.get(source.node_id)
            if upstream is None:
                fail("BAD_INPUT_SOURCE", f"{what} names {source.node_id}, which is not a live node "
                     "earlier in the sequence", found)
                continue
            upstream_spec = node_spec(catalog, upstream)
            output = upstream_spec.outputs.get(source.port) if upstream_spec else None
            if output is None:
                fail("UNKNOWN_PORT", f"{what} names {_label(sequence, source)}, which is not an "
                     f"output port of {upstream.ref}", found)
            elif not compatible(catalog, output.artifact_type, port.artifact_type):
                fail("TYPE_MISMATCH", f"{what}: expected {port.artifact_type}, received "
                     f"{output.artifact_type} from {_label(sequence, source)}", found)
            else:
                resolution.bound[port_name] = source
        elif len(found) == 1:
            resolution.bound[port_name] = found[0]
        elif not found:
            if port.required:
                fail("MISSING_INPUT", f"{what}: no earlier node produces a compatible output")
        else:
            labels = ", ".join(_label(sequence, item) for item in found)
            fail("AMBIGUOUS_INPUT", f"{what} has {len(found)} candidates ({labels}); name one in "
                 "input_sources", found)
    return resolution
