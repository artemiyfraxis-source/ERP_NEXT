"""Transactional production-kit reservation and handout use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.modules.warehouse.domain import (
    ISSUE_WRITEOFF_AUTHOR,
    StockLot,
    WriteOffEntry,
    issue_writeoff_reason,
)
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import (
    FulfillmentLine,
    FulfillmentRequest,
    FulfillmentValidationError,
    LotSnapshot,
)


class ConcurrentFulfillmentUpdate(RuntimeError):
    pass


class FulfillmentRepository(Protocol):
    def get(self, request_id: UUID) -> FulfillmentRequest | None: ...
    def by_idempotency_key(self, key: str) -> FulfillmentRequest | None: ...
    def numbers(self, year: int) -> Sequence[str]: ...
    def list(self, limit: int = 200, after_id: UUID | None = None) -> Sequence[FulfillmentRequest]: ...
    def add(self, request: FulfillmentRequest) -> None: ...


class WarehouseRepository(Protocol):
    def get(self, lot_id: UUID) -> StockLot | None: ...
    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLot]: ...
    def add(self, lot: StockLot) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    fulfillment: FulfillmentRepository
    warehouse: WarehouseRepository
    writeoffs: AppendOnlyRepository  # the warehouse СПИСАНИЕ journal
    audit: AppendOnlyRepository

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class CreateFulfillmentCommand:
    idempotency_key: str
    purpose: str
    deadline: date | None
    lines: tuple[FulfillmentLine, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class VersionedFulfillmentCommand:
    request_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class BindLotCommand(VersionedFulfillmentCommand):
    line_id: UUID
    lot_id: UUID


@dataclass(frozen=True)
class UnbindLotCommand(VersionedFulfillmentCommand):
    line_id: UUID


@dataclass(frozen=True)
class MarkReadyCommand(VersionedFulfillmentCommand):
    handout_location: str


@dataclass(frozen=True)
class MarkReturnedCommand(VersionedFulfillmentCommand):
    return_location: str


@dataclass(frozen=True)
class ReturnDisposition:
    line_id: UUID
    returned_good: Decimal
    defective_returned: Decimal
    defective_missing: Decimal
    storage_location: str | None = None


@dataclass(frozen=True)
class CompleteReturnCommand(VersionedFulfillmentCommand):
    lines: tuple[ReturnDisposition, ...]


def next_request_number(year: int, existing: Sequence[str]) -> str:
    prefix = f"REQ.{year % 100:02d}."
    used = [
        int(value[len(prefix) :])
        for value in existing
        if value.startswith(prefix) and value[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(used, default=0) + 1:07d}"


def _creation_fingerprint(command: CreateFulfillmentCommand) -> str:
    payload = {
        "purpose": str(command.purpose).strip(),
        "deadline": command.deadline.isoformat() if command.deadline else None,
        "lines": [
            {
                "article": line.article,
                "name": line.name,
                "unit": line.unit,
                "requirement": str(line.requirement),
            }
            for line in command.lines
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _versioned(
    request_id: UUID,
    expected_version: int,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    request = uow.fulfillment.get(request_id)
    if request is None:
        raise KeyError(f"Fulfillment request not found: {request_id}")
    if request.version != expected_version:
        raise ConcurrentFulfillmentUpdate("Заявка изменилась после загрузки формы")
    return request


def _audit(
    request: FulfillmentRequest,
    event_type: str,
    actor: Actor,
    now: datetime,
    details: dict,
    uow: UnitOfWork,
) -> None:
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=request.id,
            event_type=event_type,
            actor=actor,
            occurred_at=now,
            details=details,
        )
    )


def create_fulfillment(
    command: CreateFulfillmentCommand,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    with uow:
        key = str(command.idempotency_key or "").strip()
        if not key:
            raise FulfillmentValidationError("Idempotency key обязателен")
        fingerprint = _creation_fingerprint(command)
        existing = uow.fulfillment.by_idempotency_key(key)
        if existing is not None:
            if existing.source_fingerprint != fingerprint:
                raise FulfillmentValidationError(
                    "Idempotency key уже использован для другой заявки"
                )
            return existing
        request = FulfillmentRequest.create(
            number=next_request_number(command.now.year, uow.fulfillment.numbers(command.now.year)),
            source_idempotency_key=key,
            source_fingerprint=fingerprint,
            requester=command.actor.name,
            purpose=command.purpose,
            deadline=command.deadline,
            lines=command.lines,
            created_at=command.now,
        )
        uow.fulfillment.add(request)
        _audit(request, "fulfillment.created", command.actor, command.now, {"number": request.number}, uow)
        uow.commit()
        return request


def bind_lot(command: BindLotCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        line = next((item for item in request.lines if item.id == command.line_id), None)
        if line is None:
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lot = uow.warehouse.get(command.lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {command.lot_id}")
        if line.unit.strip().casefold() != lot.unit.strip().casefold():
            raise FulfillmentValidationError(
                f"Единица партии {lot.unit} не совпадает с потребностью {line.unit}"
            )
        quantity = min(line.requirement, lot.available)
        if quantity <= 0:
            raise FulfillmentValidationError("У выбранной партии нет доступного остатка")
        snapshot = LotSnapshot(
            lot_id=lot.id,
            warehouse=lot.warehouse,
            grn=lot.grn,
            package=lot.package,
            storage_location=lot.storage_location,
        )
        updated = request.bind_line(line.id, snapshot, quantity)
        uow.warehouse.add(lot.reserve(quantity))
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.lot_bound", command.actor, command.now, {"line_id": str(line.id), "lot_id": str(lot.id), "quantity": str(quantity)}, uow)
        uow.commit()
        return updated


def unbind_lot(command: UnbindLotCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        line = next((item for item in request.lines if item.id == command.line_id), None)
        if line is None or line.lot is None or line.reserved <= 0:
            raise FulfillmentValidationError("У позиции нет резерва для снятия")
        lot = uow.warehouse.get(line.lot.lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
        updated = request.unbind_line(line.id)
        uow.warehouse.add(lot.release_reserve(line.reserved))
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.lot_unbound", command.actor, command.now, {"line_id": str(line.id), "lot_id": str(lot.id), "quantity": str(line.reserved)}, uow)
        uow.commit()
        return updated


def mark_ready(command: MarkReadyCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.mark_ready(command.handout_location)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.ready", command.actor, command.now, {"handout_location": updated.handout_location}, uow)
        uow.commit()
        return updated


def issue_fulfillment(
    command: VersionedFulfillmentCommand,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.mark_issued()
        lots: dict[UUID, StockLot] = {}
        for line in request.lines:
            if line.lot is None:
                raise FulfillmentValidationError("У позиции не выбрана партия")
            lot = lots.get(line.lot.lot_id) or uow.warehouse.get(line.lot.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
            lots[lot.id] = lot.issue_reserved(line.reserved)
            uow.warehouse.add(lots[lot.id])
        # Issue.gs:475-502: one СПИСАНИЕ row per issued position, ОСТАТОК ПОСЛЕ = the lot balance once the
        # whole request is posted (two positions on one lot both show the final balance).
        for line in request.lines:
            if line.reserved <= 0:
                continue
            lot = lots[line.lot.lot_id]
            uow.writeoffs.add(WriteOffEntry.for_lot(
                lot,
                kind="issue",
                quantity=line.reserved,
                name=line.name,
                reason=issue_writeoff_reason(request.number),
                purpose="",
                author=ISSUE_WRITEOFF_AUTHOR,
                actor_id=command.actor.id,
                actor_name=command.actor.name,
                occurred_at=command.now,
                remaining_after=lot.balance,
                source_key=f"fulfillment-issue:{line.id}",
            ))
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.issued", command.actor, command.now, {"lines": len(updated.lines)}, uow)
        uow.commit()
        return updated


def mark_returned(command: MarkReturnedCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.mark_returned(command.return_location)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.returned", command.actor, command.now, {
            "return_location": updated.return_location,
        }, uow)
        uow.commit()
        return updated


def complete_return(command: CompleteReturnCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        if len({line.line_id for line in command.lines}) != len(command.lines):
            raise FulfillmentValidationError("Позиция возврата указана дважды")
        by_id = {line.line_id: line for line in command.lines}
        updated = request.complete_return({
            line.line_id: (
                line.returned_good,
                line.defective_returned,
                line.defective_missing,
            )
            for line in command.lines
        })
        original_by_id = {line.id: line for line in request.lines}
        for disposition in command.lines:
            original = original_by_id[disposition.line_id]
            if original.lot is None:
                raise FulfillmentValidationError("У позиции не сохранена выданная партия")
            if disposition.returned_good <= 0 and not str(disposition.storage_location or "").strip():
                continue
            lot = uow.warehouse.get(original.lot.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {original.lot.lot_id}")
            if disposition.returned_good > 0:
                lot = lot.return_writeoff(disposition.returned_good)
            if str(disposition.storage_location or "").strip():
                lot = lot.move(str(disposition.storage_location))
            uow.warehouse.add(lot)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.return_completed", command.actor, command.now, {
            "returned_good": str(sum((line.returned_good for line in command.lines), Decimal("0"))),
            "defective_returned": str(sum((line.defective_returned for line in command.lines), Decimal("0"))),
            "defective_missing": str(sum((line.defective_missing for line in command.lines), Decimal("0"))),
        }, uow)
        uow.commit()
        return updated


def cancel_fulfillment(
    command: VersionedFulfillmentCommand,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.cancel()
        if updated == request:
            return request
        for line in request.lines:
            if line.lot is None or line.reserved <= 0:
                continue
            lot = uow.warehouse.get(line.lot.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
            uow.warehouse.add(lot.release_reserve(line.reserved))
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.cancelled", command.actor, command.now, {}, uow)
        uow.commit()
        return updated
