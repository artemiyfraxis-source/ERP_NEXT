"""Справочники «Технологии»: происхождение записи (ключ строки зеркала, правка в ERP) — перезагрузка зеркала не затирает правки."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0049_references_erp_edit_provenance"
down_revision: str | None = "0048_fulfillment_stage_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tech_operation_records") as batch:
        batch.add_column(sa.Column("mirror_key", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("edited_in_erp", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("tech_operation_records") as batch:
        batch.drop_column("edited_in_erp")
        batch.drop_column("mirror_key")
