"""The Validator (merged plan, Phase 8.2): every code, over the whole sequence."""

from __future__ import annotations

import uuid

import pytest

from agent_system.execution.resources import pool_names
from agent_system.planning.graph_model import (
    ChangeContent,
    Expectation,
    Expectations,
    InputSource,
    InputSources,
    SeedFrom,
)
from agent_system.planning.validator import validate
from tests.support.graph_fixtures import POOLS, live, node, research_catalog, state

pytestmark = pytest.mark.unit

CATALOG = research_catalog()


def check(content: ChangeContent, graph=None, pools=POOLS):
    return validate(graph or state(), content, CATALOG, resource_pools=pools)


def codes(content: ChangeContent, graph=None, pools=POOLS) -> list[str]:
    return [error.code for error in check(content, graph, pools)]


def sources(**ports: tuple) -> InputSources:
    return InputSources(ports={port: InputSource(node_id=n.node_id, port=p) for port, (n, p) in ports.items()})


def pipeline():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    feats, model = node("extract_features"), node("run_modeling")
    return cohort, seg, feats, model


def test_a_valid_pipeline_has_no_errors():
    assert check(ChangeContent(nodes=[*pipeline(), node("report_results")])) == []


def test_unknown_ref_and_a_ref_of_the_other_kind():
    assert codes(ChangeContent(nodes=[node("no_such_tool")])) == ["UNKNOWN_REF"]
    assert codes(ChangeContent(nodes=[node("build_cohort", kind="agent", params={"task": "x"})])) == ["UNKNOWN_REF"]


def test_not_graph_visible_tool_and_control_agent():
    assert codes(ChangeContent(nodes=[node("count_studies")])) == ["NOT_GRAPH_VISIBLE"]
    assert codes(ChangeContent(nodes=[node("composer", kind="agent")])) == ["NOT_GRAPH_VISIBLE"]


@pytest.mark.parametrize(
    "params",
    [
        {},  # criteria is required
        {"criteria": "ct", "min_size": "many"},  # wrong type
        {"criteria": "ct", "colour": "red"},  # not a parameter
    ],
)
def test_invalid_tool_params(params):
    errors = check(ChangeContent(nodes=[node("build_cohort", params=params)]))
    assert [e.code for e in errors] == ["INVALID_PARAMS"]


def test_port_filled_arguments_are_not_params():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    feats = node("extract_features", params={"bin_width": 10})
    assert check(ChangeContent(nodes=[cohort, seg, feats])) == []
    bad = node("extract_features", params={"cohort_id": "x"})
    assert codes(ChangeContent(nodes=[cohort, seg, bad])) == ["INVALID_PARAMS"]


def test_agent_params_need_a_task():
    assert codes(ChangeContent(nodes=[node("population_agent", params={})])) == ["INVALID_PARAMS"]
    assert codes(ChangeContent(nodes=[node("population_agent", params={"task": "x", "n": 1})])) == [
        "INVALID_PARAMS"
    ]


def test_missing_input():
    errors = check(ChangeContent(nodes=[node("segmentation_agent")]))
    assert [(e.code, "cohort" in e.detail) for e in errors] == [("MISSING_INPUT", True)]


def test_ambiguous_input_with_two_compatible_producers_lists_them():
    cohort, seg, feats, model = pipeline()
    refit = node("refit_model")
    report = node("report_results")
    errors = check(ChangeContent(nodes=[cohort, seg, feats, model, refit, report]))
    assert [e.code for e in errors] == ["AMBIGUOUS_INPUT"]
    assert errors[0].node == report.node_id
    assert {(c.node_id, c.port) for c in errors[0].candidates} == {
        (model.node_id, "modeling"), (refit.node_id, "modeling"),
    }
    named = report.model_copy(update={"input_sources": sources(modeling=(refit, "modeling"))})
    assert check(ChangeContent(nodes=[cohort, seg, feats, model, refit, named])) == []


def test_ambiguous_input_from_one_node_with_two_compatible_ports():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    split, feats = node("split_masks"), node("extract_features")
    errors = check(ChangeContent(nodes=[cohort, seg, split, feats]))
    # seg.masks, split.left and split.right are all MaskSets.
    assert [e.code for e in errors] == ["AMBIGUOUS_INPUT"]
    assert len(errors[0].candidates) == 3


def test_an_insert_that_makes_a_later_node_ambiguous_is_refused():
    cohort, seg, feats, model = pipeline()
    report = node("report_results")
    graph = state(*(live(n, "completed") for n in (cohort, seg, feats, model)), live(report))
    assert check(ChangeContent(nodes=[cohort, seg, feats, model, report]), graph) == []
    inserted = ChangeContent(nodes=[cohort, seg, feats, model, node("refit_model"), report])
    errors = check(inserted, graph)
    assert [(e.code, e.node) for e in errors] == [("AMBIGUOUS_INPUT", report.node_id)]


def test_a_removal_that_takes_the_only_candidate_is_missing_input():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    graph = state(live(cohort, "completed"), live(seg))
    assert codes(ChangeContent(nodes=[seg]), graph) == ["MISSING_INPUT"]


def test_bad_input_source_names_a_later_or_unknown_node():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    later = node("build_cohort")
    seg_late = seg.model_copy(update={"input_sources": sources(cohort=(later, "cohort"))})
    assert codes(ChangeContent(nodes=[cohort, seg_late, later])) == ["BAD_INPUT_SOURCE"]
    ghost = InputSources(ports={"cohort": InputSource(node_id=uuid.uuid4(), port="cohort")})
    assert codes(ChangeContent(nodes=[cohort, seg.model_copy(update={"input_sources": ghost})])) == [
        "BAD_INPUT_SOURCE"
    ]


def test_type_mismatch_names_both_types():
    cohort, seg, feats, model = pipeline()
    report = node("report_results", input_sources=sources(modeling=(feats, "features")))
    errors = check(ChangeContent(nodes=[cohort, seg, feats, model, report]))
    assert [e.code for e in errors] == ["TYPE_MISMATCH"]
    assert "expected ModelingOutput, received FeatureTable" in errors[0].detail


def test_unknown_port_on_the_source_or_the_target():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    wrong_source = seg.model_copy(update={"input_sources": sources(cohort=(cohort, "people"))})
    assert codes(ChangeContent(nodes=[cohort, wrong_source])) == ["UNKNOWN_PORT"]
    wrong_target = seg.model_copy(update={"input_sources": sources(cohort=(cohort, "cohort"),
                                                                   extra=(cohort, "cohort"))})
    assert codes(ChangeContent(nodes=[cohort, wrong_target])) == ["UNKNOWN_PORT"]


def test_duplicate_node_twice_or_a_removed_id():
    cohort = node("build_cohort")
    assert codes(ChangeContent(nodes=[cohort, cohort])) == ["DUPLICATE_NODE"]
    removed = uuid.uuid4()
    graph = state(removed=(removed,))
    assert codes(ChangeContent(nodes=[node("build_cohort", node_id=removed)]), graph) == ["DUPLICATE_NODE"]


def test_a_live_node_cannot_change_its_target():
    cohort = node("build_cohort")
    graph = state(live(cohort))
    swapped = node("population_agent", node_id=cohort.node_id)
    assert codes(ChangeContent(nodes=[swapped]), graph) == ["TARGET_IMMUTABLE"]


def test_bad_expectation():
    good = Expectations(rules=[Expectation(metric="size", op=">=", value=30)])
    assert check(ChangeContent(nodes=[node("build_cohort", expect=good)])) == []
    on_port = Expectations(rules=[Expectation(metric="size", op=">=", value=30, port="cohort")])
    assert check(ChangeContent(nodes=[node("build_cohort", expect=on_port)])) == []
    bad = Expectations(rules=[Expectation(metric="auc", op=">", value=0.7)])
    errors = check(ChangeContent(nodes=[node("build_cohort", expect=bad)]))
    assert [e.code for e in errors] == ["BAD_EXPECTATION"] and "size" in errors[0].detail
    no_port = Expectations(rules=[Expectation(metric="size", op=">", value=1, port="people")])
    assert codes(ChangeContent(nodes=[node("build_cohort", expect=no_port)])) == ["UNKNOWN_PORT"]


def test_unknown_resource():
    cohort = node("build_cohort")
    gpu = node("gpu_segment")
    assert check(ChangeContent(nodes=[cohort, gpu])) == []
    assert codes(ChangeContent(nodes=[cohort, gpu]), pools=POOLS - {"gpu"}) == ["UNKNOWN_RESOURCE"]


def test_the_default_pools_come_from_config_resources_yaml():
    assert {"llm", "cpu_heavy", "gpu", "monai_inference", "orthanc_io"} <= pool_names()
    assert validate(state(), ChangeContent(nodes=[node("build_cohort"), node("gpu_segment")]), CATALOG) == []


def test_running_node_affected_only_when_rejecting():
    cohort, seg = node("build_cohort"), node("segmentation_agent")
    graph = state(live(cohort, "completed"), live(seg, "running"))
    edit = cohort.model_copy(update={"params": {"criteria": "MR"}})
    reject = ChangeContent(nodes=[edit, seg])
    errors = check(reject, graph)
    assert [(e.code, e.node) for e in errors] == [("RUNNING_NODE_AFFECTED", seg.node_id)]
    assert check(reject.model_copy(update={"on_running": "restart"}), graph) == []
    # An unaffected running node is fine: appending after it affects only the new node.
    assert check(ChangeContent(nodes=[cohort, seg, node("extract_features")]), graph) == []


def test_bad_seed():
    pop = node("population_agent")
    earlier_run = uuid.uuid4()
    graph = state(live(pop, "completed", history=(earlier_run,)))
    seed = SeedFrom(run_id=earlier_run, message_uuid="c7")

    def seeded(definition, reruns=True):
        seeded_node = definition.model_copy(update={"seed_from": seed})
        return ChangeContent(nodes=[seeded_node], rerun_node_ids=[definition.node_id] if reruns else [])

    assert check(seeded(pop), graph) == []
    assert codes(seeded(pop, reruns=False), graph) == ["BAD_SEED"]
    other_run = SeedFrom(run_id=uuid.uuid4(), message_uuid="c7")
    assert codes(ChangeContent(nodes=[pop.model_copy(update={"seed_from": other_run})],
                               rerun_node_ids=[pop.node_id]), graph) == ["BAD_SEED"]
    lit = node("literature_agent")
    assert codes(seeded(lit), state(live(lit, "completed", history=(earlier_run,)))) == ["BAD_SEED"]
    tool = node("build_cohort")
    assert codes(seeded(tool), state(live(tool, "completed", history=(earlier_run,)))) == ["BAD_SEED"]


def test_reruns_and_releases_must_name_sequence_nodes_and_real_holds():
    cohort = node("build_cohort")
    graph = state(live(cohort, "completed"))
    assert codes(ChangeContent(nodes=[cohort], rerun_node_ids=[uuid.uuid4()]), graph) == ["UNKNOWN_NODE"]
    assert codes(ChangeContent(nodes=[cohort], release_hold_node_ids=[cohort.node_id]), graph) == ["BAD_RELEASE"]
    held = state(live(cohort, "completed", hold=True))
    assert check(ChangeContent(nodes=[cohort], release_hold_node_ids=[cohort.node_id]), held) == []


def test_errors_never_suggest_a_strategy():
    """Errors name what is wrong; the fix is the Main's (system §9)."""
    cohort, seg, feats, model = pipeline()
    errors = check(ChangeContent(nodes=[cohort, seg, feats, model, node("refit_model"), node("report_results")]))
    for error in errors:
        assert not any(word in error.detail.lower() for word in ("should", "instead", "try", "recommend"))
