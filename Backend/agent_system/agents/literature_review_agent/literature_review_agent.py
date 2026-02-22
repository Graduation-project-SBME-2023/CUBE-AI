"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        PICO Literature Review Agent — Agentic AI Radiology Researcher       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Pipeline:                                                                   ║
║    1. PICO_Analyst_Agent    → Deconstructs question into topics/dimensions   ║
║    2. Query_Builder_Agent   → Builds Boolean queries & keyword sets per DB   ║
║    3. Search_Executor_Agent → Fans out across PubMed, Arxiv,                ║
║                               SemanticScholar, EuropePMC                    ║
║    4. Relevance_Ranker_Agent→ Scores & ranks papers vs. PICO hypothesis      ║
║    5. Context_Compiler_Agent→ Produces context.json + Markdown report        ║
║                                                                              ║
║  Outputs (written to ./literature_review_output/):                           ║
║    • context.json          — structured context for downstream agents        ║
║    • literature_report.md  — human-readable synthesized review               ║
║    • ranked_papers.json    — all papers with relevance scores                ║
╚══════════════════════════════════════════════════════════════════════════════╝

Downstream agents that consume context.json:
    population_planner, segmentation_agent, feature_extraction_agent,
    modeling_agent, validation_agent, reporter_agent, result_reporter_agent

Usage:
    python literature_review_agent.py

    Or import and call programmatically:
        from literature_review_agent import run_literature_review
        await run_literature_review(pico)

PICO Input Schema:
    {
        "population"   : "Who are the subjects?",
        "intervention" : "What is being tested / applied?",
        "comparison"   : "Alternative or control?",
        "outcome"      : "What is measured or expected?",
        "hypothesis"   : "Core claim the review should validate.",
        "question"     : "Free-text research question (optional extra context)."
    }
"""

# ──────────────────────────────────────────────────────────────────────────────
# Standard Library
# ──────────────────────────────────────────────────────────────────────────────
import asyncio
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────────────
# PATH FIX
# ──────────────────────────────────────────────────────────────────────────────
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

# ──────────────────────────────────────────────────────────────────────────────
# AutoGen
# ──────────────────────────────────────────────────────────────────────────────
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_core.tools import FunctionTool
from autogen_ext.models.ollama import OllamaChatCompletionClient

# ──────────────────────────────────────────────────────────────────────────────
# Output directory
# ──────────────────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path("./literature_review_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — LLM CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class OllamaClient(OllamaChatCompletionClient):
    """
    Wrapper around OllamaChatCompletionClient with sane defaults.
    Swap DEFAULT_MODEL to any tag pulled in your local Ollama instance.
    """
    DEFAULT_MODEL = "deepseek-v3.1:671b-cloud"
    DEFAULT_CTX   = 32_768  # larger context for multi-paper synthesis

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_info: Optional[Dict] = None,
        num_ctx: int = DEFAULT_CTX,
    ):
        if model_info is None:
            model_info = {
                "vision"           : False,
                "function_calling" : True,
                "json_output"      : True,
                "structured_output": True,
                "family"           : "unknown",
            }
        super().__init__(
            model      = model,
            model_info = model_info,
            options    = {"num_ctx": num_ctx},
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — SEARCH TOOL IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════

import time

# ── NCBI API key — optional but raises rate limit from 3 → 10 req/sec ────────
# Set the environment variable NCBI_API_KEY before running, or paste key here.
NCBI_API_KEY: str = os.environ.get("NCBI_API_KEY", "")

# ── Inter-query delay (seconds) — prevents 429s on repeated PubMed calls ─────
PUBMED_DELAY: float = 0.4      # 0.34s = ~3 req/s without key; 0.1s with key
GENERIC_DELAY: float = 0.2


def _http_get(url: str, timeout: int = 20, retries: int = 3) -> bytes:
    """
    HTTP GET with exponential-backoff retry and a descriptive User-Agent.
    Retries on 429 (rate limit) and transient 5xx errors.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PICO-LitReview-Agent/2.0 (radiology research)"}
    )
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt          # 1s, 2s, 4s
                time.sleep(wait)
                continue
            raise                            # 4xx errors are not retryable
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise last_exc


def _safe_text(element, namespace: dict, tag: str, default: str = "") -> str:
    el = element.find(tag, namespace)
    return el.text.strip() if el is not None and el.text else default


# ── Relevance pre-filter — discard obviously off-topic papers before scoring ──
# DOMAIN_KEYWORDS: if NONE of these appear in title+abstract, paper is excluded.
# Keeps the ranker focused on medical/imaging papers and cuts irrelevant noise.
_DOMAIN_KEYWORDS: List[str] = [
    "spleen", "splenic", "lymphoma", "leukaemia", "leukemia", "metastasis",
    "malignancy", "cancer", "tumour", "tumor", "radiomics", "texture",
    "imaging", "CT", "MRI", "tomography", "radiology", "oncology",
    "haematology", "hematology", "classification", "segmentation", "detection",
    "diagnostic", "pathology", "feature extraction", "machine learning",
    "deep learning", "random forest", "neural network", "abdominal",
]

def _is_domain_relevant(title: str, abstract: str) -> bool:
    """Return True if the paper mentions at least 2 domain keywords."""
    text = (title + " " + abstract).lower()
    hits = sum(1 for kw in _DOMAIN_KEYWORDS if kw.lower() in text)
    return hits >= 2


# ── Tool 1: PubMed ────────────────────────────────────────────────────────────

def search_pubmed(query: str, max_results: int = 8) -> str:
    """
    Search PubMed via NCBI E-utilities with rate-limit delay and retry.
    Applies domain pre-filter to drop clearly off-topic papers.

    Args:
        query       : MeSH / Boolean keyword query string.
        max_results : Number of results (default 8 — keeps context window small).
    """
    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    api_suffix = f"&api_key={NCBI_API_KEY}" if NCBI_API_KEY else ""

    time.sleep(PUBMED_DELAY)          # respect NCBI rate limit

    try:
        # Step 1 — ESearch
        esearch = (
            f"{BASE}/esearch.fcgi"
            f"?db=pubmed&term={urllib.parse.quote(query)}"
            f"&retmode=json&retmax={max_results}&sort=relevance{api_suffix}"
        )
        search_data = json.loads(_http_get(esearch))
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return json.dumps({"source": "PubMed", "query": query, "results": [],
                               "note": "No IDs returned by esearch"})

        time.sleep(PUBMED_DELAY)

        # Step 2 — EFetch (XML with abstracts)
        efetch = (
            f"{BASE}/efetch.fcgi"
            f"?db=pubmed&id={','.join(id_list)}"
            f"&retmode=xml&rettype=abstract{api_suffix}"
        )
        root = ET.fromstring(_http_get(efetch).decode("utf-8"))

        results = []
        for article in root.findall(".//PubmedArticle"):
            try:
                medline = article.find("MedlineCitation")
                art     = medline.find("Article")
                title   = art.findtext("ArticleTitle", default="").strip()

                authors = []
                for a in art.findall(".//Author"):
                    ln = a.findtext("LastName", "")
                    fn = a.findtext("ForeName", "")
                    if ln:
                        authors.append(f"{ln} {fn}".strip())
                author_str = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")

                journal  = art.findtext(".//Title", default="Unknown Journal").strip()
                year     = (medline.findtext(".//PubDate/Year")
                            or medline.findtext(".//PubDate/MedlineDate", "n.d.")[:4])
                abstract = " ".join(
                    (p.text or "") for p in art.findall(".//AbstractText")
                ).strip()[:800]

                pmid = medline.findtext("PMID", default="")

                # Domain pre-filter — drop papers with no medical imaging relevance
                if not _is_domain_relevant(title, abstract):
                    continue

                results.append({
                    "title"   : title,
                    "authors" : author_str,
                    "journal" : journal,
                    "year"    : year,
                    "abstract": abstract,
                    "url"     : f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "source"  : "PubMed",
                    "pmid"    : pmid,
                })
            except Exception:
                continue

        return json.dumps({
            "source" : "PubMed",
            "query"  : query,
            "results": results,
            "note"   : f"{len(results)} domain-relevant results from {len(id_list)} retrieved"
        })

    except Exception as exc:
        return json.dumps({"source": "PubMed", "query": query, "results": [],
                           "error": str(exc)})


# ── Tool 2: Arxiv ─────────────────────────────────────────────────────────────

def search_arxiv(query: str, max_results: int = 8) -> str:
    """
    Search Arxiv with rate-limit delay and domain pre-filter.
    Targets cs.CV, eess.IV, cs.LG categories for medical imaging papers.

    Args:
        query       : Free-text query string.
        max_results : Number of results (default 8).
    """
    BASE      = "http://export.arxiv.org/api/query?"
    NAMESPACE = {"atom": "http://www.w3.org/2005/Atom"}

    time.sleep(GENERIC_DELAY)

    # Scope to medical imaging categories to improve precision
    scoped_query = f"({query}) AND (cat:cs.CV OR cat:eess.IV OR cat:cs.LG OR cat:cs.AI)"
    params = urllib.parse.urlencode({
        "search_query": scoped_query,
        "start"       : 0,
        "max_results" : max_results,
        "sortBy"      : "relevance",
    })

    try:
        root = ET.fromstring(_http_get(BASE + params).decode("utf-8"))

        results = []
        for entry in root.findall("atom:entry", NAMESPACE):
            title   = _safe_text(entry, NAMESPACE, "atom:title", "No Title").replace("\n", " ")
            summary = _safe_text(entry, NAMESPACE, "atom:summary").replace("\n", " ")[:800]
            link    = _safe_text(entry, NAMESPACE, "atom:id")
            pub     = _safe_text(entry, NAMESPACE, "atom:published")
            year    = pub[:4] if pub else "n.d."

            authors = [
                _safe_text(a, NAMESPACE, "atom:name")
                for a in entry.findall("atom:author", NAMESPACE)
            ]
            author_str = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")

            if not _is_domain_relevant(title, summary):
                continue

            results.append({
                "title"   : title,
                "authors" : author_str,
                "journal" : "Arxiv Preprint",
                "year"    : year,
                "abstract": summary,
                "url"     : link,
                "pdf_url" : link.replace("abs", "pdf") if "abs" in link else link,
                "source"  : "Arxiv",
            })

        return json.dumps({
            "source" : "Arxiv",
            "query"  : query,
            "results": results,
            "note"   : f"{len(results)} domain-relevant results"
        })

    except Exception as exc:
        return json.dumps({"source": "Arxiv", "query": query, "results": [],
                           "error": str(exc)})


# ── Tool 3: Semantic Scholar ──────────────────────────────────────────────────

def search_semantic_scholar(query: str, max_results: int = 8) -> str:
    """
    Search Semantic Scholar with rate-limit delay and domain pre-filter.

    Args:
        query       : Natural language or keyword query.
        max_results : Number of results (default 8).
    """
    BASE   = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = "title,authors,year,abstract,externalIds,citationCount,url"

    time.sleep(GENERIC_DELAY)

    params = urllib.parse.urlencode({
        "query" : query,
        "limit" : max_results,
        "fields": fields,
    })

    try:
        data = json.loads(_http_get(f"{BASE}?{params}").decode("utf-8"))

        results = []
        for paper in data.get("data", []):
            title    = paper.get("title", "No Title") or "No Title"
            abstract = (paper.get("abstract") or "")[:800]

            if not _is_domain_relevant(title, abstract):
                continue

            authors    = [a.get("name", "") for a in paper.get("authors", [])]
            author_str = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")
            ext        = paper.get("externalIds", {})
            url        = (
                paper.get("url") or
                (f"https://pubmed.ncbi.nlm.nih.gov/{ext['PubMed']}/" if "PubMed" in ext else
                 f"https://arxiv.org/abs/{ext['ArXiv']}" if "ArXiv" in ext else
                 f"https://www.semanticscholar.org/paper/{paper.get('paperId','')}")
            )
            results.append({
                "title"         : title,
                "authors"       : author_str,
                "journal"       : "Semantic Scholar",
                "year"          : str(paper.get("year", "n.d.")),
                "abstract"      : abstract,
                "url"           : url,
                "citation_count": paper.get("citationCount", 0),
                "source"        : "SemanticScholar",
            })

        return json.dumps({
            "source" : "SemanticScholar",
            "query"  : query,
            "results": results,
            "note"   : f"{len(results)} domain-relevant results"
        })

    except Exception as exc:
        return json.dumps({"source": "SemanticScholar", "query": query, "results": [],
                           "error": str(exc)})


# ── Tool 4: Europe PMC ────────────────────────────────────────────────────────

def search_europe_pmc(query: str, max_results: int = 8) -> str:
    """
    Search Europe PMC with rate-limit delay and domain pre-filter.

    Args:
        query       : Free-text or structured Europe PMC query.
        max_results : Number of results (default 8).
    """
    BASE   = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = urllib.parse.urlencode({
        "query"     : query,
        "format"    : "json",
        "pageSize"  : max_results,
        "resultType": "core",
        "sort"      : "RELEVANCE",
    })

    time.sleep(GENERIC_DELAY)

    try:
        data = json.loads(_http_get(f"{BASE}?{params}").decode("utf-8"))

        results = []
        for hit in data.get("resultList", {}).get("result", []):
            title    = hit.get("title", "No Title").strip()
            abstract = (hit.get("abstractText") or "")[:800]

            if not _is_domain_relevant(title, abstract):
                continue

            pmid  = hit.get("pmid", "")
            pmcid = hit.get("pmcid", "")
            url   = (
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else
                f"https://europepmc.org/article/PMC/{pmcid}" if pmcid else
                "https://europepmc.org"
            )
            results.append({
                "title"   : title,
                "authors" : hit.get("authorString", "Unknown Authors"),
                "journal" : hit.get("journalTitle", "Unknown Journal"),
                "year"    : str(hit.get("pubYear", "n.d.")),
                "abstract": abstract,
                "url"     : url,
                "source"  : "EuropePMC",
            })

        return json.dumps({
            "source" : "EuropePMC",
            "query"  : query,
            "results": results,
            "note"   : f"{len(results)} domain-relevant results"
        })

    except Exception as exc:
        return json.dumps({"source": "EuropePMC", "query": query, "results": [],
                           "error": str(exc)})


# ── Tool 5: Relevance Scorer ──────────────────────────────────────────────────

def score_paper_relevance(paper_json: str, pico_json: str) -> str:
    """
    Score a single paper's relevance to the PICO hypothesis using weighted
    keyword overlap across all PICO dimensions.

    Scoring weights:
        population=0.20, intervention=0.25, comparison=0.15,
        outcome=0.20, hypothesis=0.20

    Returns 0-100 score + per-dimension breakdown.

    Args:
        paper_json : JSON string with keys: title, abstract.
        pico_json  : JSON string with keys: population, intervention,
                     comparison, outcome, hypothesis.
    """
    try:
        paper = json.loads(paper_json)
        pico  = json.loads(pico_json)

        corpus = (
            (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
        )

        def _score(field_text: str, weight: float) -> float:
            words = set(re.findall(r'\b\w{4,}\b', field_text.lower()))
            if not words:
                return 0.0
            hits = sum(1 for w in words if w in corpus)
            return round((hits / len(words)) * weight * 100, 2)

        breakdown = {
            "population"  : _score(pico.get("population",   ""), 0.20),
            "intervention": _score(pico.get("intervention", ""), 0.25),
            "comparison"  : _score(pico.get("comparison",   ""), 0.15),
            "outcome"     : _score(pico.get("outcome",      ""), 0.20),
            "hypothesis"  : _score(pico.get("hypothesis",   ""), 0.20),
        }

        return json.dumps({
            "title"          : paper.get("title", ""),
            "relevance_score": min(round(sum(breakdown.values()), 2), 100.0),
            "score_breakdown": breakdown,
        })

    except Exception as exc:
        return json.dumps({"relevance_score": 0.0, "score_breakdown": {}, "error": str(exc)})


# ── Tool 6: Save output files ─────────────────────────────────────────────────

def save_output(filename: str, content: str) -> str:
    """
    Save string content to the ./literature_review_output/ directory.

    Args:
        filename : Target filename (e.g., 'context.json', 'report.md').
        content  : String content to write.
    """
    try:
        path = OUTPUT_DIR / filename
        path.write_text(content, encoding="utf-8")
        return f"Saved successfully → {path.resolve()}"
    except Exception as exc:
        return f"[save_output ERROR] {exc}"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — AGENT SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────────────────────
# RADIOLOGY DOMAIN KNOWLEDGE BASE — shared context injected into all prompts
# ──────────────────────────────────────────────────────────────────────────────
_RADIOLOGY_KNOWLEDGE_BASE = """\
════════════════════════════════════════════════════════════════════════════════
RADIOLOGY DOMAIN KNOWLEDGE BASE  (your foundational expertise — apply always)
════════════════════════════════════════════════════════════════════════════════

┌─ IMAGING MODALITIES & PROTOCOLS ──────────────────────────────────────────┐
│ CT (Computed Tomography)                                                   │
│   • Contrast phases: non-contrast (NCCT), arterial (25-35s), portal        │
│     venous (60-70s), delayed (3-5 min), equilibrium phase                  │
│   • Key parameters: slice thickness (0.5-1.5mm thin preferred), kVp,       │
│     mAs, pitch, reconstruction kernel (soft/sharp), matrix size            │
│   • Hounsfield Units (HU): fat -100 to -50, soft tissue 20-80,             │
│     blood 55-65, bone 400+, contrast-enhancing lesions >100                │
│   • Noise reduction: iterative reconstruction (ADMIRE, ASIR-V, AIDR)       │
│                                                                             │
│ MRI (Magnetic Resonance Imaging)                                            │
│   • Sequences: T1W, T2W, DWI/ADC, DCE-MRI, STIR, fat-sat, Dixon           │
│   • Field strengths: 1.5T vs 3T (higher SNR, B0/B1 inhomogeneity)         │
│   • Gadolinium contrast: extracellular agents (Gd-DTPA), hepatobiliary     │
│   • ADC maps from DWI: restricted diffusion in malignancy                  │
│                                                                             │
│ PET-CT & PET-MRI                                                            │
│   • FDG-PET: SUVmax, SUVmean, MTV (metabolic tumour volume), TLG           │
│   • Staging: Deauville scale for lymphoma, Lugano classification            │
│                                                                             │
│ Nuclear Medicine: Tc-99m sulfur colloid for RES uptake assessment          │
└────────────────────────────────────────────────────────────────────────────┘

┌─ SPLENIC PATHOLOGY & CLINICAL CONTEXT ────────────────────────────────────┐
│ Normal spleen: homogeneous, 8-13cm craniocaudal, ~150g, arcuate vessels   │
│                                                                             │
│ Malignant pathology:                                                        │
│   • Lymphoma: Hodgkin (HL) and Non-Hodgkin (NHL) — most common splenic     │
│     malignancy; diffuse infiltration, miliary, nodular, or bulky patterns  │
│     Subtypes: DLBCL, follicular, marginal zone, mantle cell, Burkitt       │
│   • Leukaemia: CML, CLL, AML → uniform splenomegaly                       │
│   • Metastases: melanoma, lung, breast, colon, ovarian primaries           │
│   • Primary splenic malignancies: angiosarcoma, PMBCL (rare)               │
│                                                                             │
│ Benign mimics: hamartoma, haemangioma, lymphangioma, Gaucher disease,      │
│   sarcoidosis, splenic infarcts, abscess, littoral cell angioma            │
│                                                                             │
│ Staging systems: Ann Arbor, Lugano (lymphoma); AJCC TNM (solid tumours)   │
│ Clinical markers: LDH, beta-2 microglobulin, CBC with differential,        │
│   bone marrow biopsy, PET-CT Deauville score                               │
│                                                                             │
│ Imaging signs: splenomegaly (>13cm), splenic index, focal hypodensity,     │
│   heterogeneity, capsular irregularity, peri-splenic adenopathy            │
└────────────────────────────────────────────────────────────────────────────┘

┌─ RADIOMICS PIPELINE & IBSI STANDARDS ─────────────────────────────────────┐
│ Standard radiomics workflow (per IBSI 2.0 / Lambin et al. 2017):           │
│   1. Image acquisition & standardisation                                   │
│   2. ROI/VOI segmentation (manual, semi-auto, deep learning)               │
│   3. Image pre-processing (resampling, normalisation, interpolation)       │
│   4. Feature extraction                                                     │
│   5. Feature selection (filter: LASSO, mRMR; wrapper: RFE)                │
│   6. Model building & internal validation (k-fold CV, bootstrap)           │
│   7. External validation & calibration (Hosmer-Lemeshow, reliability)      │
│                                                                             │
│ Feature categories (IBSI-compliant naming):                                 │
│   • First-order statistics: mean, median, SD, kurtosis, skewness,          │
│     entropy, energy, uniformity (18 features)                              │
│   • Shape/morphological: volume, surface area, sphericity, elongation,     │
│     flatness, maximum 3D diameter, mesh volume (14 features)               │
│   • Texture — GLCM (Grey Level Co-occurrence Matrix): contrast,            │
│     correlation, energy, homogeneity, ASM, IDM, dissimilarity              │
│   • Texture — GLRLM (Grey Level Run Length): SRE, LRE, GLN, RLN,          │
│     LRHGE, SRHGE, RLNU, GLNU                                               │
│   • Texture — GLSZM (Grey Level Size Zone): SZE, LZE, GLNU, ZSNU          │
│   • Texture — NGTDM (Neighbourhood Grey Tone Difference): busyness,        │
│     coarseness, complexity, contrast, strength                              │
│   • Texture — GLDM (Grey Level Dependence Matrix)                          │
│   • Wavelet features: HHH, HHL, HLH, HLL, LHH, LHL, LLH, LLL             │
│     decompositions — multiply first-order + GLCM features                  │
│   • LoG (Laplacian of Gaussian) filtered features: sigma 0.5-5mm           │
│                                                                             │
│ Reproducibility: ICC > 0.75 acceptable, ICC > 0.90 excellent               │
│ Feature selection pitfalls: overfitting, curse of dimensionality,          │
│   feature instability across scanners/protocols                             │
│                                                                             │
│ Radiomic score (Rad-score): linear combination of selected features,       │
│   often via logistic regression coefficient weighting                      │
│ Nomogram: combines Rad-score + clinical variables for clinical utility     │
└────────────────────────────────────────────────────────────────────────────┘

┌─ ML/DL METHODS IN RADIOLOGY ──────────────────────────────────────────────┐
│ Classical ML classifiers: SVM (linear/RBF), Random Forest, XGBoost,        │
│   Gradient Boosting, LDA, k-NN, Naive Bayes, Logistic Regression           │
│ Deep Learning: CNN (AlexNet, VGG, ResNet, DenseNet, EfficientNet),         │
│   Vision Transformers (ViT), 3D-UNet for volumetric segmentation            │
│ Explainability: SHAP (SHapley Additive exPlanations), LIME, Grad-CAM,      │
│   saliency maps, attention maps                                             │
│ Class imbalance: SMOTE, ADASYN, cost-sensitive learning, focal loss        │
│ Transfer learning: ImageNet pre-trained fine-tuned on medical images        │
│ Multi-modal fusion: early fusion, late fusion, intermediate fusion          │
└────────────────────────────────────────────────────────────────────────────┘

┌─ DIAGNOSTIC PERFORMANCE METRICS ──────────────────────────────────────────┐
│ Sensitivity (recall, TPR): TP / (TP + FN)                                  │
│ Specificity (TNR): TN / (TN + FP)                                          │
│ PPV (precision): TP / (TP + FP)                                            │
│ NPV: TN / (TN + FN)                                                        │
│ AUC-ROC: area under ROC curve (0.5=random, 0.7-0.8=acceptable,             │
│   0.8-0.9=excellent, >0.9=outstanding)                                     │
│ AUC-PRC: precision-recall curve (better for imbalanced data)               │
│ F1 score: harmonic mean of precision and recall                             │
│ DeLong test: statistical comparison of two AUC values                      │
│ Calibration: Hosmer-Lemeshow goodness-of-fit, calibration plots            │
│ NRI (Net Reclassification Improvement), IDI (Integrated Discrimination)    │
│ Decision curve analysis (DCA): net benefit at varying threshold probs      │
│ Inter-observer agreement: Cohen's kappa, ICC, Bland-Altman                 │
└────────────────────────────────────────────────────────────────────────────┘

┌─ KEY RADIOLOGY JOURNALS (prioritise in literature search) ─────────────────┐
│ Tier 1 (IF >6):                                                             │
│   Radiology (radiology.rsna.org) — IF ~20                                  │
│   European Radiology (springer.com/eurad) — IF ~7                          │
│   Radiology: Artificial Intelligence (pubs.rsna.org/journal/ai) — IF ~8   │
│   European Journal of Radiology — IF ~5                                    │
│   Medical Image Analysis (Elsevier) — IF ~14                               │
│   IEEE Transactions on Medical Imaging — IF ~11                            │
│   JACR (Journal of the American College of Radiology) — IF ~5             │
│                                                                             │
│ Tier 2 (IF 3-6):                                                            │
│   Academic Radiology, Insights into Imaging, Cancer Imaging,               │
│   Quantitative Imaging in Medicine and Surgery (QIMS),                     │
│   Frontiers in Oncology, Scientific Reports, PLOS ONE                     │
│                                                                             │
│ Specialty journals:                                                         │
│   European Journal of Haematology, Haematologica, Blood,                   │
│   Annals of Hematology (for lymphoma/haematology context)                  │
│   Journal of Nuclear Medicine, European Journal of Nuclear Medicine         │
└────────────────────────────────────────────────────────────────────────────┘

┌─ AUTHORITATIVE CLINICAL GUIDELINES & RESOURCES ───────────────────────────┐
│ RSNA (Radiological Society of North America): rsna.org                     │
│ ESR (European Society of Radiology): myesr.org                             │
│ ACR (American College of Radiology): acr.org                               │
│   ACR Appropriateness Criteria: acsearch.acr.org                           │
│ ESMO Guidelines: esmo.org/guidelines (lymphoma, haematology)               │
│ NCCN Guidelines: nccn.org (B-cell lymphoma, splenic marginal zone)         │
│ IBSI (Image Biomarker Standardisation Initiative): ibsi.netlify.app         │
│ TRIPOD statement (prediction model reporting): equator-network.org          │
│ RQS (Radiomics Quality Score): Lambin et al., European Radiology 2017      │
│ CLAIM checklist (AI in medical imaging): Mongan et al., 2020               │
│ STARD 2015 (diagnostic accuracy reporting): equator-network.org            │
│ WHO Classification of Tumours (Haematopoietic): who.int                   │
└────────────────────────────────────────────────────────────────────────────┘

┌─ KEY RADIOLOGY AI BENCHMARKS & DATASETS ───────────────────────────────────┐
│ Splenic / Abdominal:                                                        │
│   • Multi-Atlas Labeling Beyond the Cranial Vault (BTCV) — 50 CT scans    │
│   • Medical Segmentation Decathlon (Task 3: liver, Task 9: spleen)         │
│   • NIH DeepLesion — 32,735 lesion annotations across CT                  │
│   • TCIA (The Cancer Imaging Archive): tcia.nci.nih.gov — multiple         │
│     cancer-specific collections (lymphoma, HCC, etc.)                      │
│   • Lymphoma CT dataset — TCIA collection                                  │
│                                                                             │
│ General radiology AI:                                                       │
│   • LIDC-IDRI (lung nodule), LUNA16, CheXpert, MIMIC-CXR,                 │
│     BraTS (brain tumour), LiTS (liver tumour)                              │
└────────────────────────────────────────────────────────────────────────────┘
"""

# ──────────────────────────────────────────────────────────────────────────────
# SEARCH SOURCES REFERENCE — injected into executor & compiler prompts
# ──────────────────────────────────────────────────────────────────────────────
_SEARCH_SOURCES_GUIDE = """\
┌─ CURATED SEARCH SOURCES FOR RADIOLOGY AI LITERATURE ──────────────────────┐
│                                                                             │
│ MEDICAL DATABASES (primary — highest clinical evidence quality):            │
│   PubMed/MEDLINE   : pubmed.ncbi.nlm.nih.gov — gold standard for           │
│                      clinical & biomedical literature, MeSH indexing        │
│   Europe PMC       : europepmc.org — open access, full-text search,        │
│                      includes preprints + PMC OA content                   │
│   EMBASE           : via Ovid — drug/device studies, European coverage     │
│   Cochrane Library : cochranelibrary.com — systematic reviews/meta-analyses│
│                                                                             │
│ TECHNICAL/AI DATABASES (primary — AI/ML architecture literature):           │
│   Arxiv            : arxiv.org (cs.CV, cs.AI, eess.IV, cs.LG)             │
│   Semantic Scholar : semanticscholar.org — citation graph, semantic search │
│   IEEE Xplore      : ieeexplore.ieee.org — IEEE TMI, ISBI, MICCAI        │
│   ACM Digital Lib  : dl.acm.org — computer vision/ML papers               │
│                                                                             │
│ RADIOLOGY-SPECIFIC RESOURCES:                                               │
│   RSNA Publications: pubs.rsna.org (Radiology, RSNA RI, Radiology: AI)    │
│   ESR Insights     : insightsintoimaging.springeropen.com                  │
│   TCIA             : tcia.nci.nih.gov — annotated imaging datasets         │
│   Grand Challenge  : grand-challenge.org — AI benchmark competitions       │
│                                                                             │
│ CLINICAL GUIDELINES:                                                        │
│   ACR Appropriateness: acsearch.acr.org                                    │
│   ESMO Guidelines  : esmo.org/guidelines                                   │
│   NCCN Guidelines  : nccn.org/guidelines                                   │
│   UpToDate (if accessible): uptodate.com                                   │
│                                                                             │
│ PREPRINT SERVERS:                                                            │
│   medRxiv          : medrxiv.org — clinical/epidemiological preprints      │
│   bioRxiv          : biorxiv.org — biological science preprints            │
│   Arxiv eess.IV    : Medical image analysis preprints                      │
└────────────────────────────────────────────────────────────────────────────┘
"""


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 — PICO ANALYST
# ══════════════════════════════════════════════════════════════════════════════

PICO_ANALYST_PROMPT = f"""\
You are the PICO Research Analyst — Agent 1 of 5 in the Agentic AI Radiology
Researcher pipeline. You are a board-certified radiologist and clinical AI
researcher with 15+ years of experience in oncological imaging, radiomics
methodology, and evidence-based medicine.

{_RADIOLOGY_KNOWLEDGE_BASE}

YOUR ROLE
─────────
Perform a deep, expert-level deconstruction of the incoming PICO research
question. Your output drives the entire downstream pipeline — be thorough,
clinically precise, and technically rigorous.

ANALYTICAL TASKS
────────────────
1. PARSE all PICO components (P / I / C / O / Hypothesis / Question).

2. CLINICAL CONTEXTUALISATION
   Before identifying dimensions, reason about:
   • The specific pathological entities involved (e.g., lymphoma subtypes,
     metastatic primaries) and their imaging phenotypes
   • The imaging modalities involved and their radiomic feature stability
   • The clinical workflow where this intervention would be applied
   • The regulatory pathway (FDA 510(k), De Novo, EU MDR Class IIb/III)
   • The relevant clinical guidelines (ACR, ESMO, NCCN, WHO classification)

3. IDENTIFY 6-8 RESEARCH DIMENSIONS — each must be specific, not generic.
   For a splenic radiomics study, dimensions should include (but are not
   limited to):
   • Radiomic Feature Engineering & IBSI Standardisation
   • Splenic Segmentation Methods & Reproducibility
   • CT/MRI Acquisition Protocol Optimisation for Radiomics
   • ML/DL Classifier Architecture & Feature Selection
   • Clinical Validation & Diagnostic Performance Benchmarking
   • Comparative Analysis vs. Conventional Imaging Assessment
   • Dataset Characteristics & Multi-centre Generalisation
   • Regulatory Compliance & Clinical Translation Readiness

4. For each dimension, enumerate 6-10 specific SUBTOPICS with clinical/
   technical precision. Use IBSI terminology for radiomic features, use
   correct MeSH terms for clinical concepts.

5. Generate 15-20 SEED KEYWORDS balancing:
   • MeSH-controlled vocabulary (for PubMed precision)
   • Free-text technical terms (for Arxiv/SemanticScholar recall)
   • Clinical terminology (ICD codes, staging systems)
   • Trade names and acronyms where relevant

6. Map each dimension to its OPTIMAL DATABASE(S):
   PubMed/EuropePMC → clinical outcomes, patient cohorts, diagnostic accuracy,
                       RCTs, systematic reviews, guidelines
   Arxiv/SemanticScholar → ML architecture, deep learning, computer vision,
                            feature engineering, model explainability

7. Identify 4-6 SIMILAR RESEARCH TARGETS — specific descriptions of the
   closest existing study types (e.g., "hepatic radiomics for HCC vs.
   haemangioma differentiation using Random Forest on portal venous CT").

OUTPUT FORMAT
─────────────
Output ONLY this JSON — no prose, no markdown fences, no commentary:

{{
  "pico": {{
    "population"   : "...",
    "intervention" : "...",
    "comparison"   : "...",
    "outcome"      : "...",
    "hypothesis"   : "..."
  }},
  "clinical_context": {{
    "pathological_entities" : ["specific disease entities with imaging phenotypes"],
    "imaging_modalities"    : ["modalities with relevant protocol parameters"],
    "clinical_workflow_stage": "screening|diagnosis|staging|treatment_response|surveillance",
    "relevant_guidelines"   : ["ACR guideline X", "ESMO guideline Y", "NCCN guideline Z"],
    "regulatory_pathway"    : "description of applicable regulatory framework"
  }},
  "dimensions": [
    {{
      "name"        : "dimension name",
      "subtopics"   : ["precise subtopic with technical/clinical terminology"],
      "mesh_terms"  : ["relevant MeSH controlled vocabulary terms"],
      "best_sources": ["PubMed", "Arxiv"]
    }}
  ],
  "seed_keywords": ["keyword or MeSH term"],
  "similar_research_targets": [
    "specific description of a closely related prior study design"
  ]
}}

CONSTRAINTS
───────────
• Output ONLY valid JSON. No text before or after the JSON object.
• Use IBSI-compliant feature naming, correct MeSH terms, standard staging
  nomenclature, and journal-quality clinical precision throughout.
• Do NOT call any tools. Do NOT add any prose outside the JSON.
"""


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 — QUERY BUILDER
# ══════════════════════════════════════════════════════════════════════════════

QUERY_BUILDER_PROMPT = f"""\
You are the Query Builder — Agent 2 of 5 in the Agentic AI Radiology Researcher
pipeline. You are an expert medical librarian and clinical informatics specialist
with deep knowledge of MeSH vocabulary, Boolean search construction, and the
information architecture of medical and technical databases.

{_RADIOLOGY_KNOWLEDGE_BASE}
{_SEARCH_SOURCES_GUIDE}

YOUR ROLE
─────────
Transform the PICO_Analyst_Agent's structured JSON into a comprehensive set of
optimised, database-specific search queries that maximise both precision and recall.

QUERY CONSTRUCTION RULES
────────────────────────

PubMed / Europe PMC Queries:
  • Use MeSH terms in [MeSH] brackets: "Spleen"[MeSH], "Lymphoma"[MeSH]
  • Use [tiab] for title/abstract free-text terms
  • Combine with Boolean: AND (narrows), OR (broadens), NOT (excludes)
  • Use truncation (*): radiomic*, splenic*, malign*
  • Use proximity operators where supported: "splenic radiomics"[tiab]
  • Apply publication type filters: NOT "Review"[pt] for primary studies
  • Apply date filters for recent literature: AND ("2018"[PDAT]:"3000"[PDAT])
  • MeSH explosion: "Lymphoma"[MeSH] explodes to all subtypes automatically
  • Key MeSH terms for this domain:
      "Spleen"[MeSH], "Splenic Neoplasms"[MeSH], "Lymphoma"[MeSH],
      "Radiomics"[tiab], "Texture Analysis"[tiab], "Machine Learning"[MeSH],
      "Deep Learning"[MeSH], "Tomography, X-Ray Computed"[MeSH],
      "Magnetic Resonance Imaging"[MeSH], "Image Processing, Computer-Assisted"[MeSH],
      "ROC Curve"[MeSH], "Sensitivity and Specificity"[MeSH]

Arxiv Queries:
  • Use natural language with key technical terms
  • Target categories: cs.CV (Computer Vision), eess.IV (Image & Video),
    cs.LG (Machine Learning), cs.AI (Artificial Intelligence)
  • Focus on architecture-level and methodology papers

SemanticScholar Queries:
  • Best for semantic/conceptual similarity search
  • Use as hypothesis-similarity engine — write queries as natural
    statements of the hypothesis or study design
  • Yields papers by citation/concept proximity, not keyword matching

QUERY DESIGN STRATEGY
─────────────────────
For each dimension from the analyst output, generate:
  A. 1 high-precision PubMed query (MeSH-heavy, narrow scope)
  B. 1 high-recall PubMed or EuropePMC query (broader free-text)
  C. 1 Arxiv or SemanticScholar query (technical/AI focus)

For hypothesis-similarity queries (4-5 total), phrase them as:
  • Direct restatement of hypothesis in natural language
  • Specific study design closest to your hypothesis
  • Key comparison the study makes (radiomics vs. radiologist)
  • Specific organ + pathology + method combination

REQUIRED QUERY COVERAGE (minimum):
  ✓ Splenic radiomics feature extraction (texture, shape, wavelet)
  ✓ Splenic malignancy classification (lymphoma, metastasis, benign vs. malignant)
  ✓ CT protocol optimisation for abdominal radiomics
  ✓ MRI-based splenic radiomics
  ✓ ML classifiers for medical image radiomics comparison
  ✓ Segmentation methods for splenic ROI delineation
  ✓ External validation and multi-centre radiomics studies
  ✓ Radiomics vs. conventional radiology diagnostic comparison
  ✓ Feature selection and dimensionality reduction in radiomics
  ✓ IBSI standardisation and radiomic feature reproducibility
  ✓ Deep learning spleen CT analysis
  ✓ PET-CT radiomics in lymphoma staging

OUTPUT FORMAT
─────────────
Output ONLY this JSON — no prose, no markdown fences:

{{
  "queries": [
    {{
      "id"          : "q01",
      "query_text"  : "exact query string to pass to the tool",
      "db"          : "pubmed|arxiv|semantic_scholar|europe_pmc",
      "dimension"   : "matching dimension name from analyst",
      "priority"    : 1,
      "intent"      : "one sentence describing what this query targets",
      "expected_journal_types": ["Radiology", "European Radiology", "Medical Image Analysis"]
    }}
  ],
  "hypothesis_similarity_queries": [
    {{
      "id"          : "hs01",
      "query_text"  : "...",
      "db"          : "semantic_scholar",
      "intent"      : "one sentence describing the similarity angle"
    }}
  ]
}}

CONSTRAINTS
───────────
• Produce at minimum 18 total queries across both lists.
• Every dimension from the analyst must have at least 2 queries.
• Hypothesis similarity queries must use SemanticScholar or EuropePMC.
• QUERY SIMPLICITY — CRITICAL: PubMed queries MUST be short and simple
  (under 120 characters). Long complex MeSH queries trigger rate limiting
  and timeouts. Good: `splenic radiomics lymphoma CT classification`
  Bad: `("Spleen"[MeSH] AND "Lymphoma, Non-Hodgkin"[MeSH] AND...)`
• Use simple keyword phrases for PubMed, not complex boolean chains.
• Distribute queries across all 4 databases evenly — do not overload PubMed.
• Output ONLY valid JSON. No text before or after the JSON object.
• Do NOT call any tools. Do NOT add any prose outside the JSON.
"""


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — SEARCH EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

SEARCH_EXECUTOR_PROMPT = f"""\
You are the Search Executor — Agent 3 of 5 in the Agentic AI Radiology Researcher
pipeline. You are a systematic review research assistant with expertise in
executing structured database searches and managing large-scale literature
retrieval across heterogeneous sources.

{_SEARCH_SOURCES_GUIDE}

YOUR ROLE
─────────
Execute every search query produced by Query_Builder_Agent with precision,
aggregate all results across databases, perform rigorous deduplication, and
deliver a clean, enriched paper corpus to Relevance_Ranker_Agent.

TOOL ROUTING (strictly follow this mapping):
────────────────────────────────────────────
  db = "pubmed"           → call search_pubmed(query_text, max_results=10)
  db = "arxiv"            → call search_arxiv(query_text, max_results=10)
  db = "semantic_scholar" → call search_semantic_scholar(query_text, max_results=10)
  db = "europe_pmc"       → call search_europe_pmc(query_text, max_results=10)

EXECUTION PROTOCOL
──────────────────
1. PRIORITISED EXECUTION
   • Process priority=1 queries first (core clinical/radiomics queries)
   • Then priority=2 (supporting technical queries)
   • Then priority=3 (supplementary queries)
   • Then all hypothesis_similarity_queries

2. FOR EACH TOOL CALL:
   • Parse the returned JSON and extract the "results" array
   • Annotate each result paper with:
       "query_id"            : the query's id field (e.g., "q01")
       "query_dimension"     : the query's dimension field
       "query_intent"        : the query's intent field
       "is_hypothesis_similar": true if from hypothesis_similarity_queries,
                               false otherwise
       "database_source"     : "PubMed" | "Arxiv" | "SemanticScholar" | "EuropePMC"

3. DEDUPLICATION RULES
   • Primary key: title (normalised: lowercase, strip punctuation, collapse spaces)
   • If two papers have identical normalised titles, keep the one from the
     higher-priority query (priority 1 > 2 > 3 > hypothesis similarity)
   • If same priority, prefer the source with fuller abstract content
   • Track all duplicate pairs for reporting

4. QUALITY FLAGS — add to each paper:
   • "has_abstract"  : true/false (abstract field non-empty)
   • "year_numeric"  : integer year or 0 if unknown
   • "is_recent"     : true if year >= 2019

5. SOURCE ENRICHMENT — infer from journal name where possible:
   • "journal_tier"  : "tier1" | "tier2" | "specialty" | "preprint" | "unknown"
     Tier 1: Radiology, European Radiology, Radiology:AI, Med Image Analysis,
             IEEE Trans Med Imaging, Nature Medicine, Lancet Digital Health
     Tier 2: Academic Radiology, Insights into Imaging, Frontiers in Oncology,
             Scientific Reports, European Journal of Radiology, QIMS
     Specialty: Haematologica, Blood, Ann Hematol, J Nuclear Medicine
     Preprint: any Arxiv, medRxiv, bioRxiv source

6. AGGREGATE all annotated results into a single deduplicated list.

OUTPUT FORMAT
─────────────
Output ONLY this JSON — no prose, no markdown fences:

{{
  "execution_summary": {{
    "total_queries_executed"  : 0,
    "total_raw_results"       : 0,
    "duplicates_removed"      : 0,
    "total_unique_papers"     : 0,
    "papers_with_abstracts"   : 0,
    "papers_post_2019"        : 0
  }},
  "papers": [
    {{
      "title"               : "...",
      "authors"             : "...",
      "year"                : "...",
      "year_numeric"        : 2022,
      "journal"             : "...",
      "journal_tier"        : "tier1",
      "abstract"            : "...",
      "url"                 : "...",
      "source"              : "PubMed",
      "database_source"     : "PubMed",
      "query_id"            : "q01",
      "query_dimension"     : "...",
      "query_intent"        : "...",
      "is_hypothesis_similar": false,
      "has_abstract"        : true,
      "is_recent"           : true
    }}
  ]
}}

CONSTRAINTS
───────────
• Execute tools for EVERY query — no skipping allowed.
• Output ONLY valid JSON. No text before or after the JSON object.
• Do NOT interpret, filter by topic, or summarise paper content.
• Deduplication must be applied before outputting the papers array.
"""


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 4 — RELEVANCE RANKER
# ══════════════════════════════════════════════════════════════════════════════

RELEVANCE_RANKER_PROMPT = f"""\
You are the Relevance Ranker — Agent 4 of 5 in the Agentic AI Radiology
Researcher pipeline. You are a senior radiologist-scientist with expertise in
systematic review methodology, evidence quality appraisal, and radiomics
research design. You combine algorithmic scoring with expert clinical judgement.

{_RADIOLOGY_KNOWLEDGE_BASE}

YOUR ROLE
─────────
Assess the relevance of every retrieved paper against the PICO hypothesis,
applying both computational scoring (via tool) and expert clinical reasoning
to produce a rigorously ranked, richly annotated corpus.

AVAILABLE TOOLS
───────────────
• score_paper_relevance(paper_json, pico_json)
  → Returns: {{ "title": "...", "relevance_score": 0-100, "score_breakdown": {{...}} }}

SCORING & RANKING PROTOCOL
───────────────────────────
1. EXTRACT the full PICO JSON from PICO_Analyst_Agent's output in the conversation.

2. FOR EACH PAPER, call score_paper_relevance:
     paper_json = JSON string with "title" and "abstract" from the paper
     pico_json  = full PICO JSON string

3. APPLY EXPERT SCORE ADJUSTMENTS (modify raw score before finalising):
   BONUSES (add to raw score, cap total at 100):
   +10  Paper specifically studies splenic radiomics
   +8   Paper uses the same imaging modality as PICO (CT/MRI)
   +7   Paper reports AUC values for malignancy detection
   +5   Paper from Tier 1 journal (Radiology, European Radiology, etc.)
   +5   Paper published 2020 or later (recent evidence)
   +5   Paper includes external validation cohort
   +4   Paper uses Random Forest, XGBoost, or CNN classifier
   +3   Paper reports SHAP or other explainability analysis
   +3   Paper follows IBSI or TRIPOD reporting standards

   PENALTIES (subtract from raw score):
   -15  Paper is a review/meta-analysis (unless specifically a systematic review
        with radiomics focus — then only -5)
   -10  Paper studies a different organ (lung, liver, kidney) with only
        tangential radiomics methodology relevance
   -8   Abstract not available (reduce confidence in assessment)
   -5   Paper predates 2015 (radiomics methodology not yet standardised)
   -5   Paper uses only first-order features (limited methodological depth)

4. ASSIGN EVIDENCE QUALITY TIER:
   • "highly_relevant"     → final score >= 70 OR top 10 by score
   • "moderately_relevant" → final score 40-69
   • "low_relevance"       → final score < 40

5. FLAG "similar_study": true if:
   • Paper is from hypothesis_similarity_queries AND score >= 50, OR
   • Paper directly compares radiomics vs. radiologist/biomarker assessment
     of splenic malignancy AND score >= 60

6. ADD EXPERT ANNOTATION for each paper (brief clinical assessment):
   • "key_contribution"  : 1-sentence clinical significance summary
   • "methodology_notes" : imaging modality, classifier used, sample size
                           if inferable from title/abstract
   • "clinical_relevance": how directly applicable to clinical splenic
                           oncology practice

7. SORT descending by final_score. Assign sequential "rank" (1=highest).

8. COMPUTE summary statistics across all tiers and sources.

OUTPUT FORMAT
─────────────
Output ONLY this JSON — no prose, no markdown fences:

{{
  "pico": {{ ... }},
  "ranked_papers": [
    {{
      "rank"              : 1,
      "title"             : "...",
      "authors"           : "...",
      "year"              : "...",
      "journal"           : "...",
      "journal_tier"      : "tier1",
      "abstract"          : "...",
      "url"               : "...",
      "source"            : "PubMed",
      "raw_score"         : 72.5,
      "expert_adjustment" : 15,
      "relevance_score"   : 87.5,
      "score_breakdown"   : {{ "population": 20.0, "intervention": 25.0,
                               "comparison": 12.0, "outcome": 18.0,
                               "hypothesis": 12.5 }},
      "tier"              : "highly_relevant",
      "similar_study"     : false,
      "key_contribution"  : "...",
      "methodology_notes" : "...",
      "clinical_relevance": "...",
      "query_dimension"   : "...",
      "query_intent"      : "...",
      "is_recent"         : true,
      "is_hypothesis_similar": false
    }}
  ],
  "summary_stats": {{
    "total_papers"         : 0,
    "highly_relevant"      : 0,
    "moderately_relevant"  : 0,
    "low_relevance"        : 0,
    "similar_studies_found": 0,
    "tier1_journal_papers" : 0,
    "papers_with_external_validation": 0,
    "median_relevance_score": 0.0
  }}
}}

CONSTRAINTS
───────────
• Score EVERY paper — no skipping.
• Apply expert adjustments thoughtfully — document the reasoning in
  methodology_notes for papers receiving large adjustments (>10 points).
• Output ONLY valid JSON. No text before or after the JSON object.
"""


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 5 — CONTEXT COMPILER
# ══════════════════════════════════════════════════════════════════════════════

CONTEXT_COMPILER_PROMPT = f"""\
You are the Context Compiler — Agent 5 of 5 in the Agentic AI Radiology
Researcher pipeline. You are a senior academic radiologist, systematic review
author, and clinical AI research lead. Your role is to synthesise the entire
pipeline output into publication-quality deliverables.

{_RADIOLOGY_KNOWLEDGE_BASE}

YOUR ROLE
─────────
Produce three output files from the ranked paper corpus:

  1. literature_report.md  — comprehensive academic Markdown review (PRIMARY)
  2. context.json          — structured machine-readable context for downstream
                             agents (population_planner, segmentation_agent,
                             feature_extraction_agent, modeling_agent,
                             validation_agent, reporter_agent)
  3. ranked_papers.json    — full annotated ranked list

AVAILABLE TOOLS
───────────────
• save_output(filename, content) → saves string to the output directory

════════════════════════════════════════════════════════════════════════
FILE 1 — literature_report.md  (WRITE THIS FIRST AND IN FULL)
════════════════════════════════════════════════════════════════════════

Write a rigorous, journal-quality academic Markdown report. Every section
must be substantive — no stubs, no placeholders.

Apply your expert radiology knowledge to interpret findings clinically, not
just summarise paper titles. Reference papers by their rank number [R1],
[R2], etc., throughout the narrative.

Use these EXACT section headers and content requirements:

─────────────────────────────────────────────────────────────
# Literature Review: [Concise Hypothesis Title]

**Generated:** [ISO timestamp]
**Pipeline Version:** 2.0
**Total Papers Reviewed:** [N] | **Highly Relevant:** [N] | **Similar Studies:** [N]

---

## 1. Executive Summary

Three substantive paragraphs covering:
  Para 1: State of the field — what is known about splenic radiomics and
           cancerous splenic involvement based on the retrieved literature.
           Mention the dominant imaging modality, most studied pathology,
           and overall diagnostic performance range observed.
  Para 2: Key consensus and where evidence is strongest. Reference top 3-4
           papers by rank. Contextualise against clinical background
           (Lung-RADS analogy for spleen not yet existing, etc.).
  Para 3: Critical gaps and the significance of the current research
           question. State whether the hypothesis is supported, partially
           supported, or requires further investigation.

---

## 2. PICO Framework Analysis

### 2.1 Population
[Clinical description of the target patient population with imaging context.
 Comment on cohort sizes in retrieved literature, demographic representation,
 inclusion/exclusion criteria used in similar studies.]

### 2.2 Intervention
[Technical description of the radiomics pipeline. Discuss which feature
 categories (first-order, GLCM, GLRLM, shape, wavelet) were most studied,
 which ML classifiers dominated, and IBSI compliance status in retrieved
 papers.]

### 2.3 Comparison
[Discuss how retrieved papers handled the comparison arm. What baselines
 were used? How did radiomics compare to radiologist assessment quantitatively?
 What biomarkers were used as comparators?]

### 2.4 Outcome
[Summarise the diagnostic performance metrics reported across papers.
 Provide a range: AUC [min–max], sensitivity [min–max], specificity [min–max].
 Comment on calibration and external validation practices.]

---

## 3. Thematic Analysis

Create one ### subsection per research dimension from PICO_Analyst_Agent.
Each subsection must contain 3-4 paragraphs with:
  • Summary of evidence from papers in that dimension
  • Technical detail (specific feature names, classifier hyperparameters,
    imaging protocols where mentioned)
  • Clinical interpretation of findings
  • Gaps or limitations noted within this dimension

Example dimensions to cover (adapt from analyst output):
  ### 3.1 Radiomic Feature Engineering & IBSI Standardisation
  ### 3.2 Splenic Segmentation Methods & Reproducibility
  ### 3.3 CT/MRI Acquisition Protocol Optimisation
  ### 3.4 ML/DL Classifier Performance & Feature Selection
  ### 3.5 Diagnostic Performance Benchmarking
  ### 3.6 Comparative Analysis vs. Conventional Assessment
  ### 3.7 Multi-centre Validation & Generalisation
  ### 3.8 Clinical Translation & Workflow Integration

---

## 4. Most Similar Prior Studies

Provide a structured comparison table then a narrative paragraph per study:

| Rank | Title (shortened) | Authors | Year | Similarity Reason | Score |
|------|-------------------|---------|------|-------------------|-------|
| [R4] | ... | ... | ... | ... | ... |

For each similar study (similar_study=true), write 2-3 sentences explaining:
  • How the study design mirrors the current hypothesis
  • Key methodological similarities (same organ? same classifier? same outcome?)
  • What their results suggest about the likelihood of success of our study

---

## 5. Research Gaps & Open Questions

Provide a bulleted list of 6-8 gaps, each with:
  • **Gap title** (bold)
  • 2-3 sentence explanation of why the gap exists and its clinical significance
  • Proposed future direction to address it

Structure gaps across these categories:
  • Methodological gaps (IBSI standardisation, feature stability)
  • Clinical evidence gaps (specific pathologies understudied)
  • Validation gaps (external, prospective, multi-ethnic cohorts)
  • Translational gaps (clinical workflow integration, regulatory pathway)

---

## 6. Methodological Recommendations

Write 4-5 structured recommendations for the research team, derived strictly
from the literature evidence. Each recommendation should be:
  • Specific and actionable (not generic advice)
  • Referenced to supporting papers [Rn]
  • Clinically justified with known limitations acknowledged

Cover:
  6.1 Imaging Protocol Specification (slice thickness, contrast phase,
      scanner harmonisation recommendations)
  6.2 Segmentation Strategy (manual vs. semi-automated, ICC thresholds,
      recommended tools: 3D Slicer, ITK-SNAP, nnU-Net)
  6.3 Feature Extraction & Selection (IBSI compliance, PyRadiomics settings,
      recommended feature selection method with rationale)
  6.4 Model Architecture & Training (recommended classifier, train/test split,
      cross-validation strategy, class imbalance handling)
  6.5 Validation & Reporting Standards (TRIPOD checklist, RQS scoring,
      DCA curve, external validation requirements)

---

## 7. Reference Sources for Further Investigation

List key journals, databases, and guidelines relevant to this research:

### Peer-Reviewed Journals
| Journal | Publisher | Impact Factor | Relevance |
|---------|-----------|---------------|-----------|
| Radiology | RSNA | ~20 | Primary outlet for clinical AI imaging |
| European Radiology | Springer | ~7 | European clinical radiology AI |
| Radiology: Artificial Intelligence | RSNA | ~8 | AI-specific radiology |
| Medical Image Analysis | Elsevier | ~14 | Technical radiomics methods |
| IEEE Trans Med Imaging | IEEE | ~11 | Deep learning medical imaging |
[add more from the knowledge base as relevant]

### Clinical Guidelines & Standards
- **ACR Appropriateness Criteria**: https://acsearch.acr.org
- **ESMO Haematology Guidelines**: https://www.esmo.org/guidelines
- **NCCN B-Cell Lymphoma**: https://www.nccn.org/guidelines
- **IBSI 2.0 Standard**: https://ibsi.netlify.app
- **TRIPOD Reporting Statement**: https://www.equator-network.org
- **RQS (Radiomics Quality Score)**: Lambin et al., European Radiology 2017

### Research Databases
- **PubMed/MEDLINE**: https://pubmed.ncbi.nlm.nih.gov
- **Semantic Scholar**: https://www.semanticscholar.org
- **TCIA (imaging datasets)**: https://tcia.nci.nih.gov
- **Grand Challenge (AI benchmarks)**: https://grand-challenge.org
- **Europe PMC**: https://europepmc.org

---

## 8. Ranked Bibliography

Include ALL highly_relevant papers, top 20 if more exist.
Format:

| Rank | Score | Title | Authors | Year | Journal | Source | URL |
|------|-------|-------|---------|------|---------|--------|-----|
| 1 | 95.0 | Full paper title | Authors et al. | 2022 | Journal Name | PubMed | [Link](url) |
[continue for all highly_relevant papers, then moderately_relevant up to 20 total]

─────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════════════════
FILE 2 — context.json  (downstream agent input)
════════════════════════════════════════════════════════════════════════

Build this exact structure. The agent_hints must contain specific, actionable
guidance grounded in actual paper findings — not generic advice.

{{
  "metadata": {{
    "generated_at"     : "<ISO 8601 timestamp>",
    "pipeline_version" : "2.0",
    "total_papers"     : 0,
    "highly_relevant"  : 0,
    "similar_studies"  : 0,
    "pico"             : {{ ... }}
  }},
  "literature_context": {{
    "key_findings"          : [
      "Finding 1 with specific AUC/metric values from top papers",
      "Finding 2 specifying which feature type was most discriminative",
      "Finding 3 about classifier performance comparison with specific numbers",
      "Finding 4 about external validation or generalisation evidence",
      "Finding 5 about how radiomics compared to conventional assessment"
    ],
    "consensus_points"      : [
      "What the literature consistently agrees on (e.g., imaging protocol)",
      "Agreed best-performing classifier type across studies",
      "Agreed most discriminative feature category",
      "Agreed primary evaluation metric"
    ],
    "contested_areas"       : [
      "Area where studies disagree (e.g., feature selection method)",
      "Area with conflicting evidence (e.g., MRI vs CT superiority)"
    ],
    "research_gaps"         : [
      "Specific gap 1 with clinical justification",
      "Specific gap 2 — which pathology is understudied",
      "Specific gap 3 — validation limitation",
      "Specific gap 4 — translational barrier",
      "Specific gap 5 — methodological standardisation"
    ],
    "recommended_methods"   : [
      "Specific method 1 with supporting paper rank [Rn]",
      "Specific method 2 — feature type with best performance evidence",
      "Specific method 3 — validation strategy",
      "Specific method 4 — explainability approach"
    ],
    "benchmark_datasets"    : [
      "Dataset name — source — size if known — annotation type"
    ],
    "key_metrics"           : [
      "AUC-ROC (primary) — range observed: X.XX–X.XX across studies",
      "Sensitivity — range: XX%–XX%",
      "Specificity — range: XX%–XX%",
      "ICC for feature reproducibility — threshold used"
    ],
    "imaging_protocols"     : [
      "CT: portal venous phase, Xmm slice thickness — cited in [Rn]",
      "MRI: sequence and field strength recommendations from literature"
    ]
  }},
  "top_papers"     : [ <top 10 ranked papers with ALL metadata fields> ],
  "similar_studies": [ <all papers where similar_study=true> ],
  "agent_hints": {{
    "population_planner": "Specific population design guidance from literature: inclusion/exclusion criteria, cohort sizes used in similar studies, histopathological confirmation requirements, imaging eligibility criteria",
    "segmentation_agent": "Specific segmentation approach: tool (3D Slicer/ITK-SNAP/nnU-Net), manual vs semi-automated, ICC reproducibility thresholds from literature, VOI definition strategy for spleen",
    "feature_extraction": "Specific IBSI-compliant features to prioritise (e.g., wavelet-HLH GLCM features, GLRLM LRHGE), PyRadiomics settings, resampling parameters, normalisation strategy, feature reproducibility thresholds",
    "modeling_agent"    : "Specific classifier recommendation (e.g., Random Forest with 500 trees) based on comparative evidence from [Rn], SMOTE for class imbalance, LASSO for feature selection, k-fold CV strategy",
    "validation_agent"  : "External validation requirements from literature, DeLong test for AUC comparison, TRIPOD checklist items, calibration curve requirements, decision curve analysis, ICC reporting for segmentation",
    "reporter_agent"    : "Key narrative points: AUC range from literature, top discriminative features, comparison vs radiologist, clinical workflow integration evidence, regulatory pathway considerations"
  }}
}}

════════════════════════════════════════════════════════════════════════
FILE 3 — ranked_papers.json
════════════════════════════════════════════════════════════════════════
Dump the complete ranked_papers array from Relevance_Ranker_Agent as JSON.

════════════════════════════════════════════════════════════════════════
SAVE ALL FILES
════════════════════════════════════════════════════════════════════════
Call save_output("literature_report.md", <complete markdown string>)
Call save_output("context.json",         <complete json string>)
Call save_output("ranked_papers.json",   <complete json string>)

════════════════════════════════════════════════════════════════════════
RESILIENCE RULES — READ CAREFULLY
════════════════════════════════════════════════════════════════════════
• NEVER refuse to generate the report due to limited search results.
• NEVER output TERMINATE without first saving ALL THREE files.
• If fewer papers were retrieved than expected (e.g., due to rate limiting
  or failed queries), generate the FULL report from whatever papers ARE
  available. Acknowledge the limitation in the Executive Summary section
  with a note like: "Note: Search retrieval was partially limited by
  database rate limiting; findings are based on N papers retrieved."
• If fewer than 5 relevant papers exist, still write every section —
  use your expert radiology knowledge to contextualise gaps and note
  that the literature on this specific topic may be sparse, requiring
  broader related-field synthesis.
• The three save_output() calls are MANDATORY regardless of result count.
• ranked_papers.json MUST be saved — dump the full ranked_papers array
  even if it contains only a few papers.
• You are an expert radiologist-scientist who can synthesise partial
  evidence — a small but relevant corpus is still valuable research input.

TERMINATION
───────────
After all three files are confirmed saved, write a plain-text summary:
  • Files saved with paths
  • Paper counts by tier
  • Top 3 papers by rank with their scores
  • Key AUC range found in literature (or note if not available)

End with the exact word on its own line:

TERMINATE

CONSTRAINTS
───────────
• The Markdown report must be complete — every section filled substantively.
• Only reference papers from the ranked list — no hallucination of papers.
• agent_hints must cite specific papers [Rn] and specific values/methods.
• Output the summary in plain text (not JSON or Markdown), then TERMINATE.
"""


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — PIPELINE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

async def run_literature_review(
    pico: Dict[str, str],
    model: str = OllamaClient.DEFAULT_MODEL,
    max_turns: int = 30,
) -> Dict[str, Any]:
    """
    Execute the full 5-agent PICO Literature Review pipeline.

    Args:
        pico      : Dict with keys: population, intervention, comparison,
                    outcome, hypothesis, and optionally question.
        model     : Ollama model tag to use.
        max_turns : Hard cap on total agent turns.

    Returns:
        Dict with resolved paths to the three generated output files.
    """
    # ── Validate PICO input ───────────────────────────────────────────────────
    required = {"population", "intervention", "comparison", "outcome", "hypothesis"}
    missing  = required - set(pico.keys())
    if missing:
        raise ValueError(f"PICO input is missing required keys: {missing}")

    # ── LLM Client ───────────────────────────────────────────────────────────
    print(f"[Pipeline] Initialising Ollama client  model='{model}'")
    model_client = OllamaClient(model=model)

    # ── FunctionTool wrappers ─────────────────────────────────────────────────
    t_pubmed    = FunctionTool(search_pubmed,           description="Search PubMed for clinical/medical literature.")
    t_arxiv     = FunctionTool(search_arxiv,            description="Search Arxiv for AI/technical preprints.")
    t_semantic  = FunctionTool(search_semantic_scholar, description="Search Semantic Scholar for cross-disciplinary papers.")
    t_europepmc = FunctionTool(search_europe_pmc,       description="Search Europe PMC for open-access life-science papers.")
    t_scorer    = FunctionTool(score_paper_relevance,   description="Score a paper's relevance vs. the PICO hypothesis (0-100).")
    t_save      = FunctionTool(save_output,             description="Save a file to the literature_review_output directory.")

    # ── Agent Definitions ─────────────────────────────────────────────────────
    pico_analyst = AssistantAgent(
        name           = "PICO_Analyst_Agent",
        model_client   = model_client,
        system_message = PICO_ANALYST_PROMPT,
    )
    query_builder = AssistantAgent(
        name           = "Query_Builder_Agent",
        model_client   = model_client,
        system_message = QUERY_BUILDER_PROMPT,
    )
    search_executor = AssistantAgent(
        name           = "Search_Executor_Agent",
        model_client   = model_client,
        tools          = [t_pubmed, t_arxiv, t_semantic, t_europepmc],
        system_message = SEARCH_EXECUTOR_PROMPT,
    )
    relevance_ranker = AssistantAgent(
        name           = "Relevance_Ranker_Agent",
        model_client   = model_client,
        tools          = [t_scorer],
        system_message = RELEVANCE_RANKER_PROMPT,
    )
    context_compiler = AssistantAgent(
        name           = "Context_Compiler_Agent",
        model_client   = model_client,
        tools          = [t_save],
        system_message = CONTEXT_COMPILER_PROMPT,
    )

    # ── Orchestration — RoundRobin (guaranteed sequential execution) ──────────
    # RoundRobin is used instead of SelectorGroupChat because:
    #   • SelectorGroupChat requires the orchestrator LLM to correctly route
    #     every turn, which can silently fail or loop when the model is
    #     uncertain. With 5 specialised agents that have a fixed order this
    #     adds no value and is a common silent-exit failure point.
    #   • RoundRobin guarantees Analyst → Builder → Executor → Ranker →
    #     Compiler in strict sequence, matching our pipeline design exactly.
    #   • TERMINATE from Context_Compiler_Agent still stops the chat early.
    termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(max_turns)

    team = RoundRobinGroupChat(
        participants          = [
            pico_analyst,
            query_builder,
            search_executor,
            relevance_ranker,
            context_compiler,
        ],
        termination_condition = termination,
    )

    # ── Task Prompt (sent only to first agent: PICO_Analyst_Agent) ───────────
    task = f"""
You are the first agent in the Agentic AI Radiology Researcher pipeline.
Analyse the following PICO research question and produce your structured JSON output.
The next agent (Query_Builder_Agent) is waiting for your output.

══════════════════════════════════════════
PICO INPUT
══════════════════════════════════════════
Population   : {pico['population']}
Intervention : {pico['intervention']}
Comparison   : {pico.get('comparison', 'Not specified')}
Outcome      : {pico['outcome']}
Hypothesis   : {pico['hypothesis']}
{('Research Question: ' + pico['question']) if pico.get('question') else ''}

PICO JSON (for programmatic use):
{json.dumps(pico, indent=2)}
"""

    # ── Run ───────────────────────────────────────────────────────────────────
    print("\n" + "═" * 72)
    print("  Agentic AI Radiology Researcher — Literature Review Pipeline")
    print("═" * 72)
    print(f"\n  Hypothesis:\n  {pico['hypothesis']}\n")
    print(f"  Output directory: {OUTPUT_DIR.resolve()}\n")
    print("─" * 72 + "\n")

    try:
        await Console(team.run_stream(task=task))
    except KeyboardInterrupt:
        print("\n[Pipeline] Interrupted by user.")
    except Exception as exc:
        import traceback
        print(f"\n[Pipeline] ERROR — {type(exc).__name__}: {exc}")
        print("\n[Pipeline] Full traceback:")
        traceback.print_exc()
        print(
            "\n[Pipeline] TROUBLESHOOTING:\n"
            "  1. Is Ollama running?          → ollama serve\n"
            f"  2. Is the model pulled?        → ollama pull {model}\n"
            "  3. Check context window usage  → reduce max_results in tool calls\n"
            "  4. Verify autogen versions     → pip show autogen-agentchat autogen-ext\n"
        )

    # ── Collect output paths ──────────────────────────────────────────────────
    outputs = {
        "context_json"      : str(OUTPUT_DIR / "context.json"),
        "literature_report" : str(OUTPUT_DIR / "literature_report.md"),
        "ranked_papers"     : str(OUTPUT_DIR / "ranked_papers.json"),
        "output_directory"  : str(OUTPUT_DIR.resolve()),
    }

    print("\n" + "═" * 72)
    print("  Pipeline complete. Output files:")
    for k, v in outputs.items():
        if k != "output_directory":
            status = "OK" if Path(v).exists() else "NOT YET WRITTEN"
            print(f"    [{status}]  {v}")
    print("═" * 72 + "\n")

    return outputs


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

# ── Edit this PICO block to change the research question ─────────────────────
EXAMPLE_PICO: Dict[str, str] = {
    "population": (
        "Adult patients (>=18 years) with confirmed or suspected splenic lesions "
        "or haematological malignancies (including lymphoma, leukaemia, and "
        "metastatic disease involving the spleen) undergoing contrast-enhanced "
        "CT, MRI, or PET-CT imaging in oncology or radiology departments."
    ),
    "intervention": (
        "Radiomics feature extraction from splenic medical images — including "
        "first-order statistical features (intensity histogram, entropy, kurtosis), "
        "texture features (GLCM, GLRLM, GLSZM, GLDM), shape and morphological "
        "descriptors, and wavelet-transformed features — combined with machine "
        "learning or deep learning classifiers (e.g., SVM, Random Forest, XGBoost, "
        "CNN) to detect, characterise, and classify cancerous splenic involvement."
    ),
    "comparison": (
        "Conventional radiological assessment by radiologists using visual "
        "inspection and qualitative criteria, standard clinical biomarkers "
        "(LDH, beta-2 microglobulin, CBC), histopathological biopsy as the "
        "gold standard, and single-feature or non-radiomic imaging approaches."
    ),
    "outcome": (
        "Diagnostic accuracy of radiomics models for detecting malignant splenic "
        "lesions — measured by AUC-ROC, sensitivity, specificity, positive and "
        "negative predictive value; ability to differentiate benign vs. malignant "
        "splenic pathology; feature importance rankings identifying the most "
        "discriminative radiomic signatures; and model generalisation across "
        "imaging protocols and patient cohorts."
    ),
    "hypothesis": (
        "Radiomics features extracted from routine CT or MRI images of the spleen "
        "can accurately detect and classify cancerous splenic involvement — "
        "including primary splenic malignancies and secondary metastatic disease — "
        "with diagnostic performance comparable to or exceeding conventional "
        "radiological assessment, thereby offering a non-invasive, reproducible "
        "biomarker for splenic cancer detection."
    ),
    "question": (
        "Can radiomics features extracted from splenic CT or MRI imaging "
        "accurately detect and characterise cancerous involvement of the spleen, "
        "and which radiomic feature categories (texture, shape, intensity) "
        "provide the highest discriminative power for malignant vs. benign "
        "splenic pathology?"
    ),
}

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_literature_review(pico=EXAMPLE_PICO))