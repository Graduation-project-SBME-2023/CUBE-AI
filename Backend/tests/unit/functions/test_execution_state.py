"""Execution progress is read from `agent_runs`, not from remembered strings."""

from __future__ import annotations

import pytest

from services import execution_state as module


@pytest.mark.unit
def test_first_incomplete_run_preserves_original_mapping():
    runs = [
        {"step_id": "population", "status": "completed"},
        {"step_id": "segmentation", "status": "running"},
        {"step_id": "features", "status": "pending"},
    ]

    assert module.find_first_incomplete_run(runs) is runs[1]
    assert (
        module.find_first_incomplete_run(
            [{"status": "completed"}, {"status": "completed"}]
        )
        is None
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "READY"),
        # Approval creates every run pending: admitted, nothing executed.
        (["pending", "pending"], "READY"),
        (["completed", "completed"], "COMPLETED"),
        (["completed", "failed"], "FAILED"),
        # A persisted RUNNING row cannot mean a live asyncio task.
        (["running", "pending"], "PAUSED"),
    ],
)
def test_recovered_session_state_is_safe(statuses, expected):
    runs = [{"status": status} for status in statuses]
    assert module.infer_recovered_session_state(runs) == expected


@pytest.mark.unit
def test_execution_has_started_only_once_a_run_leaves_pending():
    assert not module.execution_has_started([{"status": "pending"}] * 2)
    assert module.execution_has_started([{"status": "completed"}, {"status": "pending"}])
    assert module.execution_has_started([{"status": "running"}])


@pytest.mark.unit
def test_planning_restart_guard_is_unchanged():
    assert not module.planning_start_allowed(has_persisted_state=True, restart=False)
    assert module.planning_start_allowed(has_persisted_state=True, restart=True)
    assert module.planning_start_allowed(has_persisted_state=False, restart=False)
