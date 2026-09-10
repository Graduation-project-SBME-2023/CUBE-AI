"""schema drift: column types, dead columns, reserved-word rename

Revision ID: c9d0e1f2a3b4
Revises: b2c3d4e5f6a1
Create Date: 2026-09-10 00:30:00.000000

Follow-up to b2c3d4e5f6a1, applying the column-level drift (audit D-3) that
was deliberately deferred there ("resolving it safely requires inspecting the
live database first"). This migration inspects each column's current type via
information_schema before touching it, so it is safe to run against databases
that already drifted toward the ORM shape through the old
``Base.metadata.create_all()`` seeder call.

Part A schema-fix items covered here:

- F1  hypotheses: DROP the stray column literally named "column" (Text). Nothing
      reads or writes it.
- F2  plan_steps: rename the reserved word column "order" -> step_order so raw
      SQL no longer has to quote it.
- F3  7 naive timestamp columns -> timestamptz, to match chat/hypothesis/plan/
      plan_steps which already use timestamptz: created_at on models, patients,
      radiomics_features, segmentations, studies, volume_series, plus
      patients.birth_date.
- F4  6 json columns -> jsonb (containment queries + GIN, no reparse on read):
      radiomics_features.features, chat_messages.extra_data, and
      models.{anatomy,labels,params_schema,config}.
- F9  created_at on the 6 Orthanc-synced tables (patients, studies,
      volume_series, segmentations, models, radiomics_features): SET DEFAULT
      now(), backfill NULLs, SET NOT NULL, so a synced row can never land
      without a timestamp.
- F7  workflow: the one-column stub table is the live task registry that
      workflow_details FKs into and that the training routes read, so it is
      filled out (created_at / description / status) rather than dropped.

F5 (models.name UNIQUE) and F6 (chat_study_association composite PK) already
landed in b2c3d4e5f6a1 and are not repeated here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# created_at columns that must become timezone-aware, get a server default and
# be NOT NULL (F3 + F9). All six are written by the Orthanc sync path.
_SYNC_TIMESTAMP_TABLES = (
    "models",
    "patients",
    "radiomics_features",
    "segmentations",
    "studies",
    "volume_series",
)

# json -> jsonb (F4).
_JSONB_COLUMNS = (
    ("radiomics_features", "features"),
    ("chat_messages", "extra_data"),
    ("models", "anatomy"),
    ("models", "labels"),
    ("models", "params_schema"),
    ("models", "config"),
)


def _column_type(bind, table: str, column: str):
    """Return information_schema.data_type for a public-schema column, or None."""
    return bind.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :t
              AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()


def _has_column(bind, table: str, column: str) -> bool:
    return _column_type(bind, table, column) is not None


def upgrade() -> None:
    bind = op.get_bind()

    # --- F1: drop the stray hypotheses."column" ---------------------------
    if _has_column(bind, "hypotheses", "column"):
        op.execute('ALTER TABLE hypotheses DROP COLUMN "column"')

    # --- F2: plan_steps."order" -> step_order -----------------------------
    if _has_column(bind, "plan_steps", "order") and not _has_column(
        bind, "plan_steps", "step_order"
    ):
        op.execute('ALTER TABLE plan_steps RENAME COLUMN "order" TO step_order')

    # --- F3 + F9: naive created_at -> timestamptz, default, NOT NULL ------
    for table in _SYNC_TIMESTAMP_TABLES:
        if _column_type(bind, table, "created_at") == "timestamp without time zone":
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN created_at TYPE timestamptz "
                f"USING created_at AT TIME ZONE 'UTC'"
            )
        op.execute(f"UPDATE {table} SET created_at = now() WHERE created_at IS NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET DEFAULT now()")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET NOT NULL")

    # --- F3: patients.birth_date -> timestamptz (no default; still PHI) ---
    birth_date_type = _column_type(bind, "patients", "birth_date")
    if birth_date_type in ("date", "timestamp without time zone"):
        op.execute(
            "ALTER TABLE patients "
            "ALTER COLUMN birth_date TYPE timestamptz "
            "USING birth_date::timestamp AT TIME ZONE 'UTC'"
        )

    # --- F4: json -> jsonb ----------------------------------------------
    for table, column in _JSONB_COLUMNS:
        if _column_type(bind, table, column) == "json":
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {column} TYPE jsonb USING {column}::jsonb"
            )

    # --- F7: fill out the workflow registry table ----------------------
    if not _has_column(bind, "workflow", "created_at"):
        op.execute(
            "ALTER TABLE workflow "
            "ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()"
        )
    if not _has_column(bind, "workflow", "description"):
        op.execute("ALTER TABLE workflow ADD COLUMN description text")
    if not _has_column(bind, "workflow", "status"):
        op.execute(
            "ALTER TABLE workflow "
            "ADD COLUMN status varchar(50) NOT NULL DEFAULT 'active'"
        )


def downgrade() -> None:
    bind = op.get_bind()

    # --- F7 ------------------------------------------------------------
    for column in ("status", "description", "created_at"):
        if _has_column(bind, "workflow", column):
            op.execute(f"ALTER TABLE workflow DROP COLUMN {column}")

    # --- F4: jsonb -> json -------------------------------------------
    for table, column in _JSONB_COLUMNS:
        if _column_type(bind, table, column) == "jsonb":
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {column} TYPE json USING {column}::json"
            )

    # --- F3: patients.birth_date -> date ---------------------------------
    if _column_type(bind, "patients", "birth_date") == "timestamp with time zone":
        op.execute(
            "ALTER TABLE patients "
            "ALTER COLUMN birth_date TYPE date USING birth_date::date"
        )

    # --- F3 + F9: timestamptz created_at -> naive, drop default/NOT NULL -
    for table in _SYNC_TIMESTAMP_TABLES:
        # volume_series.created_at was NOT NULL before this revision; the rest
        # were nullable. Restore the nullable ones only.
        if table != "volume_series":
            op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at DROP NOT NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at DROP DEFAULT")
        if _column_type(bind, table, "created_at") == "timestamp with time zone":
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN created_at TYPE timestamp "
                f"USING created_at AT TIME ZONE 'UTC'"
            )
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP"
        )

    # --- F2: step_order -> "order" ------------------------------------
    if _has_column(bind, "plan_steps", "step_order") and not _has_column(
        bind, "plan_steps", "order"
    ):
        op.execute('ALTER TABLE plan_steps RENAME COLUMN step_order TO "order"')

    # --- F1: restore hypotheses."column" ----------------------------
    if not _has_column(bind, "hypotheses", "column"):
        op.execute('ALTER TABLE hypotheses ADD COLUMN "column" text')
