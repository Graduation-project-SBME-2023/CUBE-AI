"""create chat_messages table

Revision ID: 384fc5a72d92
Revises: db570196426f
Create Date: 2025-11-26 23:46:01.226984

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as postgresql

# revision identifiers, used by Alembic.
revision: str = '384fc5a72d92'
down_revision: Union[str, Sequence[str], None] = 'db570196426f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
        op.create_table(
        'chat_messages',
        sa.Column('message_id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('chat_id', sa.Integer, sa.ForeignKey('chat_sessions.chat_id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('extra_data', postgresql.JSON, nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )


def downgrade():
    op.drop_table('chat_messages')