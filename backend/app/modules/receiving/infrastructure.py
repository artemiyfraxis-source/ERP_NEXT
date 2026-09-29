"""SQLAlchemy adapters for the Receiving bounded context."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid, and_, or_, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.identity import Actor
from app.shared.persistence import Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository

from .domain import ReceivingLine, ReceivingStatus


class ReceivingLineRow(Base):
    __tablename__ = "receiving_lines"
    __table_args__ = (
        UniqueConstraint("grn", "package", name="uq_receiving_grn_package"),
        UniqueConstraint("revision_of_id", name="uq_receiving_lines_revision_of_id"),
        # One live line per procurement line; archived «Архив ВК» rows keep the link as history, and
        # batch/package lines `-ПN` (split_of_id) share the order of the line they came from.
        Index(
            "ux_receiving_live_procurement_line",
            "procurement_line_id",
            unique=True,
            sqlite_where=text("status <> 'Архив ВК' AND split_of_id IS NULL"),
            postgresql_where=text("status <> 'Архив ВК' AND split_of_id IS NULL"),
        ),
        Index("ix_receiving_created_id", "created_at", "id"),
        Index("ix_receiving_status_created_id", "status", "created_at", "id"),
        Index("ix_receiving_warehouse_status_created_id", "warehouse", "status", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    source_idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    procurement_line_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    legacy_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    warehouse: Mapped[str] = mapped_column(String(8), nullable=False)
    article: Mapped[str] = mapped_column(String(1024), nullable=False)
    series: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_type: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    goal: Mapped[str] = mapped_column(String(128), nullable=False)
    source_link: Mapped[str] = mapped_column(Text, nullable=False)
    supplier: Mapped[str] = mapped_column(String(512), nullable=False)
    ordered_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_document_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    document_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    fact_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    shipping_document_url: Mapped[str | None] = mapped_column(Text)
    quality_document_url: Mapped[str | None] = mapped_column(Text)
    expiry_date: Mapped[Any | None] = mapped_column(Date)
    unit_mass_g: Mapped[Decimal | None] = mapped_column(Numeric(18, 5))
    disposition: Mapped[str | None] = mapped_column(String(255))
    verdict: Mapped[str | None] = mapped_column(String(64))
    nonconformities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    accepted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    backorder_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    grn: Mapped[str | None] = mapped_column(String(32))
    package: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_location: Mapped[str | None] = mapped_column(String(16))
    inspector_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    inspector_name: Mapped[str | None] = mapped_column(String(255))
    inspector_department: Mapped[str | None] = mapped_column(String(255))
    warehouse_event_emitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inspected_on: Mapped[Any | None] = mapped_column(Date)
    revision_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("receiving_lines.id", ondelete="RESTRICT")
    )
    revision_root_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    nonconformity_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    supplier_returns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    expected_arrival: Mapped[Any | None] = mapped_column(Date)
    document_number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    document_date: Mapped[Any | None] = mapped_column(Date)
    document_name: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    inspection_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manufacture_date: Mapped[Any | None] = mapped_column(Date)
    split_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("receiving_lines.id", ondelete="RESTRICT"), index=True
    )

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


def _row(line: ReceivingLine) -> ReceivingLineRow:
    return ReceivingLineRow(
        id=line.id,
        source_event_id=line.source_event_id,
        source_idempotency_key=line.source_idempotency_key,
        procurement_line_id=line.procurement_line_id,
        legacy_id=line.legacy_id,
        status=line.status.value,
        version=line.version,
        created_at=line.created_at,
        warehouse=line.warehouse,
        article=line.article,
        series=line.series,
        name=line.name,
        item_type=line.item_type,
        purpose=line.purpose,
        goal=line.goal,
        source_link=line.source_link,
        supplier=line.supplier,
        ordered_quantity=line.ordered_quantity,
        unit=line.unit,
        quality_document_required=line.quality_document_required,
        document_quantity=line.document_quantity,
        fact_quantity=line.fact_quantity,
        shipping_document_url=line.shipping_document_url,
        quality_document_url=line.quality_document_url,
        expiry_date=line.expiry_date,
        unit_mass_g=line.unit_mass_g,
        disposition=line.disposition,
        verdict=line.verdict,
        nonconformities=list(line.nonconformities),
        accepted_quantity=line.accepted_quantity,
        backorder_quantity=line.backorder_quantity,
        grn=line.grn,
        package=line.package,
        storage_location=line.storage_location,
        inspector_id=line.inspector.id if line.inspector else None,
        inspector_name=line.inspector.name if line.inspector else None,
        inspector_department=line.inspector.department if line.inspector else None,
        warehouse_event_emitted=line.warehouse_event_emitted,
        inspected_on=line.inspected_on,
        revision_of_id=line.revision_of_id,
        revision_root_id=line.revision_root_id,
        nonconformity_description=line.nonconformity_description,
        supplier_returns=line.supplier_returns,
        order_number=line.order_number,
        expected_arrival=line.expected_arrival,
        document_number=line.document_number,
        document_date=line.document_date,
        document_name=line.document_name,
        inspection_note=line.inspection_note,
        manufacture_date=line.manufacture_date,
        split_of_id=line.split_of_id,
    )


def _domain(row: ReceivingLineRow) -> ReceivingLine:
    inspector = None
    if row.inspector_name:
        inspector = Actor(
            id=row.inspector_id,
            name=row.inspector_name,
            department=row.inspector_department or "",
        )
    return ReceivingLine(
        id=row.id,
        source_event_id=row.source_event_id,
        source_idempotency_key=row.source_idempotency_key,
        procurement_line_id=row.procurement_line_id,
        legacy_id=row.legacy_id,
        status=ReceivingStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        warehouse=row.warehouse,
        article=row.article,
        series=row.series,
        name=row.name,
        item_type=row.item_type,
        purpose=row.purpose,
        goal=row.goal,
        source_link=row.source_link,
        supplier=row.supplier,
        ordered_quantity=row.ordered_quantity,
        unit=row.unit,
        quality_document_required=row.quality_document_required,
        document_quantity=row.document_quantity,
        fact_quantity=row.fact_quantity,
        shipping_document_url=row.shipping_document_url,
        quality_document_url=row.quality_document_url,
        expiry_date=row.expiry_date,
        unit_mass_g=row.unit_mass_g,
        disposition=row.disposition,
        verdict=row.verdict,
        nonconformities=tuple(row.nonconformities),
        accepted_quantity=row.accepted_quantity,
        backorder_quantity=row.backorder_quantity,
        grn=row.grn,
        package=row.package,
        storage_location=row.storage_location,
        inspector=inspector,
        warehouse_event_emitted=row.warehouse_event_emitted,
        inspected_on=row.inspected_on,
        revision_of_id=row.revision_of_id,
        revision_root_id=row.revision_root_id,
        nonconformity_description=row.nonconformity_description or "",
        supplier_returns=row.supplier_returns or 0,
        order_number=row.order_number or "",
        expected_arrival=row.expected_arrival,
        document_number=row.document_number or "",
        document_date=row.document_date,
        document_name=row.document_name or "",
        inspection_note=row.inspection_note or "",
        manufacture_date=row.manufacture_date,
        split_of_id=row.split_of_id,
    )


class SqlAlchemyReceivingRepository:
    def __init__(self, session: Session):
        self.session = session

    def by_source_idempotency_key(self, key: str) -> ReceivingLine | None:
        row = self.session.scalar(
            select(ReceivingLineRow).where(ReceivingLineRow.source_idempotency_key == key)
        )
        return _domain(row) if row is not None else None

    def get(self, line_id: UUID) -> ReceivingLine | None:
        row = self.session.get(ReceivingLineRow, line_id)
        return _domain(row) if row is not None else None

    def queue(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
    ) -> list[ReceivingLine]:
        statement = select(ReceivingLineRow)
        if after_id is not None:
            cursor = self.session.get(ReceivingLineRow, after_id)
            if cursor is None:
                return []
            statement = statement.where(
                or_(
                    ReceivingLineRow.created_at > cursor.created_at,
                    and_(
                        ReceivingLineRow.created_at == cursor.created_at,
                        ReceivingLineRow.id > cursor.id,
                    ),
                )
            )
        rows = self.session.scalars(
            statement.order_by(ReceivingLineRow.created_at, ReceivingLineRow.id).limit(limit)
        )
        return [_domain(row) for row in rows]

    def waiting_inspected_by(self, day: date, limit: int = 200) -> list[ReceivingLine]:
        rows = self.session.scalars(
            select(ReceivingLineRow)
            .where(
                ReceivingLineRow.status == ReceivingStatus.WAITING.value,
                ReceivingLineRow.inspected_on.is_not(None),
                ReceivingLineRow.inspected_on <= day,
            )
            .order_by(ReceivingLineRow.created_at, ReceivingLineRow.id)
            .limit(limit)
        )
        return [_domain(row) for row in rows]

    def awaiting_placement(self, limit: int = 500) -> list[ReceivingLine]:
        """WH-6: «Принято», ещё без места и ещё не переданное на склад — putaway_sheet's source."""
        rows = self.session.scalars(
            select(ReceivingLineRow)
            .where(
                ReceivingLineRow.status == ReceivingStatus.ACCEPTED.value,
                ReceivingLineRow.storage_location.is_(None),
                ReceivingLineRow.warehouse_event_emitted.is_(False),
            )
            .order_by(ReceivingLineRow.created_at, ReceivingLineRow.id)
            .limit(limit)
        )
        return [_domain(row) for row in rows]

    def by_status(self, status: ReceivingStatus, limit: int = 500) -> list[ReceivingLine]:
        rows = self.session.scalars(
            select(ReceivingLineRow)
            .where(ReceivingLineRow.status == status.value)
            .order_by(ReceivingLineRow.created_at, ReceivingLineRow.id)
            .limit(limit)
        )
        return [_domain(row) for row in rows]

    def all_grns(self) -> list[str]:
        return list(
            self.session.scalars(
                select(ReceivingLineRow.grn).where(ReceivingLineRow.grn.is_not(None))
            )
        )

    def legacy_ids_starting_with(self, prefix: str) -> list[str]:
        return list(
            self.session.scalars(
                select(ReceivingLineRow.legacy_id).where(
                    ReceivingLineRow.legacy_id.startswith(prefix, autoescape=True)
                )
            )
        )

    def add(self, line: ReceivingLine) -> None:
        incoming = _row(line)
        stored = self.session.get(ReceivingLineRow, line.id)
        if stored is None:
            self.session.add(incoming)
            return
        for column in ReceivingLineRow.__table__.columns:
            if column.name != "id":
                setattr(stored, column.name, getattr(incoming, column.name))


class SqlAlchemyReceivingUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.receiving = SqlAlchemyReceivingRepository(self.session)
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
