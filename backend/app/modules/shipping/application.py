"""Transactional finished-goods intake and shipment use cases."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Protocol, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.shared.events import AuditEvent
from app.shared.file_types import stored_content_type
from app.shared.identity import Actor
from app.shared.notifications import (
    PROJECT_MANAGER_AUDIENCE,
    STOREKEEPER_AUDIENCE,
    NotificationOutbox,
    notification_requested,
    publish_notification,
)

from .domain import (
    AcceptanceAct, DeliveryTerms, FinishedGood, GoodMove, MoveContext, ProjectAddressee, ProjectAssembly, Shipment, ShipmentLine,
    ShipmentStatus, ShippingValidationError, StoredFile, UpdType, check_boxes, effective_address, packed_by_index,
    plan_project_queue,
)


class ConcurrentShippingUpdate(RuntimeError):
    pass


class ShippingPermissionError(PermissionError):
    """Часть команды, которую этой роли делать нельзя: отказ всей команде (HTTP 403)."""


# SUPPLYMAP ограничения не задаёт (Drive принимает что угодно); ERP держит файл в базе — 20 МБ.
UPD_FILE_LIMIT = 20 * 1024 * 1024


class FinishedGoodsRepository(Protocol):
    def get(self, good_id: UUID) -> FinishedGood | None: ...
    def by_key(self, project: str, item_index: str) -> FinishedGood | None: ...
    def list(self, limit: int = 500) -> Sequence[FinishedGood]: ...
    def add(self, good: FinishedGood, move: MoveContext | None = None) -> tuple[GoodMove, ...]: ...
    def reserve_grn(self, year: int) -> str: ...
    def record_act(self, good: FinishedGood, moves: tuple[GoodMove, ...]) -> AcceptanceAct | None: ...
    def acts(self, good_ids: Sequence[UUID]) -> dict[UUID, tuple[AcceptanceAct, ...]]: ...
    def by_project(self, project: str) -> Sequence[FinishedGood]: ...
    def for_project(self, project: str) -> Sequence[FinishedGood]: ...
    def in_shipments(self, good_ids: Sequence[UUID]) -> set[UUID]: ...
    def in_journal(self, good_ids: Sequence[UUID]) -> set[UUID]: ...
    def remove(self, good_id: UUID) -> None: ...


class ShipmentRepository(Protocol):
    def get(self, shipment_id: UUID) -> Shipment | None: ...
    def list(self, limit: int = 200) -> Sequence[Shipment]: ...
    def for_project(self, project: str) -> Sequence[Shipment]: ...
    def reserve_number(self, day: date) -> str: ...
    def document_sequence(self, project: str) -> int: ...
    def add(self, shipment: Shipment) -> None: ...
    def open_for_project(self, project: str) -> Sequence[Shipment]: ...
    def created_details(self, shipment_id: UUID) -> dict | None: ...
    def packers(self, shipment_id: UUID) -> list[tuple[UUID, str]]: ...


class ProjectAddresseeRepository(Protocol):
    def get(self, project: str) -> ProjectAddressee | None: ...
    def list(self) -> Sequence[ProjectAddressee]: ...
    def add(self, addressee: ProjectAddressee) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UpdFileRepository(Protocol):
    def add(self, stored: StoredFile, content: bytes) -> None: ...
    def get(self, file_id: UUID) -> tuple[StoredFile, bytes] | None: ...
    def names(self, file_ids: Sequence[UUID]) -> dict[UUID, str]: ...


class UnitOfWork(Protocol):
    files: UpdFileRepository
    goods: FinishedGoodsRepository
    shipments: ShipmentRepository
    addressees: ProjectAddresseeRepository
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
class QueueProjectCommand:
    project: str
    customer: str
    assemblies: tuple[ProjectAssembly, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class QueueProjectResult:
    added: int
    updated: int
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class DropProjectCommand:
    project: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class DropProjectResult:
    deleted: int
    kept: int


@dataclass(frozen=True)
class ShipBeforeErpCommand:
    """Перенос: столько КБ проекта уже отгружено в Google (итог по листу цеха, не приращение)."""
    project: str
    item_index: str
    total: Decimal
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
    # Сколько всего можно принять по КБ (сдано цехом; без заданий в ERP — тираж) и как это назвать в отказе;
    # None — без предела (приёмка до регистра, тесты домена).
    limit: Decimal | None = None
    limit_basis: str = "сдано цехом"
    reason: str = ""
    location: str = ""


@dataclass(frozen=True)
class RequestedGood:
    good_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class CreateShipmentCommand:
    """Empty address fields inherit the project card (`shipAddrEff_`); see `create_shipment`."""

    customer: str
    receiver: str
    shipping_point: str
    contact: str
    phone: str
    terms: DeliveryTerms | None
    carrier: str
    deadline: date | None
    upd_type: UpdType | None
    upd_number: str
    upd_date: date | None
    upd_url: str
    note: str
    lines: tuple[RequestedGood, ...]
    actor: Actor
    now: datetime
    no_docs: bool = False


@dataclass(frozen=True)
class SetProjectAddresseeCommand:
    """Card edit: only the sent keys change, an empty value erases (`portalShipProjectSet`)."""

    project: str
    fields: dict
    expected_version: int | None
    actor: Actor
    now: datetime


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
class RollbackShippedCommand(ShipmentCommand):
    """Откат «Отгружено» — только руководитель (администратор), всегда с причиной."""
    reason: str
    allowed: bool


@dataclass(frozen=True)
class UpdateDocumentsCommand(ShipmentCommand):
    upd_type: UpdType | None
    upd_number: str
    upd_date: date | None
    upd_url: str
    track_number: str


@dataclass(frozen=True)
class EditHeadCommand(ShipmentCommand):
    """Присланные поля шапки: значение — своё у отправки, пустое — снова из карточки проекта."""
    fields: dict


@dataclass(frozen=True)
class SetRequestedCommand(ShipmentCommand):
    line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class LayOutCommand(ShipmentCommand):
    boxes: tuple[dict, ...]
    tk_note: str


@dataclass(frozen=True)
class PackProjectCommand:
    """`portalShipPack`: «Упаковать» в карточке проекта — отправка из того, что лежит на складе ГП."""

    project: str
    lines: tuple[RequestedGood, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ShippedResult:
    shipment: Shipment
    why: str


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


def queue_project(command: QueueProjectCommand, uow: UnitOfWork) -> QueueProjectResult:
    """`shipQueueForProject_`: the project card puts one finished-goods row per КБ in the queue.

    Called on project creation and on every «Обновить из расчёта», hence idempotent.
    """
    project = command.project.strip()
    if not project:
        raise ShippingValidationError("Не указан проект")
    with uow:
        plan = plan_project_queue(command.assemblies, list(uow.goods.by_project(project)))
        for assembly in plan.add:
            good = FinishedGood.create(
                project=project, item_index=assembly.item_index, customer=command.customer,
                article=assembly.article, name=assembly.name.strip() or assembly.article.strip() or assembly.item_index,
                ordered=assembly.run, unit="шт",
            )
            uow.goods.add(good)
            _audit(good.id, "shipping.good_queued", command.actor, command.now, {"project": project, "item_index": good.item_index, "ordered": str(good.ordered)}, uow)
        for current, run in plan.update:
            uow.goods.add(current.reorder(run))
            _audit(current.id, "shipping.good_reordered", command.actor, command.now, {"from": str(current.ordered), "to": str(run)}, uow)
        uow.commit()
        return QueueProjectResult(added=len(plan.add), updated=len(plan.update), skipped=plan.skipped)


def drop_project(command: DropProjectCommand, uow: UnitOfWork) -> DropProjectResult:
    """`shipGpDropProject_`: the card is gone, rows without movement go with it.

    Accepted or shipped goods stay: they are real products whose balance outlives the card. A row
    already named in a shipment also stays, since the shipment line refers to it.
    """
    project = command.project.strip()
    if not project:
        return DropProjectResult(deleted=0, kept=0)
    with uow:
        goods = list(uow.goods.by_project(project))
        ids = [good.id for good in goods]
        referenced = uow.goods.in_shipments(ids) | uow.goods.in_journal(ids)
        deleted = kept = 0
        for good in goods:
            if good.has_movement or good.id in referenced:
                kept += 1
                continue
            uow.goods.remove(good.id)
            _audit(good.id, "shipping.good_dropped", command.actor, command.now, {"project": project, "item_index": good.item_index}, uow)
            deleted += 1
        uow.commit()
        return DropProjectResult(deleted=deleted, kept=kept)


def ship_before_erp(command: ShipBeforeErpCommand, uow: UnitOfWork) -> FinishedGood:
    """Отгруженное до ERP — в регистр ГП как пришедшее и уехавшее; повтор с тем же итогом ничего не меняет."""
    with uow:
        good = uow.goods.by_key(command.project.strip(), command.item_index.strip())
        if good is None:
            raise ShippingValidationError(f"В готовой продукции нет КБ {command.item_index} проекта {command.project}")
        updated = good.ship_before_erp(command.total)
        if updated is not good:
            uow.goods.add(updated, _context(command.actor, command.now, reason="отгружено до ERP (лист цеха)"))
            _audit(good.id, "shipping.shipped_before_erp", command.actor, command.now, {
                "total": str(command.total), "added": str(updated.before_erp - good.before_erp),
            }, uow)
            uow.commit()
        return updated


def _context(actor: Actor, now: datetime, **values) -> MoveContext:
    return MoveContext(actor=actor.name, actor_id=actor.id, occurred_at=now, **values)


def _plain(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def accept_good(command: AcceptGoodCommand, uow: UnitOfWork) -> FinishedGood:
    with uow:
        good = _good(command.good_id, command.expected_version, uow)
        updated = accept_into_stock(
            uow, good, command.quantity, received_at=command.received_at, actor=command.actor, now=command.now,
            limit=command.limit, limit_basis=command.limit_basis, reason=command.reason, location=command.location,
        )
        uow.commit()
        return updated


def accept_into_stock(
    uow, good: FinishedGood, quantity: Decimal, *, received_at: date, actor: Actor, now: datetime,
    limit: Decimal | None = None, limit_basis: str = "сдано цехом", reason: str = "", id_seed: UUID | None = None,
    location: str = "",
) -> FinishedGood:
    """«Принято складом» без фиксации: в производстве → принято, не больше `limit` всего по КБ (сдано цехом).
    Зовут приёмка одной КБ и пакетная «Из производства» — фиксирует вызывающий. `location` — место хранения КБ на
    складе ГП: непустое заменяет прежнее, пустое его оставляет."""
    amount = Decimal(quantity)
    if not amount.is_finite() or amount <= 0:
        raise ShippingValidationError(f"{good.project} {good.item_index}: количество должно быть больше нуля")
    if limit is not None and good.accepted + amount > limit:
        left = max(limit - good.accepted, Decimal("0"))
        raise ShippingValidationError(
            f"{good.project} {good.item_index}: {limit_basis} {_plain(limit)} {good.unit}, уже принято "
            f"{_plain(good.accepted)} — принять можно ещё {_plain(left)}"
        )
    updated = good.accept(amount, received_at, good.grn or uow.goods.reserve_grn(now.year), location)
    # Акт приёмки со своим ГРН (владелец 19.10.2026): первый — ГРН КБ, каждая следующая часть — новый номер.
    act = uow.goods.record_act(updated, uow.goods.add(updated, _context(actor, now, reason=reason, id_seed=id_seed)))
    _audit(good.id, "shipping.good_accepted", actor, now, {
        "quantity": str(amount), "grn": act.grn, **({"reason": reason} if reason else {}),
        **({"location": updated.location} if updated.location != good.location else {}),
    }, uow)
    return updated


def create_shipment(command: CreateShipmentCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _create_shipment(command, uow)
        uow.commit()
        return shipment


def _create_shipment(
    command: CreateShipmentCommand, uow: UnitOfWork, *, shipment_id: UUID | None = None, details: dict | None = None,
) -> Shipment:
    """Заведение отправки без фиксации: её фиксирует вызывающий вместе с остальными шагами."""
    if len({line.good_id for line in command.lines}) != len(command.lines):
        raise ShippingValidationError("Позиция готовой продукции указана дважды")
    lines = []
    customers: list[str] = []
    for requested in command.lines:
        good = uow.goods.get(requested.good_id)
        if good is None:
            raise KeyError(f"Finished good not found: {requested.good_id}")
        lines.append(ShipmentLine.create(good, requested.quantity))
        customers.append(good.customer)
    if not lines:
        raise ShippingValidationError("Не выбрано ни одной позиции с количеством")
    # The shipment's project is its first line's (`shipHeadEff_`).
    card = uow.addressees.get(lines[0].project)
    address = effective_address(
        receiver=command.receiver, shipping_point=command.shipping_point, contact=command.contact,
        phone=command.phone, terms=command.terms, carrier=command.carrier, upd_type=command.upd_type,
        project=card,
        customer_fallback=command.customer or next((name for name in customers if name), ""),
    )
    if not address.shipping_point:
        raise ShippingValidationError("Укажите точку отгрузки в заявке или в карточке проекта")
    shipment = Shipment.create(
        number=uow.shipments.reserve_number(command.now.date()), created_at=command.now,
        customer=command.customer, receiver=address.receiver, shipping_point=address.shipping_point,
        contact=address.contact, phone=address.phone, terms=address.terms,
        carrier=address.carrier, upd_type=address.upd_type,
        # Срок и комментарий, не заданные в заявке, — из плана отгрузки карточки проекта (копия на момент заведения).
        deadline=command.deadline or (card.plan_date if card is not None else None),
        upd_number=command.upd_number, upd_date=command.upd_date,
        upd_url=str(command.upd_url or "").strip() or _card_upd(card, address.upd_type, uow),
        manager=command.actor.name, note=str(command.note or "").strip() or (card.note if card is not None else ""),
        lines=tuple(lines),
        manager_id=command.actor.id, inherited=_unset_fields(command), id=shipment_id,
    )
    uow.shipments.add(shipment)
    if shipment.upd_url:
        _notify_upd(shipment, command.now, uow)
    _remember_addressees(shipment, command, uow)
    _audit(shipment.id, "shipping.created", command.actor, command.now, {
        "number": shipment.number, "inherited": sorted(address.inherited), **(details or {}),
    }, uow)
    # Shipment.gs shipRequestCreate_: набор сразу при заведении, затем PL/DN → «Упакована»;
    # noDocs → остаётся «Собрана». Цех ещё ничего не сдал — заявка «Новая» и ждёт.
    shipment, taken = _pick(shipment, command.actor, command.now, uow)
    if taken and not command.no_docs:
        shipment = _pack(shipment, command.actor, command.now, uow)
    return shipment


def _card_upd(card: ProjectAddressee | None, upd_type: UpdType | None, uow: UnitOfWork) -> str:
    """Бумажная УПД, приложенная к данным отправки заранее (владелец 14.10.2026), — новой отправке с бумажной УПД.
    Один файл — одной отправке: ушёл с другой (не отменённой) отправкой проекта — следующей нужен свой."""
    if card is None or not card.upd_url or upd_type != UpdType.PAPER:
        return ""
    taken = any(
        other.upd_url == card.upd_url and other.status != ShipmentStatus.CANCELLED
        for other in uow.shipments.for_project(card.project)
    )
    return "" if taken else card.upd_url


def _unset_fields(command: CreateShipmentCommand) -> frozenset[str]:
    """Поля адресата, не заданные в заявке: они и дальше идут за карточкой проекта."""
    own = {
        "receiver": command.receiver, "shipping_point": command.shipping_point, "contact": command.contact,
        "phone": command.phone, "terms": command.terms, "carrier": command.carrier, "upd_type": command.upd_type,
    }
    return frozenset(key for key, value in own.items() if not (value.strip() if isinstance(value, str) else value))


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
        uow.goods.add(typed[good_id].take(quantity), _context(actor, now, shipment_id=shipment.id))
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


def _addressee_id(project: str) -> UUID:
    return uuid5(NAMESPACE_URL, "erp-next:shipping:project-addressee:" + project)


def _remember_addressees(shipment: Shipment, command: CreateShipmentCommand, uow: UnitOfWork) -> None:
    """The typed point, contact and phone stay with each project of the shipment; empty never erases."""
    for project in dict.fromkeys(line.project for line in shipment.lines):
        current = uow.addressees.get(project) or ProjectAddressee.empty(project)
        remembered = current.remember(shipping_point=command.shipping_point, contact=command.contact, phone=command.phone)
        if remembered is current:
            continue
        uow.addressees.add(replace(
            remembered, version=current.version + 1, updated_by=command.actor.name, updated_at=command.now,
        ))
        _audit(_addressee_id(project), "shipping.addressee_remembered", command.actor, command.now, {
            "project": project, "shipment": shipment.number,
        }, uow)


def set_project_addressee(command: SetProjectAddresseeCommand, uow: UnitOfWork) -> ProjectAddressee:
    with uow:
        current, updated = _set_project_addressee(command, uow)
        if updated is not current:
            uow.commit()
        return updated


def _set_project_addressee(command: SetProjectAddresseeCommand, uow: UnitOfWork) -> tuple[ProjectAddressee, ProjectAddressee]:
    """Правка карточки без фиксации: (было, стало); без изменений — тот же объект."""
    project = str(command.project or "").strip()
    current = uow.addressees.get(project) or ProjectAddressee.empty(project)
    if command.expected_version is not None and command.expected_version != current.version:
        raise ConcurrentShippingUpdate("Адресат проекта изменился после загрузки формы")
    try:
        edited = current.edit(command.fields)
    except ValueError as error:
        if isinstance(error, ShippingValidationError):
            raise
        raise ShippingValidationError("Недопустимое значение поля адресата") from error
    if not edited.differs(current):
        return current, current
    updated = replace(edited, version=current.version + 1, updated_by=command.actor.name, updated_at=command.now)
    uow.addressees.add(updated)
    _audit(_addressee_id(project), "shipping.addressee_set", command.actor, command.now, {
        "project": project, "fields": sorted(command.fields),
    }, uow)
    for shipment in uow.shipments.open_for_project(project):
        followed = shipment.follow(updated)
        if followed is not shipment:
            uow.shipments.add(followed)
            _audit(shipment.id, "shipping.head_followed_project", command.actor, command.now, {"project": project}, uow)
    return current, updated


def _card(shipment: Shipment, uow: UnitOfWork) -> ProjectAddressee | None:
    return uow.addressees.get(shipment.lines[0].project) if shipment.lines else None


def edit_shipment_head(command: EditHeadCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.edit_head(command.fields, _card(shipment, uow))
        if updated is shipment:
            return shipment
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.head_edited", command.actor, command.now, {"fields": sorted(command.fields)}, uow)
        uow.commit()
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
        uow.goods.add(good.put_back(quantity), _context(command.actor, command.now, shipment_id=shipment.id))
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
            audiences=() if after.manager_id else (PROJECT_MANAGER_AUDIENCE,),
        ))
    if after.upd_url and after.upd_url != before.upd_url:
        _notify_upd(after, now, uow)


def _notify_upd(shipment: Shipment, now: datetime, uow: UnitOfWork) -> None:
    """Shipment.gs:888 `shipUpdAttach_`: storekeepers print the УПД, so a newly attached file goes to them — also
    a УПД link given already in the new shipment (OP-1: before, nobody heard of that file)."""
    project = shipment.lines[0].project if shipment.lines else ""
    of_project = f" проекта {project}" if project else ""
    items = ", ".join(f"{line.item_index} × {line.requested.normalize():f}" for line in shipment.lines)
    publish_notification(uow.outbox, notification_requested(
        key=f"shipping:{shipment.id}:upd:v{shipment.version}",
        now=now,
        title=f"УПД к отправке {shipment.number}",
        body=(
            f"Менеджер приложил УПД{f' № {shipment.upd_number}' if shipment.upd_number else ''} к отправке "
            f"{shipment.number}{of_project} ({items}).\nФайл: {shipment.upd_url}"
            "\n\nОткрыть: портал → Отгрузка → карточка проекта."
        ),
        kind="ship",
        reference=shipment.number,
        audiences=(STOREKEEPER_AUDIENCE,),
    ))


def notify_dispatch_gaps(uow, project: str, items: Sequence[str], key: str, now: datetime) -> bool:
    """Склад принял КБ проекта, а данных отправки нет (владелец 14.10.2026) — менеджеру проекта, один раз на приёмку.

    Менеджер проекта — менеджер последней не отменённой отправки проекта (как трек уходит менеджеру отправки); отправок
    нет — все менеджеры проектов (`shipTrackRecipients_`). `uow` — с `addressees`, `shipments` и `outbox` в той же
    транзакции, что и приёмка."""
    card = uow.addressees.get(project) or ProjectAddressee.empty(project)
    gaps = card.dispatch_gaps()
    if not gaps:
        return False
    shipments = [item for item in uow.shipments.for_project(project) if item.status != ShipmentStatus.CANCELLED and item.manager_id]
    manager_id = max(shipments, key=lambda item: item.created_at).manager_id if shipments else None
    kbs = ", ".join(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))
    return publish_notification(uow.outbox, notification_requested(
        key=f"shipping:dispatch-gaps:{key}:{project}",
        now=now,
        title=f"Нет данных отправки: проект {project}",
        body=(
            f"Приняли на склад КБ {kbs} проекта {project}, а данных отправки нет: {', '.join(gaps)}. "
            "Заполните в „Приёмке с производства“ → „Данные отправки“."
            "\n\nОткрыть: портал → Склад → Приёмка с производства (?section=intake)."
        ),
        kind="ship",
        reference=project,
        user_ids=(manager_id,),
        audiences=() if manager_id else (PROJECT_MANAGER_AUDIENCE,),
    ))


@dataclass(frozen=True)
class AttachUpdCommand(ShipmentCommand):
    file_name: str
    content_type: str
    content: bytes


UPD_FILE_URL_PREFIX = "/api/shipping/files/"


def upd_file_url(file_id: UUID) -> str:
    return f"{UPD_FILE_URL_PREFIX}{file_id}"


def attach_upd_file(command: AttachUpdCommand, uow: UnitOfWork) -> Shipment:
    """`shipUpdAttach_`: файл кладётся в хранилище, ссылка встаёт в отправку, кладовщикам — уведомление."""
    _check_upd_file(command.content)
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = _attach_upd(shipment, command.file_name, command.content_type, command.content, command.actor, command.now, uow)
        uow.commit()
        return updated


@dataclass(frozen=True)
class AttachCardUpdCommand:
    """Файл бумажной УПД к данным отправки проекта — отправки ещё нет; ссылку в карточку ставит её правка."""

    project: str
    file_name: str
    content_type: str
    content: bytes
    actor: Actor
    now: datetime


def store_card_upd_file(command: AttachCardUpdCommand, uow: UnitOfWork) -> StoredFile:
    """То же хранилище, что у файлов УПД отправок: файл помечен проектом, имя — как его приложили."""
    _check_upd_file(command.content)
    project = str(command.project or "").strip()
    if not project:
        raise ShippingValidationError("Не указан проект")
    name = str(command.file_name or "").strip().replace("/", "_").replace("\\", "_")[:512] or "УПД"
    stored = StoredFile(
        id=uuid4(), shipment_id=None, project=project, file_name=name,
        content_type=stored_content_type(command.content), size=len(command.content),
        uploaded_by=command.actor.name, uploaded_at=command.now,
    )
    with uow:
        uow.files.add(stored, command.content)
        _audit(_addressee_id(project), "shipping.addressee_upd_stored", command.actor, command.now, {
            "project": project, "file_id": str(stored.id), "name": name,
        }, uow)
        uow.commit()
    return stored


def _check_upd_file(content: bytes) -> None:
    if not content:
        raise ShippingValidationError("Файл не передан.")
    if len(content) > UPD_FILE_LIMIT:
        raise ShippingValidationError("Файл УПД больше 20 МБ.")


def _attach_upd(
    shipment: Shipment, file_name: str, content_type: str, content: bytes, actor: Actor, now: datetime, uow: UnitOfWork,
) -> Shipment:
    original = str(file_name or "").strip()
    extension = original[original.rfind("."):] if "." in original else ""
    project = shipment.lines[0].project if shipment.lines else ""
    name = " ".join(part for part in (
        "УПД", f"№ {shipment.upd_number}" if shipment.upd_number else "", project, shipment.number,
    ) if part) + extension
    stored = StoredFile(
        id=uuid4(), shipment_id=shipment.id, file_name=name,
        content_type=stored_content_type(content),
        size=len(content), uploaded_by=actor.name, uploaded_at=now,
    )
    updated = shipment.attach_upd(upd_file_url(stored.id))
    uow.files.add(stored, content)
    uow.shipments.add(updated)
    _audit(shipment.id, "shipping.upd_attached", actor, now, {"file_id": str(stored.id), "name": name}, uow)
    _notify_documents(shipment, updated, now, uow)
    return updated


def update_documents(command: UpdateDocumentsCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.update_documents(
            upd_type=command.upd_type, upd_number=command.upd_number,
            upd_date=command.upd_date, upd_url=command.upd_url,
            track_number=command.track_number, card=_card(shipment, uow),
        )
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.documents_updated", command.actor, command.now, {}, uow)
        _notify_documents(shipment, updated, command.now, uow)
        uow.commit()
        return updated


def mark_shipped(command: ShipmentCommand, uow: UnitOfWork) -> ShippedResult:
    """`shipShipped_`: одна кнопка склада — дата отгрузки и сразу «Закрыта», если для этого всё есть."""
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        result = _mark_shipped(shipment, command.actor, command.now, uow)
        uow.commit()
        return result


def _mark_shipped(shipment: Shipment, actor: Actor, now: datetime, uow: UnitOfWork) -> ShippedResult:
    updated = shipment.mark_shipped(now.date())
    uow.shipments.add(updated)
    sent: dict[UUID, Decimal] = {}
    for line in updated.lines:
        if line.picked > 0:
            sent[line.finished_good_id] = sent.get(line.finished_good_id, Decimal("0")) + line.picked
    for good_id, quantity in sent.items():
        good = uow.goods.get(good_id)
        if good is None:
            raise ShippingValidationError("Позиция готовой продукции больше не существует")
        uow.goods.add(good.dispatch(quantity), _context(actor, now, shipment_id=shipment.id))
    _audit(shipment.id, "shipping.shipped", actor, now, {}, uow)
    why = ""
    try:
        closed = updated.close()
    except ShippingValidationError as error:
        why = str(error)
    else:
        updated = closed
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.closed", actor, now, {"auto": True}, uow)
    return ShippedResult(shipment=updated, why=why)


def set_requested(command: SetRequestedCommand, uow: UnitOfWork) -> Shipment:
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.set_requested(command.line_id, command.quantity)
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.requested_changed", command.actor, command.now, {
            "line_id": str(command.line_id), "quantity": str(command.quantity),
        }, uow)
        uow.commit()
        return updated


def lay_out_shipment(command: LayOutCommand, uow: UnitOfWork) -> Shipment:
    """«Сформировать отгрузочные» / «Перевыпустить» (`portalShipDocs`): короба и примечания для ТК.

    Документов ещё нет — переход в «Упакована» с номерами PL/DN; есть — номера сохраняются.
    """
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        boxes = check_boxes(packed_by_index(shipment.lines), list(command.boxes))
        updated = shipment.lay_out(boxes, command.tk_note)
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.boxes_laid_out", command.actor, command.now, {"places": len(boxes)}, uow)
        if not updated.pl_number:
            updated = _pack(updated, command.actor, command.now, uow)
        uow.commit()
        return updated


def pack_project(command: PackProjectCommand, uow: UnitOfWork) -> Shipment:
    """`shipPackProject_`: отмеченное количество, не больше остатка ГП, адресат — из карточки проекта."""
    project = str(command.project or "").strip()
    with uow:
        goods = {good.id: good for good in uow.goods.by_project(project)}
        addressee = uow.addressees.get(project)
    if not goods:
        raise ShippingValidationError(f"У проекта {project} нет строк готовой продукции.")
    remain = {good_id: good.remaining for good_id, good in goods.items()}
    lines: list[RequestedGood] = []
    for requested in command.lines:
        if requested.good_id not in goods:
            raise ShippingValidationError("Позиция не относится к проекту")
        quantity = min(Decimal(requested.quantity), max(remain[requested.good_id], Decimal("0")))
        if not quantity > 0:
            continue
        remain[requested.good_id] -= quantity
        lines.append(RequestedGood(good_id=requested.good_id, quantity=quantity))
    if not lines:
        raise ShippingValidationError("Упаковывать нечего: на складе по отмеченным КБ пусто — сначала «Принять» от цеха.")
    if addressee is None or not addressee.shipping_point:
        raise ShippingValidationError("Укажи адрес отгрузки в карточке проекта.")
    customer = next(iter(goods.values())).customer
    # Адресат не копируется, а наследуется: правка карточки проекта дойдёт до этой отправки, пока она открыта.
    return create_shipment(CreateShipmentCommand(
        customer=customer, receiver="" if addressee.receiver or customer else project,
        shipping_point="", contact="", phone="",
        terms=None, carrier="", deadline=None, upd_type=None, upd_number="", upd_date=None, upd_url="",
        note="", lines=tuple(lines), actor=command.actor, now=command.now, no_docs=True,
    ), uow)


@dataclass(frozen=True)
class ProjectShipLine:
    good_id: UUID
    version: int
    quantity: Decimal


@dataclass(frozen=True)
class UpdUpload:
    file_name: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class ProjectShipCommand:
    """«Отгрузить» / «Упаковать, отгрузить позже» в карточке проекта — одной транзакцией.

    Карточка адресата (если правлена), отправка, файл УПД и, при `ship`, отгрузка с автозакрытием либо
    записываются все, либо ничего. `as_manager` — у пользователя «Отгрузка» на правку: он заводит отправку
    с № УПД и сроком (сразу «Упакована»); кладовщик упаковывает из склада ГП (остаётся «Собрана»).
    """

    idempotency_key: str
    project: str
    lines: tuple[ProjectShipLine, ...]
    addressee_version: int
    addressee_fields: dict
    deadline: date | None
    upd_number: str
    upd_date: date | None
    upd_file: UpdUpload | None
    ship: bool
    as_manager: bool
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ProjectShipResult:
    shipment: Shipment
    why: str
    replayed: bool = False


# Отправка «Отгрузить» получает id из ключа повтора: первичный ключ сам не даст завести её дважды.
PROJECT_SHIP_NAMESPACE = uuid5(NAMESPACE_URL, "erp-next:shipping:project-ship")


def project_ship_id(idempotency_key: str) -> UUID:
    return uuid5(PROJECT_SHIP_NAMESPACE, idempotency_key)


def project_ship_fingerprint(command: ProjectShipCommand) -> str:
    """Отпечаток тела запроса: тот же ключ с другим телом — отказ, а не чужой результат."""
    body = {
        "actor": str(command.actor.id), "project": str(command.project or "").strip(),
        "lines": [[str(line.good_id), line.version, format(Decimal(line.quantity).normalize(), "f")] for line in command.lines],
        "addressee_version": command.addressee_version,
        "addressee": {key: command.addressee_fields[key] for key in sorted(command.addressee_fields)},
        "deadline": command.deadline.isoformat() if command.deadline else None,
        "upd_number": str(command.upd_number or "").strip(),
        "upd_date": command.upd_date.isoformat() if command.upd_date else None,
        "upd_file": None if command.upd_file is None else [
            command.upd_file.file_name, command.upd_file.content_type, sha256(command.upd_file.content).hexdigest(),
        ],
        "ship": command.ship, "as_manager": command.as_manager,
    }
    return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _replayed(shipment: Shipment) -> ProjectShipResult:
    why = ""
    if shipment.status == ShipmentStatus.SHIPPED:
        try:
            shipment.close()
        except ShippingValidationError as error:
            why = str(error)
    return ProjectShipResult(shipment=shipment, why=why, replayed=True)


def ship_project(command: ProjectShipCommand, uow: UnitOfWork) -> ProjectShipResult:
    key = str(command.idempotency_key or "").strip()
    if not key:
        raise ShippingValidationError("Не передан ключ повтора (Idempotency-Key)")
    project = str(command.project or "").strip()
    if not project:
        raise ShippingValidationError("Не указан проект")
    if command.upd_file is not None:
        _check_upd_file(command.upd_file.content)
    if not command.as_manager and (command.addressee_fields or command.upd_number.strip() or command.upd_date or command.deadline):
        raise ShippingPermissionError("Карточку проекта, № УПД и срок отгрузки заполняет менеджер (раздел «Отгрузка»)")
    fingerprint = project_ship_fingerprint(command)
    shipment_id = project_ship_id(key)
    with uow:
        existing = uow.shipments.get(shipment_id)
        if existing is not None:
            if (uow.shipments.created_details(shipment_id) or {}).get("fingerprint") != fingerprint:
                raise ConcurrentShippingUpdate("Этот ключ повтора уже использован для другой отгрузки — обновите страницу")
            return _replayed(existing)

        # Всё проверяется до первой записи: версия КБ, проект, остаток, версия карточки.
        if not command.lines:
            raise ShippingValidationError("Не выбрано ни одной позиции с количеством")
        if len({line.good_id for line in command.lines}) != len(command.lines):
            raise ShippingValidationError("Позиция готовой продукции указана дважды")
        requested: list[RequestedGood] = []
        for line in command.lines:
            good = _good(line.good_id, line.version, uow)
            if good.project != project:
                raise ShippingValidationError(f"{good.item_index} не относится к проекту {project}")
            quantity = Decimal(line.quantity)
            if not quantity > 0:
                raise ShippingValidationError(f"{good.item_index}: количество должно быть больше нуля")
            if quantity > good.remaining:
                raise ConcurrentShippingUpdate(f"{good.item_index}: на складе ГП только {format(good.remaining.normalize(), 'f')} {good.unit}")
            requested.append(RequestedGood(good_id=good.id, quantity=quantity))
        current = uow.addressees.get(project) or ProjectAddressee.empty(project)
        if current.version != command.addressee_version:
            raise ConcurrentShippingUpdate("Адресат проекта изменился после загрузки формы")

        addressee = current
        if command.addressee_fields:
            _, addressee = _set_project_addressee(SetProjectAddresseeCommand(
                project=project, fields=command.addressee_fields, expected_version=command.addressee_version,
                actor=command.actor, now=command.now,
            ), uow)
        customer = next((good.customer for good in uow.goods.by_project(project) if good.customer), "")
        # Адресат не копируется, а наследуется: правка карточки дойдёт до отправки, пока она открыта.
        shipment = _create_shipment(CreateShipmentCommand(
            customer=customer, receiver="" if addressee.receiver or customer else project,
            shipping_point="", contact="", phone="", terms=None, carrier="", upd_type=None,
            deadline=command.deadline if command.as_manager else None,
            upd_number=command.upd_number.strip() if command.as_manager else "",
            upd_date=command.upd_date if command.as_manager and command.upd_number.strip() else None,
            upd_url="", note="", lines=tuple(requested), actor=command.actor, now=command.now,
            no_docs=not command.as_manager,
        ), uow, shipment_id=shipment_id, details={"idempotency_key": key, "fingerprint": fingerprint, "project": project})
        if command.upd_file is not None:
            shipment = _attach_upd(
                shipment, command.upd_file.file_name, command.upd_file.content_type, command.upd_file.content,
                command.actor, command.now, uow,
            )
        why = ""
        if command.ship:
            shipped = _mark_shipped(shipment, command.actor, command.now, uow)
            shipment, why = shipped.shipment, shipped.why
        uow.commit()
        return ProjectShipResult(shipment=shipment, why=why)


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
            uow.goods.add(good.put_back(quantity), _context(command.actor, command.now, shipment_id=shipment.id))
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.cancelled", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


ROLLBACK_SHIPPED_DENIED = "Откатить «Отгружено» может только руководитель (администратор)"


def rollback_shipped(command: RollbackShippedCommand, uow: UnitOfWork) -> Shipment:
    """Откат «Отгружено» (ошибочно нажали «Отгрузить»): отправка снова «Упакована», её штуки по КБ — из «Отгружено» в
    «Упаковано» ходами отката с причиной. Только руководитель; возврат от заказчика — не это (вне регистра)."""
    if not command.allowed:
        raise ShippingPermissionError(ROLLBACK_SHIPPED_DENIED)
    reason = str(command.reason or "").strip()
    if not reason:
        raise ShippingValidationError("Укажите причину отката")
    with uow:
        shipment = _shipment(command.shipment_id, command.expected_version, uow)
        updated = shipment.unship()
        back: dict[UUID, Decimal] = {}
        for line in shipment.lines:
            if line.picked > 0:
                back[line.finished_good_id] = back.get(line.finished_good_id, Decimal("0")) + line.picked
        for good_id, quantity in back.items():
            good = uow.goods.get(good_id)
            if good is None:
                raise ShippingValidationError("Позиция готовой продукции больше не существует")
            uow.goods.add(good.undispatch(quantity), _context(
                command.actor, command.now, shipment_id=shipment.id, reason=reason, correction=True,
            ))
        uow.shipments.add(updated)
        _audit(shipment.id, "shipping.ship_rolled_back", command.actor, command.now, {
            "reason": reason, "from": shipment.status.value, "to": updated.status.value,
            "quantities": {str(key): _plain(value) for key, value in back.items()},
        }, uow)
        uow.commit()
        return updated
