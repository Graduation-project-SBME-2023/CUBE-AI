"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          Planning Pipeline Benchmark — Published Research Papers             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Runs the 3-phase planning pipeline on 4 published radiomics studies:        ║
║    1. Heart  (MRI – Cardiomyopathies vs Normal)  — Zhang et al. 2023        ║
║    2. Liver  (CT  – Tumor vs Normal)             — Lysdahlgaard et al. 2022 ║
║    3. Prostate (MRI – Cancer vs Normal)          — Kim et al. 2025          ║
║    4. Kidney (CT  – Tumor vs Normal)             — Li et al. 2023           ║
║                                                                              ║
║  For each case the planner auto-approves the hypothesis and pipeline plan,   ║
║  saving full logs, raw agent responses, state snapshots, and timing data.    ║
║                                                                              ║
║  Usage:                                                                      ║
║      cd Backend/agent_system                                                 ║
║      python -m benchmarking.benchmark_planning                               ║
║                                                                              ║
║      Or with specific cases:                                                 ║
║      python -m benchmarking.benchmark_planning --cases heart liver           ║
║                                                                              ║
║      Skip knowledge building (faster, planning-only):                        ║
║      python -m benchmarking.benchmark_planning --skip-kb                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_SYSTEM_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(AGENT_SYSTEM_DIR))

from agents.planning_agents.planner import ClinicalPlanner
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
        "rubrics": ["Planning quality and correctness"],
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

    _safe_json_dump(report, output_dir / "judge_report.json")

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

    (output_dir / "judge_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report

# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK TEST CASES
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
            "modality": "MRI (cine CMR)",
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
            "expected_agents_optional": ["latex_reporter_agent"],
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
            "modality": "CT (contrast-enhanced)",
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
            "expected_agents_optional": ["latex_reporter_agent"],
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
            "modality": "MRI (mpMRI: T2W + ADC)",
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
            "expected_agents_optional": ["latex_reporter_agent"],
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
            "modality": "CT (arterial phase)",
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
            "expected_agents_optional": ["latex_reporter_agent"],
        },
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# JSON SERIALIZATION HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _safe_json_dump(obj: Any, path: Path, indent: int = 2) -> bool:
    """
    Write obj to path as JSON.

    Uses a custom default that converts enums, datetimes, and other
    non-serializable objects to strings so that missing/enum-valued fields
    in Pydantic model_dump() output never silently abort a file write and
    leave downstream files un-written.

    Returns True on success, False on failure (logs the error).
    """
    def _default(o: Any) -> Any:
        # Enum → its value (usually a string/int already); fall back to name
        if hasattr(o, "value"):
            return o.value
        if hasattr(o, "name"):
            return o.name
        # datetime / date → ISO string
        if hasattr(o, "isoformat"):
            return o.isoformat()
        # Pydantic models not yet dumped
        if hasattr(o, "model_dump"):
            return o.model_dump()
        return str(o)

    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=indent, ensure_ascii=False, default=_default)
        return True
    except Exception as exc:
        logging.getLogger("benchmark").error("Failed writing %s: %s", path, exc, exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN USAGE UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

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

    # Some frameworks attach usage per inner message.
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


def _extract_message_text(msg: Any) -> str:
    """
    Safely extract text content from a message object regardless of framework.

    Tries .content first (autogen TextMessage), then .text, then falls back to
    str(). This ensures approx_tokens_from_text gets actual text, not a repr
    like "<TextMessage id=... content='hello'>", which would yield near-zero
    token estimates.
    """
    # Direct string
    if isinstance(msg, str):
        return msg
    # Common attribute names for message content
    for attr in ("content", "text", "body"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val:
            return val
    # Pydantic / dataclass dict
    if hasattr(msg, "model_dump"):
        d = msg.model_dump()
        for key in ("content", "text", "body"):
            if isinstance(d.get(key), str) and d[key]:
                return d[key]
    return str(msg)


def _attach_agent_usage_wrapper(agent: Any, agent_name: str, usage_calls: List[Dict[str, Any]]) -> None:
    """
    Wraps agent.on_messages to capture token usage (actual or estimated) per call.

    FIX — two changes from the original:
      1. cancellation_token is passed positionally, not as a keyword argument,
         so it works regardless of whether the underlying method uses *args or
         an explicit keyword parameter.
      2. Message text extraction uses _extract_message_text() so the estimated
         token count is based on actual content rather than object repr strings.
    """
    original_on_messages = agent.on_messages

    async def wrapped_on_messages(messages, cancellation_token=None):
        started = time.time()
        # FIX 1: pass cancellation_token positionally to stay compatible with
        # frameworks that declare it as a positional-only parameter.
        response = await original_on_messages(messages, cancellation_token)
        elapsed = round(time.time() - started, 3)

        # FIX 2: extract real text, not repr, for the fallback estimator.
        prompt_text = "\n".join(_extract_message_text(m) for m in (messages or []))
        completion_text = _extract_message_text(getattr(response, "chat_message", None) or "")

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


def _evaluate_workspace_literature_outputs(workspace_dir: Path) -> Dict[str, Any]:
    """Check planner-produced literature files in workspace folders."""
    lit_context = workspace_dir / "literature_context.json"
    out_dir = workspace_dir / "literature_review_output"
    result = {
        "literature_context_exists": lit_context.exists(),
        "output_dir_exists": out_dir.exists(),
        "context_json_exists": (out_dir / "context.json").exists(),
        "report_md_exists": (out_dir / "literature_report.md").exists(),
        "ranked_json_exists": (out_dir / "ranked_papers.json").exists(),
    }
    result["all_outputs_exist"] = bool(
        result["context_json_exists"]
        and result["report_md_exists"]
        and result["ranked_json_exists"]
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK HARNESS
# ══════════════════════════════════════════════════════════════════════════════

class BenchmarkCollector:
    """Collects all output events from a single planner run."""

    def __init__(self, case_id: str, enable_lit_review: bool = False):
        self.case_id = case_id
        self.events: List[Dict[str, Any]] = []
        self._start_time: float = time.time()
        self.enable_lit_review = enable_lit_review
        self._latest_event_type: str = ""
        self._latest_event_data: Any = None
        self._latest_plan_steps: int = 0
        self._plan_refinement_attempts: int = 0

    async def output_callback(self, type_: str, data: Any):
        """Captures every event the planner sends to the UI."""
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

        # Print live progress
        preview = str(data)[:120] if data else ""
        print(f"  [{elapsed:7.1f}s] {type_:20s} | {preview}")

    async def input_provider(self) -> str:
        """Context-aware auto-responses for stable benchmark behavior."""
        if self._latest_event_type == "lit_review_choice":
            return "YES" if self.enable_lit_review else "NO"

        if self._latest_event_type == "hypothesis_approval":
            return "APPROVE"

        if self._latest_event_type == "msg" and isinstance(self._latest_event_data, str):
            text = self._latest_event_data.lower()
            if "review the pipeline plan" in text:
                if self._latest_plan_steps == 0 and self._plan_refinement_attempts < 2:
                    self._plan_refinement_attempts += 1
                    return (
                        "Do not ask clarifying questions. Use the confirmed hypothesis above and return a complete "
                        "pipeline with 4-6 steps including study_population_planner_agent, segmentation_agent, "
                        "feature_extraction_agent, modeling_agent, and result_reporter_agent when appropriate. "
                        "Return strictly valid JSON only, and escape all backslashes in text."
                    )
                return "APPROVE"

        return "APPROVE"


async def run_single_benchmark(
    case_id: str,
    case: Dict[str, Any],
    output_dir: Path,
    api_key: str,
    skip_kb: bool = False,
    judge_config: Optional[JudgeConfig] = None,
) -> Dict[str, Any]:
    """
    Run the planner on a single benchmark case and save all outputs.

    Returns a summary dict with timing and result info.
    """
    print(f"\n{'═' * 72}")
    print(f"  BENCHMARK: {case['name']}")
    print(f"  Paper: {case['paper']}")
    print(f"{'═' * 72}")
    print(f"  Prompt: {case['prompt'][:100]}...")
    print(f"{'─' * 72}\n")

    case_dir = output_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    collector = BenchmarkCollector(case_id, enable_lit_review=(not skip_kb))
    session_id = f"benchmark_{case_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Configure logging for this case
    case_log_file = case_dir / "planner.log"
    case_logger = logging.getLogger(f"benchmark.{case_id}")
    case_logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(case_log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    case_logger.addHandler(fh)

    start_time = time.time()

    planner = ClinicalPlanner(
        input_provider=collector.input_provider,
        output_callback=collector.output_callback,
        api_key=api_key,
        session_id=session_id,
    )

    actual_workspace_dir = planner.state_path.parent

    # Capture planner LLM token usage by wrapping planning agents.
    planner_usage_calls: List[Dict[str, Any]] = []
    _attach_agent_usage_wrapper(planner.agents["hypothesis_architect"], "hypothesis_architect", planner_usage_calls)
    _attach_agent_usage_wrapper(planner.agents["pipeline_architect"], "pipeline_architect", planner_usage_calls)

    # If skipping knowledge building, monkey-patch it.
    if skip_kb:
        async def _skip_kb(self_=None):
            print("  [SKIP] Knowledge building skipped (--skip-kb)")
            await collector.output_callback("msg", "[BENCHMARK] Knowledge building skipped.")
            # FIX: update state directly so downstream steps see empty but valid
            # KB strings rather than whatever was there before (or a stale value
            # from a previous call if the method normally mutates self).
            planner.state.knowledge_base = ""
            planner.state.sources_guide = ""
        planner._build_knowledge_base = _skip_kb

    error_msg = None
    try:
        await planner.run(start_query=case["prompt"])
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        case_logger.error(f"Planner crashed: {error_msg}", exc_info=True)
        print(f"\n  *** ERROR: {error_msg}")

    elapsed = round(time.time() - start_time, 2)

    lit_outputs_eval = _evaluate_workspace_literature_outputs(actual_workspace_dir)

    # ── Save all outputs ──────────────────────────────────────────────────
    # FIX: all json.dump calls now go through _safe_json_dump, which handles
    # enums, datetimes, and other non-serializable types that appear in Pydantic
    # model_dump() output.  The original code used json.dump with default=str
    # only on events.json; every other file (hypothesis.json, plan.json,
    # task_messages.json, token_usage.json, summary.json) used a bare json.dump
    # that would raise TypeError on the first enum value it encountered, abort
    # the write, and leave all subsequent files un-written.

    # 1. Events log (everything sent to UI)
    _safe_json_dump(collector.events, case_dir / "events.json")

    # 2. Final planner state
    _safe_json_dump(planner.state.model_dump(), case_dir / "final_state.json")

    # 3. Hypothesis output
    _safe_json_dump(planner.state.hypothesis.model_dump(), case_dir / "hypothesis.json")

    # 4. Plan output
    _safe_json_dump(planner.state.plan.model_dump(), case_dir / "plan.json")

    # 5. Task messages
    _safe_json_dump(
        [tm.model_dump() for tm in planner.state.task_messages],
        case_dir / "task_messages.json",
    )

    # 6. Ground truth reference
    _safe_json_dump(case.get("ground_truth", {}), case_dir / "ground_truth.json")

    # 7. Input prompt
    (case_dir / "input_prompt.txt").write_text(case["prompt"], encoding="utf-8")

    # 8. Raw agent responses (extract from events)
    agent_responses = [
        e for e in collector.events
        if e["type"] in ("hypothesis", "plan", "task_messages", "modules_matched")
    ]
    _safe_json_dump(agent_responses, case_dir / "agent_responses.json")

    # 9. Token usage (planner-side)
    planner_usage = _summarize_usage_calls(planner_usage_calls)

    # 9b. Snapshot generated literature-review artifacts from workspace
    lit_out_dir = actual_workspace_dir / "literature_review_output"
    if lit_out_dir.exists():
        lit_snapshot_dir = case_dir / "literature_review_output"
        lit_snapshot_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("context.json", "literature_report.md", "ranked_papers.json"):
            src = lit_out_dir / fname
            if src.exists():
                try:
                    (lit_snapshot_dir / fname).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass

    # 10. Optional LLM-as-judge scoring
    #
    # FIX: call_judge is a synchronous function.  Calling it directly inside an
    # async function blocks the entire event loop for the duration of the HTTP
    # request, which can cause timeouts in other coroutines and prevents the
    # benchmark from completing cleanly when multiple cases run concurrently.
    # We run it in a thread-pool executor so it is non-blocking.  We also wrap
    # it in a try/except so a judge failure does not abort the rest of the
    # per-case output (the original had no error handling here at all).
    judge_result: Optional[Dict[str, Any]] = None
    judge_usage: Dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "token_source": "none",
    }

    if judge_config is not None:
        judge_artifacts = {
            "prompt": case["prompt"],
            "ground_truth": case.get("ground_truth", {}),
            "hypothesis": planner.state.hypothesis.model_dump(),
            "plan": planner.state.plan.model_dump(),
            "task_messages": [
                {
                    "task_id": tm.task_id,
                    "phase": tm.phase,
                    "agent_id": tm.agent_id,
                    "instructions": _truncate_text(tm.instructions, 900),
                    "hypothesis_summary": _truncate_text(tm.hypothesis_summary, 700),
                }
                for tm in planner.state.task_messages
            ],
        }

        loop = asyncio.get_event_loop()
        try:
            judge_result = await loop.run_in_executor(
                None,  # default ThreadPoolExecutor
                lambda: call_judge(
                    config=judge_config,
                    rubric_name="Planning quality and correctness",
                    case_id=case_id,
                    case_name=case["name"],
                    artifacts=judge_artifacts,
                ),
            )
            judge_usage = judge_result.get("usage", judge_usage)
            print(f"  [judge] score={judge_result.get('result', {}).get('overall_score')} ok={judge_result.get('ok')}")
        except Exception as judge_exc:
            judge_err = f"{type(judge_exc).__name__}: {judge_exc}"
            case_logger.error("LLM judge failed: %s", judge_err, exc_info=True)
            print(f"  [judge] ERROR: {judge_err}")
            judge_result = {"ok": False, "error": judge_err}

        _safe_json_dump(judge_result, case_dir / "judge_result.json")

    _safe_json_dump(
        {
            "planner": planner_usage,
            "judge": judge_usage,
            "total": _merge_usage(planner_usage, judge_usage),
        },
        case_dir / "token_usage.json",
    )

    # ── Summary ───────────────────────────────────────────────────────────
    # FIX: compare hypothesis/plan state robustly against both string and enum
    # representations.  Pydantic models often store state as an enum; a plain
    # == "confirmed" comparison always returns False in that case.
    hyp_state = str(planner.state.hypothesis.state)
    plan_state = str(planner.state.plan.state)

    summary = {
        "case_id": case_id,
        "case_name": case["name"],
        "paper": case["paper"],
        "elapsed_seconds": elapsed,
        "hypothesis_confirmed": hyp_state.lower() in ("confirmed", "state.confirmed"),
        "plan_confirmed": plan_state.lower() in ("confirmed", "state.confirmed"),
        "num_plan_steps": len(planner.state.plan.steps),
        "num_task_messages": len(planner.state.task_messages),
        "knowledge_base_chars": len(planner.state.knowledge_base),
        "sources_guide_chars": len(planner.state.sources_guide),
        "literature_context_saved": lit_outputs_eval["literature_context_exists"],
        "literature_outputs_saved": lit_outputs_eval["all_outputs_exist"],
        "literature_outputs": lit_outputs_eval,
        "num_events": len(collector.events),
        "error": error_msg,
        "output_dir": str(case_dir),
        "workspace_dir": str(actual_workspace_dir),
        "llm_judge": {
            "enabled": judge_config is not None,
            "ok": bool(judge_result and judge_result.get("ok")),
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

    _safe_json_dump(summary, case_dir / "summary.json")

    print(f"\n{'─' * 72}")
    print(f"  DONE: {case['name']}")
    print(f"  Time: {elapsed:.1f}s | Steps: {summary['num_plan_steps']} | "
          f"Tasks: {summary['num_task_messages']} | KB: {summary['knowledge_base_chars']:,} chars")
    print(
        f"  Tokens: prompt={summary['token_usage']['total']['prompt_tokens']:,} | "
        f"completion={summary['token_usage']['total']['completion_tokens']:,} | "
        f"total={summary['token_usage']['total']['total_tokens']:,}"
    )
    if judge_config is not None:
        print(f"  LLM Judge: {summary['llm_judge']['overall_score']}")
    print(f"  Output: {case_dir}")
    if error_msg:
        print(f"  ERROR: {error_msg}")
    print(f"{'─' * 72}\n")

    case_logger.removeHandler(fh)
    fh.close()

    return summary


async def run_all_benchmarks(
    cases: List[str],
    skip_kb: bool = False,
    llm_judge: bool = False,
    judge_config: Optional[JudgeConfig] = None,
    judge_preset: str = "single_rubric",
):
    """Run all selected benchmark cases sequentially."""
    api_key = os.getenv(
        "OLLAMA_API_KEY",
        "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge",
    )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = SCRIPT_DIR / "benchmark_results" / f"run_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "═" * 72)
    print("  PLANNING PIPELINE BENCHMARK")
    print(f"  Cases: {', '.join(cases)}")
    print(f"  Output: {output_dir}")
    print(f"  Skip KB: {skip_kb}")
    print(f"  LLM Judge: {llm_judge}")
    print("═" * 72 + "\n")

    summaries: List[Dict[str, Any]] = []
    total_start = time.time()

    for case_id in cases:
        if case_id not in BENCHMARK_CASES:
            print(f"  WARNING: Unknown case '{case_id}', skipping.")
            continue

        case = BENCHMARK_CASES[case_id]
        summary = await run_single_benchmark(
            case_id=case_id,
            case=case,
            output_dir=output_dir,
            api_key=api_key,
            skip_kb=skip_kb,
            judge_config=judge_config if llm_judge else None,
        )
        summaries.append(summary)

    total_elapsed = round(time.time() - total_start, 2)

    # ── Save overall report ───────────────────────────────────────────────
    # FIX: avg_judge_score now only averages cases that actually have a score.
    # The original used max(1, count) which silently produced 0.0 instead of
    # None when no cases returned a score (e.g. all judge calls failed).
    judge_scores = [
        s["llm_judge"]["overall_score"]
        for s in summaries
        if s.get("llm_judge", {}).get("overall_score") is not None
    ]
    avg_judge_score = round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None

    report = {
        "run_timestamp": timestamp,
        "total_elapsed_seconds": total_elapsed,
        "cases_run": len(summaries),
        "skip_kb": skip_kb,
        "llm_judge": llm_judge,
        "summaries": summaries,
        "token_usage": {
            "prompt_tokens": sum(s.get("token_usage", {}).get("total", {}).get("prompt_tokens", 0) for s in summaries),
            "completion_tokens": sum(s.get("token_usage", {}).get("total", {}).get("completion_tokens", 0) for s in summaries),
            "total_tokens": sum(s.get("token_usage", {}).get("total", {}).get("total_tokens", 0) for s in summaries),
        },
        "avg_judge_score": avg_judge_score,
    }
    _safe_json_dump(report, output_dir / "benchmark_report.json")

    # ── Print final report ────────────────────────────────────────────────
    print("\n" + "═" * 72)
    print("  BENCHMARK REPORT")
    print("═" * 72)
    print(f"  Total time: {total_elapsed:.1f}s")
    print(f"  Cases run:  {len(summaries)}")
    print()

    header = (
        f"  {'Case':<12} {'Time':>7} {'Steps':>6} {'Tasks':>6} {'KB':>8} "
        f"{'LitCtx':>7} {'LitOut':>7} {'Tokens':>10} {'Judge':>7} {'Status':<10}"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))
    for s in summaries:
        status = "OK" if not s["error"] else "ERROR"
        judge_disp = s.get("llm_judge", {}).get("overall_score")
        print(
            f"  {s['case_id']:<12} "
            f"{s['elapsed_seconds']:>6.1f}s "
            f"{s['num_plan_steps']:>6} "
            f"{s['num_task_messages']:>6} "
            f"{s['knowledge_base_chars']:>7,} "
            f"{('Y' if s.get('literature_context_saved') else 'N'):>7} "
            f"{('Y' if s.get('literature_outputs_saved') else 'N'):>7} "
            f"{s.get('token_usage', {}).get('total', {}).get('total_tokens', 0):>10,} "
            f"{(judge_disp if judge_disp is not None else 'N/A'):>7} "
            f"{status:<10}"
        )

    report_path = output_dir / "benchmark_report.json"
    print(f"\n  Full report: {report_path}")
    print(
        f"  Total tokens: {report['token_usage']['total_tokens']:,} "
        f"(prompt={report['token_usage']['prompt_tokens']:,}, completion={report['token_usage']['completion_tokens']:,})"
    )
    if llm_judge:
        print(f"  Average judge score: {avg_judge_score}")

    if llm_judge and judge_config is not None:
        judge_report = _write_judge_reports(output_dir, judge_config, preset=judge_preset)
        if judge_report is not None:
            print(f"  Judge report: {output_dir / 'judge_report.json'}")
            print(f"  Judge markdown: {output_dir / 'judge_report.md'}")
    print("═" * 72 + "\n")

    # Also generate a markdown report
    md_path = output_dir / "benchmark_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Planning Pipeline Benchmark Report\n\n")
        f.write(f"**Date:** {timestamp}\n")
        f.write(f"**Total Time:** {total_elapsed:.1f}s\n")
        f.write(f"**Knowledge Building:** {'Skipped' if skip_kb else 'Enabled'}\n\n")
        f.write(f"**LLM Judge:** {'Enabled' if llm_judge else 'Disabled'}\n")
        f.write(
            f"**Total Tokens:** {report['token_usage']['total_tokens']:,} "
            f"(prompt={report['token_usage']['prompt_tokens']:,}, completion={report['token_usage']['completion_tokens']:,})\n\n"
        )
        if llm_judge:
            f.write(f"**Average Judge Score:** {avg_judge_score}\n\n")

        f.write("## Summary\n\n")
        f.write("| Case | Paper | Time | Steps | Tasks | KB Size | Lit Ctx | Lit Out | Tokens | Judge | Status |\n")
        f.write("|------|-------|------|-------|-------|---------|---------|---------|--------|-------|--------|\n")
        for s in summaries:
            status = "OK" if not s["error"] else f"ERROR: {s['error'][:50]}"
            judge_score = s.get("llm_judge", {}).get("overall_score")
            f.write(
                f"| {s['case_id']} | {s['case_name'][:40]} | {s['elapsed_seconds']:.1f}s "
                f"| {s['num_plan_steps']} | {s['num_task_messages']} "
                f"| {s['knowledge_base_chars']:,} "
                f"| {'Y' if s.get('literature_context_saved') else 'N'} "
                f"| {'Y' if s.get('literature_outputs_saved') else 'N'} "
                f"| {s.get('token_usage', {}).get('total', {}).get('total_tokens', 0):,} "
                f"| {judge_score if judge_score is not None else 'N/A'} "
                f"| {status} |\n"
            )

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
            f.write(f"- **Literature Context Saved:** {'Yes' if s.get('literature_context_saved') else 'No'}\n")
            f.write(f"- **Literature Outputs Saved:** {'Yes' if s.get('literature_outputs_saved') else 'No'}\n")
            f.write(
                f"- **Tokens:** {s.get('token_usage', {}).get('total', {}).get('total_tokens', 0):,} "
                f"(prompt={s.get('token_usage', {}).get('total', {}).get('prompt_tokens', 0):,}, "
                f"completion={s.get('token_usage', {}).get('total', {}).get('completion_tokens', 0):,})\n"
            )
            if llm_judge:
                f.write(f"- **LLM Judge Score:** {s.get('llm_judge', {}).get('overall_score')}\n")
            f.write(f"- **Output:** `{s['output_dir']}`\n")
            if s["error"]:
                f.write(f"- **Error:** {s['error']}\n")
            f.write("\n")

    return report


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the planning pipeline on published radiomics studies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m benchmarking.benchmark_planning                    # Run all 4 cases
  python -m benchmarking.benchmark_planning --cases heart liver  # Run specific cases
  python -m benchmarking.benchmark_planning --skip-kb          # Skip knowledge building
        """,
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=list(BENCHMARK_CASES.keys()),
        default=list(BENCHMARK_CASES.keys()),
        help="Which cases to run (default: all)",
    )
    parser.add_argument(
        "--skip-kb",
        action="store_true",
        help="Skip knowledge building phase (faster, planning-only benchmark)",
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

    asyncio.run(
        run_all_benchmarks(
            cases=args.cases,
            skip_kb=args.skip_kb,
            llm_judge=llm_judge_enabled,
            judge_config=judge_config,
            judge_preset=args.judge_preset,
        )
    )


if __name__ == "__main__":
    main()