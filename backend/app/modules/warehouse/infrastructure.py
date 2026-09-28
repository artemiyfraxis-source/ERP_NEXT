"""SQLAlchemy adapters for warehouse stock lots."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, Index, Integer, Numeric, String, UniqueConstraint, Uuid, func, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository
from app.shared.warehouse_location import storage_location_has_slot

from .domain import StockLot


class StockLotRow(Base):
    __tablename__ = "warehouse_lots"
    __table_args__ = (
        UniqueConstraint("warehouse", "grn", "package", name="uq_warehouse_lot_key"),
        Index("ix_warehouse_location", "warehouse", "storage_location"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    warehouse: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    grn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    package: Mapped[str] = mapped_column(String(32), nullable=False)
    article: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    series: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_type: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_location: Mapped[str] = mapped_column(String(255), nullable=False)
    inbound: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    written_off: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source_receiving_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class WarehouseSequenceRow(Base):
    __tablename__ = "warehouse_sequences"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


def _row(lot: StockLot) -> StockLotRow:
    return StockLotRow(
        id=lot.id,
        version=lot.version,
        warehouse=lot.warehouse,
        grn=lot.grn,
        package=lot.package,
        article=lot.article,
        series=lot.series,
        name=lot.name,
        item_type=lot.item_type,
        purpose=lot.purpose,
        unit=lot.unit,
        storage_location=lot.storage_location,
        inbound=lot.inbound,
        written_off=lot.written_off,
        reserved=lot.reserved,
        source_receiving_ids=list(lot.source_receiving_ids),
    )


def _domain(row: StockLotRow) -> StockLot:
    return StockLot(
        id=row.id,
        version=row.version,
        warehouse=row.warehouse,
        grn=row.grn,
        package=row.package,
        article=row.article,
        series=row.series,
        name=row.name,
        item_type=row.item_type,
        purpose=row.purpose,
        unit=row.unit,
        storage_location=row.storage_location,
        inbound=row.inbound,
        written_off=row.written_off,
        reserved=row.reserved,
        source_receiving_ids=tuple(row.source_receiving_ids),
    )


class SqlAlchemyWarehouseRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, lot_id: UUID) -> StockLot | None:
        row = self.session.get(StockLotRow, lot_id)
        return _domain(row) if row is not None else None

    def by_key(self, warehouse: str, grn: str, package: str) -> StockLot | None:
        row = self.session.scalar(
            select(StockLotRow).where(
                StockLotRow.warehouse == warehouse,
                StockLotRow.grn == grn,
                StockLotRow.package == package,
            )
        )
        return _domain(row) if row is not None else None

    def reserve_manual_numbers(self, year: int, count: int) -> int:
        name = f"manual:{year}"
        row = self.session.scalar(
            select(WarehouseSequenceRow)
            .where(WarehouseSequenceRow.name == name)
            .with_for_update()
        )
        if row is None:
            self.session.add(WarehouseSequenceRow(name=name, value=count))
            return 1
        first = row.value + 1
        row.value += count
        return first

    def ensure_manual_number(self, year: int, number: int) -> int:
        """Move the manual sequence past an imported OLD number; never moves it back."""
        name = f"manual:{year}"
        row = self.session.scalar(
            select(WarehouseSequenceRow)
            .where(WarehouseSequenceRow.name == name)
            .with_for_update()
        )
        if row is None:
            self.session.add(WarehouseSequenceRow(name=name, value=number))
            return number
        row.value = max(row.value, number)
        return row.value

    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> list[StockLot]:
        statement = select(StockLotRow).where(*self._filters(warehouse, include_zero))
        if after_id is not None:
            statement = statement.where(StockLotRow.id > after_id)
        statement = statement.order_by(StockLotRow.id)
        if not str(query or "").strip():
            statement = statement.limit(limit)
        rows = self.session.scalars(statement)
        lots = [_domain(row) for row in rows]
        if query:
            lots = [lot for lot in lots if self._matches(lot, query)]
        return lots[:limit]

    def count(
        self,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> int:
        if str(query or "").strip():
            rows = self.session.scalars(
                select(StockLotRow).where(*self._filters(warehouse, include_zero))
            )
            return sum(self._matches(_domain(row), query) for row in rows)
        statement = select(func.count()).select_from(StockLotRow).where(
            *self._filters(warehouse, include_zero)
        )
        return int(self.session.scalar(statement) or 0)

    def at_location(self, warehouse: str, storage_location: str) -> list[StockLot]:
        location_filter = StockLotRow.storage_location == storage_location
        if not storage_location_has_slot(storage_location):
            location_filter = or_(
                location_filter,
                StockLotRow.storage_location.like(f"{storage_location}_"),
            )
        rows = self.session.scalars(select(StockLotRow).where(
            StockLotRow.warehouse == warehouse,
            StockLotRow.inbound - StockLotRow.written_off > 0,
            location_filter,
        ).order_by(StockLotRow.grn, StockLotRow.package).with_for_update())
        return [_domain(row) for row in rows]

    @staticmethod
    def _filters(warehouse: str | None, include_zero: bool):
        filters = []
        if warehouse:
            filters.append(StockLotRow.warehouse == warehouse)
        if not include_zero:
            filters.append(StockLotRow.inbound - StockLotRow.written_off > 0)
        return filters

    @staticmethod
    def _matches(lot: StockLot, query: str) -> bool:
        needle = str(query or "").strip().casefold()
        haystack = " ".join((
            lot.grn,
            lot.package,
            lot.article,
            lot.series,
            lot.name,
            lot.item_type,
            lot.purpose,
            lot.storage_location,
        )).casefold()
        return needle in haystack

    def add(self, lot: StockLot) -> None:
        incoming = _row(lot)
        stored = self.session.get(StockLotRow, lot.id)
        if stored is None:
            self.session.add(incoming)
            return
        for column in StockLotRow.__table__.columns:
            if column.name != "id":
                setattr(stored, column.name, getattr(incoming, column.name))


class SqlAlchemyWarehouseUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.warehouse = SqlAlchemyWarehouseRepository(self.session)
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
