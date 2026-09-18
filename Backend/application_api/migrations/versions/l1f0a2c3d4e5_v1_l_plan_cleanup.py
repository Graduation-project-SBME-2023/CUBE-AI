"""V1 plan cleanup: chat_messages hardening, dead/duplicate column and
constraint removal, imaging type/naming fixes (DATABASE-ARCHITECTURE-REVIEW.md
section 0, items 3/8/9/10/13/14/15/16/17). Radiomics ownership (item 18 of
that plan) is intentionally NOT touched here — still undecided.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "l1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "k1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── chat_messages: harden toward true append-only (plan item 3) ────────
    op.execute("UPDATE chat_messages SET timestamp = now() WHERE timestamp IS NULL")
    op.execute("ALTER TABLE chat_messages ALTER COLUMN timestamp SET NOT NULL")
    op.execute(
        "ALTER TABLE chat_messages ADD CONSTRAINT ck_chat_messages_role "
        "CHECK (role IN ('user', 'assistant', 'system'))"
    )
    op.execute(
        "ALTER TABLE chat_messages ADD CONSTRAINT ck_chat_messages_text_not_blank "
        "CHECK (length(btrim(text)) > 0)"
    )
    # Redundant with ix_chat_messages_chat_id_timestamp (chat_id, timestamp,
    # message_id), whose leading columns are a strict superset (plan item 17).
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_chat_timestamp")

    # ── segmentation_pairs.quality -> execution_checkpoint (plan item 13) ──
    # The column has always stored crash-recovery/resume checkpoint state
    # (batch_store.py's work_state/schema_version), never a QA/Dice metric.
    op.execute("ALTER TABLE segmentation_pairs RENAME COLUMN quality TO execution_checkpoint")
    op.execute(
        "ALTER TABLE segmentation_pairs "
        "RENAME CONSTRAINT ck_segmentation_pairs_quality_versioned "
        "TO ck_segmentation_pairs_execution_checkpoint_versioned"
    )

    # ── segmentations: drop dead columns (plan item 14) ─────────────────────
    # content_digest never had a writer; provenance's only writer is fed by a
    # caller that never supplies it. Both are always NULL under current code.
    op.execute("ALTER TABLE segmentations DROP COLUMN IF EXISTS provenance")
    op.execute("ALTER TABLE segmentations DROP COLUMN IF EXISTS content_digest")

    # ── feature_sets.status: derive from frozen_at instead (plan item 15) ──
    # `(status = 'frozen') = (frozen_at IS NOT NULL)` was already DB-enforced,
    # so the two could never disagree; readers now compute status instead.
    op.execute("ALTER TABLE feature_sets DROP CONSTRAINT IF EXISTS ck_feature_sets_status")
    op.execute(
        "ALTER TABLE feature_sets DROP CONSTRAINT IF EXISTS ck_feature_sets_frozen_at_iff_frozen"
    )
    op.execute("ALTER TABLE feature_sets DROP COLUMN IF EXISTS status")

    # ── Duplicate scalar FKs where a composite FK already covers existence
    # and alignment together (plan item 16) ─────────────────────────────────
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS fk_agent_runs_step_id_plan_steps")
    op.execute(
        "ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS "
        "fk_artifacts_created_by_agent_run_id_agent_runs"
    )
    op.execute(
        "ALTER TABLE model_results DROP CONSTRAINT IF EXISTS "
        "fk_model_results_feature_set_id_feature_sets"
    )

    # ── reports: duplicate plain indexes over columns a unique constraint
    # already covers (plan item 17) ─────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_reports_created_by_agent_run_id")
    op.execute("DROP INDEX IF EXISTS ix_reports_literature_review_id")

    # ── Imaging: created_at columns actually hold DICOM dates, not
    # row-insertion time (plan item 8) ──────────────────────────────────────
    op.execute("ALTER TABLE studies RENAME COLUMN created_at TO study_datetime")
    op.execute("ALTER TABLE volume_series RENAME COLUMN created_at TO series_datetime")

    # ── Imaging parent FKs: the ingestion path never creates a study without
    # a patient or a series without a study (plan item 10) ─────────────────
    op.execute("ALTER TABLE studies ALTER COLUMN patient_id SET NOT NULL")
    op.execute("ALTER TABLE volume_series ALTER COLUMN study_uid SET NOT NULL")

    # ── patients.birth_date: DICOM PatientBirthDate (DA) is date-only
    # (plan item 9) ──────────────────────────────────────────────────────────
    op.execute("ALTER TABLE patients ALTER COLUMN birth_date TYPE date USING birth_date::date")


def downgrade() -> None:
    op.execute("ALTER TABLE patients ALTER COLUMN birth_date TYPE timestamptz USING birth_date::timestamptz")

    op.execute("ALTER TABLE volume_series ALTER COLUMN study_uid DROP NOT NULL")
    op.execute("ALTER TABLE studies ALTER COLUMN patient_id DROP NOT NULL")

    op.execute("ALTER TABLE volume_series RENAME COLUMN series_datetime TO created_at")
    op.execute("ALTER TABLE studies RENAME COLUMN study_datetime TO created_at")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reports_literature_review_id "
        "ON reports (literature_review_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reports_created_by_agent_run_id "
        "ON reports (created_by_agent_run_id)"
    )

    op.execute(
        "ALTER TABLE model_results ADD CONSTRAINT fk_model_results_feature_set_id_feature_sets "
        "FOREIGN KEY (feature_set_id) REFERENCES feature_sets (feature_set_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_created_by_agent_run_id_agent_runs "
        "FOREIGN KEY (created_by_agent_run_id) REFERENCES agent_runs (agent_run_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE agent_runs ADD CONSTRAINT fk_agent_runs_step_id_plan_steps "
        "FOREIGN KEY (step_id) REFERENCES plan_steps (step_id) ON DELETE RESTRICT"
    )

    op.execute("ALTER TABLE feature_sets ADD COLUMN status text")
    op.execute(
        "UPDATE feature_sets SET status = CASE WHEN frozen_at IS NOT NULL THEN 'frozen' "
        "ELSE 'building' END"
    )
    op.execute("ALTER TABLE feature_sets ALTER COLUMN status SET NOT NULL")
    op.execute("ALTER TABLE feature_sets ALTER COLUMN status SET DEFAULT 'building'")
    op.execute(
        "ALTER TABLE feature_sets ADD CONSTRAINT ck_feature_sets_status "
        "CHECK (status IN ('building', 'frozen'))"
    )
    op.execute(
        "ALTER TABLE feature_sets ADD CONSTRAINT ck_feature_sets_frozen_at_iff_frozen "
        "CHECK ((status = 'frozen') = (frozen_at IS NOT NULL))"
    )

    op.execute("ALTER TABLE segmentations ADD COLUMN content_digest text")
    op.execute("ALTER TABLE segmentations ADD COLUMN provenance jsonb")

    op.execute(
        "ALTER TABLE segmentation_pairs "
        "RENAME CONSTRAINT ck_segmentation_pairs_execution_checkpoint_versioned "
        "TO ck_segmentation_pairs_quality_versioned"
    )
    op.execute("ALTER TABLE segmentation_pairs RENAME COLUMN execution_checkpoint TO quality")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_chat_timestamp "
        "ON chat_messages (chat_id, timestamp)"
    )
    op.execute("ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS ck_chat_messages_text_not_blank")
    op.execute("ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS ck_chat_messages_role")
    op.execute("ALTER TABLE chat_messages ALTER COLUMN timestamp DROP NOT NULL")
