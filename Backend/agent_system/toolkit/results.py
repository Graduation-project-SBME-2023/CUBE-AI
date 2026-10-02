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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


def validation_message(exc: ValidationError) -> str:
    """A pydantic error as one line a model can act on: ``field.path: reason; ...``."""
    return "; ".join(
        f"{'.'.join(map(str, error['loc'])) or 'value'}: {error['msg']}" for error in exc.errors()
    )


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

    ``change_id`` is the ``graph_changes`` row that accepted (or still awaits
    approval for) the node's work; an approval is a decision on that change.
    There is no ``run_id``: a run is created only when its node starts (D8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    status: NodeStatus

    def to_result(self) -> dict[str, Any]:
        """The JSON a tool returns to the model."""
        return self.model_dump(mode="json")
