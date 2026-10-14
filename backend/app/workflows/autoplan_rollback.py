"""Откат «Автоплана» на проде (владелец, 14.10.2026): всё, что применение автоплана сделало в окне времени, — назад.

    python -m app.workflows.autoplan_rollback --since "2026-10-13 16:00" [--until "2026-10-13 18:00"] --dry-run | --apply

Что откатывается одной транзакцией для каждого `production.autoplan_applied` в окне [since, until):
* задания, которые применение запустило (детали события `created` и события `production.launched` того же момента и
  того же мастера), удаляются тем же путём, что и хвосты удалённого проекта (`project_kitset.purge_project_tails`):
  `production.delete` — операции, их входы, доли, бригада, серийные номера; строки базового плана задания уходят с ним
  (в PostgreSQL — каскадом FK, здесь явно, чтобы и SQLite не держал сирот). Номер запуска КБ (`launch_seq`) — колонка
  задания, уходит с ним. «Запущено» по КБ (`launched_quantity`) и этап КБ «Распланировано» не хранятся — они
  считаются по заданиям и возвращаются сами. Записей журнала «Этап КБ» запуск не пишет. Уведомления остаются;
* задание удаляется, только если по нему нет следа работы: коробка, смена, журнал времени, выработка (и всё, что от неё:
  расход входов, брак, ОТК, ГП), «Сдано» площадки, «взял/закончил» в доле, простой; не отдано площадке; «Запущено».
  Иначе задание остаётся с причиной (остальное откатывается);
* у заданий, запущенных ДО окна (ПЗ-0007 и др.), назначения и плановые окна, поставленные автопланом (`assigned_by`
  «Автоплан · …») или сдвинутые в окне (`production.replanned`), возвращаются к «до». Значения «до» журнал не хранит
  (ни применение, ни сдвиг их не пишут), поэтому: если у задания до окна не было ни одного события плана (назначение,
  доля, автоплан, сдвиг) — «до» известно: «не назначено», как при запуске без плана; иначе снимается только назначение
  автоплана, и строка отчёта это называет. Не трогаются операции с работой, назначенные в окне вручную и те, чей план
  менялся после окна.

Плюс: записи журнала плана, добавленные в окне (`production.plan_event_added`, напр. «Статус проекта» 533К.12),
удаляются. Журнал действий не правится — откат дописывает одно событие `autoplan_rollback` со списком. Повторный запуск
ничего не делает дважды: удалённого нет, снятое не назначено, обработанные события записаны в `audit_ids`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.autoplan import AUTOPLAN_MARK
from app.modules.production.baseline_infrastructure import ProductionPlanBaselineLineRow
from app.modules.production.domain import ProductionStatus
from app.modules.production.downtime import moscow
from app.modules.production.downtime_infrastructure import ProductionDowntimeRow
from app.modules.production.infrastructure import (
    ProductionOutputRow,
    ProductionPlanEventRow,
    ProductionTaskRow,
    ProductionWorkTimeLogRow,
    SqlAlchemyProductionRepository,
)
from app.modules.production.launchable import stage_name
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, SqlAlchemyAuditRepository
from app.workflows.test_rollback import RollbackError, _at, _db, parse_moment

EVENT_TYPE = "autoplan_rollback"
APPLIED = "production.autoplan_applied"
LAUNCHED = "production.launched"
REPLANNED = "production.replanned"
PLAN_EVENT_ADDED = "production.plan_event_added"
# События, которые меняют назначения и плановые окна задания (aggregate_id — задание).
MANUAL_PLAN_TYPES = frozenset({"production.assigned", "production.share_assigned", "production.share_removed"})
PLAN_CHANGE_TYPES = frozenset({*MANUAL_PLAN_TYPES, "production.auto_scheduled", REPLANNED})


@dataclass(frozen=True)
class Window:
    since: datetime
    until: datetime


@dataclass
class Plan:
    """Что откатить. Строки — по-русски, для печати и для события `autoplan_rollback`."""

    lines: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    delete_tasks: dict[UUID, str] = field(default_factory=dict)  # id → номер
    clear_operations: dict[UUID, set[UUID]] = field(default_factory=dict)  # задание → операции
    plan_event_rows: set[UUID] = field(default_factory=set)
    baseline_lines: int = 0
    audit_ids: set[UUID] = field(default_factory=set)
    actor_id: UUID | None = None

    @property
    def empty(self) -> bool:
        return not (self.delete_tasks or self.clear_operations or self.plan_event_rows)


def _already_rolled_back(session: Session) -> set[UUID]:
    done: set[UUID] = set()
    for details in session.scalars(select(AuditEventRow.details).where(AuditEventRow.event_type == EVENT_TYPE)):
        for value in (details or {}).get("audit_ids") or ():
            try:
                done.add(UUID(str(value)))
            except ValueError:
                continue
    return done


def _names(session: Session) -> dict[UUID, str]:
    return {row.id: row.name for row in session.scalars(select(IdentityUserRow))}


def _task_label(row: ProductionTaskRow) -> str:
    return f"{row.number} ({row.project} · {row.item})"


def _work_reason(session: Session, repository: SqlAlchemyProductionRepository, row: ProductionTaskRow) -> str:
    """Почему задание удалять нельзя ("" — следа работы нет)."""
    if row.id in repository.worked([row.id]):
        return "по нему отмечена работа (коробка, смена, выработка, брак, «Сдано» или «взял/закончил»)"
    if session.scalar(select(ProductionWorkTimeLogRow.id).where(ProductionWorkTimeLogRow.task_id == row.id).limit(1)):
        return "в журнале времени есть строки"
    if session.scalar(select(ProductionDowntimeRow.id).where(ProductionDowntimeRow.task_id == row.id).limit(1)):
        return "по нему записан простой"
    if row.handed_to:
        return f"отдано в {row.handed_to}"
    if row.status != ProductionStatus.RUNNING.value:
        return f"статус «{row.status}» — его меняли после запуска"
    return ""


def _launched_by_apply(session: Session, apply: AuditEventRow) -> list[UUID]:
    """Задания применения: `created` (номера) и `production.launched` того же момента от того же мастера."""
    ids = list(session.scalars(select(AuditEventRow.aggregate_id).where(
        AuditEventRow.event_type == LAUNCHED, AuditEventRow.occurred_at == apply.occurred_at,
        AuditEventRow.actor_id == apply.actor_id,
    ).order_by(AuditEventRow.id)))
    numbers = [str(value) for value in (apply.details or {}).get("created") or ()]
    if numbers:
        for task_id in session.scalars(select(ProductionTaskRow.id).where(ProductionTaskRow.number.in_(numbers))):
            if task_id not in ids:
                ids.append(task_id)
    return ids


def _had_plan_before(session: Session, row: ProductionTaskRow, start) -> bool:
    """У задания до окна было событие плана — значит «до» не «не назначено» и его значения журнал не хранит."""
    if session.scalar(select(AuditEventRow.id).where(
        AuditEventRow.aggregate_id == row.id, AuditEventRow.event_type.in_(PLAN_CHANGE_TYPES),
        AuditEventRow.occurred_at < start,
    ).limit(1)):
        return True
    return any(row.number in ((details or {}).get("tasks") or ()) for details in session.scalars(
        select(AuditEventRow.details).where(AuditEventRow.event_type == APPLIED, AuditEventRow.occurred_at < start)
    ))


def _changed_after(session: Session, row: ProductionTaskRow, end) -> bool:
    if session.scalar(select(AuditEventRow.id).where(
        AuditEventRow.aggregate_id == row.id, AuditEventRow.event_type.in_(PLAN_CHANGE_TYPES),
        AuditEventRow.occurred_at >= end,
    ).limit(1)):
        return True
    return any(row.number in ((details or {}).get("tasks") or ()) for details in session.scalars(
        select(AuditEventRow.details).where(AuditEventRow.event_type == APPLIED, AuditEventRow.occurred_at >= end)
    ))


def _assignment(operation, names: dict[UUID, str]) -> str:
    people = ", ".join(names.get(value, str(value)) for value in operation.operator_ids) or "без людей"
    window = f"{_at(operation.planned_start)}–{_at(operation.planned_finish)}" if operation.planned_start else "без окна"
    return f"{people}, {window}"


def collect(session: Session, window: Window) -> Plan:
    """Что откатить и что нельзя — только чтение."""
    plan = Plan()
    start, end = _db(session, window.since), _db(session, window.until)
    done = _already_rolled_back(session)
    repository = SqlAlchemyProductionRepository(session)
    names = _names(session)

    applies = [row for row in session.scalars(select(AuditEventRow).where(
        AuditEventRow.event_type == APPLIED, AuditEventRow.occurred_at >= start, AuditEventRow.occurred_at < end,
    ).order_by(AuditEventRow.occurred_at, AuditEventRow.id)) if row.id not in done]
    existing: dict[UUID, ProductionTaskRow] = {}
    for apply in applies:
        plan.actor_id = plan.actor_id or apply.actor_id
        details = apply.details or {}
        plan.lines.append(
            f"Автоплан {_at(apply.occurred_at)} ({apply.actor_name}): запущено {len(details.get('created') or ())}, "
            f"расписано заданий {len(details.get('tasks') or ())}, операций {details.get('operations', '?')}"
        )
        refused = False
        rows = [session.get(ProductionTaskRow, task_id) for task_id in _launched_by_apply(session, apply)]
        for row in sorted((item for item in rows if item is not None), key=lambda item: item.number):
            # Нет строки — задание уже удалено (прошлый откат).
            if moscow(row.created_at) < window.since:
                plan.refused.append(f"{_task_label(row)}: создано до окна ({_at(row.created_at)}) — не трогаю")
                refused = True
                continue
            reason = _work_reason(session, repository, row)
            if reason:
                plan.refused.append(f"{_task_label(row)}: не удаляется — {reason}")
                refused = True
                continue
            operations = len(repository.get(row.id).operations)
            plan.delete_tasks[row.id] = row.number
            plan.lines.append(f"  удалить задание {_task_label(row)}: {row.quantity} шт, операций {operations}")
        created = {str(value) for value in details.get("created") or ()}
        for number in details.get("tasks") or ():
            if str(number) in created:
                continue
            row = session.scalar(select(ProductionTaskRow).where(ProductionTaskRow.number == str(number)))
            if row is not None and row.id not in plan.delete_tasks:
                existing.setdefault(row.id, row)
        if not refused:
            plan.audit_ids.add(apply.id)

    # Сдвиги плана в окне (по факту теста): задания, запущенные до окна.
    moved: dict[UUID, set[UUID]] = {}
    for event in session.scalars(select(AuditEventRow).where(
        AuditEventRow.event_type == REPLANNED, AuditEventRow.occurred_at >= start, AuditEventRow.occurred_at < end,
    ).order_by(AuditEventRow.occurred_at, AuditEventRow.id)):
        plan.audit_ids.add(event.id)
        if event.id in done or event.aggregate_id in plan.delete_tasks:
            continue
        row = session.get(ProductionTaskRow, event.aggregate_id)
        if row is None:
            continue
        existing.setdefault(row.id, row)
        for move in (event.details or {}).get("moves") or ():
            try:
                moved.setdefault(row.id, set()).add(UUID(str(move.get("operation_id"))))
            except (TypeError, ValueError):
                continue

    for row in sorted(existing.values(), key=lambda item: item.number):
        _collect_existing(session, plan, repository, row, moved.get(row.id, set()), names, start, end)

    for event in session.scalars(select(AuditEventRow).where(
        AuditEventRow.event_type == PLAN_EVENT_ADDED, AuditEventRow.occurred_at >= start, AuditEventRow.occurred_at < end,
    ).order_by(AuditEventRow.occurred_at, AuditEventRow.id)):
        if event.id in done:
            continue
        plan.audit_ids.add(event.id)
        plan.actor_id = plan.actor_id or event.actor_id
        stored = session.get(ProductionPlanEventRow, event.aggregate_id)
        if stored is None:
            continue
        what = stored.value or stored.text or stored.blocker or ""
        line = (f"удалить запись журнала плана {stored.project}: «{stored.kind}»"
                + (f" {stored.item_index}" if stored.item_index else "") + (f" = «{what}»" if what else "")
                + f" ({stored.who}, {_at(stored.occurred_at)})")
        newer = session.scalar(select(ProductionPlanEventRow.id).where(
            ProductionPlanEventRow.project == stored.project, ProductionPlanEventRow.sequence > stored.sequence,
        ).limit(1))
        if newer:
            line += " — после неё в журнале проекта есть записи, они остаются"
        plan.plan_event_rows.add(stored.id)
        plan.lines.append(line)

    if plan.delete_tasks:
        plan.baseline_lines = len(list(session.scalars(select(ProductionPlanBaselineLineRow.task_id).where(
            ProductionPlanBaselineLineRow.task_id.in_(plan.delete_tasks),
        ))))
        if plan.baseline_lines:
            plan.lines.append(f"строк базового плана удаляемых заданий: {plan.baseline_lines} — уходят вместе с ними")
        plan.lines.append("«Запущено» по КБ и этап «Распланировано» считаются по заданиям — вернутся сами")
    return plan


def _collect_existing(
    session: Session, plan: Plan, repository: SqlAlchemyProductionRepository, row: ProductionTaskRow,
    moved: set[UUID], names: dict[UUID, str], start, end,
) -> None:
    """Назначения задания, запущенного до окна, которые поставил автоплан или сдвинул факт в окне."""
    task = repository.get(row.id)
    if task is None:
        return
    candidates = [
        operation for operation in task.operations
        if (str(operation.assigned_by or "").startswith(AUTOPLAN_MARK) or operation.id in moved)
        and (operation.shares or operation.planned_start is not None or operation.planned_finish is not None)
    ]
    if not candidates:
        return
    label = _task_label(row)
    if _changed_after(session, row, end):
        plan.kept.append(f"{label}: план менялся после окна — назначения не трогаю")
        return
    manual: set[UUID] = set()
    whole_manual = False
    for event in session.scalars(select(AuditEventRow).where(
        AuditEventRow.aggregate_id == row.id, AuditEventRow.occurred_at >= start, AuditEventRow.occurred_at < end,
        AuditEventRow.event_type.in_({*MANUAL_PLAN_TYPES, "production.auto_scheduled"}),
    )):
        if event.event_type == "production.auto_scheduled":
            whole_manual = True
        try:
            manual.add(UUID(str((event.details or {}).get("operation_id"))))
        except (TypeError, ValueError):
            continue
    with_output = set(session.scalars(select(ProductionOutputRow.operation_id).where(
        ProductionOutputRow.task_id == row.id).distinct()))
    known_before = not _had_plan_before(session, row, start)
    for operation in candidates:
        what = f"{label} · этап {operation.position} «{stage_name(operation.stage)}» ({_assignment(operation, names)})"
        if any(share.started_at is not None or share.finished_at is not None for share in operation.shares) \
                or operation.id in with_output:
            plan.kept.append(f"{what}: по операции есть работа — не трогаю")
        elif whole_manual or operation.id in manual:
            plan.kept.append(f"{what}: назначено в окне вручную мастером — не трогаю")
        elif known_before:
            plan.clear_operations.setdefault(row.id, set()).add(operation.id)
            plan.lines.append(f"{what} → не назначено (до окна у задания плана не было)")
        elif str(operation.assigned_by or "").startswith(AUTOPLAN_MARK):
            plan.clear_operations.setdefault(row.id, set()).add(operation.id)
            plan.lines.append(f"{what} → не назначено: «до» журнал не хранит (план до окна был), снимаю назначение автоплана")
        else:
            plan.kept.append(f"{what}: сдвинуто по факту в окне, «до» журнал не хранит — пересчитается следующим назначением")


def execute(session: Session, plan: Plan, window: Window, now: datetime) -> None:
    """Откат по собранному плану в открытой транзакции сессии; фиксирует вызывающий."""
    repository = SqlAlchemyProductionRepository(session)
    for task_id in plan.delete_tasks:
        session.execute(delete(ProductionPlanBaselineLineRow).where(ProductionPlanBaselineLineRow.task_id == task_id))
        repository.delete(task_id)
    for task_id, operation_ids in plan.clear_operations.items():
        task = repository.get(task_id)
        operations = tuple(
            replace(operation, operator_ids=(), shares=(), planned_start=None, planned_finish=None, assigned_by=None)
            if operation.id in operation_ids else operation
            for operation in task.operations
        )
        repository.add(replace(task, operations=operations, version=task.version + 1))
    if plan.plan_event_rows:
        session.execute(delete(ProductionPlanEventRow).where(ProductionPlanEventRow.id.in_(plan.plan_event_rows)))
    actor_id = plan.actor_id or uuid4()
    SqlAlchemyAuditRepository(session).add(AuditEvent(
        id=uuid4(), aggregate_id=actor_id, event_type=EVENT_TYPE,
        actor=Actor(id=actor_id, name="откат автоплана", department=""),
        occurred_at=now, details={
            "since": moscow(window.since).isoformat(), "until": moscow(window.until).isoformat(),
            "lines": plan.lines, "kept": plan.kept, "refused": plan.refused,
            "deleted_tasks": sorted(plan.delete_tasks.values()),
            "cleared_operations": sorted(str(item) for items in plan.clear_operations.values() for item in items),
            "plan_events": sorted(str(item) for item in plan.plan_event_rows),
            "audit_ids": sorted(str(item) for item in plan.audit_ids),
        },
    ))
    session.flush()


def report(plan: Plan, window: Window, *, applied: bool) -> str:
    out = [f"Откат автоплана: окно {moscow(window.since):%d.%m.%Y %H:%M} – {moscow(window.until):%d.%m.%Y %H:%M} (МСК)"]
    if plan.empty:
        out.append("Откатывать нечего: применений автоплана и записей журнала плана в окне нет (или они уже откачены).")
    else:
        out.append("Откачено:" if applied else "Будет отменено:")
        out.extend(f"  {line}" for line in plan.lines)
    if plan.refused:
        out.append("Не удаляется (есть след работы — разберитесь вручную):")
        out.extend(f"  ! {line}" for line in plan.refused)
    if plan.kept:
        out.append("Не откатывается:")
        out.extend(f"  - {line}" for line in plan.kept)
    if not applied and not plan.empty:
        out.append("Проверка без записи. Откатить — та же команда с --apply вместо --dry-run.")
    return "\n".join(out)


def run(sessions: Callable[[], Session], *, since: datetime, until: datetime, apply: bool, now: datetime) -> tuple[str, int]:
    """Текст отчёта и код: 0 — проверено / откачено, 2 — неверные параметры."""
    if until <= since:
        return "Конец окна раньше начала", 2
    window = Window(since, until)
    with sessions() as session:
        plan = collect(session, window)
        if not apply or plan.empty:
            session.rollback()
            return report(plan, window, applied=False), 0
        execute(session, plan, window, now)
        session.commit()
        return report(plan, window, applied=True), 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Откатить применения «Автоплана» (запущенные им задания, его назначения) и записи журнала плана "
                    "за окно времени. Сначала --dry-run.",
    )
    parser.add_argument("--since", required=True, help="начало окна, МСК: \"2026-10-13 16:00\"")
    parser.add_argument("--until", help="конец окна, МСК (по умолчанию — сейчас)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать, что будет отменено")
    mode.add_argument("--apply", action="store_true", help="откатить одной транзакцией")
    args = parser.parse_args(list(argv) if argv is not None else None)
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
            since=since, until=until, apply=args.apply, now=now,
        )
    finally:
        engine.dispose()
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
