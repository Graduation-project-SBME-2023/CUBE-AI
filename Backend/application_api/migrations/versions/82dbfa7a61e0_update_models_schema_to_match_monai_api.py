"""update models schema to match monai api

Revision ID: 82dbfa7a61e0
Revises: 0342ce56c4b1
Create Date: 2026-03-13 15:26:14.006093

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '82dbfa7a61e0'
down_revision: Union[str, Sequence[str], None] = '0342ce56c4b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():

    # rename columns
    op.alter_column("models", "model_name", new_column_name="name")
    op.alter_column("models", "configuration", new_column_name="config")

    # drop old column
    op.drop_column("models", "types")

    # add new columns
    op.add_column(
        "models",
        sa.Column("type", sa.String(), nullable=True)
    )

    op.add_column(
        "models",
        sa.Column("dimension", sa.Integer(), nullable=True)
    )

    op.add_column(
        "models",
        sa.Column("labels", sa.JSON(), nullable=True)
    )


def downgrade():

    # restore removed column
    op.add_column(
        "models",
        sa.Column("types", postgresql.JSON(), nullable=False, server_default="[]")
    )

    # remove added columns
    op.drop_column("models", "labels")
    op.drop_column("models", "dimension")
    op.drop_column("models", "type")

    # rename columns back
    op.alter_column("models", "name", new_column_name="model_name")
    op.alter_column("models", "config", new_column_name="configuration")