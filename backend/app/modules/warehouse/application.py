"""Transactional warehouse lot accounting."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.clock import MOSCOW
from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.unit_mass import mass_per_unit, parse_number
from app.shared.warehouse_location import (
    existing_shelf,
    next_free_number,
    places_on_shelf,
    remap_storage_location,
    storage_location_has_slot,
)

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
from .item_types import canonical_item_type
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
    def filtered(
        self,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLot]: ...
    def at_location(self, warehouse: str, storage_location: str) -> Sequence[StockLot]: ...
    def occupied_locations(self, warehouse: str) -> Sequence[str]: ...
    def reserve_manual_numbers(self, year: int, count: int) -> int: ...
    def ensure_manual_number(self, year: int, number: int) -> int: ...
    def every(self) -> Sequence[StockLot]: ...
    def audit_trail(self, lot_id: UUID) -> list[tuple[str, dict]]: ...
    def add(self, lot: StockLot) -> None: ...


class OutboxRepository(Protocol):
    def add(self, message: OutboxMessage) -> None: ...
    def by_idempotency_key(self, idempotency_key: str) -> OutboxMessage | None: ...
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
    entry_date: date | None = None  # ДАТА строки СПИСАНИЯ (Writeoff.html), None — сегодня


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
    # «Со склада»: the reserve procurement lines hold on the lot.
    procurement_reserve: Decimal = Decimal("0")


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
            source_receiving_ids=(str(payload.get("receipt_id") or payload["receiving_line_id"]),),
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


LOT_ARRIVED_TOPIC = "fulfillment.lot_arrived"


def _emit_lot_arrived(uow: UnitOfWork, lot: StockLot, incoming: StockLot, payload: dict, now: datetime) -> None:
    """Stock arrived on the lot (a new lot or a top-up): the kit requests waiting for it may take it
    (`workflows/arrival_kits.py`). Once per receipt — the receipt id the lot counts the arrival by."""
    receipt_id = incoming.source_receiving_ids[0]
    key = f"warehouse:{receipt_id}:lot_arrived"
    if uow.outbox.by_idempotency_key(key) is not None:
        return
    uow.outbox.add(OutboxMessage(
        id=uuid4(),
        topic=LOT_ARRIVED_TOPIC,
        idempotency_key=key,
        created_at=now,
        payload={
            "lot_id": str(lot.id),
            "receipt_id": receipt_id,
            "receiving_line_id": str(payload.get("receiving_line_id") or ""),
            "warehouse": lot.warehouse,
            "grn": lot.grn,
            "package": lot.package,
            "quantity": format(incoming.inbound.normalize(), "f"),
        },
    ))


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
            _emit_lot_arrived(uow, lot, incoming, message.payload, now)
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return lot


def dispatch_next_supplier_return(now: datetime, uow: UnitOfWork) -> StockLot | None:
    """RC-6: Receiving asks to write off the returned quantity of an accepted lot (SupplierReturn.gs
    supplierReturnJournalAppend_ → processWriteoffs_). The row goes to the СПИСАНИЕ journal as
    «Возврат поставщику», НАЗНАЧЕНИЕ is the supplier. A lot gone or short since the request is a
    poison message: the receiving side already changed status, the operator sees the refusal."""
    with uow:
        message = uow.outbox.next_unpublished("warehouse.supplier_return_requested", now)
        if message is None:
            return None
        payload = message.payload
        try:
            try:
                quantity = Decimal(str(payload["quantity"]))
                warehouse = str(payload["warehouse"]).strip()
                grn = str(payload["grn"]).strip()
                package = str(payload.get("package") or "").strip()
                reason = str(payload["reason"]).strip()
                actor = Actor(
                    id=UUID(str(payload["actor_id"])),
                    name=str(payload.get("actor_name") or "").strip(),
                    department="",
                )
            except (KeyError, ValueError, TypeError, DecimalException) as error:
                raise WarehouseValidationError(
                    f"Invalid warehouse.supplier_return_requested payload: {error}"
                ) from error
            current = uow.warehouse.by_key(warehouse, grn, package)
            if current is None:
                label = grn + (f" (пакет {package})" if package else "")
                raise WarehouseValidationError(f"Партии {label} нет на складе {warehouse}")
            updated = current.write_off(quantity)
        except WarehouseValidationError as error:
            uow.outbox.mark_failed(message.id, now, str(error))
            uow.commit()
            raise PoisonWarehouseMessage(message.id, str(error)) from error
        supplier = str(payload.get("supplier") or "").strip()
        uow.warehouse.add(updated)
        uow.writeoffs.add(WriteOffEntry.for_lot(
            updated, kind="supplier_return", quantity=quantity, reason=reason,
            purpose=supplier or "Поставщик", author=actor.name, actor_id=actor.id,
            actor_name=actor.name, occurred_at=now, remaining_after=updated.balance,
            source_key=message.idempotency_key,
        ))
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.returned_to_supplier",
                actor=actor, occurred_at=now,
                details={
                    "quantity": str(quantity),
                    "reason": reason,
                    "receiving_line_id": str(payload.get("receiving_line_id") or ""),
                },
            )
        )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return updated


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
        item_type=canonical_item_type(item.item_type),  # stock05NormItem_: «ТуТ» → «Термоусадочная трубка»
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
    could not take; their ERP state is read back too, so the report compares every sheet row.

    `sync` is the top-up («догрузка»): a lot the import made and ERP never moved is brought to the sheet
    values (a `warehouse.legacy_synced` event; a lot Google moved to the other warehouse follows it), and new
    СПИСАНИЕ rows join its history. A lot ERP has moved stays as it is: when Google left its ПРИХОД/СПИСАНО as
    the last run brought them, ERP is simply ahead (`erp_ahead`); when Google changed them too, it is a conflict
    to settle by hand, and its new СПИСАНИЕ rows are listed (`history_not_synced`), not journalled.
    A sheet row finds the lot made from it by the import mark, wherever ERP has moved it since."""

    lots: tuple[LegacyStockRow, ...]
    writeoffs: tuple[LegacyWriteOffRow, ...]
    source: str
    now: datetime
    actor: Actor = LEGACY_IMPORT_ACTOR
    dry_run: bool = False
    reconcile_keys: tuple[LotKey, ...] = ()
    sync: bool = False


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
    synced: list[tuple[LegacyStockRow, list[tuple[str, str, str, str]]]] = field(default_factory=list)
    history_synced: int = 0
    missing_in_sheet: list[StockLot] = field(default_factory=list)
    # ERP moved the lot after the import, Google left its ПРИХОД/СПИСАНО as they were: ERP kept, not a conflict.
    erp_ahead: list[tuple[LegacyStockRow, str]] = field(default_factory=list)
    # New СПИСАНИЕ rows of a lot ERP has moved or made itself: listed for a manual check, not journalled.
    history_not_synced: list[LegacyWriteOffRow] = field(default_factory=list)
    committed: bool = False


LEGACY_SOURCE_PREFIX = "supplymap-import:"


def legacy_import_source_id(key: LotKey) -> str:
    return LEGACY_SOURCE_PREFIX + ":".join(key)


def _is_imported(lot: StockLot) -> bool:
    return any(source.startswith(LEGACY_SOURCE_PREFIX) for source in lot.source_receiving_ids)


def _legacy_differences(lot: StockLot, row: LegacyStockRow) -> list[str]:
    if not _is_imported(lot):
        return ["партия уже есть в ERP и заведена не импортом"]
    expected = {
        "СКЛАД": (lot.warehouse, row.warehouse),
        "ПРИХОД": (lot.inbound, row.inbound),
        "СПИСАНО": (lot.written_off, row.written_off),
        "МЕСТО ХРАНЕНИЯ": (lot.storage_location, normalize_location_as_is(row.storage_location)),
        "АРТИКУЛ": (lot.article, row.article or row.name),
        "НАИМЕНОВАНИЕ": (lot.name, row.name),
        "СЕРИЯ": (lot.series, row.series),
        "ТИП": (lot.item_type, row.item_type),
        "НАЗНАЧЕНИЕ": (lot.purpose, row.purpose),
        "ЕД.ИЗМ": (lot.unit, row.unit),
        "МАССА 1 ШТ, Г": (lot.unit_mass_g, row.unit_mass_g),
        "СРОК ГОДНОСТИ": (lot.expiry_date, row.expiry_date),
        "ПРОИЗВОДИТЕЛЬ": (lot.manufacturer, row.manufacturer),
        "DATASHEET": (lot.datasheet, row.datasheet),
        "СЕРТИФИКАТ": (lot.certificate, row.certificate),
        "ОПИСАНИЕ": (lot.description, row.description),
        "УСЛОВИЯ ХРАНЕНИЯ": (lot.storage_conditions, row.storage_conditions),
    }
    return [label for label, (erp, sheet) in expected.items() if erp != sheet]


LEGACY_EVENTS = frozenset({
    "warehouse.legacy_imported", "warehouse.legacy_writeoff_history", "warehouse.legacy_synced",
})
LEGACY_SYNC_REASON = "догрузка из Google"


def _legacy_row_values(row: LegacyStockRow) -> dict[str, object]:
    """The sheet row as `StockLot.edit_row` changes — the fields `_legacy_differences` compares."""
    return {
        "inbound": row.inbound, "written_off": row.written_off, "storage_location": row.storage_location,
        "article": row.article or row.name, "name": row.name, "series": row.series, "item_type": row.item_type,
        "purpose": row.purpose, "unit": row.unit, "unit_mass_g": row.unit_mass_g, "expiry_date": row.expiry_date,
        "manufacturer": row.manufacturer, "datasheet": row.datasheet, "certificate": row.certificate,
        "description": row.description, "storage_conditions": row.storage_conditions,
    }


def _import_version(trail: list[tuple[str, dict]]) -> int | None:
    """The lot version the last import run left. Runs before the top-up did not record it: the import made the
    lot at version 1 and wrote СПИСАНО off once (one more version) when it was above zero."""
    version = None
    for event_type, details in trail:
        if event_type not in ("warehouse.legacy_imported", "warehouse.legacy_synced"):
            continue
        if details.get("version"):
            version = int(details["version"])
        elif event_type == "warehouse.legacy_imported":
            version = 1 + (1 if parse_quantity(str(details.get("written_off") or "0")) > 0 else 0)
    return version


def _trail_quantity(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (DecimalException, ValueError):
        return None


def _google_quantity_changes(trail: list[tuple[str, dict]], row: LegacyStockRow) -> list[str]:
    """How the sheet's ПРИХОД/СПИСАНО moved since the last import run brought them («СПИСАНО 2 → 4»). The runs
    record them in `legacy_imported` (inbound, written_off) and `legacy_synced` (<field>_to when it changed).
    No record → the sheet is taken as changed, so a lot ERP moved is never overwritten on a guess."""
    last: dict[str, Decimal | None] = {}
    for event_type, details in trail:
        if event_type == "warehouse.legacy_imported":
            last = {name: _trail_quantity(details.get(name, "")) for name in ("inbound", "written_off")}
        elif event_type == "warehouse.legacy_synced" and last:
            for name in ("inbound", "written_off"):
                if f"{name}_to" in details:
                    last[name] = _trail_quantity(details[f"{name}_to"])
    changes = []
    for name, column in (("inbound", "ПРИХОД"), ("written_off", "СПИСАНО")):
        was, now = last.get(name), getattr(row, name)
        if was != now:
            shown = "?" if was is None else format(was.normalize(), "f").replace(".", ",")
            changes.append(f"{column} {shown} → {format(now.normalize(), 'f').replace('.', ',')}")
    return changes


def _erp_movements(lot: StockLot, trail: list[tuple[str, dict]], uow: UnitOfWork) -> str:
    """What ERP itself did to an imported lot ("" — nothing: the lot is still what the import left)."""
    own_events = [event_type for event_type, _ in trail if event_type not in LEGACY_EVENTS]
    own_writeoffs = [entry for entry in uow.writeoffs.for_lot(lot.id) if entry.kind != "legacy"]
    found = []
    if own_events:
        found.append(f"событий {len(own_events)}")
    if own_writeoffs:
        found.append(f"списаний и выдач {len(own_writeoffs)}")
    if lot.reserved:
        found.append(f"резерв {format(lot.reserved.normalize(), 'f')}")
    if not found and lot.version != _import_version(trail):
        found.append("партия менялась")
    return ", ".join(found)


def _writeoff_signature(quantity: Decimal, reason: str, purpose: str, author: str) -> tuple[str, str, str, str]:
    return (format(Decimal(quantity).normalize(), "f"), str(reason or "").strip(), str(purpose or "").strip(),
            str(author or "").strip())


def _legacy_lot(row: LegacyStockRow) -> StockLot:
    lot = StockLot.create(
        warehouse=row.warehouse, grn=row.grn, package=row.package, name=row.name,
        item_type=row.item_type, purpose=row.purpose, unit=row.unit, quantity=row.inbound,
        storage_location=row.storage_location, source_receiving_ids=(legacy_import_source_id(row.key),),
        article=row.article, series=row.series, location_as_is=True,
        unit_mass_g=row.unit_mass_g, expiry_date=row.expiry_date, manufacturer=row.manufacturer,
        description=row.description, storage_conditions=row.storage_conditions, datasheet=row.datasheet,
        certificate=row.certificate,
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


def _find_imported_lot(
    row: LegacyStockRow, by_mark: dict[str, StockLot], sheet_keys: set[LotKey], uow: UnitOfWork,
) -> StockLot | None:
    """The ERP lot of a sheet row: the lot the import made from this row (by its mark — a whole-lot transfer in ERP
    changes СКЛАД, the key, so the key alone would make a second lot), else the lot at the key, else a lot the
    import made from the same ГРН+ПАКЕТ in the other warehouse that the sheet no longer has there (Google moved
    the row to this warehouse)."""
    lot = by_mark.get(legacy_import_source_id(row.key))
    if lot is None:
        lot = uow.warehouse.by_key(*row.key)
    if lot is None:
        for warehouse in WAREHOUSES:
            other = (warehouse, row.grn, row.package)
            candidate = by_mark.get(legacy_import_source_id(other))
            if warehouse != row.warehouse and other not in sheet_keys and candidate is not None \
                    and (candidate.warehouse, candidate.grn, candidate.package) == other:
                lot = candidate
                break
    return lot


def _sync_legacy_lot(
    existing: StockLot, row: LegacyStockRow, command: ImportLegacyStockCommand, uow: UnitOfWork,
) -> tuple[StockLot, list[LotRowChange]]:
    """A lot ERP never moved follows the sheet row, СКЛАД included (Google moved the whole row); the lot then also
    carries the import mark of its new key."""
    relocated = existing.warehouse != row.warehouse
    base = existing
    if relocated:
        occupied = uow.warehouse.by_key(*row.key)
        if occupied is not None and occupied.id != existing.id:
            raise WarehouseValidationError(f"на складе {row.warehouse} уже есть другая партия с этим ГРН и пакетом")
        mark = legacy_import_source_id(row.key)
        sources = existing.source_receiving_ids
        base = replace(existing, warehouse=row.warehouse,
                       source_receiving_ids=sources if mark in sources else (*sources, mark))
    updated = base.edit_row(_legacy_row_values(row))
    if updated.version == existing.version:
        updated = replace(updated, version=existing.version + 1)
    changes = ([("warehouse", "СКЛАД", existing.warehouse, row.warehouse)] if relocated else []) \
        + lot_row_changes(base, updated)
    details: dict[str, object] = {
        "source": command.source, "sheet": row.sheet, "row": str(row.row_number),
        "grn": updated.grn, "package": updated.package, "warehouse": updated.warehouse,
        "reason": LEGACY_SYNC_REASON, "version": str(updated.version),
        "changes": "; ".join(f"{column}: «{was or '—'}» → «{now or '—'}»" for _, column, was, now in changes),
    }
    for field_name, _, was, now in changes:
        details[f"{field_name}_from"], details[f"{field_name}_to"] = was, now
    uow.warehouse.add(updated)
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=updated.id, event_type="warehouse.legacy_synced",
        actor=command.actor, occurred_at=command.now, details=details,
    ))
    return updated, changes


def import_legacy_stock(command: ImportLegacyStockCommand, uow: UnitOfWork) -> LegacyStockImportResult:
    """Idempotent: a rerun over the same export changes nothing; over a newer one it adds only the difference.

    Quantities come from the stock sheet (ПРИХОД, СПИСАНО); РЕЗЕРВ is not transferred. The СПИСАНИЕ journal
    is history only: its rows become audit events and journal rows and move no quantity.
    One transaction for the whole import; dry_run runs the same path and rolls it back.
    """
    result = LegacyStockImportResult()
    with uow:
        by_mark: dict[str, StockLot] = {}
        for lot in uow.warehouse.every():
            for source in lot.source_receiving_ids:
                if source.startswith(LEGACY_SOURCE_PREFIX):
                    by_mark[source] = lot
        sheet_keys = {row.key for row in command.lots} | set(command.reconcile_keys)
        created_lots: dict[LotKey, StockLot] = {}
        found: dict[LotKey, StockLot] = {}  # sheet key → the ERP lot it already has (as it is after this run)
        claimed: set[UUID] = set()
        untouched: dict[UUID, StockLot] = {}  # sync: imported lots ERP never moved — their history is topped up
        for row in command.lots:
            existing = _find_imported_lot(row, by_mark, sheet_keys, uow)
            if existing is not None:
                if existing.id in claimed:
                    result.rejected.append((row, "эта партия ERP уже сопоставлена другой строке листа"))
                    continue
                claimed.add(existing.id)
                found[row.key] = existing
                differences = _legacy_differences(existing, row)
                imported = _is_imported(existing)
                trail = uow.warehouse.audit_trail(existing.id) if command.sync and imported else []
                movements = _erp_movements(existing, trail, uow) if command.sync and imported else ""
                if command.sync and imported and not movements:
                    untouched[existing.id] = existing
                if not differences:
                    result.unchanged.append(row)
                elif not command.sync or not imported:
                    result.conflicts.append((row, "расходится с ERP: " + ", ".join(differences)))
                elif movements:
                    google = _google_quantity_changes(trail, row)
                    if existing.warehouse != row.warehouse \
                            and legacy_import_source_id(row.key) not in existing.source_receiving_ids:
                        google.insert(0, f"СКЛАД {existing.warehouse} → {row.warehouse}")
                    if google:
                        result.conflicts.append((row, (
                            f"в ERP уже были движения ({movements}), в Google после загрузки: {', '.join(google)} — "
                            "партия не тронута, сверить вручную; расходится с Google: " + ", ".join(differences))))
                    else:
                        result.erp_ahead.append((row, movements))
                else:
                    try:
                        updated, changes = _sync_legacy_lot(existing, row, command, uow)
                    except WarehouseValidationError as error:
                        result.rejected.append((row, str(error)))
                        continue
                    found[row.key] = untouched[existing.id] = updated
                    result.synced.append((row, changes))
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
                    "version": str(lot.version),
                },
            ))
            created_lots[row.key] = lot
            result.created.append(row)
            if row.reserved > 0:
                result.reserve_not_transferred.append(row)

        # СПИСАНИЕ rows a lot does not hold yet. Row numbers and the export stamp move between exports, so a row is
        # matched by what it says (КОЛ-ВО, ОСНОВАНИЕ, НАЗНАЧЕНИЕ, КТО), counted per lot. A row's СКЛАД is where the
        # lot was then: a lot moved since is still found by its import mark.
        held: dict[UUID, Counter] = {}
        for writeoff in command.writeoffs:
            lot = created_lots.get(writeoff.key)
            if lot is not None:
                result.history_imported += 1
            else:
                lot = found.get(writeoff.key) or by_mark.get(legacy_import_source_id(writeoff.key))
                if lot is None:
                    result.history_without_lot.append(writeoff)
                    continue
                if writeoff.quantity <= 0:
                    result.history_for_existing_lots += 1
                    continue
                if lot.id not in held:
                    held[lot.id] = Counter(_writeoff_signature(e.quantity, e.reason, e.purpose, e.author)
                                           for e in uow.writeoffs.for_lot(lot.id) if e.kind == "legacy")
                signature = _writeoff_signature(writeoff.quantity, writeoff.reason, writeoff.purpose, writeoff.author)
                if held[lot.id][signature] > 0:
                    held[lot.id][signature] -= 1
                    result.history_for_existing_lots += 1
                    continue
                if lot.id not in untouched:
                    result.history_not_synced.append(writeoff)
                    continue
                lot = untouched[lot.id]
                result.history_synced += 1
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

        # The lot each sheet row has in ERP as it is now (a lot ERP moved to the other warehouse included).
        for key in (*(row.key for row in command.lots), *command.reconcile_keys):
            known = found.get(key) or by_mark.get(legacy_import_source_id(key))
            result.erp_lots[key] = uow.warehouse.get(known.id) if known is not None else uow.warehouse.by_key(*key)
        if command.sync:
            # Imported lots no sheet row has: reported only, ERP keeps them as they are.
            listed = {lot.id for lot in result.erp_lots.values() if lot is not None}
            result.missing_in_sheet = [lot for lot in uow.warehouse.every() if lot.id not in listed and _is_imported(lot)]
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
            occurred_at=(
                datetime.combine(command.entry_date, command.now.time())
                if command.entry_date and command.entry_date != command.now.date()
                else command.now
            ),
            remaining_after=updated.balance,
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
        updated = current.edit_row(
            command.changes,
            kitting_reserve=command.kitting_reserve,
            procurement_reserve=command.procurement_reserve,
        )
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
    sort: str | None = None,
    desc: bool = False,
    group: str = "",
) -> tuple[list[StockLot], int]:
    with uow:
        if after_id is not None and uow.warehouse.get(after_id) is None:
            raise KeyError(f"Warehouse cursor not found: {after_id}")
        if sort:
            # The order is the whole filter's, then the page is cut: the cursor is the last lot of the previous page.
            lots = sort_stock_lots(uow.warehouse.filtered(query, warehouse, include_zero), sort, desc, group)
            start = 0
            if after_id is not None:
                start = next((index + 1 for index, lot in enumerate(lots) if lot.id == after_id), len(lots))
            return lots[start:start + limit], len(lots)
        lots = uow.warehouse.list(limit, after_id, query, warehouse, include_zero)
        return list(lots), uow.warehouse.count(query, warehouse, include_zero)


STOCK_SORT_KEYS = ("type", "grn", "balance", "reserved", "available", "lots", "place", "series", "article", "name", "warehouse")


def _natural(value: str) -> list:
    """Natural order like the screen's `Intl.Collator("ru", {numeric: true, sensitivity: "base"})`: digit runs by value."""
    return [int(part) if index % 2 else part.casefold() for index, part in enumerate(re.split(r"(\d+)", value))]


def sort_stock_lots(lots: list[StockLot], sort: str, desc: bool, group: str = "") -> list[StockLot]:
    """All lots in the order of the grouped stock table (frontend `warehouseGroups` + `compareArticles`): groups of the
    chosen grouping by name (the empty one last), inside a group the article rows by the sorted column's value
    (numbers by value, text naturally, the article key breaks ties), a row's lots together in the id order."""
    if sort not in STOCK_SORT_KEYS:
        raise ValueError(f"Unknown stock sort: {sort}")
    if group not in STOCK_GROUPS:
        raise ValueError(f"Unknown stock group: {group}")
    rows: dict[tuple[str, str], list[StockLot]] = {}
    for lot in lots:
        rows.setdefault((stock_group_value(lot, group), stock_article_key(lot)), []).append(lot)

    def joined(items: list[StockLot], attribute: str) -> str:
        return " · ".join(dict.fromkeys(str(getattr(item, attribute) or "") for item in items if getattr(item, attribute)))

    def value(items: list[StockLot]):
        if sort == "balance":
            return sum((item.balance for item in items), Decimal(0))
        if sort == "reserved":
            return sum((item.reserved for item in items), Decimal(0))
        if sort == "available":
            return sum((item.available for item in items), Decimal(0))
        if sort == "lots":
            return len(items)
        attribute = {"type": "item_type", "place": "storage_location"}.get(sort, sort)
        if sort == "article":
            return _natural(items[0].article)
        return _natural(joined(items, attribute))

    keys = list(rows)
    keys.sort(key=lambda key: _natural(key[1]))
    keys.sort(key=lambda key: value(rows[key]), reverse=desc)
    keys.sort(key=lambda key: (not key[0], _natural(key[0])))
    return [lot for key in keys for lot in rows[key]]


STOCK_GROUPS = ("", "type", "place", "name", "series", "grn")


@dataclass
class StockTotals:
    """Sums of the lots of one group or one article of a group: the numbers do not depend on how many lots a screen loaded."""

    balance: Decimal = Decimal(0)
    reserved: Decimal = Decimal(0)
    available: Decimal = Decimal(0)
    lots: int = 0
    units: set[str] = field(default_factory=set)
    articles: set[str] = field(default_factory=set)

    def add(self, lot: StockLot, article: str) -> None:
        self.balance += lot.balance
        self.reserved += lot.reserved
        self.available += lot.available
        self.lots += 1
        if lot.unit:
            self.units.add(lot.unit)
        self.articles.add(article)


def stock_article_key(lot: StockLot) -> str:
    """The article identity of the stock table (frontend `articleKey`): article or name, spaces folded, lower case."""
    identity = " ".join((lot.article or lot.name).split()).lower()
    return identity or f"\u0001грн {lot.grn}/{lot.package}"


def stock_group_value(lot: StockLot, group: str) -> str:
    """The group a lot sits in for the table «группировать» (frontend `warehouseGroups`); "" for no grouping."""
    if group == "type":
        value = lot.item_type
    elif group == "place":
        place = "".join(lot.storage_location.split()).upper()
        found = re.match(r"^(\d+[A-ZА-Я])", place)
        value = found.group(1) if found else lot.storage_location
    elif group == "name":
        value = lot.name
    elif group == "series":
        value = lot.series
    elif group == "grn":
        value = lot.grn
    else:
        value = ""
    return value.strip()


def warehouse_totals(
    uow: UnitOfWork,
    *,
    group: str = "",
    query: str = "",
    warehouse: str | None = None,
    include_zero: bool = False,
) -> tuple[dict[str, StockTotals], dict[tuple[str, str], StockTotals]]:
    """Totals per group and per (group, article) over all lots of the filter: what the grouped stock table shows."""
    if group not in STOCK_GROUPS:
        raise ValueError(f"Unknown stock group: {group}")
    with uow:
        lots = uow.warehouse.filtered(query, warehouse, include_zero)
    groups: dict[str, StockTotals] = {}
    articles: dict[tuple[str, str], StockTotals] = {}
    for lot in lots:
        value = stock_group_value(lot, group)
        article = stock_article_key(lot)
        groups.setdefault(value, StockTotals()).add(lot, article)
        articles.setdefault((value, article), StockTotals()).add(lot, article)
    return groups, articles


@dataclass(frozen=True)
class LotDestination:
    """Where a lot was written off to («Откуда» of its return): the sums are the journal's, not the lot's."""

    name: str
    written_off: Decimal
    returned: Decimal
    outstanding: Decimal
    unit: str
    last_date: datetime


def _destination_label(entry: WriteOffEntry) -> str:
    if entry.kind == "issue":  # a kitting issue names no НАЗНАЧЕНИЕ: its basis is «Выдача <номер заявки>»
        return re.sub(r"^\s*выдача\s*", "", entry.reason, flags=re.IGNORECASE).strip()
    return " ".join(str(entry.purpose or "").split())


def lot_destinations(lot_id: UUID, uow: UnitOfWork) -> list[LotDestination]:
    """Destinations (project number or surname) this lot was written off or issued to, with what is still out
    there (written off − returned from the same destination, case-insensitively), newest write-off first.
    Fully returned destinations and rows that name no destination are left out."""
    with uow:
        if uow.warehouse.get(lot_id) is None:
            raise KeyError(f"Warehouse lot not found: {lot_id}")
        entries = uow.writeoffs.for_lot(lot_id)
    groups: dict[str, dict] = {}
    for entry in sorted(entries, key=lambda item: item.occurred_at):
        if entry.kind == "supplier_return" or not entry.posted:
            continue
        is_return = entry.kind == "return" or (entry.kind == "legacy" and bool(_RETURN_REASON.match(entry.reason)))
        label = _destination_label(entry)
        if not label:
            continue
        group = groups.setdefault(label.casefold(), {
            "name": label, "out": Decimal("0"), "back": Decimal("0"), "unit": entry.unit, "last": None,
        })
        if is_return:
            group["back"] += entry.quantity
            continue
        group["name"], group["unit"] = label, entry.unit or group["unit"]
        group["out"] += entry.quantity
        group["last"] = entry.occurred_at
    result = [
        LotDestination(g["name"], g["out"], g["back"], g["out"] - g["back"], g["unit"], g["last"])
        for g in groups.values() if g["last"] is not None and g["out"] - g["back"] > 0
    ]
    return sorted(result, key=lambda item: item.last_date, reverse=True)


@dataclass(frozen=True)
class ShelfOccupancy:
    warehouse: str
    shelf: str
    occupied: list[str]  # rack codes on the shelf where a lot with a balance lies
    next_number: int | None  # smallest free № on the shelf; None when all 999 are taken


def shelf_occupancy(uow: UnitOfWork, warehouse: str, shelf: str) -> ShelfOccupancy:
    """SK-15: what lies on a shelf and where the next lot may go. Advice only — nothing is refused."""
    if warehouse not in WAREHOUSES:
        raise WarehouseValidationError("Неизвестный склад")
    try:
        code = existing_shelf(shelf)
    except ValueError as error:
        raise WarehouseValidationError(str(error)) from error
    with uow:
        used = list(uow.warehouse.occupied_locations(warehouse))
    return ShelfOccupancy(warehouse, code, places_on_shelf(used, code), next_free_number(used, code))
