"""update chat sessions

Revision ID: a57c5c5784a7
Revises: 89424aedda49
Create Date: 2025-11-22 02:53:44.171808

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sqlalchemy.sql import table, column
from sqlalchemy import Integer, String, Boolean, Text, DateTime
from datetime import datetime

# revision identifiers, used by Alembic.
revision: str = 'a57c5c5784a7'
down_revision: Union[str, Sequence[str], None] = '89424aedda49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema: seed chat_sessions table."""
    
    chat_sessions_table = table(
        'chat_sessions',
        column('chat_id', Integer),
        column('title', String),
        column('started_at', DateTime),
        column('last_activity', DateTime),
        column('is_active', Boolean),
        column('summary', Text)
    )

    op.bulk_insert(
        chat_sessions_table,
        [
            {
                "title": "General AI Discussion",
                "started_at": datetime.utcnow(),
                "last_activity": datetime.utcnow(),
                "is_active": True,
                "summary": "Discussion on AI research directions."
            },
            {
                "title": "EEG Analysis Chat",
                "started_at": datetime.utcnow(),
                "last_activity": datetime.utcnow(),
                "is_active": True,
                "summary": "Session focused on EEG preprocessing and ML models."
            },
            {
                "title": "ML Fairness Review",
                "started_at": datetime.utcnow(),
                "last_activity": datetime.utcnow(),
                "is_active": True,
                "summary": "Analyzing bias in ML pipelines."
            },
        ]
    )


def downgrade() -> None:
    """Downgrade schema: remove seeded chat_sessions."""
    op.execute(
        "DELETE FROM chat_sessions WHERE title IN "
        "('General AI Discussion', 'EEG Analysis Chat', 'ML Fairness Review')"
    )