"""updated model table

Revision ID: 9ed010752b68
Revises: 384fc5a72d92
Create Date: 2025-12-05 18:26:57.284204

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9ed010752b68'
down_revision: Union[str, Sequence[str], None] = '384fc5a72d92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add new_column to models table."""
    op.add_column(
        'models',
        sa.Column('type', sa.String, nullable=False, server_default='default_type')
    )


def downgrade() -> None:
    """Remove new_column from models table."""
    op.drop_column('models', 'type')