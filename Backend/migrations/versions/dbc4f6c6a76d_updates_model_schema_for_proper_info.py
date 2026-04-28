from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'dbc4f6c6a76d'
down_revision = '006b8a8b4301'

def upgrade() -> None:
    # 1. Add 'modality' with a default value so existing rows don't crash
    op.add_column('models', sa.Column('modality', sa.String(), nullable=False, server_default='any'))
    
    # 2. Add 'anatomy'
    op.add_column('models', sa.Column('anatomy', sa.JSON(), nullable=True))
    
    # 3. Add 'params_schema'
    op.add_column('models', sa.Column('params_schema', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('models', 'params_schema')
    op.drop_column('models', 'anatomy')
    op.drop_column('models', 'modality')