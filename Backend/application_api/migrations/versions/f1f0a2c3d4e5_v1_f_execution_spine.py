"""current this flow: execution spine — agent_runs, exact approval cardinality, ws_log cursor"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors application_api/core/json_bounds.py. A drift between the two would
# turn a clean 422 into an opaque IntegrityError, so the contract test checks it.
AGENT_RUN_JSON_MAX_BYTES = 2 * 1024 * 1024
COMPILED_PROMPT_MAX_BYTES = 1 * 1024 * 1024
WS_LOG_PAYLOAD_MAX_BYTES = 4 * 1024 * 1024


_CARDINALITY_FUNCTION = """
CREATE OR REPLACE FUNCTION plans_enforce_run_cardinality()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
    approved timestamptz;
    step_count integer;
    run_count integer;
    matched integer;
BEGIN
    target := COALESCE(NEW.session_id, OLD.session_id);
    SELECT approved_at INTO approved FROM plans WHERE session_id = target;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT count(*) INTO run_count FROM agent_runs WHERE session_id = target;

    IF approved IS NULL THEN
        IF run_count > 0 THEN
            RAISE EXCEPTION
                'draft plan for session % has % agent run(s); a draft has none '
                '(A21)', target, run_count
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NULL;
    END IF;

    SELECT count(*) INTO step_count FROM plan_steps WHERE session_id = target;
    SELECT count(*) INTO matched
    FROM plan_steps s JOIN agent_runs r ON r.step_id = s.step_id
    WHERE s.session_id = target;

    IF step_count = 0 OR run_count <> step_count OR matched <> step_count THEN
        RAISE EXCEPTION
            'approved plan for session % has % step(s) and % run(s) (% matched); '
            'approval requires exactly one run per step (A21)',
            target, step_count, run_count, matched
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$;
"""

_RUN_ROW_RULES_FUNCTION = """
CREATE OR REPLACE FUNCTION agent_runs_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'agent run % is execution provenance and cannot be deleted',
            OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
       OR NEW.step_id IS DISTINCT FROM OLD.step_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id THEN
        RAISE EXCEPTION 'agent run % identity is immutable', OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'completed' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'agent run % is completed and immutable', OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.started_at IS NOT NULL AND (
           NEW.started_at IS DISTINCT FROM OLD.started_at
        OR NEW.compiled_prompt IS DISTINCT FROM OLD.compiled_prompt
        OR NEW.input_refs IS DISTINCT FROM OLD.input_refs) THEN
        RAISE EXCEPTION
            'agent run % compiled_prompt/input_refs/started_at are immutable once '
            'the run has started', OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'pending' AND NEW.status = 'running')
        OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
        OR (OLD.status = 'failed'  AND NEW.status = 'running')
    ) THEN
        RAISE EXCEPTION 'agent run % cannot move from % to %',
            OLD.agent_run_id, OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def _has_table(bind, table: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
    )


def _has_column(bind, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _count(bind, sql: str) -> int:
    return int(bind.execute(sa.text(sql)).scalar_one())


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. agent_runs
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE agent_runs (
            agent_run_id uuid NOT NULL DEFAULT gen_random_uuid(),
            step_id uuid NOT NULL,
            session_id uuid NOT NULL,
            compiled_prompt text,
            input_refs jsonb,
            status text NOT NULL DEFAULT 'pending',
            durable_notes jsonb,
            warnings jsonb,
            output_summary jsonb,
            error jsonb,
            started_at timestamptz,
            completed_at timestamptz,
            CONSTRAINT pk_agent_runs PRIMARY KEY (agent_run_id),
            CONSTRAINT uq_agent_runs_step_id UNIQUE (step_id),
            CONSTRAINT uq_agent_runs_agent_run_id UNIQUE (agent_run_id, session_id),
            CONSTRAINT fk_agent_runs_step_id_plan_steps
                FOREIGN KEY (step_id) REFERENCES plan_steps (step_id) ON DELETE RESTRICT,
            CONSTRAINT fk_agent_runs_step_id_session_id_plan_steps
                FOREIGN KEY (step_id, session_id)
                REFERENCES plan_steps (step_id, session_id) ON DELETE RESTRICT,
            CONSTRAINT ck_agent_runs_status
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            CONSTRAINT ck_agent_runs_started_iff_not_pending
                CHECK ((status = 'pending') = (started_at IS NULL)),
            CONSTRAINT ck_agent_runs_completed_at_iff_terminal
                CHECK ((status IN ('completed', 'failed')) = (completed_at IS NOT NULL)),
            CONSTRAINT ck_agent_runs_compiled_before_start
                CHECK (started_at IS NULL
                       OR (compiled_prompt IS NOT NULL
                           AND length(btrim(compiled_prompt)) > 0
                           AND input_refs IS NOT NULL)),
            CONSTRAINT ck_agent_runs_failed_has_error
                CHECK (status <> 'failed' OR error IS NOT NULL),
            CONSTRAINT ck_agent_runs_compiled_prompt_bounded
                CHECK (compiled_prompt IS NULL
                       OR octet_length(compiled_prompt) <= {COMPILED_PROMPT_MAX_BYTES}),
            CONSTRAINT ck_agent_runs_json_bounded CHECK (
                coalesce(pg_column_size(input_refs), 0) <= {AGENT_RUN_JSON_MAX_BYTES}
                AND coalesce(pg_column_size(durable_notes), 0) <= {AGENT_RUN_JSON_MAX_BYTES}
                AND coalesce(pg_column_size(warnings), 0) <= {AGENT_RUN_JSON_MAX_BYTES}
                AND coalesce(pg_column_size(output_summary), 0) <= {AGENT_RUN_JSON_MAX_BYTES}
                AND coalesce(pg_column_size(error), 0) <= {AGENT_RUN_JSON_MAX_BYTES}
            )
        )
        """
    )
    # the design notes minimum index set.
    op.execute(
        "CREATE INDEX ix_agent_runs_session_id_status ON agent_runs (session_id, status)"
    )

    op.execute(_RUN_ROW_RULES_FUNCTION)
    op.execute(
        "CREATE TRIGGER agent_runs_row_rules BEFORE UPDATE OR DELETE ON agent_runs "
        "FOR EACH ROW EXECUTE FUNCTION agent_runs_enforce_row_rules()"
    )

    # ------------------------------------------------------------------
    # 2.: fold task_messages + context_augmentations into the step
    #    definition, reconcile, drop. The step-immutability trigger from step
    #    E is disabled for this one statement: this is not an edit of an
    #    approved plan, it is moving a part of the definition out of a side
    #    table it should never have lived in.
    # ------------------------------------------------------------------
    if _has_table(bind, "task_messages"):
        legacy_count = _count(bind, "SELECT count(*) FROM task_messages")
        op.execute(
            "ALTER TABLE plan_steps DISABLE TRIGGER plan_steps_frozen_after_approval"
        )
        ca_join = (
            "LEFT JOIN context_augmentations ca ON ca.id = tm.context_augmentation_id"
            if _has_table(bind, "context_augmentations")
            else ""
        )
        ca_object = (
            """jsonb_build_object(
                    'knowledge_summary', ca.knowledge_summary,
                    'literature_findings', ca.literature_findings,
                    'methodology_guidance', ca.methodology_guidance,
                    'terminology', ca.terminology,
                    'sources_guide', ca.sources_guide
               )"""
            if ca_join
            else "'{}'::jsonb"
        )
        op.execute(
            f"""
            UPDATE plan_steps ps
            SET instructions = ps.instructions || jsonb_build_object(
                'task_message', jsonb_build_object(
                    'schema_version', 1,
                    'source', 'legacy_task_messages',
                    'task_id', tm.task_id,
                    'phase', tm.phase,
                    'agent_title', tm.agent_title,
                    'instructions', tm.instructions,
                    'hypothesis_summary', tm.hypothesis_summary,
                    'payload', coalesce(tm.payload, '{{}}'::jsonb),
                    'context_augmentation', {ca_object}
                )
            )
            FROM task_messages tm
            {ca_join}
            WHERE tm.agent_id = ps.step_id
              AND NOT (ps.instructions ? 'task_message')
            """
        )
        op.execute(
            "ALTER TABLE plan_steps ENABLE TRIGGER plan_steps_frozen_after_approval"
        )
        folded = _count(
            bind,
            """
            SELECT count(*) FROM task_messages tm
            JOIN plan_steps ps ON ps.step_id = tm.agent_id
            WHERE ps.instructions->'task_message'->>'task_id' = tm.task_id
            """,
        )
        if folded != legacy_count:
            raise RuntimeError(
                f"{legacy_count - folded} of {legacy_count} task message(s) did not "
                "fold into plan_steps.instructions.task_message. Dropping "
                "task_messages would lose them; reconcile and re-run."
            )
        print(
            f"[v1 step F] folded {folded} task message(s) into "
            "plan_steps.instructions.task_message."
        )
        op.execute("DROP TABLE task_messages")
    if _has_table(bind, "context_augmentations"):
        orphaned = _count(bind, "SELECT count(*) FROM context_augmentations")
        # Every attached augmentation was copied above with its task message;
        # anything left is an orphan (its task message was already gone).
        print(
            f"[v1 step F] dropping context_augmentations ({orphaned} row(s); "
            "attached rows were folded with their task message)."
        )
        op.execute("DROP TABLE context_augmentations")

    # ------------------------------------------------------------------
    # 3. Legacy approved plans. Step E backfilled approved_at from the old
    # `state='confirmed'` string. requires an approved plan to be
    #    non-empty and to own one pending run per step.
    # ------------------------------------------------------------------
    empty_approved = _count(
        bind,
        """
        SELECT count(*) FROM plans p
        WHERE p.approved_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM plan_steps s WHERE s.session_id = p.session_id)
        """,
    )
    if empty_approved:
        # An empty plan can never be approved ( validates a non-empty step
        # list), so the legacy 'confirmed' flag was not an approval. Restore
        # the draft rather than invent steps to satisfy it.
        op.execute("ALTER TABLE plans DISABLE TRIGGER plans_approval_is_one_way")
        op.execute(
            """
            UPDATE plans p SET approved_at = NULL
            WHERE p.approved_at IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM plan_steps s WHERE s.session_id = p.session_id)
            """
        )
        op.execute("ALTER TABLE plans ENABLE TRIGGER plans_approval_is_one_way")
        print(
            f"[v1 step F] {empty_approved} legacy plan(s) were marked confirmed with "
            "no steps; returned to draft (an empty plan cannot be approved)."
        )

    op.execute(
        """
        INSERT INTO agent_runs (step_id, session_id, status)
        SELECT s.step_id, s.session_id, 'pending'
        FROM plan_steps s
        JOIN plans p ON p.session_id = s.session_id
        WHERE p.approved_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.step_id = s.step_id)
        """
    )
    admitted = _count(bind, "SELECT count(*) FROM agent_runs")
    print(
        f"[v1 step F] created {admitted} pending agent run(s) for already-approved "
        "plans; no run was created for a draft and none claims to have executed."
    )

    op.execute(_CARDINALITY_FUNCTION)
    for trigger, table, events in (
        ("plans_run_cardinality", "plans", "INSERT OR UPDATE"),
        ("plan_steps_run_cardinality", "plan_steps", "INSERT OR UPDATE OR DELETE"),
        ("agent_runs_run_cardinality", "agent_runs", "INSERT OR UPDATE OR DELETE"),
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger} AFTER {events} ON {table} "
            "DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION plans_enforce_run_cardinality()"
        )

    # ------------------------------------------------------------------
    # 4. ws_log: global identity cursor.
    # ------------------------------------------------------------------
    if _has_column(bind, "ws_log", "uuid"):
        op.execute("ALTER TABLE ws_log RENAME TO ws_log_pre_f")
        op.execute("ALTER TABLE ws_log_pre_f RENAME CONSTRAINT pk_ws_log TO pk_ws_log_pre_f") if bind.execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname = 'pk_ws_log'")
        ).scalar() else None
        # Existing FK/index names must be freed for the new table.
        for name in bind.execute(
            sa.text(
                """
                SELECT c.conname FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE t.relname = 'ws_log_pre_f' AND c.contype IN ('f', 'p')
                """
            )
        ).scalars().all():
            op.execute(f'ALTER TABLE ws_log_pre_f DROP CONSTRAINT "{name}"')
        for name in bind.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'ws_log_pre_f'")
        ).scalars().all():
            op.execute(f'DROP INDEX IF EXISTS "{name}"')

    op.execute(
        f"""
        CREATE TABLE ws_log (
            event_id bigint GENERATED ALWAYS AS IDENTITY (CACHE 1),
            session_id uuid NOT NULL,
            msg_type text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_ws_log PRIMARY KEY (event_id),
            CONSTRAINT fk_ws_log_session_id_sessions
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE RESTRICT,
            CONSTRAINT ck_ws_log_msg_type_not_blank CHECK (length(btrim(msg_type)) > 0)
        )
        """
    )
    if _has_table(bind, "ws_log_pre_f"):
        missing_type = _count(
            bind,
            "SELECT count(*) FROM ws_log_pre_f WHERE msg_type IS NULL OR btrim(msg_type) = ''",
        )
        missing_time = _count(bind, "SELECT count(*) FROM ws_log_pre_f WHERE created_at IS NULL")
        total = _count(bind, "SELECT count(*) FROM ws_log_pre_f")
        # Renumber in the order the events were recorded. Identity values are
        # allocated in INSERT ... SELECT output order.
        op.execute(
            """
            INSERT INTO ws_log (session_id, msg_type, payload, created_at)
            SELECT session_id,
                   CASE WHEN msg_type IS NULL OR btrim(msg_type) = ''
                        THEN 'legacy_untyped' ELSE msg_type END,
                   coalesce(payload, 'null'::jsonb),
                   coalesce(created_at, now())
            FROM ws_log_pre_f
            ORDER BY created_at NULLS FIRST, uuid
            """
        )
        copied = _count(bind, "SELECT count(*) FROM ws_log")
        if copied != total:
            raise RuntimeError(f"ws_log copy lost rows: {total} -> {copied}")
        op.execute("DROP TABLE ws_log_pre_f")
        print(
            f"[v1 step F] renumbered {total} ws_log event(s) by created_at; "
            f"{missing_type} untyped event(s) labelled 'legacy_untyped', "
            f"{missing_time} event(s) with no timestamp dated at migration time."
        )

    oversized = _count(
        bind,
        f"SELECT count(*) FROM ws_log WHERE pg_column_size(payload) > {WS_LOG_PAYLOAD_MAX_BYTES}",
    )
    validity = " NOT VALID" if oversized else ""
    op.execute(
        "ALTER TABLE ws_log ADD CONSTRAINT ck_ws_log_payload_bounded "
        f"CHECK (pg_column_size(payload) <= {WS_LOG_PAYLOAD_MAX_BYTES}){validity}"
    )
    if oversized:
        print(
            f"[v1 step F] {oversized} legacy ws_log payload(s) exceed the bound; the "
            "CHECK is NOT VALID for existing rows and enforced for every new event."
        )
    op.execute(
        "CREATE INDEX ix_ws_log_session_id_event_id ON ws_log (session_id, event_id)"
    )


def downgrade() -> None:
    bind = op.get_bind()

    # ws_log back to a UUID key. Event ids are discarded; order survives as
    # created_at.
    op.execute("ALTER TABLE ws_log RENAME TO ws_log_f")
    op.execute("ALTER TABLE ws_log_f DROP CONSTRAINT IF EXISTS fk_ws_log_session_id_sessions")
    op.execute("ALTER TABLE ws_log_f DROP CONSTRAINT IF EXISTS pk_ws_log")
    op.execute("DROP INDEX IF EXISTS ix_ws_log_session_id_event_id")
    op.execute(
        """
        CREATE TABLE ws_log (
            uuid uuid NOT NULL DEFAULT gen_random_uuid(),
            session_id uuid NOT NULL,
            msg_type varchar(255),
            payload jsonb,
            created_at timestamptz DEFAULT now(),
            CONSTRAINT pk_ws_log PRIMARY KEY (uuid),
            CONSTRAINT fk_ws_log_session_id_sessions
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        INSERT INTO ws_log (session_id, msg_type, payload, created_at)
        SELECT session_id, msg_type, payload, created_at FROM ws_log_f ORDER BY event_id
        """
    )
    op.execute("DROP TABLE ws_log_f")

    for trigger, table in (
        ("plans_run_cardinality", "plans"),
        ("plan_steps_run_cardinality", "plan_steps"),
        ("agent_runs_run_cardinality", "agent_runs"),
        ("agent_runs_row_rules", "agent_runs"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS plans_enforce_run_cardinality()")
    op.execute("DROP FUNCTION IF EXISTS agent_runs_enforce_row_rules()")

    # Recreate the legacy side tables, restoring the folded task messages.
    op.execute(
        """
        CREATE TABLE context_augmentations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            knowledge_summary text NOT NULL DEFAULT '',
            literature_findings text NOT NULL DEFAULT '',
            methodology_guidance text NOT NULL DEFAULT '',
            terminology text NOT NULL DEFAULT '',
            sources_guide text NOT NULL DEFAULT '',
            created_at timestamptz DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE task_messages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id uuid NOT NULL UNIQUE
                REFERENCES plan_steps (step_id) ON DELETE CASCADE,
            task_id varchar(255) NOT NULL UNIQUE,
            phase varchar(100) NOT NULL DEFAULT '',
            agent_title varchar(255) NOT NULL DEFAULT '',
            instructions text NOT NULL DEFAULT '',
            hypothesis_summary text NOT NULL DEFAULT '',
            payload jsonb DEFAULT '{}'::jsonb,
            context_augmentation_id uuid
                REFERENCES context_augmentations (id) ON DELETE SET NULL,
            created_at timestamptz DEFAULT now()
        )
        """
    )
    op.execute(
        """
        WITH src AS (
            SELECT ps.step_id, ps.instructions->'task_message' AS tm, gen_random_uuid() AS ca_id
            FROM plan_steps ps WHERE ps.instructions ? 'task_message'
        ), ca AS (
            INSERT INTO context_augmentations (
                id, knowledge_summary, literature_findings, methodology_guidance,
                terminology, sources_guide
            )
            SELECT ca_id,
                   coalesce(tm->'context_augmentation'->>'knowledge_summary', ''),
                   coalesce(tm->'context_augmentation'->>'literature_findings', ''),
                   coalesce(tm->'context_augmentation'->>'methodology_guidance', ''),
                   coalesce(tm->'context_augmentation'->>'terminology', ''),
                   coalesce(tm->'context_augmentation'->>'sources_guide', '')
            FROM src
            RETURNING id
        )
        INSERT INTO task_messages (
            agent_id, task_id, phase, agent_title, instructions, hypothesis_summary,
            payload, context_augmentation_id
        )
        SELECT step_id,
               coalesce(tm->>'task_id', gen_random_uuid()::text),
               coalesce(tm->>'phase', ''),
               coalesce(tm->>'agent_title', ''),
               coalesce(tm->>'instructions', ''),
               coalesce(tm->>'hypothesis_summary', ''),
               coalesce(tm->'payload', '{}'::jsonb),
               ca_id
        FROM src
        """
    )
    op.execute("ALTER TABLE plan_steps DISABLE TRIGGER plan_steps_frozen_after_approval")
    op.execute(
        "UPDATE plan_steps SET instructions = instructions - 'task_message' "
        "WHERE instructions ? 'task_message'"
    )
    op.execute("ALTER TABLE plan_steps ENABLE TRIGGER plan_steps_frozen_after_approval")

    # agent_runs cannot be represented before F; execution state is lost.
    op.execute("DROP TABLE IF EXISTS agent_runs")
