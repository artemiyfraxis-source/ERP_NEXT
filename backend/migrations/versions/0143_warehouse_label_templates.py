"""Шаблоны этикеток ГРН для конструктора (owner 23.10.2026: «надо переработать шаблон печати, желательно сделать
конструктор»).

One saved template per label kind × format: kind 'lot' (a warehouse lot) or 'intake' (an act of «Приёмка с
производства»), format '58x30' / '58x40' / '100x50'. No row — the built-in template of the code prints, so the labels
stay as they are until someone saves a template. `version` guards a save against a newer one (optimistic lock).

One new table, nothing existing is touched; not destructive. Downgrade drops it with the saved templates.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0143_warehouse_label_templates"
down_revision: str | None = "0142_production_vswr_measurements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The same constraints as `app.modules.warehouse.label_templates.LabelTemplateRow`.
TEMPLATE_CHECKS = {
    "ck_warehouse_label_templates_kind": "kind IN ('lot', 'intake')",
    "ck_warehouse_label_templates_format": "format IN ('58x30', '58x40', '100x50')",
    "ck_warehouse_label_templates_version": "version >= 1",
}


def upgrade() -> None:
    op.create_table(
        "warehouse_label_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("template", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default=""),
        sa.UniqueConstraint("kind", "format", name="uq_warehouse_label_templates_kind_format"),
        *(sa.CheckConstraint(text, name=name) for name, text in TEMPLATE_CHECKS.items()),
    )


def downgrade() -> None:
    op.drop_table("warehouse_label_templates")
