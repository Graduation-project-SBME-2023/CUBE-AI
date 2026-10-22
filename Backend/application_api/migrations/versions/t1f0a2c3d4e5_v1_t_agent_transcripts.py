"""v1_t: agent transcripts and user-requested approvals (Cut-over 3, Phase 6.6)

Appendix P revision 3.4 (D27 rewritten, D28 new):

1. `agent_checkpoints` and `agent_checkpoint_writes` (LangGraph) are dropped;
   `agent_transcript_entries` (an agent node's Claude Agent SDK session, one per
   run) takes their place under the shared fencing trigger, and
   `main_transcript_entries` holds the Main's session (D21, not fenced);
2. `run_attempts.pending_tools`; the `suspension` contract is v2
   (`ask_user | tool_approval | pause`, `tool_use_id`, `form`): a suspension
   no longer waits for a stored checkpoint;
3. `graph_nodes.approval` (`none | before | after`) and
   `start_released_by_change_id`; the `user_review` hold on `node_runs`; only a
   change the user authored releases either (I21), and a start waits for the
   release;
4. the automatic-approval values: `cohorts.frozen_by` (`user | auto`),
   `segmentation_batches.review_status = 'auto_accepted'`,
   `segmentation_pairs.review_decision = 'auto'`;
5. `chat_messages` drops the Main's tool-call contract (its pairing trigger and
   the one-result index): the Main's tool calls live in its SDK transcript.

Nothing in production wrote the checkpoint tables or a v1 suspension, so no
data is converted; the upgrade stops if a v1 suspension exists. downgrade()
refuses whenever this migration's data exists.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "r1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRANSCRIPT_TABLE = "agent_transcript_entries"
CHECKPOINT_TABLES = ("agent_checkpoints", "agent_checkpoint_writes")
QUESTIONS = ("ask_user", "tool_approval")
SUSPENSION_KINDS = (*QUESTIONS, "pause")

# Every table execution writes, as of this revision.
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
    TRANSCRIPT_TABLE,
)


def _sql_list(values: Sequence[str]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _previous(filename: str):
    """An earlier revision module, to restore its definitions verbatim."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"_cubeai_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _execute_all(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


# ----------------------------------------------------------------------
# Triggers, as (name, table, DDL); upgrade() creates them, downgrade() drops them.
# ----------------------------------------------------------------------
T_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    (
        f"{TRANSCRIPT_TABLE}_fencing", TRANSCRIPT_TABLE,
        f"CREATE TRIGGER {TRANSCRIPT_TABLE}_fencing BEFORE INSERT OR UPDATE OR DELETE ON {TRANSCRIPT_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_producer()",
    ),
    (
        f"{TRANSCRIPT_TABLE}_immutable", TRANSCRIPT_TABLE,
        f"CREATE TRIGGER {TRANSCRIPT_TABLE}_immutable BEFORE UPDATE ON {TRANSCRIPT_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION transcript_entries_are_immutable()",
    ),
    (
        "main_transcript_entries_immutable", "main_transcript_entries",
        "CREATE TRIGGER main_transcript_entries_immutable BEFORE UPDATE ON main_transcript_entries "
        "FOR EACH ROW EXECUTE FUNCTION transcript_entries_are_immutable()",
    ),
    (
        "graph_nodes_user_release", "graph_nodes",
        "CREATE TRIGGER graph_nodes_user_release BEFORE INSERT OR UPDATE OF start_released_by_change_id "
        "ON graph_nodes FOR EACH ROW EXECUTE FUNCTION user_approvals_enforce_author()",
    ),
    (
        "node_runs_user_approvals", "node_runs",
        "CREATE TRIGGER node_runs_user_approvals BEFORE INSERT OR UPDATE OF hold_released_by_change_id "
        "ON node_runs FOR EACH ROW EXECUTE FUNCTION user_approvals_enforce_author()",
    ),
)
T_FUNCTIONS = (
    "agent_transcripts_require_purgeable(uuid)",
    "transcript_entries_are_immutable()",
    "user_approvals_enforce_author()",
)
P_CHECKPOINT_FUNCTIONS = (
    "agent_checkpoints_enforce_row_rules()",
    "agent_checkpoints_require_purgeable(uuid)",
)


# ----------------------------------------------------------------------
# Trigger functions
# ----------------------------------------------------------------------
_FUNCTIONS_T = r"""
CREATE OR REPLACE FUNCTION public.agent_transcripts_require_purgeable(target uuid)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
    run record;
    retention integer := coalesce(
        nullif(current_setting('cubeai.transcript_retention_days', true), '')::integer, 14);
BEGIN
    SELECT r.session_id, r.completed_at INTO run FROM node_runs r WHERE r.run_id = target;
    IF run.completed_at IS NULL OR run.completed_at > now() - make_interval(days => retention) THEN
        RAISE EXCEPTION
            'the transcript of run % is kept until % days after the run ends', target, retention
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
        RAISE EXCEPTION 'the transcript of run % is named by a live seed_from', target
            USING ERRCODE = 'restrict_violation';
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION public.transcript_entries_are_immutable()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION '% rows are immutable; a new step is a new entry', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$function$;

-- D28 / I21: a release of what the user asked to approve (an approval: before
-- start, a user_review hold) is made only by a change the user authored, and an
-- approval: before node starts only once released.
CREATE OR REPLACE FUNCTION public.user_approvals_enforce_author()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    releasing uuid;
    change_author text;
    node record;
BEGIN
    IF current_setting('cubeai.actor', true) = 'migration' THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'graph_nodes' THEN
        IF NEW.start_released_by_change_id IS NULL THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF NEW.start_released_by_change_id IS NOT DISTINCT FROM OLD.start_released_by_change_id THEN
                RETURN NEW;
            END IF;
        END IF;
        releasing := NEW.start_released_by_change_id;
    ELSIF TG_OP = 'INSERT' THEN
        SELECT n.approval, n.start_released_by_change_id INTO node
        FROM graph_nodes n WHERE n.node_id = NEW.node_id;
        IF FOUND AND node.approval = 'before' AND node.start_released_by_change_id IS NULL THEN
            RAISE EXCEPTION 'node % waits for the user to release it (approval: before)', NEW.node_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    ELSE
        IF OLD.downstream_hold IS DISTINCT FROM 'user_review' OR NEW.hold_released_by_change_id IS NULL
           OR NEW.hold_released_by_change_id IS NOT DISTINCT FROM OLD.hold_released_by_change_id THEN
            RETURN NEW;
        END IF;
        releasing := NEW.hold_released_by_change_id;
    END IF;
    SELECT c.author INTO change_author FROM graph_changes c WHERE c.change_id = releasing;
    IF change_author IS DISTINCT FROM 'user' THEN
        RAISE EXCEPTION
            'what the user asked to approve is released only by a change the user authored; '
            'change % is by %', releasing, coalesce(change_author, 'nobody')
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;
"""

_RUN_ATTEMPTS_RULES_T = rf"""
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
           OR NEW.suspension IS NOT NULL OR NEW.resumed_at IS NOT NULL OR NEW.usage IS NOT NULL
           OR NEW.pending_tools IS NOT NULL THEN
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

    -- T10. Suspend undecided with a v2 suspension that names its tool call (a
    -- pause names none); an answer only adds `resume` to a question or a tool
    -- approval; resume only in an active session, on a current run, answered.
    IF OLD.state = 'running' AND NEW.state = 'suspended' THEN
        IF NEW.suspension ? 'resume' OR NEW.suspension ->> 'v' IS DISTINCT FROM '2'
           OR NEW.suspension ->> 'kind' NOT IN {_sql_list(SUSPENSION_KINDS)}
           OR (NEW.suspension ->> 'kind' = 'pause') = (NEW.suspension ? 'tool_use_id') THEN
            RAISE EXCEPTION
                'attempt % suspends undecided, with a v2 suspension that names its tool call '
                '(a pause names none)', OLD.attempt_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.suspension IS DISTINCT FROM OLD.suspension AND NOT (
           OLD.state = 'suspended' AND NEW.state = 'suspended'
       AND OLD.suspension ->> 'kind' IN {_sql_list(QUESTIONS)}
       AND NOT OLD.suspension ? 'resume' AND NEW.suspension ? 'resume'
       AND (NEW.suspension - 'resume') = (OLD.suspension - 'resume')) THEN
        RAISE EXCEPTION 'attempt %: a suspension changes only by its one answer', OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.state = 'suspended' AND NEW.state = 'running' THEN
        SELECT s.control_state INTO session_state FROM sessions s WHERE s.session_id = NEW.session_id;
        SELECT r.superseded_at INTO run FROM node_runs r WHERE r.run_id = NEW.run_id;
        IF session_state IS DISTINCT FROM 'active' OR run.superseded_at IS NOT NULL
           OR (OLD.suspension ->> 'kind' IN {_sql_list(QUESTIONS)} AND NOT OLD.suspension ? 'resume')
           OR NEW.resumed_at IS NULL OR NEW.resumed_at IS NOT DISTINCT FROM OLD.resumed_at THEN
            RAISE EXCEPTION
                'attempt % resumes only in an active session, on a current run, once '
                'answered, with a new resumed_at', OLD.attempt_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.resumed_at IS DISTINCT FROM OLD.resumed_at THEN
        RAISE EXCEPTION 'attempt %: resumed_at changes only when a suspended attempt resumes',
            OLD.attempt_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The calls a running (or suspended) attempt is making, for retry rule (f).
    IF NEW.pending_tools IS DISTINCT FROM OLD.pending_tools AND OLD.state NOT IN ('running', 'suspended') THEN
        RAISE EXCEPTION 'attempt %: pending_tools change only while it runs or is suspended',
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
"""

# The shared fencing trigger as of this revision. Changes from v1_p: the
# transcript table replaces the checkpoint tables; the user gate path finds the
# attempt suspended on an `ask_user` form; an attempt may set its own
# pending_tools; `auto_accepted` is a selection; a cohort is frozen `auto` by
# its attempt and `user` by the user gate path.
_DOMAIN_ROWS_FENCING_T = r"""
CREATE OR REPLACE FUNCTION public.domain_rows_enforce_producer()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    actor text := nullif(current_setting('cubeai.actor', true), '');
    attempt_key text := nullif(current_setting('cubeai.attempt_id', true), '');
    job_run text := nullif(current_setting('cubeai.run_id', true), '');
    execution_table boolean := TG_TABLE_NAME IN ('node_runs', 'run_attempts', 'agent_transcript_entries');
    transcript_table boolean := TG_TABLE_NAME = 'agent_transcript_entries';
    -- The `ask_user` form a table's decision columns belong to, and those columns.
    form text := CASE TG_TABLE_NAME
        WHEN 'cohorts' THEN 'cohort_approval'
        WHEN 'segmentation_pairs' THEN 'segmentation_review'
        WHEN 'segmentation_batches' THEN 'segmentation_review' END;
    gate_columns text[] := CASE TG_TABLE_NAME
        WHEN 'cohorts' THEN ARRAY['frozen_at', 'frozen_by']
        WHEN 'segmentation_pairs' THEN ARRAY['review_decision', 'selected_segmentation_uid']
        WHEN 'segmentation_batches' THEN ARRAY['review_status', 'review_request_hash', 'reviewed_at']
        WHEN 'run_attempts' THEN ARRAY['suspension'] END;
    -- Who freezes a cohort on this path (D28).
    frozen_by_path text := CASE WHEN actor = 'user' THEN 'user' ELSE 'auto' END;
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
        row_run := OLD.run_id;  -- only the transcript table fences a DELETE
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
        selecting := NEW.review_status IN ('submitting', 'submitted', 'auto_accepted')
            AND (TG_OP = 'INSERT' OR NEW.review_status IS DISTINCT FROM OLD.review_status);
    END IF;

    -- D28: the user freezes a cohort on the user gate path; the population
    -- node freezes its own cohort automatically on its attempt path.
    IF TG_TABLE_NAME = 'cohorts' AND selecting THEN
        IF NEW.frozen_at IS NOT NULL AND NEW.frozen_by IS DISTINCT FROM frozen_by_path THEN
            RAISE EXCEPTION 'cohort %: frozen_by is % on this write path, not %', NEW.cohort_id,
                frozen_by_path, coalesce(NEW.frozen_by, 'null')
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    IF actor IS NULL AND attempt_key IS NULL AND job_run IS NULL THEN
        RAISE EXCEPTION
            '% write of run % declares no write path: set cubeai.attempt_id, cubeai.run_id '
            'or cubeai.actor', TG_TABLE_NAME, row_run
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF actor = 'harness' THEN
        -- Apply, start, dispatch, reconcile, the run projection, cancel, suspend
        -- and resume, and the transcript purge: lifecycle only.
        IF TG_TABLE_NAME = 'node_runs' THEN
            IF TG_OP = 'UPDATE' AND NOT row_changes_only(to_jsonb(OLD), to_jsonb(NEW), ARRAY[
                   'status', 'error', 'completed_at', 'superseded_at', 'outputs',
                   'downstream_hold', 'hold_released_by_change_id']) THEN
                RAISE EXCEPTION 'the harness path writes only lifecycle columns of node_runs'
                    USING ERRCODE = 'restrict_violation';
            END IF;
        ELSIF transcript_table THEN
            IF TG_OP = 'DELETE' THEN
                PERFORM agent_transcripts_require_purgeable(OLD.run_id);
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'the harness path only purges transcript entries; an attempt writes them'
                USING ERRCODE = 'restrict_violation';
        ELSIF TG_TABLE_NAME <> 'run_attempts' THEN
            RAISE EXCEPTION 'the harness path never writes % rows', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;

    ELSIF actor = 'user' THEN
        -- The owner's answer to a suspended attempt: decision columns only,
        -- while the run is running and current and suspended on that form.
        IF TG_OP <> 'UPDATE' OR gate_columns IS NULL
           OR NOT row_changes_only(to_jsonb(OLD), to_jsonb(NEW), gate_columns) THEN
            RAISE EXCEPTION 'the user gate path changes only gate columns, not % rows',
                TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END IF;
        -- On run_attempts itself, the row rules check the answer.
        IF form IS NOT NULL THEN
            PERFORM 1 FROM run_attempts a
            WHERE a.run_id = row_run AND a.state = 'suspended'
              AND a.suspension ->> 'kind' = 'ask_user' AND a.suspension ->> 'form' = form
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'run % is not suspended on its % form', row_run, form
                    USING ERRCODE = 'restrict_violation';
            END IF;
        END IF;
        PERFORM fencing_require_current_run(row_run, 'an answer');
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
        -- Its own transcript entries and its own pending tools: the run's
        -- latest attempt, running or suspended (D27).
        IF transcript_table THEN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'an attempt only appends transcript entries'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.attempt_id <> attempt.attempt_id OR attempt.state NOT IN ('running', 'suspended')
               OR EXISTS (SELECT 1 FROM run_attempts a
                          WHERE a.run_id = row_run AND a.attempt_number > attempt.attempt_number) THEN
                RAISE EXCEPTION
                    'only the latest running or suspended attempt of run % writes its transcript', row_run
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_TABLE_NAME = 'run_attempts' THEN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.attempt_id = attempt.attempt_id
                   AND row_changes_only(to_jsonb(OLD), to_jsonb(NEW), ARRAY['pending_tools']) THEN
                    IF attempt.state NOT IN ('running', 'suspended')
                       OR EXISTS (SELECT 1 FROM run_attempts a
                                  WHERE a.run_id = row_run AND a.attempt_number > attempt.attempt_number) THEN
                        RAISE EXCEPTION
                            'only the latest running or suspended attempt of run % sets its pending tools',
                            row_run
                            USING ERRCODE = 'restrict_violation';
                    END IF;
                    RETURN NEW;
                END IF;
            END IF;
            -- The worker moves its own attempt to running before it writes.
            IF TG_OP <> 'UPDATE' OR NEW.attempt_id <> attempt.attempt_id
               OR NEW.state <> 'running' OR OLD.state NOT IN ('preparing', 'submitted')
               OR NOT row_changes_only(to_jsonb(OLD), to_jsonb(NEW),
                                       ARRAY['state', 'dispatched_at', 'started_at']) THEN
                RAISE EXCEPTION 'attempt % may only mark itself running or set its pending tools',
                    attempt_key
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

# A review accepted automatically (D28) is final like a submitted one; every
# ready pair carries `auto` and keeps its candidate, every other pair nothing.
_SEGMENTATION_RULES_T = r"""
CREATE OR REPLACE FUNCTION public.segmentation_batches_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    offending integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation batch % is review provenance and cannot be deleted',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.segmentation_batch_id IS DISTINCT FROM OLD.segmentation_batch_id
       OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.batch_key IS DISTINCT FROM OLD.batch_key
       OR NEW.config IS DISTINCT FROM OLD.config
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation batch % identity and config are immutable',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status IN ('submitted', 'auto_accepted') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'segmentation batch % review is % and final',
            OLD.segmentation_batch_id, OLD.review_status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status IS DISTINCT FROM OLD.review_status AND NOT (
           (OLD.review_status = 'not_ready'  AND NEW.review_status = 'pending')
        OR (OLD.review_status = 'pending'    AND NEW.review_status IN ('not_ready', 'submitting', 'auto_accepted'))
        OR (OLD.review_status = 'submitting' AND NEW.review_status IN ('pending', 'submitted'))
    ) THEN
        RAISE EXCEPTION 'segmentation batch % review cannot move from % to %',
            OLD.segmentation_batch_id, OLD.review_status, NEW.review_status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitting' AND NEW.review_status = 'submitting'
       AND NEW.review_request_hash IS DISTINCT FROM OLD.review_request_hash THEN
        RAISE EXCEPTION
            'segmentation batch % is already submitting a different review',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status = 'submitted' AND OLD.review_status <> 'submitted' THEN
        -- coalesce(..., false): `review_decision` is NULL for an undecided
        -- pair, and in three-valued logic an un-coalesced OR chain evaluates
        -- to NULL there — which would let exactly the case this check exists
        -- for (a ready pair nobody decided) through.
        SELECT count(*) INTO offending
        FROM segmentation_pairs p
        WHERE p.segmentation_batch_id = NEW.segmentation_batch_id
          AND coalesce(
                (p.status <> 'ready'
                 AND p.review_decision IS NULL AND p.selected_segmentation_uid IS NULL)
             OR (p.status = 'ready' AND p.review_decision = 'rejected'
                 AND p.selected_segmentation_uid IS NULL)
             OR (p.status = 'ready' AND p.review_decision = 'modified'
                 AND p.selected_segmentation_uid IS NOT NULL)
             OR (p.status = 'ready' AND p.review_decision = 'accepted'
                 AND p.selected_segmentation_uid IS NOT DISTINCT FROM p.candidate_segmentation_uid),
                false
          ) IS FALSE;
        IF offending > 0 THEN
            RAISE EXCEPTION
                'segmentation batch % cannot be submitted: % pair(s) lack a final '
                'decision consistent with their candidate and selected SEG',
                NEW.segmentation_batch_id, offending
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.review_status = 'auto_accepted' AND OLD.review_status <> 'auto_accepted' THEN
        SELECT count(*) INTO offending
        FROM segmentation_pairs p
        WHERE p.segmentation_batch_id = NEW.segmentation_batch_id
          AND coalesce(
                (p.status = 'ready' AND p.review_decision = 'auto'
                 AND p.selected_segmentation_uid IS NOT DISTINCT FROM p.candidate_segmentation_uid)
             OR (p.status <> 'ready'
                 AND p.review_decision IS NULL AND p.selected_segmentation_uid IS NULL),
                false
          ) IS FALSE;
        IF offending > 0 THEN
            RAISE EXCEPTION
                'segmentation batch % cannot be accepted automatically: % pair(s) are not '
                'auto with their candidate selected (or undecided when not ready)',
                NEW.segmentation_batch_id, offending
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.segmentation_pairs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    run_review text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation pair % is review provenance and cannot be deleted',
            OLD.pair_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    SELECT review_status INTO run_review
    FROM segmentation_batches WHERE segmentation_batch_id = NEW.segmentation_batch_id;
    IF run_review IN ('submitted', 'auto_accepted') THEN
        RAISE EXCEPTION
            'segmentation batch % review is %; its pairs are final',
            NEW.segmentation_batch_id, run_review
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    IF NEW.pair_id IS DISTINCT FROM OLD.pair_id
       OR NEW.segmentation_batch_id IS DISTINCT FROM OLD.segmentation_batch_id
       OR NEW.target_id IS DISTINCT FROM OLD.target_id
       OR NEW.volume_series_instance_uid IS DISTINCT FROM OLD.volume_series_instance_uid
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.cohort_member_id IS DISTINCT FROM OLD.cohort_member_id
       OR NEW.segment_number IS DISTINCT FROM OLD.segment_number
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation pair % identity is immutable', OLD.pair_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The MONAI receipt pointer is content-derived: it may be recorded once,
    -- never re-pointed at a different receipt (R15).
    IF OLD.inference_job_id IS NOT NULL
       AND NEW.inference_job_id IS DISTINCT FROM OLD.inference_job_id THEN
        RAISE EXCEPTION
            'segmentation pair % already reconciles against MONAI job %',
            OLD.pair_id, OLD.inference_job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;
"""

# The v1_o bodies of the two segmentation rules, restored by downgrade().
_SEGMENTATION_RULES_O = r"""
CREATE OR REPLACE FUNCTION public.segmentation_batches_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    offending integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation batch % is review provenance and cannot be deleted',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.segmentation_batch_id IS DISTINCT FROM OLD.segmentation_batch_id
       OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.batch_key IS DISTINCT FROM OLD.batch_key
       OR NEW.config IS DISTINCT FROM OLD.config
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation batch % identity and config are immutable',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitted' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'segmentation batch % review is submitted and final',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status IS DISTINCT FROM OLD.review_status AND NOT (
           (OLD.review_status = 'not_ready'  AND NEW.review_status = 'pending')
        OR (OLD.review_status = 'pending'    AND NEW.review_status IN ('not_ready', 'submitting'))
        OR (OLD.review_status = 'submitting' AND NEW.review_status IN ('pending', 'submitted'))
    ) THEN
        RAISE EXCEPTION 'segmentation batch % review cannot move from % to %',
            OLD.segmentation_batch_id, OLD.review_status, NEW.review_status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitting' AND NEW.review_status = 'submitting'
       AND NEW.review_request_hash IS DISTINCT FROM OLD.review_request_hash THEN
        RAISE EXCEPTION
            'segmentation batch % is already submitting a different review',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status = 'submitted' AND OLD.review_status <> 'submitted' THEN
        -- coalesce(..., false): `review_decision` is NULL for an undecided
        -- pair, and in three-valued logic an un-coalesced OR chain evaluates
        -- to NULL there — which would let exactly the case this check exists
        -- for (a ready pair nobody decided) through.
        SELECT count(*) INTO offending
        FROM segmentation_pairs p
        WHERE p.segmentation_batch_id = NEW.segmentation_batch_id
          AND coalesce(
                (p.status <> 'ready'
                 AND p.review_decision IS NULL AND p.selected_segmentation_uid IS NULL)
             OR (p.status = 'ready' AND p.review_decision = 'rejected'
                 AND p.selected_segmentation_uid IS NULL)
             OR (p.status = 'ready' AND p.review_decision = 'modified'
                 AND p.selected_segmentation_uid IS NOT NULL)
             OR (p.status = 'ready' AND p.review_decision = 'accepted'
                 AND p.selected_segmentation_uid IS NOT DISTINCT FROM p.candidate_segmentation_uid),
                false
          ) IS FALSE;
        IF offending > 0 THEN
            RAISE EXCEPTION
                'segmentation batch % cannot be submitted: % pair(s) lack a final '
                'decision consistent with their candidate and selected SEG',
                NEW.segmentation_batch_id, offending
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.segmentation_pairs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    run_review text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation pair % is review provenance and cannot be deleted',
            OLD.pair_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    SELECT review_status INTO run_review
    FROM segmentation_batches WHERE segmentation_batch_id = NEW.segmentation_batch_id;
    IF run_review = 'submitted' THEN
        RAISE EXCEPTION
            'segmentation batch % review is submitted; its pairs are final',
            NEW.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    IF NEW.pair_id IS DISTINCT FROM OLD.pair_id
       OR NEW.segmentation_batch_id IS DISTINCT FROM OLD.segmentation_batch_id
       OR NEW.target_id IS DISTINCT FROM OLD.target_id
       OR NEW.volume_series_instance_uid IS DISTINCT FROM OLD.volume_series_instance_uid
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.cohort_member_id IS DISTINCT FROM OLD.cohort_member_id
       OR NEW.segment_number IS DISTINCT FROM OLD.segment_number
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation pair % identity is immutable', OLD.pair_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The MONAI receipt pointer is content-derived: it may be recorded once,
    -- never re-pointed at a different receipt (R15).
    IF OLD.inference_job_id IS NOT NULL
       AND NEW.inference_job_id IS DISTINCT FROM OLD.inference_job_id THEN
        RAISE EXCEPTION
            'segmentation pair % already reconciles against MONAI job %',
            OLD.pair_id, OLD.inference_job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;
"""


# ----------------------------------------------------------------------
# Upgrade
# ----------------------------------------------------------------------
def _refuse_v1_suspensions(bind: sa.engine.Connection) -> None:
    count = bind.execute(sa.text(
        "SELECT count(*) FROM run_attempts WHERE suspension IS NOT NULL "
        f"AND suspension ->> 'kind' NOT IN {_sql_list(SUSPENSION_KINDS)}"
    )).scalar_one()
    if count:
        raise RuntimeError(
            f"The agent-transcript migration stopped: {count} attempt(s) carry a v1 (LangGraph) suspension, "
            "which the v2 contract cannot hold. Nothing should have written one outside tests; inspect them with "
            "SELECT attempt_id, run_id, state, suspension FROM run_attempts WHERE suspension IS NOT NULL;"
        )


def _drop_checkpoints_and_tool_pairing() -> None:
    _execute_all((
        "DROP TRIGGER chat_messages_tool_pairing ON chat_messages",
        "DROP FUNCTION chat_messages_enforce_tool_pairing()",
        "DROP INDEX uq_chat_messages_one_tool_result",
        # Their row-rule and fencing triggers go with them.
        "DROP TABLE agent_checkpoint_writes, agent_checkpoints",
    ))


def _extend_execution_tables() -> None:
    _execute_all((
        f"""
        ALTER TABLE run_attempts
            ADD COLUMN pending_tools jsonb,
            DROP CONSTRAINT ck_run_attempts_json_versioned,
            DROP CONSTRAINT ck_run_attempts_json_bounded,
            ADD CONSTRAINT ck_run_attempts_json_versioned CHECK (
                (error IS NULL OR (jsonb_typeof(error) = 'object' AND error ? 'code'))
                AND (usage IS NULL OR usage ? 'v')
                AND (suspension IS NULL
                     OR (suspension ? 'v' AND suspension ->> 'kind' IN {_sql_list(SUSPENSION_KINDS)}))
                AND (pending_tools IS NULL
                     OR (jsonb_typeof(pending_tools) = 'object' AND pending_tools ? 'v'
                         AND jsonb_typeof(pending_tools -> 'calls') = 'array'))),
            ADD CONSTRAINT ck_run_attempts_json_bounded CHECK (
                COALESCE(pg_column_size(request), 0) <= 2097152
                AND COALESCE(pg_column_size(error), 0) <= 262144
                AND COALESCE(pg_column_size(suspension), 0) <= 1048576
                AND COALESCE(pg_column_size(usage), 0) <= 65536
                AND COALESCE(pg_column_size(pending_tools), 0) <= 262144)
        """,
        """
        ALTER TABLE graph_nodes
            ADD COLUMN approval text NOT NULL DEFAULT 'none',
            ADD COLUMN start_released_by_change_id uuid,
            ADD CONSTRAINT ck_graph_nodes_approval CHECK (approval IN ('none', 'before', 'after')),
            ADD CONSTRAINT ck_graph_nodes_start_release_needs_before
                CHECK (start_released_by_change_id IS NULL OR approval = 'before'),
            ADD CONSTRAINT fk_graph_nodes_start_released_by_change_id_graph_changes
                FOREIGN KEY (start_released_by_change_id, session_id)
                REFERENCES graph_changes (change_id, session_id) ON DELETE RESTRICT
        """,
        """
        ALTER TABLE node_runs
            DROP CONSTRAINT ck_node_runs_downstream_hold,
            ADD CONSTRAINT ck_node_runs_downstream_hold CHECK (downstream_hold IS NULL
                OR (status = 'completed' AND downstream_hold IN ('expectation_violated', 'user_review')))
        """,
    ))


def _automatic_approval_values() -> None:
    _execute_all((
        "ALTER TABLE cohorts ADD COLUMN frozen_by text",
        # Every cohort frozen so far was frozen by the doctor's approval.
        "UPDATE cohorts SET frozen_by = 'user' WHERE frozen_at IS NOT NULL",
        """
        ALTER TABLE cohorts
            ADD CONSTRAINT ck_cohorts_frozen_by CHECK (frozen_by IS NULL OR frozen_by IN ('user', 'auto')),
            ADD CONSTRAINT ck_cohorts_frozen_by_iff_frozen CHECK ((frozen_by IS NULL) = (frozen_at IS NULL))
        """,
        """
        ALTER TABLE segmentation_batches
            DROP CONSTRAINT ck_segmentation_batches_review_status,
            DROP CONSTRAINT ck_segmentation_batches_reviewed_at_iff_submitted,
            ADD CONSTRAINT ck_segmentation_batches_review_status CHECK
                (review_status IN ('not_ready', 'pending', 'submitting', 'submitted', 'auto_accepted')),
            ADD CONSTRAINT ck_segmentation_batches_reviewed_at_iff_submitted CHECK
                ((review_status IN ('submitted', 'auto_accepted')) = (reviewed_at IS NOT NULL))
        """,
        """
        ALTER TABLE segmentation_pairs
            DROP CONSTRAINT ck_segmentation_pairs_review_decision,
            DROP CONSTRAINT ck_segmentation_pairs_selected_implies_positive_decision,
            ADD CONSTRAINT ck_segmentation_pairs_review_decision CHECK
                (review_decision IS NULL OR review_decision IN ('accepted', 'modified', 'rejected', 'auto')),
            ADD CONSTRAINT ck_segmentation_pairs_selected_implies_positive_decision CHECK
                (selected_segmentation_uid IS NULL OR review_decision IN ('accepted', 'modified', 'auto'))
        """,
    ))


def _create_transcripts() -> None:
    _execute_all((
        f"""
        CREATE TABLE {TRANSCRIPT_TABLE} (
            run_id uuid NOT NULL,
            subpath text NOT NULL DEFAULT '',
            seq bigint GENERATED ALWAYS AS IDENTITY,
            session_id uuid NOT NULL,
            attempt_id uuid NOT NULL,
            entry_uuid text NOT NULL,
            entry jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_{TRANSCRIPT_TABLE} PRIMARY KEY (run_id, subpath, seq),
            CONSTRAINT uq_{TRANSCRIPT_TABLE}_run_id_subpath_entry_uuid UNIQUE (run_id, subpath, entry_uuid),
            CONSTRAINT fk_{TRANSCRIPT_TABLE}_run_id_session_id_node_runs FOREIGN KEY (run_id, session_id)
                REFERENCES node_runs (run_id, session_id) ON DELETE RESTRICT,
            CONSTRAINT fk_{TRANSCRIPT_TABLE}_attempt_id_run_id_run_attempts FOREIGN KEY (attempt_id, run_id)
                REFERENCES run_attempts (attempt_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT ck_{TRANSCRIPT_TABLE}_entry_uuid_not_blank CHECK (length(btrim(entry_uuid)) > 0),
            CONSTRAINT ck_{TRANSCRIPT_TABLE}_entry_bounded
                CHECK (jsonb_typeof(entry) = 'object' AND pg_column_size(entry) <= 4194304)
        )
        """,
        """
        CREATE TABLE main_transcript_entries (
            session_id uuid NOT NULL,
            subpath text NOT NULL DEFAULT '',
            seq bigint GENERATED ALWAYS AS IDENTITY,
            entry_uuid text NOT NULL,
            entry jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_main_transcript_entries PRIMARY KEY (session_id, subpath, seq),
            CONSTRAINT uq_main_transcript_entries_session_id_subpath_entry_uuid
                UNIQUE (session_id, subpath, entry_uuid),
            CONSTRAINT fk_main_transcript_entries_session_id_sessions FOREIGN KEY (session_id)
                REFERENCES sessions (session_id) ON DELETE RESTRICT,
            CONSTRAINT ck_main_transcript_entries_entry_uuid_not_blank CHECK (length(btrim(entry_uuid)) > 0),
            CONSTRAINT ck_main_transcript_entries_entry_bounded
                CHECK (jsonb_typeof(entry) = 'object' AND pg_column_size(entry) <= 4194304)
        )
        """,
    ))


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_v1_suspensions(bind)
    op.execute("SET LOCAL cubeai.actor = 'migration'")  # the migration write path
    _drop_checkpoints_and_tool_pairing()
    _extend_execution_tables()
    _automatic_approval_values()
    _create_transcripts()
    _execute_all((_FUNCTIONS_T, _RUN_ATTEMPTS_RULES_T, _DOMAIN_ROWS_FENCING_T, _SEGMENTATION_RULES_T))
    for function in P_CHECKPOINT_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    _execute_all([ddl for _, _, ddl in T_TRIGGERS])


# ----------------------------------------------------------------------
# Downgrade
# ----------------------------------------------------------------------
DOWNGRADE_REFUSALS = (
    ("an agent transcript entry exists", f"SELECT count(*) FROM {TRANSCRIPT_TABLE}"),
    ("a Main transcript entry exists", "SELECT count(*) FROM main_transcript_entries"),
    ("a node asks for an approval", "SELECT count(*) FROM graph_nodes WHERE approval <> 'none'"),
    ("a run holds for a user review", "SELECT count(*) FROM node_runs WHERE downstream_hold = 'user_review'"),
    ("a cohort was frozen automatically", "SELECT count(*) FROM cohorts WHERE frozen_by = 'auto'"),
    ("a segmentation review was accepted automatically",
     "SELECT count(*) FROM segmentation_batches WHERE review_status = 'auto_accepted'"),
    ("a segmentation pair was accepted automatically",
     "SELECT count(*) FROM segmentation_pairs WHERE review_decision = 'auto'"),
    ("an attempt recorded pending tools", "SELECT count(*) FROM run_attempts WHERE pending_tools IS NOT NULL"),
    ("an attempt carries a v2 suspension", "SELECT count(*) FROM run_attempts WHERE suspension IS NOT NULL"),
)


def _refuse_lossy_downgrade(bind: sa.engine.Connection) -> None:
    reasons = [
        f"{reason} ({count})"
        for reason, query in DOWNGRADE_REFUSALS
        if (count := bind.execute(sa.text(query)).scalar_one())
    ]
    if reasons:
        raise RuntimeError(
            "Refusing to downgrade the agent-transcript migration: the data has moved past what the "
            "v1_p schema can hold: " + "; ".join(reasons) + "."
        )


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_lossy_downgrade(bind)
    op.execute("SET LOCAL cubeai.actor = 'migration'")
    session_graph = _previous("p1f0a2c3d4e5_v1_p_session_graph.py")

    # Rules first: a deferred trigger with pending events blocks ALTER TABLE.
    for name, table, _ in T_TRIGGERS:
        op.execute(f"DROP TRIGGER {name} ON {table}")
    _execute_all((
        f"DROP TABLE main_transcript_entries, {TRANSCRIPT_TABLE}",
        # v1_p's function bodies (the run_attempts rules, the fencing trigger, the
        # checkpoint and tool-pairing functions) and v1_o's segmentation rules.
        session_graph._FUNCTIONS_P,
        _SEGMENTATION_RULES_O,
    ))
    for function in T_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")

    _execute_all((
        """
        ALTER TABLE segmentation_pairs
            DROP CONSTRAINT ck_segmentation_pairs_review_decision,
            DROP CONSTRAINT ck_segmentation_pairs_selected_implies_positive_decision,
            ADD CONSTRAINT ck_segmentation_pairs_review_decision CHECK
                (review_decision IS NULL OR review_decision IN ('accepted', 'modified', 'rejected')),
            ADD CONSTRAINT ck_segmentation_pairs_selected_implies_positive_decision CHECK
                (selected_segmentation_uid IS NULL OR review_decision IN ('accepted', 'modified'))
        """,
        """
        ALTER TABLE segmentation_batches
            DROP CONSTRAINT ck_segmentation_batches_review_status,
            DROP CONSTRAINT ck_segmentation_batches_reviewed_at_iff_submitted,
            ADD CONSTRAINT ck_segmentation_batches_review_status CHECK
                (review_status IN ('not_ready', 'pending', 'submitting', 'submitted')),
            ADD CONSTRAINT ck_segmentation_batches_reviewed_at_iff_submitted CHECK
                ((review_status = 'submitted') = (reviewed_at IS NOT NULL))
        """,
        """
        ALTER TABLE cohorts
            DROP CONSTRAINT ck_cohorts_frozen_by,
            DROP CONSTRAINT ck_cohorts_frozen_by_iff_frozen,
            DROP COLUMN frozen_by
        """,
        """
        ALTER TABLE node_runs
            DROP CONSTRAINT ck_node_runs_downstream_hold,
            ADD CONSTRAINT ck_node_runs_downstream_hold CHECK (downstream_hold IS NULL
                OR (status = 'completed' AND downstream_hold = 'expectation_violated'))
        """,
        """
        ALTER TABLE graph_nodes
            DROP CONSTRAINT fk_graph_nodes_start_released_by_change_id_graph_changes,
            DROP CONSTRAINT ck_graph_nodes_start_release_needs_before,
            DROP CONSTRAINT ck_graph_nodes_approval,
            DROP COLUMN start_released_by_change_id,
            DROP COLUMN approval
        """,
        """
        ALTER TABLE run_attempts
            DROP CONSTRAINT ck_run_attempts_json_versioned,
            DROP CONSTRAINT ck_run_attempts_json_bounded,
            DROP COLUMN pending_tools,
            ADD CONSTRAINT ck_run_attempts_json_versioned CHECK (
                (error IS NULL OR (jsonb_typeof(error) = 'object' AND error ? 'code'))
                AND (usage IS NULL OR usage ? 'v')
                AND (suspension IS NULL
                     OR (suspension ? 'v' AND suspension ->> 'kind' IN ('gate', 'pause')))),
            ADD CONSTRAINT ck_run_attempts_json_bounded CHECK (
                COALESCE(pg_column_size(request), 0) <= 2097152
                AND COALESCE(pg_column_size(error), 0) <= 262144
                AND COALESCE(pg_column_size(suspension), 0) <= 1048576
                AND COALESCE(pg_column_size(usage), 0) <= 65536)
        """,
        "CREATE UNIQUE INDEX uq_chat_messages_one_tool_result ON chat_messages "
        "(chat_id, (metadata ->> 'turn_id'), (metadata ->> 'call_id')) "
        "WHERE metadata ->> 'kind' = 'tool_result'",
    ))
    session_graph._create_agent_checkpoints()
    _execute_all([
        ddl for name, table, ddl in session_graph.P_TRIGGERS
        if table in CHECKPOINT_TABLES or name == "chat_messages_tool_pairing"
    ])
