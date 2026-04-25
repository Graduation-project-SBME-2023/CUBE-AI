import os
import httpx
from typing import Any

from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload

BASE_URL = os.environ.get("MONAI_URL", "http://localhost:8000")
SESSION_ID = os.environ.get("SESSION_ID", "")
AGENT_ID = os.environ.get("AGENT_ID", "")


async def _get_json(client: httpx.AsyncClient, path: str) -> Any:
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
    """
    Resolve and return the current agent's task message + context augmentation
    from DB using SESSION_ID and AGENT_ID environment variables.

    Resolution flow:
      1) hypothesis by chat session id
      2) plan by hypothesis id
      3) step by module == AGENT_ID
      4) task message by step id
      5) context augmentation by task_id
    """
    if not SESSION_ID:
        return {
            "status": "error",
            "message": "SESSION_ID is not set in MCP environment.",
        }

    if not AGENT_ID:
        return {
            "status": "error",
            "message": "AGENT_ID is not set in MCP environment.",
        }

    log_tool_state(ToolEvent("tool_started", "text", TextPayload(message="Reading task context from DB")))

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            hypothesis = await _get_json(client, f"/hypotheses/by-chat/{SESSION_ID}")
            hypothesis_id = str(hypothesis.get("id") or "")
            if not hypothesis_id:
                return {
                    "status": "not_found",
                    "message": f"No hypothesis linked to session {SESSION_ID}.",
                    "session_id": SESSION_ID,
                    "agent_id": AGENT_ID,
                }

            plans = await _get_json(client, f"/plans/by-hypothesis/{hypothesis_id}")
            if not isinstance(plans, list) or not plans:
                return {
                    "status": "not_found",
                    "message": f"No plans found for hypothesis {hypothesis_id}.",
                    "session_id": SESSION_ID,
                    "agent_id": AGENT_ID,
                    "hypothesis_id": hypothesis_id,
                }

            confirmed_plan = next((p for p in plans if p.get("state") == "confirmed"), None)
            plan = confirmed_plan or plans[-1]
            plan_id = str(plan.get("id") or "")
            steps = plan.get("steps") or []

            step = next((s for s in steps if s.get("module") == AGENT_ID), None)
            if not step:
                return {
                    "status": "not_found",
                    "message": f"No plan step mapped to AGENT_ID={AGENT_ID}.",
                    "session_id": SESSION_ID,
                    "agent_id": AGENT_ID,
                    "hypothesis_id": hypothesis_id,
                    "plan_id": plan_id,
                    "available_modules": [s.get("module") for s in steps if s.get("module")],
                }

            step_id = str(step.get("id") or "")
            task_message = await _get_json(client, f"/task-messages/step/{step_id}")
            task_id = str(task_message.get("task_id") or "")

            context_augmentation: dict[str, Any] = {}
            if task_id:
                try:
                    context_augmentation = await _get_json(client, f"/context-augmentations/task/{task_id}")
                except httpx.HTTPStatusError as context_error:
                    if context_error.response.status_code != 404:
                        raise

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
                "agent_id": AGENT_ID,
                "hypothesis": {
                    "id": hypothesis.get("id"),
                    "title": hypothesis.get("title", ""),
                    "state": hypothesis.get("state", ""),
                },
                "plan": {
                    "id": plan_id,
                    "state": plan.get("state", ""),
                    "study_goal": plan.get("study_goal", ""),
                },
                "step": {
                    "id": step_id,
                    "module": step.get("module", ""),
                    "title": step.get("title", ""),
                    "description": step.get("description", ""),
                    "status": step.get("status", ""),
                },
                "task_message": task_message,
                "context_augmentation": context_augmentation,
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
