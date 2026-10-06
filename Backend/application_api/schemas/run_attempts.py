"""Run-attempt schemas (dispatch, reconcile, complete, cancel, suspend)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from application_api.core.execution_rules import RunOutputs


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttemptError(_Request):
    code: str = Field(min_length=1, max_length=64)
    detail: Optional[Any] = None


class Usage(_Request):
    v: Literal[1] = 1
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model: Optional[str] = None

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class AttemptDispatched(_Request):
    """The Executor's commit after dispatch: `running` for in-process and
    inline attempts (before spawning), `submitted` for a job (after sending)."""

    state: Literal["running", "submitted"]


class AttemptComplete(_Request):
    outputs: RunOutputs
    usage: Optional[Usage] = None


class AttemptFail(_Request):
    error: AttemptError
    usage: Optional[Usage] = None


class AttemptCancel(_Request):
    """A cancel request. A Watchdog timeout also records why."""

    reason: Literal["user", "timeout", "gate_timeout", "privacy", "harness"] = "user"
    detail: Optional[str] = Field(default=None, max_length=2000)


class AttemptStopped(_Request):
    """The observed stop of an attempt whose cancel was requested."""

    usage: Optional[Usage] = None


class AttemptReconcile(_Request):
    """What the backend reports for the attempt after a restart."""

    observed: Literal[
        "queued", "running", "succeeded", "failed", "cancelled", "not_found", "unreachable",
        "process_gone",
    ]
    outputs: Optional[RunOutputs] = None
    error: Optional[AttemptError] = None
    usage: Optional[Usage] = None


class Suspension(_Request):
    kind: Literal["gate", "pause"]
    gate: Optional[Literal["cohort_approval", "segmentation_review", "tool_approval"]] = None
    interrupt_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    checkpoint_id: str = Field(min_length=1)


class AttemptSuspend(_Request):
    suspension: Suspension


class GateDecision(_Request):
    """A user's decision in a gate with no domain row (`tool_approval`)."""

    interrupt_id: str = Field(min_length=1, max_length=256)
    decision: Literal["approve", "reject"]
    comment: Optional[str] = Field(default=None, max_length=4000)


class RunAttemptOut(BaseModel):
    attempt_id: UUID
    run_id: UUID
    session_id: UUID
    attempt_number: int
    dispatch_kind: str
    submission_key: Optional[str] = None
    request: Optional[dict[str, Any]] = None
    state: str
    suspension: Optional[dict[str, Any]] = None
    resumed_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    error: Optional[dict[str, Any]] = None
    usage: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ReconcileOut(BaseModel):
    """`action` tells the caller what to do next: nothing, re-attach, or
    resubmit the same key and request."""

    attempt: RunAttemptOut
    action: Literal["unchanged", "reattach", "resubmit", "recorded"]
