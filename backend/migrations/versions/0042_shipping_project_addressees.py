"""Project card's shipping defaults: a shipment inherits receiver, point, contact, terms (SH-3)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0042_shipping_project_addressees"
down_revision: str | None = "0041_identity_own_sections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipping_project_addressees",
        sa.Column("project", sa.String(255), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("receiver", sa.String(512), nullable=False),
        sa.Column("shipping_point", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(128), nullable=False),
        sa.Column("terms", sa.String(32), nullable=True),
        sa.Column("carrier", sa.String(255), nullable=False),
        sa.Column("upd_type", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("shipping_project_addressees")
