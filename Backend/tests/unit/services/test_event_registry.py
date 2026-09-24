"""Every literal WebSocket event type this process emits must be registered
as durable or ephemeral (T12, WEBSOCKET_FIX_PLAN.md WS-05).

Static source scan, not an import of the emitting modules: several of them
(planner.py, pipeline_monitor.py) pull in AutoGen and the rest of the agent
stack, which unit tests must not touch. Each pattern below is scoped to one
file and the one call shape that file uses to emit, so a `"type"` key that
means something else in that file (a payload sub-field, an unrelated dict)
is never mistaken for the top-level msg_type -- see event_registry.py's own
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
MCP_ROOT = BACKEND_ROOT / "mcp"
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
    (AGENT_ROOT / "services" / "pipeline_monitor.py", r'msg_type\s*=\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "agents" / "planning_agents" / "planner.py", r'self\._send\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "services" / "session_service.py", r'self\._ws_send\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "api" / "runners.py", r'send_ws_message\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "api" / "routes" / "execution.py", r'send_ws_message\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (AGENT_ROOT / "api" / "routes" / "events.py", r'msg_type\s*=\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    # MCP subprocess emitters: each POSTs `{"type": "<literal>", ...}` to
    # /ws-broadcast as its own top-level dict, not nested in a payload.
    (MCP_ROOT / "tools" / "population" / "cohort_labels.py", r'"type"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (MCP_ROOT / "tools" / "targets_matcher.py", r'"type"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (MCP_ROOT / "tools" / "population" / "validate_anatomy.py", r'"type"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (MCP_ROOT / "tools" / "population" / "inclusion_criteria.py", r'"type"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    # scratchpad.py's own top-level type is the first arg to _emit_ws_message;
    # its payload's nested "type": "agent_status"/"agent_summary" keys are
    # NOT top-level msg_types and must not be scanned by the generic pattern.
    (MCP_ROOT / "tools" / "scratchpad.py", r'_emit_ws_message\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    # application_api's own durable-event writers (Wslog rows committed
    # alongside the domain change) -- reach the WS envelope's `type` field
    # via replay in agent_system/api/routes/sessions.py.
    (APPLICATION_ROOT / "repositories" / "agent_runs.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "cohorts.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "literature.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "feature_sets.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "model_results.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "reports.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
    (APPLICATION_ROOT / "repositories" / "segmentation_runs.py", r'publish\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"'),
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
    """agent_status/agent_summary are values of a "type" key *inside*
    scratchpad_update's payload, not the msg_type passed to
    broadcast_ws_message. If this ever changes, the scoped regex above would
    start silently under-scanning; this pins the current shape."""
    source = _source(MCP_ROOT / "tools" / "scratchpad.py")
    assert re.search(r'broadcast_ws_message\(\s*"scratchpad_update"', source)
    assert '"type": "agent_status"' in source
    assert '"type": "agent_summary"' in source
    assert "agent_status" not in DURABLE_EVENT_TYPES | EPHEMERAL_EVENT_TYPES
    assert "agent_summary" not in DURABLE_EVENT_TYPES | EPHEMERAL_EVENT_TYPES
