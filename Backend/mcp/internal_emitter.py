"""Non-blocking, authenticated posting from MCP tool subprocesses to the
agent system's internal event endpoints (WEBSOCKET_FIX_PLAN.md WS-07/W11).

Every MCP tool runs in its own subprocess, outside the agent-system process
that owns `socket_store.ConnectionManager`, so the only way to reach a live
socket from here is an HTTP hop to `/events` or `/ws-broadcast`. Both require
`WS_INTERNAL_TOKEN` since WS-01 -- several call sites under `mcp/tools/`
posted to `/ws-broadcast` without it and were silently getting 401'd (live
delivery only, so nothing surfaced the failure: the domain write each call
follows had already committed). `fire_and_forget()` schedules the POST on the
running loop and returns immediately: a tool call must never block on a UI
notification, and a failure here is a counted, logged miss, never an
exception raised into the tool's own return value.
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MAS_BASE_URL = os.environ.get("MAS_BASE_URL", "http://localhost:8150").rstrip("/")
INTERNAL_EMIT_TIMEOUT_SECONDS = float(os.environ.get("INTERNAL_EMIT_TIMEOUT_SECONDS", "5"))

# Counted rather than swallowed (fixes W11's `except Exception: pass`). Not
# thread-safe, but every caller runs on the single asyncio event loop inside
# one MCP subprocess -- there is no concurrent writer to race.
failure_count = 0


def _internal_auth_headers() -> dict[str, str]:
    token = os.environ.get("WS_INTERNAL_TOKEN", "")
    return {"X-Internal-Token": token} if token else {}


async def post_internal(path: str, json_body: dict) -> bool:
    """POST one internal event. Best-effort: the domain write this follows
    has already committed, so a failure here only costs a live UI update."""
    global failure_count
    try:
        async with httpx.AsyncClient(timeout=INTERNAL_EMIT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{MAS_BASE_URL}{path}",
                json=json_body,
                headers=_internal_auth_headers(),
            )
            response.raise_for_status()
            return True
    except Exception as exc:
        failure_count += 1
        logger.warning(
            "[internal_emitter] POST %s failed (failure #%d): %s",
            path, failure_count, exc,
        )
        return False


async def broadcast_ws_message(event_type: str, payload: dict, session_id: str | None) -> bool:
    """The `/ws-broadcast` shape every MCP-tool live-notification call uses."""
    if not session_id:
        return False
    return await post_internal(
        "/ws-broadcast",
        {"type": event_type, "payload": payload, "session_id": session_id},
    )


def fire_and_forget(coro) -> None:
    """Schedule `coro` on the running loop and return immediately.

    Every caller is an `async def` MCP tool function, so a loop is always
    running; the no-loop branch only guards against a future caller that
    isn't (drop and log rather than block synchronously to send one status
    update -- see the module docstring).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[internal_emitter] no running event loop; dropping emission")
        return
    task = loop.create_task(coro)
    # coro (post_internal/broadcast_ws_message) already catches everything
    # and returns a bool, but hold the reference so the task isn't GC'd
    # mid-flight and so a truly unexpected exception surfaces in logs
    # instead of asyncio's silent "exception never retrieved" warning.
    task.add_done_callback(_log_unexpected_failure)


def _log_unexpected_failure(task: "asyncio.Task") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("[internal_emitter] background emission crashed: %s", exc, exc_info=exc)
