"""updated type column

Revision ID: 4c83730acec9
Revises: 9ed010752b68
Create Date: 2025-12-05 19:06:29.186192

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4c83730acec9'
down_revision: Union[str, Sequence[str], None] = '9ed010752b68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

def upgrade():
    # Add a new column to store multiple types
    op.add_column(
        'models',
        sa.Column('types', postgresql.JSON, nullable=False, server_default='[]')
    )

    # Optional: populate existing type into the new column
    conn = op.get_bind()
    conn.execute(
        text("UPDATE models SET types = json_build_array(type)")
    )

    # Drop the old single type column
    op.drop_column('models', 'type')


def downgrade():
    # Recreate the old single type column
    op.add_column(
        'models',
        sa.Column('type', sa.String, nullable=False, server_default='automatic')
    )

    # Optional: populate from the JSON array (take first element)
    conn = op.get_bind()
    conn.execute(
        text("UPDATE models SET type = types->>0")
    )

    # Drop the new JSON column
    op.drop_column('models', 'types')
