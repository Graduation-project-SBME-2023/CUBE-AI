"""current this flow: split chats out of sessions"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "c1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    # 1. chats
    # ------------------------------------------------------------------
    if not _has_table(bind, "chats"):
        op.execute(
            """
            CREATE TABLE chats (
                chat_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id uuid NOT NULL
                    REFERENCES sessions (session_id) ON DELETE RESTRICT,
                title varchar(255),
                created_at timestamptz NOT NULL DEFAULT now(),
                last_activity timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        op.execute("CREATE INDEX ix_chats_session_id ON chats (session_id)")
        op.execute(
            "CREATE INDEX ix_chats_last_activity ON chats (last_activity DESC)"
        )

    # ------------------------------------------------------------------
    # 2. One chat per existing session. The chat gets a NEW id: reusing the
    #    session id as a chat id is precisely the conflation this step ends.
    # ------------------------------------------------------------------
    last_activity_expr = (
        "COALESCE(s.last_activity, s.created_at, now())"
        if _has_column(bind, "sessions", "last_activity")
        else "COALESCE(s.updated_at, s.created_at, now())"
    )
    op.execute(
        f"""
        INSERT INTO chats (session_id, title, created_at, last_activity)
        SELECT s.session_id,
               COALESCE(s.title, 'Chat'),
               COALESCE(s.created_at, now()),
               {last_activity_expr}
        FROM sessions s
        WHERE NOT EXISTS (SELECT 1 FROM chats c WHERE c.session_id = s.session_id)
        """
    )

    # ------------------------------------------------------------------
    # 3. Re-point chat_messages through an explicit session -> chat mapping.
    #    message_id is preserved: it is the agent system's idempotency key.
    # ------------------------------------------------------------------
    if _has_table(bind, "chat_messages"):
        before = bind.execute(
            sa.text("SELECT count(*) FROM chat_messages")
        ).scalar_one()

        for constraint in _foreign_key_names(bind, "chat_messages", "chat_id"):
            op.execute(f'ALTER TABLE chat_messages DROP CONSTRAINT "{constraint}"')

        # chat_messages.chat_id currently holds a SESSION id. Map it to the
        # session's single chat. A message whose session no longer exists
        # cannot be mapped; the migration refuses rather than dropping it.
        unmappable = bind.execute(
            sa.text(
                """
                SELECT count(*) FROM chat_messages m
                WHERE NOT EXISTS (
                    SELECT 1 FROM chats c WHERE c.session_id = m.chat_id
                )
                """
            )
        ).scalar_one()
        if unmappable:
            raise RuntimeError(
                f"{unmappable} chat_messages row(s) reference a session that does "
                "not exist, so they cannot be mapped to a chat. Reconcile them "
                "before re-running; this migration will not delete message history."
            )

        op.execute(
            """
            UPDATE chat_messages m
            SET chat_id = c.chat_id
            FROM chats c
            WHERE c.session_id = m.chat_id
            """
        )
        op.execute(
            "ALTER TABLE chat_messages ADD CONSTRAINT fk_chat_messages_chat_id_chats "
            "FOREIGN KEY (chat_id) REFERENCES chats (chat_id) ON DELETE RESTRICT"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_chat_id_timestamp "
            "ON chat_messages (chat_id, timestamp, message_id)"
        )

        after = bind.execute(sa.text("SELECT count(*) FROM chat_messages")).scalar_one()
        if before != after:
            raise RuntimeError(
                f"chat_messages count changed during re-pointing: {before} -> {after}"
            )
        print(f"[v1 step D] re-pointed {after} chat message(s), message_ids preserved.")

    # ------------------------------------------------------------------
    # 4. last_activity now belongs to the chat.
    # ------------------------------------------------------------------
    if _has_column(bind, "sessions", "last_activity"):
        op.execute("ALTER TABLE sessions DROP COLUMN last_activity")

    # ------------------------------------------------------------------
    # 5. Drop chat_study_association — dead code. Count first.
    # ------------------------------------------------------------------
    if _has_table(bind, "chat_study_association"):
        rows = bind.execute(
            sa.text("SELECT count(*) FROM chat_study_association")
        ).scalar_one()
        print(
            f"[v1 step D] dropping chat_study_association, which held {rows} row(s). "
            "Nothing reads it: the population planner queries volume_series "
            "directly by description."
        )
        op.execute("DROP TABLE chat_study_association")


def downgrade() -> None:
    bind = op.get_bind()

    # The old shape cannot represent more than one chat per session, so a
    # database that has actually used the split cannot be downgraded without
    # losing conversations. Refuse rather than silently merging or dropping.
    if _has_table(bind, "chats"):
        extra = bind.execute(
            sa.text(
                "SELECT count(*) FROM ("
                "  SELECT session_id FROM chats GROUP BY session_id HAVING count(*) > 1"
                ") AS multi"
            )
        ).scalar_one()
        if extra:
            raise RuntimeError(
                f"{extra} session(s) own more than one chat. The pre-v1 shape has "
                "no way to express that, and this downgrade will not merge or "
                "discard conversations. Export them first."
            )

    if not _has_table(bind, "chat_study_association"):
        op.execute(
            """
            CREATE TABLE chat_study_association (
                chat_id uuid NOT NULL
                    REFERENCES sessions (session_id) ON DELETE CASCADE,
                study_uid text NOT NULL
                    REFERENCES studies (study_uid) ON DELETE CASCADE,
                CONSTRAINT pk_chat_study_association PRIMARY KEY (chat_id, study_uid)
            )
            """
        )

    if not _has_column(bind, "sessions", "last_activity"):
        op.execute("ALTER TABLE sessions ADD COLUMN last_activity timestamptz")
        op.execute(
            """
            UPDATE sessions s SET last_activity = c.last_activity
            FROM chats c WHERE c.session_id = s.session_id
            """
        )

    if _has_table(bind, "chat_messages"):
        op.execute("DROP INDEX IF EXISTS ix_chat_messages_chat_id_timestamp")
        for constraint in _foreign_key_names(bind, "chat_messages", "chat_id"):
            op.execute(f'ALTER TABLE chat_messages DROP CONSTRAINT "{constraint}"')
        op.execute(
            """
            UPDATE chat_messages m
            SET chat_id = c.session_id
            FROM chats c
            WHERE c.chat_id = m.chat_id
            """
        )
        op.execute(
            "ALTER TABLE chat_messages ADD CONSTRAINT fk_chat_messages_chat_id_sessions "
            "FOREIGN KEY (chat_id) REFERENCES sessions (session_id) ON DELETE CASCADE"
        )

    op.execute("DROP TABLE IF EXISTS chats")
