"""An arrival binds itself to the project's waiting kit (owner 05.10.2026).

When receiving hands a delivery over, Warehouse creates or tops up the lot and emits `fulfillment.lot_arrived`. This
consumer finds what the delivery was bought for through Receiving's and Procurement's own rows (read-only port, the
same shape as `receiving_stock.py`; neither module is imported into Fulfillment), and if it was bought for a project,
Fulfillment binds the lot to that project's waiting kit lines (`bind_arrived_lot`: КБ in natural order, never more than
arrived, never another project's kit or reserve). The storekeepers hear once what came and whether the kit is whole.
Stock not bought for a project is left to the storekeeper, as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Protocol
from uuid import UUID

from app.modules.fulfillment.application import (
    ArrivedKit,
    ArrivedLotResult,
    BindArrivedLotCommand,
    UnitOfWork as FulfillmentUnitOfWork,
    bind_arrived_lot,
)
from app.modules.fulfillment.domain import FulfillmentValidationError
from app.modules.procurement.application import UnitOfWork as ProcurementUnitOfWork
from app.modules.receiving.application import UnitOfWork as ReceivingUnitOfWork
from app.modules.warehouse.application import LOT_ARRIVED_TOPIC
from app.modules.warehouse.domain import WarehouseValidationError
from app.shared.events import OutboxMessage
from app.shared.identity import Actor
from app.shared.notifications import STOREKEEPER_AUDIENCE, notification_requested, publish_notification

ARRIVAL_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


@dataclass(frozen=True)
class ArrivalSource:
    """What a delivery was bought for: the project and the item (КД article and names of the purchase)."""

    project: str
    articles: tuple[str, ...]
    names: tuple[str, ...]


class ArrivalSourceLookup(Protocol):
    def source_of(self, receiving_line_id: UUID) -> ArrivalSource | None: ...


class ReceivingArrivalLookup:
    """Receiving line → its procurement line → the project (ПРОЕКТ of the purchase; the receiving line's ЦЕЛЬ, a
    snapshot of it, when the purchase is gone). Read-only."""

    def __init__(
        self,
        receiving_uow_factory: Callable[[], ReceivingUnitOfWork],
        procurement_uow_factory: Callable[[], ProcurementUnitOfWork],
    ):
        self.receiving_uow_factory = receiving_uow_factory
        self.procurement_uow_factory = procurement_uow_factory

    def source_of(self, receiving_line_id: UUID) -> ArrivalSource | None:
        with self.receiving_uow_factory() as uow:
            line = uow.receiving.get(receiving_line_id)
        if line is None:
            return None
        with self.procurement_uow_factory() as uow:
            purchase = uow.procurement.get(line.procurement_line_id)
        project = str((purchase.project_reference if purchase is not None else None) or line.goal or "").strip()
        articles = (purchase.design_article if purchase is not None else None, line.article)
        names = (purchase.calculated_name if purchase is not None else None, line.name, line.document_name)
        return ArrivalSource(
            project=project,
            articles=tuple(str(value).strip() for value in articles if str(value or "").strip()),
            names=tuple(str(value).strip() for value in names if str(value or "").strip()),
        )


class ArrivalOutbox(Protocol):
    def add(self, message: OutboxMessage) -> None: ...
    def by_idempotency_key(self, idempotency_key: str) -> OutboxMessage | None: ...
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class ArrivalUnitOfWork(FulfillmentUnitOfWork, Protocol):
    outbox: ArrivalOutbox


class PoisonArrivalMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


@dataclass(frozen=True)
class Arrival:
    lot_id: UUID
    receipt_id: str
    receiving_line_id: UUID | None
    quantity: Decimal
    grn: str
    package: str


@dataclass(frozen=True)
class ArrivalOutcome:
    message_id: UUID
    project: str
    result: ArrivedLotResult
    notified: bool


_NOTHING = ArrivedLotResult(kits=(), bound=(), short=Decimal("0"))


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value or "").strip())
    except ValueError:
        return None


def _arrival(payload: dict) -> Arrival:
    lot_id = _uuid(payload.get("lot_id"))
    if lot_id is None:
        raise ValueError("Invalid fulfillment.lot_arrived payload: lot_id")
    receipt_id = str(payload.get("receipt_id") or "").strip()
    if not receipt_id:
        raise ValueError("Invalid fulfillment.lot_arrived payload: receipt_id")
    try:
        quantity = Decimal(str(payload.get("quantity")))
    except (DecimalException, ValueError) as error:
        raise ValueError(f"Invalid fulfillment.lot_arrived payload: quantity ({error})") from error
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("Invalid fulfillment.lot_arrived payload: quantity")
    # The receipt id is the receiving line id, «:rN» after a supplier return's replacement.
    line_id = _uuid(payload.get("receiving_line_id")) or _uuid(receipt_id.split(":r")[0])
    return Arrival(
        lot_id=lot_id, receipt_id=receipt_id, receiving_line_id=line_id, quantity=quantity,
        grn=str(payload.get("grn") or "").strip(), package=str(payload.get("package") or "").strip(),
    )


def _positions(count: int) -> str:
    """1 позиция, 2 позиции, 5 позиций."""
    if count % 10 == 1 and count % 100 != 11:
        return "позиция"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "позиции"
    return "позиций"


def _positions_missing(count: int) -> str:
    """не хватает 1 позиции, 2 позиций."""
    return "позиции" if count % 10 == 1 and count % 100 != 11 else "позиций"


def kit_summary(project: str, kit: ArrivedKit) -> str:
    """«Пришло под 591К.3 КБ2: 3 позиции; комплект готов к сборке» / «…; ещё не хватает 2 позиций»."""
    where = f"{project} {kit.kb}" if kit.kb else f"{project} ({kit.request.number})"
    tail = (
        "комплект готов к сборке" if kit.complete
        else f"ещё не хватает {kit.missing} {_positions_missing(kit.missing)}"
    )
    return f"Пришло под {where}: {kit.lines} {_positions(kit.lines)}; {tail}"


def _notification_key(arrival: Arrival) -> str:
    return f"fulfillment:arrival:{arrival.receipt_id}"


def _notification(arrival: Arrival, project: str, result: ArrivedLotResult, now: datetime) -> OutboxMessage | None:
    lines = [kit_summary(project, kit) for kit in result.kits]
    if len(lines) == 1:
        title = lines[0]
    else:
        title = f"Пришло под {project}: " + ", ".join(kit.kb or kit.request.number for kit in result.kits)
    grn = arrival.grn + (f" (пакет {arrival.package})" if arrival.package else "")
    body = "\n".join(lines) + (f"\n\nГРН {grn} привязан к заявкам на комплектацию." if grn else "")
    return notification_requested(
        key=_notification_key(arrival),
        now=now,
        title=title,
        body=body,
        kind="arrival",
        reference=project,
        audiences=(STOREKEEPER_AUDIENCE,),
    )


def dispatch_next_lot_arrived(
    now: datetime,
    uow_factory: Callable[[], ArrivalUnitOfWork],
    sources: ArrivalSourceLookup,
) -> ArrivalOutcome | None:
    """One `fulfillment.lot_arrived` message: the binding, the notification and the message's «published» commit
    together, so the same message is applied once. A lot not bought for a project, gone, or with nothing waiting for
    it — the message is simply done. A broken payload or a refused binding is a poison message (nothing bound)."""
    failure: str | None = None
    uow = uow_factory()
    with uow:
        message = uow.outbox.next_unpublished(LOT_ARRIVED_TOPIC, now)
        if message is None:
            return None
        try:
            arrival = _arrival(message.payload)
        except ValueError as error:
            uow.outbox.mark_failed(message.id, now, str(error))
            uow.commit()
            raise PoisonArrivalMessage(message.id, str(error)) from error
        # Read the source before writing anything in this transaction.
        source = sources.source_of(arrival.receiving_line_id) if arrival.receiving_line_id else None
        project = source.project if source is not None else ""
        result, notified = _NOTHING, False
        # The storekeepers' notification is the durable mark of an applied arrival: a message delivered again (or a
        # second one for the same receipt) finds it and binds nothing more — not even to a kit created since.
        applied = uow.outbox.by_idempotency_key(_notification(arrival, project, _NOTHING, now).idempotency_key)
        applied = applied is not None
        if source is not None and project and not applied:
            try:
                result = bind_arrived_lot(BindArrivedLotCommand(
                    project=project,
                    lot_id=arrival.lot_id,
                    quantity=arrival.quantity,
                    articles=source.articles,
                    names=source.names,
                    actor=ARRIVAL_ACTOR,
                    now=now,
                ), uow)
            except (FulfillmentValidationError, WarehouseValidationError) as error:
                failure = str(error)
        if failure is None:
            if result.kits:
                notified = publish_notification(uow.outbox, _notification(arrival, project, result, now))
            uow.outbox.mark_published(message.id, now)
            uow.commit()
            return ArrivalOutcome(message_id=message.id, project=project, result=result, notified=notified)
    # The refused binding is rolled back with its transaction; the message is marked in a fresh one.
    with uow_factory() as retry:
        retry.outbox.mark_failed(message.id, now, failure)
        retry.commit()
    raise PoisonArrivalMessage(message.id, failure)
