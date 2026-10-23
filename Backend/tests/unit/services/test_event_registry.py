"""Every literal WebSocket event type this process emits must be registered
as durable or ephemeral (T12, WEBSOCKET_FIX_PLAN.md WS-05).

Static source scan, not an import of the emitting modules: several of them
(planner.py, pipeline_monitor.py) pull in AutoGen and the rest of the agent
stack, which unit tests must not touch. Each pattern below is scoped to one
file and the one call shape that file uses to emit, so a `"type"` key that
means something else in that file (a payload sub-field, an unrelated dict)
is never mistaken for the top-level event_type -- see event_registry.py's own
note on `scratchpad_update` vs. the `agent_status`/`agent_summary` strings
nested inside its payload, which must NOT be picked up here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.event_registry import DURABLE_EVENT_TYPES, EPHEMERAL_EVENT_TYPES

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[3]
AGENT_ROOT = BACKEND_ROOT / "agent_system"
TOOLPACKS_ROOT = BACKEND_ROOT / "agent_system" / "toolpacks"
APPLICATION_ROOT = BACKEND_ROOT / "application_api"

REGISTERED = DURABLE_EVENT_TYPES | EPHEMERAL_EVENT_TYPES


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find(pattern: str, text: str) -> set[str]:
    return set(re.findall(pattern, text))


# Each entry: (file, regex whose single capture group is the literal type,
# scoped to the exact call shape that file uses to emit).
_SOURCES: list[tuple[Path, str]] = [
    (AGENT_ROOT / "view_emitter.py", r'emit_view_update\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "services" / "pipeline_monitor.py", r'event_type\s*=\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "agents" / "planning_agents" / "planner.py", r'self\._send\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "services" / "session_service.py", r'self\._ws_send\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "api" / "runners.py", r'send_ws_message\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "api" / "routes" / "execution.py", r'send_ws_message\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "api" / "routes" / "events.py", r'event_type\s*=\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    # Tool-pack view emitters: the view type is emit_view()'s first argument,
    # in every tool-pack module that emits a view. (scratchpad.py's payloads
    # carry nested "type": "agent_status"/... keys; they are not event types
    # and the pattern only reads emit_view's first argument.)
    *[
        (path, r'emit_view\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"')
        for path in sorted(TOOLPACKS_ROOT.rglob("*.py"))
        if "await emit_view(" in path.read_text(encoding="utf-8")
    ],
    # application_api's own durable-event writers (session_events rows committed
    # alongside the domain change) -- reach the WS envelope's `type` field
    # via replay in agent_system/api/routes/sessions.py.
    (APPLICATION_ROOT / "repositories" / "node_runs.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "sessions.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "chats.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "graph_changes.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "run_attempts.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "cohorts.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "literature.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "feature_sets.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "model_results.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "reports.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "segmentation_batches.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
]


def test_every_scanned_source_file_exists():
    """Guards the scan itself: a renamed file would silently scan nothing
    and this test would pass for the wrong reason."""
    missing = [str(path) for path, _ in _SOURCES if not path.is_file()]
    assert not missing, f"Scanned files no longer exist: {missing}"


def test_no_type_is_classified_as_both_durable_and_ephemeral():
    overlap = DURABLE_EVENT_TYPES & EPHEMERAL_EVENT_TYPES
    assert not overlap


@pytest.mark.parametrize("path,pattern", _SOURCES, ids=[str(p) for p, _ in _SOURCES])
def test_every_literal_event_type_emitted_is_registered(path: Path, pattern: str):
    found = _find(pattern, _source(path))
    unregistered = found - REGISTERED
    assert not unregistered, (
        f"{path} emits {sorted(unregistered)} which is not in "
        "DURABLE_EVENT_TYPES or EPHEMERAL_EVENT_TYPES (event_registry.py)"
    )


def test_scratchpad_updates_own_payload_subtype_is_not_mistaken_for_its_msg_type():
    """agent_note/agent_summary are values of a "type" key *inside*
    scratchpad_update's payload, not the event_type passed to
    emit_view. If this ever changes, the scoped regex above would
    start silently under-scanning; this pins the current shape.
    (agent_status went with scratchpad_set_status in Phase 0.2.)"""
    source = _source(TOOLPACKS_ROOT / "bookkeeping" / "scratchpad.py")
    assert re.search(r'emit_view\(\s*"scratchpad_update"', source)
    # The payload's nested "type" is _show()'s first argument.
    assert '"type": event' in source
    assert re.search(r'_show\(\s*"agent_note"', source)
    assert re.search(r'_show\(\s*"agent_summary"', source)
    assert not re.search(r'_show\(\s*"agent_status"', source)
    assert "agent_status" not in DURABLE_EVENT_TYPES | EPHEMERAL_EVENT_TYPES
    assert "agent_summary" not in DURABLE_EVENT_TYPES | EPHEMERAL_EVENT_TYPES
