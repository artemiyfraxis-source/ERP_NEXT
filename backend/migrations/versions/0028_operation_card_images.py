"""Carry SUPPLYMAP template image anchors (row/col/driveFileId/width/height) on operation-card templates."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0028_operation_card_images"
down_revision: str | None = "0027_warehouse_location_as_is"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("techcard_operation_templates") as batch:
        batch.add_column(sa.Column("images", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("techcard_operation_templates") as batch:
        batch.drop_column("images")
