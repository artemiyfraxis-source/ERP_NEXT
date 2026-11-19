"""Next control of a measured instrument: the earlier of 12 months or 100 matings since the last measurement
(СТО ФРАК-002-2026 п. 15.3; РЭ ч. 1 п. 4.5, РЭ АКМ п. 6.2.3). Both numbers live in rf-norms.json → `recheck`.

Matings are counted from the start of the measurement day, Moscow time: a margin of the measurement's own matings,
never a shortfall. The «initial» entry (history before the counter) is not a mating after the measurement."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time

from app.shared.clock import MOSCOW

from .instrument_norms import norms_data

CONTROL_INTERVAL_MONTHS = int(norms_data()["recheck"]["months"])
RECHECK_MATINGS = int(norms_data()["recheck"]["matings"])


def next_control_date(measured_on: date) -> date | None:
    """CONTROL_INTERVAL_MONTHS after the measurement, the day clamped to the month's end; None beyond the calendar."""
    year, month = divmod(measured_on.year * 12 + measured_on.month - 1 + CONTROL_INTERVAL_MONTHS, 12)
    if year > date.max.year:
        return None
    return date(year, month + 1, min(measured_on.day, monthrange(year, month + 1)[1]))


def control_since(measured_on: date) -> datetime:
    return datetime.combine(measured_on, time.min, tzinfo=MOSCOW)


def _date(value: date) -> str:
    return f"{value:%d.%m.%Y}"


def matings_word(count: int) -> str:
    """«1 сочленение», «3 сочленения», «12 сочленений»."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} сочленение"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} сочленения"
    return f"{count} сочленений"


@dataclass(frozen=True)
class ControlDue:
    measured_on: date
    due_on: date | None
    matings_since: int | None   # None — the counter was not read (e.g. a passport rendered without a database)
    today: date

    @property
    def matings_left(self) -> int | None:
        return None if self.matings_since is None else max(0, RECHECK_MATINGS - self.matings_since)

    @property
    def by_date(self) -> bool:
        return self.due_on is not None and self.due_on < self.today

    @property
    def by_matings(self) -> bool:
        return self.matings_since is not None and self.matings_since >= RECHECK_MATINGS

    @property
    def overdue(self) -> bool:
        return self.by_date or self.by_matings

    @property
    def reason(self) -> str:
        """«контроль просрочен: 100 сочленений с замера 16.11.2026» — "" while not overdue."""
        reasons = []
        if self.by_date:
            reasons.append(f"срок {_date(self.due_on)} прошёл")
        if self.by_matings:
            reasons.append(f"{matings_word(self.matings_since)} с замера {_date(self.measured_on)} (норма {RECHECK_MATINGS})")
        return "контроль просрочен: " + "; ".join(reasons) if reasons else ""

    @property
    def line(self) -> str:
        """«следующий контроль до 16.11.2027 или ещё 87 сочленений из 100 — что раньше»."""
        if self.overdue:
            return self.reason
        date_part = f"до {_date(self.due_on)}" if self.due_on else ""
        if self.matings_left is None:
            matings = f"через {RECHECK_MATINGS} сочленений"
        else:
            matings = f"ещё {matings_word(self.matings_left)} из {RECHECK_MATINGS}"
        if not date_part:
            return "следующий контроль " + matings
        return f"следующий контроль {date_part} или {matings} — что раньше"

    @property
    def label_line(self) -> str:
        """The printed label's full line — static rule, no live count: «следующий контроль: 16.11.2027 или 100 сочленений»."""
        if self.overdue:
            return "контроль просрочен"
        due = f"{_date(self.due_on)} или " if self.due_on else ""
        return f"следующий контроль: {due}{RECHECK_MATINGS} сочленений"

    @property
    def short(self) -> str:
        """The label's: «Контроль 16.11.2027 / 100 сочл.» or «КОНТРОЛЬ ПРОСРОЧЕН»."""
        if self.overdue:
            return "КОНТРОЛЬ ПРОСРОЧЕН"
        return " ".join(filter(None, ("Контроль", _date(self.due_on) if self.due_on else "", f"/ {RECHECK_MATINGS} сочл.")))


def control_due(measured_on: date, matings_since: int | None, today: date) -> ControlDue:
    return ControlDue(measured_on, next_control_date(measured_on), matings_since, today)


def recheck_text() -> str:
    return f"через {CONTROL_INTERVAL_MONTHS} мес. или {RECHECK_MATINGS} сочленений — что раньше"
