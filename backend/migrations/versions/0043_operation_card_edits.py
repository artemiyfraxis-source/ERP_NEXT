"""Keep ERP edits of operation-card records over the library snapshot (SUPPLYMAP БД.ОП-ТК «ИЗМЕНЕНО» / «КЕМ»)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0043_operation_card_edits"
down_revision: str | None = "0042_shipping_project_addressees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("techcard_operation_templates") as batch:
        batch.add_column(sa.Column("edited", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("edited_by", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("techcard_operation_templates") as batch:
        batch.drop_column("edited_by")
        batch.drop_column("edited_at")
        batch.drop_column("edited")
