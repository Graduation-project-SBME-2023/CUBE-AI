# schemas/sessions.py
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SessionControlState = Literal[
    "planning",
    "ready",
    "running",
    "pausing",
    "paused",
    "completed",
    "failed",
    "archived",
]
PromptKind = Literal["hypothesis", "general"]


class SessionCreate(BaseModel):
    """Creating a session ALWAYS requires a non-empty raw prompt."""

    prompt: str = Field(min_length=1)
    prompt_kind: PromptKind = "hypothesis"
    title: Optional[str] = None

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class SessionUpdate(BaseModel):
    """`prompt` is deliberately absent: it is immutable once written."""

    title: Optional[str] = None
    control_state: Optional[SessionControlState] = None
    intent: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None


class SessionSummary(BaseModel):
    """Lightweight list item; message history is fetched separately."""

    session_id: UUID
    title: Optional[str] = None
    prompt: Optional[str] = None
    prompt_kind: PromptKind = "hypothesis"
    control_state: SessionControlState = "planning"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class Session(SessionSummary):
    intent: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)
