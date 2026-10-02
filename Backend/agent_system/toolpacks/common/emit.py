"""Compatibility shim: the tools' old event calls, sent to the context's sink
(merged plan, Phase 2.1).

Under ``Backend/mcp`` every tool ran in its own subprocess and reported over
HTTP: ``tool_logging.log_tool_state`` posted to ``/events`` and
``internal_emitter.broadcast_ws_message`` to ``/ws-broadcast``, both reading
their identity from ``SESSION_ID`` / ``AGENT_ID``. The tools now run in the
Agent System process through ``dispatch.invoke()``, which sets a
``ToolContext`` whose ``event_sink`` reaches the session directly.

This module keeps the old names and signatures so the move changes only the
import lines. **It is temporary:** the next step rewrites the call sites to
``emit()`` / ``emit_view()`` and deletes the legacy names.

What changed underneath:

- identity comes from ``current_context()``: ``source`` follows the toolkit
  rule (the node's agent or the tool), ``session_id`` is the context's; the
  ``session_id`` argument of ``broadcast_ws_message`` is only cross-checked;
- with no context (a tool called directly, as unit tests do) or no sink, the
  event is dropped. Events were best-effort before and still are: nothing here
  ever raises into the tool;
- the payload classes are plain dataclasses, as before. They are converted to
  the toolkit's validated models inside the shim, so a payload the models
  reject costs one log line, never the tool call.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Union

from .. import _toolkit

logger = logging.getLogger(__name__)


@dataclass
class MarkdownPayload:
    markdown: str


@dataclass
class ProgressPayload:
    current: int | float
    total: int
    label: str | None = None


@dataclass
class TextPayload:
    message: str


ContentPayload = Union[MarkdownPayload, ProgressPayload, TextPayload]

_TOOLKIT_PAYLOADS: dict[type, type] = {
    MarkdownPayload: _toolkit.MarkdownPayload,
    ProgressPayload: _toolkit.ProgressPayload,
    TextPayload: _toolkit.TextPayload,
}


@dataclass
class ToolEvent:
    """The legacy event shape the call sites build. ``content_type`` is kept
    for the signature only: the toolkit derives it from the payload's class."""

    event_type: str
    content_type: str
    content: ContentPayload


# Strong references to the scheduled emissions, so none is garbage-collected
# mid-flight (the event loop keeps only weak references to tasks).
_pending: set[asyncio.Task[None]] = set()


def log_tool_state(event: ToolEvent) -> None:
    """Send ``event`` through the context's sink without waiting for it."""
    ctx = _context()
    if ctx is None or ctx.event_sink is None:
        logger.debug("no tool context or event sink; dropped %s", event.event_type)
        return
    try:
        content = _TOOLKIT_PAYLOADS[type(event.content)].model_validate(asdict(event.content))
        real = _toolkit.ToolEvent.for_context(ctx, event.event_type, content)
    except Exception:
        logger.exception("could not build the %s event of tool %s", event.event_type, ctx.tool_name)
        return
    _schedule(ctx.event_sink.emit(real), f"{event.event_type} of {ctx.tool_name}")


async def broadcast_ws_message(
    event_type: str, payload: Mapping[str, Any], session_id: str | None = None
) -> bool:
    """Send one view update through the context's sink; ``True`` when it was sent."""
    ctx = _context()
    if ctx is None or ctx.event_sink is None:
        logger.debug("no tool context or event sink; dropped view %s", event_type)
        return False
    if session_id is not None and str(session_id) != str(ctx.session_id):
        logger.warning(
            "view %s names session %s but the tool runs for session %s; dropped",
            event_type,
            session_id,
            ctx.session_id,
        )
        return False
    try:
        await ctx.event_sink.emit_view(_toolkit.ViewEvent(event_type, payload))
    except Exception:
        logger.exception("could not send view %s of tool %s", event_type, ctx.tool_name)
        return False
    return True


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
