"""literature_review_agent and its five SDK subagents, as loaded from
agentpacks/ (merged plan, Phases 3.2 and 3.3)."""

from __future__ import annotations

import asyncio
import functools
import json

import httpx
import pytest
from pydantic import ValidationError

from agent_system.agentpacks.research.literature_review_agent import contracts
from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.agents.spec import AgentKind
from agent_system.toolkit.artifacts.builtin import LITERATURE_CONTEXT
from agent_system.toolkit.catalog import build
from agent_system.toolkit.context import use_context
from agent_system.toolpacks.literature import run_state
from tests.support.toolkit_fixtures import TEST_SESSION_ID, FakeEventSink, make_context

pytestmark = pytest.mark.unit

STEPS = ("pico_analyst", "query_builder", "search_executor", "relevance_ranker", "context_compiler")
RUN_ID = "7d1f4c2e-0000-4000-8000-000000000006"
REVIEW_ID = "11111111-0000-4000-8000-0000000000d1"
RECORDED = {"literature_review_id": REVIEW_ID, "n_papers": 2, "highly_relevant": 1}
COMPILED = {
    "files_saved": list(contracts.REVIEW_FILES), "papers_reviewed": 3, "highly_relevant": 1,
    "similar_studies": 0, "summary": "3 papers; the top one scores 87.5.",
}  # fmt: skip
SEARCH_TOOLS = ("search_pubmed", "search_arxiv", "search_semantic_scholar", "search_europe_pmc")


@functools.cache
def _catalog():
    return build()


@pytest.fixture(scope="module")
def spec():
    return _catalog().agents.get("literature_review_agent")


# --- an ordinary agent with optional subagents


def test_the_spec(spec):
    assert spec.kind is AgentKind.EXECUTION
    assert spec.inputs == {}  # the PICO comes from the task
    assert spec.outputs["literature"].artifact_type == "LiteratureContext"
    # It may do every part itself, or hand parts to its subagents in any order.
    assert spec.allowed_tools == (*SEARCH_TOOLS, "score_paper_relevance", "save_literature_output")
    assert spec.delegates == STEPS
    assert spec.output_contract is contracts.CompiledContext
    assert spec.resumable is True  # a retry resumes its saved SDK session
    assert not hasattr(spec, "workflow") and not hasattr(spec, "build_graph")


def test_its_prompt_fixes_no_order(spec):
    assert "nothing fixes the order" in spec.system_prompt
    assert "several at once" in spec.system_prompt


def test_the_context_builder_sets_the_runs_domain_keywords(spec):
    task = (
        "PICO: adults with renal cell carcinoma on contrast CT; radiomics with random forest "
        "versus radiologist reading; malignancy."
    )

    assert spec.context_builder(task, {}, make_context(run_id=RUN_ID)) == task
    keywords = run_state.keywords_for(RUN_ID)
    run_state.release(RUN_ID)
    assert keywords[:2] == ["Kidney", "Carcinoma"]  # the study's terms, not only the fallback


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
    assert agent.resumable is False  # its transcript is stored with its parent's
    assert agent.delegates == ()  # subagents run one level deep


def test_every_database_has_a_search_tool_the_executor_may_use():
    executor = _catalog().agents.get("search_executor")

    assert set(contracts.DATABASE_TOOLS.values()) == set(executor.allowed_tools)


@pytest.mark.parametrize("step", ["search_executor", "relevance_ranker"])
def test_the_parallel_subagents_say_so(step):
    agent = _catalog().agents.get(step)

    assert "several may run at once" in agent.description
    assert "branch" not in agent.system_prompt


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


# --- the completion check, the record, and the literature port


def _captured(**files):
    run_state.release(RUN_ID)
    for name, content in files.items():
        run_state.capture(RUN_ID, name, content)


def _check(spec, output=COMPILED):
    parsed = None if output is None else spec.output_contract.model_validate(output)
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))


def test_complete_once_every_file_is_saved(spec):
    _captured(**{name: "x" for name in contracts.REVIEW_FILES})
    try:
        assert _check(spec) == []
    finally:
        run_state.release(RUN_ID)


def test_a_missing_file_is_named(spec):
    _captured(**{"literature_report.md": "x", "context.json": "{}"})
    try:
        (issue,) = _check(spec)
    finally:
        run_state.release(RUN_ID)

    assert "ranked_papers.json is not saved" in issue


def test_without_a_structured_answer(spec):
    (issue,) = _check(spec, output=None)

    assert "required format" in issue


PAPERS = [
    {"title": "Renal radiomics", "authors": "A. One, B. Two", "year": "2023", "pmid": "123",
     "relevance_score": 87.5, "tier": "highly_relevant", "similar_study": True},
    {"title": "CT texture", "doi": "10.1/x", "year_numeric": 2021},
    {"title": "No identifier"},
]  # fmt: skip


def _record(spec, fake_domain_api, sink=None):
    _captured(**{
        "literature_report.md": "# Report",
        "context.json": json.dumps({"metadata": {"pico": {"population": "adults"}}}),
        "ranked_papers.json": json.dumps(PAPERS),
    })
    created = fake_domain_api.post("/literature-reviews/").respond(
        201, json={"literature_review_id": REVIEW_ID}
    )
    papers = fake_domain_api.post(f"/literature-reviews/{REVIEW_ID}/papers").respond(200, json={})
    ctx = make_context(run_id=RUN_ID, attempt_id="att-1", event_sink=sink)
    with use_context(ctx):
        output = asyncio.run(spec.on_complete(ctx, COMPILED))
    return output, created, papers


def test_on_complete_records_the_review_under_the_run(spec, fake_domain_api):
    sink = FakeEventSink()
    output, created, papers = _record(spec, fake_domain_api, sink)

    review = json.loads(created.calls.last.request.content)
    assert (review["session_id"], review["review_key"]) == (TEST_SESSION_ID, RUN_ID)
    assert review["query"] == {"population": "adults"}
    recorded = json.loads(papers.calls.last.request.content)
    assert [paper["title"] for paper in recorded] == ["Renal radiomics", "CT texture"]
    assert recorded[0]["authors"] == ["A. One", "B. Two"] and recorded[0]["year"] == 2023
    assert recorded[1]["year"] == 2021
    # The write declares its D24 path, like every write on the execution path.
    assert created.calls.last.request.headers["X-Attempt-Id"] == "att-1"
    assert output == RECORDED | {"warnings": []}
    # LiteratureReviewView keeps working: the same view the planner used to send.
    (view,) = sink.views
    assert view.type == "lit_review_output"
    assert view.payload["report"] == "# Report"
    # The files are handed over once.
    assert run_state.captured_outputs(RUN_ID) == {}


def test_extract_literature(spec, fake_domain_api):
    output, _, _ = _record(spec, fake_domain_api)
    result = AgentResult(status=AgentStatus.COMPLETED, output=output)

    (artifact,) = spec.outputs["literature"].extract_artifacts(result)

    assert dict(artifact.ref) == {"literature_review_id": REVIEW_ID}
    assert dict(artifact.summary) == {"n_papers": 2}
    LITERATURE_CONTEXT.validate_artifact(artifact)
