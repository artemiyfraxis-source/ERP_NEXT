"""SQLAlchemy persistence adapters for the Procurement bounded context."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    and_,
    false,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.identity import Actor
from app.shared.persistence import (
    Base,
    SqlAlchemyAuditRepository,
    SqlAlchemyOutboxRepository,
)

from .application import StatusNotificationDue, SupplierOrder
from .domain import ProcurementLine, ProcurementStatus, ReplacementDecision, designation_key
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
    # The last status the initiator was notified of (OwnerTick.gs notified_<id>); owned by the sweep.
    notified_status: Mapped[str | None] = mapped_column(String(32))

    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": False,
    }


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
        self, actor: Actor, limit: int, after_id: UUID | None = None
    ) -> list[ProcurementLine]:
        statement = (
            select(ProcurementLineRow)
            .where(
                ProcurementLineRow.buyer_id == actor.id,
                ProcurementLineRow.status.not_in(
                    [ProcurementStatus.DONE.value, ProcurementStatus.CANCELLED.value]
                ),
            )
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

    def list(self, limit: int = 10_000) -> list[ProcurementLine]:
        rows = self.session.scalars(
            select(ProcurementLineRow)
            .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
            .limit(limit)
        )
        return [_line_domain(row) for row in rows]

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
        )

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
        for column in ProcurementLineRow.__table__.columns:
            if column.name not in _SWEEP_OWNED:
                setattr(stored, column.name, getattr(incoming, column.name))

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
