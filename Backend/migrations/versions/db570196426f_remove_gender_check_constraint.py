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
        "patients_gender_check",  # change to your actual name!
        "patients",
        type_="check"
    )

def downgrade():
    op.create_check_constraint(
        "patients_gender_check",
        "patients",
        "gender IN ('M', 'F')"
    )
