"""schema reconciliation: constraints and indexes

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-09-10 00:05:00.000000

Bundles the structural fixes from the database audit
(cubeai-docs/backend/database_audit.md) that are safe to apply
unconditionally, without first inspecting live data:

- D-4: restore the composite primary key on chat_study_association, after
  de-duplicating any rows that accumulated while the constraint was absent.
- D-5: add the missing UNIQUE(hypothesis_id) on plans, matching the ORM's
  `unique=True` declaration and the one-plan-per-hypothesis invariant.
- D-6: drop the UNIQUE(name, birth_date) on patients — the wrong key for
  patient identity now that sync keys on orthanc_patient_id.
- D-26: add indexes on filtered/ordered columns that had none.
- models.name: add UNIQUE, matching the ORM and the upsert-by-name pattern
  used throughout the model sync code.

Column-level nullable/type drift (D-3) is deliberately NOT touched here: per
the audit, resolving it safely requires inspecting the live database first
(Phase 0's schema capture), since blindly tightening a NOT NULL constraint
against a database that already has legacy NULL rows would fail the
migration outright. Do that reconciliation as its own follow-up migration
once the live schema has been captured and compared.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a1"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- D-4: restore chat_study_association's primary key -----------------
    # De-duplicate first: the table has had no uniqueness guarantee since
    # migration 6664d514406c, so duplicate (chat_id, study_uid) pairs may
    # already exist. Keep one arbitrary row per pair via ctid.
    op.execute(
        """
        DELETE FROM chat_study_association a
        USING chat_study_association b
        WHERE a.ctid < b.ctid
          AND a.chat_id = b.chat_id
          AND a.study_uid = b.study_uid
        """
    )
    op.create_primary_key(
        "pk_chat_study_association",
        "chat_study_association",
        ["chat_id", "study_uid"],
    )

    # --- D-5: plans.hypothesis_id must be unique ----------------------------
    op.create_unique_constraint(
        "uq_plans_hypothesis_id", "plans", ["hypothesis_id"]
    )

    # --- D-6: drop the wrong patient-identity constraint --------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'patients_name_birth_date_key'
            ) THEN
                ALTER TABLE patients DROP CONSTRAINT patients_name_birth_date_key;
            END IF;
        END $$;
        """
    )

    # --- models.name must be unique -----------------------------------------
    op.create_unique_constraint("uq_models_name", "models", ["name"])

    # --- D-26: missing indexes on filtered/ordered columns ------------------
    op.create_index("ix_plan_steps_plan_id", "plan_steps", ["plan_id"])
    op.create_index("ix_plans_hypothesis_id", "plans", ["hypothesis_id"])
    op.create_index(
        "ix_task_messages_context_augmentation_id",
        "task_messages",
        ["context_augmentation_id"],
    )
    op.create_index("ix_segmentations_name", "segmentations", ["name"])
    op.create_index("ix_hypotheses_created_at", "hypotheses", ["created_at"])
    op.create_index("ix_ws_log_created_at", "ws_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ws_log_created_at", table_name="ws_log")
    op.drop_index("ix_hypotheses_created_at", table_name="hypotheses")
    op.drop_index("ix_segmentations_name", table_name="segmentations")
    op.drop_index("ix_task_messages_context_augmentation_id", table_name="task_messages")
    op.drop_index("ix_plans_hypothesis_id", table_name="plans")
    op.drop_index("ix_plan_steps_plan_id", table_name="plan_steps")

    op.drop_constraint("uq_models_name", "models", type_="unique")

    op.execute(
        """
        ALTER TABLE patients
            ADD CONSTRAINT patients_name_birth_date_key UNIQUE (name, birth_date)
        """
    )

    op.drop_constraint("uq_plans_hypothesis_id", "plans", type_="unique")

    op.drop_constraint("pk_chat_study_association", "chat_study_association", type_="primary")
