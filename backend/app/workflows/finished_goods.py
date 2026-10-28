"""Регистр готовой продукции поперёк модулей (владелец 12.10.2026): «нужно новый блок, где будет список того, что в
производстве. Кладовщик будет выбирать позиции и принимать их к себе… главное, чтобы статусы были сквозные».

Штуки КБ идут В производстве → Принято складом → Упаковано → Отгружено. Остаток по состояниям — счётчики строки ГП
(модуль Shipping), каждый ход пишется в журнал `finished_goods_moves` в той же транзакции. Здесь — то, что требует
производства рядом с отгрузкой:

* «сдано цехом» по КБ (`shop_delivered`): по живым заданиям КБ — у площадки (ЖУК) «Сдано N шт», у цеха годные финальной
  операции; «Выполнено» — не меньше тиража задания. Отменённые задания не считаются;
* «Из производства» (`from_production`): КБ с живыми заданиями, которые склад принял не целиком — сдано, принято,
  можно принять сейчас;
* приёмка (`accept_batch`, `accept_one`): не больше сданного цехом. КБ без живых заданий в ERP (сделана до ERP, задания
  закрыты ручным этапом) принимается до тиража — с причиной в журнале (решено агентом 12.10 — проверить); нет данных
  отправки проекта (получатель, адрес, телефон, вариант УПД, файл бумажной УПД) — уведомление менеджеру проекта, одно
  на приёмку и проект, в той же транзакции (владелец 14.10.2026);
* сквозной статус проекта (`project_status`): по КБ штуки в каждом состоянии, отправки и журнал.

Всё читается и пишется в ОДНОЙ сессии (`SqlAlchemyFinishedGoodsFlowUnitOfWork`, как `stage_cascade`): пакет приёмки
фиксируется целиком или не фиксируется вовсе."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.production.domain import ProductionStatus, ProductionTask, ProductionValidationError
from app.modules.production.infrastructure import (
    SqlAlchemyDeliveryRepository,
    SqlAlchemyOutputRepository,
    SqlAlchemyProductionRepository,
)
from app.modules.production.plan import kit_stage_key, normalize_project_number
from app.modules.shipping.application import (
    ConcurrentShippingUpdate,
    accept_into_stock,
    notify_dispatch_gaps,
)
from app.modules.shipping.domain import (
    FinishedGood,
    GoodMove,
    GoodsRegister,
    Shipment,
    ShipmentStatus,
    ShippingValidationError,
    journal_balance,
)
from app.modules.shipping.infrastructure import (
    SqlAlchemyFinishedGoodsRepository,
    SqlAlchemyProjectAddresseeRepository,
    SqlAlchemyShipmentRepository,
)
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository

ZERO = Decimal("0")
DELIVERED_BASIS = "сдано цехом"
RUN_BASIS = "тираж"
NO_TASKS_REASON = "КБ без заданий в ERP: принято до тиража"
OUTSIDE_TASKS_REASON = "Цех сдал по заданиям меньше: принято до тиража вне заданий"
ACCEPT_BATCH_NAMESPACE = uuid5(NAMESPACE_URL, "erp-next:shipping:accept-from-production")
BATCH_EVENT = "shipping.goods_accepted_batch"


# ── One session for the whole command ───────────────────────────────────────────────────────────────────────────


class SqlAlchemyFinishedGoodsFlowUnitOfWork:
    """Задания, сдачи площадок и выход операций (Production), строки ГП, их журнал, отправки и данные отправки
    (Shipping), аудит и очередь уведомлений — в одной сессии SQLAlchemy."""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyFinishedGoodsFlowUnitOfWork:
        self.committed = False
        self.session = self.session_factory()
        self.production = SqlAlchemyProductionRepository(self.session)
        self.deliveries = SqlAlchemyDeliveryRepository(self.session)
        self.outputs = SqlAlchemyOutputRepository(self.session)
        self.goods = SqlAlchemyFinishedGoodsRepository(self.session)
        self.shipments = SqlAlchemyShipmentRepository(self.session)
        self.addressees = SqlAlchemyProjectAddresseeRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self.committed:
            self.session.rollback()
        self.session.close()

    def batch_details(self, batch_id: UUID) -> dict | None:
        """Подробности уже записанного пакета приёмки (ключ повтора, отпечаток тела) или None."""
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        details = self.session.scalar(select(AuditEventRow.details).where(
            AuditEventRow.aggregate_id == batch_id, AuditEventRow.event_type == BATCH_EVENT,
        ).limit(1))
        return dict(details) if isinstance(details, dict) else None

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        self.session.commit()
        self.committed = True


def flow_uow_factory(shipping_uow_factory) -> Callable[[], SqlAlchemyFinishedGoodsFlowUnitOfWork] | None:
    """Общая сессия над той же базой, что у модуля Shipping; None — Shipping не на SQLAlchemy."""
    if shipping_uow_factory is None:
        return None
    sessions = getattr(shipping_uow_factory(), "session_factory", None)
    if not callable(sessions):
        return None
    return lambda: SqlAlchemyFinishedGoodsFlowUnitOfWork(sessions)


# ── «Сдано цехом» по КБ ─────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskShare:
    number: str
    status: str
    site: str
    quantity: int
    delivered: Decimal


@dataclass(frozen=True)
class KbProduction:
    """Живые задания одной КБ: тираж по заданиям и сколько из него цех сдал."""

    key: str
    project: str
    item_index: str
    name: str
    run: Decimal
    delivered: Decimal
    tasks: tuple[TaskShare, ...] = ()


def task_delivered(task: ProductionTask, delivered_by_site: Mapping[UUID, int], final_good: Mapping[UUID, Decimal]) -> Decimal:
    """Сдано по заданию: площадке (ЖУК) — «Сдано N шт», цеху — годные финальной операции; «Выполнено» — не меньше тиража."""
    done = Decimal(delivered_by_site.get(task.id, 0)) if task.handed_to else Decimal(final_good.get(task.id, ZERO))
    if task.status is ProductionStatus.DONE:
        done = max(done, Decimal(task.quantity))
    return done


def shop_delivered(
    tasks: Iterable[ProductionTask], delivered_by_site: Mapping[UUID, int], final_good: Mapping[UUID, Decimal],
) -> dict[str, KbProduction]:
    """КБ (`kit_stage_key`) → тираж и сдано по её живым заданиям; отменённые задания не считаются."""
    grouped: dict[str, list[ProductionTask]] = {}
    for task in tasks:
        if task.status is ProductionStatus.CANCELLED:
            continue
        grouped.setdefault(kit_stage_key(task.project, task.item), []).append(task)
    result: dict[str, KbProduction] = {}
    for key, items in grouped.items():
        shares = tuple(
            TaskShare(task.number, task.status.value, task.handed_to, task.quantity,
                      task_delivered(task, delivered_by_site, final_good))
            for task in items
        )
        first = items[0]
        result[key] = KbProduction(
            key=key, project=normalize_project_number(first.project) or first.project.strip(),
            item_index=str(first.item or "").strip().upper(), name=first.name,
            run=sum((Decimal(task.quantity) for task in items), ZERO),
            delivered=sum((share.delivered for share in shares), ZERO),
            tasks=shares,
        )
    return result


def read_delivered(uow, tasks: Iterable[ProductionTask]) -> dict[str, KbProduction]:
    live = [task for task in tasks if task.status is not ProductionStatus.CANCELLED]
    by_site = uow.deliveries.totals([task.id for task in live if task.handed_to])
    shop = [task for task in live if not task.handed_to]
    finals: dict[UUID, UUID] = {}
    for task in shop:
        try:
            finals[task.id] = task.final_operation.id
        except ProductionValidationError:
            continue
    final_good: dict[UUID, Decimal] = {}
    for output in uow.outputs.active_for_tasks(tuple(finals)):
        if finals.get(output.task_id) == output.operation_id:
            final_good[output.task_id] = final_good.get(output.task_id, ZERO) + Decimal(output.good_quantity)
    return shop_delivered(live, by_site, final_good)


def _goods_by_key(goods: Iterable[FinishedGood]) -> dict[str, FinishedGood]:
    return {kit_stage_key(good.project, good.item_index): good for good in goods}


# ── «Из производства» ───────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FromProductionRow:
    project: str
    item_index: str
    name: str
    unit: str
    needed: Decimal
    delivered: Decimal
    accepted: Decimal
    good: FinishedGood | None
    tasks: tuple[TaskShare, ...]

    @property
    def remaining(self) -> Decimal:
        """Сдано цехом, склад ещё не принял — можно принять сейчас."""
        return max(self.delivered - self.accepted, ZERO)

    @property
    def in_production(self) -> Decimal:
        return max(self.needed - self.accepted, ZERO)


def from_production_rows(production: Mapping[str, KbProduction], goods: Iterable[FinishedGood]) -> list[FromProductionRow]:
    """КБ с живыми заданиями, которые склад принял не целиком: сначала то, что можно принять сейчас."""
    by_key = _goods_by_key(goods)
    rows = []
    for key, kb in production.items():
        good = by_key.get(key)
        row = FromProductionRow(
            project=good.project if good else kb.project, item_index=good.item_index if good else kb.item_index,
            name=(good.name if good else "") or kb.name, unit=good.unit if good else "шт",
            needed=good.ordered if good else kb.run, delivered=kb.delivered,
            accepted=good.accepted if good else ZERO, good=good, tasks=kb.tasks,
        )
        if row.remaining > 0 or row.in_production > 0:
            rows.append(row)
    return sorted(rows, key=lambda row: (row.remaining <= 0, normalize_project_number(row.project), row.item_index))


def from_production(uow_factory) -> list[FromProductionRow]:
    with uow_factory() as uow:
        tasks = [*uow.production.with_status(ProductionStatus.RUNNING), *uow.production.with_status(ProductionStatus.DONE)]
        production = read_delivered(uow, tasks)
        goods = uow.goods.list(limit=1_000_000)
    return from_production_rows(production, goods)


# ── Приёмка ─────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AcceptLine:
    project: str
    item_index: str
    quantity: Decimal
    # Место хранения на складе ГП; пусто — прежнее место КБ.
    location: str = ""


@dataclass(frozen=True)
class AcceptBatchCommand:
    idempotency_key: str
    lines: tuple[AcceptLine, ...]
    received_at: date | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class AcceptBatchResult:
    goods: tuple[FinishedGood, ...]
    replayed: bool = False


def accept_batch_id(idempotency_key: str) -> UUID:
    return uuid5(ACCEPT_BATCH_NAMESPACE, idempotency_key)


def accept_batch_fingerprint(command: AcceptBatchCommand) -> str:
    body = {
        "actor": str(command.actor.id),
        "lines": [[kit_stage_key(line.project, line.item_index), format(Decimal(line.quantity).normalize(), "f"),
                   *([line.location.strip()] if line.location.strip() else [])]
                  for line in command.lines],
        "received_at": command.received_at.isoformat() if command.received_at else None,
    }
    return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _limit(kb: KbProduction | None, good: FinishedGood) -> tuple[Decimal, str, str]:
    """(сколько всего можно принять, как это назвать, причина в журнале). КБ с заданиями цеха — до тиража, даже если цех
    сдал по заданиям меньше (владелец 28.10.2026: «всё, что в работе, можно принять»; причина — в журнале)."""
    if kb is not None:
        if kb.delivered >= good.ordered:
            return kb.delivered, DELIVERED_BASIS, ""
        return good.ordered, RUN_BASIS, OUTSIDE_TASKS_REASON
    return good.ordered, RUN_BASIS, NO_TASKS_REASON


def _good_for(uow, project: str, item_index: str, kb: KbProduction | None, actor: Actor, now: datetime) -> FinishedGood:
    """Строка ГП КБ; нет её (проект завели до очереди ГП) — заводится по заданиям, иначе отказ."""
    key = kit_stage_key(project, item_index)
    goods = list(uow.goods.for_project(project))
    good = next((item for item in goods if kit_stage_key(item.project, item.item_index) == key), None)
    if good is not None:
        return good
    if kb is None:
        raise ShippingValidationError(f"{project} {item_index}: КБ нет ни в готовой продукции, ни в заданиях цеха")
    good = FinishedGood.create(
        project=goods[0].project if goods else kb.project, item_index=kb.item_index,
        customer=next((item.customer for item in goods if item.customer), ""), article="",
        name=kb.name or kb.item_index, ordered=kb.run, unit="шт",
    )
    uow.goods.add(good)
    uow.audit.add(AuditEvent(
        id=uuid5(good.id, "registered"), aggregate_id=good.id, event_type="shipping.good_registered",
        actor=actor, occurred_at=now, details={"project": good.project, "item_index": good.item_index, "source": "приёмка из производства"},
    ))
    return good


def accept_batch(command: AcceptBatchCommand, uow_factory) -> AcceptBatchResult:
    """Кладовщик отметил несколько КБ с количеством и принял одной кнопкой: все строки или ни одной. Повтор с тем же
    ключом не принимает второй раз (id ходов журнала выводятся из ключа), тот же ключ с другим телом — отказ."""
    key = str(command.idempotency_key or "").strip()
    if not key:
        raise ShippingValidationError("Не передан ключ повтора (Idempotency-Key)")
    if not command.lines:
        raise ShippingValidationError("Не выбрано ни одной КБ с количеством")
    keys = [kit_stage_key(line.project, line.item_index) for line in command.lines]
    if len(set(keys)) != len(keys):
        raise ShippingValidationError("КБ указана дважды")
    batch_id = accept_batch_id(key)
    fingerprint = accept_batch_fingerprint(command)
    received_at = command.received_at or command.now.date()
    with uow_factory() as uow:
        done = uow.batch_details(batch_id)
        if done is not None:
            if done.get("fingerprint") != fingerprint:
                raise ConcurrentShippingUpdate("Этот ключ повтора уже использован для другой приёмки — обновите страницу")
            goods = tuple(_good_for(uow, line.project, line.item_index, None, command.actor, command.now) for line in command.lines)
            return AcceptBatchResult(goods=goods, replayed=True)
        production: dict[str, KbProduction] = {}
        for project in dict.fromkeys(normalize_project_number(line.project) for line in command.lines):
            production.update(read_delivered(uow, uow.production.for_project(project)))
        accepted = []
        for line, kb_key in zip(command.lines, keys):
            kb = production.get(kb_key)
            good = _good_for(uow, line.project.strip(), line.item_index.strip(), kb, command.actor, command.now)
            limit, basis, reason = _limit(kb, good)
            accepted.append(accept_into_stock(
                uow, good, line.quantity, received_at=received_at, actor=command.actor, now=command.now,
                limit=limit, limit_basis=basis, reason=reason, id_seed=batch_id, location=line.location,
            ))
        uow.audit.add(AuditEvent(
            id=uuid5(batch_id, "batch"), aggregate_id=batch_id, event_type=BATCH_EVENT, actor=command.actor,
            occurred_at=command.now, details={
                "idempotency_key": key, "fingerprint": fingerprint,
                "lines": [[good.project, good.item_index, format(Decimal(line.quantity).normalize(), "f")]
                          for good, line in zip(accepted, command.lines)],
            },
        ))
        by_project: dict[str, list[str]] = {}
        for good in accepted:
            by_project.setdefault(good.project, []).append(good.item_index)
        for project, items in by_project.items():
            notify_dispatch_gaps(uow, project, items, f"batch:{batch_id}", command.now)
        uow.commit()
        return AcceptBatchResult(goods=tuple(accepted))


@dataclass(frozen=True)
class AcceptOneCommand:
    good_id: UUID
    expected_version: int
    quantity: Decimal
    received_at: date | None
    actor: Actor
    now: datetime
    location: str = ""


def accept_one(command: AcceptOneCommand, uow_factory) -> FinishedGood:
    """«Принять» одной КБ (экран «Отгрузка»): тот же предел, что у пакета — не больше сданного цехом."""
    with uow_factory() as uow:
        good = uow.goods.get(command.good_id)
        if good is None:
            raise KeyError(f"Finished good not found: {command.good_id}")
        if good.version != command.expected_version:
            raise ConcurrentShippingUpdate("Позиция готовой продукции изменилась после загрузки формы")
        kb = read_delivered(uow, uow.production.for_project(good.project)).get(kit_stage_key(good.project, good.item_index))
        limit, basis, reason = _limit(kb, good)
        updated = accept_into_stock(
            uow, good, command.quantity, received_at=command.received_at or command.now.date(), actor=command.actor,
            now=command.now, limit=limit, limit_basis=basis, reason=reason, location=command.location,
        )
        notify_dispatch_gaps(uow, updated.project, (updated.item_index,), f"good:{updated.id}:v{updated.version}", command.now)
        uow.commit()
        return updated


# ── Сквозной статус проекта ─────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KbShipment:
    shipment_id: UUID
    number: str
    status: str
    picked: Decimal


@dataclass(frozen=True)
class KbStatus:
    project: str
    item_index: str
    name: str
    unit: str
    good: FinishedGood | None
    register: GoodsRegister
    has_tasks: bool
    journal_ok: bool
    shipments: tuple[KbShipment, ...] = ()


@dataclass(frozen=True)
class ProjectGoodsStatus:
    project: str
    kbs: tuple[KbStatus, ...]
    moves: tuple[GoodMove, ...] = ()
    shipment_numbers: dict[UUID, str] = field(default_factory=dict)


def project_status_of(
    project: str, production: Mapping[str, KbProduction], goods: Iterable[FinishedGood], shipments: Iterable[Shipment],
    journal: Mapping[UUID, Mapping[tuple[str, str], Decimal]], moves: Iterable[GoodMove] = (),
) -> ProjectGoodsStatus:
    """По КБ проекта: штуки в каждом состоянии, сдано цехом, отправки и согласие журнала со счётчиками."""
    by_key = _goods_by_key(goods)
    shipments = tuple(shipments)
    keys = list(dict.fromkeys([*by_key, *production]))
    kbs = []
    for key in keys:
        good, kb = by_key.get(key), production.get(key)
        register = GoodsRegister(
            needed=good.ordered if good else kb.run,
            accepted_total=good.accepted if good else ZERO,
            packed_total=good.shipped if good else ZERO,
            shipped_total=good.sent if good else ZERO,
            delivered=kb.delivered if kb else None,
        )
        totals = journal.get(good.id, {}) if good else {}
        ok = good is None or journal_balance(dict(totals)) == (good.accepted, good.shipped, good.sent)
        mine = tuple(sorted((
            KbShipment(shipment.id, shipment.number, shipment.status.value, line.picked)
            for shipment in shipments for line in shipment.lines
            if good is not None and line.finished_good_id == good.id
            and (line.picked > 0 or shipment.status is not ShipmentStatus.CANCELLED)
        ), key=lambda item: item.number))
        kbs.append(KbStatus(
            project=good.project if good else kb.project, item_index=good.item_index if good else kb.item_index,
            name=(good.name if good else "") or (kb.name if kb else ""), unit=good.unit if good else "шт",
            good=good, register=register, has_tasks=kb is not None, journal_ok=ok, shipments=mine,
        ))
    kbs.sort(key=lambda item: item.item_index)
    return ProjectGoodsStatus(
        project=normalize_project_number(project) or project.strip(), kbs=tuple(kbs), moves=tuple(moves),
        shipment_numbers={shipment.id: shipment.number for shipment in shipments},
    )


def project_status(project: str, uow_factory, moves_limit: int = 500) -> ProjectGoodsStatus:
    with uow_factory() as uow:
        goods = list(uow.goods.for_project(project))
        production = read_delivered(uow, uow.production.for_project(project))
        shipments = list(uow.shipments.for_project(project))
        journal = uow.goods.journal_totals([good.id for good in goods]) if goods else {}
        moves = uow.goods.moves_for_project(project, limit=moves_limit)
    return project_status_of(project, production, goods, shipments, journal, moves)
