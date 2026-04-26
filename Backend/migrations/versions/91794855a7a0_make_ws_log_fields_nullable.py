"""make ws_log fields nullable

Revision ID: 91794855a7a0
Revises: 25e3766e273c
Create Date: 2026-04-26 13:01:53.305323

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91794855a7a0'
down_revision: Union[str, Sequence[str], None] = '25e3766e273c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.alter_column('ws_log', 'payload', nullable=True)
    op.alter_column('ws_log', 'msg_type', nullable=True)

def downgrade():
    op.alter_column('ws_log', 'payload', nullable=False)
    op.alter_column('ws_log', 'msg_type', nullable=False)