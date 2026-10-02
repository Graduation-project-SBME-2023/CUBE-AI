"""The literature review's tools (merged plan, Phase 2.4 step 4).

Copied from ``agents/literature_review_agent/literature_review_agent.py``
(``_search_*``, ``score_paper_relevance``, ``_capture_output`` and the
``FunctionTool`` wrappers at 1499-1525), which stays frozen with the AutoGen
agent. What changed on the way:

- HTTP goes through the shared async client with explicit timeouts; the
  synchronous ``urllib`` calls and ``time.sleep`` rate-limit delays blocked the
  event loop for seconds at a time. The retry rule is kept: a 429 or a 5xx is
  retried with exponential backoff, three attempts in all.
- The domain keywords and the captured files are the run's (``run_state``),
  not a closure's.
- A failed search is an ``error_result`` that still carries ``source``,
  ``query`` and an empty ``results`` list, so the agent can move on to the
  next database. A successful result keeps its JSON shape.
- ``save_output`` is ``save_literature_output``: the only new name, because
  ``save_output`` is too generic for a registry every agent shares.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .._toolkit import error_result, tool
from ..common import domain_api
from ..common.http_client import shared_client, timeout
from . import run_state
from .pico import is_domain_relevant

logger = logging.getLogger(__name__)

# The defaults of the literature agent's configuration and rate limits.
DEFAULT_MAX_RESULTS = 8
PUBMED_DELAY_S = 0.4
GENERIC_DELAY_S = 0.25
SEMANTIC_SCHOLAR_DELAY_S = 5.0  # Semantic Scholar rate-limits aggressively without a key
HTTP_TIMEOUT_S = 20.0
ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "PICO-KnowledgeBuilder/1.0 (medical-research; radiology-AI)"
ABSTRACT_CHARS = 800

LITERATURE_TOOL = dict(
    side_effects="none",
    cost="light",
    execution="inline",
    read_only=True,
    data_scopes={"network_egress"},
    visibility={"agent"},
)


class SearchFailed(RuntimeError):
    pass


async def _fetch(url: str, params: dict[str, Any] | None = None) -> bytes:
    """GET with the retry rule of ``knowledge_builder._http_get``."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            response = await shared_client().get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"},
                timeout=timeout(HTTP_TIMEOUT_S),
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            last = exc
        else:
            if response.status_code < 400:
                return response.content
            last = SearchFailed(f"HTTP {response.status_code}")
            if response.status_code not in RETRY_STATUSES:
                break
        if attempt < ATTEMPTS - 1:
            await asyncio.sleep(2**attempt)
    raise SearchFailed(str(last))


def _authors(names: list[str]) -> str:
    return ", ".join(names[:5]) + (" et al." if len(names) > 5 else "")


def _keywords() -> list[str]:
    return run_state.keywords_for(domain_api.run_id())


def _search_failed(source: str, query: str, exc: Exception) -> dict[str, Any]:
    logger.warning("%s search failed for %r: %s", source, query, exc)
    return error_result(
        "unavailable", f"{source} search failed: {exc}", source=source, query=query, results=[]
    )


@tool(**LITERATURE_TOOL)
async def search_pubmed(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str | dict:
    """Search PubMed via NCBI E-utilities for clinical/medical literature."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    api_key = os.environ.get("NCBI_API_KEY", "")
    key = {"api_key": api_key} if api_key else {}
    keywords = _keywords()
    try:
        await asyncio.sleep(PUBMED_DELAY_S)  # NCBI rate limit
        search = json.loads(await _fetch(
            f"{base}/esearch.fcgi",
            {"db": "pubmed", "term": query, "retmode": "json", "retmax": max_results,
             "sort": "relevance", **key},
        ))
        id_list = search.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return json.dumps({"source": "PubMed", "query": query, "results": [],
                               "note": "No IDs returned by esearch"})
        await asyncio.sleep(PUBMED_DELAY_S)
        root = ET.fromstring((await _fetch(
            f"{base}/efetch.fcgi",
            {"db": "pubmed", "id": ",".join(id_list), "retmode": "xml", "rettype": "abstract", **key},
        )).decode("utf-8"))
    except (SearchFailed, ValueError, ET.ParseError) as exc:
        return _search_failed("PubMed", query, exc)

    results = []
    for article in root.findall(".//PubmedArticle"):
        try:
            medline = article.find("MedlineCitation")
            art = medline.find("Article")
            title = art.findtext("ArticleTitle", default="").strip()
            authors = [
                f"{a.findtext('LastName', '')} {a.findtext('ForeName', '')}".strip()
                for a in art.findall(".//Author")
                if a.findtext("LastName", "")
            ]
            journal = art.findtext(".//Title", default="Unknown Journal").strip()
            year = (medline.findtext(".//PubDate/Year")
                    or medline.findtext(".//PubDate/MedlineDate", "n.d.")[:4])
            abstract = " ".join((p.text or "") for p in art.findall(".//AbstractText")).strip()[:ABSTRACT_CHARS]
            pmid = medline.findtext("PMID", default="")
        except AttributeError:
            continue  # a malformed record
        if not is_domain_relevant(title, abstract, keywords):
            continue
        results.append({
            "title": title, "authors": _authors(authors), "journal": journal, "year": year,
            "abstract": abstract, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "source": "PubMed", "pmid": pmid,
        })
    return json.dumps({"source": "PubMed", "query": query, "results": results,
                       "note": f"{len(results)} domain-relevant results from {len(id_list)} retrieved"})


@tool(**LITERATURE_TOOL)
async def search_arxiv(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str | dict:
    """Search Arxiv for AI/technical preprints on medical imaging."""
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    keywords = _keywords()
    # Scoped to the medical-imaging categories for precision.
    scoped = f"({query}) AND (cat:cs.CV OR cat:eess.IV OR cat:cs.LG OR cat:cs.AI)"
    try:
        await asyncio.sleep(GENERIC_DELAY_S)
        root = ET.fromstring((await _fetch(
            "http://export.arxiv.org/api/query",
            {"search_query": scoped, "start": 0, "max_results": max_results, "sortBy": "relevance"},
        )).decode("utf-8"))
    except (SearchFailed, ValueError, ET.ParseError) as exc:
        return _search_failed("Arxiv", query, exc)

    def text(element, tag: str, default: str = "") -> str:
        found = element.find(tag, namespace)
        return found.text.strip() if found is not None and found.text else default

    results = []
    for entry in root.findall("atom:entry", namespace):
        title = text(entry, "atom:title", "No Title").replace("\n", " ")
        summary = text(entry, "atom:summary").replace("\n", " ")[:ABSTRACT_CHARS]
        if not is_domain_relevant(title, summary, keywords):
            continue
        link = text(entry, "atom:id")
        published = text(entry, "atom:published")
        results.append({
            "title": title,
            "authors": _authors([text(a, "atom:name") for a in entry.findall("atom:author", namespace)]),
            "journal": "Arxiv Preprint",
            "year": published[:4] if published else "n.d.",
            "abstract": summary,
            "url": link,
            "pdf_url": link.replace("abs", "pdf") if "abs" in link else link,
            "source": "Arxiv",
        })
    return json.dumps({"source": "Arxiv", "query": query, "results": results,
                       "note": f"{len(results)} domain-relevant results"})


@tool(**LITERATURE_TOOL)
async def search_semantic_scholar(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str | dict:
    """Search Semantic Scholar for cross-disciplinary papers."""
    keywords = _keywords()
    try:
        await asyncio.sleep(SEMANTIC_SCHOLAR_DELAY_S)
        data = json.loads(await _fetch(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            {"query": query, "limit": max_results,
             "fields": "title,authors,year,abstract,externalIds,citationCount,url"},
        ))
    except (SearchFailed, ValueError) as exc:
        return _search_failed("SemanticScholar", query, exc)

    results = []
    for paper in data.get("data", []):
        title = paper.get("title", "No Title") or "No Title"
        abstract = (paper.get("abstract") or "")[:ABSTRACT_CHARS]
        if not is_domain_relevant(title, abstract, keywords):
            continue
        ext = paper.get("externalIds") or {}
        url = (
            paper.get("url")
            or (f"https://pubmed.ncbi.nlm.nih.gov/{ext['PubMed']}/" if "PubMed" in ext else
                f"https://arxiv.org/abs/{ext['ArXiv']}" if "ArXiv" in ext else
                f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}")
        )
        results.append({
            "title": title,
            "authors": _authors([a.get("name", "") for a in paper.get("authors", [])]),
            "journal": "Semantic Scholar",
            "year": str(paper.get("year", "n.d.")),
            "abstract": abstract,
            "url": url,
            "citation_count": paper.get("citationCount", 0),
            "source": "SemanticScholar",
        })
    return json.dumps({"source": "SemanticScholar", "query": query, "results": results,
                       "note": f"{len(results)} domain-relevant results"})


@tool(**LITERATURE_TOOL)
async def search_europe_pmc(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str | dict:
    """Search Europe PMC for open-access life-science papers."""
    keywords = _keywords()
    try:
        await asyncio.sleep(GENERIC_DELAY_S)
        data = json.loads(await _fetch(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            {"query": query, "format": "json", "pageSize": max_results,
             "resultType": "core", "sort": "RELEVANCE"},
        ))
    except (SearchFailed, ValueError) as exc:
        return _search_failed("EuropePMC", query, exc)

    results = []
    for hit in data.get("resultList", {}).get("result", []):
        title = hit.get("title", "No Title").strip()
        abstract = (hit.get("abstractText") or "")[:ABSTRACT_CHARS]
        if not is_domain_relevant(title, abstract, keywords):
            continue
        pmid, pmcid = hit.get("pmid", ""), hit.get("pmcid", "")
        results.append({
            "title": title,
            "authors": hit.get("authorString", "Unknown Authors"),
            "journal": hit.get("journalTitle", "Unknown Journal"),
            "year": str(hit.get("pubYear", "n.d.")),
            "abstract": abstract,
            "url": (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else
                    f"https://europepmc.org/article/PMC/{pmcid}" if pmcid else
                    "https://europepmc.org"),
            "source": "EuropePMC",
        })
    return json.dumps({"source": "EuropePMC", "query": query, "results": results,
                       "note": f"{len(results)} domain-relevant results"})


# Weights of the PICO dimensions in the relevance score.
_SCORE_WEIGHTS = {"population": 0.20, "intervention": 0.25, "comparison": 0.15,
                  "outcome": 0.20, "hypothesis": 0.20}


@tool(
    side_effects="none",
    cost="light",
    execution="inline",
    read_only=True,
    idempotent=True,
    visibility={"agent"},
)
async def score_paper_relevance(paper_json: str, pico_json: str) -> str | dict:
    """Score a paper's relevance vs. the PICO hypothesis (0-100): weighted
    keyword overlap of its title and abstract with each PICO dimension
    (population 0.20, intervention 0.25, comparison 0.15, outcome 0.20,
    hypothesis 0.20), with the per-dimension breakdown."""
    try:
        paper = json.loads(paper_json)
        pico = json.loads(pico_json)
        corpus = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    except (ValueError, AttributeError) as exc:
        return error_result("invalid_args", f"paper_json and pico_json must be JSON objects: {exc}")

    def score(field_text: str, weight: float) -> float:
        words = set(re.findall(r"\b\w{4,}\b", str(field_text).lower()))
        if not words:
            return 0.0
        return round(sum(1 for w in words if w in corpus) / len(words) * weight * 100, 2)

    breakdown = {name: score(pico.get(name, ""), weight) for name, weight in _SCORE_WEIGHTS.items()}
    return json.dumps({
        "title": paper.get("title", ""),
        "relevance_score": min(round(sum(breakdown.values()), 2), 100.0),
        "score_breakdown": breakdown,
    })


@tool(
    side_effects="writes",
    cost="light",
    execution="inline",
    idempotent=True,
    visibility={"agent"},
)
async def save_literature_output(filename: str, content: str) -> str | dict:
    """Capture a generated literature output (context.json,
    literature_report.md or ranked_papers.json) for database persistence."""
    run_id = domain_api.run_id()
    if not run_id:
        return error_result("no_run", "Literature outputs belong to a run; this call has none.")
    try:
        name = run_state.capture(run_id, filename, content)
    except ValueError as exc:
        return error_result("invalid_args", str(exc))
    return f"Captured successfully: {name}"
