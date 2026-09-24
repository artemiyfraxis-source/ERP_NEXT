"""Transactional Procurement use cases expressed against persistence ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor

from .domain import (
    ProcurementFacts,
    ProcurementLine,
    ProcurementValidationError,
    create_line_from_requested,
)


SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


class ProcurementRepository(Protocol):
    def by_source_idempotency_key(self, key: str) -> ProcurementLine | None: ...
    def get(self, line_id: UUID) -> ProcurementLine | None: ...
    def available(
        self, limit: int, after_id: UUID | None = None
    ) -> Sequence[ProcurementLine]: ...
    def by_buyer(
        self, actor: Actor, limit: int, after_id: UUID | None = None
    ) -> Sequence[ProcurementLine]: ...
    def list(self, limit: int = 10_000) -> Sequence[ProcurementLine]: ...
    def add(self, line: ProcurementLine) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item: AuditEvent) -> None: ...


class ConcurrentProcurementUpdate(RuntimeError):
    pass


class DispatchOutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class UnitOfWork(Protocol):
    procurement: ProcurementRepository
    audit: AppendOnlyRepository
    outbox: AppendOnlyRepository

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


class DispatchUnitOfWork(UnitOfWork, Protocol):
    outbox: DispatchOutboxRepository


class PoisonOutboxMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


@dataclass(frozen=True)
class TakeProcurementInWorkCommand:
    line_ids: Sequence[UUID]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SaveProcurementFactsCommand:
    line_id: UUID
    facts: ProcurementFacts
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SendSupplierRequestCommand:
    line_id: UUID
    expected_version: int
    reference: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RecordInvoiceCommand:
    line_id: UUID
    expected_version: int
    invoice_url: str
    expected_arrival: date
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class MarkProcurementPaidCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


def consume_procurement_requested(
    message: OutboxMessage,
    uow: UnitOfWork,
) -> ProcurementLine:
    with uow:
        existing = uow.procurement.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                raise ProcurementValidationError(
                    "Ключ события закупки уже использован другим событием"
                )
            return existing

        line = create_line_from_requested(message)
        uow.procurement.add(line)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=line.id,
                event_type="procurement.created",
                actor=SYSTEM_ACTOR,
                occurred_at=message.created_at,
                details={
                    "legacy_id": line.legacy_id,
                    "source_event_id": str(message.id),
                },
            )
        )
        uow.commit()
        return line


def dispatch_next_procurement_requested(
    now: datetime,
    uow: DispatchUnitOfWork,
) -> ProcurementLine | None:
    with uow:
        message = uow.outbox.next_unpublished("procurement.requested", now)
        if message is None:
            return None
        existing = uow.procurement.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                reason = "Ключ события закупки уже использован другим событием"
                uow.outbox.mark_failed(message.id, now, reason)
                uow.commit()
                raise PoisonOutboxMessage(message.id, reason)
            line = existing
        else:
            try:
                line = create_line_from_requested(message)
            except ProcurementValidationError as error:
                uow.outbox.mark_failed(message.id, now, str(error))
                uow.commit()
                raise PoisonOutboxMessage(message.id, str(error)) from error
            uow.procurement.add(line)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=line.id,
                    event_type="procurement.created",
                    actor=SYSTEM_ACTOR,
                    occurred_at=message.created_at,
                    details={
                        "legacy_id": line.legacy_id,
                        "source_event_id": str(message.id),
                    },
                )
            )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return line


def take_procurement_in_work(
    command: TakeProcurementInWorkCommand,
    uow: UnitOfWork,
) -> list[ProcurementLine]:
    line_ids = list(dict.fromkeys(command.line_ids))
    if not line_ids:
        raise ProcurementValidationError("Не выбрано ни одной строки закупки")

    with uow:
        current: list[ProcurementLine] = []
        for line_id in line_ids:
            line = uow.procurement.get(line_id)
            if line is None:
                raise KeyError(f"Procurement line not found: {line_id}")
            current.append(line)

        updated = [line.take_in_work(command.actor) for line in current]
        changed = [
            (before, after)
            for before, after in zip(current, updated, strict=True)
            if before != after
        ]
        if not changed:
            return updated

        for before, after in changed:
            uow.procurement.add(after)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=after.id,
                    event_type="procurement.taken_in_work",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": after.legacy_id,
                        "from_status": before.status.value,
                        "to_status": after.status.value,
                    },
                )
            )
        uow.commit()
        return updated


def save_procurement_facts(
    command: SaveProcurementFactsCommand,
    uow: UnitOfWork,
) -> ProcurementLine:
    with uow:
        current = uow.procurement.get(command.line_id)
        if current is None:
            raise KeyError(f"Procurement line not found: {command.line_id}")
        if current.version != command.expected_version:
            try:
                retry_result = current.update_facts(command.facts, command.actor)
            except ProcurementValidationError as error:
                raise ConcurrentProcurementUpdate(
                    "Строка изменилась после загрузки формы"
                ) from error
            if retry_result == current:
                return current
            raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы")
        updated = current.update_facts(command.facts, command.actor)
        if updated == current:
            return current

        uow.procurement.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=updated.id,
                event_type="procurement.facts_saved",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "legacy_id": updated.legacy_id,
                    "from_status": current.status.value,
                    "to_status": updated.status.value,
                },
            )
        )
        uow.commit()
        return updated


def _load_versioned(line_id: UUID, expected_version: int, uow: UnitOfWork) -> ProcurementLine:
    current = uow.procurement.get(line_id)
    if current is None:
        raise KeyError(f"Procurement line not found: {line_id}")
    if current.version != expected_version:
        raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы")
    return current


def _save_transition(
    current: ProcurementLine,
    updated: ProcurementLine,
    *,
    event_type: str,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
) -> ProcurementLine:
    if updated == current:
        return current
    uow.procurement.add(updated)
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=updated.id,
            event_type=event_type,
            actor=actor,
            occurred_at=now,
            details={
                "legacy_id": updated.legacy_id,
                "from_status": current.status.value,
                "to_status": updated.status.value,
            },
        )
    )
    return updated


def send_supplier_request(command: SendSupplierRequestCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.send_supplier_request(command.reference, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.supplier_request_sent",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        if result != current:
            uow.commit()
        return result


def record_invoice(command: RecordInvoiceCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.record_invoice(
            command.invoice_url,
            command.expected_arrival,
            command.actor,
        )
        result = _save_transition(
            current,
            updated,
            event_type="procurement.invoice_recorded",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        uow.commit()
        return result


_WAREHOUSE_BY_POINT = {
    "г. москва, ул. 6-я радиальная, д. 17": "ЦАР",
    "г. раменское, ул. 100-й свирской дивизии, д. 52": "ЖУК",
}


def warehouse_for_delivery_point(value: str) -> str:
    normalized = " ".join(str(value or "").casefold().split())
    warehouse = _WAREHOUSE_BY_POINT.get(normalized)
    if warehouse is None:
        raise ProcurementValidationError("Для точки отгрузки не настроен склад приёмки")
    return warehouse


def mark_procurement_paid(command: MarkProcurementPaidCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.mark_paid(command.actor, command.now)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.paid",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        if result != current:
            uow.outbox.add(
                OutboxMessage(
                    id=uuid4(),
                    topic="receiving.expected",
                    idempotency_key=f"procurement:{updated.id}:receiving",
                    created_at=command.now,
                    payload={
                        "procurement_line_id": str(updated.id),
                        "legacy_id": updated.legacy_id,
                        "warehouse": warehouse_for_delivery_point(updated.delivery_point),
                        "article": updated.design_article or updated.calculated_name,
                        "name": updated.calculated_name,
                        "item_type": updated.item_type,
                        "purpose": updated.designation,
                        "goal": updated.project_reference,
                        "source_link": updated.link,
                        "supplier": updated.supplier,
                        "ordered_quantity": str(updated.actual_quantity),
                        "unit": updated.actual_unit,
                        "quality_document_required": updated.quality_document == "Да",
                    },
                )
            )
            uow.commit()
        return result


def available_procurement(
    uow: UnitOfWork, *, limit: int = 100, after_id: UUID | None = None
) -> list[ProcurementLine]:
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        return list(uow.procurement.available(limit, after_id))


def buyer_procurement(
    actor: Actor,
    uow: UnitOfWork,
    *,
    limit: int = 100,
    after_id: UUID | None = None,
) -> list[ProcurementLine]:
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        return list(uow.procurement.by_buyer(actor, limit, after_id))
