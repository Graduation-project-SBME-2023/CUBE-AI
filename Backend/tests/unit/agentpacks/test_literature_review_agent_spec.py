"""literature_review_agent and its five workflow steps, as loaded from
agentpacks/ (merged plan, Phases 3.2 and 3.3)."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from pydantic import ValidationError

from agent_system.agentpacks.research.literature_review_agent import contracts
from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.agents.spec import AgentKind
from agent_system.toolkit.artifacts.builtin import LITERATURE_CONTEXT
from agent_system.toolkit.catalog import build
from tests.support.toolkit_fixtures import TEST_SESSION_ID, make_context

pytestmark = pytest.mark.unit

STEPS = ("pico_analyst", "query_builder", "search_executor", "relevance_ranker", "context_compiler")
RUN_ID = "7d1f4c2e-0000-4000-8000-000000000006"
REVIEW_ID = "11111111-0000-4000-8000-0000000000d1"
RECORDED = {"literature_review_id": REVIEW_ID, "n_papers": 2, "highly_relevant": 1}


@functools.cache
def _catalog():
    return build()


@pytest.fixture(scope="module")
def spec():
    return _catalog().agents.get("literature_review_agent")


# --- the workflow agent


def test_the_spec(spec):
    assert spec.kind is AgentKind.EXECUTION
    assert spec.inputs == {}  # the PICO comes from the node's params
    assert spec.outputs["literature"].artifact_type == "LiteratureContext"
    assert spec.workflow == STEPS
    assert spec.delegates == () and spec.allowed_tools == ()
    assert spec.resumable is True  # a resume continues inside the step it stopped in


def test_the_graph_is_built_with_the_engine(spec):
    with pytest.raises(NotImplementedError, match="Phase 9.6"):
        spec.build_graph(make_context(run_id=RUN_ID))


@pytest.mark.parametrize(
    ("step", "contract", "tools"),
    [
        ("pico_analyst", contracts.PicoAnalysis, ()),
        ("query_builder", contracts.QueryPlan, ()),
        ("search_executor", contracts.SourceSearch,
         ("search_pubmed", "search_arxiv", "search_semantic_scholar", "search_europe_pmc")),
        ("relevance_ranker", contracts.ScoredPaper, ("score_paper_relevance",)),
        ("context_compiler", contracts.CompiledContext, ("save_literature_output",)),
    ],
)  # fmt: skip
def test_the_steps(step, contract, tools):
    agent = _catalog().agents.get(step)

    assert (agent.kind, agent.parent) == (AgentKind.INTERNAL, "literature_review_agent")
    assert agent.output_contract is contract
    assert agent.allowed_tools == tools
    assert agent.resumable is False  # checkpointed in the parent's namespace


def test_every_database_has_a_search_tool_the_executor_may_use():
    executor = _catalog().agents.get("search_executor")

    assert set(contracts.DATABASE_TOOLS.values()) == set(executor.allowed_tools)


# --- the prompts: generic, fixed, and free of the AutoGen chat


@pytest.mark.parametrize("step", STEPS)
def test_no_step_prompt_is_tied_to_one_study(step):
    """The legacy prompts were written for one splenic study: the ranker gave
    +10 to spleen papers and -10 to lung, liver and kidney papers, whatever
    the study was."""
    prompt = _catalog().agents.get(step).system_prompt.lower()

    for word in ("spleen", "splenic", "lymphoma"):
        assert word not in prompt, f"{step} prompt is about one study: {word!r}"


@pytest.mark.parametrize("step", STEPS)
def test_no_step_prompt_keeps_the_round_robin_chat(step):
    prompt = _catalog().agents.get(step).system_prompt

    for text in ("TERMINATE", "save_output(", "of 5 in the", "_Agent", "{knowledge_base}"):
        assert text not in prompt, f"{step}: {text!r}"


# --- the step contracts


def test_a_query_plan_has_unique_ids_and_splits_by_database():
    plan = contracts.QueryPlan.model_validate({
        "queries": [
            {"id": "q01", "query_text": "a", "db": "pubmed", "priority": 2},
            {"id": "q02", "query_text": "b", "db": "pubmed", "priority": 1},
            {"id": "q03", "query_text": "c", "db": "arxiv"},
        ],
        "hypothesis_similarity_queries": [{"id": "hs01", "query_text": "d", "db": "europe_pmc"}],
    })

    assert [q.id for q in plan.for_database("pubmed")] == ["q02", "q01"]  # priority first
    assert [q.id for q in plan.for_database("europe_pmc")] == ["hs01"]
    with pytest.raises(ValidationError, match="more than once"):
        contracts.QueryPlan.model_validate({"queries": [
            {"id": "q01", "query_text": "a", "db": "pubmed"},
            {"id": "q01", "query_text": "b", "db": "arxiv"},
        ]})


@pytest.mark.parametrize(
    "query",
    [
        {"id": "q01", "query_text": "x" * 301, "db": "pubmed"},
        {"id": "q01", "query_text": "a", "db": "google_scholar"},
        {"id": "q01", "query_text": "a", "db": "pubmed", "priority": 4},
    ],
)
def test_a_query_outside_the_rules_is_refused(query):
    with pytest.raises(ValidationError):
        contracts.QueryPlan.model_validate({"queries": [query]})


def test_similarity_queries_use_the_concept_databases():
    with pytest.raises(ValidationError):
        contracts.SimilarityQuery.model_validate({"id": "hs01", "query_text": "a", "db": "pubmed"})


def _scored(**fields):
    return {"title": "T", "raw_score": 72.5, "expert_adjustment": 15, "relevance_score": 87.5,
            "tier": "highly_relevant"} | fields


def test_a_score_must_add_up():
    assert contracts.ScoredPaper.model_validate(_scored()).relevance_score == 87.5
    # capped at 100
    assert contracts.ScoredPaper.model_validate(_scored(expert_adjustment=40, relevance_score=100))
    with pytest.raises(ValidationError, match="is not raw_score"):
        contracts.ScoredPaper.model_validate(_scored(relevance_score=95))


def test_the_compiler_must_save_every_file():
    answer = {"files_saved": ["literature_report.md", "context.json"], "papers_reviewed": 3,
              "highly_relevant": 1, "similar_studies": 0, "summary": "s"}

    with pytest.raises(ValidationError, match="ranked_papers.json"):
        contracts.CompiledContext.model_validate(answer)


# --- the literature port and the completion check


def test_extract_literature(spec):
    result = AgentResult(status=AgentStatus.COMPLETED, output=RECORDED)

    (artifact,) = spec.outputs["literature"].extract_artifacts(result)

    assert dict(artifact.ref) == {"literature_review_id": REVIEW_ID}
    assert dict(artifact.summary) == {"n_papers": 2}
    LITERATURE_CONTEXT.validate_artifact(artifact)


def _review(**fields):
    return {"literature_review_id": REVIEW_ID, "session_id": TEST_SESSION_ID,
            "review_key": RUN_ID, "papers": [{"title": "a"}, {"title": "b"}]} | fields


def _check(spec, fake_domain_api, review):
    route = fake_domain_api.get(f"/literature-reviews/{REVIEW_ID}")
    if review is None:
        route.mock(return_value=httpx.Response(404, json={"detail": "not found"}))
    else:
        route.mock(return_value=httpx.Response(200, json=review))
    parsed = spec.output_contract.model_validate(RECORDED)
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))


def test_complete_when_this_runs_review_is_recorded(spec, fake_domain_api):
    assert _check(spec, fake_domain_api, _review()) == []


@pytest.mark.parametrize(
    ("review", "expected"),
    [
        (None, "does not exist"),
        (_review(review_key="planner-review"), "not recorded by this run"),
        (_review(session_id="another-session"), "not recorded by this run"),
        (_review(papers=[{"title": "a"}]), "records 1 papers, not 2"),
    ],
)
def test_what_is_wrong_is_named(spec, fake_domain_api, review, expected):
    (issue,) = _check(spec, fake_domain_api, review)

    assert expected in issue
