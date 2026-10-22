"""SQLAlchemy adapters for warehouse stock lots."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from datetime import date, datetime

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid, delete,
    func, or_, select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import (
    AuditEventRow, Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository, check_read_version,
    note_written_version, track_read_versions,
)
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.warehouse_location import RackLayout, forget_rack_layout, rack_layout_from_config, storage_location_has_slot

from .domain import WAREHOUSES, StockLot, WriteOffEntry
from .journal import MIRRORED_BY_WRITEOFFS, JournalEvent
from .labels.settings import DEFAULT_PRINT_SETTINGS, LABEL_SIZES, PrintSettings
from .word_search import words_matcher


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
    unit_mass_g: Mapped[Decimal | None] = mapped_column(Numeric(18, 5))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    manufacturer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    storage_conditions: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    datasheet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    certificate: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


track_read_versions(StockLotRow)


class WriteOffEntryRow(Base):
    """The СПИСАНИЕ journal: append-only, one row per movement of one lot (SK-13)."""

    __tablename__ = "warehouse_writeoffs"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_warehouse_writeoffs_source_key"),
        Index("ix_warehouse_writeoffs_occurred_at", "occurred_at"),
        Index("ix_warehouse_writeoffs_lot_id", "lot_id"),
        Index("ix_warehouse_writeoffs_grn", "grn"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    warehouse: Mapped[str] = mapped_column(String(8), nullable=False)
    grn: Mapped[str] = mapped_column(String(32), nullable=False)
    package: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    purpose: Mapped[str] = mapped_column(String(512), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    posted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    remaining_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(255))


_JOURNAL_WRITEOFF_COLUMNS = (
    WriteOffEntryRow.occurred_at, WriteOffEntryRow.kind, WriteOffEntryRow.warehouse, WriteOffEntryRow.grn,
    WriteOffEntryRow.package, WriteOffEntryRow.name, WriteOffEntryRow.quantity, WriteOffEntryRow.unit,
    WriteOffEntryRow.reason, WriteOffEntryRow.purpose, WriteOffEntryRow.author, WriteOffEntryRow.posted,
    WriteOffEntryRow.actor_name,
)


def _writeoff_domain(row: WriteOffEntryRow) -> WriteOffEntry:
    return WriteOffEntry(**{column.name: getattr(row, column.name) for column in WriteOffEntryRow.__table__.columns})


class SqlAlchemyWriteOffJournal:
    """Append-only. A movement already journalled under the same `source_key` is not written again."""

    def __init__(self, session: Session):
        self.session = session

    def add(self, entry: WriteOffEntry) -> None:
        if entry.source_key is not None and self.by_source_key(entry.source_key) is not None:
            return
        self.session.add(WriteOffEntryRow(**{
            column.name: getattr(entry, column.name) for column in WriteOffEntryRow.__table__.columns
        }))

    def by_source_key(self, source_key: str) -> WriteOffEntry | None:
        self.session.flush()
        row = self.session.scalar(select(WriteOffEntryRow).where(WriteOffEntryRow.source_key == source_key))
        return _writeoff_domain(row) if row is not None else None

    def for_lot(self, lot_id: UUID) -> list[WriteOffEntry]:
        self.session.flush()
        rows = self.session.scalars(
            select(WriteOffEntryRow)
            .where(WriteOffEntryRow.lot_id == lot_id)
            .order_by(WriteOffEntryRow.occurred_at, WriteOffEntryRow.id)
        )
        return [_writeoff_domain(row) for row in rows]


class SqlAlchemyWarehouseJournalReader:
    """Read side of «Журнал склада» (SK-16): warehouse audit events with their lot, and the write-off journal,
    each newest first."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _event_filters():
        return (
            AuditEventRow.event_type.like("warehouse.%"),
            AuditEventRow.event_type.not_in(sorted(MIRRORED_BY_WRITEOFFS)),
        )

    def audit_events(self, limit: int | None = None) -> list[JournalEvent]:
        """Only the columns a journal row shows (no entity, no UUID parsing); `limit` — the newest ones."""
        statement = (
            select(
                AuditEventRow.occurred_at, AuditEventRow.event_type, AuditEventRow.actor_name, AuditEventRow.details,
                StockLotRow.grn, StockLotRow.package, StockLotRow.name, StockLotRow.warehouse,
            )
            .outerjoin(StockLotRow, StockLotRow.id == AuditEventRow.aggregate_id)
            .where(*self._event_filters())
            .order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return [
            JournalEvent(
                occurred_at=occurred_at, event_type=event_type, actor_name=actor_name,
                details=details or {}, grn=grn or "", package=package or "", name=name or "",
                warehouse=warehouse or "",
            )
            for occurred_at, event_type, actor_name, details, grn, package, name, warehouse in self.session.execute(
                statement
            )
        ]

    def writeoff_entries(self) -> list[WriteOffEntry]:
        rows = self.session.scalars(
            select(WriteOffEntryRow).order_by(WriteOffEntryRow.occurred_at.desc(), WriteOffEntryRow.id.desc())
        )
        return [_writeoff_domain(row) for row in rows]

    def writeoff_lines(self, limit: int | None = None) -> list:
        """The write-off journal as light rows with the attributes a journal row reads, newest first."""
        statement = select(*_JOURNAL_WRITEOFF_COLUMNS).order_by(
            WriteOffEntryRow.occurred_at.desc(), WriteOffEntryRow.id.desc()
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.execute(statement))

    def totals(self) -> int:
        """How many rows the whole journal has: listed audit events + write-off rows."""
        events = self.session.scalar(select(func.count()).select_from(AuditEventRow).where(*self._event_filters()))
        writeoffs = self.session.scalar(select(func.count()).select_from(WriteOffEntryRow))
        return int(events or 0) + int(writeoffs or 0)


class WarehouseSequenceRow(Base):
    __tablename__ = "warehouse_sequences"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class ItemTypeRow(Base):
    """The saved item type dictionary (SK-14): one row per type, `position` is its place in the dropdown.
    No rows — nobody saved a list yet, and the canonical one from the code is used (stock05TypesList_)."""

    __tablename__ = "warehouse_item_types"

    position: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class SqlAlchemyItemTypeDictionary:
    def __init__(self, session: Session):
        self.session = session

    def saved(self) -> list[str]:
        return list(self.session.scalars(select(ItemTypeRow.name).order_by(ItemTypeRow.position)))

    def replace(self, types: list[str]) -> None:
        self.session.execute(delete(ItemTypeRow))
        self.session.add_all(ItemTypeRow(position=index, name=name) for index, name in enumerate(types))


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
        unit_mass_g=lot.unit_mass_g,
        expiry_date=lot.expiry_date,
        manufacturer=lot.manufacturer,
        description=lot.description,
        storage_conditions=lot.storage_conditions,
        datasheet=lot.datasheet,
        certificate=lot.certificate,
    )


# The columns a search looks at; a search reads these and the id, never the whole row.
_SEARCH_COLUMNS = (
    StockLotRow.grn, StockLotRow.package, StockLotRow.article, StockLotRow.series, StockLotRow.name,
    StockLotRow.item_type, StockLotRow.purpose, StockLotRow.storage_location,
)


def lot_matcher(query: str) -> Callable[[object], bool]:
    """The stock search (`word_search`) as a predicate over anything with the searched attributes (a lot or a row of
    `_SEARCH_COLUMNS`); the query is folded once, not once per lot."""
    return words_matcher(query)


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
        unit_mass_g=row.unit_mass_g,
        expiry_date=row.expiry_date,
        manufacturer=row.manufacturer,
        description=row.description,
        storage_conditions=row.storage_conditions,
        datasheet=row.datasheet,
        certificate=row.certificate,
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
        return self.page(limit, after_id, query, warehouse, include_zero, with_total=False)[0]

    def count(
        self,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> int:
        if str(query or "").strip():
            return len(self._matching_ids(query, warehouse, include_zero))
        statement = select(func.count()).select_from(StockLotRow).where(
            *self._filters(warehouse, include_zero)
        )
        return int(self.session.scalar(statement) or 0)

    def filtered(
        self,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> list[StockLot]:
        """Every lot under the same filters as `list`, no paging, in the same id order (the source of the group
        totals and of the whole-stock reads that must not lose lots past a page). A search first picks the matching ids
        from the searched columns only (`_matching_ids`, the query folded once), then loads those rows."""
        if not str(query or "").strip():
            rows = self.session.scalars(
                select(StockLotRow).where(*self._filters(warehouse, include_zero)).order_by(StockLotRow.id)
            )
            return [_domain(row) for row in rows]
        ids = sorted(self._matching_ids(query, warehouse, include_zero))
        lots: list[StockLot] = []
        for start in range(0, len(ids), 500):
            lots += [_domain(row) for row in self.session.scalars(
                select(StockLotRow).where(StockLotRow.id.in_(ids[start:start + 500])).order_by(StockLotRow.id)
            )]
        return lots

    def page(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
        *,
        with_total: bool = True,
    ) -> tuple[list[StockLot], int]:
        """One page in id order after the cursor and the count of every match (the cursor ignored).

        A search reads only the id and the searched columns of the filtered lots, matches them in Python (the folding
        of `word_search` has no SQL twin, so no SQL text prefilter that could drop a match) and then
        loads the full rows of the page alone. Without a search everything is SQL: filter, order, limit, count."""
        if not str(query or "").strip():
            statement = select(StockLotRow).where(*self._filters(warehouse, include_zero))
            if after_id is not None:
                statement = statement.where(StockLotRow.id > after_id)
            rows = self.session.scalars(statement.order_by(StockLotRow.id).limit(limit))
            lots = [_domain(row) for row in rows]
            return lots, (self.count("", warehouse, include_zero) if with_total else len(lots))
        matched = self._matching_ids(query, warehouse, include_zero)
        page_ids = [lot_id for lot_id in matched if after_id is None or lot_id > after_id][:limit]
        lots: list[StockLot] = []
        for start in range(0, len(page_ids), 500):
            chunk = page_ids[start:start + 500]
            lots += [_domain(row) for row in self.session.scalars(
                select(StockLotRow).where(StockLotRow.id.in_(chunk))
            )]
        lots.sort(key=lambda lot: lot.id)
        return lots, len(matched)

    def _matching_ids(self, query: str, warehouse: str | None, include_zero: bool) -> list[UUID]:
        """Ids (ascending) of the filtered lots the search matches; the query is folded once per call."""
        matches = lot_matcher(query)
        rows = self.session.execute(
            select(StockLotRow.id, *_SEARCH_COLUMNS)
            .where(*self._filters(warehouse, include_zero))
            .order_by(StockLotRow.id)
        )
        return [row.id for row in rows if matches(row)]

    def every(self) -> list[StockLot]:
        """Every lot, zero balance included, ЦАР before ЖУК (the sheet order of SUPPLYMAP), then by ГРН and пакет."""
        # Plain rows, not entities (autoflush still runs): no identity map for thousands of lots read once.
        rows = self.session.execute(
            select(*StockLotRow.__table__.columns).order_by(StockLotRow.grn, StockLotRow.package)
        )
        order = {name: index for index, name in enumerate(WAREHOUSES)}
        return sorted((_domain(row) for row in rows), key=lambda lot: order.get(lot.warehouse, len(order)))

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

    def with_grn_ending(self, tail: str) -> list[StockLot]:
        """Lots whose ГРН ends with «.26.0000001» (zero balance too): the scan narrows them by prefix and package."""
        rows = self.session.scalars(
            select(StockLotRow).where(StockLotRow.grn.like(f"%{tail}"))
            .order_by(StockLotRow.grn, StockLotRow.package, StockLotRow.warehouse)
        )
        return [_domain(row) for row in rows]

    def with_balance_near(self, place: str) -> list[StockLot]:
        """Lots with a balance whose place starts with the rack code (both warehouses); the scan keeps the place."""
        rows = self.session.scalars(
            select(StockLotRow).where(
                StockLotRow.storage_location.like(f"{place}%"),
                StockLotRow.inbound - StockLotRow.written_off > 0,
            ).order_by(StockLotRow.warehouse, StockLotRow.storage_location, StockLotRow.grn, StockLotRow.package)
        )
        return [_domain(row) for row in rows]

    def occupied_locations(self, warehouse: str) -> list[str]:
        """Places of the lots that still have a balance: an emptied lot frees its place (SK-15)."""
        return list(self.session.scalars(select(StockLotRow.storage_location).where(
            StockLotRow.warehouse == warehouse,
            StockLotRow.storage_location != "",
            StockLotRow.inbound - StockLotRow.written_off > 0,
        ).distinct()))

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
        return lot_matcher(query)(lot)

    def recent_events(self, lot_id: UUID, limit: int = 20) -> list[AuditEvent]:
        """The lot's newest warehouse audit events, newest first («Отменить последнее»)."""
        self.session.flush()
        rows = self.session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.aggregate_id == lot_id, AuditEventRow.event_type.like("warehouse.%"),
            ).order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc()).limit(limit)
        )
        return [
            AuditEvent(
                id=row.id, aggregate_id=row.aggregate_id, event_type=row.event_type,
                actor=Actor(id=row.actor_id, name=row.actor_name, department=row.actor_department),
                occurred_at=row.occurred_at, details=dict(row.details or {}),
            )
            for row in rows
        ]

    def received_at(self, lot_id: UUID) -> datetime | None:
        """When the lot came on stock: its first arrival event — from receiving, entered by hand or carried over from
        SUPPLYMAP (the GRN label prints it as the date). None — no such event."""
        self.session.flush()
        return self.session.scalar(
            select(func.min(AuditEventRow.occurred_at)).where(
                AuditEventRow.aggregate_id == lot_id,
                AuditEventRow.event_type.in_(LOT_ARRIVAL_EVENTS),
            )
        )

    def audit_trail(self, lot_id: UUID) -> list[tuple[str, dict]]:
        """(event_type, details) of every audit event on the lot, oldest first."""
        self.session.flush()
        rows = self.session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == lot_id)
            .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
        )
        return [(row.event_type, dict(row.details or {})) for row in rows]

    def add(self, lot: StockLot) -> None:
        incoming = _row(lot)
        stored = self.session.get(StockLotRow, lot.id)
        if stored is None:
            self.session.add(incoming)
            return
        # Compare-and-swap against the version this transaction read (StockVersionConflict on a lost race).
        check_read_version(self.session, stored, lot.version)
        for column in StockLotRow.__table__.columns:
            if column.name != "id":
                setattr(stored, column.name, getattr(incoming, column.name))
        note_written_version(self.session, stored)


# The events that bring a lot onto the stock (warehouse.received, by hand, the SUPPLYMAP carry-over).
LOT_ARRIVAL_EVENTS = ("warehouse.received", "warehouse.manually_received", "warehouse.legacy_imported")


class WarehouseLabelSettingsRow(Base):
    """«Печать этикеток ГРН»: the label printer's settings, one row per installation.
    The CHECK constraints repeat `PrintSettings`' bounds (migration 0125)."""

    __tablename__ = "warehouse_label_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_warehouse_label_settings_single_row"),
        CheckConstraint("code_type IN ('qr', 'code128', 'both')", name="ck_warehouse_label_settings_code_type"),
        CheckConstraint("width_mm >= 45 AND width_mm <= 110", name="ck_warehouse_label_settings_width"),
        CheckConstraint("height_mm >= 20 AND height_mm <= 80", name="ck_warehouse_label_settings_height"),
        CheckConstraint("dpi IN (203, 300)", name="ck_warehouse_label_settings_dpi"),
        CheckConstraint("gap_mm >= 0 AND gap_mm <= 20", name="ck_warehouse_label_settings_gap"),
        CheckConstraint("density >= 0 AND density <= 15", name="ck_warehouse_label_settings_density"),
        CheckConstraint("speed >= 1 AND speed <= 12", name="ck_warehouse_label_settings_speed"),
        CheckConstraint("offset_x_mm >= -5 AND offset_x_mm <= 5", name="ck_warehouse_label_settings_offset_x"),
        CheckConstraint("offset_y_mm >= -5 AND offset_y_mm <= 5", name="ck_warehouse_label_settings_offset_y"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    printer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    code_type: Mapped[str] = mapped_column(String(8), nullable=False)
    width_mm: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    height_mm: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    dpi: Mapped[int] = mapped_column(Integer, nullable=False)
    gap_mm: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    density: Mapped[int] = mapped_column(Integer, nullable=False)
    speed: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_x_mm: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    offset_y_mm: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")


_LABEL_MM = ("width_mm", "height_mm", "gap_mm", "offset_x_mm", "offset_y_mm")
_LABEL_WHOLE = ("printer_name", "code_type", "dpi", "density", "speed")


class SqlAlchemyLabelSettings:
    """The label printer settings row; no row — None (the caller takes the defaults)."""

    def __init__(self, session: Session):
        self.session = session

    def load(self) -> PrintSettings | None:
        row = self.session.get(WarehouseLabelSettingsRow, 1)
        if row is None:
            return None
        values = {
            **{name: getattr(row, name) for name in _LABEL_WHOLE},
            **{name: float(getattr(row, name)) for name in _LABEL_MM},
        }
        if (values["width_mm"], values["height_mm"]) not in LABEL_SIZES:
            # A size saved when it was a free field: printed in the default format until it is chosen again.
            values["width_mm"], values["height_mm"] = DEFAULT_PRINT_SETTINGS.width_mm, DEFAULT_PRINT_SETTINGS.height_mm
        return PrintSettings(**values)

    def save(self, settings: PrintSettings, updated_at: datetime, updated_by: str) -> None:
        row = self.session.get(WarehouseLabelSettingsRow, 1)
        if row is None:
            row = WarehouseLabelSettingsRow(id=1)
            self.session.add(row)
        for name in _LABEL_WHOLE:
            setattr(row, name, getattr(settings, name))
        for name in _LABEL_MM:
            setattr(row, name, Decimal(str(getattr(settings, name))).quantize(Decimal("0.01")))
        row.updated_at = updated_at
        row.updated_by = updated_by


class SqlAlchemyWarehouseUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.warehouse = SqlAlchemyWarehouseRepository(self.session)
        self.writeoffs = SqlAlchemyWriteOffJournal(self.session)
        self.journal = SqlAlchemyWarehouseJournalReader(self.session)
        self.item_types = SqlAlchemyItemTypeDictionary(self.session)
        self.label_settings = SqlAlchemyLabelSettings(self.session)
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


class WarehouseRackLayoutRow(Base):
    """«Раскладка» склада (SUPPLYMAP ScriptProperties stock05_places): одна строка на установку."""

    __tablename__ = "warehouse_rack_layout"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    racks: Mapped[int] = mapped_column(Integer)
    shelves: Mapped[int] = mapped_column(Integer)
    per_rack: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    updated_by: Mapped[str] = mapped_column(String(255), default="")


class SqlAlchemyRackLayoutStore:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def load(self) -> RackLayout | None:
        with self.session_factory() as session:
            row = session.get(WarehouseRackLayoutRow, 1)
            if row is None:
                return None
            return rack_layout_from_config({"racks": row.racks, "shelves": row.shelves, "perRack": row.per_rack or {}})

    def save(self, layout: RackLayout, event: AuditEvent) -> None:
        with self.session_factory() as session:
            row = session.get(WarehouseRackLayoutRow, 1)
            if row is None:
                row = WarehouseRackLayoutRow(id=1)
                session.add(row)
            row.racks = layout.racks
            row.shelves = layout.shelves
            row.per_rack = {str(rack): count for rack, count in sorted(layout.per_rack.items())}
            row.updated_at = event.occurred_at
            row.updated_by = event.actor.name
            SqlAlchemyAuditRepository(session).add(event)
            session.commit()
        forget_rack_layout()
