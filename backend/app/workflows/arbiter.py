"""Арбитр ERP (ARCH-TRACK-3, C2): события модулей → задачи модулям по проекту.

Целевая модель (ARCHITECTURE.md «Один проект — один трек»): арбитр по триггерам раздаёт задачи в нужные модули, итог
собирается в карточке проекта. Правила декларативные: `RULES` — {тип события журнала → вид задачи}, `KINDS` — кому
задача, как она звучит и по какому факту считается сделанной. Модули об арбитре не знают: триггеры — события
`audit_events`, которые они уже пишут в той же транзакции, что и само действие.

Событие становится задачей через outbox, как у остальных обработчиков `outbox_worker`:
1. `feed_triggers` (таймер воркера) кладёт в тему `arbiter.trigger` события журнала с типом из `RULES`, которых ещё
   нет в outbox. Id сообщения = id события, ключ — `arbiter.trigger:<id события>`: событие попадает в очередь один раз.
2. `dispatch_next_trigger` (обработчик outbox) применяет правило. Повтор того же события (сообщение переотправлено,
   outbox почищен) вторую задачу не ставит: уникальность (kind, source_event_id) в `project_tasks` и проверка до
   вставки. Пока по той же КБ открыта задача того же вида, новую не ставит (пять коробок в ГП — одна «Оформить
   отгрузку»). Дело уже сделано к моменту обработки (задание запущено раньше) — задачи нет.
3. `close_followed_up` (таймер воркера) закрывает открытые задачи, дело по которым сделано: задание цеха запущено →
   «Поставить в план» и «Задание можно запускать»; отправка с этой позицией ГП оформлена → «Оформить отгрузку».
   Остальное закрывают руками (POST /api/project-tasks/{id}/close).

Цеховые уведомления (`NOTICES`) идут тем же ходом, но вместо задачи кладут в outbox `notification.requested` по одному
сообщению на человека и событие (ключ — id события и id человека, повтор ничего не добавляет): коробка сдана → мастерам
площадки сдавшего; подтверждена → монтажникам операций, которые её берут на вход; возвращена → сдавшему.

Этапы закупки и приёмки остаются в `procurement_stage` / `receipt_stage` — арбитр их не дублирует.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.fulfillment.domain import project_of
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentRepository
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.operations.domain import ProjectTask
from app.modules.operations.infrastructure import SqlAlchemyProjectTaskRepository
from app.modules.production.domain import ProductionStatus
from app.modules.production.infrastructure import (
    ProductionBoxRow,
    ProductionOperationInputRow,
    ProductionOperationOperatorRow,
    ProductionOperationRow,
    ProductionTaskRow,
)
from app.modules.production.launchable import route_from_document, stage_name
from app.modules.production.plan import kit_key_from_purpose, normalize_project_number
from app.modules.production.traceability import BoxStatus
from app.modules.shipping.domain import ShipmentStatus
from app.modules.shipping.infrastructure import ShipmentLineRow, ShipmentRow, SqlAlchemyFinishedGoodsRepository
from app.modules.techcards.infrastructure import SqlAlchemyTechcardRepository
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import MOSCOW
from app.shared.notifications import notification_requested, publish_notification
from app.shared.persistence import AuditEventRow, OutboxMessageRow, SqlAlchemyOutboxRepository

TOPIC = "arbiter.trigger"
AUTO_CLOSER = "Арбитр ERP"
FEED_LIMIT = 500


class PoisonTriggerMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


@dataclass(frozen=True)
class Trigger:
    """Событие журнала, на которое смотрит арбитр, — ровно то, что записал модуль."""

    event_id: UUID
    event_type: str
    aggregate_id: UUID
    occurred_at: datetime
    details: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id), "event_type": self.event_type, "aggregate_id": str(self.aggregate_id),
            "occurred_at": self.occurred_at.isoformat(), "details": dict(self.details),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Trigger:
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise ValueError("details не словарь")
        event_type = str(payload["event_type"] or "").strip()
        if not event_type:
            raise ValueError("нет типа события")
        return cls(
            event_id=UUID(str(payload["event_id"])), event_type=event_type,
            aggregate_id=UUID(str(payload["aggregate_id"])),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])), details=details,
        )


@dataclass(frozen=True)
class Subject:
    """О чём задача: проект, КБ и id того, по чему арбитр увидит, что дело сделано."""

    project: str
    item_index: str
    subject_id: UUID | None


def _wall(value: datetime) -> datetime:
    """Московское время без пояса: SQLite отдаёт naive (как записано, по Москве), PostgreSQL — aware."""
    return value.astimezone(MOSCOW).replace(tzinfo=None) if value.tzinfo is not None else value


def _item(value: object) -> str:
    return str(value or "").strip().upper()


# ── О чём событие: читатели модулей (только чтение, в сессии арбитра) ─────


def _issued_techcard(session: Session, trigger: Trigger) -> Subject | None:
    card = SqlAlchemyTechcardRepository(session).get(trigger.aggregate_id)
    return None if card is None else Subject(card.project, card.item_index, card.id)


def _issued_kit(session: Session, trigger: Trigger) -> Subject | None:
    """Проект и КБ — из НАЗНАЧЕНИЯ комплекта «708К.4 · КБ1 · Жгут»; ручной запрос без « · » — без проекта (`project_of`)."""
    request = SqlAlchemyFulfillmentRepository(session).get(trigger.aggregate_id)
    if request is None:
        return None
    _project, _bar, index = kit_key_from_purpose(request.purpose).partition("|")
    return Subject(project_of(request.purpose), index, request.id)


def _good(session: Session, good_id: UUID) -> Subject | None:
    good = SqlAlchemyFinishedGoodsRepository(session).get(good_id)
    return None if good is None else Subject(good.project, good.item_index, good.id)


def _accepted_good(session: Session, trigger: Trigger) -> Subject | None:
    return _good(session, trigger.aggregate_id)


def _handed_good(session: Session, trigger: Trigger) -> Subject | None:
    """Коробка сдана из цеха на склад ГП: позиция ГП — в деталях события (агрегат — коробка)."""
    try:
        good_id = UUID(str(trigger.details.get("finished_good_id") or ""))
    except ValueError:
        return None
    return _good(session, good_id)


# ── Сделано ли дело: признаки по задачам одного вида (пачкой) ─────────────


def _same_item(task_item: str, item_index: str) -> bool:
    """КБ задания цеха «КБ1» или «708К.4.КБ1» — та же, что у позиции ГП в `handoff_box`."""
    item, kb = task_item.upper().replace(" ", ""), item_index.upper().replace(" ", "")
    return bool(kb) and (item == kb or item.endswith(f".{kb}") or item.endswith(f"-{kb}"))


@dataclass(frozen=True)
class _Launched:
    project: str  # нормализованный
    item: str
    kit: UUID | None
    at: datetime  # `_wall`


def _production_tasks(session: Session, tasks: Sequence[ProjectTask]) -> list[_Launched]:
    """Не отменённые задания цеха — по комплектам задач и все, запущенные не раньше самой старой задачи."""
    since = min((task.created_at for task in tasks), key=_wall)
    kits = [task.subject_id for task in tasks if task.subject_id is not None]
    return [
        _Launched(normalize_project_number(project), item, kit, _wall(created_at))
        for project, item, kit, created_at in session.execute(
            select(ProductionTaskRow.project, ProductionTaskRow.item, ProductionTaskRow.fulfillment_request_id,
                   ProductionTaskRow.created_at)
            .where(
                ProductionTaskRow.status != ProductionStatus.CANCELLED.value,
                or_(ProductionTaskRow.fulfillment_request_id.in_(kits), ProductionTaskRow.created_at >= since),
            )
        )
    ]


def _launched_after(row: _Launched, task: ProjectTask) -> bool:
    return row.project == task.project and _same_item(row.item, task.item_index) and row.at >= _wall(task.created_at)


def _launched_for_item(session: Session, tasks: Sequence[ProjectTask]) -> set[UUID]:
    """«Поставить в план»: по проекту и КБ запущено задание цеха после выдачи техкарты."""
    rows = _production_tasks(session, tasks)
    return {task.id for task in tasks if any(_launched_after(row, task) for row in rows)}


def _launched_for_kit(session: Session, tasks: Sequence[ProjectTask]) -> set[UUID]:
    """«Задание можно запускать»: задание запущено по этому комплекту — или по проекту и КБ после выдачи комплекта."""
    rows = _production_tasks(session, tasks)
    return {
        task.id for task in tasks
        if any((task.subject_id is not None and row.kit == task.subject_id) or _launched_after(row, task) for row in rows)
    }


def _shipment_drawn_up(session: Session, tasks: Sequence[ProjectTask]) -> set[UUID]:
    """«Оформить отгрузку»: не отменённая отправка с этой позицией ГП оформлена после приёмки."""
    goods = [task.subject_id for task in tasks if task.subject_id is not None]
    latest: dict[UUID, datetime] = {}
    for good_id, created_at in session.execute(
        select(ShipmentLineRow.finished_good_id, ShipmentRow.created_at)
        .join(ShipmentRow, ShipmentRow.id == ShipmentLineRow.shipment_id)
        .where(ShipmentLineRow.finished_good_id.in_(goods), ShipmentRow.status != ShipmentStatus.CANCELLED.value)
    ):
        latest[good_id] = max(latest.get(good_id, created_at), created_at, key=_wall)
    return {
        task.id for task in tasks
        if task.subject_id in latest and _wall(latest[task.subject_id]) >= _wall(task.created_at)
    }


# ── Правила ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskKind:
    key: str
    module: str  # раздел, которому задача: ключ доступа (`require_section`)
    title: str  # к нему дописывается «: <проект> <КБ>»
    done: Callable[[Session, Sequence[ProjectTask]], set[UUID]]

    def text(self, project: str, item_index: str) -> str:
        return f"{self.title}: {project} {item_index}".strip()


@dataclass(frozen=True)
class Rule:
    trigger: str  # тип события журнала, который модуль уже пишет
    kind: str
    subject: Callable[[Session, Trigger], Subject | None]


PLAN = TaskKind("plan_item", "production", "Поставить в план", _launched_for_item)
LAUNCH = TaskKind("launch_task", "production", "Задание можно запускать", _launched_for_kit)
SHIP = TaskKind("ship_goods", "shipping", "Оформить отгрузку", _shipment_drawn_up)
KINDS: dict[str, TaskKind] = {kind.key: kind for kind in (PLAN, LAUNCH, SHIP)}

RULES: tuple[Rule, ...] = (
    Rule("techcards.project_issued", PLAN.key, _issued_techcard),  # техкарта выдана → планированию
    Rule("fulfillment.issued", LAUNCH.key, _issued_kit),  # комплект выдан в цех → задание можно запускать
    Rule("shipping.good_accepted", SHIP.key, _accepted_good),  # ГП принята складом → отгрузке
    Rule("production.box_handed_to_finished_goods", SHIP.key, _handed_good),  # ГП сдана из цеха → отгрузке
)
RULE_BY_TRIGGER: dict[str, Rule] = {rule.trigger: rule for rule in RULES}


# ── Цеховые уведомления: коробка сдана / подтверждена / возвращена ────────

SHOP_NOTICE_KIND = "production"
DEFAULT_SITE = "ЦАР"  # у монтажника без площадки — площадка по умолчанию (как у мастера)


@dataclass(frozen=True)
class Notice:
    """Одно письмо в «Уведомления» портала: кому (id пользователя), заголовок, текст и номер коробки."""

    user_id: UUID
    title: str
    body: str
    reference: str


def _count(value: object) -> str:
    return f"{value.normalize():f}" if hasattr(value, "normalize") else str(value)


def _box_of(session: Session, trigger: Trigger) -> ProductionBoxRow | None:
    """Коробка события: сдача пишет id коробки в детали (агрегат — выработка), остальные — агрегат."""
    raw = trigger.details.get("box_id") or trigger.aggregate_id
    try:
        return session.get(ProductionBoxRow, UUID(str(raw)))
    except ValueError:
        return None


def _site(value: str | None) -> str:
    return str(value or "").strip() or DEFAULT_SITE


def _label(session: Session, box: ProductionBoxRow) -> tuple[str, str] | None:
    """«ПЗ-0412» и название операции, которой сдана коробка."""
    row = session.execute(
        select(ProductionTaskRow.number, ProductionOperationRow.stage)
        .join(ProductionOperationRow, ProductionOperationRow.task_id == ProductionTaskRow.id)
        .where(ProductionOperationRow.id == box.operation_id)
    ).first()
    return None if row is None else (row[0], row[1])


def _handed_notice(session: Session, trigger: Trigger) -> list[Notice]:
    """Сдана коробка → мастерам площадки сдавшего: ждёт подтверждения."""
    box = _box_of(session, trigger)
    label = None if box is None else _label(session, box)
    if box is None or label is None or not box.number or box.status != BoxStatus.OPERATOR_CLOSED.value:
        return []  # коробку уже подтвердили или вернули — ждать нечего (и старые события журнала не будят людей)
    maker = session.get(IdentityUserRow, box.operator_id)
    site = _site(maker.site if maker is not None else None)
    masters = [
        row for row in session.scalars(
            select(IdentityUserRow).where(IdentityUserRow.site_master.is_(True), IdentityUserRow.active.is_(True))
            .order_by(IdentityUserRow.name, IdentityUserRow.id)
        ) if _site(row.site) == site
    ]
    number, stage = label
    body = f"Коробка {box.number} ждёт подтверждения: {number}, {stage}, {_count(box.quantity)} шт"
    return [Notice(master.id, "Коробка ждёт подтверждения", body, box.number) for master in masters]


def _route_names(session: Session) -> dict[str, str]:
    """Код операции → русское название по маршрутам выданных техкарт (так же, как в карточке сотрудника)."""
    names: dict[str, str] = {}
    for card in SqlAlchemyTechcardRepository(session).list():
        if card.status is not TechcardStatus.ISSUED:
            continue
        for step in route_from_document(card.document):
            if step.code and step.stage:
                names.setdefault(step.code, stage_name(step.stage))
    return names


def _confirmed_notice(session: Session, trigger: Trigger) -> list[Notice]:
    """Подтверждена коробка → монтажникам операций, которые берут её на вход: можно начинать."""
    box = _box_of(session, trigger)
    if box is None or not box.number or box.status != BoxStatus.CONFIRMED.value:
        return []
    notices: list[Notice] = []
    seen: set[UUID] = set()
    names: dict[str, str] | None = None
    for stage, code, number, operator_id in session.execute(
        select(ProductionOperationRow.stage, ProductionOperationRow.code, ProductionTaskRow.number, ProductionOperationOperatorRow.operator_id)
        .join(ProductionOperationInputRow, ProductionOperationInputRow.operation_id == ProductionOperationRow.id)
        .join(ProductionTaskRow, ProductionTaskRow.id == ProductionOperationRow.task_id)
        .join(ProductionOperationOperatorRow, ProductionOperationOperatorRow.operation_id == ProductionOperationRow.id)
        .where(ProductionOperationInputRow.input_operation_id == box.operation_id)
        .order_by(ProductionOperationRow.position, ProductionOperationOperatorRow.position)
    ):
        if operator_id in seen:  # человек на двух операциях, питаемых коробкой, — одно письмо
            continue
        seen.add(operator_id)
        if names is None:
            names = _route_names(session)
        stage = names.get(code) or stage_name(stage)  # название, не код операции
        body = f"Можно начинать: {number} {stage} — пришла коробка {box.number} ({_count(box.quantity)} шт)"
        notices.append(Notice(operator_id, "Можно начинать", body, box.number))
    return notices


def _returned_notice(session: Session, trigger: Trigger) -> list[Notice]:
    """Возвращена коробка → её монтажнику: причина."""
    box = _box_of(session, trigger)
    if box is None or not box.number or box.status != BoxStatus.RETURNED.value:
        return []
    reason = str(trigger.details.get("reason") or box.return_reason or "").strip()
    return [Notice(box.operator_id, "Коробка возвращена", f"Коробка {box.number} возвращена: {reason}", box.number)]


# тип события журнала → кому и что сообщить; модули пишут эти события сами (production/execution.py)
NOTICES: dict[str, Callable[[Session, Trigger], list[Notice]]] = {
    "production.output_recorded": _handed_notice,  # монтажник сдал коробку
    "production.box_corrected": _handed_notice,  # исправил возвращённую — снова ждёт мастера
    "production.box_confirmed": _confirmed_notice,
    "production.box_returned": _returned_notice,
}


def notify_trigger(session: Session, trigger: Trigger) -> int:
    """Уведомления по событию (добавлены в сессию, не зафиксированы); сколько поставлено. Повтор события — 0."""
    notice_for = NOTICES.get(trigger.event_type)
    if notice_for is None:
        return 0
    outbox = SqlAlchemyOutboxRepository(session)
    sent = 0
    for notice in notice_for(session, trigger):
        sent += publish_notification(outbox, notification_requested(
            key=f"arbiter:{trigger.event_id}:{notice.user_id}", now=trigger.occurred_at, title=notice.title,
            body=notice.body, kind=SHOP_NOTICE_KIND, reference=notice.reference, user_ids=(notice.user_id,),
        ))
        session.flush()
    return sent


# ── Ход арбитра ──────────────────────────────────────────────────────────


def feed_triggers(now: datetime, sessions: sessionmaker[Session], *, limit: int = FEED_LIMIT) -> int:
    """События журнала с типом из правил, которых ещё нет в outbox, — в очередь арбитра; сколько поставлено.

    Время сообщения — время события (`now` — подпись таймера воркера): очередь разбирается в порядке событий, и из двух
    приёмок одной КБ задачу ставит первая."""
    with sessions() as session:
        queued = exists().where(OutboxMessageRow.id == AuditEventRow.id)
        rows = list(session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.event_type.in_((*RULE_BY_TRIGGER, *NOTICES)), ~queued)
            .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
            .limit(limit)
        ))
        for row in rows:
            trigger = Trigger(row.id, row.event_type, row.aggregate_id, row.occurred_at, dict(row.details or {}))
            session.add(OutboxMessageRow(
                id=row.id, topic=TOPIC, idempotency_key=f"{TOPIC}:{row.id}", created_at=row.occurred_at,
                payload=trigger.payload(), attempt_count=0,
            ))
        if rows:
            session.commit()
        return len(rows)


def apply_trigger(session: Session, trigger: Trigger) -> ProjectTask | None:
    """Правило события → новая задача (добавлена в сессию, не зафиксирована) или None, если ставить нечего."""
    rule = RULE_BY_TRIGGER.get(trigger.event_type)
    if rule is None:
        return None
    kind = KINDS[rule.kind]
    tasks = SqlAlchemyProjectTaskRepository(session)
    if tasks.by_source(kind.key, trigger.event_id) is not None:
        return None
    subject = rule.subject(session, trigger)
    project = normalize_project_number(subject.project) if subject is not None else ""
    if subject is None or not project:
        return None
    item_index = _item(subject.item_index)
    if tasks.open_for(kind.key, project, item_index) is not None:
        return None
    task = ProjectTask.create(
        project=project, item_index=item_index, module=kind.module, kind=kind.key,
        title=kind.text(project, item_index), source_event_id=trigger.event_id, source_type=trigger.event_type,
        subject_id=subject.subject_id, created_at=trigger.occurred_at,
    )
    if task.id in kind.done(session, [task]):
        return None
    tasks.add(task)
    return task


@dataclass(frozen=True)
class Dispatched:
    message_id: UUID
    task: ProjectTask | None


def dispatch_next_trigger(now: datetime, sessions: sessionmaker[Session]) -> Dispatched | None:
    """Одно сообщение очереди арбитра: задача и отметка «обработано» — в одной транзакции."""
    with sessions() as session:
        outbox = SqlAlchemyOutboxRepository(session)
        message = outbox.next_unpublished(TOPIC, now)
        if message is None:
            return None
        try:
            trigger = Trigger.from_payload(message.payload)
            task = apply_trigger(session, trigger)
            notify_trigger(session, trigger)
        except (KeyError, TypeError, ValueError) as error:  # a broken payload; ProjectTaskError is a ValueError too
            session.rollback()
            reason = f"Некорректное событие арбитра: {error}"
            outbox.mark_failed(message.id, now, reason, max_attempts=1)
            session.commit()
            raise PoisonTriggerMessage(message.id, reason) from error
        outbox.mark_published(message.id, now)
        session.commit()
        return Dispatched(message.id, task)


def close_followed_up(now: datetime, sessions: sessionmaker[Session]) -> int:
    """Открытые задачи, дело по которым уже сделано, закрывает арбитр; сколько закрыто."""
    with sessions() as session:
        repository = SqlAlchemyProjectTaskRepository(session)
        by_kind: dict[str, list[ProjectTask]] = {}
        for task in repository.open_of_kinds(tuple(KINDS)):
            by_kind.setdefault(task.kind, []).append(task)
        closed = 0
        for key, tasks in by_kind.items():
            done = KINDS[key].done(session, tasks)
            for task in tasks:
                if task.id in done:
                    repository.save(task.close(now, AUTO_CLOSER))
                    closed += 1
        if closed:
            session.commit()
        return closed
