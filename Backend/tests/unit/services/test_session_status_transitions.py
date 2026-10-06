"""Guarded session control-state transitions: intent only."""

from __future__ import annotations

import pytest

from application_api.db.models.sessions import SESSION_CONTROL_STATES
from application_api.repositories.sessions import _ALLOWED_TRANSITIONS

pytestmark = pytest.mark.unit


def _allowed(current: str, new: str) -> bool:
    allowed = _ALLOWED_TRANSITIONS[new]
    return not allowed or current in allowed


def test_the_control_states_are_intent_only():
    """Progress (planning, running, pausing, completed, failed) is derived, never stored."""
    assert set(SESSION_CONTROL_STATES) == {"active", "paused", "archived"}
    assert set(_ALLOWED_TRANSITIONS) == set(SESSION_CONTROL_STATES)


def test_pause_and_resume_are_idempotent_intent_switches():
    for current in ("active", "paused"):
        assert _allowed(current, "paused")
        assert _allowed(current, "active")


def test_any_session_can_be_archived():
    """Archiving is normal removal, so it is never blocked by the current state."""
    for current in SESSION_CONTROL_STATES:
        assert _allowed(current, "archived")


def test_an_archived_session_is_final():
    assert not _allowed("archived", "active")
    assert not _allowed("archived", "paused")
