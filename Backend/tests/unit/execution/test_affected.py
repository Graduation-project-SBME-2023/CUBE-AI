"""The affected set (merged plan, Phase 8.3 step 2)."""

from __future__ import annotations

import pytest

from agent_system.execution.affected import affected
from agent_system.planning.graph_model import ChangeContent
from tests.support.graph_fixtures import node

pytestmark = pytest.mark.unit


def seq():
    return [node("build_cohort"), node("segmentation_agent"), node("extract_features"), node("run_modeling")]


def test_nothing_changed_affects_nothing():
    old = seq()
    assert affected(old, ChangeContent(nodes=old)) == []


def test_an_edit_affects_itself_and_the_suffix():
    old = seq()
    new = [old[0], old[1].model_copy(update={"params": {"task": "other"}}), *old[2:]]
    assert affected(old, ChangeContent(nodes=new)) == [n.node_id for n in old[1:]]


def test_a_rerun_affects_the_suffix():
    old = seq()
    assert affected(old, ChangeContent(nodes=old, rerun_node_ids=[old[2].node_id])) == [
        old[2].node_id, old[3].node_id,
    ]


def test_an_insert_affects_the_new_node_and_everything_after_it():
    """A -> New -> B: B is affected, A is not."""
    a, b = node("build_cohort"), node("segmentation_agent")
    new = node("literature_agent")
    assert affected([a, b], ChangeContent(nodes=[a, new, b])) == [new.node_id, b.node_id]


def test_an_append_affects_only_the_new_node():
    old = seq()
    extra = node("report_results")
    assert affected(old, ChangeContent(nodes=[*old, extra])) == [extra.node_id]


def test_a_removal_affects_the_suffix_and_the_removed_node():
    old = seq()
    new = [old[0], *old[2:]]
    assert affected(old, ChangeContent(nodes=new)) == [old[2].node_id, old[3].node_id, old[1].node_id]
    assert affected(old, ChangeContent(nodes=old[:3])) == [old[3].node_id]


def test_a_move_affects_from_the_first_moved_position():
    old = seq()
    new = [old[0], old[2], old[1], old[3]]
    assert affected(old, ChangeContent(nodes=new)) == [old[2].node_id, old[1].node_id, old[3].node_id]
