"""update tables constrains

Revision ID: a3f16d77d6eb
Revises: c1795081c45d
Create Date: 2025-11-07 02:19:01.106263

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f16d77d6eb'
down_revision: Union[str, Sequence[str], None] = 'c1795081c45d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1️⃣ Make birth_date nullable
    op.alter_column(
    'patients',
    'birth_date',
    existing_type=sa.DATE(),
    nullable=True,
    existing_nullable=False
    )

    # 2️⃣ Modify modality columns to VARCHAR(50) and drop constraints
    op.alter_column(
        'studies',
        'modality',
        existing_type=sa.VARCHAR(length=20),
        type_=sa.VARCHAR(length=50),
        nullable=True
    )

    op.alter_column(
        'volume_series',
        'modality',
        existing_type=sa.VARCHAR(length=20),
        type_=sa.VARCHAR(length=50),
        nullable=True
    )

def downgrade() -> None:
    # revert birth_date to NOT NULL
    op.alter_column(
    'patients',
    'birth_date',
    existing_type=sa.DATE(),
    nullable=False
    )

    # revert modality columns to VARCHAR(20)
    op.alter_column(
        'studies',
        'modality',
        existing_type=sa.VARCHAR(length=50),
        type_=sa.VARCHAR(length=20),
        nullable=True
    )

    op.alter_column(
        'volume_series',
        'modality',
        existing_type=sa.VARCHAR(length=50),
        type_=sa.VARCHAR(length=20),
        nullable=True
    )