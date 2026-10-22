"""Node-run schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import (
    COMPILED_PROMPT_MAX_BYTES,
    NODE_RUN_JSON_MAX_BYTES,
    JsonBoundsError,
    check_json,
)
from application_api.schemas.run_attempts import RunAttemptOut

SCRATCHPAD_SCHEMA_VERSION = 1

ScratchpadStatus = Literal["in_progress", "done", "blocked", "failed"]
FlagLevel = Literal["info", "warning", "error", "critical"]
AgentOutputStatus = Literal["completed", "failed", "partial"]


def _bounded(value: Any, what: str) -> Any:
    try:
        return check_json(value, max_bytes=NODE_RUN_JSON_MAX_BYTES, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class NodeRunNodeOut(BaseModel):
    """The node definition, read through its run — never copied onto it."""

    node_id: UUID
    node_seq: int
    position: int
    target_kind: str
    target_name: str
    title: str
    params: dict[str, Any] = Field(default_factory=dict)
    removed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class NodeRunOut(BaseModel):
    run_id: UUID
    node_id: UUID
    session_id: UUID
    accepted_by_change_id: UUID
    execution_policy: dict[str, Any]
    target_version: str
    status: str
    superseded_at: Optional[datetime] = None
    outputs: Optional[dict[str, Any]] = None
    downstream_hold: Optional[str] = None
    hold_released_by_change_id: Optional[UUID] = None
    compiled_prompt: Optional[str] = None
    resolved_inputs: Optional[dict[str, Any]] = None
    scratchpad_notes: Optional[dict[str, Any]] = None
    scratchpad_warnings: Optional[dict[str, Any]] = None
    scratchpad_summary: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    node: Optional[NodeRunNodeOut] = None

    model_config = ConfigDict(from_attributes=True)


class NodeRunWithAttemptsOut(NodeRunOut):
    attempts: list[RunAttemptOut] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Start a node (first start or retry)
# ----------------------------------------------------------------------
class InputPin(BaseModel):
    """One pinned input port: the selected upstream node and its current
    completed run. The Application API fills `type` and `ref` from that
    run's outputs; when the caller sends them they must match."""

    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    run_id: UUID
    port: str = Field(min_length=1)
    type: Optional[str] = None
    ref: Optional[dict[str, Any]] = None


class ResolvedInputs(BaseModel):
    model_config = ConfigDict(extra="allow")

    v: Literal[1] = 1
    ports: dict[str, InputPin] = Field(default_factory=dict)


class ExecutionLimits(BaseModel):
    """The manifest's limits; the Application API adds `fresh`, `privacy_mode`
    and `seed_from` and freezes the result as the run's execution_policy."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=20)
    timeout_s: Optional[float] = Field(default=None, gt=0)
    idempotent: bool = False
    resumable: bool = False
    gate_timeout_s: Optional[float] = Field(default=None, gt=0)


class NodeStart(BaseModel):
    """POST /runs/start."""

    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    # What the Executor read; a different value means the graph moved on.
    accepted_by_change_id: UUID
    dispatch_kind: Literal["in_process", "job", "inline"]
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    # First start only.
    resolved_inputs: Optional[ResolvedInputs] = None
    compiled_prompt: Optional[str] = Field(default=None, min_length=1)
    # Job attempts: the exact submitted request, persisted before dispatch.
    request: Optional[dict[str, Any]] = None
    # The agent's spec digest and the engine version, frozen in the policy and
    # compared with a seed_from source run (D27).
    spec_digest: Optional[str] = Field(default=None, max_length=256)
    engine_version: Optional[str] = Field(default=None, max_length=128)
    # A retry by the user or the Recovery Agent.
    explicit_retry: bool = False

    @field_validator("compiled_prompt")
    @classmethod
    def _prompt_bounded(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value.encode("utf-8")) > COMPILED_PROMPT_MAX_BYTES:
            raise ValueError(f"compiled_prompt exceeds {COMPILED_PROMPT_MAX_BYTES} bytes")
        return value

    @field_validator("request")
    @classmethod
    def _request_bounded(cls, value):
        return None if value is None else _bounded(value, "attempt request")


class NodeStartOut(BaseModel):
    run: NodeRunOut
    attempt: RunAttemptOut
    first_start: bool


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
    """`write_agent_output`, no longer a file. The tool's summary argument
    arrives here as `summary`."""

    agent_role: str = Field(min_length=1, max_length=128)
    status: AgentOutputStatus
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_files: dict[str, Any] = Field(default_factory=dict)
    output_metrics: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default="", max_length=20_000)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("inputs", "output_files", "output_metrics")
    @classmethod
    def _bounded_documents(cls, value):
        return _bounded(value, "agent output document")
