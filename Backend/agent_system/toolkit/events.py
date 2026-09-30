"""Tool events and the sink they go to (merged plan, Phase 1.1 step 6).

``ToolEvent``, ``EventType`` and the payload classes move here from
``Backend/mcp/tool_logging.py``. The wire shape is kept: the Agent System's
``models/tool_logging.ToolEvent`` and ``pipeline_monitor.on_tool_event`` read
``source``, ``session_id`` and ``model_dump(mode="json")``, and the canvas
renders by ``content_type``. The new fields (``tool_name``, ``subagent``,
``job_id``, ``node_id``) are optional, so old readers ignore them.

**Rule:** ``source`` is the node's ref name (an agent type or a tool name), or
``main_agent`` for the Main Agent. ``PlaygroundCanvas.tsx`` maps events to
nodes by it. ``ToolEvent.for_context()`` applies the rule; build events with
it rather than setting ``source`` by hand.

Two kinds of event reach an ``EventSink``: ``ToolEvent`` (tool lifecycle, logs,
progress) and ``ViewEvent`` (a structured update for one frontend view, such as
``population_output``, produced by a tool's ``on_result`` hook).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context import Caller

if TYPE_CHECKING:
    from .context import ToolContext

type EventType = Literal[
    "tool_started",
    "tool_progress",
    "tool_log",
    "tool_finished",
    "run_started",
    "run_finished",
    "error",
]

type ContentType = Literal["markdown", "progress", "text"]

MAIN_AGENT_SOURCE = "main_agent"


class MarkdownPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    markdown: str


class ProgressPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: int | float
    total: int
    label: str | None = None


class TextPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: str


type ContentPayload = MarkdownPayload | ProgressPayload | TextPayload

_CONTENT_TYPES: dict[type[BaseModel], str] = {
    MarkdownPayload: "markdown",
    ProgressPayload: "progress",
    TextPayload: "text",
}


def event_source(ctx: ToolContext) -> str:
    """The ``source`` of an event emitted under ``ctx`` (see the module rule)."""
    if ctx.caller is Caller.MAIN:
        return MAIN_AGENT_SOURCE
    # An agent node's events carry the agent, even for the tools it calls and
    # the internal agents it starts (those are in tool_name / subagent).
    source = ctx.agent_type or ctx.tool_name
    if not source:
        raise ValueError("the context names neither an agent nor a tool; no event source")
    return source


class ToolEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType
    source: str
    content_type: ContentType
    content: ContentPayload
    session_id: str | None = None
    # Timezone-aware, so the browser's `new Date(...)` reads it correctly.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tool_name: str | None = None
    subagent: str | None = None
    job_id: str | None = None
    node_id: str | None = None

    @model_validator(mode="after")
    def _content_matches_its_type(self) -> Self:
        expected = _CONTENT_TYPES[type(self.content)]
        if self.content_type != expected:
            raise ValueError(
                f"content_type={self.content_type!r} but content is "
                f"{type(self.content).__name__} ({expected!r})"
            )
        return self

    @classmethod
    def for_context(
        cls, ctx: ToolContext, event_type: EventType, content: ContentPayload
    ) -> ToolEvent:
        """Build an event with its identity taken from ``ctx``."""
        return cls(
            event_type=event_type,
            source=event_source(ctx),
            content_type=_CONTENT_TYPES[type(content)],
            content=content,
            session_id=ctx.session_id,
            tool_name=ctx.tool_name,
            subagent=ctx.subagent,
            job_id=ctx.job_id,
            node_id=ctx.node_id,
        )


@dataclass(frozen=True)
class ViewEvent:
    """A structured update for one frontend view, sent as a WebSocket frame of
    this ``type`` (the view's event type, e.g. ``"features_output"``)."""

    type: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type.strip():
            raise ValueError("a view event needs a non-empty type")
        if not isinstance(self.payload, Mapping):
            raise TypeError(f"view event payload must be a mapping, not {self.payload!r}")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@runtime_checkable
class EventSink(Protocol):
    """Where a call's events go. The in-process ``SessionEventSink`` (Phase 1.4)
    forwards to the session's monitor and WebSocket manager; a job worker's
    sink writes progress to the job record (Plan 5)."""

    async def emit(self, event: ToolEvent) -> None: ...

    async def emit_view(self, event: ViewEvent) -> None: ...
