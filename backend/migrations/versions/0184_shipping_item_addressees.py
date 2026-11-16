"""Per-KB delivery overrides and opt-in shipment snapshots; preserve all historical data."""

from alembic import op
import sqlalchemy as sa

revision = "0184_shipping_item_addressees"
down_revision = "0183_material_catalogs_cutover"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    op.create_table(
        "shipping_item_addressees",
        sa.Column("project_key", sa.String(255), primary_key=True),
        sa.Column("item_index", sa.String(255), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("receiver", sa.String(512), nullable=False),
        sa.Column("shipping_point", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(128), nullable=False),
        sa.Column("terms", sa.String(32), nullable=True),
        sa.Column("carrier", sa.String(255), nullable=False),
        sa.Column("upd_type", sa.String(32), nullable=True),
        sa.Column("override_fields", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_shipping_item_addressees_version"),
        sa.CheckConstraint("project_key <> '' AND item_index <> ''", name="ck_shipping_item_addressees_key"),
    )
    op.add_column("shipments", sa.Column("delivery_snapshot_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM shipping_item_addressees")).scalar() or connection.execute(
        sa.text("SELECT count(*) FROM shipments WHERE delivery_snapshot_at IS NOT NULL"),
    ).scalar():
        raise RuntimeError("Cannot erase delivery overrides or snapshots; restore a prior copy instead.")
    op.drop_column("shipments", "delivery_snapshot_at")
    op.drop_table("shipping_item_addressees")
