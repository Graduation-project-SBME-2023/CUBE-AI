"""Recovery that deletes nothing."""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class RecoveryConflict(RuntimeError):
    """Replayed content differs from what the key already holds."""


def reconcile_replayed_payload(
    key: str,
    existing: Mapping[str, Any] | None,
    replayed: Mapping[str, Any],
    *,
    compare_fields: tuple[str, ...],
) -> str:
    """Insert-or-read **and compare** — the rule makes load-bearing."""
    if existing is None:
        return "insert"
    differing = [
        field
        for field in compare_fields
        if existing.get(field) != replayed.get(field)
    ]
    if not differing:
        return "reuse"
    raise RecoveryConflict(
        f"{key} already exists with different {', '.join(differing)}; "
        "reconciliation is required before this step can continue"
    )


def describe_resume(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """A human-readable account of what a resume will continue — and keep."""
    completed = [run for run in runs if str(run.get("status")) == "completed"]
    interrupted = next(
        (run for run in runs if str(run.get("status")) != "completed"), None
    )
    return {
        "completed_runs": len(completed),
        "total_runs": len(runs),
        "resumes_at_node_id": str(interrupted.get("node_id")) if interrupted else None,
        "deleted_artifacts": [],
    }
