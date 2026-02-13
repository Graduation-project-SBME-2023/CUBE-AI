from dataclasses import dataclass, asdict, field
from typing import Literal, Union
import json
import time
import requests

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
    source: str                 # tool name or agent
    content_type: ContentType
    content: ContentPayload
    timestamp: float = field(default_factory=time.time)

def log_tool_state(event: ToolEvent):
    requests.post(
        "http://localhost:8150/events",
        json=asdict(event),
        timeout=1
    )