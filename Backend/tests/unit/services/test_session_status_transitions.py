"""Guarded session status transitions."""

from __future__ import annotations

import pytest

from application_api.repositories.sessions import _ALLOWED_TRANSITIONS

pytestmark = pytest.mark.unit

STATUSES = (
    "planning",
    "ready",
    "running",
    "pausing",
    "paused",
    "completed",
    "failed",
    "archived",
)


def _allowed(current: str, new: str) -> bool:
    allowed = _ALLOWED_TRANSITIONS[new]
    return not allowed or current in allowed


def test_every_status_has_a_transition_rule():
    assert set(_ALLOWED_TRANSITIONS) == set(STATUSES)


def test_pause_is_a_two_step_durable_path():
    assert _allowed("running", "pausing")
    assert _allowed("pausing", "paused")
    # You cannot jump straight from running to paused: the intermediate state
    # is what makes a pause request durable if the process dies mid-request.
    assert not _allowed("running", "paused")


def test_a_paused_session_resumes_to_running():
    assert _allowed("paused", "running")


def test_completion_only_follows_a_run():
    assert _allowed("running", "completed")
    for status in ("planning", "ready", "paused", "failed", "archived"):
        assert not _allowed(status, "completed")


def test_failure_is_reachable_from_every_live_state():
    for status in ("planning", "ready", "running", "pausing", "paused"):
        assert _allowed(status, "failed")


def test_any_session_can_be_archived():
    """Archiving is normal removal, so it is never blocked by status."""
    for status in STATUSES:
        assert _allowed(status, "archived")


def test_a_completed_session_cannot_go_back_to_running():
    assert not _allowed("completed", "running")
    assert not _allowed("archived", "running")
