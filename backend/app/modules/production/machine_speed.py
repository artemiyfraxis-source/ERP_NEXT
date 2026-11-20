"""ОБ.019 speed: «норма vs факт» and norm suggestions from the speed history (plan machine-speed-history S2). Pure
functions over `production_machine_speed_rows` (machine_history.py) and a БД.ОП row's fields; HTTP and storage are in
workflows/machine_speed_api.py.

Intervals. Rows of one machine in time order; the interval i→i+1 belongs to row i (its state, setup and run):
- «в работе» (running) — state 103 «Работает», alarm D782 ∈ {0, 4, 5} (none, «тираж», «тираж готов»), the same boot,
  run and setup at both ends, the counter not gone down and 0 < Δt ≤ max(60 s, 5 × norm s/pc of the setup);
- otherwise a stop by reason: another boot of the bridge — «Нет связи»; the counter went down — «Сброс счётчика»;
  state 103 with a fault alarm — «Авария»; state 103 with no row for longer than the cap — «Нет связи» (a running
  machine writes a row per piece, so a longer silence is missing data); 104 — «Пауза»; 105 — «Авария»;
  100–102 / 106 / 107 — «Готов/ждёт». A running interval whose next row has another setup or run, or an unknown state,
  is not counted at all.
A segment is a stretch of rows with one setup key and one run (`run_write_id`); a run in «Прогонов» is a segment that
made pieces. Fact s/pc of a setup = Σ running Δt / Σ Δn over its running intervals. Setup fact of a segment opened by a
new setup (`reason` setup) or a new run (`reason` run with a run): its first row → the start of its first running
interval with Δn > 0; a setup's figure is the median of its segments.

Norm = the CYCLOGRAM of the БД.ОП row (owner 20.11: not one tOp), the same formula as techcards/etk_builder.linear_seconds
and frontend opFields.linearPiece: a «Погонный» row with «Скорость проката» > 0 — per piece max(L / прокат +
ход инструмента × ходов, взять + ручные + снять ПФ); any other row — the sheet's cycleTimes (max of hands and tool × ops)
or, with an empty cyclogram, «Время операции». Setup norm — «Время подготовки».

Suggestion — never applied by itself, the owner accepts it (`suggest`)."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from statistics import median
from typing import Iterable, Mapping, Protocol, Sequence
from uuid import UUID

from app.shared.clock import MOSCOW

from .norm_fact import FAST, MIN_FACTS, SLOW, quartiles

RUNNING = 103
ALARMS_OK = frozenset({0, 4, 5})
MIN_CAP_SECONDS = 60.0
CAP_PIECES = 5
MIN_PIECES = 200
STOP_LABELS = {"pause": "Пауза", "alarm": "Авария", "idle": "Готов/ждёт", "offline": "Нет связи",
               "reset": "Сброс счётчика"}
STATE_STOPS = {104: "pause", 105: "alarm", 100: "idle", 101: "idle", 102: "idle", 106: "idle", 107: "idle"}
ONE_LENGTH = "по одной длине — только постоянная часть"

# БД.ОП cyclogram cells (frontend opFields CYCLE_COLUMNS): id → card label. The sheet's headers differ in case and units,
# so a column is found by the start of its header, as the screen does.
CYCLE_LABELS = {
    "prep": "Время подготовки", "take": "Ручные работы: взять ПФ", "manual": "Ручные работы",
    "put": "Ручные работы: снять ПФ", "roll": "Скорость проката", "tool": "Скорость работы инструмента",
    "toolOps": "Кол-во операций инструмента",
}
SUGGESTION_LABELS = {"roll": "Скорость проката, м/с", "tool": "Скорость работы инструмента, с"}
# Keys the references schema duplicates from sheet columns (opFields OP_SCHEMA_KEYS): never a cyclogram cell.
OP_SCHEMA_KEYS = frozenset({"opNumber", "opName", "tOp", "tPrep", "tMachine"})
TIME_PREFIXES = ("время чел", "время машины", "время операции")


class SpeedRow(Protocol):
    id: int
    equipment_id: UUID
    recorded_at: datetime
    boot: str
    counter_total: int
    state_code: int
    alarm_code: int | None
    run_write_id: UUID | None
    run_kind: str | None
    setup_key: str
    reason: str


def aware(value: datetime) -> datetime:
    """SQLite gives a stored moment back naive in shop time, PostgreSQL aware (machine_runs.aware)."""
    return value.replace(tzinfo=MOSCOW) if value.tzinfo is None else value


# --- БД.ОП cyclogram: a port of frontend/src/opFields.ts (cycleTimes / withCycleTimes / linearPiece) ---

def header_key(header: str) -> str:
    """referenceLabels.headerKey: whitespace collapsed, no space before a comma, trimmed, lower case."""
    return re.sub(r"\s+,", ",", re.sub(r"\s+", " ", str(header or ""))).strip().lower()


def _cycle_id(key: str) -> str | None:
    name = header_key(key)
    for prefix, cell in (("время подготовки", "prep"), ("скорость проката", "roll"),
                         ("скорость работы инструмента", "tool"), ("кол-во операций инструмента", "toolOps"),
                         ("время ручных работ для взятия", "take"), ("время ручных работ для снятия", "put"),
                         ("время ручных работ", "manual")):
        if name.startswith(prefix):
            return cell
    return None


def cycle_keys(fields: Mapping[str, object]) -> dict[str, str]:
    """Cyclogram cell id → the row's exact field key (the first column of that label, schema keys aside)."""
    keys: dict[str, str] = {}
    for key in fields:
        if key in OP_SCHEMA_KEYS:
            continue
        cell = _cycle_id(key)
        if cell and cell not in keys:
            keys[cell] = key
    return keys


def number(value: object) -> float:
    """JS Number(String(value).trim().replace(",", ".")): empty → 0, not a finite number → 0."""
    text = str(value if value is not None else "").strip().replace(",", ".", 1)
    if not text:
        return 0.0
    if "_" in text:
        return 0.0
    try:
        parsed = float(text)
    except ValueError:
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _column(fields: Mapping[str, object], prefix: str) -> str:
    return next((str(value or "") for key, value in fields.items() if header_key(key).startswith(prefix)), "")


def _cells(fields: Mapping[str, object]) -> dict[str, str]:
    keys = cycle_keys(fields)
    return {cell: str(fields.get(keys[cell]) or "") if cell in keys else "" for cell in CYCLE_LABELS}


def time_by_length(fields: Mapping[str, object]) -> bool:
    """opFields.timeByLength: a «Погонный» row with «Скорость проката» > 0 — its time depends on the wire length."""
    return (_column(fields, "тип операции").strip().lower().startswith("погон")
            and number(_cells(fields)["roll"]) > 0)


def cycle_times(fields: Mapping[str, object]) -> dict[str, float] | None:
    """opFields.cycleTimes: hands = взять + ручные + снять ПФ, machine = ход × ходов, operation = the larger; None — the
    time depends on the length or the cyclogram is empty."""
    if time_by_length(fields):
        return None
    cells = _cells(fields)
    if not any(cells[cell].strip() for cell in ("take", "manual", "put", "tool", "toolOps")):
        return None
    human = number(cells["take"]) + number(cells["manual"]) + number(cells["put"])
    machine = number(cells["tool"]) * number(cells["toolOps"])
    return {"human": human, "machine": machine, "operation": max(human, machine)}


def sheet_number(value: float) -> str:
    """toLocaleString("ru-RU", 2 decimals, no grouping) — «6,50», as the sheet stores it. ICU rounds the shortest
    decimal of the double half away from zero (1.005 → «1,01», unlike toFixed): so does this."""
    return format(Decimal(repr(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f").replace(".", ",")


def with_cycle_times(fields: Mapping[str, str]) -> dict[str, str]:
    """opFields.withCycleTimes: the row with «Время Чел» / «Время машины» / «Время операции» recomputed from its
    cyclogram, as the sheet's formulas after a cell edit; unchanged when cycle_times is None."""
    times = cycle_times(fields)
    result = dict(fields)
    if times is None:
        return result
    for prefix, value in zip(TIME_PREFIXES, (times["human"], times["machine"], times["operation"])):
        key = next((item for item in fields if item not in OP_SCHEMA_KEYS and header_key(item).startswith(prefix)), None)
        if key is not None:
            result[key] = sheet_number(value)
    return result


def _decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value if value is not None else "").strip().replace(",", ".") or "0")
    except InvalidOperation:
        return Decimal(0)
    return parsed if parsed.is_finite() else Decimal(0)


def piece_seconds(fields: Mapping[str, object], length_mm: Decimal | None) -> Decimal | None:
    """Norm s/pc by the cyclogram; None — no norm (a «Погонный» row with no known length, or nothing filled)."""
    cells = _cells(fields)
    if time_by_length(fields):
        if length_mm is None:
            return None
        machine = length_mm / 1000 / _decimal(cells["roll"]) + _decimal(cells["tool"]) * _decimal(cells["toolOps"])
        hands = _decimal(cells["take"]) + _decimal(cells["manual"]) + _decimal(cells["put"])
        return max(machine, hands)
    times = cycle_times(fields)
    seconds = Decimal(str(times["operation"])) if times is not None else _decimal(
        fields.get("tOp") or _column(fields, "время операции"))
    return seconds if seconds > 0 else None


def timing_seconds(timing: object, length_mm: Decimal | None) -> Decimal | None:
    """etk_builder.linear_seconds over an ETK operation's `linearTiming` (the БД.ОП cells copied when the ETK was
    built) — the norm of a cutting run whose code has no БД.ОП row now."""
    if not isinstance(timing, Mapping) or length_mm is None:
        return None
    roll = _decimal(timing.get("rollSpeed"))
    if roll <= 0:
        return None
    machine = length_mm / 1000 / roll + _decimal(timing.get("toolSeconds")) * _decimal(timing.get("toolOps"))
    hands = sum((_decimal(timing.get(key)) for key in ("takeSeconds", "manualSeconds", "putSeconds")), Decimal(0))
    return max(machine, hands)


def prep_seconds(fields: Mapping[str, object]) -> Decimal | None:
    seconds = _decimal(fields.get("tPrep") or _cells(fields)["prep"])
    return seconds if seconds > 0 else None


# --- setup key (machine_history.setup_key) ---

def parse_setup(key: str) -> dict[str, str]:
    """«пвам 0,35|L325|S5/5|P0/0|M1/F-B/3» → wire, length, strips, pull, marking key; a missing part is «?»."""
    parts = str(key or "").split("|")
    wire = parts[0] if parts else ""
    tagged = {part[:1]: part[1:] for part in parts[1:] if part}
    strip = (tagged.get("S") or "?/?").split("/", 1) + ["?"]
    return {"wire": wire, "length_mm": tagged.get("L") or "?", "strip_front_mm": strip[0], "strip_back_mm": strip[1],
            "pull": tagged.get("P") or "?", "marking": tagged.get("M") or "?"}


def length_mm(key: str) -> Decimal | None:
    value = _decimal(parse_setup(key)["length_mm"])
    return value if value > 0 else None


def _mark_texts(marking: str) -> int | None:
    if marking == "0":
        return 0
    parts = marking.split("/")
    if len(parts) < 2 or parts[0] != "1":
        return None
    return sum(1 for flag in parts[1] if flag != "-")


def marking_text(marking: str) -> str:
    texts = _mark_texts(marking)
    if texts is None:
        return "нет данных"
    if texts == 0:
        return "Без маркировки" if marking == "0" else "Маркировка без текста"
    word = "текст" if texts == 1 else "текста" if texts < 5 else "текстов"
    return f"Маркировка: {texts} {word}"


def setup_code(key: str) -> str:
    """БД.ОП code of a setup without a cutting run (a manual job or the HMI's own): CUT_WIRE+{n}strip+{m}mark — n
    stripped ends (D34 / D38 > 0), m marking texts (at most 2, the codes the sheet has); nothing → CUT_WIRE_auto."""
    setup = parse_setup(key)
    ends = sum(1 for value in (setup["strip_front_mm"], setup["strip_back_mm"]) if _decimal(value) > 0)
    marks = min(_mark_texts(setup["marking"]) or 0, 2)
    code = "CUT_WIRE" + (f"+{ends}strip" if ends else "") + (f"+{marks}mark" if marks else "")
    return code if ends or marks else "CUT_WIRE_auto"


# --- intervals and segments ---

def cap_seconds(norm: Decimal | None) -> float:
    return max(MIN_CAP_SECONDS, CAP_PIECES * float(norm)) if norm else MIN_CAP_SECONDS


def interval(a: SpeedRow, b: SpeedRow, cap: float) -> tuple[str | None, float, int]:
    """(kind, Δt s, pieces) of the interval a→b: kind «running», a stop reason, or None — not counted."""
    seconds = (aware(b.recorded_at) - aware(a.recorded_at)).total_seconds()
    if a.boot != b.boot:
        return "offline", seconds, 0
    pieces = b.counter_total - a.counter_total
    if pieces < 0:
        return "reset", seconds, 0
    if a.state_code == RUNNING:
        if (a.alarm_code or 0) not in ALARMS_OK:
            return "alarm", seconds, 0
        if seconds > cap:
            return "offline", seconds, 0
        if seconds <= 0 or (a.setup_key, a.run_write_id) != (b.setup_key, b.run_write_id):
            return None, seconds, 0
        return "running", seconds, pieces
    return STATE_STOPS.get(a.state_code), seconds, 0


@dataclass
class Segment:
    equipment_id: UUID
    setup_key: str
    run_write_id: UUID | None
    run_kind: str | None
    started_at: datetime
    ended_at: datetime
    pieces: int = 0
    running_seconds: float = 0.0
    stops: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    setup_seconds: float | None = None
    setup_open: bool = False

    @property
    def fact(self) -> float | None:
        return self.running_seconds / self.pieces if self.pieces > 0 and self.running_seconds > 0 else None


def segments(rows: Iterable[SpeedRow], norms: Mapping[tuple[UUID, str], Decimal | None]) -> list[Segment]:
    """History rows (any order, any machines) → segments in time order per machine. `norms` — (machine, setup key) → norm
    s/pc for the running cap (missing — 60 s)."""
    by_machine: dict[UUID, list[SpeedRow]] = defaultdict(list)
    for row in rows:
        by_machine[row.equipment_id].append(row)
    found: list[Segment] = []
    for equipment_id, items in by_machine.items():
        items.sort(key=lambda row: (aware(row.recorded_at), row.id))
        current: Segment | None = None
        for index, row in enumerate(items):
            moment = aware(row.recorded_at)
            if current is None or (row.setup_key, row.run_write_id) != (current.setup_key, current.run_write_id):
                current = Segment(equipment_id, row.setup_key, row.run_write_id, row.run_kind, moment, moment,
                                  setup_open=row.reason == "setup" or (row.reason == "run" and row.run_write_id is not None))
                found.append(current)
            if index + 1 == len(items):
                break
            following = items[index + 1]
            kind, seconds, pieces = interval(row, following, cap_seconds(norms.get((equipment_id, row.setup_key))))
            current.ended_at = aware(following.recorded_at)
            if kind == "running":
                if pieces > 0 and current.setup_open:
                    current.setup_seconds = (moment - current.started_at).total_seconds()
                    current.setup_open = False
                current.running_seconds += seconds
                current.pieces += pieces
            elif kind is not None:
                current.stops[kind] += seconds
    return found


@dataclass
class SetupFact:
    equipment_id: UUID
    setup_key: str
    segments: list[Segment]

    @property
    def pieces(self) -> int:
        return sum(item.pieces for item in self.segments)

    @property
    def running_seconds(self) -> float:
        return sum(item.running_seconds for item in self.segments)

    @property
    def runs(self) -> list[Segment]:
        return [item for item in self.segments if item.fact is not None]

    @property
    def fact(self) -> float | None:
        return self.running_seconds / self.pieces if self.pieces > 0 and self.running_seconds > 0 else None

    @property
    def setup_fact(self) -> float | None:
        values = [item.setup_seconds for item in self.segments if item.setup_seconds is not None]
        return median(values) if values else None

    @property
    def stops(self) -> dict[str, float]:
        total: dict[str, float] = defaultdict(float)
        for item in self.segments:
            for reason, seconds in item.stops.items():
                total[reason] += seconds
        return {reason: total[reason] for reason in STOP_LABELS if total.get(reason)}


def by_setup(found: Iterable[Segment]) -> list[SetupFact]:
    grouped: dict[tuple[UUID, str], list[Segment]] = defaultdict(list)
    for item in found:
        grouped[(item.equipment_id, item.setup_key)].append(item)
    return [SetupFact(equipment_id, key, items) for (equipment_id, key), items in grouped.items()]


# --- the suggestion ---

@dataclass(frozen=True)
class Point:
    """One setup of the operation: its cut length, pieces and fact s/pc."""
    length_mm: Decimal
    pieces: int
    fact: float


def _round(value: float, step: str) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal(step), rounding=ROUND_HALF_UP)


def ru(value: Decimal) -> str:
    """«0,22», «3», «1,8»: the sheet's comma, no trailing zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def steady(ratios: Sequence[float]) -> bool:
    """norm_fact's rule: at least MIN_FACTS runs, and steadily slow (median > SLOW, lower quartile > 1) or fast (median
    < FAST, upper quartile < 1)."""
    if len(ratios) < MIN_FACTS:
        return False
    middle, (low, high) = median(ratios), quartiles(ratios)
    return (middle > SLOW and low > 1) or (middle < FAST and high < 1)


def suggest(fields: Mapping[str, str], points: Sequence[Point], ratios: Sequence[float]) -> dict | None:
    """New «Скорость проката» / «Скорость работы инструмента» for a «Погонный» БД.ОП row from its setups on a machine.

    By the cyclogram the machine makes a piece of length L (m) in t = L / v + T × k (v — прокат, м/с; T — ход
    инструмента, с; k — ходов на штуку; the formula has no other constant). So the facts of setups of different lengths
    lie on a line t = a + b × L: a weighted least-squares fit (weight — pieces of the setup) gives b = 1 / v and
    a = T × k, so v = 1 / b and T = a / k. With one length (or a fit whose slope is not > 0) the length part cannot be
    told from the constant: v stays, a = the weighted mean of t − L / v, and only T is suggested, with the note
    ONE_LENGTH. v is rounded to 0,01 м/с, T to 0,1 с; a cell is suggested only when it changes.

    None — not a «Погонный» row with прокат, fewer than MIN_PIECES pieces, fewer than MIN_FACTS runs, the runs not
    steadily slow or fast against the norm (`steady`), or nothing would change. Never applied by itself."""
    if not time_by_length(fields) or not points:
        return None
    if sum(point.pieces for point in points) < MIN_PIECES or not steady(ratios):
        return None
    keys, cells = cycle_keys(fields), _cells(fields)
    roll_now, ops = number(cells["roll"]), number(cells["toolOps"])
    weight = float(sum(point.pieces for point in points))
    xs = [(float(point.length_mm) / 1000, point.fact, float(point.pieces)) for point in points]
    x_mean = sum(x * w for x, _, w in xs) / weight
    y_mean = sum(y * w for _, y, w in xs) / weight
    sxx = sum(w * (x - x_mean) ** 2 for x, _, w in xs)
    slope = sum(w * (x - x_mean) * (y - y_mean) for x, y, w in xs) / sxx if sxx > 0 else 0.0
    roll_new: Decimal | None = None
    if len({point.length_mm for point in points}) >= 2 and slope > 0 and _round(1 / slope, "0.01") > 0:
        roll_new = _round(1 / slope, "0.01")
        constant, note = y_mean - slope * x_mean, None
    else:
        constant, note = sum(w * (y - x / roll_now) for x, y, w in xs) / weight, ONE_LENGTH
    changes = []
    if roll_new is not None and roll_new != _round(roll_now, "0.01"):
        changes.append(("roll", ru(roll_new)))
    if ops > 0 and "tool" in keys:
        tool_new = _round(max(constant, 0.0) / ops, "0.1")
        if tool_new != _round(number(cells["tool"]), "0.1"):
            changes.append(("tool", ru(tool_new)))
    if not changes:
        return None
    speed = roll_new if roll_new is not None else _round(roll_now, "0.01")
    return {
        "cells": [{"field": keys[cell], "label": SUGGESTION_LABELS[cell], "old": cells[cell], "new": value}
                  for cell, value in changes],
        "fit": f"{ru(_round(constant, '0.1'))} с + L / {ru(speed)} м/с",
        "lengths": [ru(value) for value in sorted({point.length_mm for point in points})],
        "note": note,
    }


# --- report figures ---

def seconds_text(value: float | Decimal | None, step: str = "0.01") -> str | None:
    """Decimal string with a dot (the API's figures): «6.27»; None stays None."""
    if value is None:
        return None
    return format(Decimal(repr(float(value))).quantize(Decimal(step), rounding=ROUND_HALF_UP), "f")


def ratio(fact: float | None, norm: Decimal | None) -> float | None:
    return fact / float(norm) if fact is not None and norm else None
