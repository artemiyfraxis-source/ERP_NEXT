"""«Сегодня» мастера участка (владелец 29.10.2026, идея №5): один экран на смену — кто что делает, план на сегодня,
просроченное и что стоит и почему. Только чтение: ничего не пишет.

Правила (здесь — чистыми функциями; чтение из модулей — `app/workflows/production_today_api.py`):

* **Участок** — мастер (у сотрудника площадка ЦАР/ЖУК и нет полного доступа) видит свою, как доска мастера; полный доступ
  и сотрудник без площадки выбирают (по умолчанию ЦАР). Монтажник без площадки — ЦАР (`workload.DEFAULT_SITE`).
* **Сейчас** — у монтажника участка открытая работа (в работе / пауза) — операция, задание, с какого времени; иначе
  открытый простой; иначе отсутствие по календарю цеха (отпуск, больничный…); иначе «свободен». Факт сильнее календаря.
* **План на сегодня** — доли монтажников участка в живых заданиях (запущено, не отдано площадке), чьё плановое окно
  задевает сегодняшний день, плюс начатые и не сданные доли со вчерашним окном (тянутся). Сделано — сданное по доле
  (бригада — по операции), в штуках операции; готово — доля закончена или сдано всё.
* **Просрочено** — несданные доли, чей плановый конец прошёл; задания с прошедшим сроком; проекты «не успевает» из
  «Загрузки» с часами участка (или нераспределёнными).
* **Стоит и почему** — то, что не может начаться: у не начатой доли с окном до конца сегодняшнего дня — нет допуска
  исполнителя («Компетенции») или нет ни одной сдачи предыдущего этапа; у задания, по которому ещё не работали, —
  ЭТК не выдана или комплект КБ не набран; открытый простой; блокер проекта. У КБ из набора проекта участка без
  выданной ЭТК и без задания — «ЭТК не выдана». Каждая строка — причина словами и где это исправляют.
* **Площадка задания** — как площадка КБ в «Загрузке» (`workload.kb_site`): отдано площадке → она; иначе площадка
  большинства исполнителей; без исполнителей — «не распределено», такие задания видны на каждом участке.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Sequence
from uuid import UUID

from .domain import ProductionOperation, ProductionStatus, ProductionTask, SkillCheck
from .launchable import stage_name
from .workload import DEFAULT_SITE, SITES, UNASSIGNED, kb_site

ZERO = Decimal(0)

# Допуск монтажника к кодам операции (domain.skill_of по матрице компетенций) и ключ КБ (plan.kit_stage_key).
SkillLookup = Callable[[tuple[str, ...], UUID], SkillCheck]
KeyOf = Callable[[str, str], str]

# Где исправляют причину: ключ ссылки экрана → подпись кнопки.
FIX_LABELS = {
    "task": "Карточка задания",
    "skills": "Компетенции",
    "techcards": "Техкарты проекта",
    "issues": "Запросы на комплектацию",
    "prodplan": "Производственный план",
    "workload": "Загрузка",
}

STATE_DONE = "сделано"
STATE_RUNNING = "в работе"
STATE_NEW = "не начата"


def home_site(site: str | None) -> str:
    return site if site in SITES else DEFAULT_SITE


def resolve_site(viewer_site: str | None, full_access: bool, wanted: str | None) -> tuple[str, bool]:
    """Участок экрана и можно ли его переключать: мастер площадки — своя, без выбора; полный доступ и сотрудник без
    площадки — выбранная (неизвестная или пустая — ЦАР)."""
    if viewer_site in SITES and not full_access:
        return str(viewer_site), False
    return home_site(wanted), True


def day_bounds(day: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, now.tzinfo)
    return start, start + timedelta(days=1)


def live(task: ProductionTask) -> bool:
    """Задание в работе у монтажников: запущено и не отдано площадке целиком."""
    return task.status is ProductionStatus.RUNNING and not task.handed_to


def task_site(task: ProductionTask, site_of: Mapping[UUID, str]) -> str:
    """Площадка задания — как площадка КБ в «Загрузке»: отдано → та; иначе большинство исполнителей; нет — «не распределено»."""
    people = {
        operator_id for operation in task.operations
        for operator_id in (*operation.operator_ids, *(share.operator_id for share in operation.shares))
    }
    site, _note = kb_site((task.handed_to,) if task.handed_to else (), (site_of.get(person, "") for person in people))
    return site


def on_site(task: ProductionTask, site: str, site_of: Mapping[UUID, str]) -> bool:
    return task_site(task, site_of) in (site, UNASSIGNED)


# ── Доли ────────────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShareLine:
    """Доля монтажника с плановым окном: штуки и сданное — в штуках операции (как сдаёт монтажник)."""
    task: ProductionTask
    operation: ProductionOperation
    operator_id: UUID
    crew: bool
    planned_start: datetime
    planned_finish: datetime
    started_at: datetime | None
    finished_at: datetime | None
    quantity: Decimal
    done: Decimal
    working: bool = False  # открыта рабочая сессия монтажника по этой операции

    @property
    def finished(self) -> bool:
        return self.finished_at is not None or (self.quantity > 0 and self.done >= self.quantity)

    @property
    def started(self) -> bool:
        return self.started_at is not None or self.done > 0 or self.working

    @property
    def remaining(self) -> Decimal:
        return max(self.quantity - self.done, ZERO)

    @property
    def state(self) -> str:
        if self.finished:
            return STATE_DONE
        return STATE_RUNNING if self.started else STATE_NEW


def share_lines(
    tasks: Iterable[ProductionTask],
    by_operation: Mapping[UUID, Decimal],
    by_operator: Mapping[tuple[UUID, UUID], Decimal],
    operator_ids: Iterable[UUID],
    working: Iterable[tuple[UUID, UUID]] = (),
) -> list[ShareLine]:
    """Доли с плановым окном у монтажников operator_ids в живых заданиях. Операция, сданная целиком, закрывает все
    её доли; бригада считает сданное по операции, доля по штукам — своё. working — (операция, монтажник) открытых
    рабочих сессий: такая доля начата, даже если отметки «взял» ещё нет."""
    sessions = set(working)
    wanted = set(operator_ids)
    result: list[ShareLine] = []
    for task in tasks:
        if not live(task):
            continue
        for operation in task.operations:
            whole = task.operation_units(operation.id)
            total = by_operation.get(operation.id, ZERO)
            for share in operation.shares:
                if share.operator_id not in wanted or share.planned_start is None or share.planned_finish is None:
                    continue
                crew = share.quantity is None
                quantity = whole if crew else task.operation_units(operation.id, share.quantity)
                done = total if crew else by_operator.get((operation.id, share.operator_id), ZERO)
                if total >= whole > 0:
                    done = max(done, quantity)
                result.append(ShareLine(
                    task, operation, share.operator_id, crew, share.planned_start, share.planned_finish,
                    share.started_at, share.finished_at, quantity, done,
                    (operation.id, share.operator_id) in sessions,
                ))
    return result


def planned_today(lines: Iterable[ShareLine], day_start: datetime, day_end: datetime) -> list[ShareLine]:
    """План на сегодня: окно задевает день; начатая и не сданная доля со вчерашним окном тянется на сегодня."""
    result = [
        line for line in lines
        if (line.planned_start < day_end and line.planned_finish > day_start)
        or (line.planned_finish <= day_start and line.started and not line.finished)
    ]
    return sorted(result, key=lambda line: (line.planned_start, line.task.number, line.operation.position))


def overdue_lines(lines: Iterable[ShareLine], now: datetime) -> list[ShareLine]:
    """Просрочено: доля не сдана, а её плановый конец прошёл."""
    return sorted(
        (line for line in lines if not line.finished and line.planned_finish <= now),
        key=lambda line: (line.planned_finish, line.task.number, line.operation.position),
    )


def overdue_tasks(tasks: Iterable[ProductionTask], today: date) -> list[ProductionTask]:
    """Задания в работе (и отданные площадке) со сроком раньше сегодняшнего дня."""
    return sorted(
        (task for task in tasks if task.status is ProductionStatus.RUNNING and task.deadline and task.deadline < today),
        key=lambda task: (task.deadline, task.number),
    )


# ── Стоит и почему ──────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stuck:
    kind: str  # skill / input / etk / kit / downtime / blocker
    project: str
    item: str
    reason: str
    fix: str  # ключ FIX_LABELS
    task: ProductionTask | None = None
    operation: ProductionOperation | None = None
    operator_id: UUID | None = None
    since: datetime | None = None


def waiting_input(line: ShareLine, by_operation: Mapping[UUID, Decimal]) -> ProductionOperation | None:
    """Предыдущий этап, от которого не пришло ни одной штуки: доля не может начаться (нечего брать на вход)."""
    by_id = {operation.id: operation for operation in line.task.operations}
    for edge in sorted(line.operation.inputs, key=lambda item: by_id[item.input_operation_id].position
                       if item.input_operation_id in by_id else 0):
        source = by_id.get(edge.input_operation_id)
        if source is not None and by_operation.get(source.id, ZERO) <= 0:
            return source
    return None


def blocked_shares(
    lines: Iterable[ShareLine],
    day_end: datetime,
    by_operation: Mapping[UUID, Decimal],
    skill: SkillLookup,
    names: Mapping[UUID, str],
) -> list[Stuck]:
    """Не начатые доли с окном до конца сегодняшнего дня, которые начать нельзя: нет допуска, нет входа."""
    result: list[Stuck] = []
    for line in lines:
        if line.started or line.finished or line.planned_start >= day_end:
            continue
        task, operation = line.task, line.operation
        name = names.get(line.operator_id, "монтажника")
        check = skill(operation.skill_codes, line.operator_id)
        if check.checked and not check.allowed:
            result.append(Stuck(
                "skill", task.project, task.item,
                f"Нет допуска у {name} к «{stage_name(operation.stage)}»", "skills", task, operation, line.operator_id,
            ))
        source = waiting_input(line, by_operation)
        if source is not None:
            result.append(Stuck(
                "input", task.project, task.item,
                f"Ждёт этап {source.position} «{stage_name(source.stage)}»: от него ещё ничего не сдано",
                "task", task, operation, line.operator_id,
            ))
    return result


def untouched(task: ProductionTask, by_operation: Mapping[UUID, Decimal], worked: Iterable[UUID]) -> bool:
    """По заданию ещё не работали: ни одной взятой доли, ни одной сдачи, ни одной рабочей сессии."""
    return task.id not in set(worked) and not any(
        share.started_at is not None or share.finished_at is not None
        for operation in task.operations for share in operation.shares
    ) and not any(by_operation.get(operation.id, ZERO) > 0 for operation in task.operations)


def kit_text(status: str | None, number: str = "") -> str:
    if not status:
        return "Комплект КБ не набран: запроса на комплектацию нет"
    return f"Комплект КБ не набран: запрос {number} — «{status}»" if number else f"Комплект КБ не набран: «{status}»"


def blocked_tasks(
    tasks: Iterable[ProductionTask],
    by_operation: Mapping[UUID, Decimal],
    worked: Iterable[UUID],
    issued: frozenset[str],
    picked: frozenset[str],
    kit_status: Mapping[str, tuple[str, str]],
    key: KeyOf,
) -> list[Stuck]:
    """Задания, по которым ещё не работали и которые начать нельзя: ЭТК КБ не выдана, комплект не набран."""
    worked = set(worked)
    result: list[Stuck] = []
    for task in tasks:
        if task.status is not ProductionStatus.RUNNING or not untouched(task, by_operation, worked):
            continue
        kb = key(task.project, task.item)
        if kb not in issued:
            result.append(Stuck("etk", task.project, task.item, "ЭТК КБ не выдана (техкарта в черновике)", "techcards", task))
        if kb not in picked:
            status, number = kit_status.get(kb, ("", ""))
            result.append(Stuck("kit", task.project, task.item, kit_text(status, number), "issues", task))
    return result


def missing_etk(
    assemblies: Iterable[tuple[str, str, str]],
    projects: Iterable[str],
    issued: frozenset[str],
    launched: frozenset[str],
    closed: frozenset[str],
    key: KeyOf,
) -> list[Stuck]:
    """КБ набора проектов участка (номер проекта как в projects, КБ, наименование) без выданной ЭТК, без задания и не
    закрытые складом: запустить нечего."""
    wanted = set(projects)
    result: list[Stuck] = []
    for project, index, name in assemblies:
        kb = key(project, index)
        if project not in wanted:
            continue
        if kb in issued or kb in launched or kb in closed:
            continue
        reason = "ЭТК не выдана — задание не запустить" + (f" ({name})" if name else "")
        result.append(Stuck("etk", project, index, reason, "techcards"))
    return result


def stuck_order(item: Stuck) -> tuple:
    kinds = ("downtime", "blocker", "skill", "input", "etk", "kit")
    return (item.project, item.item, kinds.index(item.kind) if item.kind in kinds else 99,
            item.operation.position if item.operation else 0)


def plan_counts(lines: Sequence[ShareLine]) -> tuple[int, int]:
    """Сколько долей на сегодня и сколько из них сделано."""
    return len(lines), sum(1 for line in lines if line.finished)
