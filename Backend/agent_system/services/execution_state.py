from collections.abc import Iterable, Mapping

PENDING_STATUSES = {"", "PENDING", "NOT_CONFIRMED", "READY"}
COMPLETED_STATUSES = {"COMPLETED", "DONE"}
FAILED_STATUSES = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}
WAITING_STATUSES = {
    "PAUSED",
    "WAITING",
    "WAITING_FOR_REVIEW",
    "WAITING_FOR_USER_REVIEW",
    "WAITING_FOR_LABELS",
    "WAITING_FOR_RESOURCE",
    "WAITING_FOR_USER_INPUT",
}


def normalize_step_status(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def step_statuses(steps: Iterable[Mapping[str, object]]) -> list[str]:
    return [normalize_step_status(step.get("status")) for step in steps]


def find_first_incomplete_step(
    steps: Iterable[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Return the first persisted plan step that has not completed."""
    for step in steps:
        if normalize_step_status(step.get("status")) not in COMPLETED_STATUSES:
            return step
    return None


def execution_has_started(steps: Iterable[Mapping[str, object]]) -> bool:
    """
    Return True once a persisted plan contains execution progress.

    A plan whose steps are all pending/not-confirmed is safe to execute for the
    first time. Any other persisted status requires explicit restart intent.
    """
    statuses = step_statuses(steps)
    return bool(statuses) and any(status not in PENDING_STATUSES for status in statuses)


def execution_start_allowed(
    steps: Iterable[Mapping[str, object]],
    *,
    restart: bool,
) -> bool:
    return restart or not execution_has_started(steps)


def planning_start_allowed(*, has_persisted_state: bool, restart: bool) -> bool:
    return restart or not has_persisted_state


def infer_recovered_session_state(
    steps: Iterable[Mapping[str, object]],
) -> str:
    """
    Infer the safe in-memory state for a session hydrated from persistence.

    Persisted RUNNING cannot mean that an asyncio task is still alive after a
    process restart, so it is represented as PAUSED. Hydration must never imply
    permission to create a replacement execution task.
    """
    statuses = step_statuses(steps)
    if not statuses:
        return "READY"
    if all(status in COMPLETED_STATUSES for status in statuses):
        return "COMPLETED"
    if any(status in FAILED_STATUSES for status in statuses):
        return "FAILED"
    if any(status in WAITING_STATUSES or status == "RUNNING" for status in statuses):
        return "PAUSED"
    if execution_has_started({"status": status} for status in statuses):
        return "PAUSED"
    return "READY"
