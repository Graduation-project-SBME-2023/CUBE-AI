# schemas/sessions.py
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SessionControlState = Literal["active", "paused", "archived"]
PromptKind = Literal["hypothesis", "general"]
ExecutionMode = Literal["auto", "plan"]
PrivacyMode = Literal["standard", "phi_local"]
Activity = Literal["idle", "running", "pausing", "awaiting_user", "awaiting_approval", "blocked"]


class SessionCreate(BaseModel):
    """Creating a session ALWAYS requires a non-empty raw prompt."""

    prompt: str = Field(min_length=1)
    prompt_kind: PromptKind = "hypothesis"
    title: Optional[str] = None
    execution_mode: ExecutionMode = "auto"
    privacy_mode: PrivacyMode = "standard"

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class SessionUpdate(BaseModel):
    """`prompt` is deliberately absent: it is immutable once written. So are
    the control state (its own guarded route), the settings (a switch may have to
    cancel attempts) and `intent` (legacy, no new writers)."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    error: Optional[dict[str, Any]] = None


class SessionSettingsUpdate(BaseModel):
    """PATCH /sessions/{id}/settings. A switch to phi_local also requests the
    cancellation of every attempt that runs under `standard`."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: Optional[ExecutionMode] = None
    privacy_mode: Optional[PrivacyMode] = None


class InboxAdvance(BaseModel):
    """POST /sessions/{id}/inbox-cursor."""

    model_config = ConfigDict(extra="forbid")

    through_event_id: int = Field(ge=0)
    tokens_used: int = Field(default=0, ge=0)


class SessionSummary(BaseModel):
    """Lightweight list item; message history is fetched separately."""

    session_id: UUID
    title: Optional[str] = None
    prompt: Optional[str] = None
    prompt_kind: PromptKind = "hypothesis"
    control_state: SessionControlState = "active"
    execution_mode: ExecutionMode = "auto"
    privacy_mode: PrivacyMode = "standard"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class Session(SessionSummary):
    user_id: UUID
    intent: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    tokens_used: int = 0
    inbox_handled_through_event_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)
