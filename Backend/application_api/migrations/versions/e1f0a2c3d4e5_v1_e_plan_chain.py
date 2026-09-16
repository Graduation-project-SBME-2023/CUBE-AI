"""current this flow: plan chain keyed by session, and drop hypotheses"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "d1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# /: a committed session must have exactly one plan. A deferred
# constraint trigger is the only way to express "exactly one" when the two rows
# are inserted in the same transaction — a NOT NULL FK on the session would
# make the pair uninsertable in either order.
_PLAN_EXISTS_TRIGGER = """
CREATE OR REPLACE FUNCTION sessions_require_a_plan()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM plans WHERE session_id = NEW.session_id) THEN
        RAISE EXCEPTION
            'session % was committed with no plan; create the session and its '
            'empty plan in one transaction', NEW.session_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NULL;
END;
$$;
"""

# The mirror rule: a plan may not be deleted independently of its session.
_PLAN_DELETE_TRIGGER = """
CREATE OR REPLACE FUNCTION plans_reject_orphan_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM sessions WHERE session_id = OLD.session_id) THEN
        RAISE EXCEPTION
            'plan % cannot be deleted while its session exists', OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$;
"""

# After approval the plan and every step are immutable.
_STEP_IMMUTABLE_TRIGGER = """
CREATE OR REPLACE FUNCTION plan_steps_reject_approved_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
    approved timestamptz;
BEGIN
    target := COALESCE(NEW.session_id, OLD.session_id);
    SELECT approved_at INTO approved FROM plans WHERE session_id = target;
    IF approved IS NOT NULL THEN
        RAISE EXCEPTION
            'plan for session % was approved at %; its steps are immutable',
            target, approved
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;
"""

_PLAN_APPROVAL_ONE_WAY_TRIGGER = """
CREATE OR REPLACE FUNCTION plans_reject_unapproval()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.approved_at IS NOT NULL
       AND NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
        RAISE EXCEPTION
            'plan approval is one-way (session %)', OLD.session_id
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


def _has_constraint(bind, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}
        ).scalar()
    )


def _drop_all_constraints(bind, table: str, contype: str) -> None:
    names = bind.execute(
        sa.text(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class src ON src.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = src.relnamespace
            WHERE c.contype = :ct AND n.nspname = 'public' AND src.relname = :t
            """
        ),
        {"ct": contype, "t": table},
    ).scalars().all()
    for name in names:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Copy the PICO fields into sessions.intent, and VERIFY row-for-row
    #    before anything is dropped.
    # ------------------------------------------------------------------
    if _has_table(bind, "hypotheses"):
        op.execute(
            """
            UPDATE sessions s
            SET intent = jsonb_strip_nulls(jsonb_build_object(
                'schema_version', 1,
                'source', 'legacy_hypotheses',
                'title', h.title,
                'population', h.population,
                'intervention', h.intervention,
                'comparison', h.comparison,
                'outcome', h.outcome,
                'clinical_context', h.clinical_context,
                'relationship_type', h.relationship_type
            ))
            FROM hypotheses h
            WHERE h.chat_id = s.session_id AND s.intent IS NULL
            """
        )
        # jsonb_strip_nulls would also drop a field that was legitimately
        # empty, so verify presence field by field against the source.
        missing = bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM hypotheses h
                JOIN sessions s ON s.session_id = h.chat_id
                WHERE (h.title IS NOT NULL AND s.intent->>'title' IS DISTINCT FROM h.title)
                   OR (h.population IS NOT NULL
                       AND s.intent->>'population' IS DISTINCT FROM h.population)
                   OR (h.intervention IS NOT NULL
                       AND s.intent->>'intervention' IS DISTINCT FROM h.intervention)
                   OR (h.comparison IS NOT NULL
                       AND s.intent->>'comparison' IS DISTINCT FROM h.comparison)
                   OR (h.outcome IS NOT NULL
                       AND s.intent->>'outcome' IS DISTINCT FROM h.outcome)
                   OR (h.clinical_context IS NOT NULL
                       AND s.intent->>'clinical_context' IS DISTINCT FROM h.clinical_context)
                   OR (h.relationship_type IS NOT NULL
                       AND s.intent->>'relationship_type' IS DISTINCT FROM h.relationship_type)
                """
            )
        ).scalar_one()
        if missing:
            raise RuntimeError(
                f"{missing} hypothesis row(s) did not copy cleanly into "
                "sessions.intent. Dropping `hypotheses` would lose them; "
                "reconcile and re-run."
            )
        total = bind.execute(sa.text("SELECT count(*) FROM hypotheses")).scalar_one()
        print(f"[v1 step E] verified {total} hypothesis row(s) into sessions.intent.")

    # ------------------------------------------------------------------
    # 2. plans: session_id becomes both PK and FK.
    # ------------------------------------------------------------------
    if not _has_column(bind, "plans", "session_id"):
        op.execute("ALTER TABLE plans ADD COLUMN session_id uuid")
        if _has_table(bind, "hypotheses") and _has_column(bind, "plans", "hypothesis_id"):
            op.execute(
                """
                UPDATE plans p SET session_id = h.chat_id
                FROM hypotheses h WHERE h.id = p.hypothesis_id
                """
            )
        orphans = bind.execute(
            sa.text("SELECT count(*) FROM plans WHERE session_id IS NULL")
        ).scalar_one()
        if orphans:
            raise RuntimeError(
                f"{orphans} plan(s) could not be attached to a session through "
                "hypotheses.chat_id. A plan with no session cannot exist in v1; "
                "reconcile them before re-running."
            )
        # One plan per session is the rule; a duplicate cannot become the PK.
        duplicates = bind.execute(
            sa.text(
                "SELECT count(*) FROM ("
                "  SELECT session_id FROM plans GROUP BY session_id HAVING count(*) > 1"
                ") AS d"
            )
        ).scalar_one()
        if duplicates:
            raise RuntimeError(
                f"{duplicates} session(s) have more than one plan. V1 allows "
                "exactly one; reconcile before re-running."
            )

    if not _has_column(bind, "plans", "approved_at"):
        op.execute("ALTER TABLE plans ADD COLUMN approved_at timestamptz")
        if _has_column(bind, "plans", "state"):
            # The old `state` string is the only evidence of approval that
            # exists. `updated_at`/`created_at` is the closest honest instant;
            # nothing invents a more precise one.
            op.execute(
                """
                UPDATE plans SET approved_at = COALESCE(created_at, now())
                WHERE lower(coalesce(state, '')) = 'confirmed'
                """
            )
    if not _has_column(bind, "plans", "updated_at"):
        op.execute(
            "ALTER TABLE plans ADD COLUMN updated_at timestamptz "
            "NOT NULL DEFAULT now()"
        )

    # ------------------------------------------------------------------
    # 3. plan_steps: re-point at plans(session_id) and reshape.
    # ------------------------------------------------------------------
    if not _has_column(bind, "plan_steps", "session_id"):
        op.execute("ALTER TABLE plan_steps ADD COLUMN session_id uuid")
        op.execute(
            """
            UPDATE plan_steps ps SET session_id = p.session_id
            FROM plans p WHERE p.id = ps.plan_id
            """
        )
        orphan_steps = bind.execute(
            sa.text("SELECT count(*) FROM plan_steps WHERE session_id IS NULL")
        ).scalar_one()
        if orphan_steps:
            raise RuntimeError(
                f"{orphan_steps} plan step(s) belong to no plan. Reconcile "
                "before re-running."
            )
        op.execute("ALTER TABLE plan_steps ALTER COLUMN session_id SET NOT NULL")

    # Fold description into instructions BEFORE dropping it.
    if _has_column(bind, "plan_steps", "description"):
        op.execute("UPDATE plan_steps SET instructions = '{}'::jsonb WHERE instructions IS NULL")
        op.execute(
            """
            UPDATE plan_steps
            SET instructions = instructions || jsonb_build_object('description', description)
            WHERE description IS NOT NULL AND btrim(description) <> ''
              AND NOT (instructions ? 'description')
            """
        )

    if _has_column(bind, "plan_steps", "module") and not _has_column(
        bind, "plan_steps", "agent_type"
    ):
        op.execute("ALTER TABLE plan_steps RENAME COLUMN module TO agent_type")
    # agent_type is the sole selector for which agent runs a step, so it may
    # not be NULL. A legacy step with no module cannot be executed; it is
    # recorded as such rather than silently defaulted to some agent.
    op.execute(
        "UPDATE plan_steps SET agent_type = 'unknown' "
        "WHERE agent_type IS NULL OR btrim(agent_type) = ''"
    )
    op.execute("ALTER TABLE plan_steps ALTER COLUMN agent_type SET NOT NULL")
    op.execute("ALTER TABLE plan_steps ALTER COLUMN instructions SET NOT NULL")

    # id -> step_id. The PK and every FK follow the rename.
    if _has_column(bind, "plan_steps", "id") and not _has_column(
        bind, "plan_steps", "step_id"
    ):
        op.execute("ALTER TABLE plan_steps RENAME COLUMN id TO step_id")

    # Drop the old plan FK and the columns / removed.
    _drop_all_constraints(bind, "plan_steps", "f")
    for column in ("plan_id", "description", "status", "logs", "created_at"):
        if _has_column(bind, "plan_steps", column):
            op.execute(f"ALTER TABLE plan_steps DROP COLUMN {column}")

    # ------------------------------------------------------------------
    # 4. Now that nothing references plans.id, make session_id the PK.
    # ------------------------------------------------------------------
    _drop_all_constraints(bind, "plans", "f")
    _drop_all_constraints(bind, "plans", "u")
    op.execute("ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_pkey")
    op.execute("ALTER TABLE plans DROP CONSTRAINT IF EXISTS pk_plans")
    for column in ("id", "hypothesis_id", "study_goal", "state", "rationale", "created_at"):
        if _has_column(bind, "plans", column):
            op.execute(f"ALTER TABLE plans DROP COLUMN {column}")
    op.execute("ALTER TABLE plans ALTER COLUMN session_id SET NOT NULL")
    op.execute("ALTER TABLE plans ADD CONSTRAINT pk_plans PRIMARY KEY (session_id)")
    op.execute(
        "ALTER TABLE plans ADD CONSTRAINT fk_plans_session_id_sessions "
        "FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE RESTRICT"
    )

    # ------------------------------------------------------------------
    # 5. plan_steps constraints.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE plan_steps ADD CONSTRAINT fk_plan_steps_session_id_plans "
        "FOREIGN KEY (session_id) REFERENCES plans (session_id) ON DELETE RESTRICT"
    )
    if not _has_constraint(bind, "ck_plan_steps_step_order_positive"):
        op.execute(
            "ALTER TABLE plan_steps ADD CONSTRAINT ck_plan_steps_step_order_positive "
            "CHECK (step_order > 0)"
        )
    if not _has_constraint(bind, "uq_plan_steps_session_id"):
        # DEFERRABLE so a reorder can swap two step_orders inside one
        # transaction without an intermediate collision.
        op.execute(
            "ALTER TABLE plan_steps ADD CONSTRAINT uq_plan_steps_session_id "
            "UNIQUE (session_id, step_order) DEFERRABLE INITIALLY IMMEDIATE"
        )
    if not _has_constraint(bind, "uq_plan_steps_step_id"):
        # Free (step_id is the PK) and required as the target of agent_runs'
        # composite (step_id, session_id) FK in this flow.
        op.execute(
            "ALTER TABLE plan_steps ADD CONSTRAINT uq_plan_steps_step_id "
            "UNIQUE (step_id, session_id)"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_plan_steps_session_id_step_order "
        "ON plan_steps (session_id, step_order)"
    )

    # ------------------------------------------------------------------
    # 6. The invariants that make "exactly one plan per session" real.
    # ------------------------------------------------------------------
    op.execute(_PLAN_EXISTS_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS sessions_must_have_a_plan ON sessions")
    op.execute(
        "CREATE CONSTRAINT TRIGGER sessions_must_have_a_plan "
        "AFTER INSERT ON sessions DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION sessions_require_a_plan()"
    )

    op.execute(_PLAN_DELETE_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS plans_not_deletable_alone ON plans")
    op.execute(
        "CREATE TRIGGER plans_not_deletable_alone BEFORE DELETE ON plans "
        "FOR EACH ROW EXECUTE FUNCTION plans_reject_orphan_delete()"
    )

    op.execute(_PLAN_APPROVAL_ONE_WAY_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS plans_approval_is_one_way ON plans")
    op.execute(
        "CREATE TRIGGER plans_approval_is_one_way BEFORE UPDATE ON plans "
        "FOR EACH ROW EXECUTE FUNCTION plans_reject_unapproval()"
    )

    op.execute(_STEP_IMMUTABLE_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS plan_steps_frozen_after_approval ON plan_steps")
    op.execute(
        "CREATE TRIGGER plan_steps_frozen_after_approval "
        "BEFORE INSERT OR UPDATE OR DELETE ON plan_steps "
        "FOR EACH ROW EXECUTE FUNCTION plan_steps_reject_approved_mutation()"
    )

    # ------------------------------------------------------------------
    # 7. Every session created before current needs the plan row the invariant now
    #    requires. This is a backfill, not an approval: approved_at stays NULL.
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO plans (session_id, approved_at, updated_at)
        SELECT s.session_id, NULL, now()
        FROM sessions s
        WHERE NOT EXISTS (SELECT 1 FROM plans p WHERE p.session_id = s.session_id)
        """
    )

    # ------------------------------------------------------------------
    # 8. hypotheses is gone.
    # ------------------------------------------------------------------
    if _has_table(bind, "hypotheses"):
        op.execute("DROP TABLE hypotheses")


def downgrade() -> None:
    bind = op.get_bind()

    for trigger, table in (
        ("plan_steps_frozen_after_approval", "plan_steps"),
        ("plans_approval_is_one_way", "plans"),
        ("plans_not_deletable_alone", "plans"),
        ("sessions_must_have_a_plan", "sessions"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in (
        "plan_steps_reject_approved_mutation()",
        "plans_reject_unapproval()",
        "plans_reject_orphan_delete()",
        "sessions_require_a_plan()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")

    # hypotheses is recreated as a table, seeded from sessions.intent. The
    # per-row identity (hypotheses.id) is NOT recoverable — this is a schema
    # rollback, not a data rollback, and anything that stored a hypothesis id
    # externally will not resolve.
    if not _has_table(bind, "hypotheses"):
        op.execute(
            """
            CREATE TABLE hypotheses (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                chat_id uuid NOT NULL UNIQUE
                    REFERENCES sessions (session_id) ON DELETE CASCADE,
                title varchar(255) NOT NULL,
                population text,
                intervention text,
                hypothesis text,
                comparison text,
                outcome text,
                clinical_context text,
                relationship_type varchar(50),
                state varchar(50) DEFAULT 'not_confirmed',
                created_at timestamptz DEFAULT now()
            )
            """
        )
        op.execute(
            """
            INSERT INTO hypotheses (
                chat_id, title, population, intervention, comparison, outcome,
                clinical_context, relationship_type, hypothesis, created_at
            )
            SELECT s.session_id,
                   COALESCE(s.intent->>'title', s.title, 'Untitled Hypothesis'),
                   s.intent->>'population',
                   s.intent->>'intervention',
                   s.intent->>'comparison',
                   s.intent->>'outcome',
                   s.intent->>'clinical_context',
                   s.intent->>'relationship_type',
                   s.prompt,
                   s.created_at
            FROM sessions s
            WHERE s.intent IS NOT NULL
            """
        )

    op.execute("DROP INDEX IF EXISTS ix_plan_steps_session_id_step_order")
    _drop_all_constraints(bind, "plan_steps", "f")
    _drop_all_constraints(bind, "plan_steps", "u")
    for constraint in ("ck_plan_steps_step_order_positive",):
        if _has_constraint(bind, constraint):
            op.execute(f"ALTER TABLE plan_steps DROP CONSTRAINT {constraint}")

    _drop_all_constraints(bind, "plans", "f")
    op.execute("ALTER TABLE plans DROP CONSTRAINT IF EXISTS pk_plans")
    op.execute(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid()"
    )
    op.execute("UPDATE plans SET id = gen_random_uuid() WHERE id IS NULL")
    op.execute("ALTER TABLE plans ADD CONSTRAINT plans_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS hypothesis_id uuid")
    op.execute(
        """
        UPDATE plans p SET hypothesis_id = h.id
        FROM hypotheses h WHERE h.chat_id = p.session_id
        """
    )
    op.execute("DELETE FROM plan_steps WHERE session_id IN (SELECT session_id FROM plans WHERE hypothesis_id IS NULL)")
    op.execute("DELETE FROM plans WHERE hypothesis_id IS NULL")
    op.execute("ALTER TABLE plans ALTER COLUMN hypothesis_id SET NOT NULL")
    op.execute(
        "ALTER TABLE plans ADD CONSTRAINT uq_plans_hypothesis_id UNIQUE (hypothesis_id)"
    )
    op.execute(
        "ALTER TABLE plans ADD CONSTRAINT plans_hypothesis_id_fkey "
        "FOREIGN KEY (hypothesis_id) REFERENCES hypotheses (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS study_goal varchar(255) "
        "NOT NULL DEFAULT ''"
    )
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS state varchar(50)")
    op.execute(
        "UPDATE plans SET state = CASE WHEN approved_at IS NULL "
        "THEN 'not_confirmed' ELSE 'confirmed' END"
    )
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS rationale varchar")
    op.execute(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now()"
    )
    for column in ("approved_at", "updated_at"):
        if _has_column(bind, "plans", column):
            op.execute(f"ALTER TABLE plans DROP COLUMN {column}")

    op.execute("ALTER TABLE plan_steps ADD COLUMN IF NOT EXISTS plan_id uuid")
    op.execute(
        """
        UPDATE plan_steps ps SET plan_id = p.id
        FROM plans p WHERE p.session_id = ps.session_id
        """
    )
    op.execute("DELETE FROM plan_steps WHERE plan_id IS NULL")
    op.execute("ALTER TABLE plan_steps ALTER COLUMN plan_id SET NOT NULL")
    op.execute(
        "ALTER TABLE plan_steps ADD CONSTRAINT plan_steps_plan_id_fkey "
        "FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE plan_steps DROP COLUMN IF EXISTS session_id")
    op.execute(
        "ALTER TABLE plan_steps ADD COLUMN IF NOT EXISTS description text"
    )
    op.execute(
        "UPDATE plan_steps SET description = instructions->>'description' "
        "WHERE instructions ? 'description'"
    )
    op.execute(
        "ALTER TABLE plan_steps ADD COLUMN IF NOT EXISTS status varchar(50) "
        "DEFAULT 'pending'"
    )
    op.execute(
        "ALTER TABLE plan_steps ADD COLUMN IF NOT EXISTS logs jsonb DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE plan_steps ADD COLUMN IF NOT EXISTS created_at timestamptz "
        "DEFAULT now()"
    )
    if _has_column(bind, "plan_steps", "agent_type"):
        op.execute("ALTER TABLE plan_steps RENAME COLUMN agent_type TO module")
    if _has_column(bind, "plan_steps", "step_id"):
        op.execute("ALTER TABLE plan_steps RENAME COLUMN step_id TO id")
