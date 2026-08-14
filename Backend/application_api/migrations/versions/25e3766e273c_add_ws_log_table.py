"""add ws_log table

Revision ID: 25e3766e273c
Revises: 006b8a8b4301
Create Date: 2026-04-26 12:45:31.948380

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '25e3766e273c'
down_revision: Union[str, Sequence[str], None] = '006b8a8b4301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.create_table(
        'ws_log',
        sa.Column(
            'uuid',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()')
        ),
        sa.Column(
            'session_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('chat_sessions.chat_id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column('msg_type', sa.String(length=50), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()')
        )
    )

    op.create_index('ix_ws_log_session_id', 'ws_log', ['session_id'])#


def downgrade() -> None:
    op.drop_index('ix_ws_log_session_id', table_name='ws_log')
    op.drop_table('ws_log')