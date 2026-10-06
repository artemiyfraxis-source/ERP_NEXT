"""Tables of the purchasing hybrid (migration 0089_purchasing_hybrid) and its unit of work.

Repositories live next to the feature that needs them (settings.py, suppliers.py, items.py, rfq.py); they take the
unit of work's session. Cross-module reads (procurement lines) go through app/workflows/purchasing_*.py."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

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
    """Explicit lot → item link (migration 0090): lots without the receiving chain, or a choice that overrides it."""

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
    """«Внутренний проект» (migration 0091): a project of the company itself, numbered «ВП-001»; requests and lines
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


QUOTE_SOURCES = ("manual", "paste", "file")


class QuoteRow(Base):
    __tablename__ = "purchasing_quotes"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'paste', 'file')", name="ck_purchasing_quotes_source"),
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
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # When the choice was written into the procurement line (supplier, quantity, unit).
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
