"""from Comparison to comparison

Revision ID: 42c5d5b98a21
Revises: a0c989f85294
Create Date: 2026-03-22 19:26:38.126790

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '42c5d5b98a21'
down_revision: Union[str, Sequence[str], None] = 'a0c989f85294'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute(
        'ALTER TABLE hypotheses RENAME COLUMN "Comparison" TO comparison;'
    )

def downgrade():
    op.execute(
        'ALTER TABLE hypotheses RENAME COLUMN comparison TO "Comparison";'
    )