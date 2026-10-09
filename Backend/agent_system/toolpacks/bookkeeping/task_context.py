"""``read_agent_task_context``: what this agent was asked to do.

The session's prompt and intent, the agent's own plan step, and the compiled
prompt of the run executing it — read from the session and run rows.
"""

from __future__ import annotations

from typing import Any

from .._toolkit import error_result, tool
from ..common import domain_api
from ..common.emit import emit
from ..common.manifests import READ_ONLY


async def _this_run() -> tuple[dict[str, Any], int]:
    """The run executing this agent, and how many plan steps share its agent type."""
    if domain_api.run_id():
        return await domain_api.get_run(), 1
    resolved = await domain_api.run_by_target(domain_api.agent_type())
    if resolved is None:
        raise domain_api.DomainApiError(
            f"no plan step runs {domain_api.agent_type()!r} in this session", status_code=404
        )
    return resolved.get("run") or {}, resolved.get("nodes_sharing_this_target", 1)


def _task_context(session: dict[str, Any], run: dict[str, Any], steps_sharing: int) -> dict[str, Any]:
    node = run.get("node") or {}
    params = node.get("params") or {}
    task_message = params.get("task_message") or {}
    return {
        "status": "ok",
        "session_id": domain_api.session_id(),
        "agent_id": domain_api.agent_type() or node.get("target_name", ""),
        # The user's own words, plus the planner's structured reading of
        # them — beside each other, not one on top of the other.
        "prompt": session.get("prompt", ""),
        "prompt_kind": session.get("prompt_kind", ""),
        "intent": session.get("intent") or {},
        "step": {  # tool result key, kept (plan.md: no success result shape changes)
            "step_id": run.get("node_id"),  # tool result key, kept
            "agent_type": node.get("target_name", ""),
            "step_order": node.get("position"),  # tool result key, kept
            "title": node.get("title", ""),
            # The old free-text description lives inside the node params
            # rather than as a second, divergent field.
            "instructions": params,
            "steps_sharing_this_agent_type": steps_sharing,
        },
        "run": {
            "agent_run_id": run.get("run_id"),  # tool result key, kept
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            # Assembled from the step PLUS the upstream results that existed
            # when this run started.
            "compiled_prompt": run.get("compiled_prompt") or "",
            "input_refs": run.get("resolved_inputs") or {},  # tool result key, kept
        },
        "task_message": task_message,
        "context_augmentation": task_message.get("context_augmentation") or {},
    }


@tool(
    name="read_agent_task_context",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def read_agent_task_context() -> dict[str, Any]:
    """Return this agent's task: the session's prompt and intent, its own plan step, and the compiled prompt of the run executing it."""
    if not domain_api.session_id():
        return error_result("no_session", "This call belongs to no session.")
    if not domain_api.agent_type() and not domain_api.run_id():
        return error_result("no_run", "This call belongs to neither a run nor an agent.")

    emit("tool_log", "Reading task context from DB")
    try:
        session = await domain_api.get_session()
        run, steps_sharing = await _this_run()
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(
            exc,
            "the task context could not be loaded",
            session_id=domain_api.session_id(),
            agent_id=domain_api.agent_type(),
        )

    emit("tool_log", f"Task context loaded for {domain_api.agent_type()}")
    return _task_context(session, run, steps_sharing)
