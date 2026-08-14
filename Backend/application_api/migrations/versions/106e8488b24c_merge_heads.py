"""merge heads

Revision ID: 106e8488b24c
Revises: 91794855a7a0, c04a22da5013
Create Date: 2026-04-28 03:41:42.929772

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '106e8488b24c'
down_revision: Union[str, Sequence[str], None] = ('91794855a7a0', 'c04a22da5013')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
