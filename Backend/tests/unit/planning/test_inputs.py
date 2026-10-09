"""Strict input resolution (merged plan, Phase 8.3 step 1; D23)."""

from __future__ import annotations

import pytest

from agent_system.planning.graph_model import InputSource, InputSources
from agent_system.planning.inputs import candidates, resolve_inputs
from tests.support.graph_fixtures import node, research_catalog

pytestmark = pytest.mark.unit

CATALOG = research_catalog()


def test_non_adjacent_inputs_bind_to_their_only_candidates():
    """Feature extraction reads the cohort from node 1 and the masks from node 2."""
    cohort, seg, feats = node("build_cohort"), node("segmentation_agent"), node("extract_features")
    resolution = resolve_inputs([cohort, seg, feats], feats, CATALOG)
    assert resolution.ok
    assert resolution.bound == {
        "cohort": InputSource(node_id=cohort.node_id, port="cohort"),
        "masks": InputSource(node_id=seg.node_id, port="masks"),
    }


def test_only_earlier_nodes_are_candidates():
    seg, cohort = node("segmentation_agent"), node("build_cohort")
    sequence = [seg, cohort]
    port = CATALOG.resolve("segmentation_agent").inputs["cohort"]
    assert candidates(sequence, 0, port, CATALOG) == []
    assert [e.code for e in resolve_inputs(sequence, seg.node_id, CATALOG).errors] == ["MISSING_INPUT"]


def test_a_named_source_wins_over_several_candidates():
    cohort, seg, split = node("build_cohort"), node("segmentation_agent"), node("split_masks")
    feats = node("extract_features", input_sources=InputSources(
        ports={"masks": InputSource(node_id=split.node_id, port="right")}))
    resolution = resolve_inputs([cohort, seg, split, feats], feats, CATALOG)
    assert resolution.ok and resolution.bound["masks"] == InputSource(node_id=split.node_id, port="right")


def test_the_rule_never_picks_the_nearest():
    cohort, seg, split = node("build_cohort"), node("segmentation_agent"), node("split_masks")
    feats = node("extract_features")
    resolution = resolve_inputs([cohort, seg, split, feats], feats, CATALOG)
    assert "masks" not in resolution.bound
    assert [e.code for e in resolution.errors] == ["AMBIGUOUS_INPUT"]


def test_an_unknown_target_resolves_nothing():
    stray = node("no_such_tool")
    assert resolve_inputs([stray], stray, CATALOG).bound == {}
