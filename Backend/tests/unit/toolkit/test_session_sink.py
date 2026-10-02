"""SessionEventSink (merged plan, Phase 1.4 step 2)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_system.models.tool_logging import ToolEvent as LegacyToolEvent
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.events import EventSink, TextPayload, ToolEvent, ViewEvent
from agent_system.toolkit.runtime.dispatch import Dispatcher
from agent_system.toolkit.runtime.session_sink import SessionEventSink
from agent_system.toolkit.tools.registry import spec_of, tool

pytestmark = pytest.mark.unit

SESSION_ID = "5f0c1c3e-6f7e-4d7a-9c55-0d8f7c0e2a11"


class _Monitor:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def on_tool_event(self, event: Any) -> None:
        self.events.append(event)


class _Manager:
    def __init__(self) -> None:
        self.frames: list[tuple[str, Any, str]] = []

    async def send_ws_message(self, event_type: str, payload: Any, session_id: str = "") -> None:
        self.frames.append((event_type, payload, session_id))


@dataclass
class _Session:
    session_id: str = SESSION_ID
    execution_monitor: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def _event(session_id: str = SESSION_ID) -> ToolEvent:
    ctx = ToolContext(session_id=session_id, caller="agent", agent_type="segmentation_agent")
    return ToolEvent.for_context(ctx.for_tool("list_models"), "tool_log", TextPayload(message="hi"))


def test_it_is_an_event_sink():
    assert isinstance(SessionEventSink(_Session(), _Manager()), EventSink)


def test_tool_events_go_to_the_running_monitor():
    monitor, manager = _Monitor(), _Manager()
    sink = SessionEventSink(_Session(execution_monitor=monitor), manager)
    event = _event()

    asyncio.run(sink.emit(event))

    assert monitor.events == [event]
    assert manager.frames == []  # the monitor sends the frame itself; never twice


def test_without_a_monitor_tool_events_go_straight_to_the_socket():
    manager = _Manager()

    asyncio.run(SessionEventSink(_Session(), manager).emit(_event()))

    ((event_type, payload, session_id),) = manager.frames
    assert (event_type, session_id) == ("tool_update", SESSION_ID)
    assert payload["source"] == "segmentation_agent"
    assert payload["tool_name"] == "list_models"
    assert isinstance(payload["timestamp"], str)  # JSON-ready
    assert LegacyToolEvent.model_validate(payload).content.message == "hi"


def test_a_monitor_attached_later_is_used():
    session, monitor, manager = _Session(), _Monitor(), _Manager()
    sink = SessionEventSink(session, manager)

    asyncio.run(sink.emit(_event()))
    session.execution_monitor = monitor  # a run starts after the sink was made
    asyncio.run(sink.emit(_event()))

    assert len(manager.frames) == 1
    assert len(monitor.events) == 1


def test_view_events_go_to_the_socket_as_their_own_type():
    manager = _Manager()
    sink = SessionEventSink(_Session(execution_monitor=_Monitor()), manager)

    asyncio.run(sink.emit_view(ViewEvent("features_output", {"tool": "radiomic_extraction"})))

    assert manager.frames == [("features_output", {"tool": "radiomic_extraction"}, SESSION_ID)]
    assert type(manager.frames[0][1]) is dict  # a plain dict, not the frozen view


def test_another_sessions_event_is_refused():
    monitor, manager = _Monitor(), _Manager()
    sink = SessionEventSink(_Session(execution_monitor=monitor), manager)

    with pytest.raises(ValueError, match="sent to the sink of session"):
        asyncio.run(sink.emit(_event(session_id="another-session")))

    assert monitor.events == [] and manager.frames == []


def test_a_dispatched_call_reaches_the_socket():
    manager = _Manager()
    ctx = ToolContext(
        session_id=SESSION_ID,
        caller="main",
        agent_type="main_agent",
        event_sink=SessionEventSink(_Session(), manager),
    )

    async def on_result(result: Any, ctx: ToolContext) -> list[ViewEvent]:
        return [ViewEvent("population_output", {"tool": ctx.tool_name, "data": result})]

    @tool(
        pack="db_reads", side_effects="none", cost="light", execution="inline",
        visibility={"main"}, read_only=True, on_result=on_result,
    )  # fmt: skip
    async def get_patient(patient_id: str) -> dict[str, Any]:
        """Read one patient."""
        return {"patient_id": patient_id}

    asyncio.run(Dispatcher().invoke(spec_of(get_patient), {"patient_id": "p1"}, ctx))

    assert [(event_type, session_id) for event_type, _, session_id in manager.frames] == [
        ("tool_update", SESSION_ID),
        ("tool_update", SESSION_ID),
        ("population_output", SESSION_ID),
    ]
    started, finished, view = (payload for _, payload, _ in manager.frames)
    assert (started["event_type"], finished["event_type"]) == ("tool_started", "tool_finished")
    assert started["source"] == "main_agent"
    assert view == {"tool": "get_patient", "data": {"patient_id": "p1"}}
