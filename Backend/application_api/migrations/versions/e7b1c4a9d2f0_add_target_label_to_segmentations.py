from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e7b1c4a9d2f0"
down_revision = "5afcc11c5f5c"


def upgrade() -> None:
    op.add_column(
        "segmentations",
        sa.Column("target_label", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_segmentations_target_label",
        "segmentations",
        ["target_label"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_segmentations_target_label", table_name="segmentations")
    op.drop_column("segmentations", "target_label")
