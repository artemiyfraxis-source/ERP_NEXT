"""Receiving keeps the incoming-control act date: the «Ожидание» → «Карантин» timer counts from it."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0031_receiving_inspected_on"
down_revision: str | None = "0030_warehouse_lot_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        batch.add_column(sa.Column("inspected_on", sa.Date(), nullable=True))
    # Lines inspected before this column existed take the date of their last inspection from the audit.
    if op.get_bind().dialect.name == "sqlite":
        last_inspection = "date(MAX(audit_events.occurred_at))"
    else:
        last_inspection = "CAST(MAX(audit_events.occurred_at) AT TIME ZONE 'Europe/Moscow' AS DATE)"
    op.execute(
        f"""
        UPDATE receiving_lines SET inspected_on = (
            SELECT {last_inspection} FROM audit_events
            WHERE audit_events.aggregate_id = receiving_lines.id
              AND audit_events.event_type = 'receiving.inspected'
        )
        WHERE inspected_on IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_column("inspected_on")
