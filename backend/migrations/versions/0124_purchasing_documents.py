"""Документы закупки из Roller_Buyer: реестр УПД, счета Kaiten, связи счёт ↔ УПД, справочные цены (стенд Вити, 13.10.2026).

Branch vitya/hybrid only. Tables of app/modules/purchasing/infrastructure.py («documents» block): files kept in the
database (one row per distinct content), UPD documents with their lines (parser's and the buyer's own), invoices with
their files, the buyer's confirmed / rejected invoice ↔ UPD links, reference prices of the price book. Filled by
`python -m app.modules.purchasing.rb_import` from a copy of the Roller_Buyer database.

Downgrade drops the tables with their rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0124_purchasing_documents"
down_revision: str | None = "0123_rfq_supplier_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _when(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "purchasing_document_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(1024), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        _when("created_at", nullable=False),
        sa.UniqueConstraint("sha256", name="uq_purchasing_document_files_sha256"),
    )
    op.create_table(
        "purchasing_upd_documents",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("file_name", sa.String(1024), nullable=False),
        sa.Column("folder", sa.String(255), nullable=False, server_default=""),
        sa.Column("md5", sa.String(64), nullable=False, server_default=""),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("purchasing_document_files.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False, server_default=""),
        sa.Column("tag", sa.String(64), nullable=False, server_default=""),
        sa.Column("issues", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("number", sa.String(128), nullable=False, server_default=""),
        sa.Column("doc_date", sa.Date()),
        sa.Column("seller_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("seller_inn", sa.String(32), nullable=False, server_default=""),
        sa.Column("seller_kpp", sa.String(32), nullable=False, server_default=""),
        sa.Column("buyer_inn", sa.String(32), nullable=False, server_default=""),
        sa.Column("basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment_doc", sa.Text(), nullable=False, server_default=""),
        sa.Column("total_net", sa.BigInteger()),
        sa.Column("total_vat", sa.BigInteger()),
        sa.Column("total_gross", sa.BigInteger()),
        sa.Column("file_amount", sa.BigInteger()),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dup_of", sa.String(128)),
        sa.Column("drive_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("parser_updated", sa.String(128), nullable=False, server_default=""),
        _when("synced_at", nullable=False),
        sa.Column("manual_header", sa.JSON()),
        _when("manual_saved_at"),
        sa.Column("manual_saved_by", sa.Uuid()),
        sa.Column("manual_based_on", sa.String(128)),
        _when("verified_at"),
        sa.Column("verified_by", sa.Uuid()),
        sa.Column("verified_parser_updated", sa.String(128)),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="SET NULL")),
        _when("supplier_manual_at"),
        sa.Column("supplier_manual_by", sa.Uuid()),
        sa.Column("project_reference", sa.String(255)),
        sa.Column("twin_of", sa.String(128)),
        _when("created_at", nullable=False),
        sa.CheckConstraint("origin IN ('google', 'edo')", name="ck_purchasing_upd_documents_origin"),
        sa.CheckConstraint("twin_of IS NULL OR twin_of <> id", name="ck_purchasing_upd_documents_twin"),
    )
    op.create_index("ix_purchasing_upd_documents_supplier", "purchasing_upd_documents", ["supplier_id"])
    op.create_index("ix_purchasing_upd_documents_seller_inn", "purchasing_upd_documents", ["seller_inn"])
    op.create_index("ix_purchasing_upd_documents_date", "purchasing_upd_documents", ["doc_date"])
    op.create_index("ix_purchasing_upd_documents_twin", "purchasing_upd_documents", ["twin_of"])
    op.create_table(
        "purchasing_upd_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id", sa.String(128), sa.ForeignKey("purchasing_upd_documents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("article", sa.String(255), nullable=False, server_default=""),
        sa.Column("article_key", sa.String(255)),
        sa.Column("name_key", sa.String(512), nullable=False, server_default=""),
        sa.Column("product_type_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("unit_code", sa.String(16), nullable=False, server_default=""),
        sa.Column("unit", sa.String(64), nullable=False, server_default=""),
        sa.Column("quantity", sa.Numeric(20, 6)),
        sa.Column("price", sa.Numeric(20, 6)),
        sa.Column("net", sa.BigInteger()),
        sa.Column("excise", sa.String(64), nullable=False, server_default=""),
        sa.Column("vat_rate", sa.String(16), nullable=False, server_default=""),
        sa.Column("vat", sa.BigInteger()),
        sa.Column("gross", sa.BigInteger()),
        sa.Column("country_code", sa.String(16), nullable=False, server_default=""),
        sa.Column("country", sa.String(128), nullable=False, server_default=""),
        sa.Column("declaration", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_service", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("issues", sa.Text(), nullable=False, server_default=""),
        sa.Column("fixes", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint("source IN ('google', 'manual')", name="ck_purchasing_upd_lines_source"),
        sa.UniqueConstraint("document_id", "source", "seq", name="uq_purchasing_upd_lines_seq"),
    )
    op.create_index("ix_purchasing_upd_lines_article_key", "purchasing_upd_lines", ["article_key"])
    op.create_index("ix_purchasing_upd_lines_name_key", "purchasing_upd_lines", ["name_key"])
    op.create_table(
        "purchasing_invoices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("board_title", sa.String(255), nullable=False, server_default=""),
        sa.Column("column_title", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        _when("card_created", nullable=False),
        _when("card_updated", nullable=False),
        sa.Column("number", sa.String(128)),
        sa.Column("doc_date", sa.Date()),
        sa.Column("seller_name", sa.String(512)),
        sa.Column("seller_inn", sa.String(32)),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="SET NULL")),
        sa.Column("total", sa.BigInteger()),
        sa.Column("vat", sa.BigInteger()),
        sa.Column("vat_free", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payment_numbers", sa.String(512), nullable=False, server_default=""),
        sa.Column("payment_date", sa.Date()),
        sa.Column("payment_sum", sa.BigInteger()),
        sa.Column("parsed_file", sa.String(64)),
        sa.Column("parse_issues", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("lines", sa.JSON(), nullable=False),
        _when("synced_at", nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_purchasing_invoices_source_external"),
        sa.CheckConstraint("source IN ('kaiten')", name="ck_purchasing_invoices_source"),
        sa.CheckConstraint("status IN ('to_pay', 'paid', 'return', 'other')", name="ck_purchasing_invoices_status"),
    )
    op.create_index("ix_purchasing_invoices_supplier", "purchasing_invoices", ["supplier_id"])
    op.create_index("ix_purchasing_invoices_date", "purchasing_invoices", ["doc_date"])
    op.create_table(
        "purchasing_invoice_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("purchasing_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("purchasing_document_files.id", ondelete="SET NULL")),
        sa.UniqueConstraint("invoice_id", "external_id", name="uq_purchasing_invoice_files"),
        sa.CheckConstraint("kind IN ('invoice', 'payment', 'other')", name="ck_purchasing_invoice_files_kind"),
    )
    op.create_table(
        "purchasing_upd_invoice_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "upd_id", sa.String(128), sa.ForeignKey("purchasing_upd_documents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("purchasing_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        _when("decided_at", nullable=False),
        sa.Column("decided_by", sa.Uuid()),
        sa.UniqueConstraint("upd_id", "invoice_id", name="uq_purchasing_upd_invoice_links"),
        sa.CheckConstraint("decision IN ('confirmed', 'rejected')", name="ck_purchasing_upd_invoice_links_decision"),
    )
    op.create_index("ix_purchasing_upd_invoice_links_invoice", "purchasing_upd_invoice_links", ["invoice_id"])
    op.create_table(
        "purchasing_price_refs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="SET NULL")),
        sa.Column("supplier_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("offered_part", sa.String(255), nullable=False, server_default=""),
        sa.Column("offered_manufacturer", sa.String(255), nullable=False, server_default=""),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("unit_price4", sa.BigInteger(), nullable=False),
        sa.Column("vat_included", sa.Boolean(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6)),
        sa.Column("lead_time", sa.String(128), nullable=False, server_default=""),
        sa.Column("source", sa.String(512), nullable=False),
        sa.Column("source_row", sa.Integer()),
        _when("imported_at", nullable=False),
        sa.CheckConstraint("currency IN ('RUB', 'CNY', 'USD')", name="ck_purchasing_price_refs_currency"),
        sa.CheckConstraint("unit_price4 > 0", name="ck_purchasing_price_refs_price"),
    )
    op.create_index("ix_purchasing_price_refs_item", "purchasing_price_refs", ["item_id"])
    op.create_index("ix_purchasing_price_refs_source", "purchasing_price_refs", ["source"])


def downgrade() -> None:
    for table in (
        "purchasing_price_refs", "purchasing_upd_invoice_links", "purchasing_invoice_files", "purchasing_invoices",
        "purchasing_upd_lines", "purchasing_upd_documents", "purchasing_document_files",
    ):
        op.drop_table(table)
