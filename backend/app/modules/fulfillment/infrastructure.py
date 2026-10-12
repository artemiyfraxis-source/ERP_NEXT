"""SQLAlchemy persistence for production-kit fulfillment."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid, false, func,
    select, text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.infrastructure import SqlAlchemyStockCoverRepository
from app.modules.production.domain import ProductionStatus
from app.shared.project_number import digit_stem, normalize_project_number, project_from_purpose
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository, SqlAlchemyWriteOffJournal
from app.shared.persistence import AuditEventRow, Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository

from .application import kit_key, next_request_number, request_sequence
from .domain import (
    FulfillmentKind, FulfillmentLine, FulfillmentRequest, FulfillmentStatus, KitReserve, LotSnapshot, kb_of,
)

# The partial UNIQUE of migration 0089: one live (not «Отменена») MAIN request per project + КБ; «Довыдача» is free.
LIVE_KIT_WHERE = (
    "kind = 'main' AND status <> 'Отменена' AND project_key IS NOT NULL AND item_index IS NOT NULL"
)


class FulfillmentRequestRow(Base):
    __tablename__ = "fulfillment_requests"
    __table_args__ = (
        Index("ix_fulfillment_status_created_id", "status", "created_at", "id"),
        Index("ix_fulfillment_created_id", "created_at", "id"),
        CheckConstraint("kind IN ('main', 'extra')", name="ck_fulfillment_requests_kind"),
        # One live request per КБ of a project (migration 0089): a second «Отправить на комплектацию» can't double it.
        Index(
            "uq_fulfillment_requests_live_kit", "project_key", "item_index", unique=True,
            postgresql_where=text(LIVE_KIT_WHERE), sqlite_where=text(LIVE_KIT_WHERE),
        ),
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
    # The project and КБ of a kit request, normalized (`kit_key` of НАЗНАЧЕНИЕ); NULL for a request without them.
    project_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    item_index: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="main", server_default="main")
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


class FulfillmentRequestCounterRow(Base):
    """The last request number given out in a year: numbers only grow from it, a deleted one is never reused."""

    __tablename__ = "fulfillment_request_counters"
    __table_args__ = (CheckConstraint("last_number >= 0", name="ck_fulfillment_request_counters_last_number"),)

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False)


# «нет на складе» (migration 0104): who and when together, and only on a line without «есть, но не заведено», a lot,
# a reserve or an issue — the database refuses any other combination.
ABSENT_WHO_WHEN_CHECK = "(absent_at IS NULL AND absent_by IS NULL) OR (absent_at IS NOT NULL AND absent_by IS NOT NULL)"
ABSENT_ALONE_CHECK = "absent_at IS NULL OR (NOT off_book AND lot_id IS NULL AND reserved = 0 AND issued = 0)"


class FulfillmentLineRow(Base):
    __tablename__ = "fulfillment_lines"
    __table_args__ = (
        CheckConstraint(ABSENT_WHO_WHEN_CHECK, name="ck_fulfillment_lines_absent_who_when"),
        CheckConstraint(ABSENT_ALONE_CHECK, name="ck_fulfillment_lines_absent_alone"),
    )

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
    # «есть, но не заведено»: passes without a lot and without a stock movement (migration 0087).
    off_book: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default=false())
    # «нет на складе»: when and who confirmed the part is physically absent (migration 0104).
    absent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    absent_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


_OPEN_STATUSES = (FulfillmentStatus.NEW, FulfillmentStatus.UNDERFILLED, FulfillmentStatus.COMPLETE)


class FulfillmentKitReserveRow(Base):
    """A techcard reserve of one project КБ on one warehouse lot (KitReserve)."""

    __tablename__ = "fulfillment_kit_reserves"
    __table_args__ = (
        Index("ix_fulfillment_kit_reserves_lot_quantity", "lot_id", "quantity"),
        Index("ix_fulfillment_kit_reserves_project_item", "project", "item_index"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[str] = mapped_column(String(128), nullable=False)
    item_index: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    article: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    lot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    warehouse: Mapped[str] = mapped_column(String(8), nullable=False)
    grn: Mapped[str] = mapped_column(String(32), nullable=False)
    package: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    consumed: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0")
    consumed_by: Mapped[str] = mapped_column(Text(), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


_KIT_RESERVE_FIELDS = (
    "version", "project", "item_index", "article", "lot_id", "warehouse", "grn", "package", "unit", "quantity",
    "consumed", "consumed_by", "created_at", "created_by", "released_at",
)


def _kit_reserve(row: FulfillmentKitReserveRow) -> KitReserve:
    return KitReserve(id=row.id, **{field: getattr(row, field) for field in _KIT_RESERVE_FIELDS})


class SqlAlchemyKitReserveRepository:
    """Techcard reserves. A kit request of the same project + КБ takes them over (`_reserve_for_kit`); the warehouse
    row edit counts them in the kitting floor (`reserved_on_lot`)."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, reserve_id: UUID) -> KitReserve | None:
        row = self.session.get(FulfillmentKitReserveRow, reserve_id)
        return _kit_reserve(row) if row is not None else None

    def active_on_lots(self, lot_ids) -> list[KitReserve]:
        if not lot_ids:
            return []
        rows = self.session.scalars(
            select(FulfillmentKitReserveRow)
            .where(FulfillmentKitReserveRow.lot_id.in_(tuple(lot_ids)), FulfillmentKitReserveRow.quantity > 0)
            .order_by(FulfillmentKitReserveRow.created_at, FulfillmentKitReserveRow.id)
        )
        return [_kit_reserve(row) for row in rows]

    def for_project(self, project: str) -> list[KitReserve]:
        rows = self.session.scalars(
            select(FulfillmentKitReserveRow)
            .where(FulfillmentKitReserveRow.project == project)
            .order_by(FulfillmentKitReserveRow.created_at, FulfillmentKitReserveRow.id)
        )
        return [_kit_reserve(row) for row in rows]

    def reserved_on_lot(self, lot_id: UUID) -> Decimal:
        total = self.session.scalar(
            select(func.coalesce(func.sum(FulfillmentKitReserveRow.quantity), 0))
            .where(FulfillmentKitReserveRow.lot_id == lot_id)
        )
        return Decimal(str(total or 0))

    def add(self, reserve: KitReserve) -> None:
        stored = self.session.get(FulfillmentKitReserveRow, reserve.id)
        if stored is None:
            stored = FulfillmentKitReserveRow(id=reserve.id)
            self.session.add(stored)
        for field in _KIT_RESERVE_FIELDS:
            setattr(stored, field, getattr(reserve, field))


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
        off_book=bool(row.off_book),
        absent_at=row.absent_at,
        absent_by=row.absent_by or "",
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
            kind=FulfillmentKind(row.kind or FulfillmentKind.MAIN.value),
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

    def next_number(self, year: int) -> str:
        """The year's counter row, locked for the transaction: above the last number ever given out and above every
        existing one, so a deleted request's number never comes back (migration 0089)."""
        counter = self.session.scalar(
            select(FulfillmentRequestCounterRow).where(FulfillmentRequestCounterRow.year == year).with_for_update()
        )
        number = next_request_number(year, self.numbers(year), counter.last_number if counter is not None else 0)
        if counter is None:
            self.session.add(FulfillmentRequestCounterRow(year=year, last_number=request_sequence(number)))
        else:
            counter.last_number = request_sequence(number)
        self.session.flush()
        return number

    def live_kit_request(self, project_key: str, item_index: str) -> FulfillmentRequest | None:
        """The request of this КБ of the project in any status but «Отменена» (at most one: the partial UNIQUE)."""
        row = self.session.scalar(
            select(FulfillmentRequestRow).where(
                FulfillmentRequestRow.project_key == project_key,
                FulfillmentRequestRow.item_index == item_index,
                FulfillmentRequestRow.kind == FulfillmentKind.MAIN.value,
                FulfillmentRequestRow.status != FulfillmentStatus.CANCELLED.value,
            ).order_by(FulfillmentRequestRow.created_at, FulfillmentRequestRow.number).limit(1)
        )
        return self._domain(row) if row is not None else None

    def reserved_on_lot(self, lot_id: UUID) -> Decimal:
        """What kitting holds in reserve on one warehouse lot — kit lines plus techcard reserves (the warehouse row
        edit may not go under it)."""
        total = self.session.scalar(
            select(func.coalesce(func.sum(FulfillmentLineRow.reserved), 0)).where(FulfillmentLineRow.lot_id == lot_id)
        )
        return Decimal(str(total or 0)) + SqlAlchemyKitReserveRepository(self.session).reserved_on_lot(lot_id)

    def holders_on_lots(self, lot_ids) -> list[tuple[UUID, str, str, UUID, str, Decimal]]:
        """Kit lines holding reserve on the given lots: (request id, number, purpose, lot id, unit, reserved), the
        same lines `reserved_on_lot` sums (no status filter), in creation order."""
        if not lot_ids:
            return []
        rows = self.session.execute(
            select(
                FulfillmentRequestRow.id, FulfillmentRequestRow.number, FulfillmentRequestRow.purpose,
                FulfillmentLineRow.lot_id, FulfillmentLineRow.unit, FulfillmentLineRow.reserved,
            )
            .join(FulfillmentLineRow, FulfillmentLineRow.request_id == FulfillmentRequestRow.id)
            .where(FulfillmentLineRow.lot_id.in_(tuple(lot_ids)), FulfillmentLineRow.reserved > 0)
            .order_by(FulfillmentRequestRow.created_at, FulfillmentRequestRow.number, FulfillmentLineRow.position)
        )
        return [(row[0], row[1], row[2], row[3], row[4], Decimal(str(row[5]))) for row in rows]

    def open_requests(self) -> list[FulfillmentRequest]:
        """Requests whose lots can still change («Новая», «Недоукомплектован», «Укомплектован»), creation order."""
        rows = self.session.scalars(
            select(FulfillmentRequestRow)
            .where(FulfillmentRequestRow.status.in_(tuple(status.value for status in _OPEN_STATUSES)))
            .order_by(FulfillmentRequestRow.created_at, FulfillmentRequestRow.number)
        )
        return [self._domain(row) for row in rows]

    def list(self, limit: int = 200, after_id: UUID | None = None) -> list[FulfillmentRequest]:
        # Creation order, as SUPPLYMAP reads the ВЫДАЧА sheet top to bottom; ids are random, so the page cursor
        # is (created_at, number) of the last row, not the id alone.
        statement = select(FulfillmentRequestRow)
        if after_id is not None:
            cursor = self.session.get(FulfillmentRequestRow, after_id)
            if cursor is not None:
                statement = statement.where(
                    (FulfillmentRequestRow.created_at > cursor.created_at)
                    | ((FulfillmentRequestRow.created_at == cursor.created_at) & (FulfillmentRequestRow.number > cursor.number))
                )
        order = (FulfillmentRequestRow.created_at, FulfillmentRequestRow.number)
        rows = self.session.scalars(statement.order_by(*order).limit(limit))
        return [self._domain(row) for row in rows]

    def for_project(self, project: str) -> list[FulfillmentRequest]:
        """Запросы одного проекта (проект НАЗНАЧЕНИЯ точно по ключу), в порядке `list()`, без общего лимита.
        Колонки ключа здесь нет (её заводит ветка «одна заявка на КБ», #254): база отбирает НАЗНАЧЕНИЯ с цифрами
        номера (`digit_stem`), точное сравнение — после; цифры ключа стоят в тексте подряд, так что ничего не теряется."""
        key = normalize_project_number(project)
        if not key:
            return []
        statement = select(FulfillmentRequestRow)
        stem = digit_stem(key)
        if stem:
            statement = statement.where(FulfillmentRequestRow.purpose.contains(stem, autoescape=True))
        rows = self.session.scalars(
            statement.order_by(FulfillmentRequestRow.created_at, FulfillmentRequestRow.number)
        )
        return [self._domain(row) for row in rows if project_from_purpose(row.purpose) == key]

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
        kit = kit_key(request.purpose)
        row.project_key, row.item_index = kit if kit is not None else (None, None)
        row.kind = request.kind.value
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
            line_row.off_book = line.off_book
            line_row.absent_at = line.absent_at
            line_row.absent_by = line.absent_by if line.absent_at is not None else None
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


# The shop site a kit goes to when no production task gives another one, and the site of a master with an empty
# site: ЦАР is the default site of the staff and the site the autoplan schedules (`production.autoplan.AUTOPLAN_SITE`).
DEFAULT_KIT_SITE = "ЦАР"
# How far back `last_handout` looks for a place used at the site.
_HANDOUT_LOOKBACK = 200


def _same_kb(task_item: str, kb: str) -> bool:
    """КБ of a production task «КБ1», «КБ-1» or «708К.4.КБ1» against the kit's «КБ1» (as `workflows.arbiter`)."""
    item = str(task_item or "").upper().replace(" ", "").replace("КБ-", "КБ")
    return bool(kb) and (item == kb or item.endswith(f".{kb}") or item.endswith(f"-{kb}"))


class SqlAlchemyKitSites:
    """Where a kit goes on the shop floor (owner, G1-T3 05.10.2026), read in the fulfillment transaction.

    The site is the one the kit's production task is handed to («Отдать в ЖУК», `production_tasks.handed_to`): the task
    launched on this kit, else a task of the same project and КБ; a task distributed by people, or no task yet, means
    ЦАР. The masters are active people with «Мастер площадки» on that site (an empty site counts as ЦАР)."""

    def __init__(self, session: Session):
        self.session = session

    def site_of(self, request: FulfillmentRequest) -> str:
        # Imported here: production's persistence imports this module (kit rows), so a top-level import would cycle.
        from app.modules.production.infrastructure import ProductionTaskRow

        live = ProductionTaskRow.status != ProductionStatus.CANCELLED.value
        linked = self.session.scalars(
            select(ProductionTaskRow.handed_to)
            .where(ProductionTaskRow.fulfillment_request_id == request.id, live)
            .order_by(ProductionTaskRow.created_at.desc())
        ).first()
        if linked is not None:
            return str(linked or "").strip() or DEFAULT_KIT_SITE
        project, kb = project_from_purpose(request.purpose), kb_of(request.purpose)
        if not project or not kb:
            return DEFAULT_KIT_SITE
        handed = self.session.execute(
            select(ProductionTaskRow.project, ProductionTaskRow.item, ProductionTaskRow.handed_to)
            .where(ProductionTaskRow.handed_to != "", live)
            .order_by(ProductionTaskRow.created_at.desc())
        )
        for task_project, item, site in handed:
            if normalize_project_number(task_project) == project and _same_kb(item, kb):
                return str(site).strip()
        return DEFAULT_KIT_SITE

    def masters(self, site: str) -> list[UUID]:
        wanted = str(site or "").strip() or DEFAULT_KIT_SITE
        rows = self.session.execute(
            select(IdentityUserRow.id, IdentityUserRow.site)
            .where(IdentityUserRow.active.is_(True), IdentityUserRow.site_master.is_(True))
            .order_by(IdentityUserRow.name)
        )
        return [user_id for user_id, user_site in rows if (str(user_site or "").strip() or DEFAULT_KIT_SITE) == wanted]

    def last_handout(self, site: str) -> str | None:
        """The place of the latest «Готов к выдаче» at the site (`fulfillment.ready` keeps both since G1-T3)."""
        details = self.session.scalars(
            select(AuditEventRow.details)
            .where(AuditEventRow.event_type == "fulfillment.ready")
            .order_by(AuditEventRow.occurred_at.desc())
            .limit(_HANDOUT_LOOKBACK)
        )
        for item in details:
            if isinstance(item, dict) and item.get("site") == site and str(item.get("handout_location") or "").strip():
                return str(item["handout_location"]).strip()
        return None


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
        # «Со склада» of procurement lines: a kit of the same project takes those reserves over.
        self.stock_covers = SqlAlchemyStockCoverRepository(self.session)
        # Techcard reserves of a project КБ: its kit request takes them over after the project's covers.
        self.kit_reserves = SqlAlchemyKitReserveRepository(self.session)
        # The kit's shop site: its masters hear «готов к выдаче», its last handout place is offered next time.
        self.kit_sites = SqlAlchemyKitSites(self.session)
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
