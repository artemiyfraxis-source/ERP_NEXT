"""«Рабочее время» в личной карточке (фаза 3, T2): месяц по дням — смена (норма), отработано, переработка — и баланс
года: переработка минус отгулы. Модель чтения: ничего не пишет и таблиц не заводит.

Правила:
- норма дня — `effective_seconds` смены цеха в её рабочие дни; 0 в выходной, праздник цеха и в день с любым отсутствием
  (календарь цеха или зарегистрированное заявление, если календарь его ещё не получил);
- отработано — по данным выработки: закрытые сессии человека (`finished − started − pause`). Исправление выработки
  (`correction_of_id`) сессии не имеет и времени не добавляет: цепочка исправлений считается один раз — временем своей
  сессии и за её исполнителем. Пересекающиеся сессии сливаются: время слитого отрезка — не больше его длины по часам.
  День — московский; сессия через полночь делится по дням;
- переработка дня — отработано сверх нормы (работа в выходной — целиком);
- зарегистрированный отгул (заявление `day_off`) стоит норму смены за каждый рабочий день смены в нём;
- баланс — с 1 января года месяца по конец месяца, на следующий год не переносится (как норма отпуска); меньше нуля —
  «долг с отработкой». Переработка справочная: не согласована и не оплачивается;
- «нет данных» — у площадок без нормирования (ЖУК, `UNPLANNED_SITES`) и у тех, у кого с 1 января по конец месяца нет
  ни одной сессии выработки.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.modules.identity.leave import KIND_LABELS, LeaveKind, LeaveStatus
from app.modules.production.domain import UNPLANNED_SITES, WorkShift
from app.shared.clock import MOSCOW, as_utc

SOURCE = "по данным выработки"
NOTE = "Переработка справочная: не согласована и не оплачивается."
DEBT = "долг с отработкой"
_STATES = {"debt": DEBT, "overtime": "переработка", "even": "ноль"}
_MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
# Как у карточки: у Production нет выборки «по человеку», фильтр идёт после чтения.
_LIMIT = 10_000


def _local(now: datetime) -> datetime:
    """Время в Москве; без пояса (тесты, SQLite) — уже московское."""
    return now.astimezone(MOSCOW) if now.tzinfo is not None else now.replace(tzinfo=MOSCOW)


def parse_month(value: str | None, now: datetime) -> date:
    """`YYYY-MM` → первое число месяца; не задан — текущий месяц по Москве. Неверный формат — `ValueError`."""
    if value is None or not value.strip():
        today = _local(now).date()
        return today.replace(day=1)
    match = _MONTH.match(value.strip())
    if match is None or not 2000 <= int(match.group(1)) <= 2100:
        raise ValueError("Месяц нужен в виде ГГГГ-ММ, например 2026-10.")
    return date(int(match.group(1)), int(match.group(2)), 1)


def _next_month(first: date) -> date:
    return date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)


def _days(start: date, stop: date) -> Iterable[date]:
    """Дни `[start, stop)`."""
    for offset in range((stop - start).days):
        yield start + timedelta(days=offset)


def _span_days(from_date: date, to_date: date, start: date, stop: date) -> Iterable[date]:
    """Дни отсутствия `[from_date, to_date]` внутри окна `[start, stop)`."""
    return _days(max(from_date, start), min(to_date + timedelta(days=1), stop))


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=MOSCOW)


def _hours(seconds: float) -> float:
    return round(seconds / 3600, 2)


# ── Отработано: сессии выработки → секунды по московским дням ─────────────


@dataclass(frozen=True)
class WorkSpan:
    started: datetime
    finished: datetime
    net_seconds: float


def worked_by_day(sessions: Iterable[WorkSpan], start: date, stop: date) -> dict[date, float]:
    """Сессии → секунды по дням `[start, stop)`. Пересекающиеся сессии сливаются в отрезок; время отрезка — сумма чистого
    времени его сессий, но не больше длины отрезка. Отрезок через полночь делится по дням пропорционально длине."""
    clusters: list[list[Any]] = []  # [начало, конец, чистые секунды]
    for item in sorted(sessions, key=lambda item: item.started):
        if item.finished <= item.started:
            continue
        if clusters and item.started < clusters[-1][1]:
            last = clusters[-1]
            last[1] = max(last[1], item.finished)
            last[2] += item.net_seconds
        else:
            clusters.append([item.started, item.finished, item.net_seconds])
    window_start, window_stop = _midnight(start), _midnight(stop)
    result: dict[date, float] = {}
    for begin, end, net in clusters:
        length = (end - begin).total_seconds()
        rate = min(length, max(net, 0.0)) / length
        cursor = max(begin, window_start).astimezone(MOSCOW)
        end = min(end, window_stop)
        while cursor < end:
            day = cursor.date()
            boundary = min(_midnight(day + timedelta(days=1)), end)
            seconds = (boundary - cursor).total_seconds() * rate
            if seconds > 0:
                result[day] = result.get(day, 0.0) + seconds
            cursor = boundary
    return result


# ── Чтение модулей ───────────────────────────────────────────────────────


@dataclass
class _Person:
    site: str
    leaves: list[Any] = field(default_factory=list)  # зарегистрированные заявления


@dataclass
class _Shop:
    shift: WorkShift
    holidays: frozenset[date]
    absences: dict[date, str]
    sessions: list[WorkSpan]


def _read_person(uow, user_id: UUID) -> _Person | None:
    user = next((item for item in uow.identity.all_users() if item.id == user_id), None)
    if user is None:
        return None
    leaves = [item for item in uow.leave.for_employee(user_id) if item.status is LeaveStatus.REGISTERED]
    return _Person(site=str(user.site or "").strip(), leaves=leaves)


def _read_shop(uow, user_id: UUID, start: date, stop: date) -> _Shop:
    absences: dict[date, str] = {}
    for item in uow.calendar.absences():
        if item.operator_id != user_id:
            continue
        for day in _span_days(item.from_date, item.to_date, start, stop):
            absences.setdefault(day, item.kind)
    window_start, window_stop = _midnight(start), _midnight(stop)
    tasks = [task.id for task in uow.production.list(limit=_LIMIT)]
    sessions: list[WorkSpan] = []
    # Выработка с `session_id` — закрытая сессия; у исправления сессии нет, его время нулевое (см. модуль).
    for task_id in sorted(uow.production.worked(tasks), key=str):
        for output in uow.outputs.list_for_task(task_id):
            if output.operator_id != user_id or output.session_id is None:
                continue
            started, finished = as_utc(output.started_at), as_utc(output.finished_at)
            if finished <= window_start or started >= window_stop:
                continue
            net = (finished - started).total_seconds() - output.pause_seconds
            sessions.append(WorkSpan(started, finished, max(0.0, net)))
    return _Shop(uow.calendar.shift(), uow.calendar.holidays(), absences, sessions)


def _read(factory: Callable[[], Any] | None, reader: Callable[[Any], Any]) -> Any:
    if factory is None:
        return None
    with factory() as uow:
        return reader(uow)


# ── Сборка ───────────────────────────────────────────────────────────────


def _base(user_id: UUID, first: date, now: datetime) -> dict[str, Any]:
    return {
        "employee_id": str(user_id),
        "month": f"{first.year}-{first.month:02d}",
        "label": f"{_MONTHS[first.month - 1]} {first.year}",
        "as_of": _local(now).date().isoformat(),
        "source": SOURCE,
        "note": NOTE,
    }


def _no_data(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**base, "available": False, "no_data_reason": reason, "shift_hours": None, "days": [], "totals": None,
            "balance": None}


def worktime(
    user_id: UUID, first: date, now: datetime, *,
    identity: Callable[[], Any],
    production: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    """Рабочее время сотрудника за месяц `first` и баланс года. `None` — такого сотрудника нет. Ничего не пишет."""
    person = _read(identity, lambda uow: _read_person(uow, user_id))
    if person is None:
        return None
    base = _base(user_id, first, now)
    if person.site in UNPLANNED_SITES:
        return _no_data(base, f"Нет данных: площадка {person.site} работает без нормирования, время не учитывается.")
    if production is None:
        return _no_data(base, "Нет данных: производство не подключено.")
    year_start, stop = date(first.year, 1, 1), _next_month(first)
    shop = _read(production, lambda uow: _read_shop(uow, user_id, year_start, stop))
    if not shop.sessions:
        return _no_data(base, f"Нет данных: с 1 января {first.year} по конец месяца нет ни одной сессии выработки.")

    leave_days: dict[date, str] = {}
    day_off_days: set[date] = set()
    for leave in person.leaves:
        for day in _span_days(leave.from_date, leave.to_date, year_start, stop):
            leave_days.setdefault(day, KIND_LABELS[leave.kind])
            if leave.kind is LeaveKind.DAY_OFF:
                day_off_days.add(day)
    worked = worked_by_day(shop.sessions, year_start, stop)
    effective = shop.shift.effective_seconds
    today = _local(now).date()

    def workday(day: date) -> bool:
        return day.weekday() in shop.shift.weekdays and day not in shop.holidays

    days: list[dict[str, Any]] = []
    totals = {"norm": 0.0, "worked": 0.0, "overtime": 0.0}
    year_overtime = 0.0
    for day in _days(year_start, stop):
        absence = shop.absences.get(day) or leave_days.get(day)
        norm = effective if workday(day) and absence is None else 0
        done = worked.get(day, 0.0)
        overtime = max(0.0, done - norm)
        year_overtime += overtime
        if day < first:
            continue
        future = day > today
        totals["norm"] += norm
        totals["worked"] += done
        totals["overtime"] += overtime
        days.append({
            "date": day.isoformat(), "weekday": _WEEKDAYS[day.weekday()], "workday": workday(day),
            "absence": absence, "day_off": day in day_off_days, "future": future,
            "norm_hours": _hours(norm),
            "worked_hours": None if future else _hours(done),
            "overtime_hours": None if future else _hours(overtime),
        })
    charged = sorted(day for day in day_off_days if workday(day))
    balance_seconds = round(year_overtime) - len(charged) * effective
    state = "debt" if balance_seconds < 0 else "overtime" if balance_seconds > 0 else "even"
    return {
        **base, "available": True, "no_data_reason": None, "shift_hours": _hours(effective), "days": days,
        "totals": {f"{key}_hours": _hours(value) for key, value in totals.items()},
        "balance": {
            "year": first.year, "from": year_start.isoformat(), "to": (stop - timedelta(days=1)).isoformat(),
            "overtime_hours": _hours(year_overtime), "day_off_days": len(charged),
            "day_off_hours": _hours(len(charged) * effective), "balance_hours": _hours(balance_seconds),
            "state": state, "label": _STATES[state],
        },
    }
