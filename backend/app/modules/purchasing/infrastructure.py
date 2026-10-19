"""Tables of the purchasing hybrid (migration 0120_purchasing_hybrid) and its unit of work.

Repositories live next to the feature that needs them (settings.py, suppliers.py, items.py, rfq.py); they take the
unit of work's session. Cross-module reads (procurement lines) go through app/workflows/purchasing_*.py."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, Session, deferred, mapped_column, sessionmaker

from app.shared.persistence import Base, SqlAlchemyAuditRepository


class _Who:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class PurchasingSettingRow(Base):
    __tablename__ = "purchasing_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class SupplierRow(_Who, Base):
    __tablename__ = "purchasing_suppliers"
    __table_args__ = (
        UniqueConstraint("name_key", name="uq_purchasing_suppliers_name_key"),
        UniqueConstraint("inn", name="uq_purchasing_suppliers_inn"),
        CheckConstraint("inn IS NULL OR length(inn) IN (10, 12)", name="ck_purchasing_suppliers_inn_length"),
        CheckConstraint("length(trim(name)) > 0", name="ck_purchasing_suppliers_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    inn: Mapped[str | None] = mapped_column(String(12))
    categories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contact_person: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    site: Mapped[str | None] = mapped_column(String(512))
    domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SupplierNameRow(Base):
    __tablename__ = "purchasing_supplier_names"

    name_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


ITEM_CATEGORIES = ("cable", "connector", "ekb", "other")


class ItemRow(_Who, Base):
    __tablename__ = "purchasing_items"
    __table_args__ = (
        UniqueConstraint("part_key", name="uq_purchasing_items_part_key"),
        CheckConstraint("category IN ('cable', 'connector', 'ekb', 'other')", name="ck_purchasing_items_category"),
        CheckConstraint("length(trim(name)) > 0", name="ck_purchasing_items_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    part_number: Mapped[str | None] = mapped_column(String(255))
    part_key: Mapped[str | None] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    item_type: Mapped[str | None] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ItemAliasRow(Base):
    __tablename__ = "purchasing_item_aliases"
    __table_args__ = (UniqueConstraint("supplier_id", "alias_key", name="uq_purchasing_item_aliases_supplier_key"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="CASCADE"))
    alias_key: Mapped[str] = mapped_column(String(512), nullable=False)
    alias_text: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class LineItemRow(Base):
    __tablename__ = "purchasing_line_items"
    __table_args__ = (Index("ix_purchasing_line_items_item", "item_id"),)

    procurement_line_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    linked_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))



class LotItemRow(Base):
    """Explicit lot → item link (migration 0121): lots without the receiving chain, or a choice that overrides it."""

    __tablename__ = "purchasing_lot_items"
    __table_args__ = (
        CheckConstraint("source IN ('match', 'manual')", name="ck_purchasing_lot_items_source"),
        Index("ix_purchasing_lot_items_item", "item_id"),
    )

    lot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    linked_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class InternalProjectRow(_Who, Base):
    """«Внутренний проект» (migration 0122): a project of the company itself, numbered «ВП-001»; requests and lines
    refer to it through `project_reference` like to a commercial «591К.3»; its purpose decides the GRN prefix."""

    __tablename__ = "purchasing_internal_projects"
    __table_args__ = (
        UniqueConstraint("number", name="uq_purchasing_internal_projects_number"),
        CheckConstraint("number LIKE 'ВП-%'", name="ck_purchasing_internal_projects_number"),
        CheckConstraint("length(trim(name)) > 0", name="ck_purchasing_internal_projects_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    responsible_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

class RfqRow(_Who, Base):
    __tablename__ = "purchasing_rfqs"
    __table_args__ = (UniqueConstraint("number", name="uq_purchasing_rfqs_number"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    buyer_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RfqLineRow(Base):
    __tablename__ = "purchasing_rfq_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_purchasing_rfq_lines_quantity"),
        CheckConstraint("removed_reason IS NULL OR removed_reason IN ('new', 'requote')", name="ck_purchasing_rfq_lines_removed_reason"),
        CheckConstraint("removed_reason IS NULL OR removed_at IS NOT NULL", name="ck_purchasing_rfq_lines_removed"),
        Index(
            "uq_purchasing_rfq_lines_live", "procurement_line_id", unique=True,
            sqlite_where=text("removed_at IS NULL"), postgresql_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rfq_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False)
    procurement_line_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_items.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    article: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    need_by: Mapped[date | None] = mapped_column(Date)
    budget_unit_gross: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Why the line left (migration 0126): «new» — back to the free lines, «requote» — «Перезапрос»; NULL with removed_at
    # — closed with the request (read as «Перезапрос», as before). The note says who answered what.
    removed_reason: Mapped[str | None] = mapped_column(String(16))
    removed_note: Mapped[str | None] = mapped_column(Text)
    removed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


RFQ_SUPPLIER_REASONS = ("key", "category", "rotation", "plan", "manual")


class RfqSupplierRow(Base):
    __tablename__ = "purchasing_rfq_suppliers"
    __table_args__ = (
        UniqueConstraint("rfq_id", "supplier_id", name="uq_purchasing_rfq_suppliers"),
        CheckConstraint("reason IN ('key', 'category', 'rotation', 'plan', 'manual')", name="ck_purchasing_rfq_suppliers_reason"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rfq_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="RESTRICT"), nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_note: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)



class RfqSupplierLineRow(Base):
    """A line a supplier of a request is asked for (migration 0123). No rows for a request supplier = every line."""

    __tablename__ = "purchasing_rfq_supplier_lines"
    __table_args__ = (Index("ix_purchasing_rfq_supplier_lines_line", "rfq_line_id"),)

    rfq_supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_rfq_suppliers.id", ondelete="CASCADE"), primary_key=True,
    )
    rfq_line_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_rfq_lines.id", ondelete="CASCADE"), primary_key=True,
    )

QUOTE_SOURCES = ("manual", "paste", "file", "invoice")


class RfqDeclinedSuggestionRow(Base):
    """A rotation suggestion the buyer unticked when creating the request (migration 0126, Roller declinedSuggestion):
    the supplier was offered a turn and passed — not asked, but on record."""

    __tablename__ = "purchasing_rfq_declined_suggestions"
    __table_args__ = (UniqueConstraint("rfq_id", "supplier_id", name="uq_purchasing_rfq_declined_suggestions"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rfq_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="RESTRICT"), nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class QuoteRow(Base):
    __tablename__ = "purchasing_quotes"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'paste', 'file', 'invoice')", name="ck_purchasing_quotes_source"),
        CheckConstraint("source <> 'invoice' OR invoice_id IS NOT NULL", name="ck_purchasing_quotes_invoice"),
        Index("ix_purchasing_quotes_invoice", "invoice_id"),
        CheckConstraint("delivery_cost IS NULL OR delivery_cost >= 0", name="ck_purchasing_quotes_delivery"),
        CheckConstraint("min_order_sum IS NULL OR min_order_sum >= 0", name="ck_purchasing_quotes_min_order"),
        Index("ix_purchasing_quotes_rfq", "rfq_id", "supplier_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rfq_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="RESTRICT"), nullable=False,
    )
    # Snapshot at entry: KPI baselines never follow the supplier's current «ключевой» flag.
    supplier_is_key: Mapped[bool] = mapped_column(Boolean, nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    delivery_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    min_order_sum: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    file_name: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # Migration 0126: «Номер КП», the PDF it came in (purchasing_document_files), the Kaiten invoice it was made from.
    number: Mapped[str | None] = mapped_column(String(64))
    file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_document_files.id", ondelete="SET NULL"))
    invoice_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_invoices.id", ondelete="SET NULL"))


class QuoteLineRow(Base):
    __tablename__ = "purchasing_quote_lines"
    __table_args__ = (
        UniqueConstraint("quote_id", "rfq_line_id", name="uq_purchasing_quote_lines"),
        CheckConstraint(
            "(declined AND unit_price IS NULL) OR (NOT declined AND unit_price > 0)", name="ck_purchasing_quote_lines_price",
        ),
        CheckConstraint("moq IS NULL OR moq > 0", name="ck_purchasing_quote_lines_moq"),
        CheckConstraint("pack IS NULL OR pack > 0", name="ck_purchasing_quote_lines_pack"),
        CheckConstraint("lead_days IS NULL OR lead_days >= 0", name="ck_purchasing_quote_lines_lead"),
        CheckConstraint("vat_rate >= 0 AND vat_rate < 100", name="ck_purchasing_quote_lines_vat"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    quote_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_quotes.id", ondelete="CASCADE"), nullable=False)
    rfq_line_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_rfq_lines.id", ondelete="CASCADE"), nullable=False,
    )
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    vat_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    moq: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    pack: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    # Price breaks: [{"from": "1000", "price": "10.5"}, …] in the line's VAT mode.
    tiers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    lead_days: Mapped[int | None] = mapped_column(Integer)
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    declined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    analog: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    analog_note: Mapped[str | None] = mapped_column(String(512))


class DecisionRow(Base):
    __tablename__ = "purchasing_decisions"
    __table_args__ = (
        UniqueConstraint("rfq_line_id", name="uq_purchasing_decisions_rfq_line"),
        CheckConstraint("cover_quantity > 0 AND unit_gross > 0 AND total_gross > 0", name="ck_purchasing_decisions_amounts"),
        CheckConstraint(
            "gate_override_code IS NULL OR gate_override_code IN ('urgent', 'single_source', 'repeat_fresh_quote', 'other')",
            name="ck_purchasing_decisions_gate_code",
        ),
        CheckConstraint(
            "not_cheapest_code IS NULL OR not_cheapest_code IN ('lead_time', 'availability', 'moq', 'quality', 'other')",
            name="ck_purchasing_decisions_pricier_code",
        ),
        CheckConstraint("gate_override_code IS NULL OR gate_override_reason IS NOT NULL", name="ck_purchasing_decisions_gate_text"),
        CheckConstraint("not_cheapest_code IS NULL OR not_cheapest_reason IS NOT NULL", name="ck_purchasing_decisions_pricier_text"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rfq_line_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_rfq_lines.id", ondelete="CASCADE"), nullable=False,
    )
    quote_line_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchasing_quote_lines.id", ondelete="RESTRICT"), nullable=False,
    )
    cover_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_gross: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    baseline_unit_gross: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    cheapest_unit_gross: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    priced_quotes: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_override_reason: Mapped[str | None] = mapped_column(Text)
    not_cheapest_reason: Mapped[str | None] = mapped_column(Text)
    analog_approved_by: Mapped[str | None] = mapped_column(String(255))
    # Migration 0126: Roller's fixed reason lists (shared/decisions.ts); the *_reason text is the label or, for
    # «other», the buyer's own words.
    gate_override_code: Mapped[str | None] = mapped_column(String(32))
    not_cheapest_code: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # When the choice was written into the procurement line (supplier, quantity, unit).
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- documents (migration 0124): the UPD registry, Kaiten invoices, the buyer's invoice ↔ UPD word, price refs ----
# Ported from Roller_Buyer (upd_documents, upd_lines, invoices, upd_invoice_links, price_refs). Money in kopecks
# (BigInteger) as there; unit prices of UPD lines in rubles as printed. Files live in the database like every other
# ERP upload (app/shared/stored_files.py), one row per distinct content.

UPD_ORIGINS = ("google", "edo")
UPD_LINE_SOURCES = ("google", "manual")
INVOICE_STATUSES = ("to_pay", "paid", "return", "other")
INVOICE_FILE_KINDS = ("invoice", "payment", "other")
LINK_DECISIONS = ("confirmed", "rejected")
PRICE_CURRENCIES = ("RUB", "CNY", "USD")


class DocumentFileRow(Base):
    __tablename__ = "purchasing_document_files"
    __table_args__ = (UniqueConstraint("sha256", name="uq_purchasing_document_files_sha256"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UpdDocumentRow(Base):
    """One closing document: a PDF read by the Google parser (key = Drive file id) or an EDO XML (key «edo-<sha1 of
    ИдФайл>»). Parser fields are the parser's word; the buyer's corrections live in manual_header / manual lines and
    win on read. twin_of — the same document came twice (PDF and XML, XML resent): the copy points at the one in force."""

    __tablename__ = "purchasing_upd_documents"
    __table_args__ = (
        CheckConstraint("origin IN ('google', 'edo')", name="ck_purchasing_upd_documents_origin"),
        CheckConstraint("twin_of IS NULL OR twin_of <> id", name="ck_purchasing_upd_documents_twin"),
        Index("ix_purchasing_upd_documents_supplier", "supplier_id"),
        Index("ix_purchasing_upd_documents_seller_inn", "seller_inn"),
        Index("ix_purchasing_upd_documents_date", "doc_date"),
        Index("ix_purchasing_upd_documents_twin", "twin_of"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    file_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    folder: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    md5: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_document_files.id", ondelete="SET NULL"))
    # The parser's verdict as Roller_Buyer shows it: «Разобран», «Требует проверки», «Дубликат», «Не документ закупки».
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tag: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    issues: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    doc_date: Mapped[date | None] = mapped_column(Date)
    seller_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    seller_inn: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    seller_kpp: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    buyer_inn: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    basis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payment_doc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    total_net: Mapped[int | None] = mapped_column(BigInteger)
    total_vat: Mapped[int | None] = mapped_column(BigInteger)
    total_gross: Mapped[int | None] = mapped_column(BigInteger)
    file_amount: Mapped[int | None] = mapped_column(BigInteger)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dup_of: Mapped[str | None] = mapped_column(String(128))
    drive_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # When the parser last changed the document (Google: Drive modified time; EDO: «<time>|<md5>»).
    parser_updated: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # {number, date, seller_name, seller_inn, seller_kpp, buyer_inn, total_net, total_vat, total_gross}
    manual_header: Mapped[dict | None] = mapped_column(JSON)
    manual_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_saved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # parser_updated the correction was made against: a newer parse is shown to the buyer.
    manual_based_on: Mapped[str | None] = mapped_column(String(128))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    verified_parser_updated: Mapped[str | None] = mapped_column(String(128))
    supplier_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="SET NULL"))
    # Set when the buyer chose the supplier by hand: every relink keeps it; NULL — linked by the seller's INN.
    supplier_manual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supplier_manual_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # «591К.3» or «ВП-001», set by hand.
    project_reference: Mapped[str | None] = mapped_column(String(255))
    twin_of: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UpdLineRow(Base):
    __tablename__ = "purchasing_upd_lines"
    __table_args__ = (
        CheckConstraint("source IN ('google', 'manual')", name="ck_purchasing_upd_lines_source"),
        UniqueConstraint("document_id", "source", "seq", name="uq_purchasing_upd_lines_seq"),
        Index("ix_purchasing_upd_lines_article_key", "article_key"),
        Index("ix_purchasing_upd_lines_name_key", "name_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("purchasing_upd_documents.id", ondelete="CASCADE"), nullable=False,
    )
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    article: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Derived on write, for lookups by item: items.part_key(article) and items.alias_key(name).
    article_key: Mapped[str | None] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    product_type_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    unit_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    # Unit price without VAT in rubles (may carry 4 decimals); net / vat / gross in kopecks.
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    net: Mapped[int | None] = mapped_column(BigInteger)
    excise: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    vat_rate: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    vat: Mapped[int | None] = mapped_column(BigInteger)
    gross: Mapped[int | None] = mapped_column(BigInteger)
    country_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    declaration: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_service: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    issues: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fixes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class InvoiceRow(Base):
    """A supplier's invoice mirrored from a Kaiten card of «Закупки» (one card = one invoice + payment orders). The
    header is read from the invoice PDF's text layer; lines are the positions of its table."""

    __tablename__ = "purchasing_invoices"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_purchasing_invoices_source_external"),
        CheckConstraint("source IN ('kaiten')", name="ck_purchasing_invoices_source"),
        CheckConstraint("status IN ('to_pay', 'paid', 'return', 'other')", name="ck_purchasing_invoices_status"),
        Index("ix_purchasing_invoices_supplier", "supplier_id"),
        Index("ix_purchasing_invoices_date", "doc_date"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    board_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    column_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    card_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    card_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    number: Mapped[str | None] = mapped_column(String(128))
    doc_date: Mapped[date | None] = mapped_column(Date)
    seller_name: Mapped[str | None] = mapped_column(String(512))
    seller_inn: Mapped[str | None] = mapped_column(String(32))
    supplier_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="SET NULL"))
    total: Mapped[int | None] = mapped_column(BigInteger)
    vat: Mapped[int | None] = mapped_column(BigInteger)
    vat_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_numbers: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    payment_date: Mapped[date | None] = mapped_column(Date)
    payment_sum: Mapped[int | None] = mapped_column(BigInteger)
    parsed_file: Mapped[str | None] = mapped_column(String(64))
    parse_issues: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The invoice PDF's text layer, for search; empty for scans.
    text: Mapped[str] = deferred(mapped_column(Text, nullable=False, default=""))
    # Positions of the invoice table: [{seq, name, article, qty, unit, price, sum}] (Roller_Buyer InvoiceLine).
    lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InvoiceFileRow(Base):
    __tablename__ = "purchasing_invoice_files"
    __table_args__ = (
        UniqueConstraint("invoice_id", "external_id", name="uq_purchasing_invoice_files"),
        CheckConstraint("kind IN ('invoice', 'payment', 'other')", name="ck_purchasing_invoice_files_kind"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_invoices.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # None — the file was not downloaded.
    file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_document_files.id", ondelete="SET NULL"))


class UpdInvoiceLinkRow(Base):
    """The buyer's word on «this UPD closes that invoice»: confirmed links always, rejected never. Every other link is
    computed on read from the documents' own data."""

    __tablename__ = "purchasing_upd_invoice_links"
    __table_args__ = (
        UniqueConstraint("upd_id", "invoice_id", name="uq_purchasing_upd_invoice_links"),
        CheckConstraint("decision IN ('confirmed', 'rejected')", name="ck_purchasing_upd_invoice_links_decision"),
        Index("ix_purchasing_upd_invoice_links_invoice", "invoice_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    upd_id: Mapped[str] = mapped_column(String(128), ForeignKey("purchasing_upd_documents.id", ondelete="CASCADE"), nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_invoices.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class PriceRefRow(Base):
    """A reference price from a price book or an old quote collection: who offered what for how much, without a
    request for quotes behind it. Re-importing a source replaces its rows. The price is kept in the source currency
    as unit × 10 000 — a Chinese connector costs 0.0177 CNY, which kopecks cannot hold."""

    __tablename__ = "purchasing_price_refs"
    __table_args__ = (
        CheckConstraint("currency IN ('RUB', 'CNY', 'USD')", name="ck_purchasing_price_refs_currency"),
        CheckConstraint("unit_price4 > 0", name="ck_purchasing_price_refs_price"),
        Index("ix_purchasing_price_refs_item", "item_id"),
        Index("ix_purchasing_price_refs_source", "source"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("purchasing_suppliers.id", ondelete="SET NULL"))
    supplier_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    offered_part: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    offered_manufacturer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_price4: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vat_included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    lead_time: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    source_row: Mapped[int | None] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyPurchasingUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyPurchasingUnitOfWork:
        self.committed = False
        self.session = self.session_factory()
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self.committed:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()
        self.committed = True
