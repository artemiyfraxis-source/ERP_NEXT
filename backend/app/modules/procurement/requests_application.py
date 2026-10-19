"""Commands of «Заявки» закупщика (option procurement.requests_rb): «Разделить» by quantity and «Отменить выбранные».

Both are ordinary procurement commands — the line's own buyer, the version the screen read, audit in the same
transaction. The request-for-quotes half of a split goes through the `RfqLineSplitter` port (`app/workflows`), on the
same unit of work, so the two halves commit together or not at all."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import StockVersionConflict

from .application import (
    ConcurrentProcurementUpdate,
    StockCoverUnitOfWork,
    UnitOfWork,
    _load_versioned,
    _moscow_day,
    _release_covers,
    _save_transition,
)
from .domain import (
    ProcurementAccessDenied,
    ProcurementLine,
    ProcurementStatus,
    ProcurementValidationError,
    cancel_reason,
    split_by_quantity,
)

SPLIT_EVENT = "procurement.split_quantity"
CANCEL_MANY_LIMIT = 500


class RfqLineSplitter(Protocol):
    """The request-for-quotes side of «Разделить» (purchasing, via `app/workflows`)."""

    def live_request(self, uow: UnitOfWork, line_id: UUID) -> str | None: ...

    def split(
        self, uow: UnitOfWork, *, line_id: UUID, new_line_id: UUID, kept: Decimal, split: Decimal, actor: Actor,
        now: datetime,
    ) -> dict | None: ...


@dataclass(frozen=True)
class SplitByQuantityCommand:
    line_id: UUID
    expected_version: int
    quantity: Decimal
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SplitByQuantityResult:
    original: ProcurementLine
    created: ProcurementLine
    rfq: dict | None  # {rfq_id, rfq_number, quotes} when the line was in a live request for quotes


def split_procurement_line_by_quantity(
    command: SplitByQuantityCommand, uow: UnitOfWork, rfq: RfqLineSplitter | None,
) -> SplitByQuantityResult:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        in_request = rfq.live_request(uow, current.id) if rfq is not None else None
        day = _moscow_day(command.now)
        original, created = split_by_quantity(
            current, command.quantity, uow.procurement.legacy_ids_with_prefix(f"SM-{day:%Y%m%d}-"), day, command.now,
            command.actor, keep_supplier=in_request is None,
        )
        copied = None
        if in_request is not None and rfq is not None:
            copied = rfq.split(
                uow, line_id=current.id, new_line_id=created.id, kept=original.to_buy, split=created.to_buy,
                actor=command.actor, now=command.now,
            )
        _save_transition(
            current, original, event_type=SPLIT_EVENT, actor=command.actor, now=command.now, uow=uow,
            details={
                "quantity": format(created.calculated_quantity.normalize(), "f"),
                "kept": format(original.calculated_quantity.normalize(), "f"),
                "created_legacy_id": created.legacy_id,
                **({"rfq_number": copied["rfq_number"]} if copied else {}),
            },
        )
        uow.procurement.add(created)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=created.id, event_type="procurement.created", actor=command.actor,
            occurred_at=command.now,
            details={
                "legacy_id": created.legacy_id, "split_from": current.legacy_id,
                "quantity": format(created.calculated_quantity.normalize(), "f"),
                "source_request_id": str(created.source_request_id),
            },
        ))
        uow.commit()
        return SplitByQuantityResult(original=original, created=created, rfq=copied)


@dataclass(frozen=True)
class CancelManyItem:
    line_id: UUID
    version: int


@dataclass(frozen=True)
class CancelManyCommand:
    items: tuple[CancelManyItem, ...]
    reason: str
    actor: Actor
    now: datetime


@dataclass
class CancelManyResult:
    cancelled: list[ProcurementLine] = field(default_factory=list)
    skipped: list[tuple[UUID, str]] = field(default_factory=list)


SKIP_MISSING = "строка не найдена"
SKIP_STALE = "строка изменилась — обновите список"
SKIP_FOREIGN = "строка другого закупщика"
SKIP_ALREADY = "уже отменена"


def _cancel_one(item: CancelManyItem, reason: str, actor: Actor, now: datetime, uow: StockCoverUnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(item.line_id, item.version, uow)
        if current.status is ProcurementStatus.CANCELLED:
            raise ProcurementValidationError(SKIP_ALREADY)
        updated = current.cancel(actor)
        if current.stock_covered > 0:
            # A cancelled line needs nothing: its «Со склада» reserve goes back to the lots (as `/cancel`).
            covers = [cover for cover in uow.stock_covers.for_line(current.id) if cover.active]
            released, _, _ = _release_covers(current, covers, actor, now, uow)
            updated = replace(updated, stock_covered=updated.stock_covered - released)
        result = _save_transition(
            current, updated, event_type="procurement.cancelled", actor=actor, now=now, uow=uow,
            details={"reason": reason},
        )
        uow.commit()
        return result


def cancel_procurement_lines(
    command: CancelManyCommand, uow_factory: Callable[[], StockCoverUnitOfWork],
) -> CancelManyResult:
    """RB «Отменить выбранные»: every line on its own (one transaction each), so a paid, foreign or stale line is
    skipped with its reason and never stops the rest. The reason is required and kept in `procurement.cancelled`."""
    reason = cancel_reason(command.reason)
    items = list({item.line_id: item for item in command.items}.values())
    if not items:
        raise ProcurementValidationError("Не выбрано ни одной строки")
    if len(items) > CANCEL_MANY_LIMIT:
        raise ProcurementValidationError(f"За раз — не больше {CANCEL_MANY_LIMIT} строк")
    result = CancelManyResult()
    for item in items:
        try:
            result.cancelled.append(_cancel_one(item, reason, command.actor, command.now, uow_factory()))
        except KeyError:
            result.skipped.append((item.line_id, SKIP_MISSING))
        except (ConcurrentProcurementUpdate, StaleDataError, StockVersionConflict, IntegrityError):
            result.skipped.append((item.line_id, SKIP_STALE))
        except ProcurementAccessDenied:
            result.skipped.append((item.line_id, SKIP_FOREIGN))
        except ProcurementValidationError as error:
            result.skipped.append((item.line_id, str(error)))
    return result

