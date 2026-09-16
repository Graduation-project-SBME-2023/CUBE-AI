"""current this flow: artifacts — retained bytes in PostgreSQL, renders on disk"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "f1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors application_api/core/artifact_limits.py. sets the
# initial caps at 32 MiB for PNG/JPEG and on-disk GIF renders and 64 MiB for
# retained CSV; the column-level bound is the larger of the two, and the
# per-kind caps are enforced before insert where the mime type is known.
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
ARTIFACT_METADATA_MAX_BYTES = 1024 * 1024


_ARTIFACT_IMMUTABLE_TRIGGER = """
CREATE OR REPLACE FUNCTION artifacts_enforce_immutable_bytes()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.artifact_id IS DISTINCT FROM OLD.artifact_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.logical_key IS DISTINCT FROM OLD.logical_key
       OR NEW.retention_class IS DISTINCT FROM OLD.retention_class THEN
        RAISE EXCEPTION
            'artifact % identity is immutable', OLD.artifact_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.retention_class = 'user_retained' AND (
           NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
        OR NEW.body IS DISTINCT FROM OLD.body
    ) THEN
        RAISE EXCEPTION
            'artifact % holds retained bytes; they are immutable. Publish a new '
            'logical_key instead of replacing them', OLD.artifact_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- A13: a system_render row catalogues a REGENERABLE render. Its digest is
    -- the last-seen content, not a promise -- editing a mask legitimately
    -- produces a new GIF under the same key. The render PATH is still fixed,
    -- so the row cannot be re-pointed at some other file.
    IF OLD.retention_class = 'system_render'
       AND NEW.render_path IS DISTINCT FROM OLD.render_path THEN
        RAISE EXCEPTION
            'artifact % render_path is immutable; catalogue a new key for a '
            'different render', OLD.artifact_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE artifacts (
            artifact_id uuid NOT NULL DEFAULT gen_random_uuid(),
            session_id uuid NOT NULL,
            created_by_agent_run_id uuid,
            kind text NOT NULL,
            logical_key text NOT NULL,
            display_filename text NOT NULL,
            mime_type text NOT NULL,
            byte_size bigint NOT NULL,
            sha256 text NOT NULL,
            body bytea,
            render_path text,
            metadata jsonb,
            schema_version integer NOT NULL DEFAULT 1,
            retention_class text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_artifacts PRIMARY KEY (artifact_id),
            CONSTRAINT uq_artifacts_session_id UNIQUE (session_id, logical_key),
            CONSTRAINT fk_artifacts_session_id_sessions
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE RESTRICT,
            CONSTRAINT fk_artifacts_created_by_agent_run_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            -- R1: when the run link is present it must be a run of THIS
            -- session. A NULL run link (planning-phase literature artifacts,
            -- A12) skips the check, which is exactly the intended semantics.
            CONSTRAINT fk_artifacts_created_by_agent_run_id_session_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id, session_id)
                REFERENCES agent_runs (agent_run_id, session_id) ON DELETE RESTRICT,
            CONSTRAINT ck_artifacts_kind CHECK (kind IN (
                'figure_png', 'figure_jpeg', 'figure_gif', 'export_csv', 'input_csv'
            )),
            CONSTRAINT ck_artifacts_retention_class
                CHECK (retention_class IN ('user_retained', 'system_render')),
            CONSTRAINT ck_artifacts_logical_key_not_blank
                CHECK (length(btrim(logical_key)) > 0),
            CONSTRAINT ck_artifacts_display_filename_not_blank
                CHECK (length(btrim(display_filename)) > 0),
            CONSTRAINT ck_artifacts_mime_type_not_blank
                CHECK (length(btrim(mime_type)) > 0),
            -- Zero length is rejected: an empty artifact is a failed write
            -- that looks like a successful one.
            CONSTRAINT ck_artifacts_byte_size_positive
                CHECK (byte_size > 0 AND byte_size <= {ARTIFACT_MAX_BYTES}),
            CONSTRAINT ck_artifacts_sha256_is_a_digest
                CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_artifacts_metadata_bounded
                CHECK (coalesce(pg_column_size(metadata), 0) <= {ARTIFACT_METADATA_MAX_BYTES}),
            -- A13: durable bytes or a regenerable render, never both and never
            -- neither.
            CONSTRAINT ck_artifacts_retention_shape CHECK (
                (retention_class = 'system_render'
                 AND body IS NULL AND render_path IS NOT NULL)
                OR
                (retention_class = 'user_retained'
                 AND body IS NOT NULL AND render_path IS NULL)
            ),
            -- A relative key resolved through storage_paths.py — never a host
            -- absolute path, never a signed URL.
            CONSTRAINT ck_artifacts_render_path_is_relative CHECK (
                render_path IS NULL OR (
                    render_path !~ '^([a-zA-Z]:)?[\\\\/]'
                    AND render_path !~ '(^|[\\\\/])\\.\\.([\\\\/]|$)'
                    AND render_path !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://'
                )
            ),
            CONSTRAINT ck_artifacts_body_matches_byte_size CHECK (
                body IS NULL OR octet_length(body) = byte_size
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_artifacts_session_id_kind ON artifacts (session_id, kind)")
    op.execute(
        "CREATE INDEX ix_artifacts_created_by_agent_run_id "
        "ON artifacts (created_by_agent_run_id)"
    )

    op.execute(_ARTIFACT_IMMUTABLE_TRIGGER)
    op.execute(
        "CREATE TRIGGER artifacts_bytes_are_immutable BEFORE UPDATE ON artifacts "
        "FOR EACH ROW EXECUTE FUNCTION artifacts_enforce_immutable_bytes()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS artifacts_bytes_are_immutable ON artifacts")
    op.execute("DROP FUNCTION IF EXISTS artifacts_enforce_immutable_bytes()")
    op.execute("DROP TABLE IF EXISTS artifacts")
