"""ToolEvent, ViewEvent and EventSink (merged plan, Phase 1.1 step 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from agent_system.models.tool_logging import ToolEvent as LegacyToolEvent
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.events import (
    MAIN_AGENT_SOURCE,
    EventSink,
    MarkdownPayload,
    ProgressPayload,
    TextPayload,
    ToolEvent,
    ViewEvent,
    event_source,
)

pytestmark = pytest.mark.unit


def _ctx(**overrides: Any) -> ToolContext:
    fields: dict[str, Any] = {"session_id": "s1", "caller": "agent"}
    fields.update(overrides)
    return ToolContext(**fields)


# --- source rule


@pytest.mark.parametrize(
    ("overrides", "source"),
    [
        # the Main Agent, whatever tool it calls
        ({"caller": "main", "agent_type": "main_agent", "tool_name": "get_patient"}, "main_agent"),
        ({"caller": "main"}, MAIN_AGENT_SOURCE),
        # an agent node: its agent, not the tool it calls
        ({"agent_type": "segmentation_agent", "tool_name": "list_models"}, "segmentation_agent"),
        # an internal agent: still the node's agent
        (
            {"agent_type": "segmentation_agent", "subagent": "mask_resolution_agent", "depth": 1},
            "segmentation_agent",
        ),
        # a Tool Node: the tool
        ({"caller": "node", "tool_name": "radiomics_extract_features"}, "radiomics_extract_features"),
    ],
)
def test_event_source(overrides, source):
    assert event_source(_ctx(**overrides)) == source


def test_event_source_needs_an_identity():
    with pytest.raises(ValueError, match="no event source"):
        event_source(_ctx())


# --- ToolEvent


def test_for_context_fills_identity_and_content_type():
    ctx = _ctx(
        agent_type="segmentation_agent",
        subagent="mask_resolution_agent",
        tool_name="list_models",
        job_id="job-1",
        node_id="node-1",
    )

    event = ToolEvent.for_context(ctx, "tool_progress", ProgressPayload(current=2, total=5))

    assert event.source == "segmentation_agent"
    assert event.content_type == "progress"
    assert (event.session_id, event.tool_name, event.subagent, event.job_id, event.node_id) == (
        "s1",
        "list_models",
        "mask_resolution_agent",
        "job-1",
        "node-1",
    )


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (MarkdownPayload(markdown="# done"), "markdown"),
        (TextPayload(message="started"), "text"),
        (ProgressPayload(current=0.5, total=1, label="volumes"), "progress"),
    ],
)
def test_content_type_follows_the_payload(content, content_type):
    event = ToolEvent.for_context(_ctx(tool_name="t"), "tool_log", content)

    assert event.content_type == content_type


def test_timestamp_is_timezone_aware_utc():
    before = datetime.now(UTC)
    event = ToolEvent.for_context(_ctx(tool_name="t"), "tool_started", TextPayload(message="x"))

    assert event.timestamp.tzinfo is not None
    assert before <= event.timestamp <= datetime.now(UTC)


def test_content_type_must_match_the_content():
    with pytest.raises(ValidationError, match="content_type='markdown' but content is TextPayload"):
        ToolEvent(
            event_type="tool_log",
            source="t",
            content_type="markdown",
            content=TextPayload(message="x"),
        )


@pytest.mark.parametrize(
    "overrides",
    [{"event_type": "tool_exploded"}, {"content_type": "html"}, {"agent_id": "legacy"}],
)
def test_malformed_events_are_refused(overrides):
    fields: dict[str, Any] = {
        "event_type": "tool_log",
        "source": "t",
        "content_type": "text",
        "content": {"message": "x"},
    }
    with pytest.raises(ValidationError):
        ToolEvent(**(fields | overrides))


def test_events_are_immutable():
    event = ToolEvent.for_context(_ctx(tool_name="t"), "tool_log", TextPayload(message="x"))

    with pytest.raises(ValidationError):
        event.source = "other"  # type: ignore[misc]


def test_the_agent_system_still_accepts_the_wire_shape():
    event = ToolEvent.for_context(
        _ctx(agent_type="segmentation_agent", tool_name="list_models", node_id="n1"),
        "tool_finished",
        MarkdownPayload(markdown="**3 models**"),
    )

    legacy = LegacyToolEvent.model_validate(event.model_dump(mode="json"))

    assert legacy.event_type == "tool_finished"
    assert legacy.source == "segmentation_agent"
    assert legacy.content_type == "markdown"
    assert legacy.content.markdown == "**3 models**"
    assert legacy.session_id == "s1"


def test_an_old_producer_event_still_parses():
    # What Backend/mcp/tool_logging.py posted (deleted in Plan 2.1): asdict() with a float timestamp.
    old = {
        "event_type": "tool_progress",
        "content_type": "progress",
        "content": {"current": 3, "total": 10, "label": None},
        "source": "feature_extraction_agent",
        "timestamp": 1_790_000_000.25,
        "session_id": "s1",
    }

    event = ToolEvent.model_validate(old)

    assert isinstance(event.content, ProgressPayload)
    assert event.tool_name is None
    assert event.timestamp.tzinfo is not None


# --- ViewEvent


def test_view_event_payload_is_a_frozen_copy():
    data = {"tool": "generate_targets", "status": "completed"}
    view = ViewEvent("population_output", data)

    data["status"] = "changed"

    assert view.payload["status"] == "completed"
    with pytest.raises(TypeError):
        view.payload["status"] = "x"  # type: ignore[index]


@pytest.mark.parametrize(("type_", "payload", "error"), [("", {}, ValueError), ("x", [1], TypeError)])
def test_malformed_view_events_are_refused(type_, payload, error):
    with pytest.raises(error):
        ViewEvent(type_, payload)


# --- EventSink


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: ToolEvent) -> None:
        self.events.append(event)

    async def emit_view(self, event: ViewEvent) -> None:
        self.events.append(event)


class _HalfSink:
    async def emit(self, event: ToolEvent) -> None: ...


def test_event_sink_protocol():
    assert isinstance(_RecordingSink(), EventSink)
    assert not isinstance(_HalfSink(), EventSink)
