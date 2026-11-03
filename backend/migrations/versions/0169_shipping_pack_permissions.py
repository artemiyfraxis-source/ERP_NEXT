"""Preserve packed history and require explicit KB ceilings for all future packing."""

from datetime import datetime, timezone
from decimal import Decimal
import re

from alembic import op
import sqlalchemy as sa


revision = "0169_shipping_pack_permissions"
down_revision = "0168_requests_calc_compositions"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def _project_key(value):
    # Frozen ERP project identity rules; later application changes must not alter this migration.
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(
        str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ"),
    )
    if re.fullmatch(r"[1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?\.?", normalized):
        return normalized.rstrip(".") + ".1"
    return normalized


def upgrade():
    connection = op.get_bind()
    goods = sa.table("finished_goods", sa.column("project", sa.String), sa.column("item_index", sa.String),
                     sa.column("shipped", sa.Numeric(18, 6)))
    consumed = {}
    for project, item_index, shipped in connection.execute(sa.select(goods)):
        key = (_project_key(project), item_index)
        if not key[0] or not key[1]:
            raise RuntimeError("Packing history contains an empty project or KB; reconcile before migration.")
        amount = Decimal(shipped)
        if not amount.is_finite() or amount < 0:
            raise RuntimeError("Packing history contains an invalid quantity; reconcile before migration.")
        consumed[key] = consumed.get(key, Decimal("0")) + amount
        if consumed[key] > Decimal("999999999999.999999"):
            raise RuntimeError("Packing history exceeds the permission quantity precision.")
    permissions = op.create_table(
        "shipping_pack_permissions",
        sa.Column("project_key", sa.String(255), primary_key=True),
        sa.Column("item_index", sa.String(255), primary_key=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_shipping_pack_permissions_quantity"),
        sa.CheckConstraint("version >= 1", name="ck_shipping_pack_permissions_version"),
        sa.CheckConstraint("project_key <> '' AND item_index <> ''", name="ck_shipping_pack_permissions_key"),
    )
    now = datetime.now(timezone.utc)
    # shipped already includes sent. The old project boolean grants no additional quantity.
    # No finished goods, shipment, document, legacy flag, audit or outbox row is changed here.
    if consumed:
        connection.execute(permissions.insert(), [{
            "project_key": project, "item_index": item_index, "quantity": quantity, "version": 1,
            "updated_by": "Миграция 0169: уже упакованное", "updated_at": now,
        } for (project, item_index), quantity in sorted(consumed.items())])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM shipping_pack_permissions")).scalar():
        raise RuntimeError("Cannot erase packing permissions; restore a prior copy instead.")
    op.drop_table("shipping_pack_permissions")
