"""Add indexes for bounded application API list operations.

Revision ID: f1a2b3c4d5e6
Revises: e7b1c4a9d2f0
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7b1c4a9d2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_chat_sessions_active_last_activity",
        "chat_sessions",
        ["is_active", "last_activity"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_chat_timestamp",
        "chat_messages",
        ["chat_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_details_task_study",
        "workflow_details",
        ["task_name", "study_uid"],
        unique=False,
    )
    op.create_index(
        "ix_chat_study_association_chat_id",
        "chat_study_association",
        ["chat_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_study_association_study_uid",
        "chat_study_association",
        ["study_uid"],
        unique=False,
    )
    op.create_index(
        "ix_patients_name_trgm",
        "patients",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_studies_description_trgm",
        "studies",
        ["description"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_volume_series_description_trgm",
        "volume_series",
        ["series_description"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"series_description": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_volume_series_description_trgm", table_name="volume_series")
    op.drop_index("ix_studies_description_trgm", table_name="studies")
    op.drop_index("ix_patients_name_trgm", table_name="patients")
    op.drop_index("ix_chat_study_association_study_uid", table_name="chat_study_association")
    op.drop_index("ix_chat_study_association_chat_id", table_name="chat_study_association")
    op.drop_index("ix_workflow_details_task_study", table_name="workflow_details")
    op.drop_index("ix_chat_messages_chat_timestamp", table_name="chat_messages")
    op.drop_index("ix_chat_sessions_active_last_activity", table_name="chat_sessions")
