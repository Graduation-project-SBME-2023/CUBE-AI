"""clear main data tables

Revision ID: 89424aedda49
Revises: a3f16d77d6eb
Create Date: 2025-11-07 03:43:35.511084

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89424aedda49'
down_revision: Union[str, Sequence[str], None] = 'a3f16d77d6eb'
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
