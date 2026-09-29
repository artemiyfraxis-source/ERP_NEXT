"""Shipment places, notes for the carrier and delivery-note requisites of the project card (PL/DN)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0050_shipping_boxes_requisites"
down_revision: str | None = "0049_references_erp_edit_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUISITES = (
    ("po", sa.String(255)), ("contract", sa.String(255)), ("spec", sa.String(255)),
    ("invoice", sa.String(255)), ("pays", sa.String(255)), ("customer_address", sa.Text()),
    ("hours", sa.String(255)),
)


def upgrade() -> None:
    op.create_table(
        "shipment_boxes",
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("place", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(255), nullable=False),
        sa.Column("dims", sa.String(255), nullable=False),
        sa.Column("net", sa.String(64), nullable=False),
        sa.Column("gross", sa.String(64), nullable=False),
        sa.Column("seal", sa.String(255), nullable=False),
        sa.Column("composition", sa.Text(), nullable=False),
    )
    with op.batch_alter_table("shipments") as batch:
        batch.add_column(sa.Column("tk_note", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("shipping_project_addressees") as batch:
        for name, kind in REQUISITES:
            batch.add_column(sa.Column(name, kind, nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("shipping_project_addressees") as batch:
        for name, _kind in reversed(REQUISITES):
            batch.drop_column(name)
    with op.batch_alter_table("shipments") as batch:
        batch.drop_column("tk_note")
    op.drop_table("shipment_boxes")
