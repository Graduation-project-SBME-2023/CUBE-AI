"""The literature tools (merged plan, Phase 2.4 step 4), against respx mocks."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from agent_system.toolkit.manifest import DataScope, Visibility
from agent_system.toolkit.tools.registry import spec_of
from agent_system.toolpacks.literature import pico, run_state, search
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

RUN = "22222222-2222-4222-8222-222222222222"
PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PICO = {
    "population": "adults with splenic lymphoma on contrast CT",
    "intervention": "radiomics texture features with machine learning",
    "comparison": "visual assessment",
    "outcome": "diagnostic accuracy AUC",
    "hypothesis": "radiomics separates healthy from malignant spleens",
}
RELEVANT = "CT radiomics of the spleen for lymphoma classification"
OFF_TOPIC = "Crop yields under drought"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(search.asyncio, "sleep", sleep)
    return sleeps


@pytest.fixture(autouse=True)
def forget_runs():
    yield
    run_state._runs.clear()


def _ctx(**fields):
    return make_context(run_id=RUN, **fields)


# --- manifests


@pytest.mark.parametrize(
    "tool",
    [search.search_pubmed, search.search_arxiv, search.search_semantic_scholar, search.search_europe_pmc],
)
def test_the_searches_reach_the_network_and_only_agents_see_them(tool):
    spec = spec_of(tool)
    assert spec.data_scopes == {DataScope.NETWORK_EGRESS}
    assert spec.visibility == {Visibility.AGENT}
    assert spec.read_only


def test_save_output_has_its_one_new_name():
    assert spec_of(search.save_literature_output).name == "save_literature_output"


# --- PubMed


def _pubmed_xml(*titles: str) -> str:
    articles = "".join(
        f"""<PubmedArticle><MedlineCitation><PMID>{i}</PMID><Article>
        <ArticleTitle>{title}</ArticleTitle><Journal><Title>J</Title></Journal>
        <Abstract><AbstractText>{title}</AbstractText></Abstract>
        <AuthorList><Author><LastName>Doe</LastName><ForeName>J</ForeName></Author></AuthorList>
        </Article><DateCompleted/></MedlineCitation></PubmedArticle>"""
        for i, title in enumerate(titles, start=1)
    )
    return f"<PubmedArticleSet>{articles}</PubmedArticleSet>"


def test_pubmed_keeps_the_domain_relevant_papers():
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{PUBMED}/esearch.fcgi").respond(json={"esearchresult": {"idlist": ["1", "2"]}})
        router.get(f"{PUBMED}/efetch.fcgi").respond(text=_pubmed_xml(RELEVANT, OFF_TOPIC))
        result = json.loads(call_tool(search.search_pubmed, {"query": "spleen radiomics"}, _ctx()))

    assert [paper["title"] for paper in result["results"]] == [RELEVANT]
    assert result["results"][0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/1/"
    assert result["note"] == "1 domain-relevant results from 2 retrieved"


def test_a_server_error_is_retried_then_reported(no_waiting):
    with respx.mock() as router:
        route = router.get(f"{PUBMED}/esearch.fcgi").mock(return_value=httpx.Response(503))
        result = call_tool(search.search_pubmed, {"query": "q"}, _ctx())

    assert route.call_count == search.ATTEMPTS
    assert result["error_code"] == "unavailable"
    # The agent can still move on: the envelope says which database and query failed.
    assert (result["source"], result["query"], result["results"]) == ("PubMed", "q", [])
    assert [1, 2] == [s for s in no_waiting if s >= 1]  # the backoff, not blocking sleeps


def test_a_client_error_is_not_retried():
    with respx.mock() as router:
        route = router.get(f"{PUBMED}/esearch.fcgi").mock(return_value=httpx.Response(400))
        call_tool(search.search_pubmed, {"query": "q"}, _ctx())

    assert route.call_count == 1


# --- Arxiv, Semantic Scholar, Europe PMC


def test_arxiv_parses_the_atom_feed():
    feed = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>{RELEVANT}</title><summary>CT radiomics</summary>
      <id>http://arxiv.org/abs/1234</id><published>2024-01-02T00:00:00Z</published>
      <author><name>A</name></author></entry></feed>"""
    with respx.mock() as router:
        router.get("http://export.arxiv.org/api/query").respond(text=feed)
        result = json.loads(call_tool(search.search_arxiv, {"query": "q"}, _ctx()))

    [paper] = result["results"]
    assert paper["year"] == "2024"
    assert paper["pdf_url"] == "http://arxiv.org/pdf/1234"


def test_semantic_scholar_falls_back_to_a_pubmed_url():
    body = {"data": [{"title": RELEVANT, "abstract": "CT radiomics", "authors": [],
                      "externalIds": {"PubMed": "99"}, "year": 2023}]}
    with respx.mock() as router:
        router.get("https://api.semanticscholar.org/graph/v1/paper/search").respond(json=body)
        result = json.loads(call_tool(search.search_semantic_scholar, {"query": "q"}, _ctx()))

    assert result["results"][0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/99/"


def test_europe_pmc_filters_off_topic_hits():
    body = {"resultList": {"result": [
        {"title": RELEVANT, "abstractText": "CT radiomics", "pmid": "5"},
        {"title": OFF_TOPIC, "abstractText": "wheat"},
    ]}}
    with respx.mock() as router:
        router.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search").respond(json=body)
        result = json.loads(call_tool(search.search_europe_pmc, {"query": "q"}, _ctx()))

    assert [paper["title"] for paper in result["results"]] == [RELEVANT]


# --- the run's keywords


def test_a_runs_pico_sets_its_keywords():
    keywords = run_state.use_pico(RUN, PICO)

    assert keywords != pico.DOMAIN_KEYWORDS_FALLBACK
    assert run_state.keywords_for(RUN) == keywords
    assert run_state.keywords_for("another-run") == pico.DOMAIN_KEYWORDS_FALLBACK
    assert run_state.keywords_for(None) == pico.DOMAIN_KEYWORDS_FALLBACK


# --- scoring and saving


def test_the_relevance_score_weights_the_pico_dimensions():
    paper = {"title": "radiomics spleen lymphoma", "abstract": "machine learning texture"}
    result = json.loads(call_tool(
        search.score_paper_relevance,
        {"paper_json": json.dumps(paper), "pico_json": json.dumps(PICO)},
        _ctx(),
    ))

    assert 0 < result["relevance_score"] <= 100
    assert set(result["score_breakdown"]) == {"population", "intervention", "comparison", "outcome", "hypothesis"}


def test_a_malformed_paper_is_an_error_result():
    result = call_tool(search.score_paper_relevance, {"paper_json": "{", "pico_json": "{}"}, _ctx())
    assert result["error_code"] == "invalid_args"


def test_outputs_are_kept_per_run_until_released():
    saved = call_tool(
        search.save_literature_output,
        {"filename": "../outputs/literature_report.md", "content": "# Report"},
        _ctx(),
    )

    assert saved == "Captured successfully: literature_report.md"  # a path is never kept
    assert run_state.release(RUN) == {"literature_report.md": "# Report"}
    assert run_state.release(RUN) == {}


def test_outputs_need_a_run():
    result = call_tool(
        search.save_literature_output, {"filename": "context.json", "content": "{}"}, make_context()
    )
    assert result["error_code"] == "no_run"
