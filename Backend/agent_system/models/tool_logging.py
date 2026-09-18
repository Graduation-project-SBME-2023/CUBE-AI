from typing import Literal, Union
from datetime import datetime
from pydantic import BaseModel, Field

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
    "text",  # could add more content types
]


class MarkdownPayload(BaseModel):
    markdown: str


class ProgressPayload(BaseModel):
    current: Union[int, float]
    total: int
    label: str | None = None


class TextPayload(BaseModel):
    message: str


ContentPayload = Union[
    MarkdownPayload,
    ProgressPayload,
    TextPayload,
]


class ToolEvent(BaseModel):
    event_type: EventType
    source: str  # tool name or agent
    content_type: ContentType
    content: ContentPayload
    session_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


