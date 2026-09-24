from dataclasses import dataclass, asdict, field
from typing import Literal, Union, Dict
import time
import os

from internal_emitter import fire_and_forget, post_internal

EventType = Literal[
    "tool_started",
    "tool_progress",
    "tool_log",
    "tool_finished",
    "run_started",
    "run_finished",
    "error"
]

ContentType = Literal[
    "markdown",
    "progress",
    "text",
]


@dataclass
class MarkdownPayload:
    markdown: str


@dataclass
class ProgressPayload:
    current: int
    total: int
    label: str | None = None


@dataclass
class TextPayload:
    message: str


ContentPayload = Union[
    MarkdownPayload,
    TextPayload,
    ProgressPayload,
]


@dataclass
class ToolEvent:
    event_type: EventType
    content_type: ContentType
    content: ContentPayload
    source: str = field(default_factory=lambda: os.environ.get("AGENT_ID", "unknown"))
    timestamp: float = field(default_factory=time.time)
    session_id: str = None


def log_tool_state(event: ToolEvent) -> None:
    """Fire-and-forget: never blocks the tool call that reports it (WS-07)."""
    session_id = os.environ.get("SESSION_ID")
    event.session_id = session_id
    fire_and_forget(post_internal("/events", asdict(event)))


def log_pdf_tool_view(event: Dict) -> None:
    session_id = os.environ.get("SESSION_ID")
    payload = {
        "event_type": "latex_output",
        "content": event,
        "session_id": session_id,
    }
    fire_and_forget(post_internal("/events", payload))
