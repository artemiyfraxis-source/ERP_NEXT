"""SQLAlchemy persistence for production-kit fulfillment."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository, SqlAlchemyWriteOffJournal
from app.shared.persistence import Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository

from .domain import FulfillmentLine, FulfillmentRequest, FulfillmentStatus, LotSnapshot


class FulfillmentRequestRow(Base):
    __tablename__ = "fulfillment_requests"
    __table_args__ = (
        Index("ix_fulfillment_status_created_id", "status", "created_at", "id"),
        Index("ix_fulfillment_created_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    source_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requester: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String(512), nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    handout_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    return_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shortfall_note: Mapped[str | None] = mapped_column(Text(), nullable=True)  # НЕДОБОР written at creation
    # FF-4: stage dates by the first hit (SUPPLYMAP В РАБОТУ / НАБРАНА / ВЫДАНА / ВОЗВРАЩЕНА / ЗАКРЫТА).
    at_work: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    at_picked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    at_issued: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    at_returned: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    at_closed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class FulfillmentLineRow(Base):
    __tablename__ = "fulfillment_lines"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fulfillment_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    issued: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    returned_good: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    defective_returned: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    defective_missing: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    lot_warehouse: Mapped[str | None] = mapped_column(String(8), nullable=True)
    lot_grn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lot_package: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lot_storage_location: Mapped[str | None] = mapped_column(String(255), nullable=True)


def _line(row: FulfillmentLineRow) -> FulfillmentLine:
    lot = None
    if row.lot_id is not None:
        lot = LotSnapshot(
            lot_id=row.lot_id,
            warehouse=row.lot_warehouse or "",
            grn=row.lot_grn or "",
            package=row.lot_package or "",
            storage_location=row.lot_storage_location or "",
        )
    return FulfillmentLine(
        id=row.id,
        requirement_id=row.requirement_id,
        article=row.article,
        name=row.name,
        unit=row.unit,
        requirement=row.requirement,
        reserved=row.reserved,
        issued=row.issued,
        lot=lot,
        returned_good=row.returned_good,
        defective_returned=row.defective_returned,
        defective_missing=row.defective_missing,
    )


class SqlAlchemyFulfillmentRepository:
    def __init__(self, session: Session):
        self.session = session

    def _domain(self, row: FulfillmentRequestRow) -> FulfillmentRequest:
        lines = self.session.scalars(
            select(FulfillmentLineRow)
            .where(FulfillmentLineRow.request_id == row.id)
            .order_by(FulfillmentLineRow.position)
        )
        return FulfillmentRequest(
            id=row.id,
            number=row.number,
            source_idempotency_key=row.source_idempotency_key,
            source_fingerprint=row.source_fingerprint,
            version=row.version,
            created_at=row.created_at,
            requester=row.requester,
            purpose=row.purpose,
            deadline=row.deadline,
            status=FulfillmentStatus(row.status),
            lines=tuple(_line(line) for line in lines),
            handout_location=row.handout_location,
            return_location=row.return_location,
            shortfall_note=row.shortfall_note,
            requester_id=row.requester_id,
            at_work=row.at_work,
            at_picked=row.at_picked,
            at_issued=row.at_issued,
            at_returned=row.at_returned,
            at_closed=row.at_closed,
        )

    def get(self, request_id: UUID) -> FulfillmentRequest | None:
        row = self.session.get(FulfillmentRequestRow, request_id)
        return self._domain(row) if row is not None else None

    def by_idempotency_key(self, key: str) -> FulfillmentRequest | None:
        row = self.session.scalar(
            select(FulfillmentRequestRow).where(
                FulfillmentRequestRow.source_idempotency_key == key
            )
        )
        return self._domain(row) if row is not None else None

    def numbers(self, year: int) -> list[str]:
        prefix = f"REQ.{year % 100:02d}."
        return list(self.session.scalars(
            select(FulfillmentRequestRow.number).where(
                FulfillmentRequestRow.number.like(f"{prefix}%")
            )
        ))

    def reserved_on_lot(self, lot_id: UUID) -> Decimal:
        """What kitting lines hold in reserve on one warehouse lot (the warehouse row edit may not go under it)."""
        total = self.session.scalar(
            select(func.coalesce(func.sum(FulfillmentLineRow.reserved), 0)).where(FulfillmentLineRow.lot_id == lot_id)
        )
        return Decimal(str(total or 0))

    def list(self, limit: int = 200, after_id: UUID | None = None) -> list[FulfillmentRequest]:
        statement = select(FulfillmentRequestRow)
        if after_id is not None:
            statement = statement.where(FulfillmentRequestRow.id > after_id)
        rows = self.session.scalars(statement.order_by(FulfillmentRequestRow.id).limit(limit))
        return [self._domain(row) for row in rows]

    def add(self, request: FulfillmentRequest) -> None:
        row = self.session.get(FulfillmentRequestRow, request.id)
        if row is None:
            row = FulfillmentRequestRow(id=request.id)
            self.session.add(row)
        row.number = request.number
        row.source_idempotency_key = request.source_idempotency_key
        row.source_fingerprint = request.source_fingerprint
        row.version = request.version
        row.created_at = request.created_at
        row.requester = request.requester
        row.requester_id = request.requester_id
        row.purpose = request.purpose
        row.deadline = request.deadline
        row.status = request.status.value
        row.handout_location = request.handout_location
        row.return_location = request.return_location
        row.shortfall_note = request.shortfall_note
        row.at_work = request.at_work
        row.at_picked = request.at_picked
        row.at_issued = request.at_issued
        row.at_returned = request.at_returned
        row.at_closed = request.at_closed
        self.session.flush()
        existing = {
            line.id: line
            for line in self.session.scalars(
                select(FulfillmentLineRow).where(FulfillmentLineRow.request_id == request.id)
            )
        }
        for position, line in enumerate(request.lines):
            line_row = existing.pop(line.id, None)
            if line_row is None:
                line_row = FulfillmentLineRow(id=line.id, request_id=request.id)
                self.session.add(line_row)
            line_row.position = position
            line_row.requirement_id = line.requirement_id
            line_row.article = line.article
            line_row.name = line.name
            line_row.unit = line.unit
            line_row.requirement = line.requirement
            line_row.reserved = line.reserved
            line_row.issued = line.issued
            line_row.returned_good = line.returned_good
            line_row.defective_returned = line.defective_returned
            line_row.defective_missing = line.defective_missing
            line_row.lot_id = line.lot.lot_id if line.lot else None
            line_row.lot_warehouse = line.lot.warehouse if line.lot else None
            line_row.lot_grn = line.lot.grn if line.lot else None
            line_row.lot_package = line.lot.package if line.lot else None
            line_row.lot_storage_location = line.lot.storage_location if line.lot else None
        for stale in existing.values():
            self.session.delete(stale)


    def delete(self, request_id: UUID) -> None:
        for line in self.session.scalars(
            select(FulfillmentLineRow).where(FulfillmentLineRow.request_id == request_id)
        ):
            self.session.delete(line)
        row = self.session.get(FulfillmentRequestRow, request_id)
        if row is not None:
            self.session.delete(row)
        self.session.flush()


class SqlAlchemyFulfillmentUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.fulfillment = SqlAlchemyFulfillmentRepository(self.session)
        self.warehouse = SqlAlchemyWarehouseRepository(self.session)
        self.writeoffs = SqlAlchemyWriteOffJournal(self.session)
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
