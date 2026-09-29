"""Transactional warehouse lot accounting."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.clock import MOSCOW
from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.unit_mass import mass_per_unit, parse_number
from app.shared.warehouse_location import remap_storage_location, storage_location_has_slot

from .domain import (
    WAREHOUSES,
    StockLot,
    WarehouseValidationError,
    WriteOffEntry,
    lot_row_changes,
    normalize_location,
    normalize_location_as_is,
    normalize_location_code,
)
from .stock_export import LegacyStockRow, LegacyWriteOffRow, parse_quantity


SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
LEGACY_IMPORT_ACTOR = Actor(
    id=UUID("00000000-0000-0000-0000-000000000019"), name="SUPPLYMAP import", department="System"
)
_OLD_GRN = re.compile(r"^OLD\.(\d{2})\.(\d{7})$")


class WarehouseRepository(Protocol):
    def get(self, lot_id: UUID) -> StockLot | None: ...
    def by_key(self, warehouse: str, grn: str, package: str) -> StockLot | None: ...
    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLot]: ...
    def count(
        self,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> int: ...
    def at_location(self, warehouse: str, storage_location: str) -> Sequence[StockLot]: ...
    def reserve_manual_numbers(self, year: int, count: int) -> int: ...
    def ensure_manual_number(self, year: int, number: int) -> int: ...
    def add(self, lot: StockLot) -> None: ...


class OutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    warehouse: WarehouseRepository
    writeoffs: AppendOnlyRepository  # the СПИСАНИЕ journal (SK-13)
    audit: AppendOnlyRepository
    outbox: OutboxRepository

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


class PoisonWarehouseMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


class ConcurrentWarehouseUpdate(RuntimeError):
    pass


@dataclass(frozen=True)
class WriteOffCommand:
    lot_id: UUID
    expected_version: int
    quantity: Decimal
    purpose: str  # НАЗНАЧЕНИЕ: project number or the responsible person's surname
    reason: str  # ОСНОВАНИЕ: the class of the expense; empty means «Выдача»
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ReturnWriteOffCommand:
    lot_id: UUID
    expected_version: int
    quantity: Decimal
    source: str  # «откуда»: project number or the responsible person's surname
    reason: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class MoveLotCommand:
    lot_id: UUID
    expected_version: int
    storage_location: str
    actor: Actor
    now: datetime
    warehouse: str = ""  # target warehouse; empty or the lot's own → only the place changes


@dataclass(frozen=True)
class CorrectLotLocationCommand:
    lot_id: UUID
    expected_version: int
    storage_location: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class EditLotIdentityCommand:
    lot_id: UUID
    expected_version: int
    article: str
    name: str
    series: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class EditLotRowCommand:
    """Manual edit of a stock row (SK-7). `changes` holds only the fields to set, already typed for
    `StockLot.edit_row`; `kitting_reserve` is the reserve kitting lines hold on the lot."""

    lot_id: UUID
    expected_version: int
    changes: dict[str, object]
    actor: Actor
    now: datetime
    kitting_reserve: Decimal = Decimal("0")


@dataclass(frozen=True)
class MoveLocationCommand:
    warehouse: str
    source_location: str
    target_location: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ManualStockItem:
    warehouse: str  # empty → the first warehouse (ЦАР), as stock05NormItem_
    name: str
    item_type: str
    purpose: str  # empty → «Склад»
    unit: str  # empty → «шт»
    quantity: Decimal | str | float | int | None  # read per row as icrNum_; the row is refused if not > 0
    storage_location: str
    article: str = ""
    series: str = ""
    # Lot passport, optional (SK-6). The mass comes as the weight of 1, 10 or 100 pieces, as typed
    # (Stock05.gs:98-105); ERP also takes the other passport fields at entry — SUPPLYMAP has them only
    # in the row edit (SK-7).
    mass_1: object = None
    mass_10: object = None
    mass_100: object = None
    expiry_date: date | None = None
    manufacturer: str = ""
    description: str = ""
    storage_conditions: str = ""
    datasheet: str = ""
    certificate: str = ""


@dataclass(frozen=True)
class AddManualStockCommand:
    items: tuple[ManualStockItem, ...]
    actor: Actor
    now: datetime


MANUAL_STOCK_LIMIT = 200  # STOCK05_ADD_LIMIT_ (Stock05.gs:15)
MANUAL_STOCK_DEFAULT_UNIT = "шт"  # STOCK05_DEFAULT_UNIT_
MANUAL_STOCK_DEFAULT_PURPOSE = "Склад"  # STOCK05_DEFAULT_PURPOSE_
_MANUAL_GRN_PENDING = "OLD.pending"  # a row is checked before its number is reserved
_QUANTITY_STEP = Decimal("0.000001")  # Numeric(18, 6) of the stock columns
_QUANTITY_LIMIT = Decimal("1e12")


@dataclass(frozen=True)
class ManualStockAssigned:
    i: int  # 0-based index of the row in the request, as stock05Add_ reports it
    lot: StockLot


@dataclass(frozen=True)
class ManualStockRowError:
    i: int  # 0-based; the screen shows i + 1
    error: str


@dataclass(frozen=True)
class ManualStockResult:
    """stock05Add_: bad rows are skipped and reported, the good ones are entered."""

    assigned: tuple[ManualStockAssigned, ...]
    errors: tuple[ManualStockRowError, ...]

    @property
    def lots(self) -> list[StockLot]:
        return [row.lot for row in self.assigned]


def _lot_from_payload(payload: dict) -> StockLot:
    try:
        return StockLot.create(
            warehouse=str(payload["warehouse"]),
            grn=str(payload["grn"]),
            package=str(payload.get("package") or ""),
            name=str(payload["name"]),
            item_type=str(payload["item_type"]),
            purpose=str(payload["purpose"]),
            unit=str(payload["unit"]),
            quantity=Decimal(str(payload["quantity"])),
            storage_location=str(payload["storage_location"]),
            source_receiving_ids=(str(payload["receiving_line_id"]),),
            article=str(payload.get("article") or ""),
            series=str(payload.get("series") or ""),
            expiry_date=date.fromisoformat(payload["expiry_date"]) if payload.get("expiry_date") else None,
            unit_mass_g=Decimal(str(payload["unit_mass_g"])) if payload.get("unit_mass_g") else None,
        )
    except (KeyError, ValueError, TypeError, DecimalException) as error:
        if isinstance(error, WarehouseValidationError):
            raise
        raise WarehouseValidationError(f"Invalid warehouse.received payload: {error}") from error


def _audit_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def dispatch_next_warehouse_received(now: datetime, uow: UnitOfWork) -> StockLot | None:
    with uow:
        message = uow.outbox.next_unpublished("warehouse.received", now)
        if message is None:
            return None
        try:
            incoming = _lot_from_payload(message.payload)
            existing = uow.warehouse.by_key(incoming.warehouse, incoming.grn, incoming.package)
            lot = incoming if existing is None else existing.receive(
                incoming.inbound,
                incoming.source_receiving_ids[0],
                article=incoming.article,
                series=incoming.series,
                purpose=incoming.purpose,
                storage_location=incoming.storage_location,
                expiry_date=incoming.expiry_date,
                unit_mass_g=incoming.unit_mass_g,
            )
        except WarehouseValidationError as error:
            uow.outbox.mark_failed(message.id, now, str(error))
            uow.commit()
            raise PoisonWarehouseMessage(message.id, str(error)) from error
        uow.warehouse.add(lot)
        if existing is None or lot != existing:
            details = {"grn": lot.grn, "package": lot.package, "quantity": str(incoming.inbound)}
            if existing is not None:
                for field_name in ("storage_location", "purpose", "expiry_date", "unit_mass_g"):
                    old, new = getattr(existing, field_name), getattr(lot, field_name)
                    if old != new:
                        details[f"{field_name}_from"], details[f"{field_name}_to"] = _audit_value(old), _audit_value(new)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=lot.id,
                    event_type="warehouse.received",
                    actor=SYSTEM_ACTOR,
                    occurred_at=now,
                    details=details,
                )
            )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return lot


def _manual_unit_mass(item: ManualStockItem) -> Decimal | None:
    try:
        return mass_per_unit(item.mass_1, item.mass_10, item.mass_100)
    except ValueError as error:
        raise WarehouseValidationError(str(error)) from error


def edited_unit_mass(text: str) -> Decimal | None:
    """МАССА 1 ШТ from the row edit: empty → no mass; otherwise a number read as icrNum_ and rounded as the
    quick entry rounds it. SUPPLYMAP writes the cell text as typed; ERP keeps a number, so a text that is
    not a number is refused instead of stored, and zero means «no mass» as everywhere in the lot."""
    raw = str(text or "").strip()
    if not raw:
        return None
    value = parse_number(raw)
    if math.isnan(value) or math.isinf(value):
        raise WarehouseValidationError(f"Масса 1 шт — не число: «{raw}»")
    if value < 0:
        raise WarehouseValidationError("Масса 1 шт не может быть отрицательной")
    try:
        return mass_per_unit(raw)
    except ValueError as error:
        raise WarehouseValidationError(str(error)) from error


def _manual_quantity(value: object) -> Decimal:
    if isinstance(value, Decimal):
        quantity = value
    else:
        number = parse_number(value)
        quantity = Decimal("NaN") if math.isnan(number) or math.isinf(number) else Decimal(repr(number))
    if quantity.is_nan() or quantity <= 0:
        raise WarehouseValidationError("кол-во должно быть больше нуля")
    if quantity >= _QUANTITY_LIMIT:
        raise WarehouseValidationError("кол-во слишком большое")
    if quantity != quantity.quantize(_QUANTITY_STEP):
        raise WarehouseValidationError("кол-во — не больше 6 знаков после запятой")
    return quantity


def _manual_lot(item: ManualStockItem) -> StockLot:
    """One quick-entry row → a lot without its number yet (stock05NormItem_ + placeFits_, Stock05.gs:74-109,
    744-757). SUPPLYMAP texts where it has them; the rest are ERP's own lot checks."""
    article, name = item.article.strip(), item.name.strip()
    if not article and not name:
        raise WarehouseValidationError("пустой артикул")
    quantity = _manual_quantity(item.quantity)
    warehouse = item.warehouse.strip() or WAREHOUSES[0]
    if warehouse not in WAREHOUSES:
        raise WarehouseValidationError(f"неизвестный склад «{warehouse}»")
    if not str(item.storage_location or "").strip():
        raise WarehouseValidationError("укажи стеллаж, полку и № на полке")
    return StockLot.create(
        warehouse=warehouse,
        grn=_MANUAL_GRN_PENDING,
        package="",
        name=name,
        item_type=item.item_type,
        purpose=item.purpose.strip() or MANUAL_STOCK_DEFAULT_PURPOSE,
        unit=item.unit.strip() or MANUAL_STOCK_DEFAULT_UNIT,
        quantity=quantity,
        storage_location=item.storage_location,
        source_receiving_ids=(f"manual:{uuid4()}",),
        article=article,  # empty → the name (StockLot.create, Warehouse.gs:522)
        series=item.series,
        unit_mass_g=_manual_unit_mass(item),
        expiry_date=item.expiry_date,
        manufacturer=item.manufacturer,
        description=item.description,
        storage_conditions=item.storage_conditions,
        datasheet=item.datasheet,
        certificate=item.certificate,
    )


def add_manual_stock(command: AddManualStockCommand, uow: UnitOfWork) -> ManualStockResult:
    """Quick entry (stock05Add_, Stock05.gs:734-794): every row is checked on its own; a bad row is skipped and
    reported as {i, error}, the good ones get consecutive OLD numbers and are committed together."""
    if not command.items:
        raise WarehouseValidationError("Добавьте хотя бы одну позицию")
    if len(command.items) > MANUAL_STOCK_LIMIT:
        raise WarehouseValidationError(f"За раз не больше {MANUAL_STOCK_LIMIT} позиций.")
    good: list[tuple[int, StockLot]] = []
    errors: list[ManualStockRowError] = []
    for index, item in enumerate(command.items):
        try:
            good.append((index, _manual_lot(item)))
        except WarehouseValidationError as error:
            errors.append(ManualStockRowError(i=index, error=str(error)))
    if not good:
        return ManualStockResult(assigned=(), errors=tuple(errors))
    with uow:
        first = uow.warehouse.reserve_manual_numbers(command.now.year, len(good))
        assigned = []
        for offset, (index, draft) in enumerate(good):
            grn = f"OLD.{command.now.year % 100:02d}.{first + offset:07d}"
            lot = replace(draft, grn=grn)
            uow.warehouse.add(lot)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=lot.id,
                    event_type="warehouse.manually_received",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "grn": grn,
                        "quantity": _audit_value(lot.inbound),
                        "storage_location": lot.storage_location,
                    },
                )
            )
            assigned.append(ManualStockAssigned(i=index, lot=lot))
        uow.commit()
        return ManualStockResult(assigned=tuple(assigned), errors=tuple(errors))


LotKey = tuple[str, str, str]


@dataclass(frozen=True)
class ImportLegacyStockCommand:
    """WH-IMPORT: bring SUPPLYMAP ЦАР/ЖУК rows in as lots. `reconcile_keys` are the sheet keys the importer
    could not take; their ERP state is read back too, so the report compares every sheet row."""

    lots: tuple[LegacyStockRow, ...]
    writeoffs: tuple[LegacyWriteOffRow, ...]
    source: str
    now: datetime
    actor: Actor = LEGACY_IMPORT_ACTOR
    dry_run: bool = False
    reconcile_keys: tuple[LotKey, ...] = ()


@dataclass
class LegacyStockImportResult:
    created: list[LegacyStockRow] = field(default_factory=list)
    unchanged: list[LegacyStockRow] = field(default_factory=list)
    conflicts: list[tuple[LegacyStockRow, str]] = field(default_factory=list)
    rejected: list[tuple[LegacyStockRow, str]] = field(default_factory=list)
    reserve_not_transferred: list[LegacyStockRow] = field(default_factory=list)
    history_imported: int = 0
    history_for_existing_lots: int = 0
    history_without_lot: list[LegacyWriteOffRow] = field(default_factory=list)
    erp_lots: dict[LotKey, StockLot | None] = field(default_factory=dict)
    manual_numbers: dict[int, int] = field(default_factory=dict)
    committed: bool = False


def legacy_import_source_id(key: LotKey) -> str:
    return "supplymap-import:" + ":".join(key)


def _legacy_differences(lot: StockLot, row: LegacyStockRow) -> list[str]:
    if legacy_import_source_id(row.key) not in lot.source_receiving_ids:
        return ["партия уже есть в ERP и заведена не импортом"]
    expected = {
        "ПРИХОД": (lot.inbound, row.inbound),
        "СПИСАНО": (lot.written_off, row.written_off),
        "МЕСТО ХРАНЕНИЯ": (lot.storage_location, normalize_location_as_is(row.storage_location)),
        "АРТИКУЛ": (lot.article, row.article or row.name),
        "НАИМЕНОВАНИЕ": (lot.name, row.name),
        "СЕРИЯ": (lot.series, row.series),
        "ТИП": (lot.item_type, row.item_type),
        "НАЗНАЧЕНИЕ": (lot.purpose, row.purpose),
        "ЕД.ИЗМ": (lot.unit, row.unit),
    }
    return [label for label, (erp, sheet) in expected.items() if erp != sheet]


def _legacy_lot(row: LegacyStockRow) -> StockLot:
    lot = StockLot.create(
        warehouse=row.warehouse, grn=row.grn, package=row.package, name=row.name,
        item_type=row.item_type, purpose=row.purpose, unit=row.unit, quantity=row.inbound,
        storage_location=row.storage_location, source_receiving_ids=(legacy_import_source_id(row.key),),
        article=row.article, series=row.series, location_as_is=True,
    )
    # СПИСАНО goes through the domain write-off, so the lot obeys the same balance rules as any other.
    return lot.write_off(row.written_off) if row.written_off > 0 else lot


def legacy_writeoff_date(text: str, fallback: datetime) -> datetime:
    """ДАТА of a СПИСАНИЕ row as the sheet shows it («dd.MM.yyyy», maybe with a time; SUPPLYMAP writes Moscow
    time) → a Moscow datetime. Unreadable → `fallback` (the import time); the sheet text stays in the audit."""
    value = str(text or "").strip()
    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=MOSCOW)
        except ValueError:
            continue
    return fallback


def _legacy_remaining(text: str) -> Decimal | None:
    try:
        return parse_quantity(text) if str(text or "").strip() else None
    except ValueError:
        return None


def _journal_legacy_writeoff(
    writeoff: LegacyWriteOffRow, lot: StockLot, command: ImportLegacyStockCommand, uow: UnitOfWork
) -> None:
    """A СПИСАНИЕ row of the export becomes a journal row as it was: history, it moves no quantity."""
    if writeoff.quantity <= 0:
        return
    entry = WriteOffEntry.for_lot(
        lot, kind="legacy", quantity=writeoff.quantity, reason=writeoff.reason, purpose=writeoff.purpose,
        author=writeoff.author, actor_id=command.actor.id, actor_name=command.actor.name,
        occurred_at=legacy_writeoff_date(writeoff.date, command.now),
        remaining_after=_legacy_remaining(writeoff.remaining_after), posted=writeoff.is_posted,
        source_key=f"supplymap-writeoff:{command.source}:{writeoff.row_number}",
    )
    uow.writeoffs.add(replace(entry, unit=writeoff.unit or lot.unit))


def import_legacy_stock(command: ImportLegacyStockCommand, uow: UnitOfWork) -> LegacyStockImportResult:
    """Create-only and idempotent: a key already in ERP is compared and reported, never changed.

    Quantities come from the stock sheet (ПРИХОД, СПИСАНО); РЕЗЕРВ is not transferred. The СПИСАНИЕ journal
    is history only: every row becomes an audit event on a lot created in this run and moves no quantity.
    One transaction for the whole import; dry_run runs the same path and rolls it back.
    """
    result = LegacyStockImportResult()
    with uow:
        created_lots: dict[LotKey, StockLot] = {}
        existing_keys: set[LotKey] = set()
        for row in command.lots:
            existing = uow.warehouse.by_key(*row.key)
            if existing is not None:
                existing_keys.add(row.key)
                differences = _legacy_differences(existing, row)
                if differences:
                    result.conflicts.append((row, "расходится с ERP: " + ", ".join(differences)))
                else:
                    result.unchanged.append(row)
                continue
            try:
                lot = _legacy_lot(row)
            except WarehouseValidationError as error:
                result.rejected.append((row, str(error)))
                continue
            uow.warehouse.add(lot)
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=lot.id, event_type="warehouse.legacy_imported",
                actor=command.actor, occurred_at=command.now,
                details={
                    "source": command.source, "sheet": row.sheet, "row": str(row.row_number),
                    "grn": lot.grn, "package": lot.package, "inbound": str(lot.inbound),
                    "written_off": str(lot.written_off), "storage_location": lot.storage_location,
                },
            ))
            created_lots[row.key] = lot
            result.created.append(row)
            if row.reserved > 0:
                result.reserve_not_transferred.append(row)

        for writeoff in command.writeoffs:
            lot = created_lots.get(writeoff.key)
            if lot is None:
                if writeoff.key in existing_keys:
                    result.history_for_existing_lots += 1
                else:
                    result.history_without_lot.append(writeoff)
                continue
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=lot.id, event_type="warehouse.legacy_writeoff_history",
                actor=command.actor, occurred_at=command.now,
                details={
                    "source": command.source, "row": str(writeoff.row_number), "date": writeoff.date,
                    "quantity": str(writeoff.quantity), "unit": writeoff.unit, "reason": writeoff.reason,
                    "purpose": writeoff.purpose, "author": writeoff.author, "posted": writeoff.posted,
                    "remaining_after": writeoff.remaining_after,
                },
            ))
            _journal_legacy_writeoff(writeoff, lot, command, uow)
            result.history_imported += 1

        # ERP hands out manual OLD.YY.NNNNNNN numbers from its own sequence; it must start after the
        # imported ones, or the next manual entry would reuse a live SUPPLYMAP GRN.
        highest: dict[int, int] = defaultdict(int)
        for row in command.lots:
            match = _OLD_GRN.fullmatch(row.grn)
            if match:
                year = 2000 + int(match.group(1))
                highest[year] = max(highest[year], int(match.group(2)))
        for year, number in sorted(highest.items()):
            result.manual_numbers[year] = uow.warehouse.ensure_manual_number(year, number)

        for key in (*(row.key for row in command.lots), *command.reconcile_keys):
            result.erp_lots[key] = uow.warehouse.by_key(*key)
        if not command.dry_run:
            uow.commit()
            result.committed = True
    return result


def _versioned(lot_id: UUID, expected_version: int, uow: UnitOfWork) -> StockLot:
    lot = uow.warehouse.get(lot_id)
    if lot is None:
        raise KeyError(f"Warehouse lot not found: {lot_id}")
    if lot.version != expected_version:
        raise ConcurrentWarehouseUpdate("Партия изменилась после загрузки формы")
    return lot


WRITEOFF_DEFAULT_REASON = "Выдача"


def write_off(command: WriteOffCommand, uow: UnitOfWork) -> StockLot:
    """SUPPLYMAP stock05Writeoff_: НАЗНАЧЕНИЕ is required on the server, an empty ОСНОВАНИЕ is «Выдача»."""
    purpose = str(command.purpose or "").strip()
    if not purpose:
        raise WarehouseValidationError("Укажи назначение: номер проекта или фамилию ответственного.")
    reason = str(command.reason or "").strip() or WRITEOFF_DEFAULT_REASON
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.write_off(command.quantity)
        uow.warehouse.add(updated)
        uow.writeoffs.add(WriteOffEntry.for_lot(
            updated, kind="writeoff", quantity=command.quantity, reason=reason, purpose=purpose,
            author=command.actor.name, actor_id=command.actor.id, actor_name=command.actor.name,
            occurred_at=command.now, remaining_after=updated.balance,
        ))
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.written_off",
                actor=command.actor, occurred_at=command.now,
                details={"quantity": str(command.quantity), "reason": reason, "purpose": purpose},
            )
        )
        uow.commit()
        return updated


_RETURN_REASON = re.compile(r"^\s*возврат", re.IGNORECASE)


def return_reason(reason: str) -> str:
    """SUPPLYMAP writeoffIsReturn_ / stock05Return_: a return is told apart by a basis starting with «Возврат»,
    so a basis without it is prefixed rather than stored as a plain write-off text."""
    text = str(reason or "").strip()
    if _RETURN_REASON.match(text):
        return text
    return f"Возврат: {text}" if text else "Возврат"


def return_writeoff(command: ReturnWriteOffCommand, uow: UnitOfWork) -> StockLot:
    """SUPPLYMAP stock05Return_ + writeoffAppend_: the unused part comes back to the lot it left from."""
    source = str(command.source or "").strip()
    if not source:
        raise WarehouseValidationError("Укажи, откуда возврат: номер проекта или фамилия ответственного.")
    reason = return_reason(command.reason)
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        if command.quantity > 0 and command.quantity > current.written_off:
            label = current.grn + (f" (пакет {current.package})" if current.package else "")
            unit = f" {current.unit}" if current.unit else ""
            raise WarehouseValidationError(
                f"Больше списанного: по {label} списано {format(current.written_off.normalize(), 'f')}{unit}"
                " — вернуть можно только их. Если это НЕ возврат, а новый приход, заведи позицию через"
                " «Внести позиции»."
            )
        updated = current.return_writeoff(command.quantity)
        uow.warehouse.add(updated)
        uow.writeoffs.add(WriteOffEntry.for_lot(
            updated, kind="return", quantity=command.quantity, reason=reason, purpose=source,
            author=command.actor.name, actor_id=command.actor.id, actor_name=command.actor.name,
            occurred_at=command.now, remaining_after=updated.balance,
        ))
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.writeoff_returned",
                actor=command.actor, occurred_at=command.now,
                details={"quantity": str(command.quantity), "reason": reason, "purpose": source},
            )
        )
        uow.commit()
        return updated


def move_lot(command: MoveLotCommand, uow: UnitOfWork) -> StockLot:
    """SUPPLYMAP stock05Move_: a new place and/or the whole lot to the other warehouse."""
    target = str(command.warehouse or "").strip()
    place = str(command.storage_location or "").strip()
    if not place and not target:
        raise WarehouseValidationError("Укажи новое место или склад.")
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        if not target or target == current.warehouse:
            if not place:
                return current
            updated = current.move(place)
            if updated == current:
                return current
            event_type = "warehouse.moved"
            details = {"from": current.storage_location, "to": updated.storage_location}
        else:
            updated = current.transfer(target, place)
            occupied = uow.warehouse.by_key(updated.warehouse, updated.grn, updated.package)
            if occupied is not None and occupied.id != updated.id:
                label = updated.grn + (f"/{updated.package}" if updated.package else "")
                raise WarehouseValidationError(
                    f"На складе {updated.warehouse} уже есть партия {label}: перемещение слило бы "
                    "две партии. Проверь оба склада."
                )
            event_type = "warehouse.transferred"
            details = {
                "from_warehouse": current.warehouse, "to_warehouse": updated.warehouse,
                "from": current.storage_location, "to": updated.storage_location,
                "grn": updated.grn, "package": updated.package,
                "inbound": str(updated.inbound), "written_off": str(updated.written_off),
                "balance": str(updated.balance),
            }
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type=event_type,
                actor=command.actor, occurred_at=command.now, details=details,
            )
        )
        uow.commit()
        return updated


def correct_lot_location(command: CorrectLotLocationCommand, uow: UnitOfWork) -> StockLot:
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.correct_location(command.storage_location)
        if updated == current:
            return current
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.location_corrected",
                actor=command.actor, occurred_at=command.now,
                details={"from": current.storage_location, "to": updated.storage_location},
            )
        )
        uow.commit()
        return updated


def edit_lot_identity(command: EditLotIdentityCommand, uow: UnitOfWork) -> StockLot:
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.edit_identity(
            article=command.article,
            name=command.name,
            series=command.series,
        )
        if updated == current:
            return current
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.identity_edited",
                actor=command.actor, occurred_at=command.now,
                details={
                    "article": updated.article,
                    "name": updated.name,
                    "series": updated.series,
                },
            )
        )
        uow.commit()
        return updated


LotRowChange = tuple[str, str, str, str]  # field, sheet column, было, стало


def edit_lot_row(command: EditLotRowCommand, uow: UnitOfWork) -> tuple[StockLot, list[LotRowChange]]:
    """SUPPLYMAP stock05EditRow_: set the given fields, recount БАЛАНС, journal «было → стало» per field.
    Nothing changed → the lot as it was and no audit event («Изменений нет.»)."""
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.edit_row(command.changes, kitting_reserve=command.kitting_reserve)
        changes = lot_row_changes(current, updated)
        if not changes:
            return current, []
        details = {
            "grn": updated.grn,
            "package": updated.package,
            "warehouse": updated.warehouse,
            "changes": "; ".join(f"{column}: «{was or '—'}» → «{now or '—'}»" for _, column, was, now in changes),
        }
        for field_name, _, was, now in changes:
            details[f"{field_name}_from"], details[f"{field_name}_to"] = was, now
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.row_edited",
                actor=command.actor, occurred_at=command.now, details=details,
            )
        )
        uow.commit()
        return updated, changes


def move_location(command: MoveLocationCommand, uow: UnitOfWork) -> list[StockLot]:
    warehouse = str(command.warehouse or "").strip()
    if warehouse not in {"ЦАР", "ЖУК"}:
        raise WarehouseValidationError("Неизвестный склад")
    source = normalize_location_code(command.source_location)
    target = normalize_location(command.target_location)
    if not storage_location_has_slot(source) and storage_location_has_slot(target):
        raise WarehouseValidationError("Место целиком переносится без ячейки")
    if source == target:
        raise WarehouseValidationError("Новое место совпадает со старым")
    with uow:
        current_lots = list(uow.warehouse.at_location(warehouse, source))
        if not current_lots:
            raise WarehouseValidationError(f"На месте {source} ({warehouse}) ничего не лежит")
        moved = []
        for current in current_lots:
            remapped = remap_storage_location(current.storage_location, source, target)
            if remapped is None:
                continue
            updated = current.move(remapped)
            uow.warehouse.add(updated)
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.location_moved",
                actor=command.actor, occurred_at=command.now,
                details={"from": current.storage_location, "to": updated.storage_location},
            ))
            moved.append(updated)
        if not moved:
            raise WarehouseValidationError(f"На месте {source} ({warehouse}) ничего не лежит")
        uow.commit()
        return moved


def warehouse_lots(
    uow: UnitOfWork,
    *,
    limit: int = 200,
    after_id: UUID | None = None,
    query: str = "",
    warehouse: str | None = None,
    include_zero: bool = False,
) -> tuple[list[StockLot], int]:
    with uow:
        if after_id is not None and uow.warehouse.get(after_id) is None:
            raise KeyError(f"Warehouse cursor not found: {after_id}")
        lots = uow.warehouse.list(limit, after_id, query, warehouse, include_zero)
        return list(lots), uow.warehouse.count(query, warehouse, include_zero)
