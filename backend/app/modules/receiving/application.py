"""Transactional Receiving use cases."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.notifications import (
    SUPPLY_AUDIENCE,
    TECHNOLOGIST_AUDIENCE,
    notification_requested,
    publish_notification,
)
from app.shared.print_sheet import PrintSheet

from .domain import (
    ACT_KINDS,
    DELIVERY_DEVIATIONS,
    EXPIRED,
    MANUAL_NONCONFORMITIES,
    MISSING_QUALITY_DOCUMENT,
    QUEUE_DEFAULT,
    QUEUE_PRESETS,
    WARNING_NONCONFORMITIES,
    InspectionInput,
    ReceivingLine,
    ReceivingStatus,
    QUARANTINE_WORKDAYS,
    ReceivingValidationError,
    QUALITY_FAULTS,
    QUANTITY_FAULTS,
    SupplierReturnMode,
    act_number_prefix,
    create_line_from_expected,
    supplier_return_basis,
    latest_overdue_inspection_date,
    quarantine_notify_targets,
    NOTIFY_SUPPLY,
    NOTIFY_TECHNOLOGIST,
    RECEIVING_FILE_PATH,
    SHIPPING_DOCUMENT_PENDING_NOTE,
    refuse_backorder_remainder,
)


SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
_NOTIFY_AUDIENCES = {NOTIFY_TECHNOLOGIST: TECHNOLOGIST_AUDIENCE, NOTIFY_SUPPLY: SUPPLY_AUDIENCE}


def _notify_audiences(line: ReceivingLine) -> tuple[str, ...]:
    return tuple(_NOTIFY_AUDIENCES[target] for target in quarantine_notify_targets(line.nonconformities))


def _notify_nonconformity(line: ReceivingLine, uow: UnitOfWork, now: datetime) -> None:
    """Receiving.gs:757-788 `notifyNonconformityAlerts_`: a nonconformity after ВК goes at once to the
    technologist (брак) and/or supply (everything else). One event per inspected line; SUPPLYMAP grouped a
    sheet run per person. A shortfall alerts although the line is «Принято» (owner, 05.10.2026); the
    warnings alone («Излишек», no quality document) alert nobody."""
    if not any(item not in WARNING_NONCONFORMITIES for item in line.nonconformities):
        return
    nature = ", ".join(line.nonconformities)
    waiting = (
        f"\n\nНужно решение в течение {QUARANTINE_WORKDAYS} раб. дн. (устранено → повторный ВК; "
        "подтверждено → РЕШЕНИЕ), иначе позиция автоматически уйдёт в Карантин."
        if line.status is ReceivingStatus.WAITING else ""
    )
    publish_notification(uow.outbox, notification_requested(
        key=f"receiving:{line.id}:nonconformity:v{line.version}",
        now=now,
        title=f"Несоответствия при приёмке — {line.legacy_id} ({line.warehouse})",
        body=(
            f"Входной контроль выявил несоответствия ({line.warehouse}):\n\n"
            f"• {line.legacy_id} · {line.name or '—'} — {nature} → статус «{line.status.value}»{waiting}"
        ),
        kind="nonconformity",
        reference=line.legacy_id,
        audiences=_notify_audiences(line),
    ))


class ReceivingRepository(Protocol):
    def by_source_idempotency_key(self, key: str) -> ReceivingLine | None: ...
    def get(self, line_id: UUID) -> ReceivingLine | None: ...
    def queue(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
    ) -> Sequence[ReceivingLine]: ...
    def work_queue(
        self,
        limit: int,
        after_id: UUID | None,
        *,
        ranks: frozenset[int],
        statuses: Sequence[ReceivingStatus] = (),
    ) -> Sequence[ReceivingLine]: ...
    def waiting_inspected_by(self, day: date, limit: int = 200) -> Sequence[ReceivingLine]: ...
    def awaiting_placement(self, limit: int = 500) -> Sequence[ReceivingLine]: ...
    def by_status(self, status: ReceivingStatus, limit: int = 500) -> Sequence[ReceivingLine]: ...
    def statuses_by_procurement_line(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, list[str]]: ...
    def latest_inspection_by_procurement_line(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, date]: ...
    def open_by_procurement_lines(self, procurement_line_ids: Sequence[UUID]) -> Sequence[ReceivingLine]: ...
    def all_grns(self) -> Sequence[str]: ...
    def legacy_ids_starting_with(self, prefix: str) -> Sequence[str]: ...

    def act_numbers_with_prefixes(self, prefixes: Iterable[str]) -> Sequence[str]: ...
    def add(self, line: ReceivingLine) -> None: ...


@dataclass(frozen=True)
class StockLotBalance:
    """What the warehouse holds for one ГРН + ПАКЕТ, as a supplier return sees it."""

    warehouse: str
    balance: Decimal
    unit: str = ""


class StockBalanceLookup(Protocol):
    """Read-only port into the Warehouse module (SupplierReturn.gs warehouseGrnInfo_): the lot of a
    ГРН + ПАКЕТ in either warehouse, None when it is not on stock."""

    def lot_balance(self, grn: str, package: str) -> StockLotBalance | None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class OutboxRepository(AppendOnlyRepository, Protocol):
    def by_idempotency_key(self, key: str) -> OutboxMessage | None: ...
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class UnitOfWork(Protocol):
    receiving: ReceivingRepository
    audit: AppendOnlyRepository
    outbox: OutboxRepository

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


class ConcurrentReceivingUpdate(RuntimeError):
    pass


class PoisonReceivingMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


@dataclass(frozen=True)
class InspectReceivingCommand:
    line_id: UUID
    expected_version: int
    inspection: InspectionInput
    actor: Actor
    now: datetime
    manual_grn_prefix: str | None = None


@dataclass(frozen=True)
class PlaceReceivingCommand:
    line_id: UUID
    expected_version: int
    storage_location: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class OpenVkRevisionCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SupplierReturnItem:
    line_id: UUID
    expected_version: int
    quantity: Decimal | None = None  # empty → КОЛ-ВО ФАКТ of the line


@dataclass(frozen=True)
class SupplierReturnCommand:
    items: tuple[SupplierReturnItem, ...]
    reason: str
    mode: SupplierReturnMode
    actor: Actor
    now: datetime
    claim: str = ""
    comment: str = ""


@dataclass(frozen=True)
class SupplierReturnResult:
    lines: tuple[ReceivingLine, ...]
    writeoffs: int
    notes: tuple[str, ...]

    @property
    def message(self) -> str:
        """The report SupplierReturn.gs gives back: what went back, what the warehouse will write off, warnings."""
        if not self.lines:
            return ""
        status = self.lines[0].status.value
        done = ", ".join(
            f"{line.name or line.legacy_id}" + (f" ({line.grn})" if line.grn else "") for line in self.lines
        )
        text = f"Возврат оформлен ({len(self.lines)}): {done}. Статус приёмки → «{status}»"
        if self.writeoffs:
            text += f"; списаний со склада: {self.writeoffs}"
        return text + (f". Внимание: {'; '.join(self.notes)}" if self.notes else ".")


def _quantity_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def return_to_supplier(
    command: SupplierReturnCommand,
    uow: UnitOfWork,
    stock: StockBalanceLookup,
) -> SupplierReturnResult:
    """SupplierReturn.gs supplierReturnApply_: построчный возврат пачкой по одной поставке.

    Each accepted line changes status and gets the basis in ОПИСАНИЕ НЕСООТВ.; when its lot lies on
    stock with enough balance, the warehouse is asked to write the returned quantity off
    (`warehouse.supplier_return_requested`, same transaction). A line whose lot is not on stock (not
    placed yet, no ГРН) is returned in receiving only and the report says so. A line no longer
    «Принято» is skipped with a note; nothing done at all is an error."""
    if not command.items:
        raise ReceivingValidationError("Не выбрано ни одной позиции.")
    basis = supplier_return_basis(command.reason, command.claim, command.comment)
    done: list[ReceivingLine] = []
    notes: list[str] = []
    writeoffs = 0
    with uow:
        for item in command.items:
            current = uow.receiving.get(item.line_id)
            if current is None:
                raise KeyError(f"Receiving line not found: {item.line_id}")
            if current.version != item.expected_version:
                raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
            label = current.name or current.legacy_id
            if current.status is not ReceivingStatus.ACCEPTED:
                notes.append(f"{label}: статус уже «{current.status.value}» — пропущена")
                continue
            quantity = item.quantity
            if quantity is None:
                quantity = current.fact_quantity or current.accepted_quantity
            if quantity is None or quantity <= 0:
                notes.append(f"{label}: не удалось определить кол-во — пропущена")
                continue
            returned = current.return_to_supplier(
                reason=command.reason, mode=command.mode, claim=command.claim, comment=command.comment
            )
            written_off = False
            if not current.grn:
                notes.append(f"{label}: ГРН не присвоен — складской проводки нет, только статус приёмки")
            else:
                lot = stock.lot_balance(current.grn, current.package) if current.warehouse_event_emitted else None
                if lot is None:
                    notes.append(f"{label}: на складе партии {current.grn} нет (ещё не разложена) — снимать нечего")
                elif quantity > lot.balance:
                    notes.append(
                        f"{label}: склад — больше остатка (есть {_quantity_text(lot.balance)}), списание не поставлено"
                    )
                else:
                    uow.outbox.add(
                        OutboxMessage(
                            id=uuid4(),
                            topic="warehouse.supplier_return_requested",
                            idempotency_key=f"receiving:{current.id}:supplier-return:{returned.supplier_returns}",
                            created_at=command.now,
                            payload={
                                "receiving_line_id": str(current.id),
                                "legacy_id": current.legacy_id,
                                "warehouse": lot.warehouse,
                                "grn": current.grn,
                                "package": current.package,
                                "quantity": str(quantity),
                                "reason": basis,
                                "supplier": current.supplier,
                                "actor_id": str(command.actor.id),
                                "actor_name": command.actor.name,
                            },
                        )
                    )
                    written_off = True
                    writeoffs += 1
            uow.receiving.add(returned)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=returned.id,
                    event_type="receiving.returned_to_supplier",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": returned.legacy_id,
                        "from_status": current.status.value,
                        "to_status": returned.status.value,
                        "quantity": str(quantity),
                        "basis": basis,
                        "grn": current.grn,
                        "warehouse_writeoff": written_off,
                    },
                )
            )
            done.append(returned)
        if not done:
            raise ReceivingValidationError("Ни одна позиция не оформлена. " + "; ".join(notes))
        uow.commit()
    return SupplierReturnResult(lines=tuple(done), writeoffs=writeoffs, notes=tuple(notes))


def supplier_return_candidates(uow: UnitOfWork, *, limit: int = 500) -> list[ReceivingLine]:
    """SupplierReturn.gs supplierReturnRows_: строки «Принято» — товар уже у нас."""
    with uow:
        return list(uow.receiving.by_status(ReceivingStatus.ACCEPTED, limit))


def consume_receiving_expected(message: OutboxMessage, uow: UnitOfWork) -> ReceivingLine:
    with uow:
        existing = uow.receiving.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                raise ReceivingValidationError("Ключ события приёмки уже использован")
            return existing
        line = create_line_from_expected(message)
        uow.receiving.add(line)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=line.id,
                event_type="receiving.expected",
                actor=SYSTEM_ACTOR,
                occurred_at=message.created_at,
                details={"legacy_id": line.legacy_id, "procurement_line_id": str(line.procurement_line_id)},
            )
        )
        uow.commit()
        return line


def dispatch_next_receiving_expected(now: datetime, uow: UnitOfWork) -> ReceivingLine | None:
    with uow:
        message = uow.outbox.next_unpublished("receiving.expected", now)
        if message is None:
            return None
        existing = uow.receiving.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                reason = "Ключ события приёмки уже использован"
                uow.outbox.mark_failed(message.id, now, reason)
                uow.commit()
                raise PoisonReceivingMessage(message.id, reason)
            line = existing
        else:
            try:
                line = create_line_from_expected(message)
            except ReceivingValidationError as error:
                uow.outbox.mark_failed(message.id, now, str(error))
                uow.commit()
                raise PoisonReceivingMessage(message.id, str(error)) from error
            uow.receiving.add(line)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=line.id,
                    event_type="receiving.expected",
                    actor=SYSTEM_ACTOR,
                    occurred_at=message.created_at,
                    details={
                        "legacy_id": line.legacy_id,
                        "procurement_line_id": str(line.procurement_line_id),
                    },
                )
            )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return line


def _warehouse_payload(line: ReceivingLine) -> dict:
    return {
        "receiving_line_id": str(line.id),
        # The lot counts each receipt once by this id; a replacement after a «wait» supplier return is a
        # new receipt of the same line (RC-6), so it gets its own.
        "receipt_id": f"{line.id}:r{line.supplier_returns}" if line.supplier_returns else str(line.id),
        "legacy_id": line.legacy_id,
        "warehouse": line.warehouse,
        "grn": line.grn,
        "package": line.package,
        "article": line.article,
        "series": line.series,
        # Warehouse.gs:411 — НАИМЕНОВАНИЕ остатка из НАИМЕНОВАНИЕ (УПД/М-15); пустое — из заказа.
        "name": line.document_name or line.name,
        "item_type": line.item_type,
        "purpose": line.purpose,
        "unit": line.unit,
        "quantity": str(line.accepted_quantity),
        "storage_location": line.storage_location,
        # Warehouse.gs:418-420: СРОК ГОДНОСТИ and МАССА 1 ШТ go with the receipt into the stock row.
        "expiry_date": line.expiry_date.isoformat() if line.expiry_date else None,
        "unit_mass_g": format(line.unit_mass_g, "f") if line.unit_mass_g is not None else None,
    }


def _emit_warehouse_if_ready(line: ReceivingLine, uow: UnitOfWork, actor: Actor, now: datetime) -> ReceivingLine:
    if not line.ready_for_warehouse:
        return line
    key = line.warehouse_event_key
    if uow.outbox.by_idempotency_key(key) is None:
        uow.outbox.add(
            OutboxMessage(
                id=uuid4(),
                topic="warehouse.received",
                idempotency_key=key,
                created_at=now,
                payload=_warehouse_payload(line),
            )
        )
    emitted = line.mark_warehouse_event_emitted()
    uow.receiving.add(emitted)
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=line.id,
            event_type="receiving.sent_to_warehouse",
            actor=actor,
            occurred_at=now,
            details={"grn": line.grn, "warehouse": line.warehouse},
        )
    )
    return emitted


def inspect_receiving(command: InspectReceivingCommand, uow: UnitOfWork) -> ReceivingLine:
    with uow:
        current = uow.receiving.get(command.line_id)
        if current is None:
            raise KeyError(f"Receiving line not found: {command.line_id}")
        if current.version != command.expected_version:
            raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
        inspection = replace(
            command.inspection,
            ordered_quantity=current.ordered_quantity,
            quality_document_required=current.quality_document_required,
            has_shipping_document=bool(
                str(command.inspection.shipping_document_url or "").strip()
            ),
            quality_document_present=bool(
                str(command.inspection.quality_document_url or "").strip()
            ),
        )
        inspected = _inspect_line(
            current,
            inspection,
            uow,
            actor=command.actor,
            now=command.now,
            existing_grns=uow.receiving.all_grns(),
            existing_act_numbers=_act_numbers_for(inspection, uow),
            manual_grn_prefix=command.manual_grn_prefix,
        )
        result = _emit_warehouse_if_ready(inspected, uow, command.actor, command.now)
        uow.commit()
        return result


def _inspect_line(
    current: ReceivingLine,
    inspection: InspectionInput,
    uow: UnitOfWork,
    *,
    actor: Actor,
    now: datetime,
    existing_grns: Iterable[str],
    existing_act_numbers: Iterable[str],
    manual_grn_prefix: str | None,
    audit_details: Mapping[str, object] | None = None,
) -> ReceivingLine:
    """One line's ВК inside the caller's transaction: verdict, ГРН and acts, the `-Д` backorder of a first
    inspection, the nonconformity alert. The hand-over to the warehouse is the caller's."""
    _guard_backorder_remainder(current, inspection, uow)
    inspected = current.inspect(
        inspection,
        actor,
        existing_grns=existing_grns,
        existing_act_numbers=existing_act_numbers,
        manual_grn_prefix=manual_grn_prefix,
    )
    uow.receiving.add(inspected)
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=inspected.id,
            event_type="receiving.inspected",
            actor=actor,
            occurred_at=now,
            details={
                "legacy_id": inspected.legacy_id,
                "from_status": current.status.value,
                "to_status": inspected.status.value,
                "verdict": inspected.verdict,
                **(audit_details or {}),
            },
        )
    )
    if current.status is ReceivingStatus.IN_TRANSIT:
        _request_backorder(inspected, uow, now)
    _notify_nonconformity(inspected, uow, now)
    return inspected


def _backorder_key(line: ReceivingLine) -> str:
    # A `-ВN` revision re-inspects the same physical delivery: one backorder per lineage, never twice.
    return f"receiving:{line.lineage_root_id}:backorder"


def _guard_backorder_remainder(current: ReceivingLine, inspection: InspectionInput, uow: UnitOfWork) -> None:
    """Once the `-Д` backorder of a delivery is ordered, its remainder is received on the `-Д` line: a
    re-inspection (or a `-ВN` revision) may not take more than the УПД the backorder was computed from."""
    message = uow.outbox.by_idempotency_key(_backorder_key(current))
    if message is None:
        return
    source: ReceivingLine | None = current
    while source is not None and source.document_quantity is None and source.revision_of_id is not None:
        source = uow.receiving.get(source.revision_of_id)
    if source is None or source.document_quantity is None:
        return
    document_cap = source.document_quantity
    fact_cap = max(document_cap, source.fact_quantity or document_cap)
    base = current.base_legacy_id
    pattern = re.compile(rf"{re.escape(base)}-Д\d*")
    backorders = sorted(
        value for value in uow.receiving.legacy_ids_starting_with(f"{base}-Д") if pattern.fullmatch(value)
    )
    refuse_backorder_remainder(
        legacy_id=current.legacy_id,
        unit=current.unit,
        backorder_quantity=Decimal(str(message.payload.get("backorder_quantity") or "0")),
        backorder_legacy_ids=backorders,
        document_cap=document_cap,
        fact_cap=fact_cap,
        document_quantity=inspection.document_quantity,
        fact_quantity=inspection.fact_quantity,
    )


def _request_backorder(line: ReceivingLine, uow: UnitOfWork, now: datetime) -> None:
    # Receiving.gs:761 — only the first inspection (no act yet) records a backorder for УПД < заказ;
    # re-inspections of «Ожидание»/«Карантин» never do. Procurement turns it into the paid `-Д` line.
    if line.backorder_quantity <= 0:
        return
    key = _backorder_key(line)
    if uow.outbox.by_idempotency_key(key) is not None:
        return
    uow.outbox.add(
        OutboxMessage(
            id=uuid4(),
            topic="procurement.backorder_requested",
            idempotency_key=key,
            created_at=now,
            payload={
                "procurement_line_id": str(line.procurement_line_id),
                "receiving_line_id": str(line.id),
                "legacy_id": line.legacy_id,
                "backorder_quantity": str(line.backorder_quantity),
            },
        )
    )


@dataclass(frozen=True)
class InspectBatchesCommand:
    """RC-13: one delivery, several manufacturer batches — each gets its own line, ВК and ГРН."""

    line_id: UUID
    expected_version: int
    batches: tuple[InspectionInput, ...]
    actor: Actor
    now: datetime
    manual_grn_prefix: str | None = None


def _act_numbers_for(data: InspectionInput, uow: UnitOfWork) -> Sequence[str]:
    """Номера актов, уже занятые в дни, которыми датируются акты этой приёмки."""
    days = {data.inspection_date, data.verdict_date or data.inspection_date}
    return uow.receiving.act_numbers_with_prefixes(
        act_number_prefix(kind, day) for kind in ACT_KINDS for day in days
    )


def batch_quantity(data: InspectionInput) -> Decimal:
    """Receiving.gs batchQty_: кол-во партии — по УПД, иначе по факту."""
    if data.document_quantity is not None and data.document_quantity > 0:
        return data.document_quantity
    return data.fact_quantity


def inspect_receiving_batches(command: InspectBatchesCommand, uow: UnitOfWork) -> list[ReceivingLine]:
    """Receiving.gs submitReceiveFormImpl_ (≥ 2 партии) + applyWaitlistBatchSplits_: the line becomes batch 1,
    the others new lines `-ПN`; each is inspected at once with its own facts and the shared ones. Sum of
    batches below the order → the remainder goes to the backorder like any shortfall; above → surplus."""
    if len(command.batches) < 2:
        raise ReceivingValidationError("Для разбивки нужно минимум 2 партии")
    with uow:
        current = uow.receiving.get(command.line_id)
        if current is None:
            raise KeyError(f"Receiving line not found: {command.line_id}")
        if current.version != command.expected_version:
            raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
        quantities = [batch_quantity(batch) for batch in command.batches]
        _guard_backorder_remainder(
            current,
            replace(
                command.batches[0],
                document_quantity=sum(quantities, Decimal("0")),
                fact_quantity=sum((batch.fact_quantity for batch in command.batches), Decimal("0")),
            ),
            uow,
        )
        parts = current.split_into_batches(
            quantities,
            existing_legacy_ids=uow.receiving.legacy_ids_starting_with(current.legacy_id),
            now=command.now,
        )
        grns = list(uow.receiving.all_grns())
        acts = list(_act_numbers_for(command.batches[0], uow))
        inspected: list[ReceivingLine] = []
        for part, data in zip(parts, command.batches):
            inspection = replace(
                data,
                ordered_quantity=part.ordered_quantity,
                quality_document_required=part.quality_document_required,
                has_shipping_document=bool(str(data.shipping_document_url or "").strip()),
                quality_document_present=bool(str(data.quality_document_url or "").strip()),
            )
            line = part.inspect(
                inspection,
                command.actor,
                existing_grns=grns,
                manual_grn_prefix=command.manual_grn_prefix,
                existing_act_numbers=acts,
            )
            grns.append(line.grn)
            acts.extend(line.act(kind)[0] for kind in ACT_KINDS)
            uow.receiving.add(line)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=line.id,
                    event_type="receiving.inspected",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": line.legacy_id,
                        "from_status": current.status.value,
                        "to_status": line.status.value,
                        "verdict": line.verdict,
                        "batch_of": current.legacy_id,
                        "batch_quantity": str(line.ordered_quantity),
                    },
                )
            )
            _notify_nonconformity(line, uow, command.now)
            inspected.append(line)
        remainder = current.ordered_quantity - sum(quantities, Decimal("0"))
        if remainder > 0:
            _request_backorder(replace(inspected[0], backorder_quantity=remainder), uow, command.now)
        uow.commit()
        return inspected


@dataclass(frozen=True)
class UnpackCommand:
    line_id: UUID
    expected_version: int
    quantities: tuple[Decimal, ...]
    actor: Actor
    now: datetime


def unpack_receiving(command: UnpackCommand, uow: UnitOfWork) -> list[ReceivingLine]:
    """RC-8 «Разукомплектовка (пакеты)»: пакеты с общим ГРН до раскладки на склад."""
    with uow:
        current = uow.receiving.get(command.line_id)
        if current is None:
            raise KeyError(f"Receiving line not found: {command.line_id}")
        if current.version != command.expected_version:
            raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
        packages = current.unpack(
            list(command.quantities),
            existing_legacy_ids=uow.receiving.legacy_ids_starting_with(current.legacy_id),
            now=command.now,
        )
        for package in packages:
            uow.receiving.add(package)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=current.id,
                event_type="receiving.unpacked",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "legacy_id": current.legacy_id,
                    "grn": current.grn,
                    "packages": [
                        {
                            "legacy_id": package.legacy_id,
                            "package": package.package,
                            "quantity": str(package.fact_quantity),
                        }
                        for package in packages
                    ],
                },
            )
        )
        uow.commit()
        return packages


# УПД/М-15 and the quality document are kept in the ERP database; SUPPLYMAP put them in a Drive folder
# without a cap (owner, 29.09: ≤ 20 МБ, as the shipment УПД). Types as the portal's file picker.
RECEIVING_FILE_LIMIT = 20 * 1024 * 1024
RECEIVING_FILE_KINDS = {"upd": "УПД", "quality": "Документ качества"}
RECEIVING_FILE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class StoredReceivingFile:
    id: UUID
    receiving_line_id: UUID
    kind: str
    file_name: str
    content_type: str
    size: int
    uploaded_by: str
    uploaded_at: datetime


class ReceivingFileRepository(Protocol):
    def add(self, stored: StoredReceivingFile, content: bytes) -> None: ...
    def get(self, file_id: UUID) -> tuple[StoredReceivingFile, bytes] | None: ...


class ReceivingFileUnitOfWork(UnitOfWork, Protocol):
    files: ReceivingFileRepository


@dataclass(frozen=True)
class AttachReceivingFileCommand:
    line_id: UUID
    kind: str
    file_name: str
    content_type: str
    content: bytes
    actor: Actor
    now: datetime


def receiving_file_url(file_id: UUID) -> str:
    return f"{RECEIVING_FILE_PATH}{file_id}"


def attach_receiving_file(command: AttachReceivingFileCommand, uow: ReceivingFileUnitOfWork) -> StoredReceivingFile:
    """Portal «Документ УПД/М-15 (файл)»: the scan goes to the ERP storage and its link fills the ВК form.

    The line itself is not changed here — the link lands on it with the inspection, as SUPPLYMAP wrote the
    Drive link with `portalSubmitReceive`; a file picked and then not used stays only in the audit."""
    label = RECEIVING_FILE_KINDS.get(command.kind)
    if label is None:
        raise ReceivingValidationError("Неизвестный вид документа")
    if not command.content:
        raise ReceivingValidationError("Файл не передан")
    if len(command.content) > RECEIVING_FILE_LIMIT:
        raise ReceivingValidationError(f"{label}: файл больше 20 МБ")
    original = str(command.file_name or "").strip()
    extension = original[original.rfind("."):].lower() if "." in original else ""
    if extension not in RECEIVING_FILE_EXTENSIONS:
        raise ReceivingValidationError(f"{label}: нужен PDF или фото JPG/PNG")
    with uow:
        line = uow.receiving.get(command.line_id)
        if line is None:
            raise KeyError("Строка приёмки не найдена")
        if line.status is ReceivingStatus.VK_ARCHIVE:
            raise ReceivingValidationError("Строка в «Архив ВК» — документы прикладываются к её ревизии")
        name = " ".join(part for part in (label, line.order_number, line.legacy_id) if part) + extension
        stored = StoredReceivingFile(
            id=uuid4(),
            receiving_line_id=line.id,
            kind=command.kind,
            file_name=name,
            content_type=str(command.content_type or "").strip() or "application/octet-stream",
            size=len(command.content),
            uploaded_by=command.actor.name,
            uploaded_at=command.now,
        )
        uow.files.add(stored, command.content)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=line.id,
                event_type="receiving.file_attached",
                actor=command.actor,
                occurred_at=command.now,
                details={"legacy_id": line.legacy_id, "kind": command.kind, "file_id": str(stored.id), "name": name},
            )
        )
        uow.commit()
        return stored


def open_vk_revision(command: OpenVkRevisionCommand, uow: UnitOfWork) -> tuple[ReceivingLine, ReceivingLine]:
    with uow:
        current = uow.receiving.get(command.line_id)
        if current is None:
            raise KeyError(f"Receiving line not found: {command.line_id}")
        if current.version != command.expected_version:
            raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
        archived, revision = current.open_vk_revision(
            existing_legacy_ids=uow.receiving.legacy_ids_starting_with(current.base_legacy_id),
            now=command.now,
        )
        uow.receiving.add(archived)
        uow.receiving.add(revision)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=archived.id,
                event_type="receiving.vk_archived",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "legacy_id": archived.legacy_id,
                    "from_status": current.status.value,
                    "revision_id": str(revision.id),
                    "revision_legacy_id": revision.legacy_id,
                },
            )
        )
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=revision.id,
                event_type="receiving.vk_revision_opened",
                actor=command.actor,
                occurred_at=command.now,
                details={"legacy_id": revision.legacy_id, "revision_of_id": str(archived.id)},
            )
        )
        uow.commit()
        return archived, revision


MAX_DOCUMENT_LINES = 500


@dataclass(frozen=True)
class AttachShippingDocumentsCommand:
    lines: tuple[tuple[UUID, int], ...]
    url: str | None
    number: str
    document_date: date | None
    actor: Actor
    now: datetime


def attach_shipping_documents(command: AttachShippingDocumentsCommand, uow: UnitOfWork) -> list[ReceivingLine]:
    """Один УПД на несколько позиций заказа: всё или ничего, одна транзакция."""
    if not command.lines:
        raise ReceivingValidationError("Выберите позиции для УПД")
    if len(command.lines) > MAX_DOCUMENT_LINES:
        raise ReceivingValidationError(f"За раз можно внести УПД не более чем в {MAX_DOCUMENT_LINES} позиций")
    with uow:
        results: list[ReceivingLine] = []
        changed = False
        for line_id, expected_version in command.lines:
            current = uow.receiving.get(line_id)
            if current is None:
                raise KeyError(f"Receiving line not found: {line_id}")
            if current.version != expected_version:
                raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
            updated = current.attach_shipping_document(
                url=command.url, number=command.number, document_date=command.document_date
            )
            if updated != current:
                changed = True
                uow.receiving.add(updated)
                uow.audit.add(
                    AuditEvent(
                        id=uuid4(),
                        aggregate_id=updated.id,
                        event_type="receiving.document_attached",
                        actor=command.actor,
                        occurred_at=command.now,
                        details={
                            "legacy_id": updated.legacy_id,
                            "document_number": updated.document_number,
                            "document_date": updated.document_date.isoformat() if updated.document_date else None,
                            "shipping_document_url": updated.shipping_document_url,
                        },
                    )
                )
            results.append(updated)
        if changed:
            uow.commit()
        return results


def _place_line(
    current: ReceivingLine, location: str, uow: UnitOfWork, actor: Actor, now: datetime
) -> ReceivingLine:
    placed = current.place(location)
    if placed != current:
        uow.receiving.add(placed)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=placed.id,
                event_type="receiving.placed",
                actor=actor,
                occurred_at=now,
                details={"grn": placed.grn, "storage_location": placed.storage_location},
            )
        )
    return placed


def place_receiving(command: PlaceReceivingCommand, uow: UnitOfWork) -> ReceivingLine:
    with uow:
        current = uow.receiving.get(command.line_id)
        if current is None:
            raise KeyError(f"Receiving line not found: {command.line_id}")
        if current.version != command.expected_version:
            raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
        placed = _place_line(current, command.storage_location, uow, command.actor, command.now)
        result = _emit_warehouse_if_ready(placed, uow, command.actor, command.now)
        if result == current:
            return result
        uow.commit()
        return result


MAX_DELIVERY_LINES = 200
DELIVERY_STATUSES = frozenset({ReceivingStatus.IN_TRANSIT, ReceivingStatus.WAITING})


@dataclass(frozen=True)
class DeliveryLineInput:
    """One position of a delivery. Quantities are typed only on a discrepancy (owner, 05.10.2026): no УПД
    quantity → the ordered one, no fact → the УПД one."""

    line_id: UUID
    expected_version: int
    document_quantity: Decimal | None = None
    fact_quantity: Decimal | None = None
    storage_location: str | None = None
    deviation: str = "none"
    deviation_quantity: Decimal | None = None
    expiry_date: date | None = None
    series: str = ""
    manual_grn_prefix: str | None = None


@dataclass(frozen=True)
class AcceptDeliveryCommand:
    """«Провести поставку»: one УПД of one supplier to one warehouse, any number of its orders' positions."""

    lines: tuple[DeliveryLineInput, ...]
    document_number: str
    document_date: date | None
    actor: Actor
    now: datetime
    shipping_document_url: str | None = None
    warehouse: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    delivery_id: UUID
    lines: tuple[ReceivingLine, ...]


def _delivery_inspection(
    current: ReceivingLine, item: DeliveryLineInput, command: AcceptDeliveryCommand
) -> InspectionInput:
    nature = DELIVERY_DEVIATIONS.get(str(item.deviation or "none"))
    if nature is None:
        raise ReceivingValidationError("Неизвестный вид отклонения")
    has_upd = bool(command.document_number or command.document_date or command.shipping_document_url or current.shipping_document_url or current.document_number)
    document = item.document_quantity if item.document_quantity is not None else current.ordered_quantity
    fact = item.fact_quantity if item.fact_quantity is not None else document
    today = command.now.date()
    expiry = item.expiry_date or current.expiry_date
    if nature == EXPIRED and (expiry is None or expiry >= today):
        raise ReceivingValidationError("Отклонение «срок годности истёк» — укажите истёкший срок годности")
    description = ""
    if item.deviation_quantity is not None:
        if not nature:
            raise ReceivingValidationError("Количество отклонения указано без вида отклонения")
        if item.deviation_quantity <= 0 or item.deviation_quantity > fact:
            raise ReceivingValidationError("Количество отклонения — больше нуля и не больше факта")
        description = f"{nature}: {format(item.deviation_quantity.normalize(), 'f')} {current.unit}"
    return InspectionInput(
        fact_quantity=fact,
        document_quantity=document,
        ordered_quantity=current.ordered_quantity,
        # Optional, as in a single ВК: without a УПД the line is «Принято» with SHIPPING_DOCUMENT_PENDING_NOTE
        # (ReceivingLine.shipping_document_pending) until «Внести УПД» adds it.
        has_shipping_document=has_upd,
        quality_document_required=current.quality_document_required,
        # The receiver marks a missing certificate; otherwise it came with the goods.
        quality_document_present=nature != MISSING_QUALITY_DOCUMENT,
        expiry_date=expiry,
        inspection_date=today,
        manual_nonconformities=(nature,) if nature in MANUAL_NONCONFORMITIES else (),
        disposition=current.disposition,
        shipping_document_url=command.shipping_document_url or current.shipping_document_url,
        quality_document_url=current.quality_document_url,
        series=item.series,
        document_number=command.document_number,
        document_date=command.document_date,
        note=current.inspection_note,
        manufacture_date=current.manufacture_date,
        nonconformity_description=description,
    )


def accept_delivery(command: AcceptDeliveryCommand, uow: UnitOfWork) -> DeliveryResult:
    """«Провести поставку» in one transaction, all or nothing: every position gets its own ВК (acts, ГРН, the
    `-Д` backorder of a недовоз, the alert), the place on the shelf when given, and the hand-over to the
    warehouse under its own idempotency key. A stale version anywhere refuses the whole delivery, so a
    repeated request changes nothing."""
    if not command.lines:
        raise ReceivingValidationError("Выберите позиции поставки")
    if len(command.lines) > MAX_DELIVERY_LINES:
        raise ReceivingValidationError(f"В одной поставке не более {MAX_DELIVERY_LINES} позиций")
    if len({item.line_id for item in command.lines}) != len(command.lines):
        raise ReceivingValidationError("Позиция указана в поставке дважды")
    command = replace(command, document_number=str(command.document_number or "").strip())
    with uow:
        loaded: list[ReceivingLine] = []
        for item in command.lines:
            current = uow.receiving.get(item.line_id)
            if current is None:
                raise KeyError(f"Receiving line not found: {item.line_id}")
            if current.version != item.expected_version:
                raise ConcurrentReceivingUpdate(
                    f"Позиция {current.legacy_id} изменилась после загрузки формы — обновите поставку"
                )
            loaded.append(current)
        for current in loaded:
            if current.status not in DELIVERY_STATUSES or current.warehouse_event_emitted:
                raise ReceivingValidationError(
                    f"{current.legacy_id}: статус «{current.status.value}» — поставкой проводятся только "
                    "позиции «В пути» и «Ожидание»"
                )
            if command.warehouse and current.warehouse != command.warehouse:
                raise ReceivingValidationError(
                    f"{current.legacy_id}: склад «{current.warehouse}», а поставка — на «{command.warehouse}»"
                )
        warehouses = sorted({current.warehouse for current in loaded})
        if len(warehouses) > 1:
            raise ReceivingValidationError(f"В одной поставке позиции разных складов: {', '.join(warehouses)}")
        suppliers = {current.supplier.strip().casefold(): current.supplier.strip() for current in loaded}
        if len(suppliers) > 1:
            raise ReceivingValidationError(
                f"В одной поставке позиции разных поставщиков: {', '.join(sorted(suppliers.values()))}"
            )
        delivery_id = uuid4()
        today = command.now.date()
        grns = list(uow.receiving.all_grns())
        acts = list(uow.receiving.act_numbers_with_prefixes(act_number_prefix(kind, today) for kind in ACT_KINDS))
        results: list[ReceivingLine] = []
        placed = 0
        for current, item in zip(loaded, command.lines):
            try:
                inspected = _inspect_line(
                    current,
                    _delivery_inspection(current, item, command),
                    uow,
                    actor=command.actor,
                    now=command.now,
                    existing_grns=grns,
                    existing_act_numbers=acts,
                    manual_grn_prefix=item.manual_grn_prefix,
                    audit_details={"delivery_id": str(delivery_id)},
                )
                grns.append(inspected.grn)
                acts.extend(number for number, _ in (inspected.act(kind) for kind in ACT_KINDS) if number)
                line = inspected
                # A line held back (брак → «Ожидание», пересорт → «Возврат») is not placed: its place waits.
                if item.storage_location and inspected.status is ReceivingStatus.ACCEPTED:
                    line = _place_line(inspected, item.storage_location, uow, command.actor, command.now)
                    placed += 1
            except ReceivingValidationError as error:
                text = str(error)
                raise ReceivingValidationError(
                    text if text.startswith(current.legacy_id) else f"{current.legacy_id}: {text}"
                ) from error
            results.append(_emit_warehouse_if_ready(line, uow, command.actor, command.now))
        counts: dict[str, int] = {}
        for line in results:
            counts[line.status.value] = counts.get(line.status.value, 0) + 1
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=delivery_id,
                event_type="receiving.delivery_accepted",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "document_number": command.document_number,
                    "document_date": command.document_date.isoformat() if command.document_date else None,
                    "shipping_document_url": command.shipping_document_url,
                    "supplier": next(iter(suppliers.values())),
                    "warehouse": warehouses[0],
                    "lines": [
                        {
                            "id": str(line.id),
                            "legacy_id": line.legacy_id,
                            "status": line.status.value,
                            "grn": line.grn,
                            "accepted_quantity": str(line.accepted_quantity),
                        }
                        for line in results
                    ],
                    "counts": {
                        "lines": len(results),
                        "statuses": counts,
                        "placed": placed,
                        "sent_to_warehouse": sum(1 for line in results if line.warehouse_event_emitted),
                    },
                },
            )
        )
        uow.commit()
    return DeliveryResult(delivery_id=delivery_id, lines=tuple(results))


def quarantine_overdue_waiting(now: datetime, uow: UnitOfWork, *, limit: int = 200) -> list[ReceivingLine]:
    """OwnerTick.gs waitingToQuarantineSweep_: «Ожидание» без решения дольше срока → «Карантин».

    Ответственному по характеру — уведомление в портал (OP-1/RC-10); кому писали, пишется в аудит.
    """
    today: date = now.date()
    moved: list[ReceivingLine] = []
    with uow:
        cutoff = latest_overdue_inspection_date(today)
        for line in uow.receiving.waiting_inspected_by(cutoff, limit):
            if not line.waiting_overdue(today):
                continue
            quarantined = line.quarantine_overdue(today)
            uow.receiving.add(quarantined)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=line.id,
                    event_type="receiving.auto_quarantined",
                    actor=SYSTEM_ACTOR,
                    occurred_at=now,
                    details={
                        "legacy_id": line.legacy_id,
                        "from_status": line.status.value,
                        "to_status": quarantined.status.value,
                        "reason": f"нет решения {QUARANTINE_WORKDAYS}+ р.д.",
                        "waiting_since": line.inspected_on.isoformat() if line.inspected_on else None,
                        "nonconformities": list(line.nonconformities),
                        "notify": list(quarantine_notify_targets(line.nonconformities)),
                    },
                )
            )
            # OwnerTick.gs:573-581: the automatic move (no decision in time) notifies the same people.
            publish_notification(uow.outbox, notification_requested(
                key=f"receiving:{line.id}:auto_quarantine:v{quarantined.version}",
                now=now,
                title=f"Авто-Карантин (нет решения {QUARANTINE_WORKDAYS} р.д.) — {line.legacy_id}",
                body=(
                    f"{line.legacy_id} · {line.name or '—'} ({line.warehouse})"
                    + (f"\nВ «Ожидании» с {line.inspected_on.strftime('%d.%m.%Y')}" if line.inspected_on else "")
                    + (f"\nХарактер несоответствия: {', '.join(line.nonconformities)}" if line.nonconformities else "")
                    + f"\n\nРешение не принято за {QUARANTINE_WORKDAYS} раб. дн. — позиция автоматически "
                    "переведена в Карантин.\nПричина устранена → повторный «Входной контроль» по строке; "
                    "подтверждено → заполни РЕШЕНИЕ."
                ),
                kind="quarantine",
                reference=line.legacy_id,
                audiences=_notify_audiences(line),
            ))
            moved.append(quarantined)
        if moved:
            uow.commit()
    return moved


@dataclass(frozen=True)
class CopyExpectedArrivalCommand:
    """ОЖИД. ДАТА by procurement line id, as the buyer has it now."""

    dates: Mapping[UUID, date | None]
    actor: Actor
    now: datetime


def copy_expected_arrival(command: CopyExpectedArrivalCommand, uow: UnitOfWork) -> list[ReceivingLine]:
    """OwnerTick.gs syncExpectedDateToWaitlist_: the buyer's ОЖИД. ДАТА goes to every open delivery of the line —
    the line itself and its batches `-ПN`; accepted, returned and archived deliveries keep theirs."""
    if not command.dates:
        return []
    changed: list[ReceivingLine] = []
    with uow:
        for line in uow.receiving.open_by_procurement_lines(list(command.dates)):
            updated = line.follow_expected_arrival(command.dates.get(line.procurement_line_id))
            if updated is line:
                continue
            uow.receiving.add(updated)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=line.id,
                    event_type="receiving.expected_arrival_copied",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": line.legacy_id,
                        "expected_arrival": updated.expected_arrival.isoformat() if updated.expected_arrival else "",
                        "previous_expected_arrival": line.expected_arrival.isoformat() if line.expected_arrival else "",
                    },
                )
            )
            changed.append(updated)
        if changed:
            uow.commit()
    return changed


def receiving_queue(
    uow: UnitOfWork,
    *,
    limit: int = 200,
    after_id: UUID | None = None,
    preset: str | None = None,
    statuses: Sequence[str] = (),
) -> list[ReceivingLine]:
    """The receiver's work queue: open deliveries first (В пути, Ожидание, Карантин), then «Принято» not yet
    on stock, then the rest; «Архив ВК» only when a preset or a status asks for it."""
    if preset is not None and preset not in QUEUE_PRESETS:
        raise ReceivingValidationError("Неизвестный отбор очереди")
    try:
        wanted = tuple(ReceivingStatus(str(value).strip()) for value in statuses if str(value).strip())
    except ValueError as error:
        raise ReceivingValidationError("Неизвестный статус приёмки") from error
    if preset is not None:
        ranks = QUEUE_PRESETS[preset]
    else:
        ranks = QUEUE_PRESETS["all"] if wanted else QUEUE_DEFAULT
    with uow:
        if after_id is not None and uow.receiving.get(after_id) is None:
            raise KeyError(f"Receiving cursor not found: {after_id}")
        return list(uow.receiving.work_queue(limit, after_id, ranks=ranks, statuses=wanted))


def _display_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


# Печатный бланк (браузерная печать — см. app.shared.print_sheet, тот же механизм, что и FF-8
# fulfillment/application.py build_issue_print_sheet). Чтение, без транзакции сверх самой выборки.

def build_putaway_print_sheet(uow: UnitOfWork, now: datetime, *, limit: int = 500) -> PrintSheet:
    """WH-6: «Лист перемещения на склад» — строки «Принято» с ГРН, ещё не размещённые на складе
    (SUPPLYMAP Warehouse.gs:349-382 buildTransferSheet). Место на складе появляется у строки только
    через place_receiving, поэтому пустое storage_location и есть признак «ждёт раскладки»."""
    with uow:
        lines = list(uow.receiving.awaiting_placement(limit))
    if not lines:
        raise ReceivingValidationError(
            "Нет позиций для раскладки: нужны строки «Принято» с ГРН и пустым местом на складе."
        )
    columns = ("№", "Наименование", "ГРН", "Пакет", "Кол-во", "Ед.", "Место на складе")
    rows = tuple(
        (
            str(index),
            line.name,
            line.grn or "",
            line.package or "",
            _display_number(line.accepted_quantity) if line.accepted_quantity is not None else "",
            line.unit,
            "",
        )
        for index, line in enumerate(lines, start=1)
    )
    return PrintSheet(
        title="ЛИСТ ПЕРЕМЕЩЕНИЯ НА СКЛАД",
        subtitle=f"Позиций: {len(lines)} · сформирован {now.strftime('%d.%m.%Y %H:%M')}",
        columns=columns,
        rows=rows,
        sign="Разложил: ______________________        Дата: ______________",
    )


# RC-9: акты приёмки печатным бланком (DocGen.gs docTemplateSpec_: incoming / nonconformity / complaint /
# usepermit). Поля те же, что в шаблонах SUPPLYMAP; пустое поле — прочерк, как незаполненный плейсхолдер в DocGen.
_OK = "Соответствует"
_BAD = "Не соответствует"


def _dash(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return text or "—"


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def _qty(value: Decimal | None, unit: str) -> str:
    return f"{_display_number(value)} {unit}" if value is not None else "—"


def _incoming_results(line: ReceivingLine) -> tuple[tuple[str, str, str], ...]:
    """DocGen.gs icrResultMarks_: пункт «не соответствует» ровно тогда, когда его причина в характере НС."""
    nature = set(line.nonconformities)
    mark = lambda *items: _BAD if nature & set(items) else _OK  # noqa: E731
    quality = (
        mark("Отсутствие / несоответствие документа по качеству")
        if line.quality_document_required
        else "Не требуется"
    )
    return (
        ("1", "Внешний вид, целостность тары и упаковки", mark("Механическое повреждение / брак")),
        (
            "2",
            "УПД/М-15",
            SHIPPING_DOCUMENT_PENDING_NOTE
            if line.shipping_document_pending
            else mark("Отсутствие / несоответствие УПД/М-15"),
        ),
        ("3", "Идентификация (тот ли товар, соответствие счёту)", mark("Не тот товар (несоответствие счету)")),
        ("4", "Документ по качеству", quality),
        ("5", "Срок годности / дата изготовления", mark("Истёк срок годности")),
        ("6", "Количество УПД — соответствие счёту", mark("Недовоз (УПД < заказ)")),
        ("7", "Количество фактическое — соответствие УПД/М-15", mark("Недостача (факт < УПД)", "Излишек (факт > УПД)")),
    )


def build_act_print_sheet(line: ReceivingLine, kind: str) -> PrintSheet:
    if kind not in ACT_KINDS:
        raise KeyError(f"Неизвестный вид акта: {kind}")
    number, act_date = line.act(kind)
    title = ACT_KINDS[kind][1]
    if not number:
        raise ReceivingValidationError(f"{title} по строке {line.legacy_id} не выпускался")
    upd = f"{_dash(line.document_number)} от {_day(line.document_date)}"
    name = line.document_name or line.name
    basis = f"Акт входного контроля № {_dash(line.vk_act_number)} от {_day(line.vk_act_date)}"
    inspector = line.inspector.name if line.inspector else "______________"
    rows: list[tuple[str, str, str]] = [
        ("", "Заказ №", _dash(line.order_number)),
        ("", "Тип", _dash(line.item_type)),
        ("", "Наименование", _dash(name if kind != "incoming" else line.name)),
        ("", "ГРН", _dash(f"{line.grn} / {line.package}" if line.grn and line.package else line.grn)),
        ("", "Назначение", _dash(line.purpose)),
    ]
    if kind in {"incoming", "nonconformity"}:
        rows += [
            ("", "Дата изготовления", _day(line.manufacture_date)),
            ("", "Срок годности", _day(line.expiry_date)),
        ]
    if kind == "incoming":
        rows.append(("", "Кол-во по заказу", _qty(line.ordered_quantity, line.unit)))
    if kind == "usepermit":
        rows.append(("", "Кол-во", _qty(line.fact_quantity, line.unit)))
    else:
        rows += [
            ("", "Кол-во по УПД/М-15", _qty(line.document_quantity, line.unit)),
            ("", "Кол-во фактически", _qty(line.fact_quantity, line.unit)),
        ]
    rows.append(("", "Поставщик", _dash(line.supplier)))
    if kind == "incoming":
        rows += [
            ("", "Производитель", _dash(line.manufacturer)),
            ("", "Страна", _dash(line.country)),
            ("", "УПД/М-15 №", upd),
            ("", "№ партии", _dash(line.series)),
            ("", "Дата приезда", _day(line.inspected_on)),
            ("", "Тип документа по качеству", _dash(line.quality_document_type)),
            ("", "№ документа по качеству", _dash(line.quality_document_number)),
            ("", "Дата документа по качеству", _day(line.quality_document_date)),
            ("", "Стандарт", _dash(line.standard)),
            ("", "Документ по качеству", _dash(line.quality_document_url)),
        ]
        rows += list(_incoming_results(line))
        rows += [
            ("", "Примечание", _dash(line.inspection_note)),
            (
                "",
                "Заключение",
                "Продукция по результатам входного контроля признана: "
                + ("годной к применению" if line.verdict == "Годен" else "не соответствующей требованиям")
                + f". Результат: {_dash(line.verdict)}.",
            ),
        ]
        sign = f"ВХОДНОЙ КОНТРОЛЬ ПРОВЁЛ: {inspector}        ПРЕДСТАВИТЕЛЬ ОТК: ______________"
    elif kind == "usepermit":
        rows += [
            ("", "№ партии", _dash(line.series)),
            ("", "Основание", basis),
            (
                "",
                "Основание вывода из карантина",
                "Сопроводительная документация (УПД/М-15, документ по качеству) предоставлена в полном объёме. "
                "Продукция соответствует требованиям и разрешена к использованию.",
            ),
        ]
        sign = f"РАЗРЕШИЛ (входной контроль): {inspector}        ПРЕДСТАВИТЕЛЬ ОТК: ______________"
    elif kind == "complaint":
        claimed = [item for item in line.nonconformities if item in QUANTITY_FAULTS | QUALITY_FAULTS]
        rows += [
            ("", "УПД/М-15 №", upd),
            ("", "№ партии", _dash(line.series)),
            ("", "Основание", basis),
            ("", "Существо претензии (вина поставщика)", _dash(", ".join(claimed))),
            ("", "Описание несоответствия", _dash(line.nonconformity_description)),
            (
                "",
                "Требование к поставщику",
                "☐ Замена несоответствующей продукции  ☐ Допоставка недостающего количества  "
                "☐ Возврат продукции и денежных средств  ☐ Возмещение убытков",
            ),
        ]
        sign = f"ПРЕТЕНЗИЮ ВЫСТАВИЛ: {inspector}        ПРЕДСТАВИТЕЛЬ ПОСТАВЩИКА: ______________"
    else:
        rows += [
            ("", "УПД/М-15 №", upd),
            ("", "№ партии", _dash(line.series)),
            ("", "Основание", basis),
            ("", "Характер несоответствия", _dash(", ".join(line.nonconformities))),
            ("", "Описание несоответствия", _dash(line.nonconformity_description)),
            ("", "Классификация", "☐ Критическое  ☐ Значительное  ☐ Малозначительное"),
            ("", "Решение по продукции", _dash(line.disposition)),
        ]
        sign = f"НЕСООТВЕТСТВИЕ ВЫЯВИЛ: {inspector}        ПРЕДСТАВИТЕЛЬ ЗАКАЗЧИКА (ВП): ______________"
    return PrintSheet(
        title=f"{title.upper()} № {number}",
        subtitle=f"Дата {_day(act_date)} · строка {line.legacy_id}",
        columns=("№", "Показатель", "Значение"),
        rows=tuple(rows),
        sign=sign,
    )


def receiving_act_sheet(line_id: UUID, kind: str, uow: UnitOfWork) -> PrintSheet:
    with uow:
        line = uow.receiving.get(line_id)
    if line is None:
        raise KeyError(f"Receiving line not found: {line_id}")
    return build_act_print_sheet(line, kind)

