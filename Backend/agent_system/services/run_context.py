"""What the runner needs to execute one approved plan step."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

NODE_SEPARATOR = "__s"


@dataclass(frozen=True)
class PlannedRun:
    """One approved step and the run that will execute it."""

    agent_run_id: str
    step_id: str
    session_id: str
    agent_type: str
    step_order: int
    title: str
    instructions: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    compiled_prompt: str | None = None
    output_summary: dict[str, Any] | None = None

    @property
    def node_name(self) -> str:
        return f"{self.agent_type}{NODE_SEPARATOR}{self.step_order}"

    @property
    def task_message(self) -> dict[str, Any]:
        raw = self.instructions.get("task_message")
        return raw if isinstance(raw, dict) else {}

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"


def agent_type_of(node_name: str) -> str:
    """The agent selector behind a node name (the UI's vocabulary)."""
    return str(node_name or "").split(NODE_SEPARATOR, 1)[0]


def planned_runs(api_runs: list[dict]) -> list[PlannedRun]:
    """Map `/agent-runs/session/{id}` onto the runner's view, in step order."""
    runs: list[PlannedRun] = []
    for raw in api_runs:
        step = raw.get("step") or {}
        runs.append(
            PlannedRun(
                agent_run_id=str(raw.get("agent_run_id") or ""),
                step_id=str(raw.get("step_id") or ""),
                session_id=str(raw.get("session_id") or ""),
                agent_type=str(step.get("agent_type") or ""),
                step_order=int(step.get("step_order") or 0),
                title=str(step.get("title") or ""),
                instructions=step.get("instructions") or {},
                status=str(raw.get("status") or "pending"),
                compiled_prompt=raw.get("compiled_prompt"),
                output_summary=raw.get("output_summary"),
            )
        )
    runs.sort(key=lambda run: run.step_order)
    return runs


def first_incomplete(runs: list[PlannedRun]) -> PlannedRun | None:
    """Where a resume continues — the first run that has not completed."""
    for run in runs:
        if not run.is_complete:
            return run
    return None


def _upstream_digest(run: PlannedRun) -> dict[str, Any]:
    """What a downstream step is told about an upstream one."""
    summary = run.output_summary or {}
    agent_output = summary.get("agent_output") or {}
    outputs = agent_output.get("outputs") or {}
    return {
        "agent_run_id": run.agent_run_id,
        "step_id": run.step_id,
        "step_order": run.step_order,
        "agent_type": run.agent_type,
        "status": agent_output.get("status") or run.status,
        "summary": outputs.get("summary") or "",
        "metrics": outputs.get("metrics") or {},
        "files": outputs.get("files") or {},
    }


def build_compiled_prompt(run: PlannedRun, upstream: list[PlannedRun]) -> str:
    """The compiler's exact output for this step, resolved at start time.: this is *not* a copy of the step definition — it is the step plus the upstream results that only exist once the earlier steps have run, which is why it is resolved immediately before the step starts and is immutable"""
    from agents.planning_agents.context_compiler import format_task_prompt
    from models.plans import ContextAugmentation, TaskMessage

    task_message = run.task_message
    if task_message:
        augmentation = task_message.get("context_augmentation") or {}
        message = TaskMessage(
            task_id=str(task_message.get("task_id") or ""),
            phase=str(task_message.get("phase") or ""),
            agent_id=run.agent_type,
            agent_title=str(task_message.get("agent_title") or run.title),
            instructions=str(task_message.get("instructions") or ""),
            hypothesis_summary=str(task_message.get("hypothesis_summary") or ""),
            payload=task_message.get("payload") or {},
            context_augmentation=ContextAugmentation(
                **{
                    name: str(augmentation.get(name) or "")
                    for name in ContextAugmentation.model_fields
                }
            ),
        )
        prompt = format_task_prompt(message)
    else:
        # A step with no compiled task message still executes: its definition
        # is what the plan says it is.
        description = str(run.instructions.get("description") or "").strip()
        prompt = f"## TASK: {run.title}\n{description}".strip()

    completed = [item for item in upstream if item.is_complete]
    if completed:
        prompt += "\n\n-- UPSTREAM RESULTS --\n"
        for item in completed:
            digest = _upstream_digest(item)
            prompt += (
                f"  {item.step_order}. [{item.agent_type}] {digest['status']}: "
                f"{str(digest['summary'])[:800]}\n"
            )
            if digest["files"]:
                prompt += f"     files: {json.dumps(digest['files'])[:800]}\n"
    return prompt


def build_input_refs(
    run: PlannedRun,
    upstream: list[PlannedRun],
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Which rows fed this run — source ids, not their contents."""
    session = session or {}
    return {
        "schema_version": 1,
        "session": {
            "session_id": run.session_id,
            "prompt_kind": session.get("prompt_kind"),
            "intent_present": bool(session.get("intent")),
        },
        "step": {
            "step_id": run.step_id,
            "step_order": run.step_order,
            "agent_type": run.agent_type,
            "task_message_id": run.task_message.get("task_id"),
        },
        "upstream_runs": [_upstream_digest(item) for item in upstream if item.is_complete],
    }
