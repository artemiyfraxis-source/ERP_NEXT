"""SQLAlchemy persistence for finished goods and shipments."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid, and_, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base, SqlAlchemyAuditRepository

from .domain import DeliveryTerms, FinishedGood, Shipment, ShipmentLine, ShipmentStatus, UpdType


class FinishedGoodRow(Base):
    __tablename__ = "finished_goods"
    __table_args__ = (Index("ux_finished_goods_project_item", "project", "item_index", unique=True),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    item_index: Mapped[str] = mapped_column(String(255), nullable=False)
    customer: Mapped[str] = mapped_column(String(512), nullable=False)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    ordered: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    shipped: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    received_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    grn: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    note: Mapped[str] = mapped_column(Text(), nullable=False)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ShipmentRow(Base):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_created_id", "created_at", "id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer: Mapped[str] = mapped_column(String(512), nullable=False)
    receiver: Mapped[str] = mapped_column(String(512), nullable=False)
    shipping_point: Mapped[str] = mapped_column(Text(), nullable=False)
    contact: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(128), nullable=False)
    terms: Mapped[str] = mapped_column(String(32), nullable=False)
    carrier: Mapped[str] = mapped_column(String(255), nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    upd_type: Mapped[str] = mapped_column(String(32), nullable=False)
    upd_number: Mapped[str] = mapped_column(String(255), nullable=False)
    upd_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    upd_url: Mapped[str] = mapped_column(Text(), nullable=False)
    pl_number: Mapped[str] = mapped_column(String(255), nullable=False)
    dn_number: Mapped[str] = mapped_column(String(255), nullable=False)
    track_number: Mapped[str] = mapped_column(String(255), nullable=False)
    packed_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    shipped_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    manager: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str] = mapped_column(Text(), nullable=False)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ShipmentLineRow(Base):
    __tablename__ = "shipment_lines"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    finished_good_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("finished_goods.id", ondelete="RESTRICT"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(255), nullable=False)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    requested: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    picked: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    grn: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ShippingSequenceRow(Base):
    __tablename__ = "shipping_sequences"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


def _good(row: FinishedGoodRow) -> FinishedGood:
    return FinishedGood(
        id=row.id, version=row.version, project=row.project, item_index=row.item_index,
        customer=row.customer, article=row.article, name=row.name, ordered=row.ordered,
        unit=row.unit, accepted=row.accepted, shipped=row.shipped,
        received_at=row.received_at, grn=row.grn, note=row.note,
    )


class SqlAlchemyFinishedGoodsRepository:
    def __init__(self, session: Session): self.session = session
    def get(self, good_id: UUID) -> FinishedGood | None:
        row = self.session.get(FinishedGoodRow, good_id)
        return _good(row) if row is not None else None
    def by_key(self, project: str, item_index: str) -> FinishedGood | None:
        row = self.session.scalar(select(FinishedGoodRow).where(and_(FinishedGoodRow.project == project, FinishedGoodRow.item_index == item_index)))
        return _good(row) if row is not None else None
    def list(self, limit: int = 500) -> list[FinishedGood]:
        rows = self.session.scalars(select(FinishedGoodRow).order_by(FinishedGoodRow.project, FinishedGoodRow.item_index).limit(limit))
        return [_good(row) for row in rows]
    def reserve_grn(self, year: int) -> str:
        key = f"grn-{year % 100:02d}"
        row = self.session.scalar(select(ShippingSequenceRow).where(ShippingSequenceRow.name == key).with_for_update())
        if row is None:
            row = ShippingSequenceRow(name=key, value=0); self.session.add(row); self.session.flush()
        row.value += 1
        return f"ГП.{year % 100:02d}.{row.value:07d}"
    def add(self, good: FinishedGood) -> None:
        row = self.session.get(FinishedGoodRow, good.id)
        if row is None:
            row = FinishedGoodRow(id=good.id); self.session.add(row)
        for key in ("version", "project", "item_index", "customer", "article", "name", "ordered", "unit", "accepted", "shipped", "received_at", "grn", "note"):
            setattr(row, key, getattr(good, key))


class SqlAlchemyShipmentRepository:
    def __init__(self, session: Session): self.session = session
    def _domain(self, row: ShipmentRow) -> Shipment:
        lines = self.session.scalars(select(ShipmentLineRow).where(ShipmentLineRow.shipment_id == row.id).order_by(ShipmentLineRow.position))
        return Shipment(
            id=row.id, number=row.number, version=row.version, created_at=row.created_at,
            status=ShipmentStatus(row.status), customer=row.customer, receiver=row.receiver,
            shipping_point=row.shipping_point, contact=row.contact, phone=row.phone,
            terms=DeliveryTerms(row.terms), carrier=row.carrier, deadline=row.deadline,
            upd_type=UpdType(row.upd_type), upd_number=row.upd_number, upd_date=row.upd_date,
            upd_url=row.upd_url, pl_number=row.pl_number, dn_number=row.dn_number,
            track_number=row.track_number, packed_at=row.packed_at, shipped_at=row.shipped_at,
            manager=row.manager, note=row.note,
            lines=tuple(ShipmentLine(
                id=line.id, finished_good_id=line.finished_good_id, project=line.project,
                item_index=line.item_index, article=line.article, name=line.name,
                requested=line.requested, picked=line.picked, unit=line.unit, grn=line.grn,
            ) for line in lines),
        )
    def get(self, shipment_id: UUID) -> Shipment | None:
        row = self.session.get(ShipmentRow, shipment_id)
        return self._domain(row) if row is not None else None
    def list(self, limit: int = 200) -> list[Shipment]:
        rows = self.session.scalars(select(ShipmentRow).order_by(ShipmentRow.created_at.desc(), ShipmentRow.id.desc()).limit(limit))
        return [self._domain(row) for row in rows]
    def reserve_number(self, day: date) -> str:
        stamp = day.strftime("%Y%m%d"); key = f"shipment-{stamp}"
        row = self.session.scalar(select(ShippingSequenceRow).where(ShippingSequenceRow.name == key).with_for_update())
        if row is None:
            row = ShippingSequenceRow(name=key, value=0); self.session.add(row); self.session.flush()
        row.value += 1
        return f"ОТГ-{stamp}-{row.value:02d}"
    def document_sequence(self, project: str) -> int:
        key = "document:" + sha256(project.encode("utf-8")).hexdigest()[:48]
        row = self.session.scalar(select(ShippingSequenceRow).where(ShippingSequenceRow.name == key).with_for_update())
        if row is None:
            row = ShippingSequenceRow(name=key, value=0); self.session.add(row); self.session.flush()
        row.value += 1
        return row.value
    def add(self, shipment: Shipment) -> None:
        row = self.session.get(ShipmentRow, shipment.id)
        if row is None:
            row = ShipmentRow(id=shipment.id); self.session.add(row)
        for key in (
            "number", "version", "created_at", "customer", "receiver", "shipping_point",
            "contact", "phone", "carrier", "deadline", "upd_number", "upd_date", "upd_url",
            "pl_number", "dn_number", "track_number", "packed_at", "shipped_at", "manager", "note",
        ):
            setattr(row, key, getattr(shipment, key))
        row.status = shipment.status.value; row.terms = shipment.terms.value; row.upd_type = shipment.upd_type.value
        existing = {line.id: line for line in self.session.scalars(select(ShipmentLineRow).where(ShipmentLineRow.shipment_id == shipment.id))}
        for position, line in enumerate(shipment.lines):
            stored = existing.pop(line.id, None)
            if stored is None:
                stored = ShipmentLineRow(id=line.id, shipment_id=shipment.id); self.session.add(stored)
            stored.position = position
            for key in ("finished_good_id", "project", "item_index", "article", "name", "requested", "picked", "unit", "grn"):
                setattr(stored, key, getattr(line, key))


class SqlAlchemyShippingUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory; self.session: Session | None = None; self.committed = False
    def __enter__(self):
        self.committed = False; self.session = self.session_factory()
        self.goods = SqlAlchemyFinishedGoodsRepository(self.session)
        self.shipments = SqlAlchemyShipmentRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self
    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None: return
        if exc_type is not None or not self.committed: self.session.rollback()
        self.session.close()
    def commit(self) -> None:
        if self.session is None: raise RuntimeError("Unit of Work has not been entered")
        self.session.commit(); self.committed = True
