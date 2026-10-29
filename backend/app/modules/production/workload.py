"""«Загрузка»: загрузка цеха и прогноз готовности проектов без заданий (владелец 29.10.2026, идея №1).

Только чтение: ничего не пишет и ничего не требует от мастера. Работает на том, что в базе уже есть: выданные ЭТК
(нормы операций), тиражи КБ, факты «сдано цехом / принято складом / отгружено», календарь цеха и монтажники площадок.

Правила (все — здесь, чистыми функциями; чтение из модулей — `workload_api`):

* **Нормочасы КБ на одно изделие** — нормы маршрута выданной ЭТК (`launchable.launch_route`: та же норма, что у
  операций задания, без подготовки — владелец 18.10) × штук полуфабриката на изделие (`domain.unit_factors` по схеме
  ПФ). Разбиты по видам операций (`operation_kind`: код карточки операции, иначе название этапа).
* **Сделано** — лучший из фактов: сдано цехом (задания КБ), принято складом, отгружено. Берётся наибольший; при
  равенстве — первый по этому порядку. Подсказка на экране называет источник.
* **Осталось часов** = нормочасы на изделие × (тираж КБ − сделано), не меньше нуля.
* **Площадка КБ** — по заданиям: отдано площадке (ЖУК) → она; иначе площадка монтажников, назначенных на операции
  (больше назначений); нет — «не распределено».
* **Мощность дня** — монтажники площадки, кроме отсутствующих в этот день, × «чистое время» смены; выходные и
  праздники цеха — 0. Монтажник без площадки — ЦАР (площадка по умолчанию).
* **Прогноз** — проекты по сроку (без срока — в конце), потом по дате открытия и номеру; каждый забирает свободную
  мощность дней с сегодняшнего: часы площадки — её мощность, «не распределено» — любую свободную (ЦАР, потом ЖУК).
  День, в который ушёл последний час, — расчётная готовность. Позже срока — «не успевает», ближе 3 дней до срока —
  «риск».
* **Нужно к срокам** — часы проекта со сроком, разложенные поровну по рабочим дням до срока (просроченные — в первый
  рабочий день). Неделя, где нужно больше мощности, — перегруз; её крупнейшие виды операций — узкие места.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from .domain import ProductionValidationError, WorkShift, unit_factors
from .launchable import RouteStep, launch_operations

ZERO = Decimal(0)
HOUR = Decimal(3600)
SITES = ("ЦАР", "ЖУК")
DEFAULT_SITE = "ЦАР"
UNASSIGNED = "не распределено"
SHOP = "Весь цех"
RISK_DAYS = 3
HORIZON_DAYS = 371  # 53 недели: дальше «позже»

SOURCE_SHOP = "сдано цехом"
SOURCE_STOCK = "принято складом"
SOURCE_SHIPPED = "отгружено"

KINDS = ("Резка", "Зачистка", "Обжим", "Пайка", "Термоусадка", "Монтаж", "Маркировка", "Контроль", "Прочее")
OTHER = "Прочее"

# Префикс кода карточки операции БД.ОП (CUT_WIRE_auto, PRS_TERMINAL_hand, SDR_COAX_pin…) → вид операции.
_CODE_KINDS = {
    "CUT": "Резка", "STRIP": "Зачистка", "PRS": "Обжим", "SDR": "Пайка", "HEAT": "Термоусадка",
    "INS": "Монтаж", "OTK": "Контроль", "PRINT": "Маркировка", "LASER": "Маркировка",
}
# Название этапа → вид, по порядку: «Резка с обжимом» — резка, «Опрессовка втулки экрана» — обжим.
_NAME_KINDS = (
    ("Контроль", ("контрол", "отк", "ксвн", "провер", "испыт")),
    ("Термоусадка", ("усадк",)),
    ("Пайка", ("пайк", "лужен", "паян")),
    ("Резка", ("резк",)),
    ("Обжим", ("опрессов", "обжим")),
    ("Зачистка", ("разделк", "зачист")),
    ("Маркировка", ("маркир", "печать")),
    ("Монтаж", ("монтаж", "сборк", "установк", "надеть", "надев")),
)
_CODE_PREFIX = re.compile(r"^\s*([A-Za-z]+)")


def operation_kind(code: str, stage: str) -> str:
    """Вид операции для разбивки загрузки: по коду карточки операции, иначе по названию этапа; не узнан — «Прочее»."""
    prefix = _CODE_PREFIX.match(str(code or ""))
    if prefix and prefix.group(1).upper() in _CODE_KINDS:
        return _CODE_KINDS[prefix.group(1).upper()]
    name = str(stage or "").casefold()
    for kind, words in _NAME_KINDS:
        if any(word in name for word in words):
            return kind
    return OTHER


def unit_hours(steps: Sequence[RouteStep]) -> dict[str, Decimal]:
    """Нормочасы на одно изделие по видам операций: норма шага × штук его полуфабриката на изделие (схема ПФ).
    Схема не собирается в операции — штук на изделие 1 у каждого шага."""
    factors: dict[int, Decimal] = {}
    try:
        operations = launch_operations(steps, 1)
    except ProductionValidationError:
        operations = ()
    by_id = unit_factors(operations) if operations else {}
    for operation in operations:
        for position in operation.etk_positions:
            factors[position] = by_id.get(operation.id, Decimal(1))
    result: dict[str, Decimal] = {}
    for step in steps:
        seconds = step.seconds_per_unit * factors.get(step.position, Decimal(1))
        if seconds <= 0:
            continue
        kind = operation_kind(step.code, step.stage)
        result[kind] = result.get(kind, ZERO) + seconds / HOUR
    return result


@dataclass(frozen=True)
class Done:
    quantity: Decimal
    source: str  # SOURCE_* или "" — фактов нет


def done_of(delivered: Decimal, accepted: Decimal, shipped: Decimal) -> Done:
    """Сделано по КБ — наибольший из фактов; при равенстве — первый: сдано цехом, принято складом, отгружено."""
    best = Done(ZERO, "")
    for quantity, source in ((delivered, SOURCE_SHOP), (accepted, SOURCE_STOCK), (shipped, SOURCE_SHIPPED)):
        if quantity > best.quantity:
            best = Done(quantity, source)
    return best


@dataclass(frozen=True)
class KbLoad:
    project: str
    index: str
    name: str
    run: int | None
    done: Done
    site: str
    per_unit: Mapping[str, Decimal]  # нормочасы на изделие по видам
    site_note: str = ""

    @property
    def remaining(self) -> Decimal:
        return max(Decimal(self.run or 0) - self.done.quantity, ZERO)

    @property
    def by_kind(self) -> dict[str, Decimal]:
        left = self.remaining
        return {kind: hours * left for kind, hours in self.per_unit.items() if hours * left > 0}

    @property
    def hours(self) -> Decimal:
        return sum(self.by_kind.values(), ZERO)


@dataclass(frozen=True)
class ProjectLoad:
    project: str
    customer: str
    deadline: date | None
    opened_on: date | None
    kbs: tuple[KbLoad, ...]

    @property
    def hours(self) -> Decimal:
        return sum((kb.hours for kb in self.kbs), ZERO)

    @property
    def by_site(self) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for kb in self.kbs:
            if kb.hours > 0:
                result[kb.site] = result.get(kb.site, ZERO) + kb.hours
        return result

    def by_site_kind(self) -> dict[tuple[str, str], Decimal]:
        result: dict[tuple[str, str], Decimal] = {}
        for kb in self.kbs:
            for kind, hours in kb.by_kind.items():
                result[(kb.site, kind)] = result.get((kb.site, kind), ZERO) + hours
        return result


def kb_site(handed_to: Iterable[str], operator_sites: Iterable[str]) -> tuple[str, str]:
    """Площадка КБ и почему: отдано площадке → она; иначе площадка большинства назначений; нет — «не распределено»."""
    handed = [site for site in handed_to if site]
    if handed:
        site = max(set(handed), key=lambda item: (handed.count(item), item))
        return site, f"задание отдано в {site}"
    sites = [site or DEFAULT_SITE for site in operator_sites]
    if sites:
        site = max(set(sites), key=lambda item: (sites.count(item), item))
        return site, f"назначено монтажникам {site}"
    return UNASSIGNED, "нет заданий с исполнителями"


def plan_order(projects: Iterable[ProjectLoad]) -> list[ProjectLoad]:
    """Порядок прогноза: по сроку (без срока — в конце), потом по дате открытия, потом по номеру."""
    return sorted(projects, key=lambda item: (
        item.deadline is None, item.deadline or date.max, item.opened_on or date.max, item.project,
    ))


# ── Мощность ────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Staff:
    id: object
    site: str


@dataclass(frozen=True)
class AbsenceSpan:
    operator_id: object
    from_date: date
    to_date: date


@dataclass(frozen=True)
class Day:
    day: date
    working: bool
    capacity: Mapping[str, Decimal]  # площадка → часов


def is_working(day: date, shift: WorkShift, holidays: frozenset[date]) -> bool:
    return day.weekday() in shift.weekdays and day not in holidays


def capacity_days(
    start: date, count: int, shift: WorkShift, holidays: frozenset[date],
    staff: Sequence[Staff], absences: Sequence[AbsenceSpan],
) -> list[Day]:
    """Мощность площадок по дням: присутствующие монтажники × «чистое время»; выходной и праздник — 0."""
    per_person = Decimal(shift.effective_seconds) / HOUR
    away: dict[object, list[AbsenceSpan]] = {}
    for absence in absences:
        away.setdefault(absence.operator_id, []).append(absence)
    result: list[Day] = []
    for offset in range(count):
        day = start + timedelta(days=offset)
        working = is_working(day, shift, holidays)
        capacity = {site: ZERO for site in SITES}
        if working:
            for person in staff:
                if any(span.from_date <= day <= span.to_date for span in away.get(person.id, ())):
                    continue
                site = person.site if person.site in SITES else DEFAULT_SITE
                capacity[site] += per_person
        result.append(Day(day, working, capacity))
    return result


# ── Прогноз ─────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Forecast:
    project: str
    ready: date | None  # None — не помещается в горизонт
    status: str  # «в срок» / «риск» / «не успевает» / «без срока»
    slack_days: int | None = None  # срок − готовность, дней


def status_of(ready: date | None, deadline: date | None) -> tuple[str, int | None]:
    if deadline is None:
        return "без срока", None
    if ready is None:
        return "не успевает", None
    slack = (deadline - ready).days
    if slack < 0:
        return "не успевает", slack
    return ("риск" if slack < RISK_DAYS else "в срок"), slack


@dataclass
class Plan:
    forecasts: dict[str, Forecast]
    # День → площадка → (вид → часов) по прогнозу: что в этот день забрано из мощности.
    planned: list[dict[str, dict[str, Decimal]]] = field(default_factory=list)


def _take(free: dict[str, Decimal], site: str, need: Decimal) -> Decimal:
    taken = min(free.get(site, ZERO), need)
    free[site] = free.get(site, ZERO) - taken
    return taken


def forecast(projects: Sequence[ProjectLoad], days: Sequence[Day]) -> Plan:
    """Проекты по порядку забирают свободную мощность дней: часы площадки — её, «не распределено» — любую."""
    free = [dict(day.capacity) for day in days]
    planned: list[dict[str, dict[str, Decimal]]] = [{} for _ in days]
    forecasts: dict[str, Forecast] = {}
    for project in plan_order(projects):
        if project.hours <= 0:
            continue
        last, fits = -1, True
        work = project.by_site_kind()
        order = [key for key in work if key[0] in SITES] + [key for key in work if key[0] not in SITES]
        for site, kind in order:
            need = work[(site, kind)]
            pools = (site,) if site in SITES else SITES
            index = 0
            while need > 0 and index < len(days):
                for pool in pools:
                    taken = _take(free[index], pool, need)
                    if taken > 0:
                        need -= taken
                        slot = planned[index].setdefault(pool, {})
                        slot[kind] = slot.get(kind, ZERO) + taken
                        last = max(last, index)
                if need > 0:
                    index += 1
            fits = fits and need <= 0
        ready = days[last].day if fits and last >= 0 else None
        status, slack = status_of(ready, project.deadline)
        forecasts[project.project] = Forecast(project.project, ready, status, slack)
    return Plan(forecasts, planned)


def demand(projects: Sequence[ProjectLoad], days: Sequence[Day]) -> list[dict[str, dict[str, Decimal]]]:
    """Нужно к срокам: часы проекта со сроком поровну по рабочим дням до срока; просроченные — в первый рабочий день.
    Проекты без срока сюда не входят — они заполняют свободное время."""
    result: list[dict[str, dict[str, Decimal]]] = [{} for _ in days]
    working = [index for index, day in enumerate(days) if day.working]
    first = working[0] if working else 0
    for project in projects:
        if project.deadline is None or not days:
            continue
        span = [index for index in working if days[index].day <= project.deadline] or [first]
        share = Decimal(len(span))
        for (site, kind), hours in project.by_site_kind().items():
            for index in span:
                slot = result[index].setdefault(site, {})
                slot[kind] = slot.get(kind, ZERO) + hours / share
    return result


@dataclass(frozen=True)
class Week:
    start: date
    end: date
    capacity: Mapping[str, Decimal]  # площадка и «Весь цех»
    demand: Mapping[str, Mapping[str, Decimal]]  # площадка/«не распределено»/«Весь цех» → вид → часов
    planned: Mapping[str, Decimal]


def weeks(
    days: Sequence[Day], plan: Plan, needed: Sequence[Mapping[str, Mapping[str, Decimal]]], count: int,
) -> list[Week]:
    """Недели с понедельника недели первого дня: мощность, нужно к срокам и запланировано по прогнозу."""
    if not days:
        return []
    monday = days[0].day - timedelta(days=days[0].day.weekday())
    result: list[Week] = []
    for number in range(count):
        start = monday + timedelta(days=7 * number)
        end = start + timedelta(days=6)
        capacity: dict[str, Decimal] = {site: ZERO for site in (*SITES, SHOP)}
        load: dict[str, dict[str, Decimal]] = {}
        planned: dict[str, Decimal] = {site: ZERO for site in (*SITES, SHOP)}
        for index, day in enumerate(days):
            if not start <= day.day <= end:
                continue
            for site, hours in day.capacity.items():
                capacity[site] += hours
                capacity[SHOP] += hours
            for site, kinds in needed[index].items():
                for kind, hours in kinds.items():
                    for key in (site, SHOP):
                        slot = load.setdefault(key, {})
                        slot[kind] = slot.get(kind, ZERO) + hours
            for site, kinds in plan.planned[index].items():
                total = sum(kinds.values(), ZERO)
                planned[site] += total
                planned[SHOP] += total
        result.append(Week(start, end, capacity, load, planned))
    return result


@dataclass(frozen=True)
class Bottleneck:
    site: str
    start: date
    demand: Decimal
    capacity: Decimal
    kinds: tuple[tuple[str, Decimal], ...]  # крупнейшие виды операций недели


def bottlenecks(items: Sequence[Week], top: int = 3) -> list[Bottleneck]:
    """Перегруженные недели (нужно к срокам больше мощности) по площадкам и цеху, с крупнейшими видами операций."""
    result: list[Bottleneck] = []
    for week in items:
        for site in (*SITES, SHOP):
            kinds = week.demand.get(site, {})
            need = sum(kinds.values(), ZERO)
            if need <= 0 or need <= week.capacity.get(site, ZERO):
                continue
            ranked = sorted(kinds.items(), key=lambda item: (-item[1], KINDS.index(item[0]) if item[0] in KINDS else 99))
            result.append(Bottleneck(site, week.start, need, week.capacity.get(site, ZERO), tuple(ranked[:top])))
    return result
