"""Transactional Receiving use cases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.print_sheet import PrintSheet

from .domain import (
    InspectionInput,
    ReceivingLine,
    ReceivingStatus,
    QUARANTINE_WORKDAYS,
    ReceivingValidationError,
    create_line_from_expected,
    latest_overdue_inspection_date,
    quarantine_notify_targets,
)


SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


class ReceivingRepository(Protocol):
    def by_source_idempotency_key(self, key: str) -> ReceivingLine | None: ...
    def get(self, line_id: UUID) -> ReceivingLine | None: ...
    def queue(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
    ) -> Sequence[ReceivingLine]: ...
    def waiting_inspected_by(self, day: date, limit: int = 200) -> Sequence[ReceivingLine]: ...
    def awaiting_placement(self, limit: int = 500) -> Sequence[ReceivingLine]: ...
    def all_grns(self) -> Sequence[str]: ...
    def legacy_ids_starting_with(self, prefix: str) -> Sequence[str]: ...
    def add(self, line: ReceivingLine) -> None: ...


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
        "legacy_id": line.legacy_id,
        "warehouse": line.warehouse,
        "grn": line.grn,
        "package": line.package,
        "article": line.article,
        "series": line.series,
        "name": line.name,
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
    key = f"receiving:{line.id}:warehouse"
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
        inspected = current.inspect(
            inspection,
            command.actor,
            existing_grns=uow.receiving.all_grns(),
            manual_grn_prefix=command.manual_grn_prefix,
        )
        uow.receiving.add(inspected)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=inspected.id,
                event_type="receiving.inspected",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "legacy_id": inspected.legacy_id,
                    "from_status": current.status.value,
                    "to_status": inspected.status.value,
                    "verdict": inspected.verdict,
                },
            )
        )
        if current.status is ReceivingStatus.IN_TRANSIT:
            _request_backorder(inspected, uow, command.now)
        result = _emit_warehouse_if_ready(inspected, uow, command.actor, command.now)
        uow.commit()
        return result


def _request_backorder(line: ReceivingLine, uow: UnitOfWork, now: datetime) -> None:
    # Receiving.gs:761 — only the first inspection (no act yet) records a backorder for УПД < заказ;
    # re-inspections of «Ожидание»/«Карантин» never do. Procurement turns it into the paid `-Д` line.
    if line.backorder_quantity <= 0:
        return
    # A `-ВN` revision re-inspects the same physical delivery: one backorder per lineage, never twice.
    key = f"receiving:{line.lineage_root_id}:backorder"
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


def place_receiving(command: PlaceReceivingCommand, uow: UnitOfWork) -> ReceivingLine:
    with uow:
        current = uow.receiving.get(command.line_id)
        if current is None:
            raise KeyError(f"Receiving line not found: {command.line_id}")
        if current.version != command.expected_version:
            raise ConcurrentReceivingUpdate("Строка приёмки изменилась после загрузки формы")
        placed = current.place(command.storage_location)
        if placed != current:
            uow.receiving.add(placed)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=placed.id,
                    event_type="receiving.placed",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={"grn": placed.grn, "storage_location": placed.storage_location},
                )
            )
        result = _emit_warehouse_if_ready(placed, uow, command.actor, command.now)
        if result == current:
            return result
        uow.commit()
        return result


def quarantine_overdue_waiting(now: datetime, uow: UnitOfWork, *, limit: int = 200) -> list[ReceivingLine]:
    """OwnerTick.gs waitingToQuarantineSweep_: «Ожидание» без решения дольше срока → «Карантин».

    Письмо ответственному — отдельный шаг (уведомления, OP-1/RC-10); кому писать, пишется в аудит.
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
            moved.append(quarantined)
        if moved:
            uow.commit()
    return moved


def receiving_queue(
    uow: UnitOfWork,
    *,
    limit: int = 200,
    after_id: UUID | None = None,
) -> list[ReceivingLine]:
    with uow:
        if after_id is not None and uow.receiving.get(after_id) is None:
            raise KeyError(f"Receiving cursor not found: {after_id}")
        return list(uow.receiving.queue(limit, after_id))


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
