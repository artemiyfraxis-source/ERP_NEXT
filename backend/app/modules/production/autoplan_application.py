"""Автоплан на период: загрузка данных, предпросмотр и применение (правила — autoplan.py).

Предпросмотр ничего не пишет и отдаёт отпечаток входных данных (задания с версиями, монтажники, компетенции,
календарь, выработка, техкарты, станки БД.ОБ). Применение пересчитывает план с тем же отпечатком — данные изменились
после предпросмотра → 409, — и пишет выбранные мастером операции одним коммитом через ProductionTask.apply_schedule:
по каждому заданию версия +1, назначенное раньше не трогается. Назначение помечается «Автоплан · <мастер>»."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .application import (
    UnitOfWork,
    _busy,
    _progress,
    _shift,
    _skills,
    _with_absences,
)
from .autoplan import (
    AUTOPLAN_MARK,
    AUTOPLAN_SITE,
    AutoplanInput,
    AutoplanResult,
    Batch,
    Placement,
    SetupKey,
    Unplaced,
    batches,
    operation_keys,
    plan,
)
from .dispatch import localize
from .domain import ProductionStatus, ProductionTask, ProductionValidationError
from .launchable import stage_name

MAX_PERIOD_DAYS = 31
# Начало расчёта округляется вверх до четверти часа: предпросмотр и применение через минуту дают один и тот же план.
_ROUND_MINUTES = 15


class AutoplanForbidden(PermissionError):
    pass


class StaleAutoplan(RuntimeError):
    pass


@dataclass(frozen=True)
class CardSnapshot:
    """Текущая выданная техкарта позиции: её документ — источник ключей наладки."""
    id: UUID
    version: int
    document: Mapping[str, Any] | None


def card_key(project: str, index: str) -> tuple[str, str]:
    return (" ".join(str(project or "").split()).casefold(), " ".join(str(index or "").split()).casefold())


@dataclass(frozen=True)
class AutoplanCommand:
    date_from: date
    date_to: date
    task_ids: tuple[UUID, ...] | None
    replan_auto: bool
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ApplyAutoplanCommand:
    date_from: date
    date_to: date
    task_ids: tuple[UUID, ...] | None
    replan_auto: bool
    items: tuple[tuple[UUID, UUID, UUID], ...]  # (задание, операция, монтажник) — подмножество предпросмотра
    fingerprint: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class AutoplanPreview:
    result: AutoplanResult
    unplaced: tuple[Unplaced, ...]
    warnings: tuple[str, ...]
    fingerprint: str
    tasks: dict[UUID, ProductionTask]
    names: dict[UUID, str]
    batches: dict[UUID, tuple[Batch, ...]]
    period: tuple[datetime, datetime]

    @property
    def placements(self) -> tuple[Placement, ...]:
        return self.result.placements


@dataclass(frozen=True)
class AppliedAutoplan:
    tasks: tuple[ProductionTask, ...]
    operations: int
    preview: AutoplanPreview


def _round_up(moment: datetime) -> datetime:
    floor = moment.replace(second=0, microsecond=0)
    if floor < moment:
        floor += timedelta(minutes=1)
    return floor + timedelta(minutes=(-floor.minute) % _ROUND_MINUTES)


def _period(command: AutoplanCommand) -> tuple[datetime, datetime]:
    if command.date_to < command.date_from:
        raise ProductionValidationError("Конец периода раньше начала")
    if (command.date_to - command.date_from).days + 1 > MAX_PERIOD_DAYS:
        raise ProductionValidationError(f"Период автоплана — не больше {MAX_PERIOD_DAYS} дней")
    if command.date_to < command.now.date():
        raise ProductionValidationError("Период уже прошёл")
    tz = command.now.tzinfo
    start = max(_round_up(command.now), datetime.combine(command.date_from, time.min, tz))
    return start, datetime.combine(command.date_to + timedelta(days=1), time.min, tz)


def _auto_movable(operation, progress: Decimal, start: datetime, end: datetime) -> bool:
    """Поставлено автопланом раньше, не начато и стоит в периоде — replan_auto может переставить."""
    return (
        str(operation.assigned_by or "").startswith(AUTOPLAN_MARK)
        and bool(operation.shares) and progress <= 0
        and all(share.started_at is None and share.finished_at is None for share in operation.shares)
        and operation.planned_start is not None and operation.planned_finish is not None
        and operation.planned_finish > start and operation.planned_start < end
    )


def _fingerprint(payload: dict) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _prepare(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
) -> AutoplanPreview:
    assigner = uow.operators.get(command.actor.id)
    if assigner is not None and assigner.site and assigner.site != AUTOPLAN_SITE:
        raise AutoplanForbidden(
            f"Автоплан расписывает площадку {AUTOPLAN_SITE}; мастер площадки {assigner.site} её не планирует"
        )
    start, end = _period(command)
    now = command.now
    # Все запущенные, по индексу статуса: первые 10 000 заданий ленты теряли запущенные за границей (U06).
    stored = list(uow.production.with_status(ProductionStatus.RUNNING))
    tasks = {item.id: localize(item, now) for item in stored}
    selected = set(command.task_ids) if command.task_ids is not None else None
    warnings: list[str] = []
    if selected is not None:
        for task_id in sorted(selected - set(tasks), key=str):
            warnings.append(f"Задание {task_id} не найдено среди запущенных — пропущено")
    eligible = [
        task for task in tasks.values()
        if (selected is None or task.id in selected) and not task.handed_to
    ]
    for task in sorted(tasks.values(), key=lambda value: value.number):
        if task.handed_to and selected is not None and task.id in selected:
            warnings.append(f"{task.number} отдано в {task.handed_to} — автоплан его не расписывает")
    progress = _progress(uow, [task.id for task in eligible])
    free: set[UUID] = set()
    unplaced: list[Unplaced] = []
    movable: set[UUID] = set()
    for task in eligible:
        for operation in task.operations:
            done = progress.by_operation.get(operation.id, Decimal(0))
            # Сдано в штуках операции: ветка «2 на изделие» сделана, когда сдано вдвое больше тиража.
            if done >= task.operation_units(operation.id):
                continue
            if not operation.shares:
                if done > 0:
                    unplaced.append(Unplaced(task.id, operation.id, "Работа уже начата без назначения — назначьте вручную"))
                else:
                    free.add(operation.id)
            elif command.replan_auto and _auto_movable(operation, done, start, end):
                free.add(operation.id)
                movable.add(operation.id)
    # Переставляемые операции идут в расчёт без своих прежних окон: их время свободно.
    planned_tasks = tuple(
        replace(task, operations=tuple(
            operation.with_shares((), operation.assigned_by) if operation.id in movable else operation
            for operation in task.operations
        )) if any(operation.id in movable for operation in task.operations) else task
        for task in sorted(tasks.values(), key=lambda value: (value.created_at, str(value.id)))
    )
    operators = list(uow.operators.list())
    team_rows = sorted(
        (item for item in operators if item.installer and item.site in (AUTOPLAN_SITE, "")),
        key=lambda item: (item.name, str(item.id)),
    )
    team = tuple(item.id for item in team_rows)
    if not team:
        warnings.append(f"Нет монтажников {AUTOPLAN_SITE} — ставить работу некому")
    absences = tuple(uow.calendar.absences())
    busy = _with_absences(_busy(planned_tasks, UUID(int=0), UUID(int=0), now), absences, team, now.tzinfo)
    skills = _skills(uow)
    holidays = uow.calendar.holidays()
    shift = _shift(uow.calendar)
    keys: dict[UUID, SetupKey] = {}
    used_cards: dict[str, int] = {}
    free_tasks = {task.id for task in eligible if any(operation.id in free for operation in task.operations)}
    for task in planned_tasks:
        planned = task.id in free_tasks or any(operation.planned_start for operation in task.operations)
        if not planned:
            continue
        card = cards.get(card_key(task.project, task.item))
        if card is not None:
            used_cards[str(card.id)] = card.version
        task_keys, task_warnings = operation_keys(task, card.document if card is not None else None)
        keys.update(task_keys)
        if task.id in free_tasks:
            warnings.extend(task_warnings)
    machines = sorted({key.machine for key in keys.values() if key.limited})
    capacity = {machine: max(1, int(units.get(machine, 0)) or 1) for machine in machines}
    needed = {keys[operation_id].machine for operation_id in free if keys[operation_id].limited}
    for machine in machines:
        if machine in needed and machine not in units:
            warnings.append(f"Станок {machine} не найден в БД.ОБ — считаю, что он один")
    data = AutoplanInput(
        tasks=planned_tasks, free=frozenset(free), keys=keys, team=team, skills=skills, busy=busy,
        capacity=capacity, start=start, end=end, holidays=holidays, shift=shift,
    )
    result = plan(data)
    fingerprint = _fingerprint({
        "period": [command.date_from, command.date_to], "replan_auto": command.replan_auto,
        "task_ids": sorted(str(value) for value in selected) if selected is not None else None,
        "actor_site": assigner.site if assigner is not None else "",
        "tasks": sorted([str(task.id), task.version] for task in stored),
        "team": [[str(item.id), item.name, item.site] for item in team_rows],
        "skills": sorted(
            [code, str(operator_id), skill.allowed, str(skill.coefficient)]
            for code, rows in skills.items() for operator_id, skill in rows.items()
        ),
        "absences": sorted([str(item.operator_id), item.from_date, item.to_date] for item in absences),
        "holidays": sorted(holidays), "shift": repr(shift),
        "progress": sorted([str(key), str(value)] for key, value in progress.by_operation.items()),
        "cards": sorted(used_cards.items()), "units": sorted(units.items()),
    })
    return AutoplanPreview(
        result=result,
        unplaced=(*unplaced, *result.unplaced),
        warnings=(*warnings, *result.warnings),
        fingerprint=fingerprint,
        tasks=tasks,
        names={item.id: item.name for item in operators},
        batches=batches(result.placements),
        period=(start, end),
    )


def preview_autoplan(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
) -> AutoplanPreview:
    with uow:
        return _prepare(command, uow, cards, units)


def apply_autoplan(
    command: ApplyAutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
) -> AppliedAutoplan:
    if not command.items:
        raise ProductionValidationError("Не выбрано ни одной операции")
    if len({item[1] for item in command.items}) != len(command.items):
        raise ProductionValidationError("Операция выбрана дважды")
    with uow:
        for task_id in sorted({item[0] for item in command.items}, key=str):
            uow.production.lock(task_id)
        preview = _prepare(AutoplanCommand(
            date_from=command.date_from, date_to=command.date_to, task_ids=command.task_ids,
            replan_auto=command.replan_auto, actor=command.actor, now=command.now,
        ), uow, cards, units)
        if preview.fingerprint != command.fingerprint:
            raise StaleAutoplan("Данные изменились после предпросмотра — пересчитайте автоплан")
        proposed = {item.operation_id: item for item in preview.placements}
        chosen: dict[UUID, dict[UUID, Placement]] = {}
        for task_id, operation_id, operator_id in command.items:
            placement = proposed.get(operation_id)
            if placement is None or placement.task_id != task_id or placement.operator_id != operator_id:
                raise StaleAutoplan("План изменился после предпросмотра — пересчитайте автоплан")
            chosen.setdefault(task_id, {})[operation_id] = placement
        mark = f"{AUTOPLAN_MARK}{command.actor.name}"
        updated: list[ProductionTask] = []
        for task_id in sorted(chosen, key=lambda value: preview.tasks[value].number):
            task = preview.tasks[task_id]
            own = chosen[task_id]
            result = task.apply_schedule(tuple(
                (operation.id, (own[operation.id].operator_id,), own[operation.id].start, own[operation.id].finish)
                if operation.id in own else
                (operation.id, operation.operator_ids, operation.planned_start, operation.planned_finish)
                for operation in task.operations
            ), mark)
            uow.production.add(result)
            updated.append(result)
        applied = [placement for own in chosen.values() for placement in own.values()]
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.actor.id, event_type="production.autoplan_applied",
            actor=command.actor, occurred_at=command.now,
            details={
                "date_from": command.date_from.isoformat(), "date_to": command.date_to.isoformat(),
                "master": command.actor.name, "replan_auto": command.replan_auto,
                "operations": len(applied), "tasks": [item.number for item in updated],
                "proposed": len(preview.placements), "unplaced": len(preview.unplaced),
                "changeovers": sum(1 for item in applied if item.changeover_seconds > 0),
                "saved_seconds": sum(item.setup_seconds - item.changeover_seconds for item in applied),
                "fingerprint": command.fingerprint,
            },
        ))
        uow.commit()
        return AppliedAutoplan(tuple(updated), len(applied), preview)


def describe(preview: AutoplanPreview) -> dict:
    """Ответ предпросмотра для экрана мастера."""
    tasks = preview.tasks
    operations = {operation.id: operation for task in tasks.values() for operation in task.operations}

    def placement(item: Placement) -> dict:
        task = tasks[item.task_id]
        operation = operations[item.operation_id]
        return {
            "task_id": str(item.task_id), "task_version": task.version, "task_number": task.number,
            "operation_id": str(item.operation_id), "position": operation.position,
            "stage": stage_name(operation.stage), "code": operation.code,
            "operator_id": str(item.operator_id), "operator_name": preview.names.get(item.operator_id, ""),
            "start": item.start.isoformat(), "finish": item.finish.isoformat(),
            "machine": item.key.machine_label, "setup_label": item.key.label,
            "changeover_seconds": item.changeover_seconds, "setup_seconds": item.setup_seconds,
            "batched": item.pulled,
        }

    result = preview.result
    return {
        "period": {"start": preview.period[0].isoformat(), "finish": preview.period[1].isoformat()},
        "proposals": [placement(item) for item in result.placements],
        "batches": [{
            "operator_id": str(operator_id), "operator_name": preview.names.get(operator_id, ""),
            "batches": [{
                "label": batch.label, "machine": batch.machine_label, "operations": len(batch.operations),
                "tasks": list(dict.fromkeys(tasks[item.task_id].number for item in batch.operations)),
                "start": batch.start.isoformat(), "finish": batch.finish.isoformat(),
                "changeover_seconds": batch.changeover_seconds,
            } for batch in own],
        } for operator_id, own in sorted(preview.batches.items(), key=lambda item: (preview.names.get(item[0], ""), str(item[0])))],
        "unplaced": [{
            "operation_id": str(item.operation_id), "task_id": str(item.task_id),
            "task_number": tasks[item.task_id].number if item.task_id in tasks else "",
            "position": operations[item.operation_id].position if item.operation_id in operations else None,
            "reason": item.reason,
        } for item in preview.unplaced],
        "warnings": list(dict.fromkeys(preview.warnings)),
        "stats": {
            "changeovers": result.changeovers, "changeovers_naive": result.changeovers_naive,
            "saved_seconds": result.saved_seconds,
        },
        "fingerprint": preview.fingerprint,
    }


def equipment_numbers(records: Sequence[Mapping[str, str]]) -> list[str]:
    """Инвентарные номера записей БД.ОБ (колонка «Инв. номер»)."""
    result: list[str] = []
    for fields in records:
        value = next((text for name, text in fields.items() if "инв" in str(name).casefold()), "")
        if str(value or "").strip():
            result.append(str(value))
    return result
