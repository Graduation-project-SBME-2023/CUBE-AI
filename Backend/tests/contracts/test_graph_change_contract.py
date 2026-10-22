"""The Agent System's graph model and the Application API's apply rule agree
(merged plan, Phase 8.3: ``affected()`` equals T2 step 5).

The two services may not import each other, so the content model exists on
both sides; this test holds them together on the same cases.
"""

from __future__ import annotations

import uuid

import pytest

from agent_system.execution.affected import affected
from agent_system.planning.graph_model import (
    ChangeContent,
    Expectation,
    Expectations,
    InputSource,
    InputSources,
    SeedFrom,
)
from application_api.core import graph_content as api
from tests.support.graph_fixtures import node

pytestmark = pytest.mark.contract


def _live(definitions, statuses=None):
    statuses = statuses or {}
    nodes = []
    for position, definition in enumerate(definitions, start=1):
        columns = api.node_columns(api.NodeDef.model_validate(definition.model_dump(mode="json")))
        status = statuses.get(definition.node_id)
        nodes.append(api.LiveNode(
            node_id=definition.node_id, position=position, kind=definition.kind, ref=definition.ref,
            ref_version=columns["target_version"], title=columns["title"], params=columns["params"],
            input_sources=columns["input_sources"], expectations=columns["expectations"],
            run_id=uuid.uuid4() if status else None, run_status=status,
        ))
    return nodes


def _cases():
    a, b, c, d = (node("build_cohort"), node("segmentation_agent"), node("extract_features"),
                  node("run_modeling"))
    old = [a, b, c, d]
    new = node("literature_agent")
    sources = InputSources(ports={"cohort": InputSource(node_id=a.node_id, port="cohort")})
    expect = Expectations(rules=[Expectation(metric="size", op=">=", value=30)])
    seed = SeedFrom(run_id=uuid.uuid4(), message_uuid="cp")
    return [
        ("unchanged", old, ChangeContent(nodes=old)),
        ("edit params", old, ChangeContent(nodes=[a, b.model_copy(update={"params": {"task": "x"}}), c, d])),
        ("edit title", old, ChangeContent(nodes=[a.model_copy(update={"title": "A"}), b, c, d])),
        ("set sources", old, ChangeContent(nodes=[a, b, c.model_copy(update={"input_sources": sources}), d])),
        ("set expectations", old, ChangeContent(nodes=[a.model_copy(update={"expect": expect}), b, c, d])),
        ("rerun", old, ChangeContent(nodes=old, rerun_node_ids=[c.node_id])),
        ("seeded rerun", old, ChangeContent(nodes=[a, b.model_copy(update={"seed_from": seed}), c, d],
                                            rerun_node_ids=[b.node_id])),
        ("insert", old, ChangeContent(nodes=[a, new, b, c, d])),
        ("append", old, ChangeContent(nodes=[*old, new])),
        ("remove middle", old, ChangeContent(nodes=[a, c, d])),
        ("remove last", old, ChangeContent(nodes=[a, b, c])),
        ("move", old, ChangeContent(nodes=[a, c, b, d])),
        ("empty graph", [], ChangeContent(nodes=[a, b])),
        ("clear", old, ChangeContent(nodes=[])),
    ]


@pytest.mark.parametrize(("name", "old", "content"), _cases(), ids=[case[0] for case in _cases()])
def test_affected_equals_the_t2_rule(name, old, content):
    api_content = api.ChangeContent.model_validate(content.document())
    plan = api.plan_apply(_live(old), api_content, used_node_ids={n.node_id for n in old},
                          execution_mode="auto")
    assert plan.errors == []
    assert affected(old, content) == plan.affected


@pytest.mark.parametrize(("name", "old", "content"), _cases(), ids=[case[0] for case in _cases()])
def test_the_content_document_is_the_api_content(name, old, content):
    """The stored JSON round-trips through the API model unchanged, so the
    digest the API computes is over exactly what the Diff Engine built."""
    document = content.document()
    assert api.ChangeContent.model_validate(document).document() == document


def test_supersedes_completed_matches_the_derived_reason():
    from agent_system.execution.diff_engine import DiffEngine
    from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
    from tests.support.graph_fixtures import POOLS, live, research_catalog, state

    a, b = node("build_cohort"), node("segmentation_agent")
    content = ChangeContent(nodes=[a.model_copy(update={"params": {"criteria": "MR"}}), b])
    engine = DiffEngine(InMemoryGraphStore(), research_catalog(), resource_pools=POOLS)
    outcome = engine.plan_content(state(live(a, "completed"), live(b)), content)
    plan = api.plan_apply(_live([a, b], {a.node_id: "completed"}),
                          api.ChangeContent.model_validate(content.document()),
                          used_node_ids={a.node_id, b.node_id}, execution_mode="auto")
    assert set(outcome.approval_reasons) == plan.reasons == {api.SUPERSEDES_COMPLETED}
