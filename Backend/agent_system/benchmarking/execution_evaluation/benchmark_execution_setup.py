"""
Execution benchmark setup utilities.

This module prepares benchmark inputs for execution agents by:
1) Fetching hypothesis + plan + steps from the backend DB API by hypothesis id.
2) Building a timestamped benchmark run directory.
3) Creating per-agent benchmark folders with expected input/output scaffolding.
4) Providing fallback case preparation from old benchmark workspace state files.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_SYSTEM_DIR = SCRIPT_DIR.parent.parent
DEFAULT_OUTPUT_ROOT = SCRIPT_DIR / "runs"
DEFAULT_BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "http://localhost:8000")

AGENT_IDS = [
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
]

# Latest identified fallback case workspaces and hypothesis ids from old runs.
FALLBACK_CASES: Dict[str, Dict[str, str]] = {
    "heart": {
        "hypothesis_id": "3448272d-c16b-463c-ac47-7e069dd089b5",
        "workspace_dir": str(AGENT_SYSTEM_DIR / "workspace_benchmark_heart_20260328_155748"),
    },
    "liver": {
        "hypothesis_id": "056e94c9-7262-409f-8b8d-cf7e967b822d",
        "workspace_dir": str(AGENT_SYSTEM_DIR / "workspace_benchmark_liver_20260328_161341"),
    },
    "prostate": {
        "hypothesis_id": "1c4cd092-0fcd-4d8e-96d7-8d6ac0363467",
        "workspace_dir": str(AGENT_SYSTEM_DIR / "workspace_benchmark_prostate_20260328_164624"),
    },
    "kidney": {
        "hypothesis_id": "5d02ea3b-d729-4af3-af35-3fa74a90f595",
        "workspace_dir": str(AGENT_SYSTEM_DIR / "workspace_benchmark_kidney_20260328_171306"),
    },
}


def _safe_json_dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _normalize_step(step: Dict[str, Any], index: int) -> Dict[str, Any]:
    title = step.get("title") or step.get("name") or f"Step {index + 1}"
    description = step.get("description") or ""
    module = step.get("module") or ""
    status = step.get("status") or "pending"

    return {
        "order": int(step.get("order", index)),
        "title": title,
        "description": description,
        "module": module,
        "status": status,
        "raw": step,
    }


def _build_task_message_from_step(step: Dict[str, Any], index: int) -> Dict[str, Any]:
    # Prefer explicit task_message if present.
    if isinstance(step.get("task_message"), dict):
        return step["task_message"]

    # Some DB payloads store structured instructions under `instructions`.
    if isinstance(step.get("instructions"), dict):
        maybe_task = step["instructions"].get("task_message")
        if isinstance(maybe_task, dict):
            return maybe_task

    normalized = _normalize_step(step, index)
    return {
        "task_id": f"task_{index + 1}",
        "phase": "unknown",
        "agent_id": normalized["module"],
        "agent_title": normalized["title"],
        "instructions": normalized["description"],
        "context_augmentation": {
            "knowledge_summary": "",
            "literature_findings": "",
            "methodology_guidance": "",
            "terminology": "",
            "sources_guide": "",
        },
        "hypothesis_summary": "",
        "payload": {},
    }


def _normalize_plan(plan_payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(plan_payload)
    raw_steps = plan.get("steps") or []
    plan["steps"] = [_normalize_step(step, i) for i, step in enumerate(raw_steps)]
    return plan


def _collect_task_messages(plan_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = plan_payload.get("steps") or []
    return [_build_task_message_from_step(step, i) for i, step in enumerate(steps)]


def _derive_workspace_dir(hypothesis_payload: Dict[str, Any]) -> Optional[str]:
    chat_id = hypothesis_payload.get("chat_id")
    if not chat_id:
        return None

    candidate = AGENT_SYSTEM_DIR / f"workspace_{chat_id}"
    if candidate.exists():
        return str(candidate)
    return None


async def fetch_hypothesis_and_plan_from_db(
    hypothesis_id: str,
    base_url: str = DEFAULT_BACKEND_API_BASE,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=60) as client:
        hyp_resp = await client.get(f"{base}/hypotheses/{hypothesis_id}")
        hyp_resp.raise_for_status()
        hypothesis = hyp_resp.json()

        plan_resp = await client.get(f"{base}/plans/by-hypothesis/{hypothesis_id}")
        plan_resp.raise_for_status()
        plan_payload = plan_resp.json()

    if isinstance(plan_payload, list):
        if not plan_payload:
            raise ValueError(f"No plan found for hypothesis id {hypothesis_id}")
        plan = plan_payload[0]
    elif isinstance(plan_payload, dict):
        plan = plan_payload
    else:
        raise ValueError("Unexpected plan response shape from backend")

    return hypothesis, _normalize_plan(plan)


def load_fallback_case_state(case_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    if case_id not in FALLBACK_CASES:
        allowed = ", ".join(sorted(FALLBACK_CASES.keys()))
        raise ValueError(f"Unknown fallback case '{case_id}'. Allowed: {allowed}")

    workspace_dir = Path(FALLBACK_CASES[case_id]["workspace_dir"])
    state_path = workspace_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"Fallback state file not found: {state_path}")

    with state_path.open("r", encoding="utf-8") as f:
        state = json.load(f)

    hypothesis = state.get("hypothesis") or {}
    plan = _normalize_plan(state.get("plan") or {})
    return hypothesis, plan, str(workspace_dir)


def _ensure_agent_scaffolding(
    run_dir: Path,
    task_messages: List[Dict[str, Any]],
    hypothesis: Dict[str, Any],
) -> None:
    task_by_agent = {
        (tm.get("agent_id") or ""): tm
        for tm in task_messages
        if isinstance(tm, dict)
    }

    for agent_id in AGENT_IDS:
        agent_dir = run_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        expected_input = {
            "hypothesis": {
                "id": hypothesis.get("id"),
                "title": hypothesis.get("title"),
                "state": hypothesis.get("state"),
            },
            "task_message": task_by_agent.get(agent_id),
        }
        expected_output = {
            "agent_id": agent_id,
            "status": "pending",
            "notes": "Fill expected outputs for this benchmark case.",
        }

        _safe_json_dump(expected_input, agent_dir / "expected_input.json")
        _safe_json_dump(expected_output, agent_dir / "expected_output.json")
        _safe_json_dump({"events": []}, agent_dir / "actual_output.json")


def prepare_execution_benchmark_directory(
    hypothesis: Dict[str, Any],
    plan: Dict[str, Any],
    output_root: Path,
    source: str,
    workspace_dir: Optional[str] = None,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)

    hyp_id = str(hypothesis.get("id") or "unknown")
    run_id = f"exec_{_now_stamp()}_{hyp_id[:8]}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    task_messages = _collect_task_messages(plan)

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(),
        "source": source,
        "hypothesis_id": hyp_id,
        "workspace_dir": workspace_dir,
        "plan_state": plan.get("state"),
        "steps_count": len(plan.get("steps") or []),
        "task_messages_count": len(task_messages),
        "agents_detected": sorted(
            {tm.get("agent_id") for tm in task_messages if isinstance(tm, dict) and tm.get("agent_id")}
        ),
    }

    _safe_json_dump(manifest, run_dir / "run_manifest.json")
    _safe_json_dump(hypothesis, run_dir / "hypothesis.json")
    _safe_json_dump(plan, run_dir / "plan.json")
    _safe_json_dump(task_messages, run_dir / "task_messages.json")

    _ensure_agent_scaffolding(run_dir, task_messages, hypothesis)
    return run_dir


async def setup_execution_benchmark_from_hypothesis_id(
    hypothesis_id: str,
    output_root: Optional[str] = None,
    base_url: str = DEFAULT_BACKEND_API_BASE,
) -> Path:
    hypothesis, plan = await fetch_hypothesis_and_plan_from_db(hypothesis_id, base_url=base_url)
    workspace_dir = _derive_workspace_dir(hypothesis)

    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    return prepare_execution_benchmark_directory(
        hypothesis=hypothesis,
        plan=plan,
        output_root=root,
        source="database",
        workspace_dir=workspace_dir,
    )


def setup_execution_benchmark_from_fallback_case(
    case_id: str,
    output_root: Optional[str] = None,
) -> Path:
    hypothesis, plan, workspace_dir = load_fallback_case_state(case_id)

    if not hypothesis.get("id"):
        hypothesis["id"] = FALLBACK_CASES[case_id]["hypothesis_id"]

    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    return prepare_execution_benchmark_directory(
        hypothesis=hypothesis,
        plan=plan,
        output_root=root,
        source=f"fallback:{case_id}",
        workspace_dir=workspace_dir,
    )


def setup_all_fallback_cases(output_root: Optional[str] = None) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    for case_id in sorted(FALLBACK_CASES.keys()):
        run_dir = setup_execution_benchmark_from_fallback_case(case_id, output_root=output_root)
        outputs[case_id] = str(run_dir)
    return outputs
