"""
╔══════════════════════════════════════════════════════════════════════════════╗
║     Literature Review & Planning Pipeline Benchmark — Full Integration       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Benchmarks the complete 3-phase planner WITH literature review enabled:     ║
║    Phase 1 — Hypothesis Structuring (PICO format)                            ║
║    Phase 2 — Literature Review & Knowledge Building (7 data sources)         ║
║    Phase 3 — Pipeline Planning (knowledge-informed)                          ║
║                                                                              ║
║  Evaluates:                                                                  ║
║    • PICO hypothesis quality & alignment with published ground truth         ║
║    • Knowledge base completeness (all 7 sections from 5 databases)           ║
║    • Literature context persistence (literature_context.json)                 ║
║    • Task message quality (context augmentation populated)                    ║
║    • Pipeline plan correctness (right agents, right order)                    ║
║    • Phase-by-phase timing breakdown                                         ║
║    • Reproducibility across cases                                            ║
║                                                                              ║
║  Test Cases (4 published radiomics studies):                                 ║
║    1. Heart  (MRI – Cardiomyopathies vs Normal)  — Zhang et al. 2023        ║
║    2. Liver  (CT  – Tumor vs Normal)             — Lysdahlgaard et al. 2022 ║
║    3. Prostate (MRI – Cancer vs Normal)          — Kim et al. 2025          ║
║    4. Kidney (CT  – Tumor vs Normal)             — Li et al. 2023           ║
║                                                                              ║
║  Usage:                                                                      ║
║      cd Backend/agent_system                                                 ║
║      python -m benchmarking.benchmark_literature_review                      ║
║                                                                              ║
║      Run specific cases:                                                     ║
║      python -m benchmarking.benchmark_literature_review --cases heart liver   ║
║                                                                              ║
║      Clear knowledge cache before running:                                   ║
║      python -m benchmarking.benchmark_literature_review --clear-cache         ║
║                                                                              ║
║      Compare against a previous run (without literature review):             ║
║      python -m benchmarking.benchmark_literature_review --compare-dir         ║
║          benchmark_results/run_2026-03-01_23-09-12                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_SYSTEM_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(AGENT_SYSTEM_DIR))

from agents.planning_agents.planner import ClinicalPlanner
from agents.literature_review_agent.knowledge_builder import clear_cache as clear_kb_cache
try:
    from .llm_judge import JudgeConfig, approx_tokens_from_text, call_judge
except ImportError:
    from llm_judge import JudgeConfig, approx_tokens_from_text, call_judge


def _default_judge_model() -> str:
    return (
        os.getenv("JUDGE_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or os.getenv("MODEL_NAME")
        or "llama3.1:8b"
    )


def _normalize_judge_base_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if not url:
        return "http://localhost:11434/v1"
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _write_judge_reports(output_dir: Path, judge_config: JudgeConfig, preset: str = "single_rubric") -> Optional[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []

    for child in sorted(output_dir.iterdir()):
        if not child.is_dir():
            continue
        judge_path = child / "judge_result.json"
        if not judge_path.exists():
            continue
        try:
            with open(judge_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        result_payload = data.get("result") if isinstance(data.get("result"), dict) else {}
        summaries.append({
            "case_id": child.name,
            "case_path": str(child),
            "ok": bool(data.get("ok")),
            "error": data.get("error"),
            "overall_score": result_payload.get("overall_score"),
            "rubric_scores": result_payload.get("rubric_scores", {}),
            "dimension_scores": result_payload.get("dimension_scores", {}),
            "strengths": result_payload.get("strengths", []),
            "weaknesses": result_payload.get("weaknesses", []),
            "recommendations": result_payload.get("recommendations", []),
            "verdict": result_payload.get("verdict", ""),
            "usage": data.get("usage", {}),
        })

    if not summaries:
        return None

    valid_scores = [s.get("overall_score") for s in summaries if s.get("overall_score") is not None]
    avg_score = round(sum(float(v) for v in valid_scores) / len(valid_scores), 3) if valid_scores else None

    report = {
        "input_dir": str(output_dir),
        "model": judge_config.model,
        "base_url": _normalize_judge_base_url(judge_config.base_url),
        "preset": preset,
        "rubrics": ["Literature-informed planning quality"],
        "cases_evaluated": len(summaries),
        "cases_ok": sum(1 for s in summaries if s.get("ok")),
        "average_overall_score": avg_score,
        "summaries": summaries,
        "token_usage": {
            "prompt_tokens": sum(int((s.get("usage") or {}).get("prompt_tokens", 0)) for s in summaries),
            "completion_tokens": sum(int((s.get("usage") or {}).get("completion_tokens", 0)) for s in summaries),
            "total_tokens": sum(int((s.get("usage") or {}).get("total_tokens", 0)) for s in summaries),
        },
    }

    with open(output_dir / "judge_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [
        "# LLM Judge Report",
        "",
        f"- Input folder: {output_dir}",
        f"- Model: {judge_config.model}",
        f"- Endpoint: {_normalize_judge_base_url(judge_config.base_url)}",
        f"- Preset: {preset}",
        f"- Cases evaluated: {report['cases_evaluated']}",
        f"- Cases OK: {report['cases_ok']}",
        f"- Average overall score: {report['average_overall_score']}",
        "",
        "| Case | Score | Status |",
        "|------|-------|--------|",
    ]
    for s in summaries:
        score = s.get("overall_score")
        score_text = f"{float(score):.3f}" if score is not None else "-"
        status = "ok" if s.get("ok") else f"error: {s.get('error') or 'unknown'}"
        lines.append(f"| {s.get('case_id')} | {score_text} | {status} |")

    with open(output_dir / "judge_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return report

# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK TEST CASES  (same 4 cases as benchmark_planning.py)
# ══════════════════════════════════════════════════════════════════════════════

BENCHMARK_CASES: Dict[str, Dict[str, Any]] = {
    "heart": {
        "name": "Heart MRI — Cardiomyopathies vs Normal",
        "paper": "Zhang et al. (2023) - Cardiac magnetic resonance radiomics for disease classification",
        "pubmed": "https://pubmed.ncbi.nlm.nih.gov/36378251/",
        "dataset": "ACDC Challenge (283 subjects: 48 HCM, 52 DCM, 123 healthy)",
        "prompt": (
            "Can cardiac MRI radiomics features classify different types of "
            "cardiomyopathy? I want to use cine CMR images to distinguish "
            "hypertrophic cardiomyopathy, dilated cardiomyopathy, and normal "
            "hearts using texture and shape features with machine learning."
        ),
        "ground_truth": {
            "organ": "Heart",
            "modality": "MRI",
            "modality_detail": "cine CMR / SSFP",
            "pathologies": ["Hypertrophic Cardiomyopathy (HCM)", "Dilated Cardiomyopathy (DCM)", "Normal"],
            "best_model": "Random Forest + mRMR",
            "best_accuracy": 0.912,
            "best_aucs": {"Normal": 0.938, "HCM": 0.966, "DCM": 0.936},
            "n_subjects": 283,
            "task_type": "multi-class classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": [
                "latex_reporter_agent",
            ],
            "expected_kb_keywords": ["cardiomyopathy", "CMR", "GLCM", "radiomics", "myocardium"],
        },
    },
    "liver": {
        "name": "Liver CT — Tumor vs Normal",
        "paper": "Lysdahlgaard et al. (2022) - Comparing Radiomics features of tumour and healthy liver tissue",
        "pubmed": "https://www.sciencedirect.com/science/article/pii/S1078817422000499",
        "dataset": "LiTS Challenge (131 contrast-enhanced liver CT scans)",
        "prompt": (
            "Can CT radiomics distinguish hepatocellular carcinoma from normal "
            "liver tissue? I want to extract texture and intensity features from "
            "contrast-enhanced CT scans and classify tumor versus healthy liver "
            "parenchyma using machine learning."
        ),
        "ground_truth": {
            "organ": "Liver",
            "modality": "CT",
            "modality_detail": "contrast-enhanced CT",
            "pathologies": ["Hepatocellular Carcinoma (HCC)", "Normal liver"],
            "best_model": "Logistic Regression / Random Forest / SVM",
            "best_accuracy": 0.96,
            "n_subjects": 131,
            "task_type": "binary classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": [
                "latex_reporter_agent",
            ],
            "expected_kb_keywords": ["liver", "hepatocellular", "CT", "radiomics", "texture"],
        },
    },
    "prostate": {
        "name": "Prostate MRI — Cancer vs Normal",
        "paper": "Kim et al. (2025) - Radiomics Reproducibility in Prostate Cancer Diagnosis",
        "pubmed": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12688314/",
        "dataset": "PROSTATEx Challenge (82 men: 41 cancer, 41 normal)",
        "prompt": (
            "Can multiparametric MRI radiomics detect prostate cancer? I want to "
            "extract features from T2-weighted and ADC maps of prostate MRI to "
            "classify cancer versus normal tissue, focusing on reproducible "
            "features with high ICC."
        ),
        "ground_truth": {
            "organ": "Prostate",
            "modality": "MRI",
            "modality_detail": "mpMRI (T2W + ADC)",
            "pathologies": ["Prostate Cancer", "Normal"],
            "best_model": "SVM / Neural Net / Logistic Regression",
            "best_accuracy": 0.84,
            "best_auc": 0.85,
            "n_subjects": 82,
            "task_type": "binary classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": [
                "latex_reporter_agent",
            ],
            "expected_kb_keywords": ["prostate", "MRI", "ADC", "radiomics", "cancer"],
        },
    },
    "kidney": {
        "name": "Kidney CT — Tumor vs Normal",
        "paper": "Li et al. (2023) - Automatic classification of kidney tumor and normal kidney tissue",
        "pubmed": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9998940/",
        "dataset": "KiTS19 / C4KC-KITS (210 patients: 168 train, 42 test)",
        "prompt": (
            "Can CT radiomics classify kidney tumors versus normal kidney tissue? "
            "I want to extract radiomic features from arterial-phase CT scans of "
            "renal masses and contralateral normal kidneys to build a classification "
            "model for automatic kidney tumor detection."
        ),
        "ground_truth": {
            "organ": "Kidney",
            "modality": "CT",
            "modality_detail": "arterial-phase CT",
            "pathologies": ["Renal Cell Carcinoma", "Normal kidney"],
            "best_model": "Logistic Regression (3 features)",
            "best_accuracy": 0.85,
            "best_auc": 0.85,
            "n_subjects": 210,
            "task_type": "binary classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": [
                "latex_reporter_agent",
            ],
            "expected_kb_keywords": ["kidney", "renal", "CT", "radiomics", "tumor"],
        },
    },
}

# Expected knowledge base sections produced by knowledge_builder.py
EXPECTED_KB_SECTIONS = [
    "ANATOMY, PATHOLOGY & CLINICAL CONTEXT",
    "MeSH CONTROLLED VOCABULARY",
    "STATE-OF-THE-ART EVIDENCE",
    "METHOD & APPROACH TRENDS",
    "JOURNAL PUBLICATION LANDSCAPE",
    "CLINICAL GUIDELINES",
    "BENCHMARK DATASETS",
]


def _usage_from_obj(obj: Any) -> Optional[Dict[str, int]]:
    if obj is None:
        return None

    def _get(o: Any, keys: List[str]) -> Optional[int]:
        for key in keys:
            if isinstance(o, dict) and key in o and o[key] is not None:
                try:
                    return int(o[key])
                except (TypeError, ValueError):
                    continue
            if hasattr(o, key):
                val = getattr(o, key)
                if val is not None:
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        continue
        return None

    prompt_tokens = _get(obj, ["prompt_tokens", "input_tokens"])
    completion_tokens = _get(obj, ["completion_tokens", "output_tokens"])
    total_tokens = _get(obj, ["total_tokens"])

    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None

    p = prompt_tokens or 0
    c = completion_tokens or 0
    t = total_tokens if total_tokens is not None else (p + c)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}


def _extract_usage_from_response(response: Any) -> Optional[Dict[str, int]]:
    candidates: List[Any] = [
        getattr(response, "usage", None),
        getattr(response, "models_usage", None),
    ]

    chat_message = getattr(response, "chat_message", None)
    if chat_message is not None:
        candidates.extend([
            getattr(chat_message, "usage", None),
            getattr(chat_message, "models_usage", None),
        ])

    for candidate in candidates:
        usage = _usage_from_obj(candidate)
        if usage:
            return usage

    inner_messages = getattr(response, "inner_messages", None) or []
    aggregate = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    found = False
    for msg in inner_messages:
        for candidate in (getattr(msg, "usage", None), getattr(msg, "models_usage", None)):
            usage = _usage_from_obj(candidate)
            if usage:
                found = True
                aggregate["prompt_tokens"] += usage["prompt_tokens"]
                aggregate["completion_tokens"] += usage["completion_tokens"]
                aggregate["total_tokens"] += usage["total_tokens"]
    return aggregate if found else None


def _attach_agent_usage_wrapper(agent: Any, agent_name: str, usage_calls: List[Dict[str, Any]]) -> None:
    original_on_messages = agent.on_messages

    async def wrapped_on_messages(messages, cancellation_token=None):
        started = time.time()
        response = await original_on_messages(messages, cancellation_token=cancellation_token)
        elapsed = round(time.time() - started, 3)

        prompt_text = "\n".join(str(getattr(m, "content", "")) for m in (messages or []))
        completion_text = str(getattr(getattr(response, "chat_message", None), "content", ""))

        usage = _extract_usage_from_response(response)
        if usage:
            token_source = "actual"
            prompt_tokens = usage["prompt_tokens"]
            completion_tokens = usage["completion_tokens"]
            total_tokens = usage["total_tokens"]
        else:
            token_source = "estimated"
            prompt_tokens = approx_tokens_from_text(prompt_text)
            completion_tokens = approx_tokens_from_text(completion_text)
            total_tokens = prompt_tokens + completion_tokens

        usage_calls.append({
            "agent": agent_name,
            "elapsed_s": elapsed,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "token_source": token_source,
        })
        return response

    agent.on_messages = wrapped_on_messages


def _summarize_usage_calls(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt_tokens = sum(c.get("prompt_tokens", 0) for c in calls)
    completion_tokens = sum(c.get("completion_tokens", 0) for c in calls)
    total_tokens = sum(c.get("total_tokens", 0) for c in calls)
    actual_calls = sum(1 for c in calls if c.get("token_source") == "actual")
    estimated_calls = sum(1 for c in calls if c.get("token_source") == "estimated")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "calls_count": len(calls),
        "actual_calls": actual_calls,
        "estimated_calls": estimated_calls,
        "calls": calls,
    }


def _truncate_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated for judge]"


def _merge_usage(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, int]:
    return {
        "prompt_tokens": int(a.get("prompt_tokens", 0)) + int(b.get("prompt_tokens", 0)),
        "completion_tokens": int(a.get("completion_tokens", 0)) + int(b.get("completion_tokens", 0)),
        "total_tokens": int(a.get("total_tokens", 0)) + int(b.get("total_tokens", 0)),
    }


def _to_score(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# EVALUATION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_hypothesis(hypothesis: Dict, ground_truth: Dict) -> Dict[str, Any]:
    """
    Score how well the generated PICO hypothesis aligns with the ground truth.
    Returns a dict of scores and details.
    """
    scores = {}
    details = {}

    # ── 1. PICO completeness ──────────────────────────────────────────────
    pico_fields = ["population", "intervention", "comparison", "outcome",
                   "hypothesis", "question", "clinical_context"]
    filled = sum(1 for f in pico_fields if hypothesis.get(f))
    scores["pico_completeness"] = round(filled / len(pico_fields), 3)
    details["pico_filled_fields"] = [f for f in pico_fields if hypothesis.get(f)]
    details["pico_missing_fields"] = [f for f in pico_fields if not hypothesis.get(f)]

    # ── 2. Organ mention ──────────────────────────────────────────────────
    hyp_text = json.dumps(hypothesis).lower()
    organ = ground_truth["organ"].lower()
    scores["organ_mentioned"] = 1.0 if organ in hyp_text else 0.0

    # ── 3. Modality mention ───────────────────────────────────────────────
    modality = ground_truth["modality"].lower()
    scores["modality_mentioned"] = 1.0 if modality in hyp_text else 0.0

    # ── 4. Pathology coverage ─────────────────────────────────────────────
    gt_pathologies = ground_truth.get("pathologies", [])
    if gt_pathologies:
        matched = 0
        for path in gt_pathologies:
            # Check for any word from the pathology name (at least 4 chars)
            words = [w.lower() for w in path.split() if len(w) >= 4]
            if any(w in hyp_text for w in words):
                matched += 1
        scores["pathology_coverage"] = round(matched / len(gt_pathologies), 3)
        details["pathologies_found"] = matched
        details["pathologies_total"] = len(gt_pathologies)
    else:
        scores["pathology_coverage"] = 0.0

    # ── 5. Task type alignment ────────────────────────────────────────────
    task_type = ground_truth.get("task_type", "").lower()
    scores["task_type_aligned"] = 1.0 if task_type and any(
        w in hyp_text for w in task_type.split()
    ) else 0.0

    # ── 6. Relationship type present ──────────────────────────────────────
    scores["has_relationship_type"] = 1.0 if hypothesis.get("relationship_type") else 0.0

    # ── Overall hypothesis score ──────────────────────────────────────────
    weights = {
        "pico_completeness": 0.30,
        "organ_mentioned": 0.15,
        "modality_mentioned": 0.15,
        "pathology_coverage": 0.20,
        "task_type_aligned": 0.10,
        "has_relationship_type": 0.10,
    }
    scores["overall"] = round(
        sum(scores[k] * weights[k] for k in weights), 3
    )
    
    return {"scores": scores, "details": details}


def evaluate_knowledge_base(
    knowledge_base: str,
    sources_guide: str,
    ground_truth: Dict,
) -> Dict[str, Any]:
    """
    Evaluate the quality of the dynamically-built knowledge base.
    Checks section completeness, keyword coverage, and size.
    """
    scores = {}
    details = {}

    # ── 1. Section completeness ───────────────────────────────────────────
    kb_upper = knowledge_base.upper()
    sections_found = []
    sections_missing = []
    for section in EXPECTED_KB_SECTIONS:
        if section.upper() in kb_upper:
            sections_found.append(section)
        else:
            sections_missing.append(section)
    
    scores["section_completeness"] = round(
        len(sections_found) / len(EXPECTED_KB_SECTIONS), 3
    ) if EXPECTED_KB_SECTIONS else 0.0
    details["sections_found"] = sections_found
    details["sections_missing"] = sections_missing

    # ── 2. Knowledge base size ────────────────────────────────────────────
    kb_chars = len(knowledge_base)
    # A good KB is typically 15K-40K chars; score proportionally
    if kb_chars >= 15000:
        scores["kb_size"] = min(1.0, kb_chars / 25000)
    elif kb_chars >= 5000:
        scores["kb_size"] = round(kb_chars / 15000, 3)
    else:
        scores["kb_size"] = round(kb_chars / 5000 * 0.3, 3)
    details["kb_chars"] = kb_chars

    # ── 3. Sources guide present and substantial ──────────────────────────
    sg_chars = len(sources_guide)
    scores["sources_guide_present"] = 1.0 if sg_chars > 500 else round(sg_chars / 500, 3)
    details["sources_guide_chars"] = sg_chars

    # ── 4. Keyword coverage (domain-specific terms) ───────────────────────
    expected_keywords = ground_truth.get("expected_kb_keywords", [])
    kb_lower = knowledge_base.lower()
    if expected_keywords:
        found_kw = [kw for kw in expected_keywords if kw.lower() in kb_lower]
        scores["keyword_coverage"] = round(len(found_kw) / len(expected_keywords), 3)
        details["keywords_found"] = found_kw
        details["keywords_missing"] = [kw for kw in expected_keywords if kw.lower() not in kb_lower]
    else:
        scores["keyword_coverage"] = 0.0

    # ── 5. Contains PubMed references ─────────────────────────────────────
    pubmed_refs = len(re.findall(r'PMID[:\s]*\d+', knowledge_base, re.IGNORECASE))
    doi_refs = len(re.findall(r'10\.\d{4,}/', knowledge_base))
    scores["has_references"] = 1.0 if (pubmed_refs + doi_refs) > 0 else 0.0
    details["pubmed_refs_found"] = pubmed_refs
    details["doi_refs_found"] = doi_refs

    # ── 6. Contains organ-specific content ────────────────────────────────
    organ = ground_truth["organ"].lower()
    organ_mentions = kb_lower.count(organ)
    scores["organ_relevance"] = min(1.0, organ_mentions / 10)
    details["organ_mentions"] = organ_mentions

    # ── Overall KB score ──────────────────────────────────────────────────
    weights = {
        "section_completeness": 0.25,
        "kb_size": 0.15,
        "sources_guide_present": 0.10,
        "keyword_coverage": 0.20,
        "has_references": 0.15,
        "organ_relevance": 0.15,
    }
    scores["overall"] = round(
        sum(scores[k] * weights[k] for k in weights), 3
    )

    return {"scores": scores, "details": details}


def evaluate_literature_context(workspace_dir: Path) -> Dict[str, Any]:
    """
    Check that the literature_context.json was saved correctly in the workspace.
    This validates the planner → reporter data flow.
    """
    lit_path = workspace_dir / "literature_context.json"
    result = {
        "file_exists": False,
        "valid_json": False,
        "has_knowledge_base": False,
        "has_sources_guide": False,
        "has_hypothesis": False,
        "knowledge_base_chars": 0,
        "sources_guide_chars": 0,
        "score": 0.0,
    }

    if not lit_path.exists():
        return result

    result["file_exists"] = True

    try:
        with open(lit_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result["valid_json"] = True
    except (json.JSONDecodeError, OSError):
        return result

    kb = data.get("knowledge_base", "")
    sg = data.get("sources_guide", "")
    hyp = data.get("hypothesis", {})

    result["has_knowledge_base"] = bool(kb)
    result["has_sources_guide"] = bool(sg)
    result["has_hypothesis"] = bool(hyp) and isinstance(hyp, dict) and bool(hyp.get("title") or hyp.get("hypothesis"))
    result["knowledge_base_chars"] = len(kb)
    result["sources_guide_chars"] = len(sg)

    # Score: 5 checks, each worth 0.2
    checks = [
        result["file_exists"],
        result["valid_json"],
        result["has_knowledge_base"],
        result["has_sources_guide"],
        result["has_hypothesis"],
    ]
    result["score"] = round(sum(checks) / len(checks), 3)

    return result


def evaluate_literature_outputs(workspace_dir: Path) -> Dict[str, Any]:
    """
    Validate literature_review_output artifacts produced by run_literature_review.
    """
    out_dir = workspace_dir / "literature_review_output"
    context_path = out_dir / "context.json"
    report_path = out_dir / "literature_report.md"
    ranked_path = out_dir / "ranked_papers.json"

    result: Dict[str, Any] = {
        "output_dir_exists": out_dir.exists(),
        "context_json_exists": context_path.exists(),
        "report_md_exists": report_path.exists(),
        "ranked_json_exists": ranked_path.exists(),
        "context_valid_json": False,
        "ranked_valid_json": False,
        "context_top_papers_count": 0,
        "ranked_papers_count": 0,
        "report_chars": 0,
        "score": 0.0,
    }

    if report_path.exists():
        try:
            result["report_chars"] = len(report_path.read_text(encoding="utf-8"))
        except OSError:
            pass

    if context_path.exists():
        try:
            data = json.loads(context_path.read_text(encoding="utf-8"))
            result["context_valid_json"] = True
            top_papers = data.get("top_papers", [])
            if isinstance(top_papers, list):
                result["context_top_papers_count"] = len(top_papers)
        except (json.JSONDecodeError, OSError):
            pass

    if ranked_path.exists():
        try:
            ranked = json.loads(ranked_path.read_text(encoding="utf-8"))
            result["ranked_valid_json"] = isinstance(ranked, list)
            result["ranked_papers_count"] = len(ranked) if isinstance(ranked, list) else 0
        except (json.JSONDecodeError, OSError):
            pass

    checks = [
        result["output_dir_exists"],
        result["context_json_exists"],
        result["report_md_exists"],
        result["ranked_json_exists"],
        result["context_valid_json"],
        result["ranked_valid_json"],
        result["report_chars"] > 0,
    ]
    result["score"] = round(sum(bool(c) for c in checks) / len(checks), 3)
    return result


def evaluate_plan(plan: Dict, ground_truth: Dict) -> Dict[str, Any]:
    """
    Evaluate the generated pipeline plan against expected agents.
    """
    scores = {}
    details = {}

    steps = plan.get("steps", [])
    scores["has_steps"] = 1.0 if steps else 0.0
    details["num_steps"] = len(steps)

    # ── Agent coverage ────────────────────────────────────────────────────
    expected_agents = ground_truth.get("expected_agents_required") or ground_truth.get("expected_agents", [])
    optional_agents = ground_truth.get("expected_agents_optional", [])
    if expected_agents and steps:
        assigned_modules = [s.get("module", "") for s in steps]
        matched = [a for a in expected_agents if a in assigned_modules]
        scores["agent_coverage"] = round(len(matched) / len(expected_agents), 3)
        optional_matched = [a for a in optional_agents if a in assigned_modules]
        scores["optional_agent_coverage"] = (
            round(len(optional_matched) / len(optional_agents), 3)
            if optional_agents else 1.0
        )
        details["expected_agents"] = expected_agents
        details["optional_agents"] = optional_agents
        details["matched_agents"] = matched
        details["optional_matched_agents"] = optional_matched
        details["missing_agents"] = [a for a in expected_agents if a not in assigned_modules]
        details["extra_agents"] = [
            a for a in assigned_modules
            if a not in expected_agents and a not in optional_agents
        ]
    else:
        scores["agent_coverage"] = 0.0
        scores["optional_agent_coverage"] = 0.0

    # ── Plan confirmed ────────────────────────────────────────────────────
    scores["plan_confirmed"] = 1.0 if plan.get("state") == "confirmed" else 0.0

    # ── Step names are descriptive ────────────────────────────────────────
    if steps:
        descriptive = sum(1 for s in steps if len(s.get("name", "")) > 10)
        scores["step_names_quality"] = round(descriptive / len(steps), 3)
    else:
        scores["step_names_quality"] = 0.0

    # ── Step descriptions are descriptive ─────────────────────────────────
    if steps:
        described = sum(1 for s in steps if len(s.get("description", "")) > 30)
        scores["step_descriptions_quality"] = round(described / len(steps), 3)
    else:
        scores["step_descriptions_quality"] = 0.0

    # ── Overall plan score ────────────────────────────────────────────────
    weights = {
        "has_steps": 0.15,
        "agent_coverage": 0.35,
        "optional_agent_coverage": 0.05,
        "plan_confirmed": 0.20,
        "step_names_quality": 0.15,
        "step_descriptions_quality": 0.10,
    }
    scores["overall"] = round(
        sum(scores[k] * weights[k] for k in weights), 3
    )

    return {"scores": scores, "details": details}


def evaluate_task_messages(task_messages: List[Dict], ground_truth: Dict) -> Dict[str, Any]:
    """
    Evaluate the quality of context-enriched task messages.
    """
    scores = {}
    details = {}

    n = len(task_messages)
    scores["count"] = n
    details["expected_count"] = len(ground_truth.get("expected_agents", []))

    if n == 0:
        scores["overall"] = 0.0
        return {"scores": scores, "details": details}

    # ── 1. All have instructions ──────────────────────────────────────────
    with_instructions = sum(1 for tm in task_messages if len(tm.get("instructions", "")) > 50)
    scores["has_instructions"] = round(with_instructions / n, 3)

    # ── 2. Instructions are substantial ───────────────────────────────────
    instruction_lengths = [len(tm.get("instructions", "")) for tm in task_messages]
    avg_len = sum(instruction_lengths) / n if n else 0
    scores["avg_instruction_length"] = round(avg_len)
    scores["instructions_substantial"] = 1.0 if avg_len > 500 else round(avg_len / 500, 3)

    # ── 3. Have hypothesis summary ────────────────────────────────────────
    with_hyp = sum(1 for tm in task_messages if len(tm.get("hypothesis_summary", "")) > 50)
    scores["has_hypothesis_summary"] = round(with_hyp / n, 3)

    # ── 4. Context augmentation populated ─────────────────────────────────
    ca_scores = []
    for tm in task_messages:
        ca = tm.get("context_augmentation", {})
        ca_fields = ["knowledge_summary", "literature_findings", "methodology_guidance",
                     "terminology", "sources_guide"]
        filled = sum(1 for f in ca_fields if ca.get(f, ""))
        ca_scores.append(filled / len(ca_fields))
    
    scores["context_augmentation_avg"] = round(
        sum(ca_scores) / len(ca_scores), 3
    ) if ca_scores else 0.0

    # ── 5. At least some agents have populated context ────────────────────
    agents_with_context = sum(1 for s in ca_scores if s > 0)
    scores["agents_with_context"] = agents_with_context
    scores["context_coverage"] = round(agents_with_context / n, 3) if n else 0.0

    # ── 6. Phase assignments present ──────────────────────────────────────
    with_phase = sum(1 for tm in task_messages if tm.get("phase"))
    scores["has_phases"] = round(with_phase / n, 3)

    # ── 7. Unique agent IDs ───────────────────────────────────────────────
    agent_ids = [tm.get("agent_id", "") for tm in task_messages]
    unique_agents = len(set(a for a in agent_ids if a))
    scores["unique_agents"] = unique_agents

    # ── Overall task message score ────────────────────────────────────────
    weights = {
        "has_instructions": 0.20,
        "instructions_substantial": 0.15,
        "has_hypothesis_summary": 0.15,
        "context_augmentation_avg": 0.20,
        "context_coverage": 0.15,
        "has_phases": 0.10,
    }
    scores["overall"] = round(
        sum(scores.get(k, 0) * weights[k] for k in weights), 3
    )

    details["per_agent_context"] = [
        {
            "agent_id": tm.get("agent_id", "unknown"),
            "instructions_len": len(tm.get("instructions", "")),
            "context_fill_ratio": ca_scores[i] if i < len(ca_scores) else 0,
        }
        for i, tm in enumerate(task_messages)
    ]

    return {"scores": scores, "details": details}


# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK HARNESS
# ══════════════════════════════════════════════════════════════════════════════

class LiteratureBenchmarkCollector:
    """Collects events from a planner run and tracks phase timing."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.events: List[Dict[str, Any]] = []
        self._start_time: float = time.time()
        self._phase_times: Dict[str, float] = {}
        self._current_phase: Optional[str] = None
        self._phase_start: float = 0.0
        self._latest_event_type: str = ""
        self._latest_event_data: Any = None
        self._latest_plan_steps: int = 0
        self._plan_refinement_attempts: int = 0

    async def output_callback(self, type_: str, data: Any):
        """Captures every event and tracks phase boundaries."""
        elapsed = round(time.time() - self._start_time, 2)
        event = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_s": elapsed,
            "type": type_,
            "data": data if isinstance(data, (str, int, float, bool, list, dict, type(None))) else str(data),
        }
        self.events.append(event)
        self._latest_event_type = type_
        self._latest_event_data = data

        if type_ == "plan" and isinstance(data, dict):
            steps = data.get("steps", [])
            self._latest_plan_steps = len(steps) if isinstance(steps, list) else 0

        # Track phase transitions
        if type_ == "msg" and isinstance(data, str):
            if "**Phase 1:" in data:
                self._start_phase("phase_1_hypothesis")
            elif "**Phase 2:" in data:
                self._end_phase()
                self._start_phase("phase_2_literature_review")
            elif "**Phase 3:" in data:
                self._end_phase()
                self._start_phase("phase_3_pipeline_planning")
            elif "Pipeline confirmed" in data:
                self._end_phase()
                self._start_phase("finalization")

        if type_ == "modules_matched":
            self._end_phase()

        # Live progress
        preview = str(data)[:120] if data else ""
        print(f"  [{elapsed:7.1f}s] {type_:20s} | {preview}")

    def _start_phase(self, phase_name: str):
        self._current_phase = phase_name
        self._phase_start = time.time()

    def _end_phase(self):
        if self._current_phase:
            self._phase_times[self._current_phase] = round(
                time.time() - self._phase_start, 2
            )
            self._current_phase = None

    @property
    def phase_times(self) -> Dict[str, float]:
        # Close any open phase
        if self._current_phase:
            self._phase_times[self._current_phase] = round(
                time.time() - self._phase_start, 2
            )
        return self._phase_times

    async def input_provider(self) -> str:
        """Context-aware auto-responses for stable benchmark behavior."""
        if self._latest_event_type == "lit_review_choice":
            return "YES"

        if self._latest_event_type == "hypothesis_approval":
            return "APPROVE"

        if self._latest_event_type == "msg" and isinstance(self._latest_event_data, str):
            text = self._latest_event_data.lower()
            if "review the pipeline plan" in text:
                if self._latest_plan_steps == 0 and self._plan_refinement_attempts < 2:
                    self._plan_refinement_attempts += 1
                    return (
                        "Do not ask clarifying questions. Use the confirmed hypothesis and literature context already "
                        "provided to produce a complete plan with 4-6 steps including study_population_planner_agent, "
                        "segmentation_agent, feature_extraction_agent, modeling_agent, and result_reporter_agent when "
                        "appropriate. Return strictly valid JSON only and escape backslashes."
                    )
                return "APPROVE"

        return "APPROVE"


async def run_single_case(
    case_id: str,
    case: Dict[str, Any],
    output_dir: Path,
    api_key: str,
    judge_config: Optional[JudgeConfig] = None,
) -> Dict[str, Any]:
    """Run the planner on a single benchmark case and collect all metrics."""
    print(f"\n{'═' * 78}")
    print(f"  BENCHMARK CASE: {case['name']}")
    print(f"  Paper: {case['paper']}")
    print(f"{'═' * 78}")
    print(f"  Prompt: {case['prompt'][:100]}...")
    print(f"{'─' * 78}\n")

    case_dir = output_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    collector = LiteratureBenchmarkCollector(case_id)
    session_id = f"litbench_{case_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # The planner creates workspace_ + session_id
    workspace_dir = AGENT_SYSTEM_DIR.parent / f"workspace_{session_id}"

    # Configure logging
    case_log = case_dir / "planner.log"
    case_logger = logging.getLogger(f"litbench.{case_id}")
    case_logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(case_log, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    case_logger.addHandler(fh)

    start_time = time.time()

    planner = ClinicalPlanner(
        input_provider=collector.input_provider,
        output_callback=collector.output_callback,
        api_key=api_key,
        session_id=session_id,
    )

    # Capture planner LLM token usage by wrapping planning agents.
    planner_usage_calls: List[Dict[str, Any]] = []
    _attach_agent_usage_wrapper(planner.agents["hypothesis_architect"], "hypothesis_architect", planner_usage_calls)
    _attach_agent_usage_wrapper(planner.agents["pipeline_architect"], "pipeline_architect", planner_usage_calls)

    # Resolve the actual workspace dir from planner's state_path
    actual_workspace_dir = planner.state_path.parent

    error_msg = None
    try:
        await planner.run(start_query=case["prompt"])
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        case_logger.error(f"Planner crashed: {error_msg}", exc_info=True)
        print(f"\n  *** ERROR: {error_msg}")

    elapsed = round(time.time() - start_time, 2)

    # ══════════════════════════════════════════════════════════════════════
    # SAVE ALL RAW OUTPUT
    # ══════════════════════════════════════════════════════════════════════

    # 1. Events log
    with open(case_dir / "events.json", "w", encoding="utf-8") as f:
        json.dump(collector.events, f, indent=2, ensure_ascii=False, default=str)

    # 2. Final planner state
    with open(case_dir / "final_state.json", "w", encoding="utf-8") as f:
        f.write(planner.state.model_dump_json(indent=2))

    # 3. Hypothesis
    with open(case_dir / "hypothesis.json", "w", encoding="utf-8") as f:
        json.dump(planner.state.hypothesis.model_dump(), f, indent=2, ensure_ascii=False)

    # 4. Plan
    with open(case_dir / "plan.json", "w", encoding="utf-8") as f:
        json.dump(planner.state.plan.model_dump(), f, indent=2, ensure_ascii=False)

    # 5. Task messages
    with open(case_dir / "task_messages.json", "w", encoding="utf-8") as f:
        json.dump(
            [tm.model_dump() for tm in planner.state.task_messages],
            f, indent=2, ensure_ascii=False,
        )

    # 6. Ground truth
    with open(case_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(case["ground_truth"], f, indent=2, ensure_ascii=False)

    # 7. Input prompt
    with open(case_dir / "input_prompt.txt", "w", encoding="utf-8") as f:
        f.write(case["prompt"])

    # 8. Agent responses (subset of events)
    agent_responses = [
        e for e in collector.events
        if e["type"] in ("hypothesis", "plan", "task_messages", "modules_matched")
    ]
    with open(case_dir / "agent_responses.json", "w", encoding="utf-8") as f:
        json.dump(agent_responses, f, indent=2, ensure_ascii=False, default=str)

    # 9. Knowledge base text (for offline analysis)
    if planner.state.knowledge_base:
        with open(case_dir / "knowledge_base.txt", "w", encoding="utf-8") as f:
            f.write(planner.state.knowledge_base)

    # 10. Sources guide text
    if planner.state.sources_guide:
        with open(case_dir / "sources_guide.txt", "w", encoding="utf-8") as f:
            f.write(planner.state.sources_guide)

    # 11. Phase timing
    with open(case_dir / "phase_timing.json", "w", encoding="utf-8") as f:
        json.dump(collector.phase_times, f, indent=2)

    # 12. Snapshot generated literature-review artifacts from workspace
    lit_out_dir = actual_workspace_dir / "literature_review_output"
    lit_snapshot_dir = case_dir / "literature_review_output"
    lit_snapshot_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("context.json", "literature_report.md", "ranked_papers.json"):
        src = lit_out_dir / fname
        if src.exists():
            try:
                content = src.read_text(encoding="utf-8")
                (lit_snapshot_dir / fname).write_text(content, encoding="utf-8")
            except OSError:
                pass

    # 13. Token usage (planner-side)
    planner_usage = _summarize_usage_calls(planner_usage_calls)

    # ══════════════════════════════════════════════════════════════════════
    # RUN EVALUATIONS
    # ══════════════════════════════════════════════════════════════════════

    gt = case["ground_truth"]

    # Hypothesis evaluation
    hyp_eval = evaluate_hypothesis(planner.state.hypothesis.model_dump(), gt)

    # Knowledge base evaluation
    kb_eval = evaluate_knowledge_base(
        planner.state.knowledge_base,
        planner.state.sources_guide,
        gt,
    )

    # Literature context persistence
    lit_ctx_eval = evaluate_literature_context(actual_workspace_dir)

    # New literature-review output artifacts from workspace/literature_review_output
    lit_out_eval = evaluate_literature_outputs(actual_workspace_dir)

    # Plan evaluation
    plan_eval = evaluate_plan(planner.state.plan.model_dump(), gt)

    # Task messages evaluation
    tm_eval = evaluate_task_messages(
        [tm.model_dump() for tm in planner.state.task_messages], gt,
    )

    # Save evaluations
    evaluations = {
        "hypothesis": hyp_eval,
        "knowledge_base": kb_eval,
        "literature_context": lit_ctx_eval,
        "literature_outputs": lit_out_eval,
        "plan": plan_eval,
        "task_messages": tm_eval,
    }
    with open(case_dir / "evaluations.json", "w", encoding="utf-8") as f:
        json.dump(evaluations, f, indent=2, ensure_ascii=False)

    # 14. Optional LLM-as-judge scoring
    judge_result = None
    judge_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "token_source": "none",
    }
    if judge_config is not None:
        judge_artifacts = {
            "prompt": case["prompt"],
            "ground_truth": gt,
            "heuristic_evaluations": evaluations,
            "hypothesis": planner.state.hypothesis.model_dump(),
            "plan": planner.state.plan.model_dump(),
            "task_messages": [
                {
                    "task_id": tm.task_id,
                    "phase": tm.phase,
                    "agent_id": tm.agent_id,
                    "instructions": _truncate_text(tm.instructions, 900),
                    "hypothesis_summary": _truncate_text(tm.hypothesis_summary, 700),
                    "context_augmentation": {
                        "knowledge_summary": _truncate_text(tm.context_augmentation.knowledge_summary or "", 1200),
                        "literature_findings": _truncate_text(tm.context_augmentation.literature_findings or "", 1200),
                        "methodology_guidance": _truncate_text(tm.context_augmentation.methodology_guidance or "", 1200),
                        "terminology": _truncate_text(tm.context_augmentation.terminology or "", 800),
                    },
                }
                for tm in planner.state.task_messages
            ],
            "knowledge_base_excerpt": _truncate_text(planner.state.knowledge_base, 12000),
            "sources_guide_excerpt": _truncate_text(planner.state.sources_guide, 5000),
        }
        judge_result = call_judge(
            config=judge_config,
            rubric_name="Literature-informed planning quality",
            case_id=case_id,
            case_name=case["name"],
            artifacts=judge_artifacts,
        )
        judge_usage = judge_result.get("usage", judge_usage)
        with open(case_dir / "judge_result.json", "w", encoding="utf-8") as f:
            json.dump(judge_result, f, indent=2, ensure_ascii=False)

    with open(case_dir / "token_usage.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "planner": planner_usage,
                "judge": judge_usage,
                "total": _merge_usage(planner_usage, judge_usage),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    # ── Build summary ────────────────────────────────────────────────────
    summary = {
        "case_id": case_id,
        "case_name": case["name"],
        "paper": case["paper"],
        "elapsed_seconds": elapsed,
        "phase_timing": collector.phase_times,
        "hypothesis_confirmed": planner.state.hypothesis.state == "confirmed",
        "plan_confirmed": planner.state.plan.state == "confirmed",
        "num_plan_steps": len(planner.state.plan.steps),
        "num_task_messages": len(planner.state.task_messages),
        "knowledge_base_chars": len(planner.state.knowledge_base),
        "sources_guide_chars": len(planner.state.sources_guide),
        "literature_context_saved": lit_ctx_eval["file_exists"],
        "literature_outputs_saved": bool(
            lit_out_eval.get("context_json_exists")
            and lit_out_eval.get("report_md_exists")
            and lit_out_eval.get("ranked_json_exists")
        ),
        "num_events": len(collector.events),
        "error": error_msg,
        "output_dir": str(case_dir),
        "workspace_dir": str(actual_workspace_dir),
        # Overall scores
        "scores": {
            "hypothesis": hyp_eval["scores"]["overall"],
            "knowledge_base": kb_eval["scores"]["overall"],
            "literature_context": lit_ctx_eval["score"],
            "literature_outputs": lit_out_eval["score"],
            "plan": plan_eval["scores"]["overall"],
            "task_messages": tm_eval["scores"]["overall"],
            "llm_judge": (
                judge_result.get("result", {}).get("overall_score")
                if judge_result and isinstance(judge_result.get("result"), dict)
                else None
            ),
        },
        "llm_judge": {
            "enabled": judge_config is not None,
            "ok": bool(judge_result and judge_result.get("ok")),
            "error": (judge_result.get("error") if isinstance(judge_result, dict) else None),
            "overall_score": (
                judge_result.get("result", {}).get("overall_score")
                if judge_result and isinstance(judge_result.get("result"), dict)
                else None
            ),
        },
        "token_usage": {
            "planner": planner_usage,
            "judge": judge_usage,
            "total": _merge_usage(planner_usage, judge_usage),
        },
    }

    with open(case_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # ── Print result ──────────────────────────────────────────────────────
    print(f"\n{'─' * 78}")
    print(f"  ✓ {case['name']}")
    print(f"  Time: {elapsed:.1f}s | Steps: {summary['num_plan_steps']} | "
          f"Tasks: {summary['num_task_messages']} | KB: {summary['knowledge_base_chars']:,} chars")
    print(
        f"  Tokens: prompt={summary['token_usage']['total']['prompt_tokens']:,} | "
        f"completion={summary['token_usage']['total']['completion_tokens']:,} | "
        f"total={summary['token_usage']['total']['total_tokens']:,}"
    )
    if judge_config is not None:
        print(f"  LLM Judge: {summary['llm_judge']['overall_score']}")
    print(f"  Phase Timing: {json.dumps(collector.phase_times)}")
    print(f"  Scores:")
    for k, v in summary["scores"].items():
        score_v = _to_score(v, 0.0)
        bar = "█" * int(score_v * 20) + "░" * (20 - int(score_v * 20))
        print(f"    {k:<25s} {bar} {score_v:.3f}")
    if error_msg:
        print(f"  ERROR: {error_msg}")
    print(f"  Output: {case_dir}")
    print(f"{'─' * 78}\n")

    case_logger.removeHandler(fh)
    fh.close()

    return summary


def load_comparison_run(compare_dir: Path) -> Optional[Dict[str, Any]]:
    """Load a previous benchmark run for comparison."""
    report_path = compare_dir / "benchmark_report.json"
    if not report_path.exists():
        print(f"  WARNING: No benchmark_report.json found in {compare_dir}")
        return None

    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_comparison_section(
    current_summaries: List[Dict],
    compare_data: Dict,
) -> str:
    """Generate a markdown comparison between current and previous run."""
    lines = [
        "\n## Comparison with Previous Run\n",
        f"**Previous run:** {compare_data['run_timestamp']}",
        f"**KB enabled in previous:** {'No' if compare_data.get('skip_kb') else 'Yes'}\n",
        "| Case | Prev Time | Curr Time | Prev KB | Curr KB | Prev Steps | Curr Steps | Prev Tasks | Curr Tasks |",
        "|------|-----------|-----------|---------|---------|------------|------------|------------|------------|",
    ]

    prev_by_id = {s["case_id"]: s for s in compare_data.get("summaries", [])}

    for curr in current_summaries:
        cid = curr["case_id"]
        prev = prev_by_id.get(cid)
        if prev:
            lines.append(
                f"| {cid} "
                f"| {prev['elapsed_seconds']:.1f}s "
                f"| {curr['elapsed_seconds']:.1f}s "
                f"| {prev['knowledge_base_chars']:,} "
                f"| {curr['knowledge_base_chars']:,} "
                f"| {prev['num_plan_steps']} "
                f"| {curr['num_plan_steps']} "
                f"| {prev['num_task_messages']} "
                f"| {curr['num_task_messages']} |"
            )
        else:
            lines.append(f"| {cid} | N/A | {curr['elapsed_seconds']:.1f}s | N/A | {curr['knowledge_base_chars']:,} | N/A | {curr['num_plan_steps']} | N/A | {curr['num_task_messages']} |")

    return "\n".join(lines)


async def run_benchmark(
    cases: List[str],
    clear_cache: bool = False,
    compare_dir: Optional[Path] = None,
    llm_judge: bool = False,
    judge_config: Optional[JudgeConfig] = None,
    judge_preset: str = "single_rubric",
):
    """Run the full benchmark suite."""
    api_key = os.getenv(
        "OLLAMA_API_KEY",
        "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge",
    )

    # Optionally clear the knowledge cache
    if clear_cache:
        removed = clear_kb_cache()
        print(f"\n  Cleared {removed} cached knowledge files.\n")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = SCRIPT_DIR / "benchmark_results" / f"litreview_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load comparison data if requested
    compare_data = None
    if compare_dir:
        compare_data = load_comparison_run(compare_dir)
        if compare_data:
            print(f"  Loaded comparison run: {compare_data['run_timestamp']}")

    print("\n" + "═" * 78)
    print("  LITERATURE REVIEW & PLANNING PIPELINE BENCHMARK")
    print(f"  Cases: {', '.join(cases)}")
    print(f"  Output: {output_dir}")
    print(f"  Cache cleared: {clear_cache}")
    print(f"  Comparing against: {compare_dir or 'None'}")
    print(f"  LLM Judge: {llm_judge}")
    print("═" * 78 + "\n")

    summaries: List[Dict[str, Any]] = []
    total_start = time.time()

    for case_id in cases:
        if case_id not in BENCHMARK_CASES:
            print(f"  WARNING: Unknown case '{case_id}', skipping.")
            continue

        summary = await run_single_case(
            case_id=case_id,
            case=BENCHMARK_CASES[case_id],
            output_dir=output_dir,
            api_key=api_key,
            judge_config=judge_config if llm_judge else None,
        )
        summaries.append(summary)

    total_elapsed = round(time.time() - total_start, 2)

    # ══════════════════════════════════════════════════════════════════════════
    # AGGREGATE SCORES
    # ══════════════════════════════════════════════════════════════════════════

    score_categories = ["hypothesis", "knowledge_base", "literature_context", "literature_outputs", "plan", "task_messages"]
    include_judge_category = bool(
        llm_judge and any(s.get("llm_judge", {}).get("ok") for s in summaries)
    )
    if include_judge_category:
        score_categories.append("llm_judge")
    avg_scores = {}
    for cat in score_categories:
        vals = [
            _to_score(s.get("scores", {}).get(cat), default=0.0)
            for s in summaries
            if s.get("scores") and s.get("scores", {}).get(cat) is not None
        ]
        avg_scores[cat] = round(sum(vals) / len(vals), 3) if vals else 0.0
    avg_scores["overall"] = round(
        sum(v for k, v in avg_scores.items() if k != "overall")
        / max(1, len([k for k in avg_scores.keys() if k != "overall"])),
        3,
    ) if avg_scores else 0.0

    # ══════════════════════════════════════════════════════════════════════════
    # SAVE OVERALL REPORT
    # ══════════════════════════════════════════════════════════════════════════

    report = {
        "run_timestamp": timestamp,
        "total_elapsed_seconds": total_elapsed,
        "cases_run": len(summaries),
        "cache_cleared": clear_cache,
        "compare_dir": str(compare_dir) if compare_dir else None,
        "llm_judge": llm_judge,
        "average_scores": avg_scores,
        "summaries": summaries,
        "token_usage": {
            "prompt_tokens": sum(s.get("token_usage", {}).get("total", {}).get("prompt_tokens", 0) for s in summaries),
            "completion_tokens": sum(s.get("token_usage", {}).get("total", {}).get("completion_tokens", 0) for s in summaries),
            "total_tokens": sum(s.get("token_usage", {}).get("total", {}).get("total_tokens", 0) for s in summaries),
        },
    }
    with open(output_dir / "benchmark_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ══════════════════════════════════════════════════════════════════════════
    # PRINT FINAL REPORT
    # ══════════════════════════════════════════════════════════════════════════

    print("\n" + "═" * 78)
    print("  LITERATURE REVIEW BENCHMARK — FINAL REPORT")
    print("═" * 78)
    print(f"  Total time: {total_elapsed:.1f}s")
    print(f"  Cases run:  {len(summaries)}")
    print()

    # Per-case table
    header = (
        f"  {'Case':<12} {'Time':>7} {'Steps':>6} {'Tasks':>6} "
        f"{'KB':>8} {'Tokens':>10} {'Hyp':>6} {'KB':>6} {'Lit':>6} {'Out':>6} {'Plan':>6} {'Tasks':>6} {'Judge':>6} {'Status':<7}"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))
    for s in summaries:
        status = "OK" if not s["error"] else "ERROR"
        sc = s["scores"]
        print(
            f"  {s['case_id']:<12} "
            f"{s['elapsed_seconds']:>6.1f}s "
            f"{s['num_plan_steps']:>6} "
            f"{s['num_task_messages']:>6} "
            f"{s['knowledge_base_chars']:>7,} "
            f"{s.get('token_usage', {}).get('total', {}).get('total_tokens', 0):>10,} "
            f"{sc['hypothesis']:>5.2f} "
            f"{sc['knowledge_base']:>5.2f} "
            f"{sc['literature_context']:>5.2f} "
            f"{sc['literature_outputs']:>5.2f} "
            f"{sc['plan']:>5.2f} "
            f"{sc['task_messages']:>5.2f} "
            f"{(sc['llm_judge'] if sc.get('llm_judge') is not None else 'N/A'):>6} "
            f"{status:<7}"
        )

    # Average scores with visual bars
    print(f"\n  AVERAGE SCORES:")
    for cat in score_categories:
        v = avg_scores[cat]
        bar = "█" * int(v * 20) + "░" * (20 - int(v * 20))
        print(f"    {cat:<25s} {bar} {v:.3f}")
    overall = avg_scores["overall"]
    bar = "█" * int(overall * 20) + "░" * (20 - int(overall * 20))
    print(f"    {'OVERALL':<25s} {bar} {overall:.3f}")
    print(
        f"\n  Total tokens: {report['token_usage']['total_tokens']:,} "
        f"(prompt={report['token_usage']['prompt_tokens']:,}, completion={report['token_usage']['completion_tokens']:,})"
    )
    if llm_judge and not include_judge_category:
        first_error = next(
            (
                s.get("llm_judge", {}).get("error")
                for s in summaries
                if s.get("llm_judge", {}).get("error")
            ),
            "No successful judge responses.",
        )
        print(f"  WARNING: LLM judge enabled but no successful judge responses. Example error: {first_error}")

    # Phase timing averages
    all_phases = set()
    for s in summaries:
        all_phases.update(s.get("phase_timing", {}).keys())
    if all_phases:
        print(f"\n  AVERAGE PHASE TIMING:")
        for phase in sorted(all_phases):
            times = [s["phase_timing"].get(phase, 0) for s in summaries if s.get("phase_timing")]
            avg_t = sum(times) / len(times) if times else 0
            print(f"    {phase:<35s} {avg_t:>7.1f}s")

    print(f"\n  Full report: {output_dir / 'benchmark_report.json'}")

    # ══════════════════════════════════════════════════════════════════════════
    # GENERATE MARKDOWN REPORT
    # ══════════════════════════════════════════════════════════════════════════

    md_path = output_dir / "benchmark_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Literature Review & Planning Pipeline Benchmark\n\n")
        f.write(f"**Date:** {timestamp}\n")
        f.write(f"**Total Time:** {total_elapsed:.1f}s\n")
        f.write(f"**Cases:** {len(summaries)}\n")
        f.write(f"**Cache Cleared:** {clear_cache}\n\n")
        f.write(f"**LLM Judge:** {'Enabled' if llm_judge else 'Disabled'}\n")
        f.write(
            f"**Total Tokens:** {report['token_usage']['total_tokens']:,} "
            f"(prompt={report['token_usage']['prompt_tokens']:,}, completion={report['token_usage']['completion_tokens']:,})\n\n"
        )

        # Average scores
        f.write("## Average Scores\n\n")
        f.write("| Category | Score |\n")
        f.write("|----------|-------|\n")
        for cat in score_categories:
            f.write(f"| {cat.replace('_', ' ').title()} | {avg_scores[cat]:.3f} |\n")
        f.write(f"| **Overall** | **{avg_scores['overall']:.3f}** |\n\n")

        # Summary table
        f.write("## Per-Case Summary\n\n")
        f.write("| Case | Paper | Time | Steps | Tasks | KB Size | Tokens | Hyp | KB | Lit | Out | Plan | Tasks | Judge | Status |\n")
        f.write("|------|-------|------|-------|-------|---------|--------|-----|-----|-----|-----|------|-------|-------|--------|\n")
        for s in summaries:
            status = "✅" if not s["error"] else f"❌ {s['error'][:30]}"
            sc = s["scores"]
            f.write(
                f"| {s['case_id']} | {s['case_name'][:35]} | {s['elapsed_seconds']:.1f}s "
                f"| {s['num_plan_steps']} | {s['num_task_messages']} "
                f"| {s['knowledge_base_chars']:,} "
                f"| {s.get('token_usage', {}).get('total', {}).get('total_tokens', 0):,} "
                f"| {sc['hypothesis']:.2f} | {sc['knowledge_base']:.2f} "
                f"| {sc['literature_context']:.2f} | {sc['literature_outputs']:.2f} "
                f"| {sc['plan']:.2f} "
                f"| {sc['task_messages']:.2f} | {sc['llm_judge'] if sc.get('llm_judge') is not None else 'N/A'} | {status} |\n"
            )

        # Phase timing
        f.write("\n## Phase Timing Breakdown\n\n")
        f.write("| Case |")
        sorted_phases = sorted(all_phases)
        for phase in sorted_phases:
            f.write(f" {phase} |")
        f.write("\n|------|")
        for _ in sorted_phases:
            f.write("--------|")
        f.write("\n")
        for s in summaries:
            f.write(f"| {s['case_id']} |")
            for phase in sorted_phases:
                t = s.get("phase_timing", {}).get(phase, 0)
                f.write(f" {t:.1f}s |")
            f.write("\n")

        # Per-case details
        f.write("\n## Per-Case Details\n\n")
        for s in summaries:
            case = BENCHMARK_CASES[s["case_id"]]
            f.write(f"### {s['case_name']}\n\n")
            f.write(f"- **Paper:** {case['paper']}\n")
            f.write(f"- **Prompt:** {case['prompt']}\n")
            f.write(f"- **Time:** {s['elapsed_seconds']:.1f}s\n")
            f.write(f"- **Plan Steps:** {s['num_plan_steps']}\n")
            f.write(f"- **Task Messages:** {s['num_task_messages']}\n")
            f.write(f"- **Knowledge Base:** {s['knowledge_base_chars']:,} chars\n")
            f.write(
                f"- **Tokens:** {s.get('token_usage', {}).get('total', {}).get('total_tokens', 0):,} "
                f"(prompt={s.get('token_usage', {}).get('total', {}).get('prompt_tokens', 0):,}, "
                f"completion={s.get('token_usage', {}).get('total', {}).get('completion_tokens', 0):,})\n"
            )
            if llm_judge:
                f.write(f"- **LLM Judge Score:** {s.get('scores', {}).get('llm_judge')}\n")
            f.write(f"- **Literature Context Saved:** {'Yes' if s['literature_context_saved'] else 'No'}\n")
            f.write(f"- **Scores:** Hyp={s['scores']['hypothesis']:.3f}, "
                    f"KB={s['scores']['knowledge_base']:.3f}, "
                    f"Lit={s['scores']['literature_context']:.3f}, "
                    f"Out={s['scores']['literature_outputs']:.3f}, "
                    f"Plan={s['scores']['plan']:.3f}, "
                    f"Tasks={s['scores']['task_messages']:.3f}, "
                    f"Judge={s['scores']['llm_judge'] if s['scores'].get('llm_judge') is not None else 'N/A'}\n")
            f.write(f"- **Literature Outputs Saved:** {'Yes' if s.get('literature_outputs_saved') else 'No'}\n")
            if s["error"]:
                f.write(f"- **Error:** {s['error']}\n")
            f.write(f"- **Output:** `{s['output_dir']}`\n\n")

            # Phase timing for this case
            if s.get("phase_timing"):
                f.write("  **Phase Timing:**\n")
                for phase, t in s["phase_timing"].items():
                    f.write(f"  - {phase}: {t:.1f}s\n")
                f.write("\n")

        # Comparison section
        if compare_data:
            f.write(generate_comparison_section(summaries, compare_data))
            f.write("\n")

    print(f"  Markdown report: {md_path}")

    if llm_judge and judge_config is not None:
        judge_report = _write_judge_reports(output_dir, judge_config, preset=judge_preset)
        if judge_report is not None:
            print(f"  Judge report: {output_dir / 'judge_report.json'}")
            print(f"  Judge markdown: {output_dir / 'judge_report.md'}")
    print("═" * 78 + "\n")

    return report


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the planner with literature review integration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m benchmarking.benchmark_literature_review                          # All 4 cases
  python -m benchmarking.benchmark_literature_review --cases heart liver      # Specific cases
  python -m benchmarking.benchmark_literature_review --clear-cache            # Fresh KB build
  python -m benchmarking.benchmark_literature_review --compare-dir \\
    benchmark_results/run_2026-03-04_18-51-30                                 # Compare runs
        """,
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=list(BENCHMARK_CASES.keys()),
        default=list(BENCHMARK_CASES.keys()),
        help="Which cases to run (default: all 4)",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the knowledge builder cache before running (forces fresh API calls)",
    )
    parser.add_argument(
        "--compare-dir",
        type=Path,
        default=None,
        help="Path to a previous benchmark run directory to compare against",
    )
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Enable LLM-as-judge scoring for each benchmark case.",
    )
    parser.add_argument(
        "--judge-model",
        default=_default_judge_model(),
        help="Judge model name for OpenAI-compatible chat completions.",
    )
    parser.add_argument(
        "--judge-base-url",
        default=os.getenv("JUDGE_BASE_URL", "http://localhost:11434/v1"),
        help="Judge base URL (OpenAI-compatible).",
    )
    parser.add_argument(
        "--judge-api-key",
        default=os.getenv("JUDGE_API_KEY", os.getenv("QWEN_API_KEY", os.getenv("OLLAMA_API_KEY", ""))),
        help="Judge API key.",
    )
    parser.add_argument(
        "--judge-timeout",
        type=int,
        default=int(os.getenv("JUDGE_TIMEOUT", "180")),
        help="Judge request timeout in seconds.",
    )
    parser.add_argument(
        "--judge-preset",
        default="single_rubric",
        help="Label written into judge_report.json/.md for traceability.",
    )

    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    judge_config = None
    llm_judge_enabled = args.llm_judge
    if args.llm_judge:
        judge_api_key = (args.judge_api_key or "").strip() or "ollama"
        judge_base_url = (args.judge_base_url or "").strip()
        if not judge_base_url:
            judge_base_url = "http://localhost:11434/v1"

        judge_config = JudgeConfig(
            model=args.judge_model,
            base_url=judge_base_url,
            api_key=judge_api_key,
            timeout_seconds=args.judge_timeout,
            temperature=0.0,
        )

    asyncio.run(run_benchmark(
        cases=args.cases,
        clear_cache=args.clear_cache,
        compare_dir=args.compare_dir,
        llm_judge=llm_judge_enabled,
        judge_config=judge_config,
        judge_preset=args.judge_preset,
    ))


if __name__ == "__main__":
    main()
