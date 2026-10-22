"""Run-attempt schemas (dispatch, reconcile, complete, cancel, suspend, answer,
pending tools)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


SuspensionForm = Literal["cohort_approval", "segmentation_review"]


class Suspension(_Request):
    """T10 (v2): an `ask_user` question, a tool approval, or a pause. A
    question or a tool approval names the tool call it suspends on; a domain
    form (the cohort approval, the segmentation review) is a form of `ask_user`."""

    kind: Literal["ask_user", "tool_approval", "pause"]
    tool_use_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    form: Optional[SuspensionForm] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _shape(self) -> Suspension:
        if (self.kind == "pause") != (self.tool_use_id is None):
            raise ValueError("a question or a tool approval names its tool_use_id; a pause does not")
        if self.form is not None and self.kind != "ask_user":
            raise ValueError("only an ask_user question is a domain form")
        return self


class AttemptSuspend(_Request):
    suspension: Suspension


class AttemptAnswer(_Request):
    """T10's answer step, the user's: an answer to an `ask_user` question or a
    decision on a tool approval, for the suspended call `tool_use_id`."""

    tool_use_id: str = Field(min_length=1, max_length=256)
    answer: Optional[Any] = None
    decision: Optional[Literal["approve", "reject"]] = None
    comment: Optional[str] = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _one(self) -> AttemptAnswer:
        if (self.answer is None) == (self.decision is None):
            raise ValueError("send an answer or a decision, not both")
        return self


class PendingCall(_Request):
    tool_use_id: str = Field(min_length=1, max_length=256)
    tool: str = Field(min_length=1, max_length=255)
    idempotent: bool
    started_at: datetime


class PendingTools(_Request):
    """The calls the attempt is running, written before a call runs and when it ends."""

    v: Literal[1] = 1
    calls: list[PendingCall] = Field(default_factory=list, max_length=200)


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
    pending_tools: Optional[dict[str, Any]] = None
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
