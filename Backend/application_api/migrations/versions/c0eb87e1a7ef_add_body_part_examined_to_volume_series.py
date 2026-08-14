"""add body_part_examined to volume_series

Revision ID: c0eb87e1a7ef
Revises: 106e8488b24c
Create Date: 2026-05-01 17:04:57.157132

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c0eb87e1a7ef'
down_revision: Union[str, Sequence[str], None] = '106e8488b24c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'volume_series',
        sa.Column('body_part_examined', sa.String(), nullable=True)
    )

def downgrade() -> None:
        op.drop_column('volume_series', 'body_part_examined')
