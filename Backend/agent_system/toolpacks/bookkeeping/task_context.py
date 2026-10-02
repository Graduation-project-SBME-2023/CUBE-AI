from typing import Any
import logging

import httpx

logger = logging.getLogger(__name__)

from .._toolkit import tool
from ..common.manifests import READ_ONLY
from ..common.domain_api import agent_type, run_id, session_id
from ..common.emit import emit
from ..common.http_client import shared_client, timeout
from ..common.service_urls import app_api_url



REQUEST_TIMEOUT = 20.0


async def _get_json(path: str) -> Any:
    logger.info(f"GET {path}")
    response = await shared_client().get(f"{app_api_url()}{path}", timeout=timeout(REQUEST_TIMEOUT))
    response.raise_for_status()
    return response.json()


@tool(
    name="read_agent_task_context",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def read_agent_task_context() -> dict[str, Any]:
    """Return this agent's task: the session's prompt and intent, its own plan step, and the compiled prompt of the run executing it."""
    if not session_id():
        return {
            "status": "error",
            "message": "This call belongs to no session.",
        }

    if not agent_type() and not run_id():
        return {
            "status": "error",
            "message": "This call belongs to neither a run nor an agent.",
        }

    emit("tool_log", "Reading task context from DB")

    try:
        session = await _get_json(f"/sessions/{session_id()}")

        steps_sharing = 1
        if run_id():
            run = await _get_json(f"/runs/{run_id()}")
        else:
            resolved = await _get_json(
                f"/runs/session/{session_id()}/by-target/{agent_type()}",
            )
            run = resolved.get("run") or {}
            steps_sharing = resolved.get("nodes_sharing_this_target", 1)

        node = run.get("node") or {}
        params = node.get("params") or {}
        task_message = params.get("task_message") or {}

        emit("tool_log", f"Task context loaded for {agent_type()}")

        return {
            "status": "ok",
            "session_id": session_id(),
            "agent_id": agent_type() or node.get("target_name", ""),
            # The user's own words, plus the planner's structured reading
            # of them — beside each other, not one on top of the other.
            "prompt": session.get("prompt", ""),
            "prompt_kind": session.get("prompt_kind", ""),
            "intent": session.get("intent") or {},
            "step": {  # tool result key, kept (plan.md: no success result shape changes)
                "step_id": run.get("node_id"),  # tool result key, kept (plan.md: no success result shape changes)
                "agent_type": node.get("target_name", ""),
                "step_order": node.get("position"),  # tool result key, kept (plan.md: no success result shape changes)
                "title": node.get("title", ""),
                # The old free-text description lives inside the node
                # params rather than as a second, divergent field.
                "instructions": params,
                "steps_sharing_this_agent_type": steps_sharing,
            },
            "run": {
                "agent_run_id": run.get("run_id"),  # tool result key, kept (plan.md: no success result shape changes)
                "status": run.get("status"),
                "started_at": run.get("started_at"),
                # Assembled from the step PLUS the upstream results that
                # existed when this run started.
                "compiled_prompt": run.get("compiled_prompt") or "",
                "input_refs": run.get("resolved_inputs") or {},  # tool result key, kept (plan.md: no success result shape changes)
            },
            "task_message": task_message,
            "context_augmentation": task_message.get("context_augmentation") or {},
        }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code if e.response is not None else None
        body = e.response.text if e.response is not None else ""
        return {
            "status": "error",
            "message": f"HTTP error while loading task context: {status_code}",
            "details": body[:500],
            "session_id": session_id(),
            "agent_id": agent_type(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error while loading task context: {str(e)}",
            "session_id": session_id(),
            "agent_id": agent_type(),
        }
