"""toolpacks/common/emit.py: the tools' old event calls, sent to the context's
sink instead of over HTTP (merged plan, Phase 2.1)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from agent_system.toolkit.context import use_context
from agent_system.toolkit.events import ProgressPayload as ToolkitProgress
from agent_system.toolkit.events import TextPayload as ToolkitText
from agent_system.toolpacks.common import emit
from tests.support.toolkit_fixtures import FakeEventSink, make_context

pytestmark = pytest.mark.unit


def _run(coro_fn, ctx=None) -> Any:
    """Run ``coro_fn()`` on a fresh loop, under ``ctx`` when given, and let the
    emissions ``log_tool_state`` scheduled finish before the loop closes."""

    async def main() -> Any:
        if ctx is None:
            result = await coro_fn()
        else:
            with use_context(ctx):
                result = await coro_fn()
        # The shim logs a failed emission itself; only wait for them here.
        await asyncio.gather(*emit._pending, return_exceptions=True)
        await asyncio.sleep(0)  # let the done callbacks run
        return result

    return asyncio.run(main())


class _BrokenSink(FakeEventSink):
    async def emit(self, event) -> None:
        raise RuntimeError("socket gone")

    async def emit_view(self, event) -> None:
        raise RuntimeError("socket gone")


# --- log_tool_state


def test_an_event_reaches_the_sink_with_the_contexts_identity():
    sink = FakeEventSink()
    ctx = make_context(agent_type="segmentation_agent", tool_name="list_models", event_sink=sink)

    async def call() -> None:
        emit.log_tool_state(emit.ToolEvent("tool_started", "text", emit.TextPayload("hi")))

    _run(call, ctx)

    [event] = sink.events
    assert event.event_type == "tool_started"
    assert event.source == "segmentation_agent"  # the toolkit rule, not AGENT_ID
    assert event.session_id == ctx.session_id
    assert event.tool_name == "list_models"
    assert event.content == ToolkitText(message="hi")


def test_the_content_type_follows_the_payload_not_the_argument():
    sink = FakeEventSink()
    ctx = make_context(tool_name="t", event_sink=sink)

    async def call() -> None:
        # A real call site passes "text" with a progress payload.
        emit.log_tool_state(emit.ToolEvent("tool_progress", "text", emit.ProgressPayload(1, 4, "a")))

    _run(call, ctx)

    [event] = sink.events
    assert event.content_type == "progress"
    assert event.content == ToolkitProgress(current=1, total=4, label="a")


def test_log_tool_state_returns_before_the_sink_runs():
    sink = FakeEventSink()
    ctx = make_context(tool_name="t", event_sink=sink)
    seen_during_call: list[int] = []

    async def call() -> None:
        emit.log_tool_state(emit.ToolEvent("tool_log", "text", emit.TextPayload("x")))
        seen_during_call.append(len(sink.events))

    _run(call, ctx)

    assert seen_during_call == [0]
    assert len(sink.events) == 1


def test_without_a_context_the_event_is_dropped():
    async def call() -> None:
        emit.log_tool_state(emit.ToolEvent("tool_started", "text", emit.TextPayload("hi")))

    _run(call)  # a tool called directly, as unit tests do: no error


def test_without_a_sink_the_event_is_dropped():
    async def call() -> None:
        emit.log_tool_state(emit.ToolEvent("tool_started", "text", emit.TextPayload("hi")))

    _run(call, make_context(tool_name="t"))


def test_a_payload_the_toolkit_rejects_costs_a_log_line_not_the_tool(caplog):
    sink = FakeEventSink()
    ctx = make_context(tool_name="t", event_sink=sink)

    async def call() -> None:
        emit.log_tool_state(emit.ToolEvent("tool_progress", "progress", emit.ProgressPayload(1, 2.5)))

    with caplog.at_level(logging.ERROR, logger=emit.__name__):
        _run(call, ctx)

    assert sink.events == []
    assert "could not build the tool_progress event" in caplog.text


def test_a_failing_sink_never_reaches_the_tool(caplog):
    ctx = make_context(tool_name="t", event_sink=_BrokenSink())

    async def call() -> str:
        emit.log_tool_state(emit.ToolEvent("tool_finished", "text", emit.TextPayload("done")))
        return "result"

    with caplog.at_level(logging.ERROR, logger=emit.__name__):
        assert _run(call, ctx) == "result"

    assert "socket gone" in caplog.text


# --- broadcast_ws_message


def test_a_view_reaches_the_sink():
    sink = FakeEventSink()
    ctx = make_context(tool_name="t", event_sink=sink)

    async def call() -> bool:
        return await emit.broadcast_ws_message("population_output", {"n": 3}, ctx.session_id)

    assert _run(call, ctx) is True
    [view] = sink.views
    assert view.type == "population_output"
    assert dict(view.payload) == {"n": 3}


def test_a_view_without_a_session_argument_uses_the_contexts():
    sink = FakeEventSink()
    ctx = make_context(tool_name="t", event_sink=sink)

    async def call() -> bool:
        return await emit.broadcast_ws_message("population_output", {})

    assert _run(call, ctx) is True
    assert len(sink.views) == 1


def test_a_view_for_another_session_is_dropped():
    sink = FakeEventSink()
    ctx = make_context(tool_name="t", event_sink=sink)

    async def call() -> bool:
        return await emit.broadcast_ws_message("population_output", {}, "another-session")

    assert _run(call, ctx) is False
    assert sink.views == []


def test_a_view_without_a_context_is_dropped():
    async def call() -> bool:
        return await emit.broadcast_ws_message("population_output", {}, "s1")

    assert _run(call) is False


def test_a_failing_view_sink_returns_false():
    ctx = make_context(tool_name="t", event_sink=_BrokenSink())

    async def call() -> bool:
        return await emit.broadcast_ws_message("population_output", {})

    assert _run(call, ctx) is False
