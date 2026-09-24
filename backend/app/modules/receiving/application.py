"""Transactional Receiving use cases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor

from .domain import InspectionInput, ReceivingLine, ReceivingValidationError, create_line_from_expected


SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


class ReceivingRepository(Protocol):
    def by_source_idempotency_key(self, key: str) -> ReceivingLine | None: ...
    def get(self, line_id: UUID) -> ReceivingLine | None: ...
    def queue(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
    ) -> Sequence[ReceivingLine]: ...
    def all_grns(self) -> Sequence[str]: ...
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
        result = _emit_warehouse_if_ready(inspected, uow, command.actor, command.now)
        uow.commit()
        return result


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
