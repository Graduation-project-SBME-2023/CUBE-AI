"""Graph-change schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.graph_content import DERIVED_REASONS, ChangeContent

# Reasons the Agent System guardrails compute from the target manifests; the
# Application API adds PLAN_MODE and supersedes_completed itself.
MANIFEST_REASON_PATTERN = r"^(destructive|generated_code|untrusted_tool|approval_required:[a-z0-9_.-]+)$"


class ToolCallRef(BaseModel):
    """The Main's control-tool call this change answers (D21). Its result is
    in the Main's SDK transcript; the call keys the change, so a replayed call
    returns the change it already made."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    tool_use_id: str = Field(min_length=1, max_length=256)


class GraphChangeSubmit(BaseModel):
    """POST /session-graphs/session/{id}/changes: applied when no approval
    reason applies, proposed otherwise."""

    model_config = ConfigDict(extra="forbid")

    author: Literal["main", "user", "harness"]
    base_version: int = Field(ge=0)
    content: ChangeContent
    approval_reasons: list[str] = Field(default_factory=list, max_length=32)
    # Required for the Main: the key is then sha256(session_id || tool_use_id).
    tool_call: Optional[ToolCallRef] = None

    @field_validator("approval_reasons")
    @classmethod
    def _known_reasons(cls, reasons: list[str]) -> list[str]:
        unknown = [
            reason for reason in reasons
            if reason not in DERIVED_REASONS and not re.match(MANIFEST_REASON_PATTERN, reason)
        ]
        if unknown:
            raise ValueError(f"unknown approval reasons: {unknown}")
        return sorted(set(reasons))


class GraphChangeDecision(BaseModel):
    """POST /graph-changes/{change_id}/decision: a user decision quoting the digest."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: Optional[str] = Field(default=None, max_length=4000)


class GraphChangeOut(BaseModel):
    change_id: UUID
    session_id: UUID
    author: str
    idempotency_key: str
    base_version: int
    content: dict[str, Any]
    content_digest: str
    approval_reasons: list[str]
    status: str
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    applied_version: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GraphChangeResult(BaseModel):
    """What the submission did: the change, the graph version after it, and its effect."""

    change: GraphChangeOut
    version: int
    node_ids: list[UUID] = Field(default_factory=list)
    affected: list[UUID] = Field(default_factory=list)
    superseded_run_ids: list[UUID] = Field(default_factory=list)
    replayed: bool = False


class GraphChangeHistory(BaseModel):
    pending: Optional[GraphChangeOut] = None
    changes: list[GraphChangeOut]
