"""sessions.created_at becomes timestamptz, as the model declares it

The column was created as `timestamp without time zone` while every other
timestamp of the schema (and the model) is `timestamptz`, so a session's
creation time came back naive and could not be compared with the aware times
next to it. The stored values were written by `now()` and by the API in UTC
(the database runs with TimeZone = UTC), so they are read as UTC.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "q1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "p1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC',
            ALTER COLUMN created_at SET DEFAULT now()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
            ALTER COLUMN created_at TYPE timestamp USING created_at AT TIME ZONE 'UTC',
            ALTER COLUMN created_at SET DEFAULT now()
        """
    )
