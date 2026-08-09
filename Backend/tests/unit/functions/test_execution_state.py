from __future__ import annotations

import pytest

from services import execution_state as module


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        (" waiting for review ", "WAITING_FOR_REVIEW"),
        ("done", "DONE"),
        ("Canceled", "CANCELED"),
    ],
)
def test_normalize_step_status(raw, expected):
    assert module.normalize_step_status(raw) == expected


@pytest.mark.unit
def test_first_incomplete_step_preserves_original_mapping():
    steps = [
        {"id": "population", "status": "completed"},
        {"id": "segmentation", "status": "waiting for review"},
        {"id": "features", "status": "pending"},
    ]

    assert module.find_first_incomplete_step(steps) is steps[1]
    assert module.find_first_incomplete_step(
        [{"status": "done"}, {"status": "completed"}]
    ) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "READY"),
        (["pending", "ready"], "READY"),
        (["completed", "done"], "COMPLETED"),
        (["completed", "failed"], "FAILED"),
        (["running", "pending"], "PAUSED"),
        (["waiting for labels", "pending"], "PAUSED"),
    ],
)
def test_recovered_session_state_is_safe(statuses, expected):
    steps = [{"status": status} for status in statuses]
    assert module.infer_recovered_session_state(steps) == expected


@pytest.mark.unit
def test_restart_is_required_after_execution_progress():
    untouched = [{"status": "pending"}, {"status": "not confirmed"}]
    started = [{"status": "completed"}, {"status": "pending"}]

    assert module.execution_start_allowed(untouched, restart=False)
    assert not module.execution_start_allowed(started, restart=False)
    assert module.execution_start_allowed(started, restart=True)
    assert not module.planning_start_allowed(
        has_persisted_state=True,
        restart=False,
    )
    assert module.planning_start_allowed(
        has_persisted_state=True,
        restart=True,
    )

