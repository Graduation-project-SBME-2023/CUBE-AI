"""remove demo chat session seed data

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-10 00:00:00.000000

Historical migration a57c5c5784a7 inserted three fixture chat sessions
("General AI Discussion", "EEG Analysis Chat", "ML Fairness Review") into
every environment that ran the migration chain, including production. That
migration is left in place (rewriting an already-applied migration is not
safe), but this follow-up removes the rows it created. The DELETE is
idempotent by title, so it is harmless to run against a database that never
had the demo rows in the first place.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEMO_TITLES = ("General AI Discussion", "EEG Analysis Chat", "ML Fairness Review")


def upgrade() -> None:
    op.execute(
        "DELETE FROM chat_sessions WHERE title IN "
        "('General AI Discussion', 'EEG Analysis Chat', 'ML Fairness Review')"
    )


def downgrade() -> None:
    # Intentionally not reinserted: the demo rows were never meant to exist
    # in a real deployment, so there is nothing correct to restore here.
    pass
