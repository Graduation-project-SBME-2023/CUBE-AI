"""clear main data tables

Revision ID: c1795081c45d
Revises: c4cde385321c
Create Date: 2025-11-06 19:08:37.527463

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1795081c45d'
down_revision: Union[str, Sequence[str], None] = 'c4cde385321c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
        TRUNCATE TABLE radiomics_features CASCADE;
        TRUNCATE TABLE segmentations CASCADE;
        TRUNCATE TABLE volume_series CASCADE;
        TRUNCATE TABLE studies CASCADE;
        TRUNCATE TABLE patients CASCADE;
    """)

def downgrade() -> None:
    """Downgrade schema."""
    pass
