"""add orthanc ids

Revision ID: 0342ce56c4b1
Revises: 918cb5e42593
Create Date: 2025-12-22 20:09:36.849119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0342ce56c4b1'
down_revision: Union[str, Sequence[str], None] = '918cb5e42593'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("patients", sa.Column("orthanc_patient_id", sa.String(), nullable=True))
    op.add_column("studies", sa.Column("orthanc_study_id", sa.String(), nullable=True))
    op.add_column("volume_series", sa.Column("orthanc_series_id", sa.String(), nullable=True))
    op.add_column("segmentations", sa.Column("orthanc_series_id", sa.String(), nullable=True))

    op.create_index(None, "patients", ["orthanc_patient_id"], unique=True)
    op.create_index(None, "studies", ["orthanc_study_id"], unique=True)
    op.create_index(None, "volume_series", ["orthanc_series_id"], unique=True)
    op.create_index(None, "segmentations", ["orthanc_series_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    pass
