"""Запрос КП: свои позиции у каждого поставщика (стенд Вити, 12.10.2026).

Branch vitya/hybrid only. A supplier of a request for quotes may be asked for a part of its lines. No rows for a
request supplier = asked for every line of the request (the way it was), so older requests keep working.

Downgrade drops the table with its rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0136_rfq_supplier_lines"
down_revision: str | None = "0135_internal_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchasing_rfq_supplier_lines",
        sa.Column(
            "rfq_supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_rfq_suppliers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "rfq_line_id", sa.Uuid(), sa.ForeignKey("purchasing_rfq_lines.id", ondelete="CASCADE"), primary_key=True,
        ),
    )
    op.create_index("ix_purchasing_rfq_supplier_lines_line", "purchasing_rfq_supplier_lines", ["rfq_line_id"])


def downgrade() -> None:
    op.drop_table("purchasing_rfq_supplier_lines")
