"""current this flow: session spine — chat_sessions -> sessions, and the raw prompt"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "b1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SESSION_STATUSES = (
    "planning",
    "ready",
    "running",
    "pausing",
    "paused",
    "completed",
    "failed",
    "archived",
)

_PROMPT_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION sessions_reject_prompt_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.prompt IS NOT NULL AND NEW.prompt IS DISTINCT FROM OLD.prompt THEN
        RAISE EXCEPTION
            'sessions.prompt is immutable once written (session_id=%)', OLD.session_id
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


def _foreign_key_names(bind, table: str, column: str) -> list[str]:
    return list(
        bind.execute(
            sa.text(
                """
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class src ON src.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = src.relnamespace
                JOIN LATERAL unnest(c.conkey) AS k(attnum) ON true
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.contype = 'f'
                  AND n.nspname = 'public'
                  AND src.relname = :t
                  AND array_length(c.conkey, 1) = 1
                  AND a.attname = :c
                """
            ),
            {"t": table, "c": column},
        ).scalars().all()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Rename the table and its identity column.
    #
    # PostgreSQL carries foreign keys, indexes and check constraints through
    # a table or column rename, so chat_messages, hypotheses, ws_log and
    # chat_study_association keep pointing at the right place; only the
    # constraint *names* stay stale, and those are renamed below for legibility.
    # ------------------------------------------------------------------
    if _has_table(bind, "chat_sessions") and not _has_table(bind, "sessions"):
        op.execute("ALTER TABLE chat_sessions RENAME TO sessions")
    if _has_column(bind, "sessions", "chat_id"):
        op.execute("ALTER TABLE sessions RENAME COLUMN chat_id TO session_id")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chat_sessions_pkey') THEN
                ALTER TABLE sessions RENAME CONSTRAINT chat_sessions_pkey TO pk_sessions;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pk_chat_sessions') THEN
                ALTER TABLE sessions RENAME CONSTRAINT pk_chat_sessions TO pk_sessions;
            END IF;
            -- The initial schema's CHECK (last_activity >= started_at) is
            -- dropped: started_at becomes created_at and last_activity moves
            -- to `chats` in step D, so the pair it constrained stops existing.
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chat_sessions_check') THEN
                ALTER TABLE sessions DROP CONSTRAINT chat_sessions_check;
            END IF;
        END $$;
        """
    )

    # ------------------------------------------------------------------
    # 1b. Re-point ws_log at the renamed table with RESTRICT.
    #
    # A table rename carries its foreign keys AND their actions, so ws_log kept
    # the ON DELETE CASCADE the initial schema gave it. Under current a session is
    # removed by archiving it; a hard delete that silently took the whole
    # event log with it is exactly what RESTRICT is here to stop.
    # ------------------------------------------------------------------
    for constraint in _foreign_key_names(bind, "ws_log", "session_id"):
        op.execute(f'ALTER TABLE ws_log DROP CONSTRAINT "{constraint}"')
    op.execute(
        "ALTER TABLE ws_log ADD CONSTRAINT fk_ws_log_session_id_sessions "
        "FOREIGN KEY (session_id) REFERENCES sessions (session_id) "
        "ON DELETE RESTRICT"
    )

    # ------------------------------------------------------------------
    # 2. Timestamps: started_at -> created_at, plus a real updated_at.
    # ------------------------------------------------------------------
    if _has_column(bind, "sessions", "started_at") and not _has_column(
        bind, "sessions", "created_at"
    ):
        op.execute("ALTER TABLE sessions RENAME COLUMN started_at TO created_at")
    op.execute(
        "ALTER TABLE sessions ALTER COLUMN created_at SET DEFAULT now()"
    )
    op.execute("UPDATE sessions SET created_at = now() WHERE created_at IS NULL")
    op.execute("ALTER TABLE sessions ALTER COLUMN created_at SET NOT NULL")

    if not _has_column(bind, "sessions", "updated_at"):
        op.execute(
            "ALTER TABLE sessions ADD COLUMN updated_at timestamptz "
            "NOT NULL DEFAULT now()"
        )
        # last_activity is the closest honest record of the last mutation.
        # It is not dropped here: this flow moves it onto the `chats` row that
        # owns it, and dropping it now would destroy that value.
        if _has_column(bind, "sessions", "last_activity"):
            op.execute(
                "UPDATE sessions SET updated_at = "
                "COALESCE(last_activity, created_at, now())"
            )

    # ------------------------------------------------------------------
    # 3. The new spine columns.
    # ------------------------------------------------------------------
    if not _has_column(bind, "sessions", "prompt"):
        op.execute("ALTER TABLE sessions ADD COLUMN prompt text")
    if not _has_column(bind, "sessions", "prompt_kind"):
        op.execute(
            "ALTER TABLE sessions ADD COLUMN prompt_kind text "
            "NOT NULL DEFAULT 'hypothesis'"
        )
    if not _has_column(bind, "sessions", "intent"):
        op.execute("ALTER TABLE sessions ADD COLUMN intent jsonb")
    if not _has_column(bind, "sessions", "status"):
        op.execute(
            "ALTER TABLE sessions ADD COLUMN status text NOT NULL DEFAULT 'planning'"
        )
    if not _has_column(bind, "sessions", "error"):
        op.execute("ALTER TABLE sessions ADD COLUMN error jsonb")

    # ------------------------------------------------------------------
    # 4. Backfill the prompt. Order matters: the earliest user message is
    #    the ORIGINAL typed text; hypotheses.hypothesis may already be the
    #    planner's rewrite of it, so it is only the fallback.
    # ------------------------------------------------------------------
    if _has_table(bind, "chat_messages"):
        op.execute(
            """
            UPDATE sessions s
            SET prompt = first_message.text
            FROM (
                SELECT DISTINCT ON (chat_id) chat_id, text
                FROM chat_messages
                WHERE role = 'user' AND btrim(coalesce(text, '')) <> ''
                ORDER BY chat_id, timestamp, message_id
            ) AS first_message
            WHERE first_message.chat_id = s.session_id AND s.prompt IS NULL
            """
        )

    if _has_table(bind, "hypotheses"):
        op.execute(
            """
            UPDATE sessions s
            SET prompt = h.hypothesis
            FROM hypotheses h
            WHERE h.chat_id = s.session_id
              AND s.prompt IS NULL
              AND btrim(coalesce(h.hypothesis, '')) <> ''
            """
        )
        # A session that has PICO structure is a clinical hypothesis; one that
        # does not is a general question. Nothing else distinguishes them, and
        # guessing from the prompt text would be a fabricated classification.
        op.execute(
            """
            UPDATE sessions s
            SET prompt_kind = 'general'
            WHERE NOT EXISTS (
                SELECT 1 FROM hypotheses h
                WHERE h.chat_id = s.session_id
                  AND (
                      btrim(coalesce(h.population, '')) <> ''
                      OR btrim(coalesce(h.intervention, '')) <> ''
                      OR btrim(coalesce(h.outcome, '')) <> ''
                  )
            )
            """
        )

    # ------------------------------------------------------------------
    # 5. Backfill status from evidence that actually exists, then mark the
    #    unrecoverable rows.
    # ------------------------------------------------------------------
    if _has_table(bind, "plans") and _has_table(bind, "plan_steps"):
        op.execute(
            """
            WITH step_state AS (
                SELECT h.chat_id AS session_id,
                       count(*) AS total,
                       count(*) FILTER (WHERE ps.status = 'completed') AS completed,
                       count(*) FILTER (WHERE ps.status = 'failed') AS failed
                FROM plan_steps ps
                JOIN plans p ON p.id = ps.plan_id
                JOIN hypotheses h ON h.id = p.hypothesis_id
                GROUP BY h.chat_id
            )
            UPDATE sessions s
            SET status = CASE
                WHEN step_state.failed > 0 THEN 'failed'
                WHEN step_state.completed = step_state.total THEN 'completed'
                ELSE 'ready'
            END
            FROM step_state
            WHERE step_state.session_id = s.session_id
            """
        )

    if _has_column(bind, "sessions", "is_active"):
        # An explicitly deactivated session is archived; nothing else about
        # is_active carries over.
        op.execute(
            "UPDATE sessions SET status = 'archived' WHERE is_active IS FALSE"
        )

    # Report the count, never invent the text.
    unrecoverable = bind.execute(
        sa.text("SELECT count(*) FROM sessions WHERE prompt IS NULL")
    ).scalar_one()
    if unrecoverable:
        op.execute(
            """
            UPDATE sessions
            SET status = 'archived',
                error = jsonb_build_object(
                    'code', 'legacy_prompt_unavailable',
                    'message',
                    'The original prompt was destroyed before v1 added a raw '
                    'prompt column: the planner overwrote hypotheses.hypothesis '
                    'and no original user message survives. Nothing was invented '
                    'to fill it.'
                )
            WHERE prompt IS NULL
            """
        )
    print(
        f"[v1 step C] prompt backfill: {unrecoverable} session(s) had no "
        "recoverable prompt and were archived with "
        "error.code='legacy_prompt_unavailable'."
    )

    # ------------------------------------------------------------------
    # 6. Constraints — including the one that makes a NULL prompt possible
    #    only for an archived legacy row.
    # ------------------------------------------------------------------
    statuses = ", ".join(f"'{s}'" for s in SESSION_STATUSES)
    if not _has_constraint(bind, "ck_sessions_status"):
        op.execute(
            f"ALTER TABLE sessions ADD CONSTRAINT ck_sessions_status "
            f"CHECK (status IN ({statuses}))"
        )
    if not _has_constraint(bind, "ck_sessions_prompt_kind"):
        op.execute(
            "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_prompt_kind "
            "CHECK (prompt_kind IN ('hypothesis', 'general'))"
        )
    if not _has_constraint(bind, "ck_sessions_prompt_present"):
        op.execute(
            "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_prompt_present "
            "CHECK (prompt IS NOT NULL OR status = 'archived')"
        )
    if not _has_constraint(bind, "ck_sessions_prompt_not_blank"):
        op.execute(
            "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_prompt_not_blank "
            "CHECK (prompt IS NULL OR btrim(prompt) <> '')"
        )

    # prompt is immutable once written. A convention is what failed here, so
    # this is enforced by the database.
    op.execute(_PROMPT_IMMUTABLE_FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS sessions_prompt_is_immutable ON sessions")
    op.execute(
        "CREATE TRIGGER sessions_prompt_is_immutable "
        "BEFORE UPDATE ON sessions FOR EACH ROW "
        "EXECUTE FUNCTION sessions_reject_prompt_mutation()"
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_sessions_status ON sessions (status)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sessions_updated_at ON sessions (updated_at DESC)"
    )

    # ------------------------------------------------------------------
    # 7. — columns with no current reader.
    # ------------------------------------------------------------------
    # Dropping is_active would drop these implicitly; naming them keeps the
    # intent visible and the downgrade symmetric.
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_active_last_activity")
    op.execute("DROP INDEX IF EXISTS idx_chat_study_active")
    for column in ("is_active", "summary"):
        if _has_column(bind, "sessions", column):
            op.execute(f"ALTER TABLE sessions DROP COLUMN {column}")


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("DROP TRIGGER IF EXISTS sessions_prompt_is_immutable ON sessions")
    op.execute("DROP FUNCTION IF EXISTS sessions_reject_prompt_mutation()")
    op.execute("DROP INDEX IF EXISTS ix_sessions_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_sessions_status")

    for constraint in (
        "ck_sessions_prompt_not_blank",
        "ck_sessions_prompt_present",
        "ck_sessions_prompt_kind",
        "ck_sessions_status",
    ):
        if _has_constraint(bind, constraint):
            op.execute(f"ALTER TABLE sessions DROP CONSTRAINT {constraint}")

    if not _has_column(bind, "sessions", "is_active"):
        op.execute(
            "ALTER TABLE sessions ADD COLUMN is_active boolean DEFAULT true"
        )
        op.execute(
            "UPDATE sessions SET is_active = (status <> 'archived')"
        )
    if not _has_column(bind, "sessions", "summary"):
        # The original summary text is not recoverable; this restores the
        # column, not the data.
        op.execute("ALTER TABLE sessions ADD COLUMN summary text")

    for column in ("error", "status", "intent", "prompt_kind", "prompt", "updated_at"):
        if _has_column(bind, "sessions", column):
            op.execute(f"ALTER TABLE sessions DROP COLUMN {column}")

    if _has_column(bind, "sessions", "created_at"):
        op.execute("ALTER TABLE sessions RENAME COLUMN created_at TO started_at")
    if _has_column(bind, "sessions", "session_id"):
        op.execute("ALTER TABLE sessions RENAME COLUMN session_id TO chat_id")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pk_sessions') THEN
                ALTER TABLE sessions RENAME CONSTRAINT pk_sessions TO chat_sessions_pkey;
            END IF;
        END $$;
        """
    )
    for constraint in _foreign_key_names(bind, "ws_log", "session_id"):
        op.execute(f'ALTER TABLE ws_log DROP CONSTRAINT "{constraint}"')
    if _has_table(bind, "sessions") and not _has_table(bind, "chat_sessions"):
        op.execute("ALTER TABLE sessions RENAME TO chat_sessions")
    op.execute(
        "ALTER TABLE ws_log ADD CONSTRAINT ws_log_session_id_fkey "
        "FOREIGN KEY (session_id) REFERENCES chat_sessions (chat_id) "
        "ON DELETE CASCADE"
    )
