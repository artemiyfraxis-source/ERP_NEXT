"""Откат тестовых нажатий в «Заданиях» на проде (владелец, 13.10.2026), без восстановления дампа: цех в это время
работает по-настоящему, поэтому откатывается только то, что нажал тестирующий, а не всё после момента.

    python -m app.workflows.test_rollback --since "2026-10-13 16:00" --actor <e-mail | логин | id>
        [--as-employee <ФИО | логин | id>] [--until "2026-10-13 18:00"] --dry-run | --apply

Что считается тестом. «Работаете за сотрудника» (`on_behalf_of`, production/api.py `_performer`) пишет исполнителем
сотрудника, а нажавшего — в журнал времени (`actor_id`), в журнал действий (`actor_id`, `details.performer_id`) и в
простои (`started_by_id` / `ended_by_id`). Тест — нажатия `--actor` в окне [since, until); с `--as-employee` — только
те, что сделаны за этого сотрудника (статусы задания и этапы, нажатые самим тестирующим, — только по заданиям и
проектам этих нажатий). Без `--actor` тестирующие — все, кто в окне работал за сотрудника, кроме него самого.

Что откатывается одной транзакцией:
* работа, начатая в окне (сессия, её строки журнала времени, входы): «начал», паузы, продолжения, отмена, сдача;
* сдача: выработка и её исправления, коробка сдачи, брак (и решение по нему, если его принял тестирующий), снятое из
  входных коробок (штуки возвращаются в них) и списанное из выданного комплекта (строки расхода комплекта);
* «взял»/«закончил» доли монтажника, поставленные тестовой работой (момент совпадает с тестовым нажатием);
* простои, начатые тестирующим (удаляются), и закрытые им чужие (снова открыты);
* «Выполнено» / «Вернуть в работу» / «Отменено» / «Вернуть» задания — статус «до» берётся из журнала действий;
* подтверждение и возврат коробки мастером-тестирующим;
* ручная смена этапа КБ (строки журнала проекта удаляются — этап снова прежний), если она ничего не закрыла.

Не откатывается и печатается отдельно: сдвиги плана, которые факт теста сделал в чужих заданиях (`production.replanned`
— прогноз, пересчитается следующим «Автопланом» / назначением), передача задания площадке, смена этапа с каскадом,
прочие действия тестирующего. Номера коробок и брака не переиспользуются (счётчик не откатывается). Уже разосланные
уведомления остаются; журнал действий не правится — откат дописывает в него одно событие `test_rollback` со списком.

Отказ (ничего не пишется), если от отката зависит чужое более позднее действие: другой человек нажал в тестовой
работе, сдал по той же операции после теста, взял тестовую коробку на вход, подтвердил/вернул/принял в ОТК её, решил по
тестовому браку, сменил статус задания или этап КБ после теста. Повторный запуск ничего не откатывает дважды.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.modules.fulfillment.infrastructure import FulfillmentLineRow
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.domain import ProductionStatus
from app.modules.production.downtime import REASON_NAMES, DowntimeReason, moscow
from app.modules.production.downtime_infrastructure import ProductionDowntimeRow
from app.modules.production.infrastructure import (
    ProductionBoxRow,
    ProductionConsumptionRow,
    ProductionDefectDecisionRow,
    ProductionDefectRow,
    ProductionFinishedGoodsReceiptRow,
    ProductionMaterialConsumptionRow,
    ProductionOperationOperatorRow,
    ProductionOperationRow,
    ProductionOutputRow,
    ProductionPlanEventRow,
    ProductionQcInspectionRow,
    ProductionSerialRow,
    ProductionTaskRow,
    ProductionWorkInputRow,
    ProductionWorkMaterialInputRow,
    ProductionWorkSessionRow,
    ProductionWorkTimeLogRow,
)
from app.modules.production.launchable import stage_name
from app.modules.production.plan import PLAN_KINDS
from app.modules.production.traceability import BoxStatus, WorkStatus
from app.shared.clock import MOSCOW, moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, SqlAlchemyAuditRepository

EVENT_TYPE = "test_rollback"

WORK_EVENT_NAMES = {"start": "начал", "pause": "пауза", "resume": "продолжил", "finish": "сдал", "cancel": "отменил"}
# Действия исполнения: откатываются вместе со своей работой (сессией), а не по одному.
EXECUTION_TYPES = frozenset({
    "production.work_started", "production.work_paused", "production.work_resumed", "production.work_cancelled",
    "production.output_recorded",
})
DOWNTIME_TYPES = frozenset({"production.downtime_started", "production.downtime_ended"})
# Статус задания после события; «до» — статус после предыдущего такого события по заданию (или «Запущено»).
STATUS_AFTER = {
    "production.done": ProductionStatus.DONE.value,
    "production.reopened": ProductionStatus.RUNNING.value,
    "production.cancelled": ProductionStatus.CANCELLED.value,
    "production.restored": ProductionStatus.RUNNING.value,
}
# Чужие события по заданию после теста, которые опираются на сданное тестом.
TASK_DEPENDENT_TYPES = frozenset({*STATUS_AFTER, "production.handed_over", "production.delivered"})
BOX_AFTER = {
    "production.box_confirmed": (BoxStatus.CONFIRMED.value, BoxStatus.OPERATOR_CLOSED.value),
    "production.box_returned": (BoxStatus.RETURNED.value, BoxStatus.OPERATOR_CLOSED.value),
}
EVENT_NAMES = {
    "production.box_confirmed": "подтвердил(а)",
    "production.box_returned": "вернул(а)",
    "production.box_corrected": "исправил(а)",
    "production.qc_accepted": "принял(а) в ОТК",
    "production.box_handed_to_finished_goods": "сдал(а) на склад ГП",
    "production.done": "отметил(а) «Выполнено»",
    "production.reopened": "вернул(а) в работу",
    "production.cancelled": "отменил(а) задание",
    "production.restored": "вернул(а) отменённое задание",
    "production.handed_over": "передал(а) задание площадке",
    "production.delivered": "записал(а) «Сдано» площадки",
}


class RollbackError(Exception):
    """Неверные параметры команды (кого или с какого момента откатывать)."""


@dataclass(frozen=True)
class Scope:
    since: datetime
    until: datetime
    actor_ids: frozenset[UUID]
    actor_names: str
    employee_id: UUID | None = None
    employee_name: str = ""


@dataclass
class Plan:
    """Что откатить (и почему нельзя). Строки — по-русски, для печати и для события `test_rollback`."""

    lines: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    session_ids: set[UUID] = field(default_factory=set)
    output_ids: set[UUID] = field(default_factory=set)
    box_ids: set[UUID] = field(default_factory=set)
    defect_ids: set[UUID] = field(default_factory=set)
    decision_ids: set[UUID] = field(default_factory=set)
    # Входная коробка → сколько штук вернуть в неё (минус — снова снять: тест исправлением возвращал штуки).
    source_returns: dict[UUID, Decimal] = field(default_factory=dict)
    # (операция, монтажник) → (снять «взял», снять «закончил»).
    share_resets: dict[tuple[UUID, UUID], tuple[bool, bool]] = field(default_factory=dict)
    downtime_deletes: set[UUID] = field(default_factory=set)
    downtime_reopens: set[UUID] = field(default_factory=set)
    task_status: dict[UUID, str] = field(default_factory=dict)  # задание → статус, к которому вернуть
    box_status: dict[UUID, str] = field(default_factory=dict)  # коробка → статус, к которому вернуть
    plan_event_ids: set[UUID] = field(default_factory=set)
    audit_ids: set[UUID] = field(default_factory=set)
    tasks: set[UUID] = field(default_factory=set)  # задания, чья версия растёт (открытые экраны перечитают)

    @property
    def empty(self) -> bool:
        return not self.lines


# ── время и люди ─────────────────────────────────────────────────────────


def parse_moment(text: str) -> datetime:
    """«2026-10-13 16:00» или «13.10.2026 16:00» — московское время."""
    value = str(text or "").strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=MOSCOW)
        except ValueError:
            continue
    raise RollbackError(f"Не понял момент «{value}»: нужен вид 2026-10-13 16:00 (московское время)")


def _db(session: Session, value: datetime) -> datetime:
    """Момент для сравнения в SQL: SQLite хранит московское время без пояса, PostgreSQL — с поясом."""
    if session.get_bind().dialect.name == "sqlite":
        return moscow(value).replace(tzinfo=None)
    return moscow(value)


def _at(value: datetime | None) -> str:
    return f"{moscow(value):%d.%m %H:%M}" if value is not None else "—"


def _same(left: datetime | None, right: datetime | None) -> bool:
    return left is not None and right is not None and moscow(left) == moscow(right)


def _count(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def resolve_user(session: Session, text: str) -> IdentityUserRow:
    """Сотрудник по id, e-mail, логину или ФИО (целиком или однозначной частью)."""
    value = str(text or "").strip()
    if not value:
        raise RollbackError("Пустое имя сотрудника")
    try:
        found = session.get(IdentityUserRow, UUID(value))
    except ValueError:
        found = None
    if found is not None:
        return found
    users = list(session.scalars(select(IdentityUserRow)))
    folded = value.casefold()
    if "@" in folded:
        # Адрес — только по полю e-mail, без запасных поисков по логину и ФИО.
        hits = [user for user in users if (user.email or "").strip().casefold() == folded]
        if not hits:
            raise RollbackError(f"Сотрудник с e-mail «{value}» не найден")
        if len(hits) > 1:
            raise RollbackError(f"E-mail «{value}» у нескольких сотрудников: {', '.join(sorted(user.name for user in hits))} — укажите id")
        return hits[0]
    for match in (
        lambda user: (user.email or "").casefold() == folded,
        lambda user: (user.login or "").casefold() == folded,
        lambda user: (user.name or "").casefold() == folded,
        lambda user: all(part in (user.name or "").casefold() for part in folded.split()),
    ):
        hits = [user for user in users if match(user)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise RollbackError(f"«{value}» подходит нескольким: {', '.join(sorted(user.name for user in hits))} — уточните")
    raise RollbackError(f"Сотрудник «{value}» не найден")


def make_scope(
    session: Session, *, since: datetime, until: datetime, actor: str | None, employee: str | None,
) -> Scope:
    if until <= since:
        raise RollbackError("Конец окна раньше начала")
    viewed = resolve_user(session, employee) if employee else None
    if actor:
        user = resolve_user(session, actor)
        if viewed is not None and viewed.id == user.id:
            raise RollbackError("Тестирующий и сотрудник — один человек: за себя «за сотрудника» не работают")
        return Scope(since, until, frozenset({user.id}), user.name, viewed.id if viewed else None, viewed.name if viewed else "")
    if viewed is None:
        raise RollbackError("Укажите --actor (кто нажимал) или --as-employee (за кого)")
    actors = _actors_for(session, viewed.id, since, until)
    if not actors:
        raise RollbackError(f"В окне никто не работал за сотрудника {viewed.name}")
    names = {row.id: row.name for row in session.scalars(select(IdentityUserRow).where(IdentityUserRow.id.in_(actors)))}
    return Scope(since, until, frozenset(actors), ", ".join(sorted(names.get(item, str(item)) for item in actors)), viewed.id, viewed.name)


def _actors_for(session: Session, employee_id: UUID, since: datetime, until: datetime) -> set[UUID]:
    """Кто в окне нажимал за сотрудника (кроме него самого): журнал времени, журнал действий, простои."""
    start, end = _db(session, since), _db(session, until)
    log = ProductionWorkTimeLogRow
    actors = set(session.scalars(select(log.actor_id).where(
        log.operator_id == employee_id, log.actor_id != employee_id, log.occurred_at >= start, log.occurred_at < end,
    )))
    for row in session.scalars(select(AuditEventRow).where(
        AuditEventRow.occurred_at >= start, AuditEventRow.occurred_at < end, AuditEventRow.event_type.like("production.%"),
    )):
        if str((row.details or {}).get("performer_id") or "") == str(employee_id) and row.actor_id != employee_id:
            actors.add(row.actor_id)
    down = ProductionDowntimeRow
    actors.update(session.scalars(select(down.started_by_id).where(
        down.operator_id == employee_id, down.started_by_id != employee_id, down.started_at >= start, down.started_at < end,
    )))
    return actors


# ── сбор ─────────────────────────────────────────────────────────────────


class _Reader:
    """Кэш строк, которые сбор читает много раз (задания, операции, люди)."""

    def __init__(self, session: Session):
        self.session = session
        self._names: dict[UUID, str] | None = None

    def name(self, user_id: UUID | None) -> str:
        if user_id is None:
            return "—"
        if self._names is None:
            self._names = {row.id: row.name for row in self.session.scalars(select(IdentityUserRow))}
        return self._names.get(user_id, str(user_id))

    def task(self, task_id: UUID) -> ProductionTaskRow | None:
        return self.session.get(ProductionTaskRow, task_id)

    def task_label(self, task_id: UUID) -> str:
        task = self.task(task_id)
        return f"{task.number} ({task.project} · {task.item})" if task is not None else f"задание {task_id}"

    def operation_label(self, operation_id: UUID) -> str:
        operation = self.session.get(ProductionOperationRow, operation_id)
        if operation is None:
            return "операция удалена"
        return f"этап {operation.position} «{stage_name(operation.stage)}»"

    def box_label(self, box_id: UUID) -> str:
        box = self.session.get(ProductionBoxRow, box_id)
        return f"коробка {box.number or 'без номера'}" if box is not None else "коробка удалена"


def _already_rolled_back(session: Session) -> set[UUID]:
    done: set[UUID] = set()
    for details in session.scalars(select(AuditEventRow.details).where(AuditEventRow.event_type == EVENT_TYPE)):
        for value in (details or {}).get("audit_ids") or ():
            try:
                done.add(UUID(str(value)))
            except ValueError:
                continue
    return done


def collect(session: Session, scope: Scope) -> Plan:
    """Что откатить и что мешает — только чтение."""
    plan = Plan()
    read = _Reader(session)
    start, end = _db(session, scope.since), _db(session, scope.until)
    done = _already_rolled_back(session)
    events = [row for row in session.scalars(select(AuditEventRow).where(
        AuditEventRow.actor_id.in_(scope.actor_ids), AuditEventRow.occurred_at >= start, AuditEventRow.occurred_at < end,
    ).order_by(AuditEventRow.occurred_at, AuditEventRow.id)) if row.id not in done]

    _collect_work(session, scope, plan, read, start, end, events)
    _collect_downtimes(session, scope, plan, read, start, end)
    _collect_events(session, scope, plan, read, events)
    return plan


def _collect_work(session, scope: Scope, plan: Plan, read: _Reader, start, end, events) -> None:
    log = ProductionWorkTimeLogRow
    query = select(log).where(log.actor_id.in_(scope.actor_ids), log.occurred_at >= start, log.occurred_at < end)
    if scope.employee_id is not None:
        query = query.where(log.operator_id == scope.employee_id)
    window = list(session.scalars(query.order_by(log.occurred_at, log.sequence)))
    started = list(dict.fromkeys(row.session_id for row in window if row.event == "start"))
    for session_id in dict.fromkeys(row.session_id for row in window if row.session_id not in started):
        work = session.get(ProductionWorkSessionRow, session_id)
        if work is None:
            continue
        plan.conflicts.append(
            f"{read.task_label(work.task_id)} · {read.operation_label(work.operation_id)} — работу "
            f"{read.name(work.operator_id)} начали {_at(work.started_at)}, до окна или не тестирующий: откатить только "
            "тестовые нажатия в ней нельзя (сдвиньте --since раньше её начала)"
        )
    sessions = [row for row in (session.get(ProductionWorkSessionRow, item) for item in started) if row is not None]
    if not sessions:
        return
    plan.session_ids = {row.id for row in sessions}
    outputs_by_session: dict[UUID, list[ProductionOutputRow]] = {}
    corrected = {str((row.details or {}).get("output_id")) for row in events if row.event_type == "production.box_corrected"}
    for work in sessions:
        output = session.scalar(select(ProductionOutputRow).where(ProductionOutputRow.session_id == work.id))
        if output is None:
            continue
        chain = list(session.scalars(select(ProductionOutputRow).where(
            ProductionOutputRow.output_box_id == output.output_box_id,
        ).order_by(ProductionOutputRow.finished_at)))
        for item in chain:
            if item.id != output.id and str(item.id) not in corrected:
                plan.conflicts.append(
                    f"{read.box_label(output.output_box_id)} из тестовой сдачи исправлена не тестирующим "
                    f"({read.name(item.operator_id)}, {_at(item.finished_at)})"
                )
        plan.output_ids.update(item.id for item in chain)
        plan.box_ids.add(output.output_box_id)
        outputs_by_session[work.id] = chain

    for work in sessions:
        entries = list(session.scalars(select(ProductionWorkTimeLogRow).where(
            ProductionWorkTimeLogRow.session_id == work.id,
        ).order_by(ProductionWorkTimeLogRow.sequence)))
        foreign = [row for row in entries if row.actor_id not in scope.actor_ids]
        label = f"{read.task_label(work.task_id)} · {read.operation_label(work.operation_id)}"
        for row in foreign:
            plan.conflicts.append(
                f"{label} — в тестовой работе потом нажал(а) {row.actor_name}: "
                f"«{WORK_EVENT_NAMES.get(row.event, row.event)}» {_at(row.occurred_at)}"
            )
        steps = ", ".join(f"{WORK_EVENT_NAMES.get(row.event, row.event)} {_at(row.occurred_at)}" for row in entries)
        plan.lines.append(
            f"Работа {label}: {read.name(work.operator_id)}, «{work.status}» — удаляется вместе с журналом времени "
            f"({len(entries)} стр.: {steps})"
        )
        plan.tasks.add(work.task_id)
        for output in outputs_by_session.get(work.id, ()):
            kind = "исправление" if output.correction_of_id else "сдача"
            plan.lines.append(
                f"  {kind} {_at(output.finished_at)}: годных {_count(output.good_quantity)}, брак "
                f"{_count(output.defect_quantity)} — удаляется"
            )
        for box_id in dict.fromkeys(output.output_box_id for output in outputs_by_session.get(work.id, ())):
            plan.lines.append(f"  {read.box_label(box_id)} этой сдачи — удаляется")

    _collect_box_dependents(session, scope, plan, read, events)
    _collect_consumption(session, plan, read)
    _collect_later_outputs(session, scope, plan, read)
    _collect_shares(session, plan, read, sessions, outputs_by_session)


def _collect_box_dependents(session, scope: Scope, plan: Plan, read: _Reader, events) -> None:
    """Чужие действия, которые опираются на тестовую коробку или брак, — отказ."""
    outputs, boxes, sessions = plan.output_ids, plan.box_ids, plan.session_ids
    if not boxes:
        return
    for edge in session.scalars(select(ProductionConsumptionRow).where(ProductionConsumptionRow.source_box_id.in_(boxes))):
        if edge.output_id not in outputs:
            taker = session.get(ProductionOutputRow, edge.output_id)
            plan.conflicts.append(
                f"{read.box_label(edge.source_box_id)} из теста уже взята в работу: "
                f"{read.name(taker.operator_id) if taker else '?'} сдал(а) из неё {_count(edge.quantity)} шт"
            )
    for item in session.scalars(select(ProductionWorkInputRow).where(ProductionWorkInputRow.box_id.in_(boxes))):
        if item.session_id not in sessions:
            work = session.get(ProductionWorkSessionRow, item.session_id)
            plan.conflicts.append(
                f"{read.box_label(item.box_id)} из теста введена на вход работы {read.name(work.operator_id if work else None)}"
            )
    for row in session.scalars(select(ProductionQcInspectionRow).where(ProductionQcInspectionRow.box_id.in_(boxes))):
        plan.conflicts.append(f"{read.box_label(row.box_id)} из теста уже принята ОТК")
    for row in session.scalars(select(ProductionFinishedGoodsReceiptRow).where(ProductionFinishedGoodsReceiptRow.source_box_id.in_(boxes))):
        plan.conflicts.append(f"{read.box_label(row.source_box_id)} из теста уже сдана на склад ГП")
    for row in session.scalars(select(ProductionSerialRow).where(ProductionSerialRow.current_box_id.in_(boxes))):
        plan.conflicts.append(f"{read.box_label(row.current_box_id)} из теста держит серийный номер {row.serial_number}")
    for row in session.scalars(select(AuditEventRow).where(
        AuditEventRow.aggregate_id.in_(boxes), AuditEventRow.actor_id.notin_(scope.actor_ids),
    )):
        plan.conflicts.append(
            f"{read.box_label(row.aggregate_id)} из теста: {row.actor_name} "
            f"{EVENT_NAMES.get(row.event_type, row.event_type)} её {_at(row.occurred_at)}"
        )
    own = {row.aggregate_id for row in events if row.event_type == "production.defect_decided"}
    for defect in session.scalars(select(ProductionDefectRow).where(ProductionDefectRow.output_id.in_(outputs))):
        plan.defect_ids.add(defect.id)
        plan.lines.append(f"Брак {defect.number}: {_count(defect.quantity)} шт «{defect.character}» — удаляется")
        decision = session.scalar(select(ProductionDefectDecisionRow).where(ProductionDefectDecisionRow.defect_id == defect.id))
        if decision is None:
            continue
        if defect.id in own:
            plan.decision_ids.add(decision.id)
            plan.lines.append(f"Решение по браку {defect.number} ({decision.act_reference}) — удаляется")
        else:
            plan.conflicts.append(f"По тестовому браку {defect.number} решение принял(а) {decision.decided_by} {_at(decision.decided_at)}")


def _collect_consumption(session, plan: Plan, read: _Reader) -> None:
    """Что тест снял из входных коробок и из выданного комплекта — вернуть."""
    if not plan.output_ids:
        return
    for edge in session.scalars(select(ProductionConsumptionRow).where(ProductionConsumptionRow.output_id.in_(plan.output_ids))):
        if edge.source_box_id in plan.box_ids:
            continue
        plan.source_returns[edge.source_box_id] = plan.source_returns.get(edge.source_box_id, Decimal(0)) + edge.quantity
    for box_id, quantity in sorted(plan.source_returns.items(), key=lambda item: str(item[0])):
        if quantity == 0:
            continue
        box = session.get(ProductionBoxRow, box_id)
        if box is None:
            continue
        if box.consumed - quantity < 0 or box.consumed - quantity > box.quantity:
            plan.conflicts.append(f"{read.box_label(box_id)}: вернуть {_count(quantity)} шт нельзя — расход коробки с тех пор менялся")
            continue
        verb = "вернуть" if quantity > 0 else "снова снять"
        plan.lines.append(f"Входная {read.box_label(box_id)}: {verb} {_count(abs(quantity))} шт")
    lines: dict[UUID, Decimal] = {}
    for row in session.scalars(select(ProductionMaterialConsumptionRow).where(
        ProductionMaterialConsumptionRow.output_id.in_(plan.output_ids),
    )):
        lines[row.fulfillment_line_id] = lines.get(row.fulfillment_line_id, Decimal(0)) + row.quantity
    for line_id, quantity in lines.items():
        line = session.get(FulfillmentLineRow, line_id)
        what = f"{line.article} {line.name}".strip() if line is not None else str(line_id)
        unit = f" {line.unit}" if line is not None and line.unit else ""
        plan.lines.append(f"Комплект: {what} — расход {_count(quantity)}{unit} снимается (снова доступно в выдаче)")


def _collect_later_outputs(session, scope: Scope, plan: Plan, read: _Reader) -> None:
    """После тестовой сдачи кто-то другой сдал по той же операции или изменил статус задания — отказ."""
    if not plan.output_ids:
        return
    earliest: dict[UUID, datetime] = {}
    by_task: dict[UUID, datetime] = {}
    for output in session.scalars(select(ProductionOutputRow).where(ProductionOutputRow.id.in_(plan.output_ids))):
        if output.operation_id not in earliest or moscow(output.finished_at) < moscow(earliest[output.operation_id]):
            earliest[output.operation_id] = output.finished_at
        if output.task_id not in by_task or moscow(output.finished_at) < moscow(by_task[output.task_id]):
            by_task[output.task_id] = output.finished_at
    for operation_id, moment in earliest.items():
        for other in session.scalars(select(ProductionOutputRow).where(
            ProductionOutputRow.operation_id == operation_id, ProductionOutputRow.id.notin_(plan.output_ids),
        )):
            if moscow(other.finished_at) > moscow(moment):
                plan.conflicts.append(
                    f"{read.task_label(other.task_id)} · {read.operation_label(operation_id)} — после тестовой сдачи "
                    f"{_at(moment)} по этой операции сдал(а) {read.name(other.operator_id)} {_at(other.finished_at)} "
                    f"({_count(other.good_quantity + other.defect_quantity)} шт): его/её сдача считалась вместе с тестовой"
                )
    for task_id, moment in by_task.items():
        for row in session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == task_id, AuditEventRow.actor_id.notin_(scope.actor_ids),
            AuditEventRow.event_type.in_(TASK_DEPENDENT_TYPES),
        )):
            if moscow(row.occurred_at) > moscow(moment):
                plan.conflicts.append(
                    f"{read.task_label(task_id)}: после тестовой сдачи {row.actor_name} "
                    f"{EVENT_NAMES.get(row.event_type, row.event_type)} {_at(row.occurred_at)}"
                )


def _collect_shares(session, plan: Plan, read: _Reader, sessions, outputs_by_session) -> None:
    """«Взял»/«закончил» доли, поставленные тестом: момент доли совпадает с тестовым «начал»/сдачей."""
    starts: dict[tuple[UUID, UUID], list[datetime]] = {}
    finishes: dict[UUID, list[datetime]] = {}
    for work in sessions:
        starts.setdefault((work.operation_id, work.operator_id), []).append(work.started_at)
        for output in outputs_by_session.get(work.id, ()):
            finishes.setdefault(output.operation_id, []).append(output.finished_at)
    for operation_id in {key[0] for key in starts} | set(finishes):
        operation = session.get(ProductionOperationRow, operation_id)
        if operation is None:
            continue
        for share in session.scalars(select(ProductionOperationOperatorRow).where(
            ProductionOperationOperatorRow.operation_id == operation_id,
        )):
            own_starts = starts.get((operation_id, share.operator_id), ())
            other_work = session.scalar(select(ProductionWorkSessionRow.id).where(
                ProductionWorkSessionRow.operation_id == operation_id,
                ProductionWorkSessionRow.operator_id == share.operator_id,
                ProductionWorkSessionRow.id.notin_(plan.session_ids),
            ).limit(1))
            reset_start = other_work is None and any(_same(share.started_at, moment) for moment in own_starts)
            reset_finish = any(_same(share.finished_at, moment) for moment in finishes.get(operation_id, ()))
            if not (reset_start or reset_finish):
                continue
            plan.share_resets[(operation_id, share.operator_id)] = (reset_start, reset_finish)
            plan.tasks.add(operation.task_id)
            marks = [text for flag, text in ((reset_start, f"«взял» {_at(share.started_at)}"), (reset_finish, f"«закончил» {_at(share.finished_at)}")) if flag]
            plan.lines.append(
                f"План {read.task_label(operation.task_id)} · {read.operation_label(operation_id)}: у "
                f"{read.name(share.operator_id)} снимается {' и '.join(marks)}"
            )


def _collect_downtimes(session, scope: Scope, plan: Plan, read: _Reader, start, end) -> None:
    down = ProductionDowntimeRow
    query = select(down).where(down.started_by_id.in_(scope.actor_ids), down.started_at >= start, down.started_at < end)
    if scope.employee_id is not None:
        query = query.where(down.operator_id == scope.employee_id)
    for row in session.scalars(query.order_by(down.started_at)):
        label = f"Простой {read.task_label(row.task_id) if row.task_id else row.task_number} · {_reason(row.reason)} · {row.operator_name}"
        if row.ended_by_id is not None and row.ended_by_id not in scope.actor_ids:
            plan.conflicts.append(f"{label}: закончил(а) не тестирующий — {row.ended_by_name} {_at(row.ended_at)}")
            continue
        plan.downtime_deletes.add(row.id)
        plan.lines.append(f"{label}, {_at(row.started_at)}–{_at(row.ended_at) if row.ended_at else 'открыт'} — удаляется")
    query = select(down).where(down.ended_by_id.in_(scope.actor_ids), down.ended_at >= start, down.ended_at < end)
    if scope.employee_id is not None:
        query = query.where(down.operator_id == scope.employee_id)
    for row in session.scalars(query.order_by(down.ended_at)):
        if row.id in plan.downtime_deletes:
            continue
        label = f"Простой {read.task_label(row.task_id) if row.task_id else row.task_number} · {_reason(row.reason)} · {row.operator_name}"
        if row.task_id is not None and session.scalar(select(down.id).where(
            down.task_id == row.task_id, down.ended_at.is_(None), down.id != row.id,
        )) is not None:
            plan.conflicts.append(f"{label}: снова открыть нельзя — у задания уже открыт другой простой")
            continue
        plan.downtime_reopens.add(row.id)
        plan.lines.append(f"{label}: «закончить» {_at(row.ended_at)} снимается — простой снова открыт")


def _reason(value: str) -> str:
    try:
        return REASON_NAMES[DowntimeReason(value)]
    except ValueError:
        return value


def _collect_events(session, scope: Scope, plan: Plan, read: _Reader, events) -> None:
    """События журнала действий тестирующего: что уже покрыто, что откатить, что остаётся."""
    test_tasks = set(plan.tasks)
    for row in session.scalars(select(ProductionDowntimeRow.task_id).where(
        ProductionDowntimeRow.id.in_(plan.downtime_deletes | plan.downtime_reopens),
    )):
        if row is not None:
            test_tasks.add(row)
    test_projects = {task.project for task in (read.task(item) for item in test_tasks) if task is not None}
    employee = str(scope.employee_id) if scope.employee_id else ""
    replanned: dict[UUID, int] = {}
    task_state: dict[UUID, str] = {}
    box_state: dict[UUID, str] = {}
    for row in reversed(events):
        details = row.details or {}
        kind = row.event_type
        on_behalf = str(details.get("performer_id") or "") == employee if employee else True
        if kind == EVENT_TYPE:
            continue
        if kind in EXECUTION_TYPES or kind in DOWNTIME_TYPES:
            if on_behalf:
                plan.audit_ids.add(row.id)
            continue
        if kind == "production.box_corrected":
            if str(details.get("output_id")) in {str(item) for item in plan.output_ids}:
                plan.audit_ids.add(row.id)
            elif on_behalf:
                plan.kept.append(f"{_at(row.occurred_at)} исправление {read.box_label(row.aggregate_id)} — не откатывается автоматически")
            continue
        if kind == "production.defect_decided":
            if row.aggregate_id in plan.defect_ids:
                plan.audit_ids.add(row.id)
            elif not employee:
                plan.kept.append(f"{_at(row.occurred_at)} решение по браку {details.get('number', '')} — не откатывается автоматически")
            continue
        if kind in BOX_AFTER:
            if row.aggregate_id in plan.box_ids:
                plan.audit_ids.add(row.id)
                continue
            box = session.get(ProductionBoxRow, row.aggregate_id)
            if box is None or (employee and box.operator_id != scope.employee_id):
                continue
            _plan_box_revert(session, scope, plan, read, row, box, box_state)
            continue
        if kind in STATUS_AFTER:
            if employee and row.aggregate_id not in test_tasks:
                continue
            _plan_status_revert(session, scope, plan, read, row, task_state)
            continue
        if kind == "production.stage_changed":
            if employee and details.get("project") not in test_projects:
                continue
            _plan_stage_revert(session, plan, row)
            continue
        if kind == "production.replanned":
            if not employee or row.aggregate_id in test_tasks or str(details.get("reason")) in ("started", "output"):
                replanned[row.aggregate_id] = replanned.get(row.aggregate_id, 0) + 1
            continue
        if not kind.startswith("production.") or (employee and not on_behalf):
            continue
        plan.kept.append(f"{_at(row.occurred_at)} {kind} — не откатывается автоматически")
    if replanned:
        tasks = ", ".join(sorted(read.task_label(item) for item in replanned))
        plan.kept.append(
            f"Сдвиги плана после фактов теста остаются (прогноз, пересчитается «Автопланом»/назначением): {tasks}"
        )
    plan.kept.reverse()


def _plan_box_revert(session, scope: Scope, plan: Plan, read: _Reader, row, box: ProductionBoxRow, state) -> None:
    after, before = BOX_AFTER[row.event_type]
    label = f"{read.box_label(box.id)} ({_at(row.occurred_at)} {EVENT_NAMES[row.event_type]} тестирующий)"
    current = state.get(box.id, box.status)
    later = [item for item in session.scalars(select(AuditEventRow).where(
        AuditEventRow.aggregate_id == box.id, AuditEventRow.actor_id.notin_(scope.actor_ids),
    )) if moscow(item.occurred_at) > moscow(row.occurred_at)]
    reasons = [f"{item.actor_name} {EVENT_NAMES.get(item.event_type, item.event_type)} её {_at(item.occurred_at)}" for item in later]
    if current != after:
        reasons.append(f"сейчас она «{current}»")
    if after == BoxStatus.CONFIRMED.value:
        consumed = box.consumed - plan.source_returns.get(box.id, Decimal(0))
        if consumed != 0 or box.reserved != 0 or box.rejected != 0:
            reasons.append("из неё уже брали штуки")
        if session.scalar(select(ProductionWorkInputRow.session_id).where(
            ProductionWorkInputRow.box_id == box.id, ProductionWorkInputRow.session_id.notin_(plan.session_ids),
        ).limit(1)) is not None:
            reasons.append("она введена на вход чужой работы")
        if session.scalar(select(ProductionQcInspectionRow.id).where(ProductionQcInspectionRow.box_id == box.id)) is not None:
            reasons.append("её принял ОТК")
    if reasons:
        plan.conflicts.append(f"{label}: откатить нельзя — {'; '.join(reasons)}")
        return
    state[box.id] = before
    plan.box_status[box.id] = before
    plan.audit_ids.add(row.id)
    plan.lines.append(f"{label} — снимается, снова «{before}»")


def _previous_status(session, task_id: UUID, moment: datetime, event_id: UUID) -> str:
    previous = ProductionStatus.RUNNING.value
    for item in session.scalars(select(AuditEventRow).where(
        AuditEventRow.aggregate_id == task_id, AuditEventRow.event_type.in_(STATUS_AFTER),
    ).order_by(AuditEventRow.occurred_at, AuditEventRow.id)):
        if item.id == event_id or moscow(item.occurred_at) > moscow(moment):
            break
        previous = STATUS_AFTER[item.event_type]
    return previous


def _plan_status_revert(session, scope: Scope, plan: Plan, read: _Reader, row, state) -> None:
    task = read.task(row.aggregate_id)
    if task is None:
        return
    after = STATUS_AFTER[row.event_type]
    before = _previous_status(session, task.id, row.occurred_at, row.id)
    label = f"Статус {read.task_label(task.id)} ({_at(row.occurred_at)} {EVENT_NAMES[row.event_type]})"
    later = [item for item in session.scalars(select(AuditEventRow).where(
        AuditEventRow.aggregate_id == task.id, AuditEventRow.actor_id.notin_(scope.actor_ids),
        AuditEventRow.event_type.in_(TASK_DEPENDENT_TYPES),
    )) if moscow(item.occurred_at) > moscow(row.occurred_at)]
    current = state.get(task.id, task.status)
    reasons = [f"{item.actor_name} {EVENT_NAMES.get(item.event_type, item.event_type)} {_at(item.occurred_at)}" for item in later]
    if current != after:
        reasons.append(f"сейчас оно «{current}»")
    if reasons:
        plan.conflicts.append(f"{label}: откатить нельзя — {'; '.join(reasons)}")
        return
    state[task.id] = before
    plan.task_status[task.id] = before
    plan.tasks.add(task.id)
    plan.audit_ids.add(row.id)
    plan.lines.append(f"{label}: «{after}» → снова «{before}»")


def _plan_stage_revert(session, plan: Plan, row) -> None:
    details = row.details or {}
    project = str(details.get("project") or "")
    changes = "; ".join(details.get("changes") or ()) or str(details.get("value") or "")
    label = f"Этап КБ {project} ({_at(row.occurred_at)}: {changes})"
    if details.get("closed"):
        plan.kept.append(f"{label} — смена этапа закрыла заявки/задания, откатите её руками на экране «Проекты»")
        return
    kinds = (PLAN_KINDS["stage"], PLAN_KINDS["status"])
    event = ProductionPlanEventRow
    rows = list(session.scalars(select(event).where(
        event.project == project, event.who == row.actor_name, event.kind.in_(kinds), event.occurred_at == row.occurred_at,
    )))
    if not rows:
        plan.kept.append(f"{label} — строк журнала проекта не нашлось, нечего снимать")
        return
    last = max(item.sequence for item in rows)
    items = {item.item_index for item in rows if item.kind == PLAN_KINDS["stage"]}
    for later in session.scalars(select(event).where(event.project == project, event.sequence > last, event.kind.in_(kinds))):
        if later.who != row.actor_name and (later.kind == PLAN_KINDS["status"] or later.item_index in items):
            plan.conflicts.append(f"{label}: откатить нельзя — потом {later.who} {later.kind.lower()} {later.item_index} «{later.value}» {_at(later.occurred_at)}")
            return
    plan.plan_event_ids.update(item.id for item in rows)
    plan.audit_ids.add(row.id)
    plan.lines.append(f"{label} — строки журнала проекта ({len(rows)}) удаляются, этап снова прежний")


# ── запись ───────────────────────────────────────────────────────────────


def _bump(session: Session, task_ids: Iterable[UUID], status: dict[UUID, str]) -> None:
    for task_id in task_ids:
        task = session.get(ProductionTaskRow, task_id)
        if task is None:
            continue
        if task_id in status:
            task.status = status[task_id]
        task.version = task.version + 1


def execute(session: Session, plan: Plan, scope: Scope, now: datetime) -> None:
    """Откат по собранному плану в открытой транзакции сессии; фиксирует вызывающий."""
    if plan.decision_ids:
        session.execute(delete(ProductionDefectDecisionRow).where(ProductionDefectDecisionRow.id.in_(plan.decision_ids)))
    if plan.defect_ids:
        session.execute(delete(ProductionDefectRow).where(ProductionDefectRow.id.in_(plan.defect_ids)))
    if plan.output_ids:
        session.execute(delete(ProductionConsumptionRow).where(ProductionConsumptionRow.output_id.in_(plan.output_ids)))
        session.execute(delete(ProductionMaterialConsumptionRow).where(ProductionMaterialConsumptionRow.output_id.in_(plan.output_ids)))
    for box_id, quantity in plan.source_returns.items():
        box = session.get(ProductionBoxRow, box_id)
        if box is None or quantity == 0:
            continue
        box.consumed = box.consumed - quantity
        left = box.quantity - box.consumed - box.reserved - box.rejected
        if box.status == BoxStatus.CONSUMED.value and left > 0:
            box.status = BoxStatus.CONFIRMED.value
        elif box.status == BoxStatus.CONFIRMED.value and left == 0:
            box.status = BoxStatus.CONSUMED.value
        box.version = box.version + 1
    for box_id, status in plan.box_status.items():
        box = session.get(ProductionBoxRow, box_id)
        if box.status == BoxStatus.CONFIRMED.value:
            box.confirmed_at, box.confirmed_by = None, None
        if box.status == BoxStatus.RETURNED.value:
            box.return_reason = ""
        box.status = status
        box.version = box.version + 1
    if plan.session_ids:
        sessions = plan.session_ids
        session.execute(delete(ProductionWorkInputRow).where(ProductionWorkInputRow.session_id.in_(sessions)))
        session.execute(delete(ProductionWorkMaterialInputRow).where(ProductionWorkMaterialInputRow.session_id.in_(sessions)))
        session.execute(delete(ProductionWorkTimeLogRow).where(ProductionWorkTimeLogRow.session_id.in_(sessions)))
    if plan.output_ids:
        session.execute(update(ProductionOutputRow).where(ProductionOutputRow.id.in_(plan.output_ids)).values(correction_of_id=None))
        session.execute(delete(ProductionOutputRow).where(ProductionOutputRow.id.in_(plan.output_ids)))
    if plan.box_ids:
        session.execute(delete(ProductionBoxRow).where(ProductionBoxRow.id.in_(plan.box_ids)))
    if plan.session_ids:
        session.execute(delete(ProductionWorkSessionRow).where(ProductionWorkSessionRow.id.in_(plan.session_ids)))
    for (operation_id, operator_id), (reset_start, reset_finish) in plan.share_resets.items():
        share = session.get(ProductionOperationOperatorRow, (operation_id, operator_id))
        if share is None:
            continue
        if reset_start:
            share.started_at = None
        if reset_finish:
            share.finished_at = None
    _bump(session, plan.tasks, plan.task_status)
    if plan.downtime_deletes:
        session.execute(delete(ProductionDowntimeRow).where(ProductionDowntimeRow.id.in_(plan.downtime_deletes)))
    if plan.downtime_reopens:
        session.execute(update(ProductionDowntimeRow).where(ProductionDowntimeRow.id.in_(plan.downtime_reopens)).values(
            ended_at=None, ended_by_id=None, ended_by_name="",
        ))
    if plan.plan_event_ids:
        session.execute(delete(ProductionPlanEventRow).where(ProductionPlanEventRow.id.in_(plan.plan_event_ids)))
    actor_id = sorted(scope.actor_ids, key=str)[0]
    SqlAlchemyAuditRepository(session).add(AuditEvent(
        id=uuid4(), aggregate_id=actor_id, event_type=EVENT_TYPE,
        actor=Actor(id=actor_id, name=f"откат теста: {scope.actor_names}", department=""),
        occurred_at=now, details={
            "since": moscow(scope.since).isoformat(), "until": moscow(scope.until).isoformat(),
            "actors": sorted(str(item) for item in scope.actor_ids),
            "employee": scope.employee_name, "employee_id": str(scope.employee_id) if scope.employee_id else None,
            "lines": plan.lines, "kept": plan.kept,
            "audit_ids": sorted(str(item) for item in plan.audit_ids),
            "sessions": sorted(str(item) for item in plan.session_ids),
            "outputs": sorted(str(item) for item in plan.output_ids),
            "boxes": sorted(str(item) for item in plan.box_ids),
            "downtimes": sorted(str(item) for item in plan.downtime_deletes | plan.downtime_reopens),
        },
    ))
    session.flush()


def report(plan: Plan, scope: Scope, *, applied: bool) -> str:
    who = f"нажимал(и) {scope.actor_names}" + (f", за сотрудника {scope.employee_name}" if scope.employee_name else "")
    out = [f"Откат теста: {who}; окно {moscow(scope.since):%d.%m.%Y %H:%M} – {moscow(scope.until):%d.%m.%Y %H:%M} (МСК)"]
    if plan.conflicts:
        out.append("ОТКАЗ — от тестовых действий уже зависят чужие, ничего не изменено:")
        out.extend(f"  ! {line}" for line in plan.conflicts)
    if plan.lines:
        out.append("Откачено:" if applied else ("Было бы отменено:" if plan.conflicts else "Будет отменено:"))
        out.extend(f"  {line}" for line in plan.lines)
    else:
        out.append("Откатывать нечего: тестовых изменений в окне не найдено (или они уже откачены).")
    if plan.kept:
        out.append("Не откатывается автоматически:")
        out.extend(f"  - {line}" for line in plan.kept)
    if not applied and not plan.conflicts and plan.lines:
        out.append("Проверка без записи. Откатить — та же команда с --apply вместо --dry-run.")
    return "\n".join(out)


def run(
    sessions: Callable[[], Session], *, since: datetime, until: datetime, actor: str | None, employee: str | None,
    apply: bool, now: datetime,
) -> tuple[str, int]:
    """Текст отчёта и код: 0 — проверено / откачено, 1 — отказ (зависят чужие действия), 2 — неверные параметры."""
    with sessions() as session:
        try:
            scope = make_scope(session, since=since, until=until, actor=actor, employee=employee)
        except RollbackError as error:
            return str(error), 2
        plan = collect(session, scope)
        if plan.conflicts:
            session.rollback()
            return report(plan, scope, applied=False), 1
        if not apply or plan.empty:
            session.rollback()
            return report(plan, scope, applied=False), 0
        execute(session, plan, scope, now)
        session.commit()
        return report(plan, scope, applied=True), 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Откатить тестовые нажатия в «Заданиях» (в том числе «за сотрудника») за окно времени, не трогая "
                    "работу цеха. Сначала --dry-run.",
    )
    parser.add_argument("--since", required=True, help="начало окна, МСК: \"2026-10-13 16:00\"")
    parser.add_argument("--until", help="конец окна, МСК (по умолчанию — сейчас)")
    parser.add_argument("--actor", help="кто нажимал: e-mail, логин, ФИО или id")
    parser.add_argument("--as-employee", dest="employee", help="за кого работали: ФИО, логин или id")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать, что будет отменено")
    mode.add_argument("--apply", action="store_true", help="откатить одной транзакцией")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    now = moscow_now()
    try:
        since = parse_moment(args.since)
        until = parse_moment(args.until) if args.until else now
    except RollbackError as error:
        print(error, file=sys.stderr)
        return 2
    from sqlalchemy.orm import sessionmaker

    from app.database import build_engine

    engine = build_engine(url)
    try:
        text, code = run(
            sessionmaker(bind=engine, expire_on_commit=False, class_=Session),
            since=since, until=until, actor=args.actor, employee=args.employee, apply=args.apply, now=now,
        )
    finally:
        engine.dispose()
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
