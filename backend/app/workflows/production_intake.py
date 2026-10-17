"""«Приёмка с производства» (владелец 14.10.2026): «там будет список всего, что у нас в работе, и Алексей должен мочь
это принимать» — частичная приёмка в порядке вещей.

Только чтение. Строка — КБ открытого проекта (реестр проектов плана: не «Отменён», не «Закрыт»), в том числе ещё не
запущенная, пока заказанное не уехало целиком (`sent >= needed` — строки нет). По КБ: этап лестницы (`KIT_STAGES`, как в
производственном плане), сколько нужно, запущено в заданиях, сдано цехом, принято складом, упаковано, уехало и сколько
можно принять сейчас (`remaining`: сдано − принято; без заданий на этапе «В работе» и дальше — заказано − принято). Приёмка — `finished_goods.accept_batch` / `accept_one`.

Читается одной сессией и пачками: реестр, журнал плана, наборы и карточки ТК открытых проектов, живые задания этих
проектов, их коробки, ОТК, сдачи и выход, ГП — без сборки всего производственного плана (снабжение, календарь)."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.fulfillment.infrastructure import FulfillmentRequestRow
from app.modules.production.domain import ProductionStatus, ProductionTask
from app.modules.production.infrastructure import (
    ProductionBoxRow,
    ProductionTaskRow,
    SqlAlchemyDeliveryRepository,
    SqlAlchemyOutputRepository,
    SqlAlchemyPlanEventRepository,
    SqlAlchemyPlanProjectRepository,
    SqlAlchemyProductionRepository,
    SqlAlchemyQcRepository,
)
from app.modules.production.plan import (
    PlanEvent,
    PlanProjectSnapshot,
    ProjectJournal,
    kit_stage_key,
    normalize_project_number,
    project_stage,
    project_terminal,
    summarize_journal,
    summarize_kit_marks,
)
from app.modules.production.traceability import BoxStatus
from app.modules.shipping.domain import FinishedGood
from app.modules.shipping.infrastructure import SqlAlchemyFinishedGoodsRepository
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.modules.techcards.kitset_infrastructure import SqlAlchemyKitsetRepository
from app.modules.techcards.kitsets import Kitset
from app.modules.techcards.project_cards import TechcardStatus

from .finished_goods import KbProduction, TaskShare, read_delivered
from .production_plan import kit_stage_facts, project_kits
from .project_registry import CLOSED_PROJECT_STATUS

ZERO = Decimal("0")
LIVE_TASKS = (ProductionStatus.RUNNING.value, ProductionStatus.DONE.value)
# Заявки на комплект, которые лестница читает как «КБ набрана» (тот же набор, что в `kit_stage_facts`).
PICKED_KITS = (
    FulfillmentStatus.COMPLETE, FulfillmentStatus.READY, FulfillmentStatus.ISSUED,
    FulfillmentStatus.RETURNED, FulfillmentStatus.DONE,
)


# ── Строка списка ───────────────────────────────────────────────────────────────────────────────────────────────


# Этапы, на которых КБ без заданий ERP принимается до заказанного (решено агентом 14.10 — проверить).
ACCEPT_WITHOUT_TASKS_STAGES = frozenset({"В работе", "ОТК", "Готово к отгрузке", "На отгрузке"})


@dataclass(frozen=True)
class IntakeRow:
    project: str
    customer: str
    deadline: date | None
    item_index: str
    name: str
    unit: str
    stage: str
    needed: Decimal
    launched: Decimal
    delivered: Decimal
    accepted: Decimal
    packed: Decimal
    sent: Decimal
    good: FinishedGood | None = None
    tasks: tuple[TaskShare, ...] = ()

    @property
    def remaining(self) -> Decimal:
        """Можно принять сейчас — тот же предел, что у `finished_goods.accept_batch` (`_limit`): есть задания цеха —
        сдано цехом минус принятое; заданий нет, а КБ по этапу уже в работе (цех ведёт её вне заданий ERP) — заказанное
        минус принятое (владелец 14.10: «почему Алексей не может тыкнуть на те, что в работе»)."""
        if self.tasks or self.stage not in ACCEPT_WITHOUT_TASKS_STAGES:
            return max(self.delivered - self.accepted, ZERO)
        return max(self.needed - self.accepted, ZERO)

    @property
    def location(self) -> str:
        """Место хранения ГП (появится в регистре ГП); пока поля нет — пусто."""
        return str(getattr(self.good, "location", "") or "") if self.good is not None else ""


# Факты лестницы, прочитанные колонками (без документа ТК и строк заявок): `kit_stage_facts` читает только эти поля.
@dataclass(frozen=True)
class CardFact:
    project: str
    item_index: str
    status: TechcardStatus
    updated_at: datetime
    deadline: date | None = None


@dataclass(frozen=True)
class KitRequestFact:
    purpose: str
    status: FulfillmentStatus


@dataclass(frozen=True)
class BoxFact:
    id: UUID
    task_id: UUID
    operation_id: UUID
    status: BoxStatus


@dataclass(frozen=True)
class IntakeFacts:
    projects: tuple[PlanProjectSnapshot, ...] = ()
    events: tuple[PlanEvent, ...] = ()
    kitsets: tuple[Kitset, ...] = ()
    cards: tuple[CardFact, ...] = ()
    tasks: tuple[ProductionTask, ...] = ()
    production: Mapping[str, KbProduction] = field(default_factory=dict)
    goods: tuple[FinishedGood, ...] = ()
    kit_requests: tuple[KitRequestFact, ...] = ()
    boxes: tuple[BoxFact, ...] = ()
    inspected: Collection[UUID] = frozenset()
    worked: Collection[UUID] = frozenset()


def is_closed(snapshot: PlanProjectSnapshot, journal: ProjectJournal | None) -> bool:
    """Отменён (запись журнала «Статус проекта» сильнее реестра) или «Закрыт» в реестре — как у списка проектов заявок."""
    if journal is not None and journal.status is not None:
        return bool(journal.status)
    return (
        snapshot.terminal or bool(project_terminal(snapshot, None))
        or str(snapshot.status or "").strip() == CLOSED_PROJECT_STATUS
    )


def open_projects(projects: Iterable[PlanProjectSnapshot], events: Iterable[PlanEvent]) -> dict[str, PlanProjectSnapshot]:
    """№ проекта (канон) → строка реестра открытых проектов."""
    journal = summarize_journal(events)
    result: dict[str, PlanProjectSnapshot] = {}
    for item in projects:
        canon = normalize_project_number(item.project)
        if canon and canon not in result and not is_closed(item, journal.get(canon)):
            result[canon] = item
    return result


def natural_key(text: str) -> tuple:
    """КБ2 раньше КБ10: числа сравниваются числами."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text) if part)


def intake_rows(facts: IntakeFacts) -> list[IntakeRow]:
    """КБ открытых проектов, у которых ещё не всё заказанное уехало: по сроку проекта (без срока — в конце), проекту,
    КБ по номеру."""
    events = tuple(facts.events)
    projects = open_projects(facts.projects, events)
    journal = summarize_journal(events)
    stage_facts, marks = kit_stage_facts(
        cards=facts.cards, tasks=facts.tasks, fulfillment=facts.kit_requests, goods=facts.goods,
        marks=summarize_kit_marks(events), boxes=facts.boxes, inspected=facts.inspected, worked=facts.worked,
    )
    kitsets = {normalize_project_number(kitset.project): kitset for kitset in facts.kitsets}
    cards_by_project: dict[str, list[CardFact]] = {}
    for card in facts.cards:
        cards_by_project.setdefault(normalize_project_number(card.project), []).append(card)
    goods_by_project: dict[str, list[FinishedGood]] = {}
    for good in facts.goods:
        goods_by_project.setdefault(normalize_project_number(good.project), []).append(good)
    production_by_project: dict[str, list[KbProduction]] = {}
    for kb in facts.production.values():
        production_by_project.setdefault(normalize_project_number(kb.project), []).append(kb)

    rows: list[IntakeRow] = []
    for canon, snapshot in projects.items():
        kitset = kitsets.get(canon)
        cards = cards_by_project.get(canon, [])
        goods = {kit_stage_key(good.project, good.item_index): good for good in goods_by_project.get(canon, [])}
        launched = production_by_project.get(canon, [])
        assemblies = {assembly.index.strip().upper(): assembly for assembly in (kitset.assemblies if kitset else ())}
        indexes = list(dict.fromkeys([
            *project_kits(snapshot.project, assemblies, cards),
            *(good.item_index.strip().upper() for good in goods.values()),
            *(kb.item_index for kb in launched),
        ]))
        if not indexes:
            continue
        stage = project_stage(snapshot.project, indexes, stage_facts, "", marks)
        stages = {kit.item_index: kit.status for kit in stage.kits}
        project_journal = journal.get(canon)
        card_deadlines = [card.deadline for card in cards if card.deadline]
        deadline = (
            (project_journal.deadline if project_journal else None) or snapshot.deadline
            or (min(card_deadlines) if card_deadlines else None)
        )
        customer = snapshot.customer or (kitset.customer if kitset else "") or next(
            (good.customer for good in goods.values() if good.customer), "",
        )
        for item_index in indexes:
            key = kit_stage_key(snapshot.project, item_index)
            good, kb, assembly = goods.get(key), facts.production.get(key), assemblies.get(item_index)
            needed = good.ordered if good else (assembly.run if assembly and assembly.run > 0 else (kb.run if kb else ZERO))
            sent = good.sent if good else ZERO
            # Всё заказанное уехало — КБ ушла из приёмки; КБ без известного тиража остаётся (решено агентом 14.10).
            if needed > 0 and sent >= needed:
                continue
            # Этап «Отгружено» (в т. ч. отгружено ещё в Google или отмечено вручную) — принимать нечего (владелец 14.10).
            if stages.get(item_index, "") == "Отгружено":
                continue
            rows.append(IntakeRow(
                project=snapshot.project, customer=customer, deadline=deadline, item_index=item_index,
                name=(good.name if good else "") or (assembly.name if assembly else "") or (kb.name if kb else "") or item_index,
                unit=good.unit if good else "шт", stage=stages.get(item_index, ""),
                needed=needed, launched=kb.run if kb else ZERO, delivered=kb.delivered if kb else ZERO,
                accepted=good.accepted if good else ZERO, packed=good.shipped if good else ZERO, sent=sent,
                good=good, tasks=kb.tasks if kb else (),
            ))
    return sorted(rows, key=lambda row: (
        row.deadline is None, row.deadline or date.max, normalize_project_number(row.project), natural_key(row.item_index),
    ))


# ── Чтение одной сессией ────────────────────────────────────────────────────────────────────────────────────────


class SqlAlchemyIntakeUnitOfWork:
    """Реестр и журнал плана, задания, сдачи, выход и ОТК (Production), наборы и ТК (Techcards), заявки на комплект
    (Fulfillment) и ГП (Shipping) — в одной сессии, только чтение."""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> SqlAlchemyIntakeUnitOfWork:
        self.session = self.session_factory()
        self.production = SqlAlchemyProductionRepository(self.session)
        self.deliveries = SqlAlchemyDeliveryRepository(self.session)
        self.outputs = SqlAlchemyOutputRepository(self.session)
        self.qc = SqlAlchemyQcRepository(self.session)
        self.plan_projects = SqlAlchemyPlanProjectRepository(self.session)
        self.plan_events = SqlAlchemyPlanEventRepository(self.session)
        self.kitsets = SqlAlchemyKitsetRepository(self.session)
        self.goods = SqlAlchemyFinishedGoodsRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        self.session.rollback()
        self.session.close()

    def _session(self) -> Session:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        return self.session

    def live_tasks(self, projects: Collection[str]) -> list[ProductionTask]:
        """Живые задания («Запущено», «Выполнено») открытых проектов: отбор одним запросом по ключу проекта."""
        rows = self._session().execute(
            select(ProductionTaskRow.id, ProductionTaskRow.project)
            .where(ProductionTaskRow.status.in_(LIVE_TASKS))
            .order_by(ProductionTaskRow.created_at, ProductionTaskRow.id)
        )
        ids = [task_id for task_id, project in rows if normalize_project_number(project) in projects]
        return [task for task in (self.production.get(task_id) for task_id in ids) if task is not None]

    def cards(self, projects: Collection[str]) -> list[CardFact]:
        row = ProjectTechcardRow
        found = self._session().execute(select(row.project, row.item_index, row.status, row.updated_at, row.deadline))
        return [
            CardFact(project, item_index, TechcardStatus(status), updated_at, deadline)
            for project, item_index, status, updated_at, deadline in found
            if normalize_project_number(project) in projects
        ]

    def kit_requests(self) -> list[KitRequestFact]:
        row = FulfillmentRequestRow
        found = self._session().execute(
            select(row.purpose, row.status).where(row.status.in_([status.value for status in PICKED_KITS]))
        )
        return [KitRequestFact(purpose or "", FulfillmentStatus(status)) for purpose, status in found]

    def boxes(self, task_ids: Collection[UUID]) -> list[BoxFact]:
        if not task_ids:
            return []
        row = ProductionBoxRow
        found = self._session().execute(
            select(row.id, row.task_id, row.operation_id, row.status).where(row.task_id.in_(list(task_ids)))
        )
        return [BoxFact(box_id, task_id, operation_id, BoxStatus(status)) for box_id, task_id, operation_id, status in found]


def intake_uow_factory(shipping_uow_factory) -> Callable[[], SqlAlchemyIntakeUnitOfWork] | None:
    """Сессия над той же базой, что у модуля Shipping; None — Shipping не на SQLAlchemy."""
    if shipping_uow_factory is None:
        return None
    sessions = getattr(shipping_uow_factory(), "session_factory", None)
    if not callable(sessions):
        return None
    return lambda: SqlAlchemyIntakeUnitOfWork(sessions)


def read_intake(uow) -> IntakeFacts:
    projects = tuple(uow.plan_projects.list())
    events = tuple(uow.plan_events.list())
    active = set(open_projects(projects, events))
    if not active:
        return IntakeFacts(projects=projects, events=events)
    tasks = tuple(uow.live_tasks(active))
    task_ids = [task.id for task in tasks]
    return IntakeFacts(
        projects=projects, events=events,
        kitsets=tuple(uow.kitsets.for_projects(
            [item.project for item in projects if normalize_project_number(item.project) in active],
        ).values()),
        cards=tuple(uow.cards(active)),
        tasks=tasks,
        production=read_delivered(uow, tasks),
        goods=tuple(good for good in uow.goods.list(limit=1_000_000) if normalize_project_number(good.project) in active),
        kit_requests=tuple(uow.kit_requests()),
        boxes=tuple(uow.boxes(task_ids)),
        inspected=frozenset(uow.qc.inspected_box_ids()),
        worked=frozenset(uow.production.worked(task_ids)),
    )


def production_intake(uow_factory) -> list[IntakeRow]:
    with uow_factory() as uow:
        facts = read_intake(uow)
    return intake_rows(facts)
