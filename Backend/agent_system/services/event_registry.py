"""Event-class registry (WEBSOCKET_FIX_PLAN.md WS-05/principle 4).

Every WebSocket event type this process ever emits is classified here as one
of two classes:

  DURABLE   -- the UI needs it to rebuild state after a reload. Persisted
              before send, carries an `event_id`, and is replayed.
  EPHEMERAL -- a later event supersedes it (a progress tick, a "started"
              flag, a live-only mirror of state that is already durable
              somewhere else). Never persisted, never replayed, `event_id`
              stays null.

Getting this wrong in one direction silently drops state (an unrecoverable
type classified ephemeral); getting it wrong in the other direction is just
wasted writes (a superseded type classified durable). `classify()` therefore
fails *safe*, not closed: an unregistered type is logged loudly and treated
as durable (over-persisting is the recoverable mistake) rather than raised
into the caller -- several emitters, notably the planner's `output_handler`
(`agents/planning_agents/planner.py`), call `send_ws_message` with no
try/except around it, so raising here would turn a registry gap into a
crashed planning session. Completeness is instead enforced by
`Backend/tests/unit/services/test_event_registry.py` (T12), which scans the
emitting source files for every literal type actually sent and asserts each
one is registered -- an unregistered type is a *test* failure, not a runtime
one.
"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class EventClass(str, Enum):
    DURABLE = "durable"
    EPHEMERAL = "ephemeral"


# UI state that must survive a reload. Each of these either has no other
# durable source the frontend reads on mount/reconnect (task_messages,
# hypothesis, plan), or represents a domain transition the corresponding view
# renders directly from the replayed event rather than a separate fetch.
DURABLE_EVENT_TYPES: frozenset[str] = frozenset({
    # Planner state (agents/planning_agents/planner.py, services/session_service.py)
    "hypothesis",
    "pipeline_update",
    "plan",  # legacy alias of pipeline_update, kept for old clients
    "lit_review_output",
    "task_messages",  # the only place this is ever produced -- see plan-state's own note
    "planning_complete",
    # Execution status (api/runners.py, services/session_service.py,
    # application_api/repositories/node_runs.py via replay)
    "node_status",
    "run_finished",
    "run_stopped",
    "agent_waiting_for_user_input",  # blocks the pipeline until a human resolves it
    # View snapshots (view_emitter.py, services/pipeline_monitor.py)
    "population_output",
    "segmentation_output",
    "features_output",
    "modeling_output",
    "report_output",
    "latex_output",
    # Tool-pack view refreshes (agent_system/toolpacks/**) -- each mirrors a write
    # the tool already made through the domain API; durable so the view
    # renders the completed state after a reload, not just a live toast.
    "cohort_labels_output",
    # application_api-originated, reached through agent_system's replay/
    # broadcast path (api/routes/sessions.py, populations.py,
    # segmentation_review.py) -- each names a domain-state transition with no
    # separate REST source the frontend re-fetches on reload.
    "model_results_recorded",
    "feature_set_frozen",
    "cohort_approval_status",
    "literature_review_created",
    "segmentation_review_submitted",
    "report_created",
    "report_published",
})

# Superseded by whatever comes next, or a live mirror of state that is
# already durable elsewhere (a DB column, a node_runs row) -- replaying
# these would either be redundant or show a stale mid-transition snapshot.
EPHEMERAL_EVENT_TYPES: frozenset[str] = frozenset({
    "msg",  # transient chat text; nothing rebuilds a chat log from session_events
    "planning_failed",  # error toast; the durable plan/hypothesis already reflect the real state
    "lit_review_log",  # streaming progress lines
    "lit_review_node",  # "add the canvas node" trigger, superseded by lit_review_output
    "lit_review_done",  # "stop the spinner" trigger, superseded by lit_review_output
    "modules_matched",  # phase-entry flag
    "run_status",  # explicitly "live only" in session_service.py -- the run row is the durable copy
    "pipeline_resume_prepared",  # one-shot resume notice
    "state_update",  # live mirror of sessions.control_state, already persisted by set_session_control_state
    "tool_update",  # streaming tool_started/tool_progress/tool_finished ticks
    "agent_waiting_resolved",  # transient unblock signal, paired with the durable *_wait above
    "scratchpad_update",  # live mirror of node_runs.scratchpad_notes, already persisted there
    # Named by WEBSOCKET_FIX_PLAN.md principle 4 as the canonical ephemeral
    # examples; not emitted as a top-level type by anything today (tool
    # progress currently travels inside "tool_update"'s payload), registered
    # so a future direct emitter doesn't have to touch this file to exist.
    "tool_progress",
    "tool_log",
    "stream_chunk",
    "heartbeat",
    # Handshake control frames (WS-08, socket_store.ConnectionManager.replay).
    # Built directly by the manager, not through send_ws_message, so T12's
    # source scan never finds these -- registered anyway so classify() would
    # do the right thing if a caller ever did route them through it.
    "replay_complete",
    "replay_failed",
})

# Old durable event types: never emitted any more, but rows of these types
# stay in session_events (historical payloads are not rewritten) and are
# replayed as they are. Phase 6.1 renamed `pipeline_agent_status` to
# `node_status`; the old name lives only here and in the replay consumers.
LEGACY_DURABLE_EVENT_TYPES: frozenset[str] = frozenset({
    "pipeline_agent_status",
})

_OVERLAP = DURABLE_EVENT_TYPES & EPHEMERAL_EVENT_TYPES
assert not _OVERLAP, f"Event types classified as both durable and ephemeral: {_OVERLAP}"


def classify(event_type: str) -> EventClass:
    """Durable unless explicitly registered as ephemeral.

    An unregistered type is a registry gap, not evidence the event is safe
    to drop -- treating it as durable is the fail-safe default. See the
    module docstring for why this does not raise.
    """
    if event_type in EPHEMERAL_EVENT_TYPES:
        return EventClass.EPHEMERAL
    if event_type not in DURABLE_EVENT_TYPES | LEGACY_DURABLE_EVENT_TYPES:
        logger.error(
            "Event type %r is not registered in event_registry.py as durable "
            "or ephemeral (WEBSOCKET_FIX_PLAN.md WS-05); persisting it by "
            "default. Add it to DURABLE_EVENT_TYPES or EPHEMERAL_EVENT_TYPES.",
            event_type,
        )
    return EventClass.DURABLE
