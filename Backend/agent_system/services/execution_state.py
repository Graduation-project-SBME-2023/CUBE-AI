"""Reading execution progress off the durable record."""

from collections.abc import Iterable, Mapping

# The four node_runs statuses. A human-in-the-loop wait is NOT one of them:
# an agent waiting for a review is still running, and the wait is carried as a
# session_events row rather than as a fifth status nothing could transition out of.
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"


def run_statuses(runs: Iterable[Mapping[str, object]]) -> list[str]:
    return [str(run.get("status") or PENDING).strip().lower() for run in runs]


def find_first_incomplete_run(
    runs: Iterable[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Where a resume continues. Nothing is deleted to make room for it."""
    for run in runs:
        if str(run.get("status") or PENDING).strip().lower() != COMPLETED:
            return run
    return None


def execution_has_started(runs: Iterable[Mapping[str, object]]) -> bool:
    """True once any run has left `pending`."""
    statuses = run_statuses(runs)
    return bool(statuses) and any(status != PENDING for status in statuses)


def planning_start_allowed(*, has_persisted_state: bool, restart: bool) -> bool:
    return restart or not has_persisted_state


def infer_recovered_session_state(
    runs: Iterable[Mapping[str, object]],
) -> str:
    """The safe in-memory state for a session hydrated from persistence."""
    statuses = run_statuses(runs)
    if not statuses:
        return "READY"
    if all(status == COMPLETED for status in statuses):
        return "COMPLETED"
    if any(status == FAILED for status in statuses):
        return "FAILED"
    if any(status == RUNNING for status in statuses):
        return "PAUSED"
    if execution_has_started({"status": status} for status in statuses):
        return "PAUSED"
    return "READY"
