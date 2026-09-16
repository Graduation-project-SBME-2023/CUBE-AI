"""current this flow: imaging safety — RESTRICT, archive_status, segmentation provenance"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ARCHIVE_STATUS_TABLES = ("patients", "studies", "volume_series", "segmentations")
UPDATED_AT_TABLES = ("patients", "studies", "volume_series", "models", "workflow")

# (table, column, referenced table, referenced column) — every research or
# history DICOM reference, which requires to be RESTRICT.
RESTRICT_FOREIGN_KEYS = (
    ("studies", "patient_id", "patients", "patient_id"),
    ("volume_series", "study_uid", "studies", "study_uid"),
    ("segmentations", "volume_series_instance_uid", "volume_series", "volume_series_instance_uid"),
    ("radiomics_features", "label_series_instance_uid", "segmentations", "label_series_instance_uid"),
    ("workflow_details", "task_name", "workflow", "task_name"),
)


# ----------------------------------------------------------------------
# Introspection helpers — this database has drifted from its own migration
# history, so nothing below assumes a column or constraint name exists.
# ----------------------------------------------------------------------
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


def _has_constraint(bind, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}
        ).scalar()
    )


def _foreign_key_names(bind, table: str, column: str) -> list[str]:
    """Every FK constraint on ``table`` whose single key column is ``column``."""
    rows = bind.execute(
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
    return list(rows)


def _fk_name(table: str, column: str, referred: str) -> str:
    """The name the ORM's naming convention would produce for this FK."""
    return f"fk_{table}_{column}_{referred}"


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. archive_status — Orthanc loss marks rows, it never deletes history
    # ------------------------------------------------------------------
    for table in ARCHIVE_STATUS_TABLES:
        if not _has_column(bind, table, "archive_status"):
            op.execute(
                f"ALTER TABLE {table} ADD COLUMN archive_status text "
                "NOT NULL DEFAULT 'available'"
            )
        constraint = f"ck_{table}_archive_status"
        if not _has_constraint(bind, constraint):
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                "CHECK (archive_status IN ('available', 'unavailable', 'quarantined'))"
            )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_archive_status "
            f"ON {table} (archive_status)"
        )

    # ------------------------------------------------------------------
    # 2.updated_at on the five sync-written tables that keep it
    # ------------------------------------------------------------------
    for table in UPDATED_AT_TABLES:
        if not _has_column(bind, table, "updated_at"):
            op.execute(
                f"ALTER TABLE {table} ADD COLUMN updated_at timestamptz "
                "NOT NULL DEFAULT now()"
            )
            if _has_column(bind, table, "created_at"):
                # An existing row has never been mutated since it was written,
                # so its creation time is the honest updated_at.
                op.execute(
                    f"UPDATE {table} SET updated_at = created_at "
                    "WHERE created_at IS NOT NULL"
                )

    #drop studies.metadata_synced_at. It is absent from the checked-in
    # history; the guard exists because the live database has drifted before.
    if _has_column(bind, "studies", "metadata_synced_at"):
        op.execute("ALTER TABLE studies DROP COLUMN metadata_synced_at")

    # ------------------------------------------------------------------
    # 3. volume_series supporting UNIQUE tuple
    # ------------------------------------------------------------------
    if not _has_constraint(bind, "uq_volume_series_study_uid_volume_series_instance_uid"):
        op.execute(
            "ALTER TABLE volume_series ADD CONSTRAINT "
            "uq_volume_series_study_uid_volume_series_instance_uid "
            "UNIQUE (study_uid, volume_series_instance_uid)"
        )

    # ------------------------------------------------------------------
    # 4. segmentations — renames, provenance, model_id backfill, drop name
    # ------------------------------------------------------------------
    if _has_column(bind, "segmentations", "segmentation_type") and not _has_column(
        bind, "segmentations", "series_description"
    ):
        op.execute(
            "ALTER TABLE segmentations RENAME COLUMN segmentation_type TO series_description"
        )
    if _has_column(bind, "segmentations", "method") and not _has_column(
        bind, "segmentations", "generation_method"
    ):
        op.execute("ALTER TABLE segmentations RENAME COLUMN method TO generation_method")

    if not _has_column(bind, "segmentations", "origin"):
        op.execute(
            "ALTER TABLE segmentations ADD COLUMN origin text "
            "NOT NULL DEFAULT 'imported'"
        )
    if not _has_constraint(bind, "ck_segmentations_origin"):
        op.execute(
            "ALTER TABLE segmentations ADD CONSTRAINT ck_segmentations_origin "
            "CHECK (origin IN ('assisted', 'pipeline', 'imported'))"
        )
    if not _has_column(bind, "segmentations", "is_empty_mask"):
        op.execute("ALTER TABLE segmentations ADD COLUMN is_empty_mask boolean")
    if not _has_column(bind, "segmentations", "provenance"):
        op.execute("ALTER TABLE segmentations ADD COLUMN provenance jsonb")
    if not _has_column(bind, "segmentations", "content_digest"):
        op.execute("ALTER TABLE segmentations ADD COLUMN content_digest text")

    if not _has_column(bind, "segmentations", "model_id"):
        op.execute("ALTER TABLE segmentations ADD COLUMN model_id uuid")
        # backfill from the EXACT model name before the column is dropped.
        # No fuzzy matching — an unmatched name leaves model_id NULL, which is
        # the honest record of "the producing model is not in the catalog".
        if _has_column(bind, "segmentations", "name"):
            op.execute(
                """
                UPDATE segmentations s
                SET model_id = m.model_id
                FROM models m
                WHERE s.name IS NOT NULL AND s.name = m.name
                """
            )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_segmentations_model_id "
            "ON segmentations (model_id)"
        )
    if not _has_constraint(bind, _fk_name("segmentations", "model_id", "models")):
        op.execute(
            "ALTER TABLE segmentations ADD CONSTRAINT "
            f"{_fk_name('segmentations', 'model_id', 'models')} "
            "FOREIGN KEY (model_id) REFERENCES models (model_id) ON DELETE RESTRICT"
        )

    if _has_column(bind, "segmentations", "name"):
        for constraint in _foreign_key_names(bind, "segmentations", "name"):
            op.execute(f'ALTER TABLE segmentations DROP CONSTRAINT "{constraint}"')
        op.execute("ALTER TABLE segmentations DROP COLUMN name")

    # ------------------------------------------------------------------
    # 5. RESTRICT everywhere on the archive chain
    # ------------------------------------------------------------------
    for table, column, referred, referred_column in RESTRICT_FOREIGN_KEYS:
        if not _has_table(bind, table):
            continue
        for constraint in _foreign_key_names(bind, table, column):
            op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{constraint}"')
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {_fk_name(table, column, referred)} "
            f"FOREIGN KEY ({column}) REFERENCES {referred} ({referred_column}) "
            "ON DELETE RESTRICT"
        )

    # ------------------------------------------------------------------
    # 6.workflow hardening
    # ------------------------------------------------------------------
    if not _has_column(bind, "workflow", "display_title"):
        op.execute("ALTER TABLE workflow ADD COLUMN display_title text")
    if not _has_column(bind, "workflow", "definition"):
        op.execute(
            "ALTER TABLE workflow ADD COLUMN definition jsonb NOT NULL DEFAULT '{}'::jsonb"
        )
    if not _has_column(bind, "workflow", "frozen_at"):
        op.execute("ALTER TABLE workflow ADD COLUMN frozen_at timestamptz")
    if not _has_column(bind, "workflow", "dataset_id"):
        op.execute("ALTER TABLE workflow ADD COLUMN dataset_id integer")
        # Deterministic allocation for existing tasks: ordered by task_name so
        # the assignment is reproducible, starting at 1.
        op.execute(
            """
            WITH numbered AS (
                SELECT task_name, row_number() OVER (ORDER BY task_name) AS n
                FROM workflow
            )
            UPDATE workflow w SET dataset_id = numbered.n
            FROM numbered WHERE numbered.task_name = w.task_name
            """
        )
        op.execute("ALTER TABLE workflow ALTER COLUMN dataset_id SET NOT NULL")
    if not _has_constraint(bind, "uq_workflow_dataset_id"):
        op.execute(
            "ALTER TABLE workflow ADD CONSTRAINT uq_workflow_dataset_id "
            "UNIQUE (dataset_id)"
        )
    if not _has_constraint(bind, "ck_workflow_dataset_id_positive"):
        op.execute(
            "ALTER TABLE workflow ADD CONSTRAINT ck_workflow_dataset_id_positive "
            "CHECK (dataset_id > 0)"
        )

    # ------------------------------------------------------------------
    # 7. workflow_details: deterministic, complete training input
    # ------------------------------------------------------------------
    orphans = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM workflow_details d
            WHERE NOT EXISTS (
                SELECT 1 FROM volume_series v
                WHERE v.study_uid = d.study_uid
                  AND v.volume_series_instance_uid = d.volume_series_instance_uid
            )
            """
        )
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} workflow_details row(s) reference a (study_uid, "
            "volume_series_instance_uid) pair that is not in volume_series. "
            "R12 requires the composite archive FK; reconcile or remove those "
            "rows (they cannot produce a training case) and re-run. This "
            "migration deliberately refuses to weaken the constraint or to "
            "delete training membership on your behalf."
        )

    if not _has_column(bind, "workflow_details", "case_index"):
        op.execute("ALTER TABLE workflow_details ADD COLUMN case_index integer")
        op.execute(
            """
            WITH numbered AS (
                SELECT task_name, study_uid, volume_series_instance_uid,
                       row_number() OVER (
                           PARTITION BY task_name
                           ORDER BY study_uid, volume_series_instance_uid
                       ) AS n
                FROM workflow_details
            )
            UPDATE workflow_details d SET case_index = numbered.n
            FROM numbered
            WHERE numbered.task_name = d.task_name
              AND numbered.study_uid = d.study_uid
              AND numbered.volume_series_instance_uid = d.volume_series_instance_uid
            """
        )
        op.execute("ALTER TABLE workflow_details ALTER COLUMN case_index SET NOT NULL")
    if not _has_column(bind, "workflow_details", "channel_index"):
        op.execute(
            "ALTER TABLE workflow_details ADD COLUMN channel_index integer "
            "NOT NULL DEFAULT 0"
        )
    if not _has_column(bind, "workflow_details", "subject_key"):
        op.execute("ALTER TABLE workflow_details ADD COLUMN subject_key text")

    if not _has_constraint(bind, "ck_workflow_details_case_index_positive"):
        op.execute(
            "ALTER TABLE workflow_details ADD CONSTRAINT "
            "ck_workflow_details_case_index_positive CHECK (case_index > 0)"
        )
    if not _has_constraint(bind, "ck_workflow_details_channel_index_nonnegative"):
        op.execute(
            "ALTER TABLE workflow_details ADD CONSTRAINT "
            "ck_workflow_details_channel_index_nonnegative CHECK (channel_index >= 0)"
        )
    if not _has_constraint(bind, "uq_workflow_details_task_name"):
        op.execute(
            "ALTER TABLE workflow_details ADD CONSTRAINT uq_workflow_details_task_name "
            "UNIQUE (task_name, case_index, channel_index)"
        )

    # Composite archive FK: a training member's volume must actually belong to
    # the study it claims ('s rule applied to the one existing table that
    # already carried both halves of the key).
    composite = "fk_workflow_details_study_uid_volume_series"
    if not _has_constraint(bind, composite):
        op.execute(
            f"ALTER TABLE workflow_details ADD CONSTRAINT {composite} "
            "FOREIGN KEY (study_uid, volume_series_instance_uid) "
            "REFERENCES volume_series (study_uid, volume_series_instance_uid) "
            "ON DELETE RESTRICT"
        )

    label_fk = _fk_name("workflow_details", "label_series_instance_uid", "segmentations")
    for constraint in _foreign_key_names(
        bind, "workflow_details", "label_series_instance_uid"
    ):
        op.execute(f'ALTER TABLE workflow_details DROP CONSTRAINT "{constraint}"')
    # Legacy rows may name a label series that was never synced; those are
    # cleared rather than blocking the constraint, because a dangling label
    # reference carries no information the training path can use.
    op.execute(
        """
        UPDATE workflow_details d SET label_series_instance_uid = NULL
        WHERE d.label_series_instance_uid IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM segmentations s
              WHERE s.label_series_instance_uid = d.label_series_instance_uid
          )
        """
    )
    op.execute(
        f"ALTER TABLE workflow_details ADD CONSTRAINT {label_fk} "
        "FOREIGN KEY (label_series_instance_uid) "
        "REFERENCES segmentations (label_series_instance_uid) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    bind = op.get_bind()

    # 7/6. workflow_details + workflow
    for constraint in (
        _fk_name("workflow_details", "label_series_instance_uid", "segmentations"),
        "fk_workflow_details_study_uid_volume_series",
        "uq_workflow_details_task_name",
        "ck_workflow_details_channel_index_nonnegative",
        "ck_workflow_details_case_index_positive",
    ):
        if _has_constraint(bind, constraint):
            op.execute(f'ALTER TABLE workflow_details DROP CONSTRAINT "{constraint}"')
    for column in ("subject_key", "channel_index", "case_index"):
        if _has_column(bind, "workflow_details", column):
            op.execute(f"ALTER TABLE workflow_details DROP COLUMN {column}")

    for constraint in ("ck_workflow_dataset_id_positive", "uq_workflow_dataset_id"):
        if _has_constraint(bind, constraint):
            op.execute(f'ALTER TABLE workflow DROP CONSTRAINT "{constraint}"')
    for column in ("dataset_id", "frozen_at", "definition", "display_title"):
        if _has_column(bind, "workflow", column):
            op.execute(f"ALTER TABLE workflow DROP COLUMN {column}")

    # 5. FK actions back to the legacy cascades.
    legacy_actions = {
        ("studies", "patient_id"): "CASCADE",
        ("volume_series", "study_uid"): "CASCADE",
        ("segmentations", "volume_series_instance_uid"): "CASCADE",
        ("radiomics_features", "label_series_instance_uid"): "CASCADE",
        ("workflow_details", "task_name"): "CASCADE",
    }
    for table, column, referred, referred_column in RESTRICT_FOREIGN_KEYS:
        if not _has_table(bind, table):
            continue
        for constraint in _foreign_key_names(bind, table, column):
            op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{constraint}"')
        action = legacy_actions[(table, column)]
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {_fk_name(table, column, referred)} "
            f"FOREIGN KEY ({column}) REFERENCES {referred} ({referred_column}) "
            f"ON DELETE {action}"
        )

    # 4. segmentations. `name` is restored as a column but its values are NOT
    # recoverable for rows whose model left the catalog; this downgrade is a
    # schema rollback, not a data rollback.
    if not _has_column(bind, "segmentations", "name"):
        op.execute("ALTER TABLE segmentations ADD COLUMN name varchar(100)")
        op.execute(
            """
            UPDATE segmentations s SET name = m.name
            FROM models m WHERE s.model_id = m.model_id
            """
        )
        op.execute(
            "ALTER TABLE segmentations ADD CONSTRAINT fk_segmentation_model "
            "FOREIGN KEY (name) REFERENCES models (name)"
        )
    for constraint in (_fk_name("segmentations", "model_id", "models"), "ck_segmentations_origin"):
        if _has_constraint(bind, constraint):
            op.execute(f'ALTER TABLE segmentations DROP CONSTRAINT "{constraint}"')
    op.execute("DROP INDEX IF EXISTS ix_segmentations_model_id")
    for column in ("model_id", "content_digest", "provenance", "is_empty_mask", "origin"):
        if _has_column(bind, "segmentations", column):
            op.execute(f"ALTER TABLE segmentations DROP COLUMN {column}")
    if _has_column(bind, "segmentations", "generation_method"):
        op.execute("ALTER TABLE segmentations RENAME COLUMN generation_method TO method")
    if _has_column(bind, "segmentations", "series_description"):
        op.execute(
            "ALTER TABLE segmentations RENAME COLUMN series_description TO segmentation_type"
        )

    # 3.
    if _has_constraint(bind, "uq_volume_series_study_uid_volume_series_instance_uid"):
        op.execute(
            "ALTER TABLE volume_series DROP CONSTRAINT "
            "uq_volume_series_study_uid_volume_series_instance_uid"
        )

    # 2.
    for table in UPDATED_AT_TABLES:
        if _has_column(bind, table, "updated_at"):
            op.execute(f"ALTER TABLE {table} DROP COLUMN updated_at")

    # 1.
    for table in ARCHIVE_STATUS_TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_archive_status")
        if _has_constraint(bind, f"ck_{table}_archive_status"):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_archive_status")
        if _has_column(bind, table, "archive_status"):
            op.execute(f"ALTER TABLE {table} DROP COLUMN archive_status")
