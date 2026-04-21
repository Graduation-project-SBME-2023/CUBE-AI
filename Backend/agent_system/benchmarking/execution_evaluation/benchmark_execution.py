"""
Execution benchmark runner.

Supports:
- Full-system execution benchmark.
- Single-agent execution benchmark.

Usage examples:
  python -m benchmarking.execution_evaluation.benchmark_execution \
    --hypothesis-id <uuid> --mode full_system

  python -m benchmarking.execution_evaluation.benchmark_execution \
    --use-fallback --fallback-case heart --mode single_agent --agent-id segmentation_agent
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Support both invocation styles:
# - python -m benchmarking.execution_evaluation.benchmark_execution
# - python Backend/agent_system/benchmarking/execution_evaluation/benchmark_execution.py
if __package__ in (None, ""):
    AGENT_SYSTEM_DIR = Path(__file__).resolve().parents[2]
    if str(AGENT_SYSTEM_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_SYSTEM_DIR))

    from client_config import create_model_client_from_config
    from benchmarking.execution_evaluation.benchmark_config import (
        BENCHMARK_MODE_FULL_SYSTEM,
        BENCHMARK_MODE_SINGLE_AGENT,
        ExecutionBenchmarkConfig,
    )
    from benchmarking.execution_evaluation.benchmark_execution_setup import (
        AGENT_IDS,
        normalize_agent_id,
        setup_execution_benchmark_from_fallback_case,
        setup_execution_benchmark_from_hypothesis_id,
    )
else:
    from client_config import create_model_client_from_config
    from .benchmark_config import (
        BENCHMARK_MODE_FULL_SYSTEM,
        BENCHMARK_MODE_SINGLE_AGENT,
        ExecutionBenchmarkConfig,
    )
    from .benchmark_execution_setup import (
        AGENT_IDS,
        normalize_agent_id,
        setup_execution_benchmark_from_fallback_case,
        setup_execution_benchmark_from_hypothesis_id,
    )


def _safe_json_dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _serialize_event(event: Any) -> Dict[str, Any]:
    def _normalize(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {k: _normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_normalize(v) for v in value]
        if hasattr(value, "model_dump"):
            try:
                return _normalize(value.model_dump())
            except Exception:
                return str(value)
        if hasattr(value, "dict"):
            try:
                return _normalize(value.dict())
            except Exception:
                return str(value)
        return str(value)

    event_type = type(event).__name__
    source = getattr(event, "source", None)
    content = _normalize(getattr(event, "content", None))

    event_kind = "other"
    extra: Dict[str, Any] = {}

    if event_type == "ThoughtEvent":
        event_kind = "thought"
        extra["thought"] = content
    elif event_type == "ToolCallRequestEvent":
        event_kind = "tool_call_request"
        tool_calls = []
        if isinstance(content, list):
            for call in content:
                if isinstance(call, dict):
                    tool_calls.append(
                        {
                            "name": call.get("name"),
                            "arguments": call.get("arguments"),
                        }
                    )
                else:
                    tool_calls.append({"raw": call})
        extra["tool_calls"] = tool_calls
    elif event_type == "ToolCallExecutionEvent":
        event_kind = "tool_call_result"
        tool_results = []
        if isinstance(content, list):
            for result in content:
                if isinstance(result, dict):
                    tool_results.append(
                        {
                            "name": result.get("name"),
                            "content": result.get("content"),
                        }
                    )
                else:
                    tool_results.append({"raw": result})
        extra["tool_results"] = tool_results
    elif event_type in ("TextMessage", "BaseChatMessage"):
        event_kind = "message"
        extra["message"] = content
    elif event_type == "TaskResult":
        event_kind = "task_result"
        if isinstance(content, list):
            extra["messages"] = content
    elif "Error" in event_type or "Exception" in event_type:
        event_kind = "error"

    payload = {
        "event_type": event_type,
        "event_kind": event_kind,
        "source": source,
        "content": content,
    }
    payload.update(extra)
    return payload


def _group_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_agent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    counts_by_agent: Dict[str, int] = defaultdict(int)
    counts_by_type: Dict[str, int] = defaultdict(int)

    for event in events:
        source = str(event.get("source") or "unknown")
        event_kind = str(event.get("event_kind") or "other")
        by_agent[source].append(event)
        by_type[event_kind].append(event)
        counts_by_agent[source] += 1
        counts_by_type[event_kind] += 1

    return {
        "counts": {
            "total_events": len(events),
            "by_agent": dict(counts_by_agent),
            "by_type": dict(counts_by_type),
        },
        "by_agent": dict(by_agent),
        "by_type": dict(by_type),
    }


def _write_grouped_outputs(run_dir: Path, events: List[Dict[str, Any]], prefix: str) -> Dict[str, str]:
    grouped = _group_events(events)

    raw_path = run_dir / f"{prefix}_events_raw.json"
    grouped_path = run_dir / f"{prefix}_events_grouped.json"

    _safe_json_dump(events, raw_path)
    _safe_json_dump(grouped, grouped_path)

    for agent_id, agent_events in grouped["by_agent"].items():
        agent_dir = run_dir / "agents" / agent_id
        _safe_json_dump(agent_events, agent_dir / f"{prefix}_timeline.json")
        by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in agent_events:
            by_type[str(event.get("event_kind") or "other")].append(event)
        _safe_json_dump(dict(by_type), agent_dir / f"{prefix}_by_type.json")

    return {
        "raw": str(raw_path),
        "grouped": str(grouped_path),
    }


def _load_benchmark_inputs(run_dir: Path) -> Dict[str, Any]:
    hypothesis = _read_json(run_dir / "hypothesis.json")
    plan = _read_json(run_dir / "plan.json")
    task_messages = _read_json(run_dir / "task_messages.json")
    manifest = _read_json(run_dir / "run_manifest.json")

    return {
        "hypothesis": hypothesis,
        "plan": plan,
        "task_messages": task_messages,
        "manifest": manifest,
    }


def _pick_agent_task(task_messages: List[Dict[str, Any]], agent_id: str) -> Optional[Dict[str, Any]]:
    normalized_agent_id = normalize_agent_id(agent_id)
    for tm in task_messages:
        if isinstance(tm, dict) and normalize_agent_id(tm.get("agent_id")) == normalized_agent_id:
            return tm
    return None


def _build_single_agent_prompt(
    hypothesis: Dict[str, Any],
    agent_id: str,
    task_message: Optional[Dict[str, Any]],
    prompt_prefix: str,
) -> str:
    hypothesis_text = hypothesis.get("hypothesis") or hypothesis.get("title") or ""

    lines = [
        prompt_prefix,
        "",
        "[BENCHMARK CONTEXT]",
        f"Hypothesis ID: {hypothesis.get('id')}",
        f"Hypothesis Title: {hypothesis.get('title', '')}",
        f"Target Agent: {agent_id}",
    ]

    if task_message:
        lines.extend(
            [
                "",
                "[TASK MESSAGE]",
                f"Agent Title: {task_message.get('agent_title', '')}",
                f"Phase: {task_message.get('phase', '')}",
                "Instructions:",
                str(task_message.get("instructions", "")),
            ]
        )

    lines.extend(
        [
            "",
            "[BASE HYPOTHESIS INPUT]",
            hypothesis_text,
        ]
    )

    return "\n".join(lines)


async def _create_agent(agent_id: str, workspace_dir: str, session_id: Optional[str]):
    from agents.execution_agents.feature_extraction_agent import FeatureExtractionAgent
    from agents.execution_agents.modeling_agent import ModelingAgent
    from agents.execution_agents.reporter_agent import ReporterAgent
    from agents.execution_agents.result_reporter_agent import ResultReporterAgent
    from agents.execution_agents.segmentation_agent import SegmentationAgent
    from agents.planning_agents.population_planner import PopulationPlannerAgent

    agent_id = normalize_agent_id(agent_id)
    client = create_model_client_from_config()

    if agent_id == "study_population_planner_agent":
        return await PopulationPlannerAgent.create(
            model_client=client,
            workspace_folder=workspace_dir,
            session_id=session_id,
        )
    if agent_id == "segmentation_agent":
        return await SegmentationAgent.create(
            model_client=client,
            workspace_folder=workspace_dir,
            session_id=session_id,
        )
    if agent_id == "feature_extraction_agent":
        return await FeatureExtractionAgent.create(
            model_client=client,
            workspace_folder=workspace_dir,
            session_id=session_id,
        )
    if agent_id == "modeling_agent":
        return await ModelingAgent.create(
            model_client=client,
            workspace_folder=workspace_dir,
            session_id=session_id,
        )
    if agent_id == "result_reporter_agent":
        return await ResultReporterAgent.create(
            model_client=client,
            workspace_folder=workspace_dir,
            session_id=session_id,
        )
    if agent_id == "latex_reporter_agent":
        return await ReporterAgent.create(
            model_client=client,
            workspace_folder=workspace_dir,
            session_id=session_id,
        )

    raise ValueError(f"Unsupported agent id: {agent_id}")


def _validate_structured_output_contracts(
    workspace_dir: str,
    expected_agents: Set[str],
) -> Dict[str, Any]:
    workspace = Path(workspace_dir)
    outputs_dir = workspace / "outputs"

    result: Dict[str, Any] = {
        "workspace_dir": str(workspace),
        "outputs_dir": str(outputs_dir),
        "expected_agents": sorted(expected_agents),
        "checks": {},
        "summary": {
            "agents_checked": 0,
            "agents_passed": 0,
            "agents_failed": 0,
        },
    }

    status_allowed = {"completed", "partial", "failed"}

    for agent_id in sorted(expected_agents):
        check: Dict[str, Any] = {
            "agent_id": agent_id,
            "ok": True,
            "issues": [],
            "structured_output_path": str(outputs_dir / f"{agent_id}_output.json"),
        }

        if agent_id == "latex_reporter_agent":
            reports_dir = workspace / "reports"
            pdfs = sorted([p.name for p in reports_dir.glob("*.pdf")]) if reports_dir.exists() else []
            check["pdf_reports"] = pdfs
            if not pdfs:
                check["ok"] = False
                check["issues"].append("Missing PDF artifact in workspace/reports")
            result["checks"][agent_id] = check
            result["summary"]["agents_checked"] += 1
            result["summary"]["agents_passed" if check["ok"] else "agents_failed"] += 1
            continue

        path = outputs_dir / f"{agent_id}_output.json"
        if not path.exists():
            check["ok"] = False
            check["issues"].append("Missing structured output file")
            result["checks"][agent_id] = check
            result["summary"]["agents_checked"] += 1
            result["summary"]["agents_passed" if check["ok"] else "agents_failed"] += 1
            continue

        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            check["ok"] = False
            check["issues"].append(f"Invalid JSON: {exc}")
            result["checks"][agent_id] = check
            result["summary"]["agents_checked"] += 1
            result["summary"]["agents_passed" if check["ok"] else "agents_failed"] += 1
            continue

        required_top = ["agent_id", "agent_role", "status", "inputs", "outputs"]
        for key in required_top:
            if key not in payload:
                check["ok"] = False
                check["issues"].append(f"Missing top-level key: {key}")

        payload_agent_id = normalize_agent_id(payload.get("agent_id"))
        if payload_agent_id != agent_id:
            check["ok"] = False
            check["issues"].append(
                f"agent_id mismatch: expected '{agent_id}', got '{payload.get('agent_id')}'"
            )

        status = payload.get("status")
        if status not in status_allowed:
            check["ok"] = False
            check["issues"].append(
                f"Invalid status '{status}', expected one of {sorted(status_allowed)}"
            )

        outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
        files = outputs.get("files") if isinstance(outputs.get("files"), dict) else {}
        metrics = outputs.get("metrics") if isinstance(outputs.get("metrics"), dict) else {}
        summary_text = outputs.get("summary")

        if not isinstance(summary_text, str):
            check["ok"] = False
            check["issues"].append("outputs.summary must be a string")

        if not isinstance(files, dict):
            check["ok"] = False
            check["issues"].append("outputs.files must be an object")

        if not isinstance(metrics, dict):
            check["ok"] = False
            check["issues"].append("outputs.metrics must be an object")

        check["observed"] = {
            "status": status,
            "file_keys": sorted(files.keys()) if isinstance(files, dict) else [],
            "metric_keys": sorted(metrics.keys()) if isinstance(metrics, dict) else [],
        }

        result["checks"][agent_id] = check
        result["summary"]["agents_checked"] += 1
        result["summary"]["agents_passed" if check["ok"] else "agents_failed"] += 1

    result["summary"]["ok"] = result["summary"]["agents_failed"] == 0
    return result


async def _run_full_system(config: ExecutionBenchmarkConfig, run_dir: Path) -> Dict[str, Any]:
    from agents.planning_agents.context_compiler import build_enriched_pipeline_prompt
    from autogen_agentchat.teams import GraphFlow
    from teams.execution_team import BasicPipelineBuilder

    data = _load_benchmark_inputs(run_dir)
    task_messages = data["task_messages"]
    hypothesis = data["hypothesis"]

    workspace_dir = (
        config.workspace_dir
        or data["manifest"].get("workspace_dir")
        or str((Path(__file__).resolve().parents[2] / "workspace"))
    )
    session_id = str(hypothesis.get("chat_id") or "benchmark_session")

    builder = await BasicPipelineBuilder.create(
        workspace_folder=workspace_dir,
        task_messages=task_messages,
        model_client=create_model_client_from_config(),
        session_id=session_id,
    )
    flow = GraphFlow(participants=builder.get_participants(), graph=builder.build())

    try:
        prompt = build_enriched_pipeline_prompt(task_messages) if task_messages else hypothesis.get("title", "Run the pipeline")
    except Exception:
        prompt = hypothesis.get("title", "Run the pipeline")

    prompt = f"{config.prompt_prefix}\n\n{prompt}"

    events: List[Dict[str, Any]] = []

    async for event in flow.run_stream(task=prompt):
        payload = _serialize_event(event)
        events.append(payload)

    output_files = _write_grouped_outputs(run_dir, events, prefix="full_system")
    grouped = _group_events(events)
    active_agents = {
        source
        for source in grouped["counts"]["by_agent"].keys()
        if source in AGENT_IDS
    }
    contract_validation = _validate_structured_output_contracts(workspace_dir, active_agents)
    _safe_json_dump(contract_validation, run_dir / "contract_validation.json")

    summary = {
        "mode": BENCHMARK_MODE_FULL_SYSTEM,
        "workspace_dir": workspace_dir,
        "session_id": session_id,
        "events_count": grouped["counts"]["total_events"],
        "event_count_by_source": grouped["counts"]["by_agent"],
        "event_count_by_type": grouped["counts"]["by_type"],
        "contract_validation_ok": contract_validation["summary"]["ok"],
        "output_files": output_files,
        "run_dir": str(run_dir),
    }
    _safe_json_dump(summary, run_dir / "summary_full_system.json")
    return summary


async def _run_single_agent(config: ExecutionBenchmarkConfig, run_dir: Path) -> Dict[str, Any]:
    if not config.target_agent_id:
        raise ValueError("target_agent_id is required for single-agent mode")

    data = _load_benchmark_inputs(run_dir)
    hypothesis = data["hypothesis"]
    task_messages = data["task_messages"]

    workspace_dir = (
        config.workspace_dir
        or data["manifest"].get("workspace_dir")
        or str((Path(__file__).resolve().parents[2] / "workspace"))
    )
    session_id = str(hypothesis.get("chat_id") or "benchmark_session")

    target_agent_id = normalize_agent_id(config.target_agent_id)
    task_message = _pick_agent_task(task_messages, target_agent_id)
    prompt = _build_single_agent_prompt(
        hypothesis=hypothesis,
        agent_id=target_agent_id,
        task_message=task_message,
        prompt_prefix=config.prompt_prefix,
    )

    agent = await _create_agent(target_agent_id, workspace_dir, session_id)

    events: List[Dict[str, Any]] = []
    async for event in agent.run_stream(task=prompt):
        events.append(_serialize_event(event))

    agent_dir = run_dir / "agents" / target_agent_id
    _safe_json_dump({"prompt": prompt}, agent_dir / "prompt_used.json")

    output_files = _write_grouped_outputs(run_dir, events, prefix=f"single_agent_{target_agent_id}")
    grouped = _group_events(events)
    _safe_json_dump({"events": events}, agent_dir / "actual_output.json")
    _safe_json_dump(grouped, agent_dir / "actual_output_grouped.json")
    contract_validation = _validate_structured_output_contracts(workspace_dir, {target_agent_id})
    _safe_json_dump(contract_validation, run_dir / "contract_validation.json")

    summary = {
        "mode": BENCHMARK_MODE_SINGLE_AGENT,
        "target_agent_id": target_agent_id,
        "workspace_dir": workspace_dir,
        "session_id": session_id,
        "events_count": grouped["counts"]["total_events"],
        "event_count_by_source": grouped["counts"]["by_agent"],
        "event_count_by_type": grouped["counts"]["by_type"],
        "contract_validation_ok": contract_validation["summary"]["ok"],
        "output_files": output_files,
        "run_dir": str(run_dir),
    }
    _safe_json_dump(summary, run_dir / "summary_single_agent.json")
    return summary


async def run_execution_benchmark(config: ExecutionBenchmarkConfig) -> Dict[str, Any]:
    config.validate()

    if config.use_fallback:
        run_dir = setup_execution_benchmark_from_fallback_case(
            case_id=str(config.fallback_case),
            output_root=config.output_root,
        )
    else:
        run_dir = await setup_execution_benchmark_from_hypothesis_id(
            hypothesis_id=str(config.hypothesis_id),
            output_root=config.output_root,
            base_url=config.base_url,
        )

    if config.mode == BENCHMARK_MODE_FULL_SYSTEM:
        return await _run_full_system(config, run_dir)

    return await _run_single_agent(config, run_dir)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execution benchmarking runner")

    parser.add_argument("--hypothesis-id", type=str, default=None, help="Hypothesis UUID from database")
    parser.add_argument(
        "--mode",
        type=str,
        default=BENCHMARK_MODE_FULL_SYSTEM,
        choices=[BENCHMARK_MODE_FULL_SYSTEM, BENCHMARK_MODE_SINGLE_AGENT],
        help="Benchmark mode",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=None,
        choices=AGENT_IDS,
        help="Target agent id for single-agent mode",
    )
    parser.add_argument("--base-url", type=str, default="http://localhost:8000", help="Backend API base URL")
    parser.add_argument("--output-root", type=str, default=None, help="Output directory root")
    parser.add_argument("--workspace-dir", type=str, default=None, help="Optional workspace override")

    parser.add_argument(
        "--use-fallback",
        action="store_true",
        help="Use old benchmark fallback case instead of DB fetch",
    )
    parser.add_argument(
        "--fallback-case",
        type=str,
        default=None,
        choices=["heart", "liver", "prostate", "kidney"],
        help="Fallback case id",
    )

    parser.add_argument(
        "--prompt-prefix",
        type=str,
        default=(
            "You are running in execution benchmark mode. "
            "Be deterministic, log assumptions, and produce structured outputs."
        ),
        help="Extra prompt header injected before benchmark input",
    )

    return parser


async def _main_async() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Validate CLI argument combinations early to provide actionable CLI errors
    # instead of a runtime traceback from config.validate().
    if args.use_fallback and args.hypothesis_id:
        parser.error("--hypothesis-id cannot be used together with --use-fallback")

    if args.use_fallback and not args.fallback_case:
        parser.error("--fallback-case is required when --use-fallback is set")

    if not args.use_fallback and not args.hypothesis_id:
        parser.error("--hypothesis-id is required unless --use-fallback is set")

    if args.mode == BENCHMARK_MODE_SINGLE_AGENT and not args.agent_id:
        parser.error("--agent-id is required when --mode single_agent")

    config = ExecutionBenchmarkConfig(
        hypothesis_id=args.hypothesis_id,
        use_fallback=args.use_fallback,
        fallback_case=args.fallback_case,
        base_url=args.base_url,
        mode=args.mode,
        target_agent_id=args.agent_id,
        output_root=args.output_root or str((Path(__file__).parent / "runs")),
        workspace_dir=args.workspace_dir,
        prompt_prefix=args.prompt_prefix,
    )

    summary = await run_execution_benchmark(config)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
