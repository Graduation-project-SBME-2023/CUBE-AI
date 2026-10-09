"""The affected set of a graph change (merged plan, Phase 8.3 step 2).

Affected = the changed, moved, rerun or removed nodes, plus every live node
after the first of them in the new sequence. This is the sequential suffix,
a current-phase constraint (Appendix P §P.4); with a DAG it becomes "reachable
from the changed nodes", and only this function changes.

It mirrors step 5 of T2 (``plan_apply`` in the Application API) exactly; a
contract test runs both on the same cases. No fingerprints now (§P.10).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from ..planning.graph_model import ChangeContent, NodeDef


def touched(old: Sequence[NodeDef], new: ChangeContent) -> set[UUID]:
    """The nodes the change adds, edits or reruns (not counting moves)."""
    before = {node.node_id: node.definition() for node in old}
    edited = {node.node_id for node in new.nodes if before.get(node.node_id) != node.definition()}
    return edited | set(new.rerun_node_ids)


def affected(old: Sequence[NodeDef], new: ChangeContent) -> list[UUID]:
    """Affected node ids: the new-sequence suffix in order, then the removed nodes."""
    old_order = [node.node_id for node in old]
    new_order = [node.node_id for node in new.nodes]
    changed = touched(old, new)
    first = next(
        (
            i for i, node_id in enumerate(new_order)
            if node_id in changed or i >= len(old_order) or old_order[i] != node_id
        ),
        len(new_order),
    )
    kept = set(new_order)
    return new_order[first:] + [node_id for node_id in old_order if node_id not in kept]
