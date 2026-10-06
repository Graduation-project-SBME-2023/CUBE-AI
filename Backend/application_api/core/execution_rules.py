"""The pure rules of execution: run outputs and
expectations, retries, and the derived session activity.

Nothing here reads the database; the repositories pass in what they read under
the session lock, so each rule is tested on its own.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------
# Outputs: {v, ports: {port: {type, ref: {table, ids}, summary, phi}}}
# ----------------------------------------------------------------------
# Tables a port may reference, with the key column. Produced tables must hold
# rows of this run; reference tables only need the rows to exist; `node_runs`
# is the run itself (a ResultSummary).
PRODUCED_TABLES: dict[str, str] = {
    "cohorts": "cohort_id",
    "segmentation_batches": "segmentation_batch_id",
    "feature_sets": "feature_set_id",
    "model_results": "model_result_id",
    "reports": "report_id",
    "stored_files": "file_id",
}
REFERENCE_TABLES: dict[str, str] = {
    "volume_series": "volume_series_instance_uid",
    "studies": "study_uid",
}
SELF_TABLE = "node_runs"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutputRef(_Strict):
    table: str
    ids: list[str] = Field(min_length=1)


class OutputPort(_Strict):
    type: str = Field(min_length=1)
    ref: OutputRef
    summary: Optional[dict[str, Any]] = None
    phi: bool = False


class RunOutputs(_Strict):
    v: Literal[1] = 1
    ports: dict[str, OutputPort]

    def tables(self) -> set[str]:
        return {port.ref.table for port in self.ports.values()}


def unknown_output_tables(outputs: RunOutputs) -> list[str]:
    allowed = PRODUCED_TABLES.keys() | REFERENCE_TABLES.keys() | {SELF_TABLE}
    return sorted(outputs.tables() - allowed)


# ----------------------------------------------------------------------
# Expectations: a violation holds the next node
# ----------------------------------------------------------------------
_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_MISSING = object()


def _metric(outputs: dict[str, Any], metric: str, port: Optional[str]) -> Any:
    ports = outputs.get("ports", {})
    candidates = [ports.get(port, {})] if port else list(ports.values())
    for candidate in candidates:
        summary = candidate.get("summary") or {}
        if metric in summary:
            return summary[metric]
    return _MISSING


def violated_expectations(
    expectations: Optional[dict[str, Any]], outputs: dict[str, Any]
) -> list[str]:
    """The rules a run's outputs break. A metric the summaries do not report is
    a violation: an expectation that cannot be checked must not pass silently."""
    failures = []
    for rule in (expectations or {}).get("rules", []):
        value = _metric(outputs, rule["metric"], rule.get("port"))
        try:
            ok = value is not _MISSING and _OPS[rule["op"]](value, rule["value"])
        except TypeError:
            ok = False
        if not ok:
            seen = "missing" if value is _MISSING else repr(value)
            failures.append(f"{rule['metric']} {rule['op']} {rule['value']!r} (got {seen})")
    return failures


# ----------------------------------------------------------------------
# Retries
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LatestAttempt:
    attempt_number: int
    state: str
    dispatched: bool


@dataclass(frozen=True, slots=True)
class RetryVerdict:
    allowed: bool
    code: str = ""
    reason: str = ""


def retry_verdict(
    *,
    run_status: str,
    latest: LatestAttempt,
    policy: dict[str, Any],
    explicit: bool,
    pending_tools: Optional[list[dict[str, Any]]] = None,
) -> RetryVerdict:
    """Whether a failed or cancelled run may get another attempt; the readiness
    checks belong to the start itself. ``explicit`` is a user or Recovery Agent retry; ``pending_tools`` comes from the latest
    checkpoint of a resumable run."""

    def refuse(code: str, reason: str) -> RetryVerdict:
        return RetryVerdict(False, code, reason)

    # A cancelled run or attempt is retried only explicitly.
    if run_status == "cancelled" or latest.state == "cancelled":
        if not explicit:
            return refuse("CANCELLED", "a cancelled run is retried only by an explicit retry")
    elif run_status != "failed":
        return refuse("NOT_RETRYABLE", f"the run is {run_status}; only a failed run is retried")
    # Only a finished latest attempt is retried (the database also allows one active attempt).
    if latest.state not in ("failed", "interrupted", "cancelled"):
        return refuse("ATTEMPT_ACTIVE", f"attempt {latest.attempt_number} is {latest.state}")
    # The attempt budget.
    max_attempts = int(policy.get("max_attempts", 1))
    if latest.attempt_number >= max_attempts:
        return refuse("MAX_ATTEMPTS", f"the run allows {max_attempts} attempt(s)")
    # Work that may already have had effects is not repeated automatically.
    if latest.state == "interrupted" and latest.dispatched and not explicit:
        if policy.get("resumable"):
            unsafe = [tool["name"] for tool in pending_tools or [] if not tool.get("idempotent", False)]
            if unsafe:
                return refuse(
                    "NEEDS_DECISION",
                    f"the checkpoint has unfinished non-idempotent tool calls: {', '.join(unsafe)}",
                )
        elif not policy.get("idempotent", False):
            return refuse(
                "NEEDS_DECISION",
                "the interrupted attempt was dispatched and the target is not idempotent",
            )
    return RetryVerdict(True)


def auto_retry_left(
    *, run_status: str, latest: LatestAttempt, policy: dict[str, Any],
    pending_tools: Optional[list[dict[str, Any]]] = None,
) -> bool:
    return retry_verdict(
        run_status=run_status, latest=latest, policy=policy, explicit=False,
        pending_tools=pending_tools,
    ).allowed


# ----------------------------------------------------------------------
# Derived activity
# ----------------------------------------------------------------------
Activity = Literal["idle", "running", "pausing", "awaiting_user", "awaiting_approval", "blocked"]


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    control_state: str
    # States of the session's non-terminal attempts (at most one now).
    active_attempts: tuple[tuple[str, Optional[str]], ...]  # (state, suspension kind)
    active_hold: bool
    failed_without_auto_retry: bool
    pending_change: bool


def derive_activity(facts: ActivityFacts) -> Activity:
    states = {state for state, _ in facts.active_attempts}
    working = bool(states & {"preparing", "submitted", "running"})
    if facts.active_hold or facts.failed_without_auto_retry or "unknown" in states:
        return "blocked"
    if facts.control_state == "paused" and working:
        return "pausing"
    if any(state == "suspended" and kind == "gate" for state, kind in facts.active_attempts):
        return "awaiting_user"
    if working:
        return "running"
    if facts.pending_change:
        return "awaiting_approval"
    return "idle"
