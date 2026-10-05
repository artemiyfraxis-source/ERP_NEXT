"""Гибрид закупки (стенд Вити, 05.10.2026): настройки, поставщики, номенклатура, запросы КП и решения.

Branch vitya/hybrid only — the stage runs it on its own SQLite copy. New empty tables, nothing existing changes.
Module `purchasing` owns them; links to procurement lines are plain ids (no cross-module FK, as everywhere).
Money is Numeric like the procurement module; supplier_is_key on a quote is a snapshot taken at entry.

Downgrade drops the tables with their rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0089_purchasing_hybrid"
down_revision: str | None = "0088_api_tokens_assembly_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _who() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "purchasing_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    )

    op.create_table(
        "purchasing_suppliers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("inn", sa.String(12), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("is_key", sa.Boolean(), nullable=False),
        sa.Column("contact_person", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("site", sa.String(512), nullable=True),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_who(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("name_key", name="uq_purchasing_suppliers_name_key"),
        sa.UniqueConstraint("inn", name="uq_purchasing_suppliers_inn"),
        sa.CheckConstraint("inn IS NULL OR length(inn) IN (10, 12)", name="ck_purchasing_suppliers_inn_length"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_purchasing_suppliers_name"),
    )
    # The supplier text on procurement / receiving lines → the directory entry (one text, one supplier).
    op.create_table(
        "purchasing_supplier_names",
        sa.Column("name_key", sa.String(255), primary_key=True),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "purchasing_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("part_number", sa.String(255), nullable=True),
        sa.Column("part_key", sa.String(255), nullable=True),
        sa.Column("manufacturer", sa.String(255), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("item_type", sa.String(128), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_who(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("part_key", name="uq_purchasing_items_part_key"),
        sa.CheckConstraint("category IN ('cable', 'connector', 'ekb', 'other')", name="ck_purchasing_items_category"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_purchasing_items_name"),
    )
    # How a supplier (or anyone, supplier NULL) names the item; learned on every confirmed match.
    op.create_table(
        "purchasing_item_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("alias_key", sa.String(512), nullable=False),
        sa.Column("alias_text", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("supplier_id", "alias_key", name="uq_purchasing_item_aliases_supplier_key"),
    )
    op.create_table(
        "purchasing_line_items",
        sa.Column("procurement_line_id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("linked_by", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_purchasing_line_items_item", "purchasing_line_items", ["item_id"])

    op.create_table(
        "purchasing_rfqs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("buyer_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_who(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("number", name="uq_purchasing_rfqs_number"),
    )
    op.create_table(
        "purchasing_rfq_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rfq_id", sa.Uuid(), sa.ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("procurement_line_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("purchasing_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("article", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("need_by", sa.Date(), nullable=True),
        sa.Column("budget_unit_gross", sa.Numeric(18, 6), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_purchasing_rfq_lines_quantity"),
    )
    # A procurement line sits in at most one live request for quotes.
    op.create_index(
        "uq_purchasing_rfq_lines_live", "purchasing_rfq_lines", ["procurement_line_id"], unique=True,
        sqlite_where=sa.text("removed_at IS NULL"), postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_table(
        "purchasing_rfq_suppliers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rfq_id", sa.Uuid(), sa.ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_note", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rfq_id", "supplier_id", name="uq_purchasing_rfq_suppliers"),
        sa.CheckConstraint("reason IN ('key', 'category', 'rotation', 'plan', 'manual')", name="ck_purchasing_rfq_suppliers_reason"),
    )

    op.create_table(
        "purchasing_quotes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rfq_id", sa.Uuid(), sa.ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_is_key", sa.Boolean(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("delivery_cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("min_order_sum", sa.Numeric(18, 2), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("source IN ('manual', 'paste', 'file')", name="ck_purchasing_quotes_source"),
        sa.CheckConstraint("delivery_cost IS NULL OR delivery_cost >= 0", name="ck_purchasing_quotes_delivery"),
        sa.CheckConstraint("min_order_sum IS NULL OR min_order_sum >= 0", name="ck_purchasing_quotes_min_order"),
    )
    op.create_index("ix_purchasing_quotes_rfq", "purchasing_quotes", ["rfq_id", "supplier_id"])
    op.create_table(
        "purchasing_quote_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("quote_id", sa.Uuid(), sa.ForeignKey("purchasing_quotes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rfq_line_id", sa.Uuid(), sa.ForeignKey("purchasing_rfq_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("vat_included", sa.Boolean(), nullable=False),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("moq", sa.Numeric(18, 6), nullable=True),
        sa.Column("pack", sa.Numeric(18, 6), nullable=True),
        sa.Column("tiers", sa.JSON(), nullable=False),
        sa.Column("lead_days", sa.Integer(), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        sa.Column("declined", sa.Boolean(), nullable=False),
        sa.Column("analog", sa.Boolean(), nullable=False),
        sa.Column("analog_note", sa.String(512), nullable=True),
        sa.UniqueConstraint("quote_id", "rfq_line_id", name="uq_purchasing_quote_lines"),
        sa.CheckConstraint(
            "(declined AND unit_price IS NULL) OR (NOT declined AND unit_price > 0)", name="ck_purchasing_quote_lines_price",
        ),
        sa.CheckConstraint("moq IS NULL OR moq > 0", name="ck_purchasing_quote_lines_moq"),
        sa.CheckConstraint("pack IS NULL OR pack > 0", name="ck_purchasing_quote_lines_pack"),
        sa.CheckConstraint("lead_days IS NULL OR lead_days >= 0", name="ck_purchasing_quote_lines_lead"),
        sa.CheckConstraint("vat_rate >= 0 AND vat_rate < 100", name="ck_purchasing_quote_lines_vat"),
    )

    op.create_table(
        "purchasing_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rfq_line_id", sa.Uuid(), sa.ForeignKey("purchasing_rfq_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quote_line_id", sa.Uuid(), sa.ForeignKey("purchasing_quote_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cover_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_gross", sa.Numeric(18, 6), nullable=False),
        sa.Column("total_gross", sa.Numeric(20, 2), nullable=False),
        sa.Column("baseline_unit_gross", sa.Numeric(18, 6), nullable=True),
        sa.Column("cheapest_unit_gross", sa.Numeric(18, 6), nullable=True),
        sa.Column("priced_quotes", sa.Integer(), nullable=False),
        sa.Column("gate_override_reason", sa.Text(), nullable=True),
        sa.Column("not_cheapest_reason", sa.Text(), nullable=True),
        sa.Column("analog_approved_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("rfq_line_id", name="uq_purchasing_decisions_rfq_line"),
        sa.CheckConstraint("cover_quantity > 0 AND unit_gross > 0 AND total_gross > 0", name="ck_purchasing_decisions_amounts"),
    )


def downgrade() -> None:
    for table in (
        "purchasing_decisions", "purchasing_quote_lines", "purchasing_quotes", "purchasing_rfq_suppliers",
        "purchasing_rfq_lines", "purchasing_rfqs", "purchasing_line_items", "purchasing_item_aliases",
        "purchasing_items", "purchasing_supplier_names", "purchasing_suppliers", "purchasing_settings",
    ):
        op.drop_table(table)
