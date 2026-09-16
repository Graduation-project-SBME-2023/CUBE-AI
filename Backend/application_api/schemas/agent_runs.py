"""Agent-run schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import (
    AGENT_RUN_JSON_MAX_BYTES,
    COMPILED_PROMPT_MAX_BYTES,
    JsonBoundsError,
    check_json,
)

SCRATCHPAD_SCHEMA_VERSION = 1

ScratchpadStatus = Literal["in_progress", "done", "blocked", "failed"]
FlagLevel = Literal["info", "warning", "error", "critical"]
AgentOutputStatus = Literal["completed", "failed", "partial"]


def _bounded(value: Any, what: str) -> Any:
    try:
        return check_json(value, max_bytes=AGENT_RUN_JSON_MAX_BYTES, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class AgentRunStepOut(BaseModel):
    """The step definition, read through its run — never copied onto it."""

    step_id: UUID
    step_order: int
    agent_type: str
    title: str
    instructions: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class AgentRunOut(BaseModel):
    agent_run_id: UUID
    step_id: UUID
    session_id: UUID
    status: str
    compiled_prompt: Optional[str] = None
    input_refs: Optional[dict[str, Any]] = None
    durable_notes: Optional[dict[str, Any]] = None
    warnings: Optional[dict[str, Any]] = None
    output_summary: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    step: Optional[AgentRunStepOut] = None

    model_config = ConfigDict(from_attributes=True)


class AgentRunEventOut(BaseModel):
    """The event a transition published,with the cursor it committed at."""

    event_id: int
    msg_type: str
    payload: Any
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentRunTransitionOut(BaseModel):
    run: AgentRunOut
    event: Optional[AgentRunEventOut] = None


class AgentRunStart(BaseModel):
    """Claim a pending (or interrupted) run."""

    compiled_prompt: str = Field(min_length=1)
    input_refs: dict[str, Any]

    @field_validator("compiled_prompt")
    @classmethod
    def _prompt_bounded(cls, value: str) -> str:
        if len(value.encode("utf-8")) > COMPILED_PROMPT_MAX_BYTES:
            raise ValueError(
                f"compiled_prompt exceeds {COMPILED_PROMPT_MAX_BYTES} bytes"
            )
        return value

    @field_validator("input_refs")
    @classmethod
    def _refs_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("schema_version") != 1:
            raise ValueError("input_refs must carry schema_version = 1")
        return _bounded(value, "input_refs")


class AgentRunComplete(BaseModel):
    output_summary: Optional[dict[str, Any]] = None

    @field_validator("output_summary")
    @classmethod
    def _bounded_summary(cls, value):
        return None if value is None else _bounded(value, "output_summary")


class AgentRunFail(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=8000)
    details: Optional[dict[str, Any]] = None

    @field_validator("details")
    @classmethod
    def _bounded_details(cls, value):
        return None if value is None else _bounded(value, "error details")


# ----------------------------------------------------------------------
# Scratchpad (the MCP signatures are unchanged; only the storage moved)
# ----------------------------------------------------------------------
class ScratchpadRegister(BaseModel):
    role: str = Field(min_length=1, max_length=64)


class ScratchpadNote(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class ScratchpadFlag(BaseModel):
    level: FlagLevel
    message: str = Field(min_length=1, max_length=2_000)


class ScratchpadSetStatus(BaseModel):
    status: ScratchpadStatus


class ScratchpadSummary(BaseModel):
    summary: str = Field(min_length=1, max_length=20_000)
    overwrite: bool = False


class AgentOutputWrite(BaseModel):
    """`write_agent_output`, unchanged in shape and no longer a file."""

    agent_role: str = Field(min_length=1, max_length=128)
    status: AgentOutputStatus
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_files: dict[str, Any] = Field(default_factory=dict)
    output_metrics: dict[str, Any] = Field(default_factory=dict)
    output_summary: str = Field(default="", max_length=20_000)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("inputs", "output_files", "output_metrics")
    @classmethod
    def _bounded_documents(cls, value):
        return _bounded(value, "agent output document")
