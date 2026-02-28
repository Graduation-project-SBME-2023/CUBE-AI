"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         Dynamic Knowledge Base Builder for Literature Review Agent           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Builds domain knowledge dynamically from online medical databases           ║
║  instead of relying on a static hardcoded knowledge base.                    ║
║                                                                              ║
║  Data Sources:                                                               ║
║    • NCBI MeSH Database    — medical terminology, term hierarchies           ║
║    • PubMed E-Utilities    — recent reviews, guidelines, key findings        ║
║    • Wikipedia REST API    — structured summaries of medical concepts         ║
║    • Semantic Scholar API  — field-of-study discovery, methods trends         ║
║    • Europe PMC            — open-access annotations, journal metadata       ║
║                                                                              ║
║  The knowledge base is PICO-driven: it adapts to any research question       ║
║  (e.g., liver radiomics, brain tumour classification, cardiac MRI).          ║
║                                                                              ║
║  Caching: Results are cached locally for 72 hours to avoid redundant         ║
║  API calls across repeated runs with the same PICO.                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────────────────────────────────────
# Standard Library
# ──────────────────────────────────────────────────────────────────────────────
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
CACHE_DIR = Path(__file__).parent / ".knowledge_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

NCBI_API_KEY: str = os.environ.get("NCBI_API_KEY", "")
PUBMED_DELAY: float = 0.4
GENERIC_DELAY: float = 0.25
SEMANTIC_SCHOLAR_DELAY: float = 3.5  # S2 rate-limits aggressively without API key

# Maximum reviews/papers to fetch per concept for knowledge extraction
MAX_REVIEWS_PER_CONCEPT = 5
MAX_WIKI_CONCEPTS = 12


# ══════════════════════════════════════════════════════════════════════════════
# HTTP & CACHING UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _http_get(url: str, timeout: int = 20, retries: int = 3) -> bytes:
    """HTTP GET with exponential-backoff retry and descriptive User-Agent."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PICO-KnowledgeBuilder/1.0 (medical-research; radiology-AI)",
            "Accept": "application/json, text/xml, */*",
        },
    )
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                logger.warning(f"HTTP {exc.code} for {url[:80]}... retrying in {wait}s")
                time.sleep(wait)
                continue
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise last_exc


def _cache_key(prefix: str, query: str) -> str:
    """Generate a cache filename from prefix and query."""
    h = hashlib.md5(query.encode("utf-8")).hexdigest()[:12]
    safe_prefix = re.sub(r"[^a-zA-Z0-9_]", "_", prefix)[:30]
    return f"{safe_prefix}_{h}.json"


def _get_cached(key: str, max_age_hours: int = 72) -> Optional[Any]:
    """Return cached data if fresh enough, else None."""
    path = CACHE_DIR / key
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < max_age_hours:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _set_cache(key: str, data: Any) -> None:
    """Write data to local cache."""
    try:
        (CACHE_DIR / key).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning(f"Cache write failed for {key}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# CONCEPT EXTRACTION FROM PICO
# ══════════════════════════════════════════════════════════════════════════════

# Canonical concept dictionaries — used to detect what the PICO is about
_ORGAN_TERMS: Dict[str, str] = {
    "spleen": "Spleen", "splenic": "Spleen",
    "liver": "Liver", "hepatic": "Liver", "hepatocellular": "Liver",
    "kidney": "Kidney", "renal": "Kidney", "nephrotic": "Kidney",
    "lung": "Lung", "pulmonary": "Lung", "bronchial": "Lung",
    "brain": "Brain", "cerebral": "Brain", "intracranial": "Brain",
    "breast": "Breast", "mammary": "Breast", "mammographic": "Breast",
    "prostate": "Prostate", "prostatic": "Prostate",
    "pancreas": "Pancreas", "pancreatic": "Pancreas",
    "colon": "Colon", "colorectal": "Colon", "rectal": "Rectum",
    "thyroid": "Thyroid", "cardiac": "Heart", "heart": "Heart",
    "bone": "Bone", "skeletal": "Bone", "osseous": "Bone",
    "ovary": "Ovary", "ovarian": "Ovary",
    "stomach": "Stomach", "gastric": "Stomach",
    "esophagus": "Esophagus", "oesophagus": "Esophagus",
    "bladder": "Urinary Bladder", "adrenal": "Adrenal Glands",
    "lymph node": "Lymph Nodes", "lymph nodes": "Lymph Nodes",
}

_PATHOLOGY_TERMS: Dict[str, str] = {
    "lymphoma": "Lymphoma", "hodgkin": "Hodgkin Lymphoma",
    "non-hodgkin": "Non-Hodgkin Lymphoma", "dlbcl": "Diffuse Large B-Cell Lymphoma",
    "follicular": "Follicular Lymphoma", "marginal zone": "Marginal Zone Lymphoma",
    "mantle cell": "Mantle Cell Lymphoma", "burkitt": "Burkitt Lymphoma",
    "leukaemia": "Leukemia", "leukemia": "Leukemia",
    "metastasis": "Neoplasm Metastasis", "metastases": "Neoplasm Metastasis",
    "metastatic": "Neoplasm Metastasis",
    "carcinoma": "Carcinoma", "adenocarcinoma": "Adenocarcinoma",
    "melanoma": "Melanoma", "sarcoma": "Sarcoma",
    "glioma": "Glioma", "glioblastoma": "Glioblastoma",
    "hepatocellular carcinoma": "Hepatocellular Carcinoma", "hcc": "Hepatocellular Carcinoma",
    "renal cell carcinoma": "Renal Cell Carcinoma",
    "angiosarcoma": "Angiosarcoma",
    "haemangioma": "Hemangioma", "hemangioma": "Hemangioma",
    "hamartoma": "Hamartoma",
    "abscess": "Abscess", "infarct": "Infarction",
    "benign": "Benign Neoplasm", "malignant": "Malignant Neoplasm",
    "cancer": "Neoplasms", "tumour": "Neoplasms", "tumor": "Neoplasms",
    "neoplasm": "Neoplasms", "lesion": "Pathologic Processes",
    "cyst": "Cysts", "fibrosis": "Fibrosis",
    "splenomegaly": "Splenomegaly", "hepatomegaly": "Hepatomegaly",
}

_MODALITY_TERMS: Dict[str, str] = {
    "ct": "Computed Tomography", "computed tomography": "Computed Tomography",
    "mri": "Magnetic Resonance Imaging", "magnetic resonance": "Magnetic Resonance Imaging",
    "pet-ct": "PET-CT", "pet/ct": "PET-CT", "pet ct": "PET-CT",
    "pet-mri": "PET-MRI", "pet/mri": "PET-MRI",
    "ultrasound": "Ultrasound", "sonography": "Ultrasound",
    "mammography": "Mammography", "x-ray": "X-Ray Radiography",
    "fluoroscopy": "Fluoroscopy", "nuclear medicine": "Nuclear Medicine",
    "spect": "SPECT", "oct": "Optical Coherence Tomography",
}

_METHOD_TERMS: Dict[str, str] = {
    "radiomics": "Radiomics", "radiomic": "Radiomics",
    "texture analysis": "Texture Analysis", "texture feature": "Texture Analysis",
    "machine learning": "Machine Learning", "deep learning": "Deep Learning",
    "random forest": "Random Forest", "svm": "Support Vector Machine",
    "support vector": "Support Vector Machine",
    "xgboost": "Gradient Boosting", "gradient boosting": "Gradient Boosting",
    "logistic regression": "Logistic Regression",
    "neural network": "Neural Networks", "cnn": "Convolutional Neural Network",
    "convolutional": "Convolutional Neural Network",
    "transformer": "Vision Transformer", "vision transformer": "Vision Transformer",
    "u-net": "U-Net Segmentation", "unet": "U-Net Segmentation",
    "segmentation": "Image Segmentation", "classification": "Classification",
    "feature extraction": "Feature Extraction", "feature selection": "Feature Selection",
    "glcm": "GLCM Texture Features", "glrlm": "GLRLM Texture Features",
    "glszm": "GLSZM Texture Features", "gldm": "GLDM Texture Features",
    "wavelet": "Wavelet Features", "shape feature": "Shape Features",
    "first-order": "First-Order Statistics", "histogram": "Histogram Features",
    "ibsi": "IBSI Standard", "pyradiomics": "PyRadiomics",
    "lasso": "LASSO Regression", "mrmr": "mRMR Feature Selection",
    "shap": "SHAP Explainability", "grad-cam": "Grad-CAM",
    "smote": "SMOTE Oversampling", "cross-validation": "Cross-Validation",
    "transfer learning": "Transfer Learning",
    "nomogram": "Nomogram", "rad-score": "Radiomics Score",
}

_METRIC_TERMS: Dict[str, str] = {
    "auc": "AUC-ROC", "roc": "ROC Curve", "auc-roc": "AUC-ROC",
    "sensitivity": "Sensitivity", "specificity": "Specificity",
    "accuracy": "Accuracy", "precision": "Precision", "recall": "Recall",
    "f1": "F1 Score", "ppv": "Positive Predictive Value",
    "npv": "Negative Predictive Value",
    "icc": "Intraclass Correlation Coefficient",
    "dice": "Dice Similarity Coefficient",
    "hausdorff": "Hausdorff Distance",
}


def extract_concepts_from_pico(pico: Dict[str, str]) -> Dict[str, List[str]]:
    """
    Extract key medical/technical concepts from PICO input.

    Returns a dict with keys: organs, pathologies, modalities, methods, metrics.
    Each value is a deduplicated list of canonical concept names.
    """
    all_text = " ".join(pico.values()).lower()

    def _match(term_dict: Dict[str, str]) -> List[str]:
        found = []
        for term, canonical in term_dict.items():
            # Use word boundary matching for short terms to avoid false positives
            if len(term) <= 3:
                if re.search(rf"\b{re.escape(term)}\b", all_text):
                    if canonical not in found:
                        found.append(canonical)
            else:
                if term in all_text and canonical not in found:
                    found.append(canonical)
        return found

    concepts = {
        "organs": _match(_ORGAN_TERMS),
        "pathologies": _match(_PATHOLOGY_TERMS),
        "modalities": _match(_MODALITY_TERMS),
        "methods": _match(_METHOD_TERMS),
        "metrics": _match(_METRIC_TERMS),
    }

    logger.info(
        f"Extracted concepts — organs: {concepts['organs']}, "
        f"pathologies: {len(concepts['pathologies'])}, "
        f"modalities: {concepts['modalities']}, "
        f"methods: {len(concepts['methods'])}"
    )
    return concepts


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 1 — WIKIPEDIA REST API (structured medical summaries)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_wikipedia_summary(topic: str) -> Optional[Dict[str, str]]:
    """
    Fetch a structured summary from Wikipedia REST API.
    Falls back to Wikipedia search API for compound terms that 404.

    Returns dict with keys: title, description, extract (first ~300 words).
    Returns None on failure.
    """
    cache_k = _cache_key("wiki", topic)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    time.sleep(GENERIC_DELAY)
    encoded = urllib.parse.quote(topic.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"

    try:
        data = json.loads(_http_get(url).decode("utf-8"))
        result = {
            "title": data.get("title", topic),
            "description": data.get("description", ""),
            "extract": data.get("extract", "")[:2000],
        }
        _set_cache(cache_k, result)
        return result
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Fallback: use Wikipedia search API to find the best matching article
            return _fetch_wikipedia_via_search(topic)
        logger.warning(f"Wikipedia fetch failed for '{topic}': {exc}")
        return None
    except Exception as exc:
        logger.warning(f"Wikipedia fetch failed for '{topic}': {exc}")
        return None


def _fetch_wikipedia_via_search(topic: str) -> Optional[Dict[str, str]]:
    """Fallback: search Wikipedia for a topic and return the best match summary."""
    cache_k = _cache_key("wiki_search", topic)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    time.sleep(GENERIC_DELAY)
    search_url = (
        f"https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&srsearch={urllib.parse.quote(topic)}"
        f"&srnamespace=0&srlimit=1&format=json"
    )
    try:
        search_data = json.loads(_http_get(search_url).decode("utf-8"))
        results = search_data.get("query", {}).get("search", [])
        if not results:
            return None
        best_title = results[0].get("title", "")
        if not best_title:
            return None
        # Now fetch the summary for the matched title
        time.sleep(GENERIC_DELAY)
        encoded = urllib.parse.quote(best_title.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        data = json.loads(_http_get(url).decode("utf-8"))
        result = {
            "title": data.get("title", best_title),
            "description": data.get("description", ""),
            "extract": data.get("extract", "")[:2000],
        }
        _set_cache(cache_k, result)
        return result
    except Exception as exc:
        logger.debug(f"Wikipedia search fallback failed for '{topic}': {exc}")
        return None


def fetch_wikipedia_knowledge(concepts: Dict[str, List[str]]) -> Dict[str, Dict[str, str]]:
    """
    Fetch Wikipedia summaries for all extracted concepts.

    Returns a dict mapping concept_name -> {title, description, extract}.
    """
    # Build list of topics to fetch — prioritise organs and pathologies
    topics: List[str] = []
    for organ in concepts.get("organs", []):
        topics.append(organ)
        # Also fetch organ-specific pathology articles
        for path in concepts.get("pathologies", [])[:3]:
            topics.append(f"{organ} {path.lower()}")
    for path in concepts.get("pathologies", []):
        if path not in topics:
            topics.append(path)
    for mod in concepts.get("modalities", []):
        if mod not in topics:
            topics.append(mod)
    for method in concepts.get("methods", [])[:5]:
        if method not in topics:
            topics.append(method)

    # Limit total fetches to avoid excessive API calls
    topics = topics[:MAX_WIKI_CONCEPTS]

    results = {}
    for topic in topics:
        summary = _fetch_wikipedia_summary(topic)
        if summary and summary.get("extract"):
            results[topic] = summary

    logger.info(f"Wikipedia: fetched {len(results)}/{len(topics)} summaries")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 2 — NCBI MeSH DATABASE (medical terminology hierarchies)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_mesh_info(term: str) -> Optional[Dict[str, Any]]:
    """
    Fetch MeSH descriptor information for a term via the NLM MeSH REST API
    (id.nlm.nih.gov) with fallback to NCBI E-Utilities text parsing.

    Returns dict with: mesh_id, preferred_term, tree_numbers, scope_note,
                       entry_terms (synonyms), qualifiers.
    """
    cache_k = _cache_key("mesh", term)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    time.sleep(GENERIC_DELAY)

    try:
        # ── Strategy 1: NLM MeSH REST API (preferred — returns JSON) ─────────
        # Step 1: Look up descriptor by label
        lookup_url = (
            f"https://id.nlm.nih.gov/mesh/lookup/descriptor"
            f"?label={urllib.parse.quote(term)}&match=contains&limit=5"
        )
        lookup_data = json.loads(_http_get(lookup_url).decode("utf-8"))

        if not lookup_data:
            # Try with exact match disabled
            lookup_url = (
                f"https://id.nlm.nih.gov/mesh/lookup/term"
                f"?label={urllib.parse.quote(term)}&match=contains&limit=5"
            )
            lookup_data = json.loads(_http_get(lookup_url).decode("utf-8"))

        if not lookup_data:
            return None

        # Pick the best match (first result is usually most relevant)
        best_match = lookup_data[0]
        descriptor_uri = best_match.get("resource", "")
        mesh_ui = descriptor_uri.rsplit("/", 1)[-1] if descriptor_uri else ""

        result: Dict[str, Any] = {
            "mesh_id": mesh_ui,
            "preferred_term": best_match.get("label", term),
            "tree_numbers": [],
            "scope_note": "",
            "entry_terms": [],
            "qualifiers": [],
        }

        # Step 2: Fetch full descriptor details via SPARQL-like JSON endpoint
        if mesh_ui:
            time.sleep(GENERIC_DELAY)
            detail_url = f"https://id.nlm.nih.gov/mesh/{mesh_ui}.json"
            try:
                detail_data = json.loads(_http_get(detail_url).decode("utf-8"))

                # Extract tree numbers
                for tn in detail_data.get("treeNumber", []):
                    if isinstance(tn, dict):
                        tn_label = tn.get("label", "")
                    elif isinstance(tn, str):
                        tn_label = tn.rsplit("/", 1)[-1] if "/" in tn else tn
                    else:
                        continue
                    if tn_label:
                        result["tree_numbers"].append(tn_label)

                # Extract scope note
                scope = detail_data.get("scopeNote", "")
                if isinstance(scope, list):
                    scope = scope[0] if scope else ""
                if isinstance(scope, dict):
                    scope = scope.get("@value", "")
                result["scope_note"] = str(scope)[:1000]

                # Extract entry terms / concepts
                for concept in detail_data.get("concept", []):
                    if isinstance(concept, dict):
                        label = concept.get("label", "")
                        if label and label != result["preferred_term"]:
                            result["entry_terms"].append(label)
                        # Check for terms within concept
                        for t in concept.get("term", []):
                            if isinstance(t, dict):
                                t_label = t.get("label", "")
                                if t_label and t_label != result["preferred_term"]:
                                    if t_label not in result["entry_terms"]:
                                        result["entry_terms"].append(t_label)
                    elif isinstance(concept, str):
                        c_label = concept.rsplit("/", 1)[-1] if "/" in concept else concept
                        if c_label and c_label != result["preferred_term"]:
                            result["entry_terms"].append(c_label)

                # Extract qualifiers
                for qual in detail_data.get("allowableQualifier", []):
                    if isinstance(qual, dict):
                        q_label = qual.get("label", "")
                        if q_label:
                            result["qualifiers"].append(q_label)
                    elif isinstance(qual, str):
                        q_label = qual.rsplit("/", 1)[-1] if "/" in qual else qual
                        if q_label:
                            result["qualifiers"].append(q_label)

            except Exception as detail_exc:
                logger.debug(f"MeSH detail fetch failed for {mesh_ui}: {detail_exc}")
                # Still return the basic info from lookup

        result["entry_terms"] = result["entry_terms"][:15]
        result["qualifiers"] = result["qualifiers"][:10]

        _set_cache(cache_k, result)
        return result

    except Exception as exc:
        logger.warning(f"MeSH fetch failed for '{term}': {exc}")
        return None


def fetch_mesh_knowledge(concepts: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch MeSH records for key concepts (organs, pathologies, modalities).

    Returns dict mapping concept_name -> MeSH record.
    """
    terms_to_fetch: List[str] = []
    for category in ["organs", "pathologies", "modalities"]:
        for concept in concepts.get(category, []):
            if concept not in terms_to_fetch:
                terms_to_fetch.append(concept)

    # Also add method-related MeSH terms
    mesh_method_map = {
        "Radiomics": "Radiomics",
        "Machine Learning": "Machine Learning",
        "Deep Learning": "Deep Learning",
        "Image Segmentation": "Image Processing, Computer-Assisted",
        "Feature Extraction": "Image Processing, Computer-Assisted",
        "Support Vector Machine": "Support Vector Machine",
        "Random Forest": "Random Forest",
        "Neural Networks": "Neural Networks, Computer",
    }
    for method in concepts.get("methods", []):
        mesh_term = mesh_method_map.get(method, method)
        if mesh_term not in terms_to_fetch:
            terms_to_fetch.append(mesh_term)

    terms_to_fetch = terms_to_fetch[:20]  # Cap to avoid excessive API calls

    results = {}
    for term in terms_to_fetch:
        info = _fetch_mesh_info(term)
        if info and info.get("preferred_term"):
            results[term] = info

    logger.info(f"MeSH: fetched {len(results)}/{len(terms_to_fetch)} records")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 3 — PubMed REVIEW ARTICLES (state-of-the-art knowledge)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_pubmed_reviews(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Fetch recent review/meta-analysis abstracts from PubMed for a query.
    These provide authoritative summaries of the state of the art.

    Returns list of {title, authors, journal, year, abstract, pmid}.
    """
    cache_k = _cache_key("reviews", query)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    api_suffix = f"&api_key={NCBI_API_KEY}" if NCBI_API_KEY else ""

    # Append review/meta-analysis filter and recency filter
    full_query = f"({query}) AND (review[pt] OR systematic review[pt] OR meta-analysis[pt]) AND 2019:3000[pdat]"

    time.sleep(PUBMED_DELAY)

    try:
        esearch = (
            f"{BASE}/esearch.fcgi"
            f"?db=pubmed&term={urllib.parse.quote(full_query)}"
            f"&retmode=json&retmax={max_results}&sort=relevance{api_suffix}"
        )
        search_data = json.loads(_http_get(esearch).decode("utf-8"))
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []

        time.sleep(PUBMED_DELAY)

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
                art = medline.find("Article")
                title = art.findtext("ArticleTitle", default="").strip()

                authors = []
                for a in art.findall(".//Author"):
                    ln = a.findtext("LastName", "")
                    fn = a.findtext("ForeName", "")
                    if ln:
                        authors.append(f"{ln} {fn}".strip())
                author_str = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")

                journal = art.findtext(".//Title", default="Unknown Journal").strip()
                year = (
                    medline.findtext(".//PubDate/Year")
                    or medline.findtext(".//PubDate/MedlineDate", "n.d.")[:4]
                )
                abstract = " ".join(
                    (p.text or "") for p in art.findall(".//AbstractText")
                ).strip()[:1500]

                pmid = medline.findtext("PMID", default="")

                if abstract:  # Only include reviews with abstracts
                    results.append({
                        "title": title,
                        "authors": author_str,
                        "journal": journal,
                        "year": year,
                        "abstract": abstract,
                        "pmid": pmid,
                    })
            except Exception:
                continue

        _set_cache(cache_k, results)
        return results

    except Exception as exc:
        logger.warning(f"PubMed review fetch failed for '{query}': {exc}")
        return []


def fetch_review_knowledge(
    concepts: Dict[str, List[str]], pico: Dict[str, str]
) -> Dict[str, List[Dict[str, str]]]:
    """
    Fetch recent review articles for key concept combinations.

    Returns dict mapping query_description -> list of review articles.
    """
    queries: List[Tuple[str, str]] = []  # (description, query_string)

    # Build targeted review queries from PICO concepts
    primary_organs = concepts.get("organs", [])[:2]
    primary_pathologies = concepts.get("pathologies", [])[:3]
    primary_modalities = concepts.get("modalities", [])[:2]
    primary_methods = concepts.get("methods", [])[:3]

    # Organ + pathology reviews
    for organ in primary_organs:
        for pathology in primary_pathologies[:2]:
            queries.append(
                (f"{organ} {pathology}", f"{organ} {pathology} imaging diagnosis")
            )

    # Organ + method reviews (e.g., "spleen radiomics")
    for organ in primary_organs:
        for method in primary_methods[:2]:
            queries.append(
                (f"{organ} {method}", f"{organ} {method}")
            )

    # Modality + method reviews (e.g., "CT radiomics")
    for mod in primary_modalities:
        for method in primary_methods[:2]:
            queries.append(
                (f"{mod} {method}", f"{mod} {method}")
            )

    # Method-specific methodology reviews
    for method in primary_methods[:3]:
        queries.append(
            (f"{method} methodology", f"{method} medical imaging methodology")
        )

    # Clinical guideline search for pathologies
    for pathology in primary_pathologies[:2]:
        queries.append(
            (f"{pathology} guidelines", f"{pathology} clinical guideline diagnosis imaging")
        )

    # Deduplicate and limit
    seen = set()
    unique_queries = []
    for desc, q in queries:
        if desc not in seen:
            seen.add(desc)
            unique_queries.append((desc, q))
    unique_queries = unique_queries[:15]

    results: Dict[str, List[Dict[str, str]]] = {}
    for desc, q in unique_queries:
        reviews = _fetch_pubmed_reviews(q, max_results=MAX_REVIEWS_PER_CONCEPT)
        if reviews:
            results[desc] = reviews

    logger.info(
        f"PubMed reviews: fetched {sum(len(v) for v in results.values())} "
        f"articles across {len(results)} queries"
    )
    return results


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 4 — SEMANTIC SCHOLAR (field trends & method discovery)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_semantic_scholar_trends(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch trending/highly-cited papers from Semantic Scholar for a query.
    Provides insight into which methods and approaches are most active.

    Returns list of {title, year, citation_count, authors, fields_of_study, tldr}.
    """
    cache_k = _cache_key("s2trends", query)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = "title,year,citationCount,authors,fieldsOfStudy,tldr"

    time.sleep(SEMANTIC_SCHOLAR_DELAY)

    params = urllib.parse.urlencode({
        "query": query,
        "limit": max_results,
        "fields": fields,
        "sort": "citationCount:desc",
    })

    try:
        data = json.loads(_http_get(f"{BASE}?{params}").decode("utf-8"))

        results = []
        for paper in data.get("data", []):
            authors = [a.get("name", "") for a in paper.get("authors", [])]
            author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
            tldr = paper.get("tldr", {})
            results.append({
                "title": paper.get("title", ""),
                "year": paper.get("year"),
                "citation_count": paper.get("citationCount", 0),
                "authors": author_str,
                "fields_of_study": paper.get("fieldsOfStudy", []),
                "tldr": tldr.get("text", "") if isinstance(tldr, dict) else "",
            })

        _set_cache(cache_k, results)
        return results

    except Exception as exc:
        logger.warning(f"Semantic Scholar trends fetch failed for '{query}': {exc}")
        return []


def fetch_method_trends(concepts: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch method/approach trends from Semantic Scholar.

    Returns dict mapping query_description -> list of trending papers.
    """
    queries: List[Tuple[str, str]] = []

    primary_organs = concepts.get("organs", [])[:2]
    primary_methods = concepts.get("methods", [])[:4]
    primary_modalities = concepts.get("modalities", [])[:2]

    # Organ + method trends (most important — max 2)
    for organ in primary_organs[:1]:
        for method in primary_methods[:2]:
            queries.append((f"{organ} {method}", f"{organ} {method}"))

    # General method trends in medical imaging (max 2)
    for method in primary_methods[:2]:
        queries.append(
            (f"{method} medical imaging",
             f"{method} medical imaging recent advances")
        )

    # Modality + organ trends (max 1)
    for mod in primary_modalities[:1]:
        for organ in primary_organs[:1]:
            queries.append(
                (f"{mod} {organ} AI", f"{mod} {organ} artificial intelligence")
            )

    # Deduplicate and limit to 5 (to stay within S2 rate limits)
    seen = set()
    unique_queries = []
    for desc, q in queries:
        if desc not in seen:
            seen.add(desc)
            unique_queries.append((desc, q))
    unique_queries = unique_queries[:5]

    results: Dict[str, List[Dict[str, Any]]] = {}
    for desc, q in unique_queries:
        papers = _fetch_semantic_scholar_trends(q, max_results=8)
        if papers:
            results[desc] = papers

    logger.info(
        f"Semantic Scholar trends: fetched {sum(len(v) for v in results.values())} "
        f"papers across {len(results)} queries"
    )
    return results


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 5 — EUROPE PMC (journal discovery & dataset references)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_europepmc_journal_stats(query: str, max_results: int = 50) -> Dict[str, int]:
    """
    Fetch journal frequency distribution from Europe PMC for a query.
    Shows which journals publish most on the topic.

    Returns dict mapping journal_title -> article_count.
    """
    cache_k = _cache_key("epmc_journals", query)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "pageSize": max_results,
        "resultType": "lite",
    })

    time.sleep(GENERIC_DELAY)

    try:
        resp_bytes = _http_get(f"{BASE}?{params}")
        data = json.loads(resp_bytes.decode("utf-8"))
        journal_counts: Dict[str, int] = {}
        for hit in data.get("resultList", {}).get("result", []):
            journal = hit.get("journalTitle", "Unknown")
            if journal and journal != "Unknown":
                journal_counts[journal] = journal_counts.get(journal, 0) + 1

        # Sort by count
        sorted_journals = dict(
            sorted(journal_counts.items(), key=lambda x: x[1], reverse=True)
        )
        _set_cache(cache_k, sorted_journals)
        return sorted_journals

    except Exception as exc:
        logger.warning(f"Europe PMC journal stats failed for '{query}': {exc}")
        return {}


def fetch_journal_landscape(concepts: Dict[str, List[str]]) -> Dict[str, int]:
    """
    Discover which journals publish most on the PICO topic.
    Combines results from multiple concept queries.

    Returns aggregated journal -> count mapping.
    """
    queries = []
    for organ in concepts.get("organs", [])[:2]:
        for method in concepts.get("methods", [])[:2]:
            queries.append(f"{organ} {method}")
    for mod in concepts.get("modalities", [])[:2]:
        for method in concepts.get("methods", [])[:2]:
            queries.append(f"{mod} {method}")

    queries = queries[:6]

    aggregated: Dict[str, int] = {}
    for q in queries:
        stats = _fetch_europepmc_journal_stats(q, max_results=30)
        for journal, count in stats.items():
            aggregated[journal] = aggregated.get(journal, 0) + count

    return dict(sorted(aggregated.items(), key=lambda x: x[1], reverse=True)[:30])


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 6 — PubMed CLINICAL GUIDELINES
# ══════════════════════════════════════════════════════════════════════════════

def fetch_clinical_guidelines(concepts: Dict[str, List[str]]) -> List[Dict[str, str]]:
    """
    Fetch recent clinical guidelines and practice parameters from PubMed
    relevant to the PICO pathologies and organs.

    Returns list of {title, journal, year, abstract, pmid}.
    """
    guidelines: List[Dict[str, str]] = []
    seen_pmids = set()

    queries = []
    for organ in concepts.get("organs", [])[:2]:
        queries.append(f"{organ} imaging guideline")
    for pathology in concepts.get("pathologies", [])[:3]:
        queries.append(f"{pathology} clinical guideline")

    queries = queries[:5]

    for q in queries:
        full_q = f"({q}) AND (guideline[pt] OR practice guideline[pt]) AND 2018:3000[pdat]"
        cache_k = _cache_key("guideline", full_q)
        cached = _get_cached(cache_k)
        if cached is not None:
            for g in cached:
                if g["pmid"] not in seen_pmids:
                    guidelines.append(g)
                    seen_pmids.add(g["pmid"])
            continue

        BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        api_suffix = f"&api_key={NCBI_API_KEY}" if NCBI_API_KEY else ""

        time.sleep(PUBMED_DELAY)

        try:
            esearch = (
                f"{BASE}/esearch.fcgi"
                f"?db=pubmed&term={urllib.parse.quote(full_q)}"
                f"&retmode=json&retmax=3&sort=relevance{api_suffix}"
            )
            search_data = json.loads(_http_get(esearch).decode("utf-8"))
            id_list = search_data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                _set_cache(cache_k, [])
                continue

            time.sleep(PUBMED_DELAY)

            efetch = (
                f"{BASE}/efetch.fcgi"
                f"?db=pubmed&id={','.join(id_list)}"
                f"&retmode=xml&rettype=abstract{api_suffix}"
            )
            root = ET.fromstring(_http_get(efetch).decode("utf-8"))

            fetched = []
            for article in root.findall(".//PubmedArticle"):
                try:
                    medline = article.find("MedlineCitation")
                    art = medline.find("Article")
                    title = art.findtext("ArticleTitle", default="").strip()
                    journal = art.findtext(".//Title", default="").strip()
                    year = (
                        medline.findtext(".//PubDate/Year")
                        or medline.findtext(".//PubDate/MedlineDate", "n.d.")[:4]
                    )
                    abstract = " ".join(
                        (p.text or "") for p in art.findall(".//AbstractText")
                    ).strip()[:1000]
                    pmid = medline.findtext("PMID", default="")

                    entry = {
                        "title": title,
                        "journal": journal,
                        "year": year,
                        "abstract": abstract,
                        "pmid": pmid,
                    }
                    fetched.append(entry)

                    if pmid not in seen_pmids:
                        guidelines.append(entry)
                        seen_pmids.add(pmid)
                except Exception:
                    continue

            _set_cache(cache_k, fetched)

        except Exception as exc:
            logger.warning(f"Guideline fetch failed for '{q}': {exc}")
            _set_cache(cache_k, [])

    logger.info(f"Clinical guidelines: fetched {len(guidelines)} guidelines")
    return guidelines


# ══════════════════════════════════════════════════════════════════════════════
# DATA SOURCE 7 — PubMed DATASET DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def fetch_dataset_references(concepts: Dict[str, List[str]]) -> List[Dict[str, str]]:
    """
    Search PubMed for dataset description papers and benchmark references
    relevant to the PICO topic.

    Returns list of {title, journal, year, abstract, pmid}.
    """
    queries = []
    for organ in concepts.get("organs", [])[:2]:
        queries.append(f"{organ} imaging dataset benchmark segmentation")
    for mod in concepts.get("modalities", [])[:2]:
        queries.append(f"{mod} medical image analysis challenge dataset")
    for organ in concepts.get("organs", [])[:1]:
        for pathology in concepts.get("pathologies", [])[:2]:
            queries.append(f"{organ} {pathology} annotated dataset imaging")

    queries = queries[:5]

    datasets: List[Dict[str, str]] = []
    seen_pmids = set()

    for q in queries:
        reviews = _fetch_pubmed_reviews(
            q.replace("review[pt] OR systematic review[pt] OR meta-analysis[pt]", ""),
            max_results=3,
        )
        for r in reviews:
            if r.get("pmid") and r["pmid"] not in seen_pmids:
                datasets.append(r)
                seen_pmids.add(r["pmid"])

    logger.info(f"Dataset references: fetched {len(datasets)} dataset papers")
    return datasets


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE FORMATTER
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_section(title: str, content: str) -> str:
    """Format a knowledge base section with box-drawing characters."""
    border = "─" * (len(title) + 4)
    return f"\n┌─ {title} {border}┐\n{content}\n└{'─' * (len(title) + len(border) + 3)}┘\n"


def _format_wiki_knowledge(
    wiki_data: Dict[str, Dict[str, str]],
    concepts: Dict[str, List[str]],
) -> str:
    """Format Wikipedia knowledge into structured sections."""
    sections = []

    # Organ anatomy and physiology
    for organ in concepts.get("organs", []):
        if organ in wiki_data:
            extract = wiki_data[organ]["extract"]
            if extract:
                sections.append(f"│ {organ.upper()}")
                # Wrap extract to ~75 chars per line
                words = extract.split()
                lines = []
                current_line = "│   "
                for word in words:
                    if len(current_line) + len(word) + 1 > 75:
                        lines.append(current_line)
                        current_line = "│   " + word
                    else:
                        current_line += " " + word if current_line != "│   " else word
                if current_line.strip("│ "):
                    lines.append(current_line)
                sections.append("\n".join(lines[:10]))  # Limit to ~10 lines
                sections.append("│")

    # Pathology descriptions
    pathology_entries = []
    for pathology in concepts.get("pathologies", []):
        if pathology in wiki_data:
            desc = wiki_data[pathology].get("description", "")
            extract = wiki_data[pathology]["extract"][:300]
            if desc or extract:
                pathology_entries.append(f"│   • {pathology}: {desc}")
                if extract:
                    # First 2 sentences of extract
                    sentences = re.split(r'(?<=[.!?])\s+', extract)
                    summary = " ".join(sentences[:2])
                    pathology_entries.append(f"│     {summary}")

    if pathology_entries:
        sections.append("│ RELEVANT PATHOLOGIES")
        sections.extend(pathology_entries)
        sections.append("│")

    # Imaging modality descriptions
    modality_entries = []
    for mod in concepts.get("modalities", []):
        if mod in wiki_data:
            extract = wiki_data[mod]["extract"][:400]
            if extract:
                sentences = re.split(r'(?<=[.!?])\s+', extract)
                summary = " ".join(sentences[:3])
                modality_entries.append(f"│   • {mod}: {summary}")

    if modality_entries:
        sections.append("│ IMAGING MODALITIES")
        sections.extend(modality_entries)
        sections.append("│")

    # Method descriptions
    method_entries = []
    for method in concepts.get("methods", [])[:5]:
        if method in wiki_data:
            extract = wiki_data[method]["extract"][:300]
            if extract:
                sentences = re.split(r'(?<=[.!?])\s+', extract)
                summary = " ".join(sentences[:2])
                method_entries.append(f"│   • {method}: {summary}")

    if method_entries:
        sections.append("│ ANALYTICAL METHODS")
        sections.extend(method_entries)

    return "\n".join(sections) if sections else "│ No Wikipedia data available."


def _format_mesh_knowledge(mesh_data: Dict[str, Dict[str, Any]]) -> str:
    """Format MeSH terminology knowledge into structured sections."""
    sections = []

    for concept, info in mesh_data.items():
        preferred = info.get("preferred_term", concept)
        tree_nums = info.get("tree_numbers", [])
        scope = info.get("scope_note", "")
        synonyms = info.get("entry_terms", [])[:8]

        entry = f"│ {preferred}"
        if tree_nums:
            entry += f"  [MeSH: {', '.join(tree_nums[:3])}]"
        sections.append(entry)

        if scope:
            # Wrap scope note
            scope_short = scope[:300]
            sections.append(f"│   Definition: {scope_short}")

        if synonyms:
            sections.append(f"│   Synonyms: {', '.join(synonyms)}")

        sections.append("│")

    return "\n".join(sections) if sections else "│ No MeSH data available."


def _format_review_knowledge(review_data: Dict[str, List[Dict[str, str]]]) -> str:
    """
    Format review article knowledge — extract key findings from abstracts.
    """
    sections = []

    for topic, reviews in review_data.items():
        sections.append(f"│ {topic.upper()} — Recent Evidence")
        for i, rev in enumerate(reviews[:3], 1):
            title = rev.get("title", "Untitled")[:100]
            journal = rev.get("journal", "")
            year = rev.get("year", "")
            abstract = rev.get("abstract", "")

            sections.append(f"│   [{i}] {title}")
            sections.append(f"│       {journal} ({year})")

            # Extract key sentences from abstract — look for conclusions/results
            if abstract:
                sentences = re.split(r'(?<=[.!?])\s+', abstract)
                # Prefer sentences containing key result-indicating words
                result_keywords = [
                    "result", "found", "showed", "demonstrated", "achieved",
                    "suggest", "conclude", "significant", "performance",
                    "auc", "sensitivity", "specificity", "accuracy",
                    "effective", "superior", "promising", "recommend",
                ]
                key_sentences = []
                for s in sentences:
                    s_lower = s.lower()
                    if any(kw in s_lower for kw in result_keywords):
                        key_sentences.append(s)
                    if len(key_sentences) >= 3:
                        break
                # Fallback to last 2 sentences if no key sentences found
                if not key_sentences and len(sentences) >= 2:
                    key_sentences = sentences[-2:]

                for ks in key_sentences[:2]:
                    sections.append(f"│       → {ks.strip()[:200]}")

        sections.append("│")

    return "\n".join(sections) if sections else "│ No review articles found."


def _format_method_trends(trend_data: Dict[str, List[Dict[str, Any]]]) -> str:
    """Format Semantic Scholar method trends."""
    sections = []

    for topic, papers in trend_data.items():
        sections.append(f"│ {topic.upper()} — Trending Research")
        for p in papers[:5]:
            title = p.get("title", "Untitled")[:90]
            year = p.get("year", "?")
            cites = p.get("citation_count", 0)
            tldr = p.get("tldr", "")
            fields = p.get("fields_of_study", [])

            sections.append(f"│   • {title} ({year}, {cites} citations)")
            if fields:
                sections.append(f"│     Fields: {', '.join(fields[:4])}")
            if tldr:
                sections.append(f"│     TLDR: {tldr[:200]}")

        sections.append("│")

    return "\n".join(sections) if sections else "│ No trend data available."


def _format_journal_landscape(journal_data: Dict[str, int]) -> str:
    """Format journal publication frequency data."""
    sections = []

    # Classify journals into tiers based on known radiology/medical imaging journals
    _TIER1_KEYWORDS = [
        "radiology", "medical image analysis", "ieee trans",
        "nature", "lancet", "jama", "new england", "bmj",
        "nuclear medicine", "neuroimage",
    ]
    _TIER2_KEYWORDS = [
        "european", "academic", "frontiers", "scientific reports",
        "plos", "diagnostics", "cancer", "insights",
    ]

    tier1_journals = []
    tier2_journals = []
    other_journals = []

    for journal, count in list(journal_data.items())[:25]:
        j_lower = journal.lower()
        if any(kw in j_lower for kw in _TIER1_KEYWORDS):
            tier1_journals.append((journal, count))
        elif any(kw in j_lower for kw in _TIER2_KEYWORDS):
            tier2_journals.append((journal, count))
        else:
            other_journals.append((journal, count))

    if tier1_journals:
        sections.append("│ HIGH-IMPACT JOURNALS (publishing on this topic):")
        for j, c in tier1_journals[:10]:
            sections.append(f"│   • {j} ({c} recent articles)")

    if tier2_journals:
        sections.append("│ MID-TIER JOURNALS:")
        for j, c in tier2_journals[:8]:
            sections.append(f"│   • {j} ({c} recent articles)")

    if other_journals:
        sections.append("│ ADDITIONAL JOURNALS:")
        for j, c in other_journals[:7]:
            sections.append(f"│   • {j} ({c} recent articles)")

    return "\n".join(sections) if sections else "│ No journal data available."


def _format_guidelines(guideline_data: List[Dict[str, str]]) -> str:
    """Format clinical guideline references."""
    sections = []

    for g in guideline_data[:8]:
        title = g.get("title", "Untitled")[:120]
        journal = g.get("journal", "")
        year = g.get("year", "")
        pmid = g.get("pmid", "")

        sections.append(f"│ • {title}")
        sections.append(f"│   {journal} ({year})  PMID: {pmid}")

        abstract = g.get("abstract", "")
        if abstract:
            sentences = re.split(r'(?<=[.!?])\s+', abstract)
            # Extract recommendation sentences
            rec_keywords = ["recommend", "guideline", "standard", "should", "consensus"]
            for s in sentences:
                if any(kw in s.lower() for kw in rec_keywords):
                    sections.append(f"│   → {s.strip()[:200]}")
                    break

    return "\n".join(sections) if sections else "│ No clinical guidelines found."


def _format_datasets(dataset_data: List[Dict[str, str]]) -> str:
    """Format dataset references."""
    sections = []

    for d in dataset_data[:6]:
        title = d.get("title", "Untitled")[:120]
        journal = d.get("journal", "")
        year = d.get("year", "")
        sections.append(f"│ • {title}")
        sections.append(f"│   {journal} ({year})")

    return "\n".join(sections) if sections else "│ No dataset references found."


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH SOURCES GUIDE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _build_sources_guide(concepts: Dict[str, List[str]], journal_data: Dict[str, int]) -> str:
    """
    Build a dynamic search sources guide based on the PICO concepts
    and discovered journal landscape.
    """
    # Determine topic-specific database recommendations
    primary_organs = ", ".join(concepts.get("organs", ["general"])) or "general"
    primary_methods = ", ".join(concepts.get("methods", ["imaging"])[:3]) or "imaging"

    # Build the top journals list from actual data
    top_journals_str = ""
    if journal_data:
        top_10 = list(journal_data.items())[:10]
        for j, c in top_10:
            top_journals_str += f"│   {j} ({c} recent articles)\n"
    else:
        top_journals_str = "│   (Journal landscape will be populated from search results)\n"

    return f"""\
┌─ SEARCH SOURCES — DYNAMICALLY CONFIGURED FOR THIS RESEARCH TOPIC ────────┐
│                                                                             │
│ Topic Focus: {primary_organs} — {primary_methods}
│                                                                             │
│ MEDICAL DATABASES (primary — highest clinical evidence quality):            │
│   PubMed/MEDLINE   : pubmed.ncbi.nlm.nih.gov — gold standard for           │
│                      clinical & biomedical literature, MeSH indexing        │
│   Europe PMC       : europepmc.org — open access, full-text search,        │
│                      includes preprints + PMC OA content                   │
│   Cochrane Library : cochranelibrary.com — systematic reviews              │
│                                                                             │
│ TECHNICAL/AI DATABASES (primary — AI/ML architecture literature):           │
│   Arxiv            : arxiv.org (cs.CV, cs.AI, eess.IV, cs.LG)             │
│   Semantic Scholar : semanticscholar.org — citation graph, semantic search │
│   IEEE Xplore      : ieeexplore.ieee.org — IEEE TMI, ISBI, MICCAI        │
│                                                                             │
│ TOP JOURNALS PUBLISHING ON THIS TOPIC (from Europe PMC):                   │
{top_journals_str}│                                                                             │
│ PREPRINT SERVERS:                                                            │
│   medRxiv          : medrxiv.org — clinical/epidemiological preprints      │
│   bioRxiv          : biorxiv.org — biological science preprints            │
│   Arxiv eess.IV    : Medical image analysis preprints                      │
│                                                                             │
│ CLINICAL GUIDELINES:                                                        │
│   ACR Appropriateness: acsearch.acr.org                                    │
│   ESMO Guidelines  : esmo.org/guidelines                                   │
│   NCCN Guidelines  : nccn.org/guidelines                                   │
│                                                                             │
│ DATASET REGISTRIES:                                                          │
│   TCIA             : tcia.nci.nih.gov — annotated imaging datasets         │
│   Grand Challenge  : grand-challenge.org — AI benchmark competitions       │
│   PhysioNet        : physionet.org — open datasets                         │
│   Kaggle           : kaggle.com/datasets — community datasets              │
└────────────────────────────────────────────────────────────────────────────┘
"""


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BUILDER FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def build_knowledge_base(pico: Dict[str, str], cache_hours: int = 72) -> Tuple[str, str]:
    """
    Build a dynamic, PICO-specific knowledge base by querying online
    medical databases.

    This replaces the static _RADIOLOGY_KNOWLEDGE_BASE and _SEARCH_SOURCES_GUIDE
    with dynamically fetched, topic-relevant content.

    Args:
        pico: PICO dict with keys: population, intervention, comparison,
              outcome, hypothesis (and optionally question).
        cache_hours: How long to cache fetched data (default 72 hours).

    Returns:
        Tuple of (knowledge_base_text, search_sources_guide_text).

    The knowledge base includes:
        1. Organ anatomy & physiology (from Wikipedia)
        2. Pathology descriptions (from Wikipedia + MeSH)
        3. MeSH terminology hierarchy (from NCBI MeSH)
        4. State-of-the-art findings (from PubMed reviews)
        5. Method trends (from Semantic Scholar)
        6. Journal landscape (from Europe PMC)
        7. Clinical guidelines (from PubMed)
        8. Dataset references (from PubMed)
    """
    # Check for a complete cached build
    pico_hash = hashlib.md5(json.dumps(pico, sort_keys=True).encode()).hexdigest()[:16]
    full_cache_key = f"full_kb_{pico_hash}.json"
    cached_full = _get_cached(full_cache_key, max_age_hours=cache_hours)
    if cached_full and isinstance(cached_full, dict):
        kb = cached_full.get("knowledge_base", "")
        sg = cached_full.get("sources_guide", "")
        if kb and sg:
            logger.info("Using fully cached knowledge base")
            print("[KnowledgeBuilder] Using cached knowledge base.")
            return kb, sg

    print("[KnowledgeBuilder] Building dynamic knowledge base from online sources...")
    print("[KnowledgeBuilder] This may take 1-3 minutes on first run (results are cached).")

    # ── Step 1: Extract concepts from PICO ────────────────────────────────────
    print("[KnowledgeBuilder] Step 1/7: Extracting concepts from PICO...")
    concepts = extract_concepts_from_pico(pico)
    print(f"  Organs: {concepts['organs']}")
    print(f"  Pathologies: {concepts['pathologies'][:5]}...")
    print(f"  Modalities: {concepts['modalities']}")
    print(f"  Methods: {concepts['methods'][:5]}...")

    # ── Step 2: Fetch Wikipedia summaries ─────────────────────────────────────
    print("[KnowledgeBuilder] Step 2/7: Fetching Wikipedia medical knowledge...")
    wiki_data = fetch_wikipedia_knowledge(concepts)
    print(f"  Retrieved {len(wiki_data)} Wikipedia articles")

    # ── Step 3: Fetch MeSH terminology ────────────────────────────────────────
    print("[KnowledgeBuilder] Step 3/7: Fetching MeSH terminology hierarchy...")
    mesh_data = fetch_mesh_knowledge(concepts)
    print(f"  Retrieved {len(mesh_data)} MeSH records")

    # ── Step 4: Fetch PubMed review articles ──────────────────────────────────
    print("[KnowledgeBuilder] Step 4/7: Fetching PubMed review articles...")
    review_data = fetch_review_knowledge(concepts, pico)
    total_reviews = sum(len(v) for v in review_data.values())
    print(f"  Retrieved {total_reviews} review articles across {len(review_data)} topics")

    # ── Step 5: Fetch Semantic Scholar method trends ──────────────────────────
    print("[KnowledgeBuilder] Step 5/7: Fetching method trends from Semantic Scholar...")
    trend_data = fetch_method_trends(concepts)
    total_trends = sum(len(v) for v in trend_data.values())
    print(f"  Retrieved {total_trends} trending papers across {len(trend_data)} topics")

    # ── Step 6: Fetch journal landscape from Europe PMC ───────────────────────
    print("[KnowledgeBuilder] Step 6/7: Discovering journal landscape...")
    journal_data = fetch_journal_landscape(concepts)
    print(f"  Discovered {len(journal_data)} publishing journals")

    # ── Step 7: Fetch clinical guidelines & dataset references ────────────────
    print("[KnowledgeBuilder] Step 7/7: Fetching guidelines and dataset references...")
    guideline_data = fetch_clinical_guidelines(concepts)
    dataset_data = fetch_dataset_references(concepts)
    print(f"  Retrieved {len(guideline_data)} guidelines, {len(dataset_data)} dataset refs")

    # ── Assemble the knowledge base ───────────────────────────────────────────
    primary_organ = concepts["organs"][0] if concepts["organs"] else "Medical"
    primary_pathology = concepts["pathologies"][0] if concepts["pathologies"] else "Disease"

    knowledge_base = f"""\
════════════════════════════════════════════════════════════════════════════════
DYNAMICALLY BUILT DOMAIN KNOWLEDGE BASE
(Auto-generated from PubMed, MeSH, Wikipedia, Semantic Scholar, Europe PMC)
Focus: {primary_organ} — {primary_pathology} — {', '.join(concepts['modalities'][:3])}
════════════════════════════════════════════════════════════════════════════════

{_fmt_section(
    "ANATOMY, PATHOLOGY & CLINICAL CONTEXT (Wikipedia + MeSH)",
    _format_wiki_knowledge(wiki_data, concepts),
)}

{_fmt_section(
    "MeSH CONTROLLED VOCABULARY & TERMINOLOGY HIERARCHY (NCBI MeSH)",
    _format_mesh_knowledge(mesh_data),
)}

{_fmt_section(
    "STATE-OF-THE-ART EVIDENCE (PubMed Systematic Reviews & Meta-Analyses)",
    _format_review_knowledge(review_data),
)}

{_fmt_section(
    "METHOD & APPROACH TRENDS (Semantic Scholar — Highly Cited)",
    _format_method_trends(trend_data),
)}

{_fmt_section(
    "JOURNAL PUBLICATION LANDSCAPE (Europe PMC)",
    _format_journal_landscape(journal_data),
)}

{_fmt_section(
    "CLINICAL GUIDELINES & PRACTICE PARAMETERS (PubMed Guidelines)",
    _format_guidelines(guideline_data),
)}

{_fmt_section(
    "BENCHMARK DATASETS & REFERENCE COLLECTIONS",
    _format_datasets(dataset_data),
)}

════════════════════════════════════════════════════════════════════════════════
NOTE: This knowledge base was built dynamically from live queries to medical
databases. Content reflects the current published literature and terminology.
Cached for {cache_hours} hours. Delete .knowledge_cache/ to force refresh.
════════════════════════════════════════════════════════════════════════════════
"""

    sources_guide = _build_sources_guide(concepts, journal_data)

    # ── Cache the complete build ──────────────────────────────────────────────
    _set_cache(full_cache_key, {
        "knowledge_base": knowledge_base,
        "sources_guide": sources_guide,
        "concepts": concepts,
        "build_stats": {
            "wiki_articles": len(wiki_data),
            "mesh_records": len(mesh_data),
            "review_articles": total_reviews,
            "trend_papers": total_trends,
            "journals_discovered": len(journal_data),
            "guidelines": len(guideline_data),
            "dataset_refs": len(dataset_data),
        },
    })

    print(f"\n[KnowledgeBuilder] Knowledge base built successfully:")
    print(f"  Wikipedia articles:   {len(wiki_data)}")
    print(f"  MeSH records:         {len(mesh_data)}")
    print(f"  Review articles:      {total_reviews}")
    print(f"  Trending papers:      {total_trends}")
    print(f"  Journals discovered:  {len(journal_data)}")
    print(f"  Clinical guidelines:  {len(guideline_data)}")
    print(f"  Dataset references:   {len(dataset_data)}")
    print(f"  Knowledge base size:  {len(knowledge_base):,} characters")
    print(f"  Cached to: {CACHE_DIR / full_cache_key}\n")

    return knowledge_base, sources_guide


def clear_cache() -> int:
    """
    Clear all cached knowledge data. Returns number of files removed.
    """
    count = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
            count += 1
    logger.info(f"Cleared {count} cached files from {CACHE_DIR}")
    return count
