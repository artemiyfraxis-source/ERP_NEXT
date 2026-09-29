"""Transactional finished-goods intake and shipment use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.notifications import (
    PROJECT_MANAGER_ROLE,
    STOREKEEPER_ROLE,
    NotificationOutbox,
    notification_requested,
    publish_notification,
)

from .domain import DeliveryTerms, FinishedGood, Shipment, ShipmentLine, ShippingValidationError, UpdType


class ConcurrentShippingUpdate(RuntimeError):
    pass


class FinishedGoodsRepository(Protocol):
    def get(self, good_id: UUID) -> FinishedGood | None: ...
    def by_key(self, project: str, item_index: str) -> FinishedGood | None: ...
    def list(self, limit: int = 500) -> Sequence[FinishedGood]: ...
    def add(self, good: FinishedGood) -> None: ...
    def reserve_grn(self, year: int) -> str: ...


class ShipmentRepository(Protocol):
    def get(self, shipment_id: UUID) -> Shipment | None: ...
    def list(self, limit: int = 200) -> Sequence[Shipment]: ...
    def reserve_number(self, day: date) -> str: ...
    def document_sequence(self, project: str) -> int: ...
    def add(self, shipment: Shipment) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    goods: FinishedGoodsRepository
    shipments: ShipmentRepository
    audit: AppendOnlyRepository
    outbox: NotificationOutbox
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class RegisterGoodCommand:
    project: str
    item_index: str
    customer: str
    article: str
    name: str
    ordered: Decimal
    unit: str
    note: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class AcceptGoodCommand:
    good_id: UUID
    expected_version: int
    quantity: Decimal
    received_at: date
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RequestedGood:
    good_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class CreateShipmentCommand:
    customer: str
    receiver: str
    shipping_point: str
    contact: str
    phone: str
    terms: DeliveryTerms
    carrier: str
    deadline: date | None
    upd_type: UpdType
    upd_number: str
    upd_date: date | None
    upd_url: str
    note: str
    lines: tuple[RequestedGood, ...]
    actor: Actor
    now: datetime
    no_docs: bool = False


@dataclass(frozen=True)
class ShipmentCommand:
    shipment_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class UnpickCommand(ShipmentCommand):
    line_id: UUID


@dataclass(frozen=True)
class UpdateDocumentsCommand(ShipmentCommand):
    upd_type: UpdType
    upd_number: str
    upd_date: date | None
    upd_url: str
    track_number: str


def _audit(aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict, uow: UnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id, event_type=event_type,
        actor=actor, occurred_at=now, details=details,
    ))


def _good(good_id: UUID, version: int, uow: UnitOfWork) -> FinishedGood:
    good = uow.goods.get(good_id)
    if good is None:
        raise KeyError(f"Finished good not found: {good_id}")
    if good.version != version:
        raise ConcurrentShippingUpdate("Позиция готовой продукции изменилась после загрузки формы")
    return good


def _shipment(shipment_id: UUID, version: int, uow: UnitOfWork) -> Shipment:
    shipment = uow.shipments.get(shipment_id)
    if shipment is None:
        raise KeyError(f"Shipment not found: {shipment_id}")
    if shipment.version != version:
        raise ConcurrentShippingUpdate("Отгрузка изменилась после загрузки формы")
    return shipment


def register_good(command: RegisterGoodCommand, uow: UnitOfWork) -> FinishedGood:
    with uow:
        if uow.goods.by_key(command.project.strip(), command.item_index.strip()) is not None:
            raise ShippingValidationError("Такая КБ проекта уже есть в готовой продукции")
        good = FinishedGood.create(
            project=command.project, item_index=command.item_index, customer=command.customer,
            article=command.article, name=command.name, ordered=command.ordered,
            unit=command.unit, note=command.note,
        )
        uow.goods.add(good)
        _audit(good.id, "shipping.good_registered", command.actor, command.now, {"project": good.project, "item_index": good.item_index}, uow)
        uow.commit()
        return good


def accept_good(command: AcceptGoodCommand, uow: UnitOfWork) -> FinishedGood:
    with uow:
        good = _good(command.good_id, command.expected_version, uow)
        grn = good.grn or uow.goods.reserve_grn(command.now.year)
        updated = good.accept(command.quantity, command.received_at, grn)
        uow.goods.add(updated)
        _audit(good.id, "shipping.good_accepted", command.actor, command.now, {"quantity": str(command.quantity), "grn": grn}, uow)
        uow.commit()
        return updated


def create_shipment(command: CreateShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        if len({line.good_id for line in command.lines}) != len(command.lines):
            raise ShippingValidationError("Позиция готовой продукции указана дважды")
        lines = []
        for requested in command.lines:
            good = uow.goods.get(requested.good_id)
            if good is None:
                raise KeyError(f"Finished good not found: {requested.good_id}")
            lines.append(ShipmentLine.create(good, requested.quantity))
        shipment = Shipment.create(
            number=uow.shipments.reserve_number(command.now.date()), created_at=command.now,
            customer=command.customer, receiver=command.receiver, shipping_point=command.shipping_point,
            contact=command.contact, phone=command.phone, terms=command.terms,
            carrier=command.carrier, deadline=command.deadline, upd_type=command.upd_type,
            upd_number=command.upd_number, upd_date=command.upd_date, upd_url=command.upd_url,
            manager=command.actor.name, note=command.note, lines=tuple(lines),
            manager_id=command.actor.id,
        )
        uow.shipments.add(shipment)
        _audit(shipment.id, "shipping.created", command.actor, command.now, {"number": shipment.number}, uow)
        # Shipment.gs shipRequestCreate_: набор сразу при заведении, затем PL/DN → «Упакована»;
        # noDocs → остаётся «Собрана». Цех ещё ничего не сдал — заявка «Новая» и ждёт.
        shipment, taken = _pick(shipment, command.actor, command.now, uow)
        if taken and not command.no_docs:
            shipment = _pack(shipment, command.actor, command.now, uow)
        uow.commit()
        return shipment


def _pick(shipment: Shipment, actor: Actor, now: datetime, uow: UnitOfWork) -> tuple[Shipment, dict[UUID, Decimal]]:
    goods = {line.finished_good_id: uow.goods.get(line.finished_good_id) for line in shipment.lines}
    if any(good is None for good in goods.values()):
        raise ShippingValidationError("Позиция готовой продукции больше не существует")
    typed = {good_id: good for good_id, good in goods.items() if good is not None}
    updated, taken = shipment.pick(
        {good_id: good.remaining for good_id, good in typed.items()},
        {good_id: good.grn or "" for good_id, good in typed.items()},
    )
    for good_id, quantity in taken.items():
        uow.goods.add(typed[good_id].take(quantity))
    if taken:
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.picked", actor, now, {str(key): str(value) for key, value in taken.items()}, uow)
    return updated, taken


def _pack(shipment: Shipment, actor: Actor, now: datetime, uow: UnitOfWork) -> Shipment:
    projects = sorted({line.project for line in shipment.lines if line.picked > 0})
    if len(projects) == 1:
        suffix = f"{projects[0]}-{uow.shipments.document_sequence(projects[0])}"
    else:
        suffix = shipment.number
    updated = shipment.pack(f"PL{suffix}", f"DN{suffix}", now.date())
    uow.shipments.add(updated)
    _audit(shipment.id, "shipping.packed", actor, now, {"pl": updated.pl_number, "dn": updated.dn_number}, uow)
    return updated


def pick_shipment(command: ShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated, taken = _pick(shipment, command.actor, command.now, uow)
        if taken:
            uow.commit()
        return updated


def unpick_line(command: UnpickCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated, good_id, quantity = shipment.unpick(command.line_id)
        good = uow.goods.get(good_id)
        if good is None:
            raise ShippingValidationError("Позиция готовой продукции больше не существует")
        uow.goods.add(good.put_back(quantity))
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.unpicked", command.actor, command.now, {"line_id": str(command.line_id), "quantity": str(quantity)}, uow)
        uow.commit()
        return updated


def pack_shipment(command: ShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = _pack(shipment, command.actor, command.now, uow)
        uow.commit()
        return updated


def _notify_documents(before: Shipment, after: Shipment, now: datetime, uow: UnitOfWork) -> None:
    project = after.lines[0].project if after.lines else ""
    of_project = f" проекта {project}" if project else ""
    # Shipment.gs:840-850: the storekeeper types the track, the manager waits for it. Recipient is the
    # shipment's manager; no matching user → every project manager (`shipTrackRecipients_`).
    if after.track_number and after.track_number != before.track_number:
        publish_notification(uow.outbox, notification_requested(
            key=f"shipping:{after.id}:track:v{after.version}",
            now=now,
            title=f"Трек по отправке {after.number}",
            body=(
                f"Отправка {after.number}{of_project} ушла. Трек-номер: {after.track_number}"
                "\n\nОткрыть: портал → Отгрузка → карточка проекта."
            ),
            kind="ship",
            reference=after.number,
            user_ids=(after.manager_id,),
            roles=() if after.manager_id else (PROJECT_MANAGER_ROLE,),
        ))
    # Shipment.gs:888 `shipUpdAttach_`: storekeepers print the УПД, so a newly attached file goes to them.
    if after.upd_url and after.upd_url != before.upd_url:
        items = ", ".join(f"{line.item_index} × {line.requested.normalize():f}" for line in after.lines)
        publish_notification(uow.outbox, notification_requested(
            key=f"shipping:{after.id}:upd:v{after.version}",
            now=now,
            title=f"УПД к отправке {after.number}",
            body=(
                f"Менеджер приложил УПД{f' № {after.upd_number}' if after.upd_number else ''} к отправке "
                f"{after.number}{of_project} ({items}).\nФайл: {after.upd_url}"
                "\n\nОткрыть: портал → Отгрузка → карточка проекта."
            ),
            kind="ship",
            reference=after.number,
            roles=(STOREKEEPER_ROLE,),
        ))


def update_documents(command: UpdateDocumentsCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.update_documents(
            upd_type=command.upd_type, upd_number=command.upd_number,
            upd_date=command.upd_date, upd_url=command.upd_url,
            track_number=command.track_number,
        )
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.documents_updated", command.actor, command.now, {}, uow)
        _notify_documents(shipment, updated, command.now, uow)
        uow.commit()
        return updated


def mark_shipped(command: ShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.mark_shipped(command.now.date())
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.shipped", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


def close_shipment(command: ShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.close()
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.closed", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


def cancel_shipment(command: ShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated, released = shipment.cancel()
        for good_id, quantity in released.items():
            good = uow.goods.get(good_id)
            if good is None:
                raise ShippingValidationError("Позиция готовой продукции больше не существует")
            uow.goods.add(good.put_back(quantity))
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.cancelled", command.actor, command.now, {}, uow)
        uow.commit()
        return updated
