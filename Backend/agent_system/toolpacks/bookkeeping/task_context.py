import os
from typing import Any
import logging

import httpx

logger = logging.getLogger(__name__)

from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload

def _app_api_url() -> str:
    """The Application API base URL."""
    try:
        from service_urls import APP_API_URL

        return APP_API_URL
    except ImportError:
        return os.environ.get(
            "APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")
        ).rstrip("/")


BASE_URL = _app_api_url()
SESSION_ID = os.environ.get("SESSION_ID", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
# The approved step and run this agent instance was created for. When it
# is present there is nothing to resolve: identity is the run, not the name.
RUN_ID = os.environ.get("RUN_ID", "")
NODE_ID = os.environ.get("NODE_ID", "")


async def _get_json(client: httpx.AsyncClient, path: str) -> Any:
    logger.info(f"GET {path}")
    response = await client.get(f"{BASE_URL}{path}")
    response.raise_for_status()
    return response.json()


@mcp.tool(
    name="read_agent_task_context",
    annotations={
        "title": "Read Agent Task Context",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def read_agent_task_context() -> dict[str, Any]:
    """Return this agent's task: the session's prompt and intent, its own plan step, and the compiled prompt of the run executing it."""
    if not SESSION_ID:
        return {
            "status": "error",
            "message": "SESSION_ID is not set in MCP environment.",
        }

    if not AGENT_ID and not RUN_ID:
        return {
            "status": "error",
            "message": "Neither RUN_ID nor AGENT_ID is set in MCP environment.",
        }

    log_tool_state(ToolEvent("tool_started", "text", TextPayload(message="Reading task context from DB")))

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            session = await _get_json(client, f"/sessions/{SESSION_ID}")

            steps_sharing = 1
            if RUN_ID:
                run = await _get_json(client, f"/runs/{RUN_ID}")
            else:
                resolved = await _get_json(
                    client,
                    f"/runs/session/{SESSION_ID}/by-target/{AGENT_ID}",
                )
                run = resolved.get("run") or {}
                steps_sharing = resolved.get("nodes_sharing_this_target", 1)

            node = run.get("node") or {}
            params = node.get("params") or {}
            task_message = params.get("task_message") or {}

            log_tool_state(
                ToolEvent(
                    "tool_finished",
                    "text",
                    TextPayload(message=f"Task context loaded for {AGENT_ID}"),
                )
            )

            return {
                "status": "ok",
                "session_id": SESSION_ID,
                "agent_id": AGENT_ID or node.get("target_name", ""),
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
            "session_id": SESSION_ID,
            "agent_id": AGENT_ID,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error while loading task context: {str(e)}",
            "session_id": SESSION_ID,
            "agent_id": AGENT_ID,
        }
