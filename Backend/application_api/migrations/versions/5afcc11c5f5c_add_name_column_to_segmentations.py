"""add name column to segmentations

Revision ID: 5afcc11c5f5c
Revises: c0eb87e1a7ef
Create Date: 2026-05-01 23:49:55.191567

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text
# revision identifiers, used by Alembic.
revision: str = '5afcc11c5f5c'
down_revision: Union[str, Sequence[str], None] = 'c0eb87e1a7ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    # 1. Drop ONLY the model FK constraint (correct name)
    op.drop_constraint(
        'fk_segmentation_model',
        'segmentations',
        type_='foreignkey'
    )

    # 3. Rename in segmentations table
    op.alter_column(
        'segmentations',
        'model_name',
        new_column_name='name'
    )

    # 4. Recreate FK correctly
    op.create_foreign_key(
        'fk_segmentation_model',
        'segmentations',
        'models',
        ['name'],
        ['name']
    )


def downgrade():
    op.drop_constraint(
        'fk_segmentation_model',
        'segmentations',
        type_='foreignkey'
    )

    op.alter_column(
        'segmentations',
        'name',
        new_column_name='model_name'
    )



    op.create_foreign_key(
        'fk_segmentation_model',
        'segmentations',
        'models',
        ['model_name'],
        ['model_name']
    )