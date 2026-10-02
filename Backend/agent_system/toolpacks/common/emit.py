"""How a tool reports to the UI: events and view updates, sent to the
context's sink (merged plan, Phase 2.1).

Under ``Backend/mcp`` every tool ran in its own subprocess and reported over
HTTP (``POST /events``, ``POST /ws-broadcast``), reading its identity from
``SESSION_ID`` / ``AGENT_ID``. The tools now run in the Agent System process
through ``dispatch.invoke()``, which sets a ``ToolContext`` whose
``event_sink`` reaches the session directly.

- ``emit()`` / ``emit_progress()`` send a ``ToolEvent`` without waiting for it;
  ``emit_view()`` sends one view update (a WebSocket frame of its own type).
- Identity comes from ``current_context()``: ``source`` follows the toolkit
  rule (the node's agent or the tool) and ``session_id`` is the context's.
- Reporting is best-effort and never raises into the tool. With no context (a
  tool called directly, as unit tests do) or no sink, nothing is sent; a
  payload the toolkit's models reject, or a failing sink, costs a log line.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from typing import Any

from .. import _toolkit

logger = logging.getLogger(__name__)

# Strong references to the scheduled emissions, so none is garbage-collected
# mid-flight (the event loop keeps only weak references to tasks).
_pending: set[asyncio.Task[None]] = set()


def emit(event_type: str, message: str) -> None:
    """Report a step of the running tool as text (``tool_started``,
    ``tool_progress``, ``tool_log``, ``tool_finished`` or ``error``)."""
    _send(event_type, lambda: _toolkit.TextPayload(message=message))


def emit_progress(current: int | float, total: int, label: str | None = None) -> None:
    """Report ``current`` of ``total`` stages done, as a ``tool_progress`` event."""
    _send(
        "tool_progress",
        lambda: _toolkit.ProgressPayload(current=current, total=total, label=label),
    )


async def emit_view(
    view_type: str, payload: Mapping[str, Any], session_id: str | None = None
) -> bool:
    """Send one view update; ``True`` when it was sent.

    ``session_id`` is only cross-checked against the context: a view naming
    another session is dropped. It goes away with the call sites' env reads
    (Phase 2.3).
    """
    ctx = _context()
    if ctx is None or ctx.event_sink is None:
        logger.debug("no tool context or event sink; dropped view %s", view_type)
        return False
    if session_id is not None and str(session_id) != str(ctx.session_id):
        logger.warning(
            "view %s names session %s but the tool runs for session %s; dropped",
            view_type,
            session_id,
            ctx.session_id,
        )
        return False
    try:
        await ctx.event_sink.emit_view(_toolkit.ViewEvent(view_type, payload))
    except Exception:
        logger.exception("could not send view %s of tool %s", view_type, ctx.tool_name)
        return False
    return True


def _send(event_type: str, content: Any) -> None:
    ctx = _context()
    if ctx is None or ctx.event_sink is None:
        logger.debug("no tool context or event sink; dropped %s", event_type)
        return
    try:
        event = _toolkit.ToolEvent.for_context(ctx, event_type, content())
    except Exception:
        logger.exception("could not build the %s event of tool %s", event_type, ctx.tool_name)
        return
    _schedule(ctx.event_sink.emit(event), f"{event_type} of {ctx.tool_name}")


def _context() -> _toolkit.ToolContext | None:
    try:
        return _toolkit.current_context()
    except _toolkit.NoToolContextError:
        return None


def _schedule(coro: Coroutine[Any, Any, None], what: str) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no running loop: every caller is async, so this is a bug elsewhere
        coro.close()
        logger.debug("no running event loop; dropped %s", what)
        return
    _pending.add(task)
    task.add_done_callback(lambda done: _finished(done, what))


def _finished(task: asyncio.Task[None], what: str) -> None:
    _pending.discard(task)
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("could not emit %s: %s", what, exc, exc_info=exc)
