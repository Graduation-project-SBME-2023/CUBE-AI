"""add logs field to plan_steps

Revision ID: 918cb5e42593
Revises: 6664d514406c
Create Date: 2025-12-12 23:30:51.461338

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '918cb5e42593'
down_revision: Union[str, Sequence[str], None] = '6664d514406c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column(
        'plan_steps',
        sa.Column('logs', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=True)
    )


def downgrade():
    op.drop_column('plan_steps', 'logs')