"""v1_p: the session-graph schema

Additive migration for the agent harness. With no dual path, the AutoGen pipeline does not keep
working against this schema; the rows it wrote are kept as history.

1. `users` with one explicit `legacy` owner; `sessions.user_id` NOT NULL;
2. `sessions`: execution/privacy mode, token budget, inbox cursor; `control_state`
   narrowed to control intent (`active|paused|archived`);
3. `session_graphs.version`, the compare-and-swap token;
4. `graph_changes`: approved plans become one applied change; drafts become a
   `pending` change that only a user can apply, and their node rows are dropped;
5. `graph_nodes`: stable handle, target, tombstone, accepting change; the live
   positions move to a deferrable EXCLUDE;
6. `node_runs`: accepting change, frozen policy, supersession, `outputs` (only
   from an exact mapping), expectation hold; never-started runs are closed as
   migration cleanup; `UNIQUE(node_id)` becomes one current run per node;
7. `run_attempts`, backfilled only with what is provable (one legacy attempt per
   started run; a run that was running is recorded as interrupted);
7a. `agent_checkpoints`, `agent_checkpoint_writes` and the `suspended` state;
8. the shared fencing trigger with a producer allow-list on every result
   table and the selection rules, plus every row rule;
9. `training_jobs`;
10. one chat per session: empty duplicates are dropped, any other duplicate stops
    the migration;
11. `chat_messages.seq`, a total transcript order, and the `tool` role;
12. downgrade() refuses whenever the migration created something it cannot undo.

Every new trigger is created after every backfill, so no legacy row is ever
judged by a rule written for the harness.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Literal, NamedTuple, Sequence, Union

import sqlalchemy as sa
from alembic import op

from application_api.core.canonical_json import content_digest

revision: str = "p1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "o1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_USERNAME = "legacy"

ACTIVE_ATTEMPT_STATES = ("preparing", "submitted", "running", "unknown", "suspended")
TERMINAL_ATTEMPT_STATES = ("succeeded", "failed", "cancelled", "interrupted")

CHECKPOINT_TABLES = ("agent_checkpoints", "agent_checkpoint_writes")

# Every table execution writes. The fencing trigger reads the declared
# write path on each of them.
FENCED_TABLES = (
    "cohorts",
    "feature_sets",
    "model_results",
    "radiomics_features",
    "reports",
    "segmentation_batches",
    "segmentation_pairs",
    "stored_files",
    "node_runs",
    "run_attempts",
    *CHECKPOINT_TABLES,
)

# Graph targets whose runs may insert into a table: every fenced result
# table. The legacy agent ids stay for history; the graph-visible tools and the
# modeling job tools join them, and a later target joins with its own migration
# (checked against the catalog). Two fenced tables have no list on
# purpose: any run may store a file, and a pair is owned through its batch.
UNRESTRICTED_PRODUCERS = ("stored_files", "segmentation_pairs")
PRODUCERS: dict[str, tuple[str, ...]] = {
    "cohorts": ("study_population_planner_agent",),
    "segmentation_batches": ("segmentation_agent", "run_segmentation_with_guard"),
    "reports": ("latex_reporter_agent",),
    "feature_sets": ("feature_extraction_agent", "radiomics_extract_features"),
    "radiomics_features": ("feature_extraction_agent", "radiomics_extract_features"),
    "model_results": (
        "modeling_agent",
        "run_modeling",
        "train_classifier",
        "train_regressor",
        "run_classifier_inference",
        "run_regression_inference",
        "refit_model",
    ),
}
V1_PRODUCERS: dict[str, tuple[str, ...]] = {
    "feature_sets": ("feature_extraction_agent",),
    "radiomics_features": ("feature_extraction_agent",),
    "model_results": ("modeling_agent",),
}


class LegacyPort(NamedTuple):
    """The one output port a legacy agent's completed run is known to fill."""

    target_name: str
    port: str
    artifact_type: str
    table: str
    key: str
    # one: the run produced exactly one row; many: the ref is all of the run's
    # rows; self: the run row itself (a ResultSummary).
    cardinality: Literal["one", "many", "self"]
    phi: bool


LEGACY_PORTS = (
    LegacyPort(
        "study_population_planner_agent", "cohort", "Cohort", "cohorts", "cohort_id", "one", True
    ),
    LegacyPort(
        "segmentation_agent", "masks", "MaskSet",
        "segmentation_batches", "segmentation_batch_id", "one", True,
    ),
    LegacyPort(
        "feature_extraction_agent", "features", "FeatureTable",
        "feature_sets", "feature_set_id", "one", True,
    ),
    LegacyPort(
        "modeling_agent", "modeling", "ModelingOutput",
        "model_results", "model_result_id", "many", False,
    ),
    LegacyPort(
        "result_reporter_agent", "summary", "ResultSummary", "node_runs", "run_id", "self", False
    ),
    LegacyPort(
        "latex_reporter_agent", "report", "ReportDocument", "reports", "report_id", "one", False
    ),
)


def _sql_list(values: Sequence[str]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


ACTIVE = _sql_list(ACTIVE_ATTEMPT_STATES)
TERMINAL = _sql_list(TERMINAL_ATTEMPT_STATES)


# ----------------------------------------------------------------------
# Triggers, as (name, table, DDL). upgrade() and downgrade() both iterate these
# tables, so the two directions cannot drift apart.
# ----------------------------------------------------------------------
Trigger = tuple[str, str, str]


def _row_trigger(name: str, table: str, events: str, function: str, args: str = "") -> Trigger:
    return (
        name,
        table,
        f"CREATE TRIGGER {name} BEFORE {events} ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}({args})",
    )


def _deferred_trigger(name: str, table: str, events: str, function: str) -> Trigger:
    """Checked at commit, after every row of the transaction is written."""
    return (
        name,
        table,
        f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON {table} "
        f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function}()",
    )


def _producer_trigger(table: str, producers: Sequence[str], suffix: str, events: str) -> Trigger:
    args = ", ".join(f"'{name}'" for name in producers)
    return _row_trigger(f"{table}_{suffix}", table, events, "domain_rows_enforce_producer", args)


V1_TRIGGERS: tuple[Trigger, ...] = (
    _deferred_trigger(
        "session_graphs_run_cardinality", "session_graphs", "INSERT OR UPDATE",
        "session_graphs_enforce_run_cardinality",
    ),
    _deferred_trigger(
        "graph_nodes_run_cardinality", "graph_nodes", "INSERT OR DELETE OR UPDATE",
        "session_graphs_enforce_run_cardinality",
    ),
    _deferred_trigger(
        "node_runs_run_cardinality", "node_runs", "INSERT OR DELETE OR UPDATE",
        "session_graphs_enforce_run_cardinality",
    ),
    _row_trigger(
        "graph_nodes_frozen_after_approval", "graph_nodes", "INSERT OR DELETE OR UPDATE",
        "graph_nodes_reject_approved_mutation",
    ),
    _row_trigger(
        "node_runs_row_rules", "node_runs", "DELETE OR UPDATE", "node_runs_enforce_row_rules"
    ),
    *(
        _producer_trigger(table, names, "producer", "INSERT")
        for table, names in V1_PRODUCERS.items()
    ),
)
V1_DROPPED_FUNCTIONS = (
    "session_graphs_enforce_run_cardinality",
    "graph_nodes_reject_approved_mutation",
)

_ALL_WRITES = "INSERT OR UPDATE OR DELETE"
P_TRIGGERS: tuple[Trigger, ...] = (
    _row_trigger("sessions_row_rules", "sessions", "UPDATE", "sessions_enforce_row_rules"),
    _row_trigger(
        "session_graphs_version_rules", "session_graphs", "UPDATE", "session_graphs_enforce_version"
    ),
    _row_trigger(
        "graph_changes_row_rules", "graph_changes", _ALL_WRITES, "graph_changes_enforce_row_rules"
    ),
    _row_trigger(
        "graph_nodes_row_rules", "graph_nodes", _ALL_WRITES, "graph_nodes_enforce_row_rules"
    ),
    _row_trigger("node_runs_row_rules", "node_runs", _ALL_WRITES, "node_runs_enforce_row_rules"),
    _row_trigger(
        "run_attempts_row_rules", "run_attempts", _ALL_WRITES, "run_attempts_enforce_row_rules"
    ),
    _row_trigger(
        "agent_checkpoints_row_rules", "agent_checkpoints", "UPDATE",
        "agent_checkpoints_enforce_row_rules",
    ),
    _row_trigger(
        "agent_checkpoint_writes_row_rules", "agent_checkpoint_writes", "INSERT OR UPDATE",
        "agent_checkpoints_enforce_row_rules",
    ),
    _row_trigger(
        "chat_messages_tool_pairing", "chat_messages", "INSERT",
        "chat_messages_enforce_tool_pairing",
    ),
    _deferred_trigger(
        "graph_nodes_version_advanced", "graph_nodes", "INSERT OR UPDATE",
        "graph_nodes_check_version_advanced",
    ),
    _deferred_trigger(
        "graph_nodes_accepted_change_applied", "graph_nodes",
        "INSERT OR UPDATE OF accepted_by_change_id", "graph_nodes_check_accepted_change",
    ),
    _deferred_trigger(
        "node_runs_status_projection", "node_runs", "INSERT OR UPDATE OF status, error",
        "node_runs_check_status_projection",
    ),
    _deferred_trigger(
        "run_attempts_status_projection", "run_attempts", "INSERT OR UPDATE OF state, error",
        "node_runs_check_status_projection",
    ),
    *(
        _producer_trigger(
            table,
            PRODUCERS.get(table, ()),
            "fencing",
            _ALL_WRITES if table in CHECKPOINT_TABLES else "INSERT OR UPDATE",
        )
        for table in FENCED_TABLES
    ),
)
P_FUNCTIONS = (
    "row_changes_only(jsonb, jsonb, text[])",
    "run_attempt_state_is_active(text)",
    "fencing_require_current_run(uuid, text)",
    "agent_checkpoints_require_purgeable(uuid)",
    "sessions_enforce_row_rules()",
    "session_graphs_enforce_version()",
    "graph_changes_enforce_row_rules()",
    "graph_nodes_enforce_row_rules()",
    "graph_nodes_check_version_advanced()",
    "graph_nodes_check_accepted_change()",
    "node_runs_check_status_projection()",
    "run_attempts_enforce_row_rules()",
    "agent_checkpoints_enforce_row_rules()",
    "chat_messages_enforce_tool_pairing()",
)


# ----------------------------------------------------------------------
# Trigger functions. Bodies are text: they name no Python
# constant except the attempt states, rendered once below.
# ----------------------------------------------------------------------
_HELPER_FUNCTIONS = f"""
CREATE OR REPLACE FUNCTION public.run_attempt_state_is_active(state text)
 RETURNS boolean
 LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $function$ SELECT state IN {ACTIVE} $function$;

CREATE OR REPLACE FUNCTION public.row_changes_only(old_row jsonb, new_row jsonb, columns text[])
 RETURNS boolean
 LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $function$ SELECT (old_row - columns) = (new_row - columns) $function$;
"""

_FUNCTIONS_P = r"""
CREATE OR REPLACE FUNCTION public.fencing_require_current_run(target uuid, what text)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
    run record;
BEGIN
    SELECT r.status, r.superseded_at INTO run FROM node_runs r WHERE r.run_id = target;
    IF run.status IS DISTINCT FROM 'running' OR run.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION
            '% of run % changes only while the run is running and current; it is %',
            what, target, coalesce(run.status, 'missing')
                || CASE WHEN run.superseded_at IS NOT NULL THEN ' and superseded' ELSE '' END
            USING ERRCODE = 'restrict_violation';
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION public.agent_checkpoints_require_purgeable(target uuid)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
    run record;
    retention integer := coalesce(
        nullif(current_setting('cubeai.checkpoint_retention_days', true), '')::integer, 14);
BEGIN
    SELECT r.session_id, r.completed_at INTO run FROM node_runs r WHERE r.run_id = target;
    IF run.completed_at IS NULL OR run.completed_at > now() - make_interval(days => retention) THEN
        RAISE EXCEPTION
            'checkpoints of run % are kept until % days after the run ends', target, retention
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- A seed is live while its change is pending, or while a live node that the
    -- change accepted has not started under it yet.
    IF EXISTS (
        SELECT 1 FROM graph_changes c
        WHERE c.session_id = run.session_id
          AND jsonb_path_exists(c.content, '$.** ? (@.seed_from.run_id == $run)',
                                jsonb_build_object('run', target::text))
          AND (c.status = 'pending' OR EXISTS (
                SELECT 1 FROM graph_nodes n
                WHERE n.accepted_by_change_id = c.change_id AND n.removed_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM node_runs s
                                  WHERE s.node_id = n.node_id
                                    AND s.accepted_by_change_id = c.change_id)))) THEN
        RAISE EXCEPTION 'checkpoints of run % are named by a live seed_from', target
            USING ERRCODE = 'restrict_violation';
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION public.sessions_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id THEN
        RAISE EXCEPTION 'session % owner is immutable', OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.tokens_used < OLD.tokens_used
       OR NEW.inbox_handled_through_event_id < OLD.inbox_handled_through_event_id
       OR (NEW.inbox_handled_through_event_id IS NULL
           AND OLD.inbox_handled_through_event_id IS NOT NULL) THEN
        RAISE EXCEPTION 'session % tokens_used and inbox cursor only move forward',
            OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.session_graphs_enforce_version()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.version IS DISTINCT FROM OLD.version THEN
        IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'session graph % moves from version % to %; applying a change advances it by 1',
                OLD.session_id, OLD.version, NEW.version
                USING ERRCODE = 'restrict_violation';
        END IF;
        -- Marks this transaction as the one that advanced the graph: the only
        -- transaction allowed to change its nodes (graph_nodes_check_version_advanced).
        PERFORM set_config('cubeai.graph_advanced', NEW.session_id::text, true);
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.graph_changes_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'graph change % is approval history and cannot be deleted', OLD.change_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('pending', 'applied') THEN
            RAISE EXCEPTION 'graph change % is inserted pending or applied, not %',
                NEW.change_id, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.change_id IS DISTINCT FROM OLD.change_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.author IS DISTINCT FROM OLD.author
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.base_version IS DISTINCT FROM OLD.base_version
       OR NEW.content IS DISTINCT FROM OLD.content
       OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
       OR NEW.approval_reasons IS DISTINCT FROM OLD.approval_reasons
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'graph change % content and provenance are immutable', OLD.change_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status <> 'pending' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'graph change % is % and final', OLD.change_id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.graph_nodes_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'graph node % is never deleted; a removal sets removed_at', OLD.node_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.node_id IS DISTINCT FROM OLD.node_id
           OR NEW.session_id IS DISTINCT FROM OLD.session_id
           OR NEW.node_seq IS DISTINCT FROM OLD.node_seq
           OR NEW.target_name IS DISTINCT FROM OLD.target_name
           OR NEW.target_kind IS DISTINCT FROM OLD.target_kind THEN
            RAISE EXCEPTION 'graph node % identity and target are immutable', OLD.node_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF OLD.removed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'graph node % was removed at %; a removal is final',
                OLD.node_id, OLD.removed_at
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.removed_at IS NOT NULL THEN
        RAISE EXCEPTION 'graph node % must be inserted live', NEW.node_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;

-- Checked at commit, so applying a change may advance the version at any
-- point of its transaction.
CREATE OR REPLACE FUNCTION public.graph_nodes_check_version_advanced()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NULL;
    END IF;
    IF current_setting('cubeai.graph_advanced', true) IS DISTINCT FROM NEW.session_id::text
       AND current_setting('cubeai.actor', true) IS DISTINCT FROM 'migration' THEN
        RAISE EXCEPTION
            'graph node % of session % changes only in the transaction that advances the '
            'graph version', NEW.node_id, NEW.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.graph_nodes_check_accepted_change()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    change_status text;
BEGIN
    -- Re-read the row: a later statement of the transaction may have moved it on.
    SELECT c.status INTO change_status
    FROM graph_nodes n JOIN graph_changes c ON c.change_id = n.accepted_by_change_id
    WHERE n.node_id = NEW.node_id;
    IF change_status IS DISTINCT FROM 'applied' THEN
        RAISE EXCEPTION 'graph node % is accepted by a change that is %, not applied',
            NEW.node_id, coalesce(change_status, 'missing')
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.node_runs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    node record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'run % is execution provenance and cannot be deleted', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- Only starting a node creates a run: when its node starts, with attempt 1.
        IF NEW.execution_policy ? 'legacy' THEN
            RAISE EXCEPTION 'run %: legacy runs come only from the migration backfill', NEW.run_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.status <> 'running' OR NEW.started_at IS NULL OR NEW.completed_at IS NOT NULL
           OR NEW.superseded_at IS NOT NULL OR NEW.outputs IS NOT NULL
           OR NEW.downstream_hold IS NOT NULL OR NEW.hold_released_by_change_id IS NOT NULL THEN
            RAISE EXCEPTION 'run % is inserted started, running and current', NEW.run_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT n.accepted_by_change_id, n.removed_at, n.target_kind INTO node
        FROM graph_nodes n WHERE n.node_id = NEW.node_id;
        IF NOT FOUND THEN
            RETURN NEW;  -- the FK reports the missing node
        END IF;
        IF node.removed_at IS NOT NULL THEN
            RAISE EXCEPTION 'graph node % was removed; it cannot start', NEW.node_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.accepted_by_change_id IS DISTINCT FROM node.accepted_by_change_id THEN
            RAISE EXCEPTION
                'run % executes change %, but its node holds change %',
                NEW.run_id, NEW.accepted_by_change_id, node.accepted_by_change_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF (node.target_kind = 'agent') <> (coalesce(btrim(NEW.compiled_prompt), '') <> '') THEN
            RAISE EXCEPTION
                'run %: an agent node starts with a compiled prompt, a tool node without one',
                NEW.run_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.node_id IS DISTINCT FROM OLD.node_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.accepted_by_change_id IS DISTINCT FROM OLD.accepted_by_change_id
       OR NEW.execution_policy IS DISTINCT FROM OLD.execution_policy
       OR NEW.target_version IS DISTINCT FROM OLD.target_version THEN
        RAISE EXCEPTION 'run % identity and accepted specification are immutable', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.started_at IS NOT NULL AND (
           NEW.started_at IS DISTINCT FROM OLD.started_at
        OR NEW.compiled_prompt IS DISTINCT FROM OLD.compiled_prompt
        OR NEW.resolved_inputs IS DISTINCT FROM OLD.resolved_inputs) THEN
        RAISE EXCEPTION
            'run % compiled_prompt/resolved_inputs/started_at are immutable once '
            'the run has started', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
       OR (OLD.downstream_hold IS NOT NULL
           AND NEW.downstream_hold IS DISTINCT FROM OLD.downstream_hold)
       OR (OLD.hold_released_by_change_id IS NOT NULL
           AND NEW.hold_released_by_change_id IS DISTINCT FROM OLD.hold_released_by_change_id) THEN
        RAISE EXCEPTION
            'run %: superseded_at, downstream_hold and hold_released_by_change_id are set once',
            OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'completed' THEN
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.outputs IS DISTINCT FROM OLD.outputs
           OR NEW.error IS DISTINCT FROM OLD.error
           OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
           OR NEW.scratchpad_notes IS DISTINCT FROM OLD.scratchpad_notes
           OR NEW.scratchpad_warnings IS DISTINCT FROM OLD.scratchpad_warnings
           OR NEW.scratchpad_summary IS DISTINCT FROM OLD.scratchpad_summary THEN
            RAISE EXCEPTION
                'run % is completed; only its supersession and hold may still be set',
                OLD.run_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'running' AND NEW.status IN ('completed', 'failed', 'cancelled'))
        OR (OLD.status IN ('failed', 'cancelled') AND NEW.status = 'running'
            AND NEW.superseded_at IS NULL)) THEN
        RAISE EXCEPTION 'run % cannot move from % to %', OLD.run_id, OLD.status,
            NEW.status || CASE WHEN NEW.superseded_at IS NOT NULL
                               THEN ' (a superseded run is never retried)' ELSE '' END
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.node_runs_check_status_projection()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    run record;
    latest record;
    expected text;
BEGIN
    SELECT r.status, r.error, r.execution_policy INTO run FROM node_runs r WHERE r.run_id = NEW.run_id;
    -- Attempts are sequential, so the newest one alone decides the projection.
    SELECT a.state, a.error INTO latest
    FROM run_attempts a WHERE a.run_id = NEW.run_id
    ORDER BY a.attempt_number DESC LIMIT 1;
    IF NOT FOUND THEN
        IF run.status = 'cancelled' AND run.execution_policy ? 'legacy' THEN
            RETURN NULL;  -- a legacy run that never started
        END IF;
        RAISE EXCEPTION 'run % has no attempt; a start inserts a run with its attempt 1', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    expected := CASE
        WHEN run_attempt_state_is_active(latest.state) THEN 'running'
        WHEN latest.state = 'succeeded' THEN 'completed'
        WHEN latest.state = 'cancelled' THEN 'cancelled'
        ELSE 'failed'
    END;
    IF run.status IS DISTINCT FROM expected
       OR (expected IN ('failed', 'cancelled') AND run.error IS DISTINCT FROM latest.error) THEN
        RAISE EXCEPTION
            'run % is % with error %, but its latest attempt (%) projects % with error %',
            NEW.run_id, run.status, run.error, latest.state, expected, latest.error
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.run_attempts_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    run record;
    previous record;
    has_previous boolean;
    expected_number integer;
    session_state text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'attempt % is execution history and cannot be deleted', OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.dispatch_kind = 'legacy' THEN
            RAISE EXCEPTION 'attempt %: legacy attempts come only from the migration backfill',
                NEW.attempt_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.state <> 'preparing' OR NEW.dispatched_at IS NOT NULL OR NEW.ended_at IS NOT NULL
           OR NEW.suspension IS NOT NULL OR NEW.resumed_at IS NOT NULL OR NEW.usage IS NOT NULL THEN
            RAISE EXCEPTION 'attempt % is inserted preparing, before dispatch', NEW.attempt_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT r.status, r.superseded_at, r.execution_policy INTO run
        FROM node_runs r WHERE r.run_id = NEW.run_id;
        IF NOT FOUND THEN
            RETURN NEW;  -- the FK reports the missing run
        END IF;
        IF run.superseded_at IS NOT NULL OR run.status = 'completed' THEN
            RAISE EXCEPTION 'run % is superseded or completed; it gets no new attempt', NEW.run_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        SELECT a.attempt_number, a.state INTO previous
        FROM run_attempts a WHERE a.run_id = NEW.run_id
        ORDER BY a.attempt_number DESC LIMIT 1;
        has_previous := FOUND;
        expected_number := CASE WHEN has_previous THEN previous.attempt_number + 1 ELSE 1 END;
        IF NEW.attempt_number <> expected_number THEN
            RAISE EXCEPTION 'attempts of run % are numbered consecutively from 1', NEW.run_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF has_previous AND run_attempt_state_is_active(previous.state) THEN
            RAISE EXCEPTION 'attempt % of run % is still %; a run has one non-terminal attempt',
                previous.attempt_number, NEW.run_id, previous.state
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF jsonb_typeof(run.execution_policy -> 'max_attempts') = 'number'
           AND NEW.attempt_number > (run.execution_policy ->> 'max_attempts')::integer THEN
            RAISE EXCEPTION 'run % allows % attempt(s) (execution_policy.max_attempts)',
                NEW.run_id, run.execution_policy ->> 'max_attempts'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
       OR NEW.dispatch_kind IS DISTINCT FROM OLD.dispatch_kind THEN
        RAISE EXCEPTION 'attempt % identity is immutable', OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT run_attempt_state_is_active(OLD.state) AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'attempt % is % and final', OLD.attempt_id, OLD.state
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (OLD.submission_key IS NOT NULL AND NEW.submission_key IS DISTINCT FROM OLD.submission_key)
       OR (OLD.request IS NOT NULL AND NEW.request IS DISTINCT FROM OLD.request)
       OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)
       OR (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.cancel_requested_at IS NOT NULL
           AND NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at) THEN
        RAISE EXCEPTION
            'attempt %: submission_key, request, dispatched_at, started_at and '
            'cancel_requested_at are set once', OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Observed transitions only.
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
           (OLD.state = 'preparing' AND NEW.state IN
                ('submitted', 'running', 'unknown', 'interrupted', 'cancelled', 'failed'))
        OR (OLD.state = 'submitted' AND NEW.state IN
                ('running', 'unknown', 'succeeded', 'failed', 'cancelled', 'interrupted'))
        OR (OLD.state = 'running' AND NEW.state IN
                ('succeeded', 'failed', 'cancelled', 'interrupted', 'unknown', 'suspended'))
        OR (OLD.state = 'unknown' AND NEW.state IN
                ('submitted', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'))
        OR (OLD.state = 'suspended' AND NEW.state IN ('running', 'cancelled', 'failed'))) THEN
        RAISE EXCEPTION 'attempt % cannot move from % to %', OLD.attempt_id, OLD.state, NEW.state
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Suspend after storing the interrupt checkpoint; a gate decision only
    -- adds `resume`; resume only in an active session, on a current run.
    IF OLD.state = 'running' AND NEW.state = 'suspended' THEN
        IF NEW.suspension ? 'resume' OR NOT EXISTS (
               SELECT 1 FROM agent_checkpoints c
               WHERE c.run_id = NEW.run_id AND c.attempt_id = NEW.attempt_id
                 AND c.checkpoint_id = NEW.suspension ->> 'checkpoint_id') THEN
            RAISE EXCEPTION
                'attempt % suspends undecided, after storing its interrupt checkpoint',
                OLD.attempt_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.suspension IS DISTINCT FROM OLD.suspension AND NOT (
           OLD.state = 'suspended' AND NEW.state = 'suspended'
       AND OLD.suspension ->> 'kind' = 'gate'
       AND NOT OLD.suspension ? 'resume' AND NEW.suspension ? 'resume'
       AND (NEW.suspension - 'resume') = (OLD.suspension - 'resume')) THEN
        RAISE EXCEPTION 'attempt %: a suspension changes only by its one gate decision',
            OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.state = 'suspended' AND NEW.state = 'running' THEN
        SELECT s.control_state INTO session_state FROM sessions s WHERE s.session_id = NEW.session_id;
        SELECT r.superseded_at INTO run FROM node_runs r WHERE r.run_id = NEW.run_id;
        IF session_state IS DISTINCT FROM 'active' OR run.superseded_at IS NOT NULL
           OR (OLD.suspension ->> 'kind' = 'gate' AND NOT OLD.suspension ? 'resume')
           OR NEW.resumed_at IS NULL OR NEW.resumed_at IS NOT DISTINCT FROM OLD.resumed_at THEN
            RAISE EXCEPTION
                'attempt % resumes only in an active session, on a current run, after its '
                'gate is decided, with a new resumed_at', OLD.attempt_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.resumed_at IS DISTINCT FROM OLD.resumed_at THEN
        RAISE EXCEPTION 'attempt %: resumed_at changes only when a suspended attempt resumes',
            OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.usage IS DISTINCT FROM OLD.usage AND run_attempt_state_is_active(NEW.state) THEN
        RAISE EXCEPTION 'attempt %: usage is written when the attempt ends', OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.agent_checkpoints_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION '% rows are immutable; a new step is a new checkpoint', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- A replayed pending write (same key) is a no-op, as LangGraph expects.
    IF EXISTS (SELECT 1 FROM agent_checkpoint_writes w
               WHERE w.run_id = NEW.run_id AND w.checkpoint_ns = NEW.checkpoint_ns
                 AND w.checkpoint_id = NEW.checkpoint_id AND w.task_id = NEW.task_id
                 AND w.idx = NEW.idx) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.chat_messages_enforce_tool_pairing()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.metadata ->> 'kind' IS DISTINCT FROM 'tool_result' THEN
        RETURN NEW;
    END IF;
    IF NEW.metadata -> 'turn_id' IS NULL OR NEW.metadata -> 'call_id' IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM chat_messages m
           WHERE m.chat_id = NEW.chat_id AND m.seq < NEW.seq
             AND m.metadata ->> 'kind' = 'tool_calls'
             AND m.metadata ->> 'turn_id' = NEW.metadata ->> 'turn_id'
             AND m.metadata -> 'calls' @> jsonb_build_array(
                     jsonb_build_object('call_id', NEW.metadata -> 'call_id'))) THEN
        RAISE EXCEPTION
            'tool_result % names call % of turn %, which no earlier tool_calls row of chat % '
            'holds', NEW.message_id, NEW.metadata -> 'call_id',
            NEW.metadata -> 'turn_id', NEW.chat_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.domain_rows_enforce_cohort_alignment()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    own_session uuid;
    cohort record;
BEGIN
    SELECT session_id INTO own_session
    FROM node_runs WHERE run_id = NEW.created_by_run_id;

    SELECT r.session_id, c.frozen_at INTO cohort
    FROM cohorts c JOIN node_runs r ON r.run_id = c.created_by_run_id
    WHERE c.cohort_id = NEW.cohort_id;

    IF own_session IS NULL OR cohort.session_id IS NULL THEN
        -- The plain FKs report the missing parent with the right error.
        RETURN NEW;
    END IF;

    IF own_session <> cohort.session_id THEN
        RAISE EXCEPTION
            '% row from session % cannot use cohort % from session % (R1)',
            TG_TABLE_NAME, own_session, NEW.cohort_id, cohort.session_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- A cohort is frozen by its approval gate while its own run is running
    --. Freezing it here, on first use by another run, would change a
    -- selection after that run completed.
    IF cohort.frozen_at IS NULL THEN
        RAISE EXCEPTION
            '% row cannot use cohort %: it is not frozen. A cohort is frozen by its '
            'approval gate before another run uses it', TG_TABLE_NAME, NEW.cohort_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;

-- The shared fencing trigger with the producer and selection
-- rules. TG_ARGV is the producer allow-list; an empty list admits any target.
CREATE OR REPLACE FUNCTION public.domain_rows_enforce_producer()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    actor text := nullif(current_setting('cubeai.actor', true), '');
    attempt_key text := nullif(current_setting('cubeai.attempt_id', true), '');
    job_run text := nullif(current_setting('cubeai.run_id', true), '');
    execution_table boolean := TG_TABLE_NAME IN
        ('node_runs', 'run_attempts', 'agent_checkpoints', 'agent_checkpoint_writes');
    checkpoint_table boolean := TG_TABLE_NAME IN ('agent_checkpoints', 'agent_checkpoint_writes');
    -- The in-node gate a table's decision columns belong to, and those columns.
    gate text := CASE TG_TABLE_NAME
        WHEN 'cohorts' THEN 'cohort_approval'
        WHEN 'segmentation_pairs' THEN 'segmentation_review'
        WHEN 'segmentation_batches' THEN 'segmentation_review' END;
    gate_columns text[] := CASE TG_TABLE_NAME
        WHEN 'cohorts' THEN ARRAY['frozen_at']
        WHEN 'segmentation_pairs' THEN ARRAY['review_decision', 'selected_segmentation_uid']
        WHEN 'segmentation_batches' THEN ARRAY['review_status', 'review_request_hash', 'reviewed_at']
        WHEN 'run_attempts' THEN ARRAY['suspension'] END;
    row_run uuid;
    producer text;
    selecting boolean := false;
    attempt record;
BEGIN
    IF actor = 'migration' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    -- The run that owns the row. Each branch names only its own table's
    -- columns: PL/pgSQL resolves a field when the statement first runs.
    IF TG_OP = 'DELETE' THEN
        row_run := OLD.run_id;  -- only the checkpoint tables fence a DELETE
    ELSIF execution_table THEN
        row_run := NEW.run_id;
    ELSIF TG_TABLE_NAME = 'segmentation_pairs' THEN
        SELECT b.created_by_run_id INTO row_run
        FROM segmentation_batches b WHERE b.segmentation_batch_id = NEW.segmentation_batch_id;
    ELSE
        row_run := NEW.created_by_run_id;
        IF TG_OP = 'UPDATE' AND row_run IS DISTINCT FROM OLD.created_by_run_id THEN
            RAISE EXCEPTION '% row: its producing run is immutable', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    IF row_run IS NULL THEN
        RETURN NEW;  -- a run-less row: a direct Main result, a literature report
    END IF;

    IF TG_OP = 'INSERT' AND TG_NARGS > 0 THEN
        SELECT n.target_name INTO producer
        FROM node_runs r JOIN graph_nodes n ON n.node_id = r.node_id
        WHERE r.run_id = row_run;
        IF FOUND AND NOT (producer = ANY (TG_ARGV)) THEN
            RAISE EXCEPTION '% rows are owned by %; run % executes %',
                TG_TABLE_NAME, array_to_string(TG_ARGV, ' or '), row_run, producer
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    -- Writes that select or approve a result.
    IF TG_TABLE_NAME = 'model_results' THEN
        selecting := CASE WHEN TG_OP = 'INSERT' THEN NEW.selected
                          ELSE NEW.selected IS DISTINCT FROM OLD.selected END;
    ELSIF TG_TABLE_NAME IN ('cohorts', 'feature_sets') THEN
        selecting := CASE WHEN TG_OP = 'INSERT' THEN NEW.frozen_at IS NOT NULL
                          ELSE NEW.frozen_at IS DISTINCT FROM OLD.frozen_at END;
    ELSIF TG_TABLE_NAME = 'segmentation_pairs' THEN
        selecting := CASE
            WHEN TG_OP = 'INSERT' THEN
                NEW.review_decision IS NOT NULL OR NEW.selected_segmentation_uid IS NOT NULL
            ELSE NEW.review_decision IS DISTINCT FROM OLD.review_decision
              OR NEW.selected_segmentation_uid IS DISTINCT FROM OLD.selected_segmentation_uid END;
    ELSIF TG_TABLE_NAME = 'segmentation_batches' THEN
        selecting := NEW.review_status IN ('submitting', 'submitted')
            AND (TG_OP = 'INSERT' OR NEW.review_status IS DISTINCT FROM OLD.review_status);
    END IF;

    IF actor IS NULL AND attempt_key IS NULL AND job_run IS NULL THEN
        RAISE EXCEPTION
            '% write of run % declares no write path: set cubeai.attempt_id, cubeai.run_id '
            'or cubeai.actor', TG_TABLE_NAME, row_run
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF actor = 'harness' THEN
        -- Apply, start, dispatch, reconcile, the run projection, cancel, suspend
        -- and the purge: lifecycle only.
        IF TG_TABLE_NAME = 'node_runs' THEN
            IF TG_OP = 'UPDATE' AND NOT row_changes_only(to_jsonb(OLD), to_jsonb(NEW), ARRAY[
                   'status', 'error', 'completed_at', 'superseded_at', 'outputs',
                   'downstream_hold', 'hold_released_by_change_id']) THEN
                RAISE EXCEPTION 'the harness path writes only lifecycle columns of node_runs'
                    USING ERRCODE = 'restrict_violation';
            END IF;
        ELSIF checkpoint_table THEN
            IF TG_OP = 'DELETE' THEN
                PERFORM agent_checkpoints_require_purgeable(OLD.run_id);
                RETURN OLD;
            END IF;
            -- The start's seeded copy, written for the run's one active attempt.
            PERFORM 1 FROM run_attempts a
            WHERE a.attempt_id = NEW.attempt_id AND a.run_id = row_run
              AND run_attempt_state_is_active(a.state)
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'a seeded checkpoint of run % names its active attempt', row_run
                    USING ERRCODE = 'restrict_violation';
            END IF;
        ELSIF TG_TABLE_NAME <> 'run_attempts' THEN
            RAISE EXCEPTION 'the harness path never writes % rows', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;

    ELSIF actor = 'user' THEN
        -- The owner's decision in an in-node gate: gate columns only, while the
        -- run is running and current and suspended on that gate.
        IF TG_OP <> 'UPDATE' OR gate_columns IS NULL
           OR NOT row_changes_only(to_jsonb(OLD), to_jsonb(NEW), gate_columns) THEN
            RAISE EXCEPTION 'the user gate path changes only gate columns, not % rows',
                TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END IF;
        -- On run_attempts itself, the row rules check the gate decision.
        IF gate IS NOT NULL THEN
            PERFORM 1 FROM run_attempts a
            WHERE a.run_id = row_run AND a.state = 'suspended'
              AND a.suspension ->> 'kind' = 'gate' AND a.suspension ->> 'gate' = gate
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'run % is not suspended on its % gate', row_run, gate
                    USING ERRCODE = 'restrict_violation';
            END IF;
        END IF;
        PERFORM fencing_require_current_run(row_run, 'a gate decision');
        RETURN NEW;

    ELSIF actor IS NOT NULL THEN
        RAISE EXCEPTION 'unknown write path cubeai.actor = %', actor
            USING ERRCODE = 'restrict_violation';

    ELSIF attempt_key IS NOT NULL THEN
        SELECT a.attempt_id, a.run_id, a.state, a.attempt_number INTO attempt
        FROM run_attempts a WHERE a.attempt_id = attempt_key::uuid
        FOR SHARE;  -- holds completion and cancellation off until this write commits
        IF NOT FOUND OR attempt.run_id <> row_run THEN
            RAISE EXCEPTION 'attempt % cannot write rows of run %', attempt_key, row_run
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF TG_TABLE_NAME = 'run_attempts' THEN
            -- The worker moves its own attempt to running before it writes.
            IF TG_OP <> 'UPDATE' OR NEW.attempt_id <> attempt.attempt_id
               OR NEW.state <> 'running' OR OLD.state NOT IN ('preparing', 'submitted')
               OR NOT row_changes_only(to_jsonb(OLD), to_jsonb(NEW),
                                       ARRAY['state', 'dispatched_at', 'started_at']) THEN
                RAISE EXCEPTION 'attempt % may only mark itself running', attempt_key
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF checkpoint_table THEN
            IF TG_OP <> 'INSERT' OR NEW.attempt_id <> attempt.attempt_id
               OR attempt.state NOT IN ('running', 'suspended')
               OR EXISTS (SELECT 1 FROM run_attempts a
                          WHERE a.run_id = row_run AND a.attempt_number > attempt.attempt_number) THEN
                RAISE EXCEPTION
                    'only the latest running or suspended attempt of run % writes its own '
                    'checkpoints', row_run
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF attempt.state NOT IN ('submitted', 'running') THEN
            RAISE EXCEPTION 'attempt % is %; only a submitted or running attempt writes',
                attempt_key, attempt.state
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF TG_TABLE_NAME = 'node_runs' AND (TG_OP <> 'UPDATE' OR NOT row_changes_only(
               to_jsonb(OLD), to_jsonb(NEW),
               ARRAY['scratchpad_notes', 'scratchpad_warnings', 'scratchpad_summary'])) THEN
            RAISE EXCEPTION 'an attempt writes only the scratchpad of its run'
                USING ERRCODE = 'restrict_violation';
        END IF;

    ELSE
        -- Internal job (cubeai.run_id): jobs that outlive one attempt.
        IF job_run::uuid IS DISTINCT FROM row_run OR execution_table THEN
            RAISE EXCEPTION 'an internal job of run % writes only domain rows of that run',
                job_run
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM node_runs r WHERE r.run_id = row_run AND r.status = 'completed') THEN
            RAISE EXCEPTION 'run % is completed; its internal jobs no longer write', row_run
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF TG_OP = 'UPDATE' AND NOT (TG_TABLE_NAME = 'segmentation_pairs' AND row_changes_only(
               to_jsonb(OLD), to_jsonb(NEW),
               ARRAY['status', 'inference_job_id', 'candidate_segmentation_uid',
                     'execution_checkpoint'])) THEN
            RAISE EXCEPTION 'an internal job inserts run-scoped rows and keeps pair bookkeeping only'
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    IF selecting THEN
        PERFORM fencing_require_current_run(row_run, format('the selection on %s', TG_TABLE_NAME));
    END IF;
    RETURN NEW;
END;
$function$;
"""


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------
def _execute_all(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


def _drop_v1_rules() -> None:
    """The run-cardinality, frozen-plan, run-row and producer rules assume one
    eager run per step of an immutable plan; they go before any backfill."""
    for name, table, _ in V1_TRIGGERS:
        op.execute(f"DROP TRIGGER {name} ON {table}")
    for function in V1_DROPPED_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}()")


def _extend_sessions_and_graphs() -> None:
    _execute_all((
        """
        CREATE TABLE users (
            user_id uuid NOT NULL DEFAULT gen_random_uuid(),
            username text NOT NULL,
            display_name text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_users PRIMARY KEY (user_id),
            CONSTRAINT uq_users_username UNIQUE (username),
            CONSTRAINT ck_users_username_normalised
                CHECK (length(username) > 0 AND username = lower(btrim(username)))
        )
        """,
        f"INSERT INTO users (username, display_name) "
        f"VALUES ('{LEGACY_USERNAME}', 'Sessions created before the session-graph migration')",
        """
        ALTER TABLE sessions
            ADD COLUMN user_id uuid,
            ADD COLUMN execution_mode text NOT NULL DEFAULT 'auto',
            ADD COLUMN privacy_mode text NOT NULL DEFAULT 'standard',
            ADD COLUMN tokens_used bigint NOT NULL DEFAULT 0,
            ADD COLUMN inbox_handled_through_event_id bigint,
            DROP CONSTRAINT ck_sessions_control_state,
            ALTER COLUMN control_state SET DEFAULT 'active'
        """,
        # control_state keeps intent only; every progress value is derived.
        f"""
        UPDATE sessions SET
            user_id = (SELECT user_id FROM users WHERE username = '{LEGACY_USERNAME}'),
            control_state = CASE
                WHEN control_state IN ('pausing', 'paused') THEN 'paused'
                WHEN control_state = 'archived' THEN 'archived'
                ELSE 'active' END
        """,
        """
        ALTER TABLE sessions
            ALTER COLUMN user_id SET NOT NULL,
            ADD CONSTRAINT fk_sessions_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE RESTRICT,
            ADD CONSTRAINT ck_sessions_control_state
                CHECK (control_state IN ('active', 'paused', 'archived')),
            ADD CONSTRAINT ck_sessions_execution_mode CHECK (execution_mode IN ('plan', 'auto')),
            ADD CONSTRAINT ck_sessions_privacy_mode CHECK (privacy_mode IN ('standard', 'phi_local')),
            ADD CONSTRAINT ck_sessions_tokens_used_nonnegative CHECK (tokens_used >= 0),
            ADD CONSTRAINT ck_sessions_inbox_cursor_nonnegative
                CHECK (inbox_handled_through_event_id IS NULL OR inbox_handled_through_event_id >= 0)
        """,
        "CREATE INDEX ix_sessions_user_id_updated_at ON sessions (user_id, updated_at DESC)",
        """
        ALTER TABLE session_graphs
            ADD COLUMN version integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_session_graphs_version_nonnegative CHECK (version >= 0)
        """,
        "UPDATE session_graphs SET version = 1 WHERE approved_at IS NOT NULL",
    ))


_LEGACY_GRAPHS = sa.text(
    """
    SELECT g.session_id, g.approved_at, g.updated_at,
           jsonb_agg(jsonb_build_object(
               'node_id', n.node_id, 'kind', 'agent', 'ref', n.target_name,
               'ref_version', 'legacy', 'title', n.title, 'params', n.params,
               'input_sources', NULL, 'expect', NULL) ORDER BY n.position) AS nodes
    FROM session_graphs g JOIN graph_nodes n USING (session_id)
    GROUP BY g.session_id
    """
)

_INSERT_CHANGE = sa.text(
    """
    INSERT INTO graph_changes (
        session_id, author, idempotency_key, base_version, content, content_digest,
        approval_reasons, status, decided_by, decided_at, applied_version, created_at)
    VALUES (
        :session_id, 'legacy', :idempotency_key, 0, CAST(:content AS jsonb), :content_digest,
        CAST(:approval_reasons AS text[]), :status, :decided_by, :decided_at,
        :applied_version, :created_at)
    """
)


def _legacy_change(graph: Any) -> dict[str, Any]:
    """An approved plan becomes applied change #1; a draft becomes a pending
    change only a user can apply. The content is the ordered node
    snapshot, keeping every step id as its node id."""
    draft = graph.approved_at is None
    content: dict[str, Any] = {
        "v": 1,
        "nodes": graph.nodes,
        "rerun_node_ids": [],
        "release_hold_node_ids": [],
        "on_running": "reject",
    }
    if draft:
        content["origin"] = "legacy_draft"
    common = {
        "session_id": graph.session_id,
        "content": json.dumps(content),
        "content_digest": content_digest(content),
    }
    if draft:
        return common | {
            "idempotency_key": "draft-backfill",
            "approval_reasons": ["approval_required:legacy_draft"],
            "status": "pending",
            "decided_by": None,
            "decided_at": None,
            "applied_version": None,
            "created_at": graph.updated_at,
        }
    return common | {
        "idempotency_key": "initial-backfill",
        "approval_reasons": [],
        "status": "applied",
        "decided_by": "user",
        "decided_at": graph.approved_at,
        "applied_version": 1,
        "created_at": graph.approved_at,
    }


def _create_graph_changes(bind: sa.engine.Connection) -> None:
    op.execute(
        """
        CREATE TABLE graph_changes (
            change_id uuid NOT NULL DEFAULT gen_random_uuid(),
            session_id uuid NOT NULL,
            author text NOT NULL,
            idempotency_key text NOT NULL,
            base_version integer NOT NULL,
            content jsonb NOT NULL,
            content_digest text NOT NULL,
            approval_reasons text[] NOT NULL DEFAULT '{}',
            status text NOT NULL,
            decided_by text,
            decided_at timestamptz,
            applied_version integer,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_graph_changes PRIMARY KEY (change_id),
            CONSTRAINT fk_graph_changes_session_id_session_graphs
                FOREIGN KEY (session_id) REFERENCES session_graphs (session_id) ON DELETE RESTRICT,
            CONSTRAINT uq_graph_changes_session_id_idempotency_key UNIQUE (session_id, idempotency_key),
            CONSTRAINT uq_graph_changes_session_id_applied_version UNIQUE (session_id, applied_version),
            CONSTRAINT uq_graph_changes_change_id_session_id UNIQUE (change_id, session_id),
            CONSTRAINT ck_graph_changes_author CHECK (author IN ('main', 'user', 'harness', 'legacy')),
            CONSTRAINT ck_graph_changes_status CHECK
                (status IN ('pending', 'applied', 'rejected', 'stale', 'withdrawn', 'expired')),
            CONSTRAINT ck_graph_changes_decided_by CHECK (decided_by IS NULL OR decided_by IN ('user', 'auto')),
            CONSTRAINT ck_graph_changes_applied_iff_version
                CHECK ((status = 'applied') = (applied_version IS NOT NULL)),
            CONSTRAINT ck_graph_changes_applied_version_follows_base
                CHECK (applied_version IS NULL OR applied_version = base_version + 1),
            CONSTRAINT ck_graph_changes_decided_iff_decision
                CHECK ((status IN ('applied', 'rejected')) = (decided_at IS NOT NULL AND decided_by IS NOT NULL)),
            CONSTRAINT ck_graph_changes_reasons_need_a_user
                CHECK (cardinality(approval_reasons) = 0 OR decided_by IS DISTINCT FROM 'auto'),
            CONSTRAINT ck_graph_changes_content_digest_is_sha256 CHECK (content_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_graph_changes_content_versioned CHECK
                (jsonb_typeof(content) = 'object' AND content ? 'v' AND pg_column_size(content) <= 2097152),
            CONSTRAINT ck_graph_changes_base_version_nonnegative CHECK (base_version >= 0),
            CONSTRAINT ck_graph_changes_idempotency_key_not_blank CHECK (length(btrim(idempotency_key)) > 0)
        )
        """
    )
    _execute_all((
        "CREATE UNIQUE INDEX uq_graph_changes_one_pending ON graph_changes (session_id) "
        "WHERE status = 'pending'",
        "CREATE INDEX ix_graph_changes_session_id_created_at ON graph_changes (session_id, created_at)",
    ))
    changes = [_legacy_change(graph) for graph in bind.execute(_LEGACY_GRAPHS)]
    if changes:
        bind.execute(_INSERT_CHANGE, changes)


def _extend_graph_nodes() -> None:
    _execute_all((
        """
        ALTER TABLE graph_nodes
            ADD COLUMN node_seq integer,
            ADD COLUMN target_kind text NOT NULL DEFAULT 'agent',
            ADD COLUMN target_version text NOT NULL DEFAULT 'legacy',
            ADD COLUMN input_sources jsonb,
            ADD COLUMN expectations jsonb,
            ADD COLUMN accepted_by_change_id uuid,
            ADD COLUMN removed_at timestamptz
        """,
        # A draft's definition now lives in its pending change; its steps have
        # no runs and nothing references them (no node without an applied change).
        """
        DELETE FROM graph_nodes n USING session_graphs g
        WHERE g.session_id = n.session_id AND g.approved_at IS NULL
        """,
        """
        UPDATE graph_nodes n SET node_seq = n.position, accepted_by_change_id = c.change_id
        FROM graph_changes c WHERE c.session_id = n.session_id AND c.status = 'applied'
        """,
        """
        ALTER TABLE graph_nodes
            ALTER COLUMN node_seq SET NOT NULL,
            ALTER COLUMN accepted_by_change_id SET NOT NULL,
            DROP CONSTRAINT uq_graph_nodes_session_id,
            ADD CONSTRAINT ex_graph_nodes_live_position
                EXCLUDE USING btree (session_id WITH =, "position" WITH =)
                WHERE (removed_at IS NULL) DEFERRABLE INITIALLY IMMEDIATE,
            ADD CONSTRAINT uq_graph_nodes_session_id_node_seq UNIQUE (session_id, node_seq),
            ADD CONSTRAINT fk_graph_nodes_accepted_by_change_id_graph_changes
                FOREIGN KEY (accepted_by_change_id, session_id)
                REFERENCES graph_changes (change_id, session_id) ON DELETE RESTRICT,
            ADD CONSTRAINT ck_graph_nodes_node_seq_positive CHECK (node_seq > 0),
            ADD CONSTRAINT ck_graph_nodes_target_kind CHECK (target_kind IN ('agent', 'tool')),
            ADD CONSTRAINT ck_graph_nodes_target_version_not_blank
                CHECK (length(btrim(target_version)) > 0),
            ADD CONSTRAINT ck_graph_nodes_definition_versioned CHECK (
                (input_sources IS NULL OR (jsonb_typeof(input_sources) = 'object' AND input_sources ? 'v'))
                AND (expectations IS NULL OR (jsonb_typeof(expectations) = 'object' AND expectations ? 'v')))
        """,
        "CREATE INDEX ix_graph_nodes_accepted_by_change_id ON graph_nodes (accepted_by_change_id)",
    ))


def _outputs_sql(port: LegacyPort) -> str:
    """Backfill one port, only when the mapping is exact: the
    target fixes the port and table, every row is the run's own, and a
    single-row ref names the run's only row. Otherwise outputs stay NULL."""
    ids = "jsonb_build_array(r.run_id::text)" if port.cardinality == "self" else "p.ids"
    outputs = (
        "jsonb_build_object('v', 1, 'origin', 'legacy_backfill', 'ports', jsonb_build_object("
        f"'{port.port}', jsonb_build_object('type', '{port.artifact_type}', "
        f"'ref', jsonb_build_object('table', '{port.table}', 'ids', {ids}), "
        f"'summary', NULL, 'phi', {str(port.phi).lower()})))"
    )
    run_filter = (
        f"n.node_id = r.node_id AND n.target_name = '{port.target_name}' AND r.status = 'completed'"
    )
    if port.cardinality == "self":
        return f"UPDATE node_runs r SET outputs = {outputs} FROM graph_nodes n WHERE {run_filter}"
    single = "AND p.row_count = 1" if port.cardinality == "one" else ""
    return f"""
        WITH p AS (
            SELECT created_by_run_id AS run_id, count(*) AS row_count,
                   jsonb_agg({port.key}::text ORDER BY created_at, {port.key}) AS ids
            FROM {port.table} WHERE created_by_run_id IS NOT NULL
            GROUP BY created_by_run_id)
        UPDATE node_runs r SET outputs = {outputs}
        FROM p, graph_nodes n
        WHERE p.run_id = r.run_id AND {run_filter} {single}
    """


def _extend_node_runs() -> None:
    _execute_all((
        """
        ALTER TABLE node_runs
            ADD COLUMN accepted_by_change_id uuid,
            ADD COLUMN execution_policy jsonb,
            ADD COLUMN target_version text,
            ADD COLUMN superseded_at timestamptz,
            ADD COLUMN outputs jsonb,
            ADD COLUMN downstream_hold text,
            ADD COLUMN hold_released_by_change_id uuid,
            ALTER COLUMN status DROP DEFAULT,
            DROP CONSTRAINT ck_node_runs_status,
            DROP CONSTRAINT ck_node_runs_completed_at_iff_terminal,
            DROP CONSTRAINT ck_node_runs_started_iff_not_pending,
            DROP CONSTRAINT ck_node_runs_compiled_before_start,
            DROP CONSTRAINT uq_node_runs_node_id
        """,
        """
        UPDATE node_runs r SET
            accepted_by_change_id = n.accepted_by_change_id,
            execution_policy = jsonb_build_object(
                'v', 1, 'max_attempts', 1, 'legacy', true, 'privacy_mode', 'standard'),
            target_version = 'legacy'
        FROM graph_nodes n WHERE n.node_id = r.node_id
        """,
        *(_outputs_sql(port) for port in LEGACY_PORTS),
        # Migration cleanup, not a user cancellation: nothing ran, so nothing
        # was stopped, no attempt exists and no event is published.
        """
        UPDATE node_runs SET
            status = 'cancelled', superseded_at = now(), completed_at = now(),
            error = jsonb_build_object('code', 'legacy_not_started', 'source', 'migration')
        WHERE status = 'pending'
        """,
    ))


def _create_run_attempts() -> None:
    _execute_all((
        f"""
        CREATE TABLE run_attempts (
            attempt_id uuid NOT NULL DEFAULT gen_random_uuid(),
            run_id uuid NOT NULL,
            session_id uuid NOT NULL,
            attempt_number smallint NOT NULL,
            dispatch_kind text NOT NULL,
            submission_key text,
            request jsonb,
            state text NOT NULL,
            suspension jsonb,
            resumed_at timestamptz,
            dispatched_at timestamptz,
            cancel_requested_at timestamptz,
            error jsonb,
            usage jsonb,
            started_at timestamptz,
            ended_at timestamptz,
            CONSTRAINT pk_run_attempts PRIMARY KEY (attempt_id),
            CONSTRAINT fk_run_attempts_run_id_session_id_node_runs FOREIGN KEY (run_id, session_id)
                REFERENCES node_runs (run_id, session_id) ON DELETE RESTRICT,
            CONSTRAINT uq_run_attempts_run_id_attempt_number UNIQUE (run_id, attempt_number),
            CONSTRAINT uq_run_attempts_attempt_id_run_id UNIQUE (attempt_id, run_id),
            CONSTRAINT ck_run_attempts_attempt_number_positive CHECK (attempt_number > 0),
            CONSTRAINT ck_run_attempts_dispatch_kind
                CHECK (dispatch_kind IN ('in_process', 'job', 'inline', 'legacy')),
            CONSTRAINT ck_run_attempts_state
                CHECK (state IN {_sql_list(ACTIVE_ATTEMPT_STATES + TERMINAL_ATTEMPT_STATES)}),
            CONSTRAINT ck_run_attempts_ended_at_iff_terminal
                CHECK ((state IN {TERMINAL}) = (ended_at IS NOT NULL)),
            CONSTRAINT ck_run_attempts_dispatched_before_running CHECK
                (state NOT IN ('submitted', 'running', 'succeeded', 'suspended') OR dispatched_at IS NOT NULL),
            CONSTRAINT ck_run_attempts_failed_has_error_code CHECK (state <> 'failed' OR error ? 'code'),
            CONSTRAINT ck_run_attempts_suspension_shape CHECK
                (state <> 'suspended' OR (dispatch_kind = 'in_process' AND suspension ? 'kind')),
            CONSTRAINT ck_run_attempts_submission_key_not_blank
                CHECK (submission_key IS NULL OR length(btrim(submission_key)) > 0),
            CONSTRAINT ck_run_attempts_json_versioned CHECK (
                (error IS NULL OR (jsonb_typeof(error) = 'object' AND error ? 'code'))
                AND (usage IS NULL OR usage ? 'v')
                AND (suspension IS NULL
                     OR (suspension ? 'v' AND suspension ->> 'kind' IN ('gate', 'pause')))),
            CONSTRAINT ck_run_attempts_json_bounded CHECK (
                COALESCE(pg_column_size(request), 0) <= 2097152
                AND COALESCE(pg_column_size(error), 0) <= 262144
                AND COALESCE(pg_column_size(suspension), 0) <= 1048576
                AND COALESCE(pg_column_size(usage), 0) <= 65536)
        )
        """,
        "CREATE UNIQUE INDEX uq_run_attempts_submission_key ON run_attempts (submission_key) "
        "WHERE submission_key IS NOT NULL",
        # One non-terminal attempt per run, and (for now) per session.
        f"CREATE UNIQUE INDEX uq_run_attempts_one_active_per_run ON run_attempts (run_id) "
        f"WHERE state IN {ACTIVE}",
        f"CREATE UNIQUE INDEX uq_run_attempts_one_active_per_session ON run_attempts (session_id) "
        f"WHERE state IN {ACTIVE}",
        "CREATE INDEX ix_run_attempts_suspended ON run_attempts (state) WHERE state = 'suspended'",
        # Only what is provable: attempt 1 of each started run.
        # The stack was stopped, so a running run was interrupted. A wrapped
        # error keeps the legacy one under legacy_error for downgrade().
        """
        INSERT INTO run_attempts (run_id, session_id, attempt_number, dispatch_kind, state,
                                  started_at, dispatched_at, ended_at, error)
        SELECT run_id, session_id, 1, 'legacy',
               CASE status WHEN 'running' THEN 'interrupted'
                           WHEN 'completed' THEN 'succeeded' ELSE 'failed' END,
               started_at, started_at, COALESCE(completed_at, now()),
               CASE
                   WHEN status = 'running' THEN jsonb_build_object(
                       'code', 'interrupted', 'source', 'migration', 'legacy_error', error)
                   WHEN status = 'failed' AND NOT (error ? 'code') THEN jsonb_build_object(
                       'code', 'error', 'source', 'migration', 'legacy_error', error)
                   WHEN status = 'failed' THEN error
               END
        FROM node_runs WHERE status IN ('running', 'completed', 'failed')
        """,
        # The run is the projection of its attempt: interrupted -> failed.
        """
        UPDATE node_runs r SET
            status = CASE a.state WHEN 'interrupted' THEN 'failed' ELSE r.status END,
            completed_at = a.ended_at,
            error = a.error
        FROM run_attempts a
        WHERE a.run_id = r.run_id AND r.status IN ('running', 'failed')
        """,
    ))


def _constrain_node_runs() -> None:
    _execute_all((
        """
        ALTER TABLE node_runs
            ALTER COLUMN accepted_by_change_id SET NOT NULL,
            ALTER COLUMN execution_policy SET NOT NULL,
            ALTER COLUMN target_version SET NOT NULL,
            ADD CONSTRAINT fk_node_runs_accepted_by_change_id_graph_changes
                FOREIGN KEY (accepted_by_change_id, session_id)
                REFERENCES graph_changes (change_id, session_id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_node_runs_hold_released_by_change_id_graph_changes
                FOREIGN KEY (hold_released_by_change_id, session_id)
                REFERENCES graph_changes (change_id, session_id) ON DELETE RESTRICT,
            ADD CONSTRAINT ck_node_runs_status
                CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
            ADD CONSTRAINT ck_node_runs_completed_at_iff_terminal
                CHECK ((status IN ('completed', 'failed', 'cancelled')) = (completed_at IS NOT NULL)),
            ADD CONSTRAINT ck_node_runs_started_unless_never_started
                CHECK (started_at IS NOT NULL OR (status = 'cancelled' AND execution_policy ? 'legacy')),
            ADD CONSTRAINT ck_node_runs_inputs_before_start
                CHECK (started_at IS NULL OR resolved_inputs IS NOT NULL),
            ADD CONSTRAINT ck_node_runs_downstream_hold CHECK (downstream_hold IS NULL
                OR (status = 'completed' AND downstream_hold = 'expectation_violated')),
            ADD CONSTRAINT ck_node_runs_hold_release_needs_a_hold
                CHECK (hold_released_by_change_id IS NULL OR downstream_hold IS NOT NULL),
            ADD CONSTRAINT ck_node_runs_outputs_only_when_completed CHECK (outputs IS NULL
                OR (status = 'completed' AND jsonb_typeof(outputs) = 'object' AND outputs ? 'v')),
            ADD CONSTRAINT ck_node_runs_versioned CHECK (
                jsonb_typeof(execution_policy) = 'object' AND execution_policy ? 'v'
                AND (resolved_inputs IS NULL OR resolved_inputs ? 'v' OR execution_policy ? 'legacy')),
            ADD CONSTRAINT ck_node_runs_target_version_not_blank CHECK (length(btrim(target_version)) > 0),
            ADD CONSTRAINT ck_node_runs_policy_and_outputs_bounded CHECK (
                pg_column_size(execution_policy) <= 65536
                AND COALESCE(pg_column_size(outputs), 0) <= 2097152)
        """,
        "CREATE UNIQUE INDEX uq_node_runs_one_current ON node_runs (node_id) "
        "WHERE superseded_at IS NULL",
        "CREATE INDEX ix_node_runs_session_id_current ON node_runs (session_id) "
        "WHERE superseded_at IS NULL",
        "CREATE INDEX ix_node_runs_node_id_started_at ON node_runs (node_id, started_at)",
    ))


def _create_agent_checkpoints() -> None:
    _execute_all((
        """
        CREATE TABLE agent_checkpoints (
            run_id uuid NOT NULL,
            session_id uuid NOT NULL,
            checkpoint_ns text NOT NULL DEFAULT '',
            checkpoint_id text NOT NULL,
            parent_checkpoint_id text,
            attempt_id uuid NOT NULL,
            type text NOT NULL,
            checkpoint bytea NOT NULL,
            metadata jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_agent_checkpoints PRIMARY KEY (run_id, checkpoint_ns, checkpoint_id),
            CONSTRAINT fk_agent_checkpoints_run_id_session_id_node_runs FOREIGN KEY (run_id, session_id)
                REFERENCES node_runs (run_id, session_id) ON DELETE RESTRICT,
            CONSTRAINT fk_agent_checkpoints_attempt_id_run_id_run_attempts FOREIGN KEY (attempt_id, run_id)
                REFERENCES run_attempts (attempt_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT ck_agent_checkpoints_checkpoint_id_not_blank CHECK (length(btrim(checkpoint_id)) > 0),
            CONSTRAINT ck_agent_checkpoints_metadata_versioned CHECK (
                jsonb_typeof(metadata) = 'object' AND metadata ? 'v' AND metadata ? 'spec_digest'
                AND metadata ? 'engine_version' AND pg_column_size(metadata) <= 1048576)
        )
        """,
        "CREATE INDEX ix_agent_checkpoints_run_id_checkpoint_ns_created_at "
        "ON agent_checkpoints (run_id, checkpoint_ns, created_at DESC)",
        # A pending write belongs to its checkpoint: the purge removes both.
        """
        CREATE TABLE agent_checkpoint_writes (
            run_id uuid NOT NULL,
            session_id uuid NOT NULL,
            checkpoint_ns text NOT NULL DEFAULT '',
            checkpoint_id text NOT NULL,
            task_id text NOT NULL,
            idx integer NOT NULL,
            channel text NOT NULL,
            type text,
            value bytea NOT NULL,
            attempt_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_agent_checkpoint_writes
                PRIMARY KEY (run_id, checkpoint_ns, checkpoint_id, task_id, idx),
            CONSTRAINT fk_agent_checkpoint_writes_checkpoint
                FOREIGN KEY (run_id, checkpoint_ns, checkpoint_id)
                REFERENCES agent_checkpoints (run_id, checkpoint_ns, checkpoint_id) ON DELETE CASCADE,
            CONSTRAINT fk_agent_checkpoint_writes_run_id_session_id_node_runs
                FOREIGN KEY (run_id, session_id)
                REFERENCES node_runs (run_id, session_id) ON DELETE RESTRICT,
            CONSTRAINT fk_agent_checkpoint_writes_attempt_id_run_id_run_attempts
                FOREIGN KEY (attempt_id, run_id)
                REFERENCES run_attempts (attempt_id, run_id) ON DELETE RESTRICT
        )
        """,
    ))


def _create_training_jobs() -> None:
    _execute_all((
        """
        CREATE TABLE training_jobs (
            job_id text NOT NULL,
            task_name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            submitted_at timestamptz,
            cancel_requested_at timestamptz,
            ended_at timestamptz,
            outcome text,
            error jsonb,
            CONSTRAINT pk_training_jobs PRIMARY KEY (job_id),
            CONSTRAINT fk_training_jobs_task_name_workflow
                FOREIGN KEY (task_name) REFERENCES workflow (task_name) ON DELETE RESTRICT,
            CONSTRAINT ck_training_jobs_job_id_not_blank CHECK (length(btrim(job_id)) > 0),
            CONSTRAINT ck_training_jobs_outcome
                CHECK (outcome IS NULL OR outcome IN ('completed', 'failed', 'cancelled')),
            CONSTRAINT ck_training_jobs_ended_at_iff_outcome CHECK ((ended_at IS NULL) = (outcome IS NULL)),
            CONSTRAINT ck_training_jobs_error_bounded CHECK (error IS NULL
                OR (jsonb_typeof(error) = 'object' AND pg_column_size(error) <= 262144))
        )
        """,
        "CREATE UNIQUE INDEX uq_training_jobs_one_active ON training_jobs (task_name) "
        "WHERE ended_at IS NULL",
    ))


def _one_chat_per_session(bind: sa.engine.Connection) -> None:
    """An empty chat beside another chat of its session is a race
    artefact and goes (the oldest survives when all are empty). A session that
    still has two chats with messages stops the migration: conversations are
    never merged or guessed."""
    op.execute(
        """
        DELETE FROM chats c
        WHERE NOT EXISTS (SELECT 1 FROM chat_messages m WHERE m.chat_id = c.chat_id)
          AND EXISTS (
              SELECT 1 FROM chats o
              WHERE o.session_id = c.session_id AND o.chat_id <> c.chat_id
                AND (EXISTS (SELECT 1 FROM chat_messages m WHERE m.chat_id = o.chat_id)
                     OR (o.created_at, o.chat_id) < (c.created_at, c.chat_id)))
        """
    )
    sessions = bind.execute(sa.text(
        "SELECT session_id FROM chats GROUP BY session_id HAVING count(*) > 1 ORDER BY session_id"
    )).scalars().all()
    if sessions:
        listed = ", ".join(str(session) for session in sessions)
        raise RuntimeError(
            f"The session-graph migration stopped: {len(sessions)} session(s) have more than one chat with "
            f"messages ({listed}). Decide which chat to keep for each, move or archive the "
            "other, then rerun the migration. Inspect them with:\n"
            "  SELECT c.session_id, c.chat_id, count(m.*), min(m.created_at), max(m.created_at)\n"
            "  FROM chats c LEFT JOIN chat_messages m USING (chat_id)\n"
            f"  WHERE c.session_id IN ({', '.join(repr(str(s)) for s in sessions)})\n"
            "  GROUP BY c.session_id, c.chat_id ORDER BY c.session_id;"
        )
    op.execute("ALTER TABLE chats ADD CONSTRAINT uq_chats_session_id UNIQUE (session_id)")


def _sequence_chat_messages(bind: sa.engine.Connection) -> None:
    """An identity added to a filled table numbers rows in
    physical order, so seq is backfilled by time first, then made an identity."""
    _execute_all((
        "ALTER TABLE chat_messages ADD COLUMN seq bigint",
        """
        UPDATE chat_messages m SET seq = o.seq
        FROM (SELECT message_id,
                     row_number() OVER (ORDER BY created_at, message_id) AS seq
              FROM chat_messages) o
        WHERE o.message_id = m.message_id
        """,
        "ALTER TABLE chat_messages ALTER COLUMN seq SET NOT NULL",
    ))
    start = int(bind.execute(sa.text("SELECT COALESCE(max(seq), 0) + 1 FROM chat_messages")).scalar_one())
    _execute_all((
        f"ALTER TABLE chat_messages ALTER COLUMN seq ADD GENERATED ALWAYS AS IDENTITY (START WITH {start})",
        """
        ALTER TABLE chat_messages
            ADD CONSTRAINT uq_chat_messages_seq UNIQUE (seq),
            DROP CONSTRAINT ck_chat_messages_role,
            ADD CONSTRAINT ck_chat_messages_role CHECK (role IN ('user', 'assistant', 'system', 'tool'))
        """,
        "CREATE INDEX ix_chat_messages_chat_id_seq ON chat_messages (chat_id, seq)",
        # One result per call, per turn.
        "CREATE UNIQUE INDEX uq_chat_messages_one_tool_result ON chat_messages "
        "(chat_id, (metadata ->> 'turn_id'), (metadata ->> 'call_id')) "
        "WHERE metadata ->> 'kind' = 'tool_result'",
    ))


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("SET LOCAL cubeai.actor = 'migration'")  # the migration write path
    _drop_v1_rules()
    _extend_sessions_and_graphs()
    _create_graph_changes(bind)
    _extend_graph_nodes()
    _extend_node_runs()
    _create_run_attempts()
    _constrain_node_runs()
    _create_agent_checkpoints()
    _create_training_jobs()
    _one_chat_per_session(bind)
    _sequence_chat_messages(bind)
    _execute_all((_HELPER_FUNCTIONS, _FUNCTIONS_P))
    _execute_all([ddl for _, _, ddl in P_TRIGGERS])


# ----------------------------------------------------------------------
# Downgrade
# ----------------------------------------------------------------------
# (what the migration cannot undo, the query that counts it), plus
# the new tables whose rows downgrade() would otherwise drop silently.
DOWNGRADE_REFUSALS = (
    ("a node has more than one run",
     "SELECT count(*) FROM (SELECT node_id FROM node_runs GROUP BY node_id HAVING count(*) > 1) t"),
    ("a run has more than one attempt", "SELECT count(*) FROM run_attempts WHERE attempt_number > 1"),
    ("a run was started after the migration",
     "SELECT count(*) FROM node_runs WHERE NOT execution_policy ? 'legacy'"),
    ("a draft was moved into a change",
     "SELECT count(*) FROM graph_changes WHERE content ->> 'origin' = 'legacy_draft'"),
    ("a user other than the legacy user exists",
     f"SELECT count(*) FROM users WHERE username <> '{LEGACY_USERNAME}'"),
    ("a non-legacy graph change exists", "SELECT count(*) FROM graph_changes WHERE author <> 'legacy'"),
    ("an agent checkpoint exists", "SELECT count(*) FROM agent_checkpoints"),
    ("an attempt is suspended", "SELECT count(*) FROM run_attempts WHERE state = 'suspended'"),
    ("a tool message exists", "SELECT count(*) FROM chat_messages WHERE role = 'tool'"),
    ("a training job receipt exists", "SELECT count(*) FROM training_jobs"),
)


def _refuse_lossy_downgrade(bind: sa.engine.Connection) -> None:
    reasons = [
        f"{reason} ({count})"
        for reason, query in DOWNGRADE_REFUSALS
        if (count := bind.execute(sa.text(query)).scalar_one())
    ]
    if reasons:
        raise RuntimeError(
            "Refusing to downgrade the session-graph migration: the data has moved past what the o1 schema "
            "can hold: " + "; ".join(reasons) + "."
        )


def _o1_functions() -> str:
    """The o1 trigger-function bodies, restored verbatim from that revision."""
    path = Path(__file__).with_name("o1f0a2c3d4e5_v1_o_renames.py")
    spec = importlib.util.spec_from_file_location("_cubeai_v1_o_renames", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._FUNCTIONS_O


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_lossy_downgrade(bind)
    op.execute("SET LOCAL cubeai.actor = 'migration'")

    # Rules first: a deferred trigger with pending events blocks ALTER TABLE.
    for name, table, _ in P_TRIGGERS:
        op.execute(f"DROP TRIGGER {name} ON {table}")
    for function in P_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")

    _execute_all((
        """
        DROP INDEX uq_chat_messages_one_tool_result, ix_chat_messages_chat_id_seq;
        ALTER TABLE chat_messages
            DROP CONSTRAINT uq_chat_messages_seq,
            DROP CONSTRAINT ck_chat_messages_role,
            DROP COLUMN seq,
            ADD CONSTRAINT ck_chat_messages_role CHECK (role IN ('user', 'assistant', 'system'));
        ALTER TABLE chats DROP CONSTRAINT uq_chats_session_id;
        DROP TABLE agent_checkpoint_writes, agent_checkpoints, training_jobs;
        """,
        # node_runs back to the o1 vocabulary, reading the legacy attempts once more.
        """
        DROP INDEX uq_node_runs_one_current, ix_node_runs_session_id_current,
                   ix_node_runs_node_id_started_at;
        ALTER TABLE node_runs
            DROP CONSTRAINT fk_node_runs_accepted_by_change_id_graph_changes,
            DROP CONSTRAINT fk_node_runs_hold_released_by_change_id_graph_changes,
            DROP CONSTRAINT ck_node_runs_status,
            DROP CONSTRAINT ck_node_runs_completed_at_iff_terminal,
            DROP CONSTRAINT ck_node_runs_started_unless_never_started,
            DROP CONSTRAINT ck_node_runs_inputs_before_start,
            DROP CONSTRAINT ck_node_runs_downstream_hold,
            DROP CONSTRAINT ck_node_runs_hold_release_needs_a_hold,
            DROP CONSTRAINT ck_node_runs_outputs_only_when_completed,
            DROP CONSTRAINT ck_node_runs_versioned,
            DROP CONSTRAINT ck_node_runs_target_version_not_blank,
            DROP CONSTRAINT ck_node_runs_policy_and_outputs_bounded;
        UPDATE node_runs r SET
            status = 'running', completed_at = NULL,
            error = NULLIF(r.error -> 'legacy_error', 'null'::jsonb)
        FROM run_attempts a WHERE a.run_id = r.run_id AND a.state = 'interrupted';
        UPDATE node_runs SET error = error -> 'legacy_error'
        WHERE status = 'failed' AND error ->> 'source' = 'migration' AND error ? 'legacy_error';
        UPDATE node_runs SET status = 'pending', superseded_at = NULL, completed_at = NULL, error = NULL
        WHERE error ->> 'code' = 'legacy_not_started';
        DROP TABLE run_attempts;
        ALTER TABLE node_runs
            DROP COLUMN accepted_by_change_id,
            DROP COLUMN execution_policy,
            DROP COLUMN target_version,
            DROP COLUMN superseded_at,
            DROP COLUMN outputs,
            DROP COLUMN downstream_hold,
            DROP COLUMN hold_released_by_change_id,
            ALTER COLUMN status SET DEFAULT 'pending',
            ADD CONSTRAINT uq_node_runs_node_id UNIQUE (node_id),
            ADD CONSTRAINT ck_node_runs_status
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            ADD CONSTRAINT ck_node_runs_completed_at_iff_terminal
                CHECK ((status IN ('completed', 'failed')) = (completed_at IS NOT NULL)),
            ADD CONSTRAINT ck_node_runs_started_iff_not_pending
                CHECK ((status = 'pending') = (started_at IS NULL)),
            ADD CONSTRAINT ck_node_runs_compiled_before_start CHECK (started_at IS NULL
                OR (compiled_prompt IS NOT NULL AND length(btrim(compiled_prompt)) > 0
                    AND resolved_inputs IS NOT NULL));
        """,
        """
        DROP INDEX ix_graph_nodes_accepted_by_change_id;
        ALTER TABLE graph_nodes
            DROP CONSTRAINT ex_graph_nodes_live_position,
            DROP CONSTRAINT uq_graph_nodes_session_id_node_seq,
            DROP CONSTRAINT fk_graph_nodes_accepted_by_change_id_graph_changes,
            DROP COLUMN node_seq,
            DROP COLUMN target_kind,
            DROP COLUMN target_version,
            DROP COLUMN input_sources,
            DROP COLUMN expectations,
            DROP COLUMN accepted_by_change_id,
            DROP COLUMN removed_at,
            ADD CONSTRAINT uq_graph_nodes_session_id UNIQUE (session_id, "position") DEFERRABLE;
        DROP TABLE graph_changes;
        ALTER TABLE session_graphs DROP COLUMN version;
        """,
        # control_state: o1 stored progress, which `active` derives. The
        # runs are back in the o1 vocabulary here, so the progress value is
        # rebuilt from them. Only the transient `planning` and `pausing` cannot
        # come back: they described a process that no longer runs.
        """
        DROP INDEX ix_sessions_user_id_updated_at;
        ALTER TABLE sessions
            DROP CONSTRAINT fk_sessions_user_id_users,
            DROP CONSTRAINT ck_sessions_control_state,
            DROP CONSTRAINT ck_sessions_execution_mode,
            DROP CONSTRAINT ck_sessions_privacy_mode,
            DROP CONSTRAINT ck_sessions_tokens_used_nonnegative,
            DROP CONSTRAINT ck_sessions_inbox_cursor_nonnegative,
            DROP COLUMN user_id,
            DROP COLUMN execution_mode,
            DROP COLUMN privacy_mode,
            DROP COLUMN tokens_used,
            DROP COLUMN inbox_handled_through_event_id,
            ALTER COLUMN control_state SET DEFAULT 'planning';
        UPDATE sessions s SET control_state = CASE
            WHEN EXISTS (SELECT 1 FROM node_runs r
                         WHERE r.session_id = s.session_id AND r.status = 'running') THEN 'running'
            WHEN EXISTS (SELECT 1 FROM node_runs r
                         WHERE r.session_id = s.session_id AND r.status = 'failed') THEN 'failed'
            WHEN EXISTS (SELECT 1 FROM session_graphs g
                         WHERE g.session_id = s.session_id AND g.approved_at IS NOT NULL)
             AND EXISTS (SELECT 1 FROM graph_nodes n WHERE n.session_id = s.session_id)
             AND NOT EXISTS (SELECT 1 FROM graph_nodes n
                             LEFT JOIN node_runs r ON r.node_id = n.node_id
                             WHERE n.session_id = s.session_id
                               AND r.status IS DISTINCT FROM 'completed') THEN 'completed'
            ELSE 'ready' END
        WHERE control_state = 'active';
        ALTER TABLE sessions ADD CONSTRAINT ck_sessions_control_state CHECK (control_state IN
            ('planning', 'ready', 'running', 'pausing', 'paused', 'completed', 'failed', 'archived'));
        DROP TABLE users;
        """,
    ))

    op.execute(_o1_functions())
    _execute_all([ddl for _, _, ddl in V1_TRIGGERS])
