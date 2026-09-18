"""remove gender check constraint

Revision ID: db570196426f
Revises: a57c5c5784a7
Create Date: 2025-11-25 23:10:41.628068

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db570196426f'
down_revision: Union[str, Sequence[str], None] = 'a57c5c5784a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_constraint(
        # This constraint was created by the initial migration with this exact
        # legacy name. Mark it as finalized so the current SQLAlchemy naming
        # convention does not turn it into
        # ``ck_patients_patients_gender_check``.
        op.f("patients_gender_check"),
        "patients",
        type_="check"
    )

def downgrade():
    op.create_check_constraint(
        op.f("patients_gender_check"),
        "patients",
        "gender IN ('M', 'F')"
    )
