"""The graph model and apply_ops (merged plan, Phase 8.1)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from agent_system.planning.graph_model import (
    AddNode,
    DiffError,
    Expectation,
    GraphDiff,
    GraphOp,
    GraphProposal,
    InputSource,
    MoveNode,
    ReleaseHold,
    RemoveNode,
    RerunNode,
    SeedFrom,
    SetExpectations,
    SetInputSources,
    UpdateParams,
    apply_ops,
)
from tests.support.graph_fixtures import live, node, state

pytestmark = pytest.mark.unit


def three():
    a, b, c = node("build_cohort"), node("segmentation_agent"), node("extract_features")
    return a, b, c, state(live(a, "completed"), live(b, "completed"), live(c))


def ids(content):
    return [n.node_id for n in content.nodes]


def test_add_node_appends_or_inserts_with_the_proposed_id():
    a, b, c, graph = three()
    new_id = uuid.uuid4()
    content = apply_ops(graph, GraphDiff(base_version=3, ops=[
        AddNode(node_id=new_id, kind="tool", ref="run_modeling", params={"target": "y"}, at=1),
    ]), versions={"run_modeling": "4"})
    assert ids(content) == [a.node_id, new_id, b.node_id, c.node_id]
    added = content.node(new_id)
    assert (added.ref_version, added.title, added.params) == ("4", "run_modeling", {"target": "y"})
    appended = apply_ops(graph, GraphDiff(base_version=3, ops=[AddNode(kind="tool", ref="run_modeling")]))
    assert ids(appended)[:3] == [a.node_id, b.node_id, c.node_id] and len(appended.nodes) == 4


def test_remove_move_and_edit_ops():
    a, b, c, graph = three()
    content = apply_ops(graph, GraphDiff(base_version=3, ops=[
        MoveNode(node_id=c.node_id, to=0),
        RemoveNode(node_id=b.node_id),
        UpdateParams(node_id=a.node_id, params={"criteria": "MR"}, title="MR cohort"),
        SetInputSources(node_id=c.node_id, ports={"cohort": InputSource(node_id=a.node_id, port="cohort")}),
        SetExpectations(node_id=a.node_id, rules=[Expectation(metric="size", op=">=", value=30)]),
    ]))
    assert ids(content) == [c.node_id, a.node_id]
    edited = content.node(a.node_id)
    assert (edited.params, edited.title, edited.expect.rules[0].metric) == ({"criteria": "MR"}, "MR cohort", "size")
    assert content.node(c.node_id).input_sources.ports["cohort"].node_id == a.node_id
    cleared = apply_ops(graph, GraphDiff(base_version=3, ops=[SetInputSources(node_id=c.node_id, ports={})]))
    assert cleared.node(c.node_id).input_sources is None


def test_rerun_with_seed_release_and_on_running_are_carried():
    a, b, c, graph = three()
    seed = SeedFrom(run_id=uuid.uuid4(), checkpoint_id="cp")
    content = apply_ops(graph, GraphDiff(base_version=3, on_running="restart", origin="composer", ops=[
        RerunNode(node_id=b.node_id, seed_from=seed), ReleaseHold(node_id=a.node_id),
    ]))
    assert content.rerun_node_ids == [b.node_id] and content.release_hold_node_ids == [a.node_id]
    assert content.node(b.node_id).seed_from == seed and content.node(a.node_id).seed_from is None
    assert (content.on_running, content.origin) == ("restart", "composer")


def test_a_removed_node_drops_its_rerun_and_release():
    a, b, c, graph = three()
    content = apply_ops(graph, GraphDiff(base_version=3, ops=[
        RerunNode(node_id=b.node_id), ReleaseHold(node_id=b.node_id), RemoveNode(node_id=b.node_id),
    ]))
    assert content.rerun_node_ids == [] and content.release_hold_node_ids == []


def test_ops_on_unknown_nodes_or_past_the_end_fail():
    a, b, c, graph = three()
    with pytest.raises(DiffError) as caught:
        apply_ops(graph, GraphDiff(base_version=3, ops=[
            RemoveNode(node_id=uuid.uuid4()),
            MoveNode(node_id=a.node_id, to=9),
            AddNode(kind="tool", ref="run_modeling", at=7),
            AddNode(node_id=b.node_id, kind="tool", ref="run_modeling"),
        ]))
    assert [e.code for e in caught.value.errors] == ["UNKNOWN_NODE", "BAD_POSITION", "BAD_POSITION",
                                                     "DUPLICATE_NODE"]


def test_the_document_is_the_stored_content():
    a, b, c, graph = three()
    content = apply_ops(graph, GraphDiff(base_version=3, ops=[RerunNode(node_id=a.node_id)]))
    document = content.document()
    assert document["v"] == 1 and document["rerun_node_ids"] == [str(a.node_id)]
    assert "seed_from" not in document["nodes"][0] and "origin" not in document


def test_ops_parse_by_their_op_name():
    op = TypeAdapter(GraphOp).validate_python({"op": "rerun_node", "node_id": str(uuid.uuid4())})
    assert isinstance(op, RerunNode)
    with pytest.raises(PydanticValidationError):
        TypeAdapter(GraphOp).validate_python({"op": "rebind", "node_id": str(uuid.uuid4())})
    with pytest.raises(PydanticValidationError):
        GraphDiff(base_version=0, ops=[])


def test_a_proposal_round_trips_as_json():
    proposal = GraphProposal(diff=GraphDiff(base_version=0, ops=[AddNode(kind="tool", ref="build_cohort")]),
                             rationale="start with a cohort")
    assert GraphProposal.model_validate_json(proposal.model_dump_json()) == proposal
