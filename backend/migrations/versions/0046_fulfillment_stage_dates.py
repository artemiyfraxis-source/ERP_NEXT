"""Fulfillment stage dates by the first hit: В РАБОТУ / НАБРАНА / ВЫДАНА / ВОЗВРАЩЕНА / ЗАКРЫТА (FF-4). Nullable: old requests keep no date, as a sheet row with an empty cell."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0046_fulfillment_stage_dates"
down_revision: str | None = "0045_production_box_luhn_check_digits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("at_work", "at_picked", "at_issued", "at_returned", "at_closed")


def upgrade() -> None:
    with op.batch_alter_table("fulfillment_requests") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fulfillment_requests") as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
