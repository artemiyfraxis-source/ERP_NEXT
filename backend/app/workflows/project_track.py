"""Трек проекта — всё, что по одному проекту создано во всех модулях, в порядке времени (ARCH-TRACK-3, C1).

Целевая модель (ARCHITECTURE.md «Один проект — один трек»): карточка проекта показывает, что по нему сделано —
от расчёта до отгрузки. Это модель чтения: ничего не пишет и не заводит таблиц. Записи берутся у модулей через их
собственные unit of work (порты чтения), а не из `audit_events` — у журнала нет колонки проекта.

Каждый раздел необязателен: нет фабрики модуля — раздел пропущен, как `kitset_facts` читает необязательные модули. Проект сопоставляется после нормализации
(`normalize_project_number` / `project_key`), так что «719К.2» не цепляет «719К.21».
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Iterable

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.operations.domain import task_module_label
from app.modules.production.plan import kit_key_from_purpose, normalize_project_number, project_from_purpose
from app.modules.techcards.kitsets import item_id, project_key
from app.modules.techcards.project_cards import TechcardStatus
from app.workflows.project_kitset import request_matches_project

# Названия разделов — как в меню ERP.
MODULE_PROJECTS = "Проекты"
MODULE_TECHCARDS = "Техкарты"
MODULE_REQUESTS = "Заявки"
MODULE_PROCUREMENT = "Закупка"
MODULE_RECEIVING = "Приёмка"
MODULE_FULFILLMENT = "Комплектация"
MODULE_PRODUCTION = "Производство"
MODULE_SHIPPING = "Отгрузка"
# Задачи, которые арбитр поставил модулям по проекту (C2, `app.workflows.arbiter`).
MODULE_TASKS = "Задачи"

# Каждый модуль отдаёт только записи этого проекта (`for_project`: точный ключ № проекта по индексу, U06), без общего
# лимита: старый проект не пропадает за границей «первых N» всей ERP, а чтение растёт с проектом, а не со всей базой.
# Задачи арбитра и так выбираются по проекту; их предел — на один проект, недостижимый на деле.
_PROJECT_TASKS_LIMIT = 100_000


@dataclass(frozen=True)
class TrackEntry:
    """Одна запись трека. `at` — момент события; None — события ещё нет или его дата не записана (такие идут в конце)."""

    at: datetime | None
    module: str
    kind: str
    ref: str
    status: str
    item_index: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat() if self.at is not None else None,
            "module": self.module, "kind": self.kind, "ref": self.ref, "status": self.status,
            "item_index": self.item_index,
        }


def _day(value: date | None) -> datetime | None:
    return None if value is None else datetime.combine(value, time.min)


def _kb(*purposes: object) -> str:
    """КБ из НАЗНАЧЕНИЯ «708К.4 · КБ1 · Жгут»; пусто, когда в тексте КБ нет."""
    for purpose in purposes:
        _project, _bar, index = kit_key_from_purpose(purpose).partition("|")
        if index:
            return index
    return ""


def _stamp(value: datetime) -> float:
    """Сравнимое время: SQLite отдаёт naive, PostgreSQL — aware; naive считаем UTC, как план производства."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).timestamp()


def _read(factory: Callable[[], Any] | None, reader: Callable[[Any], list[TrackEntry]]) -> list[TrackEntry]:
    if factory is None:
        return []
    with factory() as uow:
        return reader(uow)


# ── Проекты и техкарты (Techcards) ──────────────────────────────────────


def _project_entries(uow, project: str) -> list[TrackEntry]:
    kitset = uow.kitsets.get(project)
    entries: list[TrackEntry] = []
    if kitset is not None:
        entries.append(TrackEntry(kitset.created_at, MODULE_PROJECTS, "Карточка проекта из расчёта", kitset.project, "Создана"))
        if kitset.updated_at > kitset.created_at:
            entries.append(TrackEntry(kitset.updated_at, MODULE_PROJECTS, "Карточка проекта", kitset.project, "Обновлена"))
    canon = project_key(project)
    for card in sorted((card for card in uow.techcards.for_project(project) if project_key(card.project) == canon),
                       key=lambda card: (card.item_index, card.created_at)):
        ref = item_id(card.project, card.item_index)
        index = card.item_index.strip().upper()
        entries.append(TrackEntry(card.created_at, MODULE_TECHCARDS, "Техкарта изделия", ref, "Черновик", index))
        if card.status is TechcardStatus.ISSUED:
            revision = f" (редакция {card.revision})" if card.revision > 1 else ""
            entries.append(TrackEntry(card.issued_at, MODULE_TECHCARDS, "Техкарта выдана", ref + revision, "Выдана", index))
    return entries


# ── Заявки, закупка, приёмка ─────────────────────────────────────────────


def _request_entries(uow, canon: str, kb_of_request: dict[Any, str]) -> list[TrackEntry]:
    """Заявки проекта; `kb_of_request` заполняется для закупки: {id заявки: КБ}."""
    entries: list[TrackEntry] = []
    for request in sorted((request for request in uow.requests.for_project(canon) if request_matches_project(canon, request)),
                          key=lambda request: (request.created_at, request.legacy_id)):
        index = _kb(request.purpose, request.designation)
        kb_of_request[request.id] = index
        entries.append(TrackEntry(
            request.created_at, MODULE_REQUESTS, "Заявка на закупку", request.legacy_id or str(request.id),
            request.status.value, index,
        ))
    return entries


def _procurement_entries(
    uow, canon: str, kb_of_request: dict[Any, str], kb_of_line: dict[Any, str],
) -> list[TrackEntry]:
    """Строки закупки проекта — по заявке или по № проекта; `kb_of_line` заполняется для приёмки: {id строки: КБ}."""
    entries: list[TrackEntry] = []
    lines = [
        line for line in uow.procurement.for_project(canon, tuple(kb_of_request))
        if line.source_request_id in kb_of_request
        or (line.project_reference and normalize_project_number(line.project_reference) == canon)
    ]
    for line in sorted(lines, key=lambda line: (line.created_at, line.legacy_id)):
        index = kb_of_request.get(line.source_request_id, "")
        kb_of_line[line.id] = index
        ref = line.legacy_id or str(line.id)
        entries.append(TrackEntry(line.created_at, MODULE_PROCUREMENT, "Позиция закупки", ref, line.status.value, index))
        if line.paid_at is not None:
            entries.append(TrackEntry(line.paid_at, MODULE_PROCUREMENT, "Оплата поставщику", ref, "Оплачено", index))
    return entries


def _receiving_entries(uow, canon: str, kb_of_line: dict[Any, str]) -> list[TrackEntry]:
    entries: list[TrackEntry] = []
    mine = [
        line for line in uow.receiving.for_project(canon, tuple(kb_of_line))
        if line.procurement_line_id in kb_of_line
        or canon in (project_from_purpose(line.purpose), project_from_purpose(line.goal))
    ]
    for line in sorted(mine, key=lambda line: (line.created_at, line.legacy_id)):
        index = kb_of_line.get(line.procurement_line_id) or _kb(line.purpose, line.goal)
        entries.append(TrackEntry(
            line.created_at, MODULE_RECEIVING, "Поставка ожидается", line.legacy_id, line.status.value, index,
        ))
        if line.inspected_on is not None:
            entries.append(TrackEntry(
                _day(line.inspected_on), MODULE_RECEIVING, "Входной контроль", line.grn or line.legacy_id,
                line.status.value, index,
            ))
    return entries


# ── Комплектация, производство, отгрузка ─────────────────────────────────


def _fulfillment_entries(uow, canon: str) -> list[TrackEntry]:
    entries: list[TrackEntry] = []
    mine = [request for request in uow.fulfillment.for_project(canon) if project_from_purpose(request.purpose) == canon]
    for request in sorted(mine, key=lambda request: (request.created_at, request.number)):
        index = _kb(request.purpose)
        entries.append(TrackEntry(
            request.created_at, MODULE_FULFILLMENT, "Запрос на комплектацию", request.number, request.status.value, index,
        ))
        if request.at_issued is not None:
            entries.append(TrackEntry(
                request.at_issued, MODULE_FULFILLMENT, "Комплект выдан в цех", request.number,
                FulfillmentStatus.ISSUED.value, index,
            ))
    return entries


def _production_entries(uow, canon: str) -> list[TrackEntry]:
    tasks = sorted(
        (task for task in uow.production.for_project(canon) if normalize_project_number(task.project) == canon),
        key=lambda task: (task.created_at, task.number),
    )
    # Операция выполнена, когда по ней сдано не меньше количества задания (`prodOpState_` в production/api.py).
    done: dict[Any, list[Any]] = {}
    if tasks:
        for output in uow.outputs.active_for_tasks(tuple(task.id for task in tasks)):
            totals = done.setdefault(output.operation_id, [0, output.finished_at])
            totals[0] += output.good_quantity + output.defect_quantity
            totals[1] = max(totals[1], output.finished_at, key=_stamp)
    entries: list[TrackEntry] = []
    for task in tasks:
        index = task.item.strip().upper()
        entries.append(TrackEntry(
            task.created_at, MODULE_PRODUCTION, "Задание запущено", task.number, task.status.value, index,
        ))
        for operation in task.operations:
            processed, finished_at = done.get(operation.id, (0, None))
            if processed and processed >= task.operation_units(operation.id):
                entries.append(TrackEntry(
                    finished_at, MODULE_PRODUCTION, "Операция выполнена",
                    f"{task.number} · {operation.stage or operation.code}", "Выполнена", index,
                ))
    return entries


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _good_entries(uow, canon: str) -> list[TrackEntry]:
    entries: list[TrackEntry] = []
    mine = [good for good in uow.goods.for_project(canon) if normalize_project_number(good.project) == canon]
    for good in sorted(mine, key=lambda good: (good.item_index, good.article)):
        index = good.item_index.strip().upper()
        ref = good.grn or good.article or item_id(good.project, good.item_index)
        if good.accepted > 0:
            status = f"Принято {_number(good.accepted)} из {_number(good.ordered)} {good.unit}".strip()
            entries.append(TrackEntry(
                _day(good.received_at), MODULE_SHIPPING, "Готовая продукция принята", ref, status, index,
            ))
        else:
            status = f"В очереди: {_number(good.ordered)} {good.unit}".strip()
            entries.append(TrackEntry(None, MODULE_SHIPPING, "Готовая продукция ожидается", ref, status, index))
    return entries


def _shipment_entries(uow, canon: str) -> list[TrackEntry]:
    entries: list[TrackEntry] = []
    for shipment in sorted(uow.shipments.for_project(canon), key=lambda shipment: (shipment.created_at, shipment.number)):
        lines = [line for line in shipment.lines if normalize_project_number(line.project) == canon]
        if not lines:
            continue
        indexes = sorted({line.item_index.strip().upper() for line in lines if line.item_index.strip()})
        index = ", ".join(indexes)
        entries.append(TrackEntry(
            shipment.created_at, MODULE_SHIPPING, "Отправка оформлена", shipment.number, shipment.status.value, index,
        ))
        if shipment.shipped_at is not None:
            entries.append(TrackEntry(
                _day(shipment.shipped_at), MODULE_SHIPPING, "Отправка уехала", shipment.number, "Отгружена", index,
            ))
    return entries


def _task_entries(uow, canon: str) -> list[TrackEntry]:
    """Задача модулю: вид — её текст, номер — кому она («Производство»), статус — «Открыта»/«Закрыта»."""
    return [
        TrackEntry(task.created_at, MODULE_TASKS, task.title, task_module_label(task.module), task.status_label,
                   task.item_index)
        for task in uow.project_tasks.list(project=canon, limit=_PROJECT_TASKS_LIMIT)
    ]


def _order(entries: Iterable[TrackEntry]) -> list[TrackEntry]:
    """По времени; без даты — в конец. Сортировка устойчива: при равном времени остаётся порядок разделов."""
    return sorted(entries, key=lambda entry: (entry.at is None, 0.0 if entry.at is None else _stamp(entry.at)))


def project_track(
    project: str, *,
    techcards: Callable[[], Any] | None = None,
    requests: Callable[[], Any] | None = None,
    procurement: Callable[[], Any] | None = None,
    receiving: Callable[[], Any] | None = None,
    fulfillment: Callable[[], Any] | None = None,
    production: Callable[[], Any] | None = None,
    shipping: Callable[[], Any] | None = None,
    tasks: Callable[[], Any] | None = None,
) -> list[TrackEntry]:
    """Трек проекта из фабрик unit of work модулей; нет фабрики — раздел пропущен. Ничего не пишет.
    `tasks` — unit of work операций (задачи арбитра); при равном времени задача идёт после записи, что её родила."""
    canon = normalize_project_number(project)
    if not canon:
        return []
    kb_of_request: dict[Any, str] = {}
    kb_of_line: dict[Any, str] = {}
    entries = _read(techcards, lambda uow: _project_entries(uow, project))
    entries += _read(requests, lambda uow: _request_entries(uow, canon, kb_of_request))
    entries += _read(procurement, lambda uow: _procurement_entries(uow, canon, kb_of_request, kb_of_line))
    entries += _read(receiving, lambda uow: _receiving_entries(uow, canon, kb_of_line))
    entries += _read(fulfillment, lambda uow: _fulfillment_entries(uow, canon))
    entries += _read(production, lambda uow: _production_entries(uow, canon))
    entries += _read(shipping, lambda uow: _good_entries(uow, canon) + _shipment_entries(uow, canon))
    entries += _read(tasks, lambda uow: _task_entries(uow, canon))
    return _order(entries)
