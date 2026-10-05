"""Автоплан площадки ЦАР на период с учётом наладки станков (фаза 1, владелец 05.10.2026).

«Когда в проекте обжимается один контакт, но много разных исполнений, можно под это выделить одного человека…
резка коаксиального с зачисткой — если там тоже схожие, надо это учитывать». Здесь — чистые правила: ключ наладки
операции, цена переналадки и сам расчёт. Загрузка данных и запись — autoplan_application.py.

Ключ наладки. Семейство = тип станка (префикс инвентарного номера «ОБ.027» из строки «Оборудование» карточки
операции, иначе код операции) + для опрессовки: аппликатор и артикул терминала; для резки/разделки коаксиала: кабель и
разъём; для резки провода: артикул провода; для пайки/лужения: код и «Параметры» оснастки (жало, температура); для
прочих: код и разъём операции. Точный ключ = семейство + подстройка (высота обжима или сечение; L1–L3/D1–D3 коаксиала;
длины зачистки A/B). Длина резки в ключ не входит: её меняют без переналадки.

Цена переналадки: тот же точный ключ — 0; то же семейство — min(подготовка карты tPrep, 10 мин); другое — tPrep.
Наладка живёт на станке: у станка в одном экземпляре — общая для всех (кто бы ни работал на нём последним), у станков
в нескольких экземплярах — своя у каждого монтажника («его» станок остаётся настроенным, пока он на нём не сменит
наладку). Станков одного типа одновременно занято не больше, чем единиц в БД.ОБ с этим префиксом (нет в БД.ОБ — одна);
операции без станка (только инструмент) по станкам не ограничены.

Расчёт — жадное списочное расписание. Готовые операции (все их входы по схеме задания уже стоят в плане) берутся по
приоритету: дедлайн задания, потом номер ПЗ, потом порядок этапов. Операция встаёт на монтажника с допуском, у
которого она кончится раньше всех (смена, праздники, отсутствия, уже назначенная работа, занятость станков, своя
переналадка). Вход передаётся всем количеством, если на ребре схемы не задана своя передаточная партия. Поставив
операцию, тот же монтажник подтягивает следующие готовые операции того же семейства наладки (сначала с тем же точным
ключом), пока это экономит наладку, идёт подряд (не дальше часа работы после предыдущей) и ни одно ПЗ с более ранним
приоритетом не опаздывает к дедлайну из-за этого: прогноз — тот же жадный расчёт оставшихся операций более срочных ПЗ
с подтяжкой и без неё. Один монтажник на операцию (без бригад). Всё детерминировано: порядок стабильный, случайности нет.

Почему не CP-SAT (решено агентом): в периоде десятки операций и девять монтажников ЦАР — жадный расчёт успевает за
доли секунды, мастеру можно объяснить каждое решение («та же наладка», «раньше закончит»), а в контейнере не нужна
тяжёлая зависимость (ortools) и подбор весов целевой функции.

Ограничение фазы 1: dispatch.reflow пересчитывает длительность доли с полной подготовкой (share_seconds), поэтому
сдвиг по факту после применения может удлинить операции партии, у которых автоплан пропустил наладку. Фаза 2 хранит
пропущенную наладку в назначении."""

from __future__ import annotations

import re
from bisect import insort
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from .domain import (
    BusyInterval,
    OperatorSkill,
    ProductionOperation,
    ProductionTask,
    WorkShift,
    add_work_seconds,
    next_working_moment,
)
from .launchable import route_from_document, stage_name
from .scheduling import (
    ScheduledOperation,
    _allowed,
    _rate,
    chain_start,
    edge_batch,
    network_order,
    work_seconds_between,
)

# Автоплан расписывает только площадку с нормированием (ЖУК работает не нормированно, ему отдают задание целиком).
AUTOPLAN_SITE = "ЦАР"
# Метка назначения автопланом (поле assigned_by операции): «Автоплан · <мастер>». По ней replan_auto знает, что можно
# переставить.
AUTOPLAN_MARK = "Автоплан · "
ADJUSTMENT_SECONDS = 10 * 60
BATCH_GAP_SECONDS = 60 * 60
_SLOT_ATTEMPTS = 400

CRIMP_TYPES = frozenset({"prsTerm", "prsTermA", "prsTermB"})
CUT_CRIMP_TYPES = frozenset({"cutWireCrimp"})
CUT_TYPES = frozenset({"cutWire", "cutWireStrip"})
COAX_TYPES = frozenset({"coaxCut", "coaxStrip"})
SOLDER_TYPES = frozenset({"tin", "solderConn", "coaxSolderShield", "coaxPin"})
_COAX_SIZES = ("l1", "l2", "l3", "d1", "d2", "d3")
_INVENTORY = re.compile(r"([ОO]Б)\s*\.\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class SetupKey:
    """Наладка операции: тип станка, семейство (меняется долго) и точный ключ (семейство + подстройка)."""
    machine: str
    family: tuple[str, ...]
    exact: tuple[str, ...]
    label: str
    family_label: str = ""
    machine_label: str = ""
    limited: bool = False  # станок из «Оборудования» с инвентарным номером: занятость ограничена единицами БД.ОБ


def changeover_seconds(previous: SetupKey | None, current: SetupKey, setup_seconds: Decimal) -> int:
    full = ceil(max(Decimal(0), Decimal(setup_seconds)))
    if previous is None or previous.family != current.family:
        return full
    if previous.exact == current.exact:
        return 0
    return min(full, ADJUSTMENT_SECONDS)


def inventory_prefix(value: object) -> str:
    """«ОБ.027.2» / «ОБ.027.X» / «ОБ.027» → «ОБ.027»: тип станка, общий для всех его единиц."""
    match = _INVENTORY.search(str(value or ""))
    return f"ОБ.{match.group(2)}" if match else ""


def equipment_units(inventory_numbers: Iterable[str]) -> dict[str, int]:
    """Единиц каждого типа станка в БД.ОБ: разные инвентарные номера с общим префиксом."""
    seen: dict[str, set[str]] = {}
    for number in inventory_numbers:
        prefix = inventory_prefix(number)
        if prefix:
            seen.setdefault(prefix, set()).add(" ".join(str(number).split()).upper())
    return {prefix: len(numbers) for prefix, numbers in seen.items()}


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _join(values: Iterable[object]) -> str:
    return ", ".join(sorted({_text(value) for value in values if _text(value)}))


def fallback_key(code: str, stage: str) -> SetupKey:
    """Нет техкарты или коды расходятся: наладка по коду операции."""
    name = _text(code) or _text(stage)
    label = stage_name(stage) or name
    return SetupKey(name, (name,), (name,), label, label)


def setup_key(
    *,
    code: str,
    stage: str,
    built: Mapping[str, Any] | None,
    config_op: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None,
) -> SetupKey:
    """Ключ наладки операции по выданной ЭТК: built — операция документа, config_op — операция генератора."""
    built, op, config = _mapping(built), _mapping(config_op), _mapping(config)
    fields = _mapping(built.get("fields"))
    kind = _text(op.get("type") or built.get("type"))
    title = stage_name(stage) or stage_name(kind) or _text(code)
    prefix, machine_name = "", ""
    for tool in _mapping(built.get("card")).get("tooling") or []:
        tool = _mapping(tool)
        if _text(tool.get("section")) == "Оборудование" and inventory_prefix(tool.get("inventory_number")):
            prefix, machine_name = inventory_prefix(tool.get("inventory_number")), _text(tool.get("name"))
            break
    machine = prefix or _text(code) or _text(stage)
    machine_label = f"{prefix} {machine_name}".strip()

    def key(family: Sequence[str], family_label: str, adjust: Sequence[str] = (), adjust_label: str = "") -> SetupKey:
        family_key = (machine, *family)
        exact = (*family_key, *adjust)
        label = f"{title}: {family_label}" if family_label else title
        return SetupKey(
            machine, family_key, exact, f"{label}, {adjust_label}" if adjust_label else label, label,
            machine_label, bool(prefix),
        )

    endpoint = _mapping(op.get("ep"))
    norm = _mapping(op.get("terminalNorm"))
    terminal = _text(endpoint.get("termArt") or endpoint.get("termName"))
    applicator = _text(fields.get("applicator") or norm.get("applicator"))
    height = _text(fields.get("crimpHeight") or norm.get("crimpHeight"))
    sections = _join(_mapping(item).get("section") for item in fields.get("crimpBySection") or norm.get("bySection") or [])
    wires = _join(_mapping(item).get("art") or _mapping(item).get("name") for item in fields.get("wires") or [])
    strip = _mapping(fields.get("strip") or op.get("strip") or (config.get("strip") if kind == "cutWire" else None))
    strip_lengths = "/".join(
        _text(strip.get(f"len{side.upper()}")) if strip.get(side) else "—" for side in ("a", "b")
    ) if strip else ""

    if kind in CRIMP_TYPES and (applicator or terminal):
        adjust = height or sections
        return key(
            (applicator, terminal), f"терминал {terminal or '—'}, аппликатор {applicator or '—'}",
            (adjust,), f"высота обжима {height} мм" if height else (f"сечение {sections}" if sections else ""),
        )
    if kind in CUT_CRIMP_TYPES and (wires or applicator or terminal):
        adjust_label = ", ".join(part for part in (
            f"высота обжима {height} мм" if height else "", f"зачистка {strip_lengths} мм" if strip_lengths else "",
        ) if part)
        return key(
            (wires, applicator, terminal), f"провод {wires or '—'}, терминал {terminal or '—'}, аппликатор {applicator or '—'}",
            (height or sections, strip_lengths), adjust_label,
        )
    if kind in CUT_TYPES and wires:
        return key((wires,), f"провод {wires}", (strip_lengths,), f"зачистка {strip_lengths} мм" if strip_lengths else "")
    if kind in COAX_TYPES:
        coax = _mapping(config.get("coax"))
        side = _mapping(coax.get("sideB" if _text(op.get("side") or built.get("side")) == "B" else "sideA"))
        built_coax = _mapping(fields.get("coax"))
        cable = _text(built_coax.get("cable") or coax.get("cableArt"))
        connector = _text(built_coax.get("connector") or side.get("article"))
        if cable or connector:
            sizes = {name: _text(built_coax.get(name) or side.get(name)) for name in _COAX_SIZES}
            lengths = "/".join(sizes[name] or "—" for name in ("l1", "l2", "l3"))
            return key(
                (cable, connector), f"кабель {cable or '—'}, разъём {connector or '—'}",
                tuple(sizes[name] for name in _COAX_SIZES), f"L1–L3 {lengths}",
            )
    if kind in SOLDER_TYPES:
        parameters = _join(_mapping(tool).get("parameters") for tool in _mapping(built.get("card")).get("tooling") or [])
        return key((_text(code), parameters), parameters)
    connector = _text(built.get("connector_article"))
    return key((_text(code) or _text(stage), connector), f"разъём {connector}" if connector else "")


def operation_keys(task: ProductionTask, document: Mapping[str, Any] | None) -> tuple[dict[UUID, SetupKey], tuple[str, ...]]:
    """Ключи наладки операций задания по текущей выданной техкарте (позиция этапа = номер операции ЭТК). Нет
    техкарты или код этапа расходится с ней — ключ по коду операции и предупреждение."""
    if not isinstance(document, Mapping):
        return (
            {operation.id: fallback_key(operation.code, operation.stage) for operation in task.operations},
            (f"{task.number}: выданная техкарта {task.project} {task.item} не найдена — наладка по кодам операций",),
        )
    route = route_from_document(document)
    built = [item for item in document.get("operations") or [] if isinstance(item, Mapping)]
    config = _mapping(document.get("build_config"))
    config_ops = [item for item in config.get("ops") or [] if isinstance(item, Mapping)]
    keys: dict[UUID, SetupKey] = {}
    warnings: list[str] = []
    for operation in task.operations:
        index = operation.position - 1
        step = route[index] if 0 <= index < len(route) else None
        if step is None or step.code != operation.code:
            keys[operation.id] = fallback_key(operation.code, operation.stage)
            warnings.append(
                f"{task.number} · этап {operation.position}: код {operation.code or '—'} не совпадает с техкартой "
                f"({step.code or '—' if step else 'нет этапа'}) — наладка по коду операции"
            )
            continue
        keys[operation.id] = setup_key(
            code=operation.code, stage=operation.stage,
            built=built[index] if index < len(built) else None,
            config_op=config_ops[index] if index < len(config_ops) else None, config=config,
        )
    return keys, tuple(warnings)


@dataclass(frozen=True)
class AutoplanInput:
    tasks: tuple[ProductionTask, ...]  # живые задания в поясе «сейчас»; у переставляемых операций доли уже сняты
    free: frozenset[UUID]  # операции, которые надо расставить
    keys: dict[UUID, SetupKey]  # у всех свободных и, где известно, у назначенных операций
    team: tuple[UUID, ...]  # монтажники ЦАР в стабильном порядке
    skills: dict[str, dict[UUID, OperatorSkill]]
    busy: dict[UUID, tuple[BusyInterval, ...]]  # назначенная работа и отсутствия монтажников
    capacity: dict[str, int]  # единиц станка по префиксу; нет — одна
    start: datetime
    end: datetime
    holidays: frozenset[date]
    shift: WorkShift


@dataclass(frozen=True)
class Placement:
    task_id: UUID
    operation_id: UUID
    operator_id: UUID
    start: datetime
    finish: datetime
    key: SetupKey
    changeover_seconds: int
    setup_seconds: int  # подготовка по карте — столько стоила бы операция без учёта наладки
    pulled: bool  # подтянута в партию той же наладки


@dataclass(frozen=True)
class Unplaced:
    task_id: UUID
    operation_id: UUID
    reason: str


@dataclass(frozen=True)
class AutoplanResult:
    placements: tuple[Placement, ...]
    unplaced: tuple[Unplaced, ...]
    warnings: tuple[str, ...]

    @property
    def changeovers(self) -> int:
        return sum(1 for item in self.placements if item.changeover_seconds > 0)

    @property
    def changeovers_naive(self) -> int:
        """Сколько наладок было бы, если каждая операция налаживается заново (как считает поштучный авторасчёт)."""
        return sum(1 for item in self.placements if item.setup_seconds > 0)

    @property
    def saved_seconds(self) -> int:
        return sum(item.setup_seconds - item.changeover_seconds for item in self.placements)


@dataclass(frozen=True)
class _Item:
    start: datetime
    finish: datetime
    key: SetupKey
    full: int  # подготовка по карте
    allotted: int | None  # наладка, заложенная автопланом; None — назначено раньше (с полной подготовкой)


@dataclass(frozen=True)
class _Slot:
    start: datetime
    finish: datetime
    changeover: int


def _peak(intervals: Sequence[tuple[datetime, datetime]], start: datetime, finish: datetime) -> int:
    events = sorted(
        [(max(item_start, start), 1) for item_start, _ in intervals]
        + [(min(item_finish, finish), -1) for _, item_finish in intervals],
        key=lambda event: (event[0], event[1]),
    )
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _number_key(number: str) -> tuple[int, str]:
    digits = re.sub(r"\D", "", number)
    return (int(digits) if digits else 0, number)


def _day(value: date) -> str:
    return f"{value:%d.%m.%Y}"


class _Planner:
    def __init__(self, data: AutoplanInput):
        self.data = data
        self.tasks = {task.id: task for task in data.tasks}
        self.task_of = {operation.id: task for task in data.tasks for operation in task.operations}
        self.ops = {operation.id: operation for task in data.tasks for operation in task.operations}
        # Штук полуфабриката на изделие («2 на изделие» на стрелке схемы): время операции — на её штуки.
        self.factors = {operation_id: factor for task in data.tasks for operation_id, factor in task.unit_factors.items()}
        self.topo = {
            operation.id: index for task in data.tasks for index, operation in enumerate(network_order(task.operations))
        }
        self.busy: dict[UUID, list[tuple[datetime, datetime]]] = {
            operator_id: sorted(
                (item.start, item.finish) for item in data.busy.get(operator_id, ()) if item.finish > item.start
            ) for operator_id in data.team
        }
        self.machines: dict[str, list[tuple[datetime, datetime]]] = {}
        self.streams: dict[tuple, list[_Item]] = {}
        self.plans: dict[UUID, tuple[ScheduledOperation, ProductionOperation, Decimal]] = {}
        self.placements: list[Placement] = []
        self.unplaced: dict[UUID, str] = {}
        self.pending: set[UUID] = set()
        self.version = 0
        self._forecasts: dict[tuple, tuple[dict[UUID, datetime | None], tuple[Placement, ...]]] = {}
        # Календарь — общий для всех копий расчёта (прогнозы дедлайнов считают одни и те же окна много раз).
        self._moments: dict[datetime, datetime] = {}
        self._finishes: dict[tuple[datetime, int], datetime] = {}
        for task in data.tasks:
            for operation in task.operations:
                if operation.id in data.free:
                    continue
                if operation.planned_start is not None and operation.planned_finish is not None:
                    self.plans[operation.id] = (
                        ScheduledOperation(
                            operation.id, operation.position, operation.operator_ids,
                            operation.planned_start, operation.planned_finish, False,
                        ),
                        operation, _rate(data.skills, operation.code, operation.operator_ids),
                    )
                key = data.keys.get(operation.id)
                if key is None:
                    continue
                for share in operation.shares:
                    if share.planned_start is None or share.planned_finish is None:
                        continue
                    if key.limited:
                        insort(self.machines.setdefault(key.machine, []), (share.planned_start, share.planned_finish))
                    insort(
                        self.streams.setdefault(self._stream(key, share.operator_id), []),
                        _Item(share.planned_start, share.planned_finish, key, 0, None),
                        key=lambda item: (item.start, item.finish),
                    )

    def clone(self) -> _Planner:
        copy = object.__new__(_Planner)
        copy.__dict__.update(self.__dict__)
        copy.busy = {key: list(value) for key, value in self.busy.items()}
        copy.machines = {key: list(value) for key, value in self.machines.items()}
        copy.streams = {key: list(value) for key, value in self.streams.items()}
        copy.plans = dict(self.plans)
        copy.placements = list(self.placements)
        copy.unplaced = dict(self.unplaced)
        copy.pending = set(self.pending)
        copy._forecasts = {}
        return copy

    def _next(self, moment: datetime) -> datetime:
        if moment not in self._moments:
            self._moments[moment] = next_working_moment(moment, self.data.holidays, self.data.shift)
        return self._moments[moment]

    def _finish(self, start: datetime, seconds: int) -> datetime:
        if (start, seconds) not in self._finishes:
            self._finishes[(start, seconds)] = add_work_seconds(start, seconds, self.data.holidays, self.data.shift)
        return self._finishes[(start, seconds)]

    def _capacity(self, key: SetupKey) -> int | None:
        return max(1, self.data.capacity.get(key.machine, 1)) if key.limited else None

    def _stream(self, key: SetupKey, operator_id: UUID) -> tuple:
        # Станок в одном экземпляре помнит наладку для всех; в нескольких — у каждого монтажника «свой».
        if key.limited and self._capacity(key) == 1:
            return ("machine", key.machine)
        return ("fitter", operator_id, key.machine)

    def priority(self, operation_id: UUID) -> tuple:
        task = self.task_of[operation_id]
        return (task.deadline or date.max, *_number_key(task.number), self.topo[operation_id])

    def _input_state(self, source_id: UUID) -> str:
        if source_id in self.unplaced:
            return "blocked"
        return "wait" if source_id in self.pending else "ok"

    def earliest(self, operation: ProductionOperation, operator_id: UUID) -> datetime:
        task = self.task_of[operation.id]
        rate = _rate(self.data.skills, operation.code, (operator_id,))
        lower = self.data.start
        for edge in operation.inputs:
            plan = self.plans.get(edge.input_operation_id)
            if plan is None:
                continue
            scheduled, source, source_rate = plan
            # Подготовку текущей операции не накладываем на хвост входа: при пропущенной наладке её нет.
            lower = max(lower, chain_start(
                source, scheduled, source_rate, replace(operation, setup_seconds=Decimal(0)), rate,
                task.quantity, edge_batch(edge, task.quantity), self.data.holidays, self.data.shift,
                previous_factor=self.factors[edge.input_operation_id], current_factor=self.factors[operation.id],
            ))
        return lower

    def slot(self, operation: ProductionOperation, operator_id: UUID, earliest: datetime) -> _Slot | None:
        data = self.data
        key = data.keys[operation.id]
        task = self.task_of[operation.id]
        rate = _rate(data.skills, operation.code, (operator_id,))
        items = self.streams.get(self._stream(key, operator_id), [])
        capacity = self._capacity(key)
        start = self._next(earliest)
        unit = operation.seconds_per_unit * task.operation_units(operation.id) / (rate if rate > 0 else Decimal(1))
        for _ in range(_SLOT_ATTEMPTS):
            if start >= data.end:
                return None
            previous = max((item for item in items if item.finish <= start), key=lambda item: (item.finish, item.start), default=None)
            change = changeover_seconds(previous.key if previous else None, key, operation.setup_seconds)
            # Как operation_duration_seconds, но с подготовкой, равной переналадке.
            finish = self._finish(start, ceil(change + unit))
            moves: list[datetime] = []
            for busy_start, busy_finish in self.busy.get(operator_id, ()):
                if busy_start >= finish:
                    break
                if busy_finish > start:
                    moves.append(busy_finish)
            if capacity is not None:
                overlapping = [
                    (item_start, item_finish) for item_start, item_finish in self.machines.get(key.machine, ())
                    if item_start < finish and item_finish > start
                ]
                if overlapping and _peak(overlapping, start, finish) >= capacity:
                    moves.append(min(item_finish for _, item_finish in overlapping))
            following = min((item for item in items if item.start >= finish), key=lambda item: (item.start, item.finish), default=None)
            if (
                following is not None and following.allotted is not None
                and changeover_seconds(key, following.key, Decimal(following.full)) > following.allotted
            ):
                # Следующая операция этого станка рассчитана без полной наладки — вклиниться перед ней нельзя.
                moves.append(following.finish)
            if not moves:
                return _Slot(start, finish, change)
            start = self._next(min(moves))
        return None

    def commit(self, operation_id: UUID, operator_id: UUID, slot: _Slot, pulled: bool) -> None:
        operation = self.ops[operation_id]
        key = self.data.keys[operation_id]
        insort(self.busy.setdefault(operator_id, []), (slot.start, slot.finish))
        if key.limited:
            insort(self.machines.setdefault(key.machine, []), (slot.start, slot.finish))
        insort(
            self.streams.setdefault(self._stream(key, operator_id), []),
            _Item(slot.start, slot.finish, key, ceil(max(Decimal(0), operation.setup_seconds)), slot.changeover),
            key=lambda item: (item.start, item.finish),
        )
        self.plans[operation_id] = (
            ScheduledOperation(operation_id, operation.position, (operator_id,), slot.start, slot.finish, True),
            replace(operation, setup_seconds=Decimal(slot.changeover)),
            _rate(self.data.skills, operation.code, (operator_id,)),
        )
        self.placements.append(Placement(
            self.task_of[operation_id].id, operation_id, operator_id, slot.start, slot.finish, key,
            slot.changeover, ceil(max(Decimal(0), operation.setup_seconds)), pulled,
        ))
        self.pending.discard(operation_id)
        self.version += 1

    def _ready(self) -> list[UUID]:
        """Готовые операции по приоритету. Операция, чей вход не расставлен, сама уходит в нерасставленные."""
        while True:
            ready: list[UUID] = []
            blocked = False
            for operation_id in sorted(self.pending, key=self.priority):
                states = [self._input_state(edge.input_operation_id) for edge in self.ops[operation_id].inputs]
                if "blocked" in states:
                    source = next(
                        self.ops[edge.input_operation_id] for edge in self.ops[operation_id].inputs
                        if edge.input_operation_id in self.unplaced
                    )
                    self.unplaced[operation_id] = (
                        f"Ждёт этап {source.position} «{stage_name(source.stage)}» — он не распланирован"
                    )
                    self.pending.discard(operation_id)
                    blocked = True
                elif "wait" not in states:
                    ready.append(operation_id)
            if not blocked:
                return ready

    def run(self, operations: Iterable[UUID], batching: bool) -> None:
        self.pending = set(operations)
        while self.pending:
            ready = self._ready()
            if not ready:
                if self.pending:
                    for operation_id in sorted(self.pending, key=self.priority):
                        self.unplaced[operation_id] = "Схема этапов задания замкнута в цикл"
                    self.pending.clear()
                break
            placed = self.place_best(ready[0])
            if placed is not None and batching:
                self.pull(ready[0], *placed)

    def place_best(self, operation_id: UUID) -> tuple[UUID, _Slot] | None:
        operation = self.ops[operation_id]
        best: tuple[tuple, UUID, _Slot] | None = None
        allowed = False
        for index, operator_id in enumerate(self.data.team):
            if not _allowed(self.data.skills, operation.code, operator_id):
                continue
            allowed = True
            slot = self.slot(operation, operator_id, self.earliest(operation, operator_id))
            if slot is None:
                continue
            rank = (slot.finish, slot.changeover, slot.start, index)
            if best is None or rank < best[0]:
                best = (rank, operator_id, slot)
        if best is None:
            self.unplaced[operation_id] = (
                f"Не помещается в период до {_day((self.data.end - timedelta(microseconds=1)).date())}"
                if allowed else
                f"Нет монтажников {AUTOPLAN_SITE} с допуском к операции {operation.code or stage_name(operation.stage)} — заполните компетенции"
            )
            self.pending.discard(operation_id)
            return None
        _, operator_id, slot = best
        self.commit(operation_id, operator_id, slot, False)
        return operator_id, slot

    def pull(self, operation_id: UUID, operator_id: UUID, slot: _Slot) -> None:
        """Тот же монтажник берёт готовые операции того же семейства наладки, пока это экономит наладку."""
        family = self.data.keys[operation_id].family
        exact = self.data.keys[operation_id].exact
        batch_end = slot.finish
        while True:
            candidates = sorted(
                (
                    candidate for candidate in self._ready()
                    if self.data.keys[candidate].family == family
                    and _allowed(self.data.skills, self.ops[candidate].code, operator_id)
                ),
                key=lambda candidate: (self.data.keys[candidate].exact != exact, self.priority(candidate)),
            )
            chosen: tuple[UUID, _Slot] | None = None
            for candidate in candidates:
                operation = self.ops[candidate]
                full = ceil(max(Decimal(0), operation.setup_seconds))
                if full <= 0:
                    continue
                trial = self.slot(operation, operator_id, self.earliest(operation, operator_id))
                if trial is None or trial.changeover >= full:
                    continue
                if work_seconds_between(batch_end, trial.start, self.data.holidays, self.data.shift) > BATCH_GAP_SECONDS:
                    continue
                if not self.deadline_safe(candidate, operator_id, trial):
                    continue
                chosen = (candidate, trial)
                break
            if chosen is None:
                return
            candidate, trial = chosen
            self.commit(candidate, operator_id, trial, True)
            batch_end = max(batch_end, trial.finish)
            exact = self.data.keys[candidate].exact

    def task_finish(self, task_id: UUID) -> datetime | None:
        """Конец задания по плану; None — у задания есть нерасставленная операция."""
        task = self.tasks[task_id]
        if any(operation.id in self.unplaced or operation.id in self.pending for operation in task.operations):
            return None
        finishes = [self.plans[operation.id][0].finish for operation in task.operations if operation.id in self.plans]
        return max(finishes) if finishes else None

    def _forecast(self, rivals: list[UUID]) -> tuple[dict[UUID, datetime | None], tuple[Placement, ...]]:
        """Жадный расчёт (без партий) только операций более срочных ПЗ: их концы и где они встали."""
        trial = self.clone()
        trial.run(rivals, batching=False)
        finishes = {task_id: trial.task_finish(task_id) for task_id in {self.task_of[item].id for item in rivals}}
        return finishes, tuple(trial.placements[len(self.placements):])

    def deadline_safe(self, operation_id: UUID, operator_id: UUID, slot: _Slot) -> bool:
        """Подтяжка не должна сорвать дедлайн более срочного ПЗ: прогноз его оставшихся операций с ней и без неё."""
        level = self.priority(operation_id)[:3]
        rivals = sorted(
            (item for item in self.pending if item != operation_id and self.priority(item)[:3] < level),
            key=self.priority,
        )
        watched = {self.task_of[item].id for item in rivals if self.task_of[item].deadline is not None}

        def late(task_id: UUID, finish: datetime | None) -> bool:
            return finish is None or finish.date() > self.tasks[task_id].deadline

        if not watched:
            return True
        cache = (self.version, level)
        if cache not in self._forecasts:
            self._forecasts[cache] = self._forecast(rivals)
        before, placed = self._forecasts[cache]
        on_time = {task_id for task_id in watched if not late(task_id, before.get(task_id))}
        if not on_time:
            return True
        key = self.data.keys[operation_id]
        # Расчёт детерминирован: если без подтяжки ни одна срочная операция не встала к этому монтажнику и не делила с
        # подтяжкой станок, подтяжка их не меняет — пересчитывать незачем.
        if not any(
            item.operator_id == operator_id
            or (key.limited and item.key.machine == key.machine)
            for item in placed
        ):
            return True
        trial = self.clone()
        trial.commit(operation_id, operator_id, slot, True)
        after, _ = trial._forecast(rivals)
        return not any(late(task_id, after.get(task_id)) for task_id in on_time)


def plan(data: AutoplanInput, *, batching: bool = True) -> AutoplanResult:
    """Расставить свободные операции. batching=False — без подтяжки партий (поштучный жадный расчёт)."""
    planner = _Planner(data)
    free = [operation_id for operation_id in data.free if operation_id in planner.ops]
    missing = [operation_id for operation_id in free if operation_id not in data.keys]
    if missing:
        raise ValueError("У свободной операции нет ключа наладки")
    planner.run(free, batching)
    warnings: list[str] = []
    placed = {item.operation_id: item for item in planner.placements}
    for item in sorted(planner.placements, key=lambda value: planner.priority(value.operation_id)):
        task = planner.task_of[item.operation_id]
        for consumer in task.operations:
            if consumer.id in data.free or consumer.planned_start is None:
                continue
            if any(edge.input_operation_id == item.operation_id for edge in consumer.inputs) and consumer.planned_start < item.finish:
                warnings.append(
                    f"{task.number} · этап {consumer.position} назначен на {consumer.planned_start:%d.%m %H:%M} — раньше, "
                    f"чем по автоплану закончится этап {planner.ops[item.operation_id].position}"
                )
    for task in sorted({planner.task_of[item].id: planner.task_of[item] for item in placed}.values(), key=lambda value: _number_key(value.number)):
        finish = planner.task_finish(task.id)
        if task.deadline is not None and finish is not None and finish.date() > task.deadline:
            warnings.append(f"{task.number} не успевает к дедлайну {_day(task.deadline)}: окончание {finish:%d.%m %H:%M}")
    ordered = sorted(planner.placements, key=lambda item: (item.start, item.finish, str(item.operator_id)))
    unplaced = sorted(
        (Unplaced(planner.task_of[operation_id].id, operation_id, reason) for operation_id, reason in planner.unplaced.items()),
        key=lambda item: planner.priority(item.operation_id),
    )
    return AutoplanResult(tuple(ordered), tuple(unplaced), tuple(warnings))


@dataclass(frozen=True)
class Batch:
    operator_id: UUID
    label: str
    machine_label: str
    operations: tuple[Placement, ...]

    @property
    def start(self) -> datetime:
        return self.operations[0].start

    @property
    def finish(self) -> datetime:
        return max(item.finish for item in self.operations)

    @property
    def changeover_seconds(self) -> int:
        return sum(item.changeover_seconds for item in self.operations)


def batches(placements: Sequence[Placement]) -> dict[UUID, tuple[Batch, ...]]:
    """Партии по монтажнику: подряд идущие операции одного семейства наладки, по времени."""
    result: dict[UUID, list[Batch]] = {}
    for item in sorted(placements, key=lambda value: (value.start, value.finish)):
        own = result.setdefault(item.operator_id, [])
        if own and own[-1].operations[-1].key.family == item.key.family:
            own[-1] = replace(own[-1], operations=(*own[-1].operations, item))
        else:
            own.append(Batch(item.operator_id, item.key.family_label or item.key.label, item.key.machine_label, (item,)))
    return {key: tuple(value) for key, value in result.items()}
