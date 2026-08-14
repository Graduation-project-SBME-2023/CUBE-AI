"""Convert ChatSession-Study to many-to-many

Revision ID: c4cde385321c
Revises: 04eab3c05281
Create Date: 2025-11-06 03:35:33.837523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4cde385321c'
down_revision: Union[str, Sequence[str], None] = '04eab3c05281'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # 1. Drop old one-to-many column if it exists
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_column("study_uid")

    # 2. Create association table
    op.create_table(
        "chat_study_association",
        sa.Column("chat_id", sa.Integer, sa.ForeignKey("chat_sessions.chat_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("study_uid", sa.String, sa.ForeignKey("studies.study_uid", ondelete="CASCADE"), primary_key=True)
    )

def downgrade():
    op.drop_table("chat_study_association")
    # Re-add the old column
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.add_column(sa.Column("study_uid", sa.String, nullable=True))