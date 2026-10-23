"""SQLAlchemy persistence adapters for the Procurement bounded context."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import (
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
    Uuid,
    and_,
    delete as sql_delete,
    false,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker, validates

from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number
from app.shared.persistence import (
    Base,
    SqlAlchemyAuditRepository,
    SqlAlchemyOutboxRepository,
    check_read_version,
    note_written_version,
    track_read_versions,
)

from .application import StatusNotificationDue, StoredInvoiceFile, SupplierOrder, SupplierOrderDocument
from .domain import (
    ProcurementLine,
    ProcurementStatus,
    ProcurementTraceability,
    ReplacementDecision,
    StockCover,
    designation_key,
)
from .money import FactMoney


class ProcurementLineRow(Base):
    __tablename__ = "procurement_lines"
    __table_args__ = (
        Index("ix_procurement_status_created_id", "status", "created_at", "id"),
        Index(
            "ix_procurement_buyer_status_created_id",
            "buyer_id",
            "status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    source_idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    source_request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    legacy_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    revision: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    request_date: Mapped[Any] = mapped_column(Date, nullable=False)
    initiator: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    designation: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_reference: Mapped[str | None] = mapped_column(String(128), index=True)
    priority: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_point: Mapped[str] = mapped_column(String(512), nullable=False)
    planned_date: Mapped[Any] = mapped_column(Date, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[str] = mapped_column(String(255), nullable=False)
    design_article: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    calculated_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    calculated_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    calculated_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_document: Mapped[str] = mapped_column(String(8), nullable=False, server_default="Нет")
    calculated_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    calculated_price_with_vat: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    calculated_sum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    buyer_name: Mapped[str | None] = mapped_column(String(255))
    buyer_department: Mapped[str | None] = mapped_column(String(255))
    buyer_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    supplier: Mapped[str | None] = mapped_column(String(512))
    site_name: Mapped[str | None] = mapped_column(String(512))
    actual_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_unit: Mapped[str | None] = mapped_column(String(32))
    supplier_request_reference: Mapped[str | None] = mapped_column(Text)
    expected_arrival: Mapped[Any | None] = mapped_column(Date)
    invoice_url: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str | None] = mapped_column(String(8))
    actual_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    actual_price_with_vat: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_sum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    money_driver: Mapped[str | None] = mapped_column(String(8))
    source_currency: Mapped[str | None] = mapped_column(String(8))
    source_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_price_with_vat: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_sum: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    exchange_rate_date: Mapped[Any | None] = mapped_column(Date)
    exchange_rate_manual: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    invoice_item_name: Mapped[str | None] = mapped_column(String(1024))
    invoice_number: Mapped[str | None] = mapped_column(String(128))
    invoice_date: Mapped[Any | None] = mapped_column(Date)
    order_number: Mapped[str | None] = mapped_column(String(32), index=True)
    order_date: Mapped[Any | None] = mapped_column(Date)
    replacement: Mapped[bool | None] = mapped_column(Boolean)
    replacement_decision: Mapped[str | None] = mapped_column(String(16))
    permission_number: Mapped[str | None] = mapped_column(String(32), index=True)
    permission_date: Mapped[Any | None] = mapped_column(Date)
    initiator_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(128))
    quality_document_type: Mapped[str | None] = mapped_column(String(128))
    quality_document_number: Mapped[str | None] = mapped_column(String(128))
    quality_document_date: Mapped[Any | None] = mapped_column(Date)
    standard: Mapped[str | None] = mapped_column(String(255))
    # The last status the initiator was notified of (OwnerTick.gs notified_<id>); owned by the sweep.
    notified_status: Mapped[str | None] = mapped_column(String(32))
    # «Со склада»: the part of КОЛ-ВО (Расчет) our lots reserve for the line (procurement_stock_covers).
    stock_covered: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0")
    # КОММЕНТАРИЙ (the buyer's note) and ФАКТ. ДАТА (latest inspection of the line's deliveries, set by the tick).
    note: Mapped[str | None] = mapped_column(Text)
    actual_arrival: Mapped[Any | None] = mapped_column(Date)
    # DATASHEET: a link or text the buyer keeps on the line.
    datasheet: Mapped[str | None] = mapped_column(Text)
    # № ПРОЕКТА строки для сравнения (`normalize_project_number`; «» — без проекта): строки проекта по индексу.
    # Ставится при записи `project_reference`; NULL — строка мимо ORM (читается и сверяется в Python).
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": False,
    }

    @validates("project_reference")
    def _keep_project_key(self, _name: str, value: str | None) -> str | None:
        self.project_key = normalize_project_number(value) if value else ""
        return value


def _line_row(line: ProcurementLine) -> ProcurementLineRow:
    return ProcurementLineRow(
        id=line.id,
        source_event_id=line.source_event_id,
        source_idempotency_key=line.source_idempotency_key,
        source_request_id=line.source_request_id,
        legacy_id=line.legacy_id,
        revision=line.revision,
        status=line.status.value,
        version=line.version,
        created_at=line.created_at,
        request_date=line.request_date,
        initiator=line.initiator,
        department=line.department,
        task_type=line.task_type,
        designation=line.designation,
        project_reference=line.project_reference,
        priority=line.priority,
        delivery_point=line.delivery_point,
        planned_date=line.planned_date,
        link=line.link,
        item_type=line.item_type,
        design_article=line.design_article,
        category=line.category,
        calculated_name=line.calculated_name,
        calculated_quantity=line.calculated_quantity,
        calculated_unit=line.calculated_unit,
        quality_document=line.quality_document,
        calculated_price=line.calculated_price,
        calculated_price_with_vat=line.calculated_price_with_vat,
        calculated_sum=line.calculated_sum,
        buyer_name=line.buyer.name if line.buyer else None,
        buyer_department=line.buyer.department if line.buyer else None,
        buyer_id=line.buyer.id if line.buyer else None,
        supplier=line.supplier,
        site_name=line.site_name,
        actual_quantity=line.actual_quantity,
        actual_unit=line.actual_unit,
        supplier_request_reference=line.supplier_request_reference,
        expected_arrival=line.expected_arrival,
        invoice_url=line.invoice_url,
        paid_at=line.paid_at,
        currency=line.money.currency,
        actual_price=line.money.price,
        actual_vat_rate=line.money.vat_rate,
        actual_price_with_vat=line.money.price_with_vat,
        actual_sum=line.money.sum,
        money_driver=line.money.driver,
        source_currency=line.money.source_currency,
        source_price=line.money.source_price,
        source_price_with_vat=line.money.source_price_with_vat,
        source_sum=line.money.source_sum,
        exchange_rate=line.money.exchange_rate,
        exchange_rate_date=line.money.exchange_rate_date,
        exchange_rate_manual=line.money.exchange_rate_manual,
        invoice_item_name=line.invoice_item_name,
        invoice_number=line.invoice_number,
        invoice_date=line.invoice_date,
        order_number=line.order_number,
        order_date=line.order_date,
        replacement=line.replacement,
        replacement_decision=(
            line.replacement_decision.value if line.replacement_decision else None
        ),
        permission_number=line.permission_number,
        permission_date=line.permission_date,
        initiator_id=line.initiator_id,
        manufacturer=line.trace.manufacturer,
        country=line.trace.country,
        quality_document_type=line.trace.quality_document_type,
        quality_document_number=line.trace.quality_document_number,
        quality_document_date=line.trace.quality_document_date,
        standard=line.trace.standard,
        stock_covered=line.stock_covered,
        note=line.note,
        actual_arrival=line.actual_arrival,
        datasheet=line.datasheet,
    )


def _line_domain(row: ProcurementLineRow) -> ProcurementLine:
    buyer = None
    if row.buyer_name:
        buyer = Actor(
            id=row.buyer_id,
            name=row.buyer_name,
            department=row.buyer_department or "",
        )
    return ProcurementLine(
        id=row.id,
        source_event_id=row.source_event_id,
        source_idempotency_key=row.source_idempotency_key,
        source_request_id=row.source_request_id,
        legacy_id=row.legacy_id,
        revision=row.revision,
        status=ProcurementStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        request_date=row.request_date,
        initiator=row.initiator,
        department=row.department,
        task_type=row.task_type,
        designation=row.designation,
        project_reference=row.project_reference,
        priority=row.priority,
        delivery_point=row.delivery_point,
        planned_date=row.planned_date,
        link=row.link,
        item_type=row.item_type,
        design_article=row.design_article,
        category=row.category,
        calculated_name=row.calculated_name,
        calculated_quantity=row.calculated_quantity,
        calculated_unit=row.calculated_unit,
        quality_document=row.quality_document,
        calculated_price=row.calculated_price,
        calculated_price_with_vat=row.calculated_price_with_vat,
        calculated_sum=row.calculated_sum,
        buyer=buyer,
        supplier=row.supplier,
        site_name=row.site_name,
        actual_quantity=row.actual_quantity,
        actual_unit=row.actual_unit,
        supplier_request_reference=row.supplier_request_reference,
        expected_arrival=row.expected_arrival,
        invoice_url=row.invoice_url,
        paid_at=row.paid_at,
        money=FactMoney(
            currency=row.currency,
            price=row.actual_price,
            vat_rate=row.actual_vat_rate,
            price_with_vat=row.actual_price_with_vat,
            sum=row.actual_sum,
            driver=row.money_driver,
            source_currency=row.source_currency,
            source_price=row.source_price,
            source_price_with_vat=row.source_price_with_vat,
            source_sum=row.source_sum,
            exchange_rate=row.exchange_rate,
            exchange_rate_date=row.exchange_rate_date,
            exchange_rate_manual=bool(row.exchange_rate_manual),
        ),
        invoice_item_name=row.invoice_item_name,
        invoice_number=row.invoice_number,
        invoice_date=row.invoice_date,
        order_number=row.order_number,
        order_date=row.order_date,
        replacement=row.replacement,
        replacement_decision=(
            ReplacementDecision(row.replacement_decision) if row.replacement_decision else None
        ),
        permission_number=row.permission_number,
        permission_date=row.permission_date,
        initiator_id=row.initiator_id,
        trace=ProcurementTraceability(
            manufacturer=row.manufacturer,
            country=row.country,
            quality_document_type=row.quality_document_type,
            quality_document_number=row.quality_document_number,
            quality_document_date=row.quality_document_date,
            standard=row.standard,
        ),
        stock_covered=row.stock_covered if row.stock_covered is not None else Decimal("0"),
        note=row.note,
        actual_arrival=row.actual_arrival,
        datasheet=row.datasheet,
    )


_SWEEP_OWNED = frozenset({"id", "notified_status"})


class ProcurementSupplierOrderRow(Base):
    """An issued supplier request; the primary key keeps № ЗАКАЗА unique under concurrent buyers."""

    __tablename__ = "procurement_supplier_orders"

    order_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_date: Mapped[Any] = mapped_column(Date, nullable=False)
    supplier: Mapped[str] = mapped_column(String(512), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_point: Mapped[str] = mapped_column(String(512), nullable=False)
    buyer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    buyer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    buyer_department: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    letter: Mapped[str] = mapped_column(Text, nullable=False)


class ProcurementSupplierOrderDocumentRow(Base):
    """Документ запроса поставщику (SUPPLYMAP клал Google Doc в «Запросы поставщикам/год/месяц/поставщик»)."""

    __tablename__ = "procurement_supplier_order_documents"

    order_number: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("procurement_supplier_orders.order_number", ondelete="CASCADE"),
        primary_key=True,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    folder: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)


def _document_from_row(row: ProcurementSupplierOrderDocumentRow) -> SupplierOrderDocument:
    return SupplierOrderDocument(
        order_number=row.order_number,
        file_name=row.file_name,
        folder=row.folder,
        content_type=row.content_type,
        size=row.size,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class SqlAlchemyProcurementRepository:
    def __init__(self, session: Session):
        self.session = session

    def by_source_idempotency_key(self, key: str) -> ProcurementLine | None:
        row = self.session.scalar(
            select(ProcurementLineRow).where(
                ProcurementLineRow.source_idempotency_key == key
            )
        )
        return _line_domain(row) if row is not None else None

    def get(self, line_id: UUID) -> ProcurementLine | None:
        row = self.session.get(ProcurementLineRow, line_id)
        return _line_domain(row) if row is not None else None

    def _after(self, statement, after_id: UUID | None):
        if after_id is None:
            return statement
        cursor = self.session.get(ProcurementLineRow, after_id)
        if cursor is None:
            return statement.where(False)
        return statement.where(
            or_(
                ProcurementLineRow.created_at > cursor.created_at,
                and_(
                    ProcurementLineRow.created_at == cursor.created_at,
                    ProcurementLineRow.id > cursor.id,
                ),
            )
        )

    def available(
        self, limit: int, after_id: UUID | None = None
    ) -> list[ProcurementLine]:
        statement = (
            select(ProcurementLineRow)
            .where(ProcurementLineRow.status == ProcurementStatus.NEW.value)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
            .limit(limit)
        )
        statement = self._after(statement, after_id)
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def by_buyer(
        self,
        actor: Actor,
        limit: int,
        after_id: UUID | None = None,
        accepted_since: date | None = None,
    ) -> list[ProcurementLine]:
        wanted = ProcurementLineRow.status.not_in(
            [ProcurementStatus.DONE.value, ProcurementStatus.CANCELLED.value]
        )
        if accepted_since is not None:
            # «Готово», принятые не раньше `accepted_since` (ФАКТ. ДАТА; без неё — день создания строки).
            # «Готово · без ГРН» (closed «Есть на складе, без ГРН», no lot bound yet) stays at any age: the buyer
            # still has «Привязать ГРН» to do on it.
            since = datetime.combine(accepted_since, datetime.min.time()).replace(tzinfo=timezone.utc)
            lotless = select(ProcurementStockCoverRow.line_id).where(
                ProcurementStockCoverRow.lot_id.is_(None), ProcurementStockCoverRow.quantity > 0,
            )
            wanted = or_(
                wanted,
                and_(
                    ProcurementLineRow.status == ProcurementStatus.DONE.value,
                    or_(
                        ProcurementLineRow.actual_arrival >= accepted_since,
                        and_(
                            ProcurementLineRow.actual_arrival.is_(None),
                            ProcurementLineRow.created_at >= since,
                        ),
                        ProcurementLineRow.id.in_(lotless),
                    ),
                ),
            )
        statement = (
            select(ProcurementLineRow)
            .where(ProcurementLineRow.buyer_id == actor.id, wanted)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
            .limit(limit)
        )
        statement = self._after(statement, after_id)
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def in_work(
        self, limit: int, after_id: UUID | None = None
    ) -> list[ProcurementLine]:
        statement = (
            select(ProcurementLineRow)
            .where(
                ProcurementLineRow.status.not_in(
                    [
                        ProcurementStatus.NEW.value,
                        ProcurementStatus.DONE.value,
                        ProcurementStatus.CANCELLED.value,
                    ]
                ),
            )
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
            .limit(limit)
        )
        statement = self._after(statement, after_id)
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def search(
        self,
        *,
        query: str,
        status: str | None,
        buyer: str | None,
        limit: int,
        after_id: UUID | None = None,
    ) -> list[ProcurementLine]:
        statement = select(ProcurementLineRow)
        if status:
            statement = statement.where(ProcurementLineRow.status == status)
        if buyer:
            statement = statement.where(ProcurementLineRow.buyer_name == buyer)
        # Every word of the query must occur in one of the line's text columns (SUPPLYMAP search).
        columns = (
            ProcurementLineRow.legacy_id,
            ProcurementLineRow.designation,
            ProcurementLineRow.project_reference,
            ProcurementLineRow.calculated_name,
            ProcurementLineRow.design_article,
            ProcurementLineRow.site_name,
            ProcurementLineRow.supplier,
            ProcurementLineRow.order_number,
            ProcurementLineRow.initiator,
            ProcurementLineRow.buyer_name,
            ProcurementLineRow.link,
            ProcurementLineRow.invoice_number,
            ProcurementLineRow.item_type,
        )
        for word in query.split():
            escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            statement = statement.where(or_(*(column.ilike(pattern, escape="\\") for column in columns)))
        statement = statement.order_by(ProcurementLineRow.created_at, ProcurementLineRow.id).limit(limit)
        statement = self._after(statement, after_id)
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def buyer_names(self) -> list[str]:
        statement = select(ProcurementLineRow.buyer_name).where(ProcurementLineRow.buyer_name.is_not(None)).distinct()
        return [name for name in self.session.scalars(statement) if name]

    def excluding_statuses(self, statuses) -> list[ProcurementLine]:
        statement = (
            select(ProcurementLineRow)
            .where(ProcurementLineRow.status.not_in(list(statuses)))
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
        )
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def with_statuses(self, statuses) -> list[ProcurementLine]:
        statement = (
            select(ProcurementLineRow)
            .where(ProcurementLineRow.status.in_(list(statuses)))
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
        )
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def list(self, limit: int = 10_000) -> list[ProcurementLine]:
        rows = self.session.scalars(
            select(ProcurementLineRow)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
            .limit(limit)
        )
        return [_line_domain(row) for row in rows]

    def for_project(self, project: str, request_ids: Sequence[UUID] = ()) -> list[ProcurementLine]:
        """Строки одного проекта: по своему № ПРОЕКТА (точно по ключу, по индексу) или по заявке из `request_ids`
        (индекс `source_request_id`). Старые первыми, как `list()`, но без общего лимита."""
        key = normalize_project_number(project)
        ids = list(dict.fromkeys(request_ids))
        if not key and not ids:
            return []
        own = or_(ProcurementLineRow.project_key == key, ProcurementLineRow.project_key.is_(None)) if key else false()
        rows = self.session.scalars(
            select(ProcurementLineRow)
            .where(or_(own, ProcurementLineRow.source_request_id.in_(ids)) if ids else own)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
        )
        wanted = set(ids)
        return [
            _line_domain(row) for row in rows
            if row.source_request_id in wanted
            or (key and row.project_reference and normalize_project_number(row.project_reference) == key)
        ]

    def legacy_ids_with_prefix(self, prefix: str) -> list[str]:
        # startswith() escapes LIKE wildcards, so an ID with "_" or "%" matches only itself.
        statement = select(ProcurementLineRow.legacy_id).where(
            ProcurementLineRow.legacy_id.startswith(prefix, autoescape=True)
        )
        return list(self.session.scalars(statement))

    def ready_for_request(self) -> list[ProcurementLine]:
        statement = (
            select(ProcurementLineRow)
            .where(ProcurementLineRow.status == ProcurementStatus.READY.value)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
        )
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def order_numbers_with_prefix(self, prefix: str) -> list[str]:
        statement = select(ProcurementSupplierOrderRow.order_number).where(
            ProcurementSupplierOrderRow.order_number.startswith(f"{prefix}-", autoescape=True)
        )
        return list(self.session.scalars(statement))

    def permission_numbers_with_prefix(self, prefix: str) -> list[str]:
        statement = select(ProcurementLineRow.permission_number).where(
            ProcurementLineRow.permission_number.is_not(None),
            ProcurementLineRow.permission_number.startswith(f"{prefix}-", autoescape=True),
        )
        return list(self.session.scalars(statement))

    def add_order(self, order: SupplierOrder) -> None:
        self.session.add(
            ProcurementSupplierOrderRow(
                order_number=order.order_number,
                order_date=order.order_date,
                supplier=order.supplier,
                task_type=order.task_type,
                delivery_point=order.delivery_point,
                buyer_id=order.buyer.id,
                buyer_name=order.buyer.name,
                buyer_department=order.buyer.department,
                created_at=order.created_at,
                letter=order.letter,
            )
        )
        # The PK insert runs now, so a number taken by a concurrent buyer fails before lines move.
        self.session.flush()

    def get_order(self, order_number: str) -> SupplierOrder | None:
        row = self.session.get(ProcurementSupplierOrderRow, order_number)
        if row is None:
            return None
        line_ids = self.session.scalars(
            select(ProcurementLineRow.id)
            .where(ProcurementLineRow.order_number == order_number)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
        )
        return SupplierOrder(
            order_number=row.order_number,
            order_date=row.order_date,
            supplier=row.supplier,
            task_type=row.task_type,
            delivery_point=row.delivery_point,
            buyer=Actor(id=row.buyer_id, name=row.buyer_name, department=row.buyer_department or ""),
            created_at=row.created_at,
            letter=row.letter,
            line_ids=tuple(line_ids),
            document=self._document_meta(order_number),
        )

    def _document_meta(self, order_number: str) -> SupplierOrderDocument | None:
        # Без колонки content: метаданные карточки заказа не тянут байты файла.
        found = self.session.execute(
            select(
                ProcurementSupplierOrderDocumentRow.order_number,
                ProcurementSupplierOrderDocumentRow.file_name,
                ProcurementSupplierOrderDocumentRow.folder,
                ProcurementSupplierOrderDocumentRow.content_type,
                ProcurementSupplierOrderDocumentRow.size,
                ProcurementSupplierOrderDocumentRow.created_by,
                ProcurementSupplierOrderDocumentRow.created_at,
            ).where(ProcurementSupplierOrderDocumentRow.order_number == order_number)
        ).first()
        return SupplierOrderDocument(*found) if found else None

    def add_order_document(self, document: SupplierOrderDocument, content: bytes) -> None:
        self.session.add(
            ProcurementSupplierOrderDocumentRow(
                order_number=document.order_number,
                file_name=document.file_name,
                folder=document.folder,
                content_type=document.content_type,
                size=document.size,
                content=content,
                created_by=document.created_by,
                created_at=document.created_at,
            )
        )

    def get_order_document(self, order_number: str) -> tuple[SupplierOrderDocument, bytes] | None:
        row = self.session.get(ProcurementSupplierOrderDocumentRow, order_number)
        if row is None:
            return None
        return _document_from_row(row), row.content

    def has_lines_for_request(self, source_request_id: UUID) -> bool:
        statement = (
            select(ProcurementLineRow.id)
            .where(ProcurementLineRow.source_request_id == source_request_id)
            .limit(1)
        )
        return self.session.scalar(statement) is not None

    def by_source_request_ids(self, source_request_ids: Sequence[UUID]) -> list[ProcurementLine]:
        ids = list(source_request_ids)
        if not ids:
            return []
        statement = (
            select(ProcurementLineRow)
            .where(ProcurementLineRow.source_request_id.in_(ids))
            .order_by(ProcurementLineRow.created_at)
        )
        return [_line_domain(row) for row in self.session.scalars(statement)]

    def revisions_for_designation(self, designation: str) -> list[str]:
        # Compared in Python: SQLite's lower() folds only ASCII, and НАЗНАЧЕНИЕ is Cyrillic.
        key = designation_key(designation)
        statement = select(ProcurementLineRow.designation, ProcurementLineRow.revision)
        return [revision for stored, revision in self.session.execute(statement) if designation_key(stored) == key]

    def add(self, line: ProcurementLine) -> None:
        incoming = _line_row(line)
        stored = self.session.get(ProcurementLineRow, line.id)
        if stored is None:
            self.session.add(incoming)
            return
        # Compare-and-swap against the version this transaction read (StockVersionConflict on a lost race).
        check_read_version(self.session, stored, line.version)
        for column in ProcurementLineRow.__table__.columns:
            if column.name not in _SWEEP_OWNED:
                setattr(stored, column.name, getattr(incoming, column.name))
        note_written_version(self.session, stored)

    def delete(self, line_id: UUID, expected_version: int) -> bool:
        # Core DELETE guarded by the version: a concurrent edit wins and nothing is touched. Released «Со склада»
        # covers are this line's own history: the FK cascades them on PostgreSQL, the second DELETE does it on SQLite.
        result = self.session.execute(
            sql_delete(ProcurementLineRow)
            .where(ProcurementLineRow.id == line_id, ProcurementLineRow.version == expected_version)
            .execution_options(synchronize_session=False)
        )
        if not result.rowcount:
            return False
        self.session.execute(
            sql_delete(ProcurementStockCoverRow)
            .where(ProcurementStockCoverRow.line_id == line_id)
            .execution_options(synchronize_session=False)
        )
        return True

    def status_notifications_due(self, statuses: Sequence[str], limit: int) -> list[StatusNotificationDue]:
        rows = self.session.execute(
            select(
                ProcurementLineRow.id, ProcurementLineRow.version, ProcurementLineRow.legacy_id,
                ProcurementLineRow.status, ProcurementLineRow.initiator_id,
                ProcurementLineRow.designation, ProcurementLineRow.calculated_name,
                ProcurementLineRow.expected_arrival,
            )
            .where(
                ProcurementLineRow.status.in_(tuple(statuses)),
                or_(
                    ProcurementLineRow.notified_status.is_(None),
                    ProcurementLineRow.notified_status != ProcurementLineRow.status,
                ),
            )
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
            .limit(limit)
        )
        return [StatusNotificationDue(*row) for row in rows]

    def mark_status_notified(self, line_id: UUID, status: str) -> bool:
        # Core UPDATE: no version bump, and a line whose status moved on since the read stays due.
        result = self.session.execute(
            update(ProcurementLineRow)
            .where(ProcurementLineRow.id == line_id, ProcurementLineRow.status == status)
            .values(notified_status=status)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)


class ProcurementFileRow(Base):
    """A file of a procurement line: invoices and КП (`uploadInvoice` kept them in the Drive folder «Счета»), and
    since 0089 the line's РАЗРЕШЕНИЕ and ДОК. КАЧЕСТВА files (`kind`), since 0147 the ПП of a paid order (`payment`)."""

    __tablename__ = "procurement_files"
    __table_args__ = (
        CheckConstraint("kind IN ('other', 'permit', 'quality', 'payment')", name="ck_procurement_files_kind"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    line_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="other", server_default="other")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)


def _stored_file(row) -> StoredInvoiceFile:
    return StoredInvoiceFile(
        id=row.id, line_id=row.line_id, file_name=row.file_name, content_type=row.content_type,
        size=row.size, uploaded_by=row.uploaded_by, uploaded_at=row.uploaded_at, kind=row.kind,
    )


class SqlAlchemyInvoiceFileRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, stored: StoredInvoiceFile, content: bytes) -> None:
        self.session.add(ProcurementFileRow(
            id=stored.id, line_id=stored.line_id, kind=stored.kind, file_name=stored.file_name,
            content_type=stored.content_type, size=stored.size, content=content, uploaded_by=stored.uploaded_by,
            uploaded_at=stored.uploaded_at,
        ))

    def get(self, file_id: UUID) -> tuple[StoredInvoiceFile, bytes] | None:
        row = self.session.get(ProcurementFileRow, file_id)
        if row is None:
            return None
        return _stored_file(row), row.content

    def of_lines(self, line_ids: Sequence[UUID], kinds: Sequence[str]) -> list[StoredInvoiceFile]:
        """The files of these kinds on these lines, by kind and then oldest first, without their content — one query
        per list."""
        if not line_ids or not kinds:
            return []
        rows = self.session.execute(
            select(
                ProcurementFileRow.id, ProcurementFileRow.line_id, ProcurementFileRow.kind,
                ProcurementFileRow.file_name, ProcurementFileRow.content_type, ProcurementFileRow.size,
                ProcurementFileRow.uploaded_by, ProcurementFileRow.uploaded_at,
            )
            .where(ProcurementFileRow.line_id.in_(tuple(line_ids)), ProcurementFileRow.kind.in_(tuple(kinds)))
            .order_by(ProcurementFileRow.kind, ProcurementFileRow.uploaded_at, ProcurementFileRow.id)
        )
        return [_stored_file(row) for row in rows]


class ProcurementStockCoverRow(Base):
    """«Со склада»: a reserve one procurement line holds on one warehouse lot (StockCover)."""

    __tablename__ = "procurement_stock_covers"
    __table_args__ = (Index("ix_procurement_stock_covers_lot_quantity", "lot_id", "quantity"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    line_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("procurement_lines.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    legacy_id: Mapped[str] = mapped_column(String(32), nullable=False)
    project_reference: Mapped[str | None] = mapped_column(String(128), index=True)
    # Nullable since 0087: «Есть на складе, без ГРН» — a cover without a lot holds no reserve.
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    warehouse: Mapped[str] = mapped_column(String(8), nullable=False)
    grn: Mapped[str] = mapped_column(String(32), nullable=False)
    package: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_location: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    trimmed: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    consumed: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    consumed_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    released_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


def _cover_row(cover: StockCover) -> ProcurementStockCoverRow:
    return ProcurementStockCoverRow(
        id=cover.id, version=cover.version, line_id=cover.line_id, legacy_id=cover.legacy_id,
        project_reference=cover.project_reference, lot_id=cover.lot_id, warehouse=cover.warehouse, grn=cover.grn,
        package=cover.package, storage_location=cover.storage_location, unit=cover.unit, quantity=cover.quantity,
        trimmed=cover.trimmed, consumed=cover.consumed, consumed_by=cover.consumed_by, created_at=cover.created_at,
        created_by=cover.created_by, created_by_id=cover.created_by_id, released_at=cover.released_at,
    )


def _cover_domain(row: ProcurementStockCoverRow) -> StockCover:
    return StockCover(
        id=row.id, version=row.version, line_id=row.line_id, legacy_id=row.legacy_id,
        project_reference=row.project_reference, lot_id=row.lot_id, warehouse=row.warehouse, grn=row.grn,
        package=row.package, storage_location=row.storage_location, unit=row.unit, quantity=row.quantity,
        trimmed=row.trimmed, consumed=row.consumed, consumed_by=row.consumed_by, created_at=row.created_at,
        created_by=row.created_by, created_by_id=row.created_by_id, released_at=row.released_at,
    )


track_read_versions(ProcurementLineRow)
track_read_versions(ProcurementStockCoverRow)


class SqlAlchemyStockCoverRepository:
    """«Со склада» reserves. Read by Procurement (cover/release), Fulfillment (a kit of the same project takes
    them over) and the warehouse screen (who holds a lot's РЕЗЕРВ)."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, cover_id: UUID) -> StockCover | None:
        row = self.session.get(ProcurementStockCoverRow, cover_id)
        return _cover_domain(row) if row is not None else None

    def for_line(self, line_id: UUID) -> list[StockCover]:
        rows = self.session.scalars(
            select(ProcurementStockCoverRow)
            .where(ProcurementStockCoverRow.line_id == line_id)
            .order_by(ProcurementStockCoverRow.created_at, ProcurementStockCoverRow.id)
        )
        return [_cover_domain(row) for row in rows]

    def active_on_lots(self, lot_ids: Sequence[UUID]) -> list[StockCover]:
        if not lot_ids:
            return []
        rows = self.session.scalars(
            select(ProcurementStockCoverRow)
            .where(ProcurementStockCoverRow.lot_id.in_(tuple(lot_ids)), ProcurementStockCoverRow.quantity > 0)
            .order_by(ProcurementStockCoverRow.created_at, ProcurementStockCoverRow.id)
        )
        return [_cover_domain(row) for row in rows]

    def active_without_lot(self, line_ids: Sequence[UUID]) -> list[StockCover]:
        """Active covers «Есть на складе, без ГРН» of these lines (no lot holds them) — the project's «Снабжение»."""
        ids = tuple(dict.fromkeys(line_ids))
        found: list[StockCover] = []
        for start in range(0, len(ids), 500):
            rows = self.session.scalars(
                select(ProcurementStockCoverRow)
                .where(
                    ProcurementStockCoverRow.line_id.in_(ids[start:start + 500]),
                    ProcurementStockCoverRow.lot_id.is_(None),
                    ProcurementStockCoverRow.quantity > 0,
                )
                .order_by(ProcurementStockCoverRow.created_at, ProcurementStockCoverRow.id)
            )
            found.extend(_cover_domain(row) for row in rows)
        return found

    def reserved_on_lot(self, lot_id: UUID) -> Decimal:
        """What «Со склада» holds in reserve on one lot (the warehouse row edit may not go under it)."""
        total = self.session.scalar(
            select(func.coalesce(func.sum(ProcurementStockCoverRow.quantity), 0))
            .where(ProcurementStockCoverRow.lot_id == lot_id)
        )
        return Decimal(str(total or 0))

    def add(self, cover: StockCover) -> None:
        incoming = _cover_row(cover)
        stored = self.session.get(ProcurementStockCoverRow, cover.id)
        if stored is None:
            self.session.add(incoming)
            return
        check_read_version(self.session, stored, cover.version)
        for column in ProcurementStockCoverRow.__table__.columns:
            if column.name != "id":
                setattr(stored, column.name, getattr(incoming, column.name))
        note_written_version(self.session, stored)


class SqlAlchemyProcurementUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyProcurementUnitOfWork:
        self.committed = False
        self.session = self.session_factory()
        self.procurement = SqlAlchemyProcurementRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        self.files = SqlAlchemyInvoiceFileRepository(self.session)
        # «Со склада» covers stay owned by procurement; the warehouse reserve is
        # supplied by the composite stock UoW in app.workflows.procurement_stock.
        self.stock_covers = SqlAlchemyStockCoverRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self.committed:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        self.session.commit()
        self.committed = True
