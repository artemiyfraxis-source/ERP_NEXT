"""Record production-kit return disposition and good stock recovery."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_fulfillment_returns"
down_revision: str | None = "0006_fulfillment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fulfillment_requests",
        sa.Column("return_location", sa.String(length=255), nullable=True),
    )
    for name in ("returned_good", "defective_returned", "defective_missing"):
        op.add_column(
            "fulfillment_lines",
            sa.Column(name, sa.Numeric(18, 6), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in ("defective_missing", "defective_returned", "returned_good"):
        op.drop_column("fulfillment_lines", name)
    op.drop_column("fulfillment_requests", "return_location")
