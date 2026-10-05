"""Личная карточка сотрудника — одна страница про человека из данных всех модулей (концепт владельца, 04.10).

Модель чтения: ничего не пишет и таблиц не заводит. Данные берутся у модулей через их unit of work (порты чтения), как
в `project_track` и `project_registry`; каждый раздел необязателен: нет фабрики модуля — раздел пустой (списки `[]`,
числа и часы `None`, никаких выдуманных нулей). Раздел без записей у модуля, который подключён, — настоящий пустой
результат: `count: 0`.

Разделы: шапка (ФИО, отдел, роль, площадка), кадровые поля (табельный номер, должность, категория, «в должности с»,
руководитель), подчинённые мастера площадки (флаги «Мастер площадки» и «Монтажник»), проекты (исполнитель по
операциям + инициатор заявок, срок из снимка плана), компетенции, отсутствия и ближайший отпуск, часы за текущий месяц, свои открытые заявки,
открытые задачи арбитра в разделах, которые человек правит.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.modules.identity.domain import (
    FULL_ACCESS_ROLES,
    ROLE_LABELS,
    WORK_SITES,
    AccessMode,
    IdentityUser,
    effective_permissions,
    role_permissions,
)
from app.modules.operations.domain import task_module_label
from app.modules.production.domain import ProductionStatus
from app.modules.production.plan import normalize_project_number, project_from_purpose
from app.modules.requests.domain import RequestStatus, project_reference_valid
from app.shared.clock import MOSCOW, as_utc
from app.workflows.project_registry import PlanSnapshotProjectRegistry

# Как и у трека проекта: у модулей нет выборки «по человеку», фильтр идёт после чтения (limit=10_000 у `kitset_facts`).
_LIMIT = 10_000
# Сколько записей показывает список в карточке; всего записей — в `count`.
LIST_LIMIT = 10

ROLE_EXECUTOR = "Исполнитель"
ROLE_INITIATOR = "Инициатор заявок"
VACATION = "отпуск"
_MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
_NOT_ACTIVE_REQUEST = (RequestStatus.CLOSED, RequestStatus.CANCELLED)


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _local(now: datetime) -> datetime:
    """«Сейчас» в Москве; время без пояса (тесты, SQLite) считаем московским."""
    return now.astimezone(MOSCOW) if now.tzinfo is not None else now.replace(tzinfo=MOSCOW)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = _local(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, following


# ── Человек и его права (Identity) ───────────────────────────────────────


def _edit_sections(uow, user: IdentityUser) -> frozenset[str]:
    """Разделы с правкой у человека — как при входе (`_principal` в Identity): роль, затем личные исключения; роли
    с полным доступом не сужаются ни матрицей, ни исключением."""
    base = role_permissions(user.role, uow.identity.role_access(user.role))
    effective = base if user.role in FULL_ACCESS_ROLES else effective_permissions(base, uow.identity.user_access(user.id))
    return frozenset(section for section, mode in effective.items() if mode is AccessMode.EDIT)


def _header(user: IdentityUser) -> dict[str, Any]:
    site = _text(user.site)
    return {
        "id": str(user.id),
        "name": user.name,
        "department": _text(user.department),
        "roles": [{"key": user.role, "label": ROLE_LABELS.get(user.role, user.role)}],
        "site": site,
        "site_label": WORK_SITES.get(site) if site else None,
        "active": user.active,
    }


def _personnel(user: IdentityUser, manager: IdentityUser | None) -> dict[str, Any]:
    """Кадровые поля из «Сотрудников» (миграция 0085): табельный номер, должность, категория, «в должности с»,
    руководитель. Не заполнено — `None`."""
    return {
        "tab_number": _text(user.personnel_number),
        "position": _text(user.job_title),
        "category": _text(user.qualification),
        "since": _iso(user.job_since),
        "manager_name": manager.name if manager is not None else None,
    }


# ── Производство: подчинённые, проекты, компетенции, отсутствия, часы ────


def _is_fitter(operator) -> bool:
    """Монтажник — активный сотрудник с флагом «Монтажник (работает в цеху)», как `Operator.installer`, но без
    исключения площадок без нормирования: люди ЖУК не расписываются по часам, а мастеру ЖУК всё равно подчиняются."""
    return operator.active and operator.shop_worker


@dataclass
class _Shop:
    subordinates: list[dict[str, Any]] = field(default_factory=list)
    executor_projects: set[str] = field(default_factory=set)
    deadlines: dict[str, date | None] = field(default_factory=dict)
    known_projects: set[str] = field(default_factory=set)
    skills: list[dict[str, Any]] = field(default_factory=list)
    absences: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    sessions: int = 0


def _read_shop(uow, user: IdentityUser, now: datetime) -> _Shop:
    shop = _Shop()
    today = _local(now).date()
    if user.site_master:
        # Флаг «Мастер площадки»: мастер видит монтажников своей площадки; без площадки — всех (как авторасчёт и
        # назначение в Production). Мастер, который сам работает в цеху, себе не подчинён.
        shop.subordinates = [
            {"id": str(item.id), "name": item.name, "site": _text(item.site), "department": _text(item.department)}
            for item in uow.operators.list()
            if _is_fitter(item) and item.id != user.id and (not user.site or item.site == user.site)
        ]
    for snapshot in uow.plan_projects.list():
        key = normalize_project_number(snapshot.project)
        shop.known_projects.add(key)
        shop.deadlines[key] = snapshot.deadline
    shop.skills = [
        {"operation": item.operation_code, "allowed": item.allowed, "stars": item.stars,
         "coefficient": format(item.coefficient.normalize(), "f")}
        for item in sorted(uow.skills.list(), key=lambda item: item.operation_code) if item.operator_id == user.id
    ]
    shop.absences = [
        {"kind": item.kind, "from_date": item.from_date.isoformat(), "to_date": item.to_date.isoformat()}
        for item in sorted(uow.calendar.absences(), key=lambda item: (item.from_date, item.to_date))
        if item.operator_id == user.id and item.to_date >= today
    ]
    month_start, month_end = _month_bounds(now)
    for task in uow.production.list(limit=_LIMIT):
        if not any(user.id in operation.operator_ids for operation in task.operations):
            continue
        if task.status is ProductionStatus.RUNNING:
            shop.executor_projects.add(normalize_project_number(task.project))
        # Работа отменённого задания — всё равно отработанное время. Сессия = выработка с `session_id`: у исправления
        # (`correction_of_id`) сессии нет, а время исходной выработки остаётся за ней.
        for output in uow.outputs.list_for_task(task.id):
            if output.operator_id != user.id or output.session_id is None:
                continue
            finished = as_utc(output.finished_at)
            if not month_start <= finished < month_end:
                continue
            shop.seconds += max(0.0, (finished - as_utc(output.started_at)).total_seconds() - output.pause_seconds)
            shop.sessions += 1
    return shop


# ── Заявки (Requests) ────────────────────────────────────────────────────


def _request_project(request) -> str:
    """Проект заявки: № проекта, иначе проект из назначения («708К.4 · КБ1 · …»), если он похож на номер проекта."""
    if request.project_reference:
        return normalize_project_number(request.project_reference)
    candidate = project_from_purpose(request.purpose)
    return candidate if project_reference_valid(candidate) else ""


@dataclass
class _Own:
    open_items: list[dict[str, Any]] = field(default_factory=list)
    open_count: int = 0
    projects: set[str] = field(default_factory=set)


def _read_requests(uow, user_id: UUID) -> _Own:
    own = _Own()
    # Новые сверху; закрытые и отменённые в «открытых» не считаются, отменённые и проект не дают.
    # Заявки проекта (заведены из расчёта под номером проекта) — не личные (владелец 04.10): они дают проект в
    # «Проекты», но в «Мои заявки на покупку» карточки не попадают.
    for request in uow.requests.by_initiator(user_id, _LIMIT, 0):
        if request.status is RequestStatus.CANCELLED:
            continue
        if project := _request_project(request):
            own.projects.add(project)
            continue
        if request.status in _NOT_ACTIVE_REQUEST:
            continue
        own.open_count += 1
        if len(own.open_items) < LIST_LIMIT:
            own.open_items.append({
                "id": str(request.id), "number": request.legacy_id, "name": request.name, "status": request.status.value,
            })
    return own


# ── Задачи арбитра (Operations) ──────────────────────────────────────────


def _read_tasks(uow, sections: frozenset[str]) -> dict[str, Any]:
    """Открытые задачи модулям: очередь, старая сверху. Задача адресована модулю, а не человеку, поэтому в карточке
    те, чей раздел человек правит."""
    mine = [task for task in uow.project_tasks.list(status="open", limit=_LIMIT) if task.module in sections]
    return {"count": len(mine), "items": [{
        "id": str(task.id), "project": task.project, "item_index": _text(task.item_index), "module": task.module,
        "module_label": task_module_label(task.module), "kind": task.kind, "title": task.title,
        "created_at": task.created_at.isoformat(),
    } for task in mine[:LIST_LIMIT]]}


# ── Сборка ───────────────────────────────────────────────────────────────


def _read(factory: Callable[[], Any] | None, reader: Callable[[Any], Any]) -> Any:
    if factory is None:
        return None
    with factory() as uow:
        return reader(uow)


def _projects(shop: _Shop | None, own: _Own | None, open_projects: list[str] | None) -> list[dict[str, Any]]:
    """Проекты человека: роли «Исполнитель» и «Инициатор заявок» вместе, по проекту одна строка. Закрытый в реестре
    проект не показывается; проект, которого нет в снимке плана, остаётся (закрыт он или нет — неизвестно)."""
    roles: dict[str, list[str]] = {}
    for project in sorted(shop.executor_projects) if shop else ():
        roles.setdefault(project, []).append(ROLE_EXECUTOR)
    for project in sorted(own.projects) if own else ():
        roles.setdefault(project, []).append(ROLE_INITIATOR)
    open_set = {normalize_project_number(project) for project in open_projects} if open_projects is not None else None
    closed = (shop.known_projects - open_set) if shop is not None and open_set is not None else set()
    deadlines = shop.deadlines if shop is not None else {}
    cards = [
        {"project": project, "roles": names, "deadline": _iso(deadlines.get(project))}
        for project, names in roles.items() if project and project not in closed
    ]
    return sorted(cards, key=lambda card: (card["deadline"] is None, card["deadline"] or "", card["project"]))


def employee_card(
    user_id: UUID, now: datetime, *,
    identity: Callable[[], Any],
    production: Callable[[], Any] | None = None,
    requests: Callable[[], Any] | None = None,
    operations: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    """Карточка сотрудника из фабрик unit of work модулей; нет фабрики — раздел пустой. `None` — такого сотрудника нет.

    `identity` обязателен: без человека карточки нет. Ничего не пишет."""
    def person(uow) -> tuple[IdentityUser, IdentityUser | None, frozenset[str]] | None:
        users = uow.identity.all_users()
        user = next((item for item in users if item.id == user_id), None)
        if user is None:
            return None
        manager = next((item for item in users if item.id == user.manager_id), None) if user.manager_id else None
        return user, manager, _edit_sections(uow, user)

    found = _read(identity, person)
    if found is None:
        return None
    user, manager, sections = found
    shop = _read(production, lambda uow: _read_shop(uow, user, now))
    own = _read(requests, lambda uow: _read_requests(uow, user.id))
    tasks = _read(operations, lambda uow: _read_tasks(uow, sections))
    # Закрытые проекты отсекает тот же реестр открытых проектов, что проверяет заявки (снимок плана + журнал).
    open_projects = PlanSnapshotProjectRegistry(production).open_projects() if production is not None else None
    month_start, _end = _month_bounds(now)
    absences = shop.absences if shop is not None else []
    return {
        "as_of": _local(now).date().isoformat(),
        "header": _header(user),
        "personnel": _personnel(user, manager),
        "subordinates": shop.subordinates if shop is not None else [],
        "projects": _projects(shop, own, open_projects),
        "skills": shop.skills if shop is not None else [],
        "absences": {
            "upcoming": absences,
            "next_vacation": next((item for item in absences if item["kind"].strip().casefold() == VACATION), None),
        },
        "hours_this_month": {
            "month": f"{month_start.year}-{month_start.month:02d}",
            "label": f"{_MONTHS[month_start.month - 1]} {month_start.year}",
            "hours": round(shop.seconds / 3600, 2) if shop is not None and shop.sessions else None,
            "sessions": shop.sessions if shop is not None else None,
        },
        "requests": {
            "count": own.open_count if own is not None else None,
            "items": own.open_items if own is not None else [],
        },
        "tasks": tasks if tasks is not None else {"count": None, "items": []},
    }
