"""Result shapes shared by every tool (merged plan, Phase 1.1 step 7).

- ``error_result()`` is the one error envelope. Expected failures return it;
  dispatch (Phase 1.4) also turns exceptions into it, so no traceback reaches
  the model. Success results keep each tool's own shape.
- ``RunAccepted`` is what submitting work returns: the node was accepted, and
  the caller does not wait for it.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Error codes are snake_case so the Watchdog, Recovery and the UI can match on them.
ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_ENVELOPE_KEYS = frozenset({"status", "error_code", "error_message"})


def error_result(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """``{"status": "error", "error_code": code, "error_message": message, **extra}``."""
    if not isinstance(code, str) or not ERROR_CODE_PATTERN.match(code):
        raise ValueError(f"error code {code!r} must match {ERROR_CODE_PATTERN.pattern}")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("an error result needs a message")
    clash = sorted(_ENVELOPE_KEYS & extra.keys())
    if clash:
        raise ValueError(f"extra fields may not override the envelope: {clash}")
    return {"status": "error", "error_code": code, "error_message": message, **extra}


def is_error_result(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "error" and "error_code" in result


class NodeStatus(StrEnum):
    """The status of a graph node (``graph_nodes.status``, Plan 6)."""

    PENDING = "pending"
    READY = "ready"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"  # waiting for a resource pool
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"  # an in-node gate (cohort approval, review)
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    HELD = "held"
    REMOVED = "removed"


class RunAccepted(BaseModel):
    """Returned at once when work is submitted; the Main Agent never waits on it.

    ``run_id`` is None until a run exists (e.g. while awaiting approval).
    ``approval_id`` is set exactly when ``status`` is ``awaiting_approval``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    run_id: str | None = None
    status: NodeStatus
    graph_version: int = Field(ge=0)
    approval_id: str | None = None

    @model_validator(mode="after")
    def _approval_matches_status(self) -> Self:
        awaiting = self.status is NodeStatus.AWAITING_APPROVAL
        if awaiting and not self.approval_id:
            raise ValueError("status 'awaiting_approval' needs an approval_id")
        if not awaiting and self.approval_id is not None:
            raise ValueError(f"approval_id is only set while awaiting approval, not {self.status}")
        return self

    def to_result(self) -> dict[str, Any]:
        """The JSON a tool returns to the model."""
        return self.model_dump(mode="json")
