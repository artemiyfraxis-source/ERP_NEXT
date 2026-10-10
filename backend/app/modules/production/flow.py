"""План задания (Цель 2): как партия идёт по этапам задания — три режима передачи, люди на этапе, «первые k раньше».

Режимы (виды движения предметов труда):
- последовательно — этап берёт всю партию, когда все его входы закончили её целиком;
- параллельно — каждая передаточная партия уходит на следующий этап сразу; этап, который быстрее входа, ждёт
  следующую партию (простой внутри окна этапа);
- параллельно-последовательно — партиями, но этап начинает так поздно, чтобы дальше работать без простоев
  (так считает автоплан ProdSchedule.gs, `scheduling.chain_start`).

Время — чистые секунды работы от начала задания, без календаря: application переводит их в даты сменой цеха.
Штуки и партии — в изделиях задания; время этапа на одно изделие = норма на штуку × штук его полуфабриката на
изделие / скорость людей этапа (сумма коэффициентов, без матрицы — число людей).

«Первые k раньше»: первые k изделий проходят все этапы отдельной партией, остальные — следом, теми же людьми
(без второй подготовки). Пороги 5 % и 15 % (EARLY_NOTICE / EARLY_LIMIT) — доля k в тираже: до 5 % — образцы вперёд
в рамках задания, 5–15 % — предупреждение, больше 15 % — это уже отдельное задание. Изменение срока всего задания
считается и показывается отдельно (delay): тем же людям вторая партия обычно ничего не стоит, но с простоями —
стоит.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import ceil
from uuid import UUID

from .domain import FlowMode, ProductionValidationError

# Пороги «первые k раньше» (вопрос 4 владельца, решено агентом): доля k в тираже до 5 % — норма, 5–15 % —
# предупреждение, больше 15 % — совет запустить первые k отдельным заданием.
EARLY_NOTICE = Decimal("0.05")
EARLY_LIMIT = Decimal("0.15")
# Больше партий не считаем поштучно: передаточная партия укрупняется до тиража / MAX_BATCHES.
MAX_BATCHES = 2000

LEVEL_NONE = ""
LEVEL_OK = "ok"
LEVEL_NOTICE = "notice"
LEVEL_LIMIT = "limit"


@dataclass(frozen=True)
class FlowStep:
    operation_id: UUID
    position: int
    inputs: tuple[UUID, ...]
    setup_seconds: Decimal
    unit_seconds: Decimal  # секунд на одно изделие задания одним человеком с коэффициентом 1
    people: Decimal = Decimal(1)  # скорость этапа: сумма коэффициентов людей (N человек — N)


@dataclass(frozen=True)
class StepTiming:
    operation_id: UUID
    position: int
    start: Decimal  # начало подготовки
    first_out: Decimal  # первая передаточная партия готова
    finish: Decimal
    wait: Decimal  # простой внутри окна этапа: ждал партию от входа


@dataclass(frozen=True)
class FlowPlan:
    mode: FlowMode
    quantity: int
    transfer_batch: int
    early_quantity: int | None
    steps: tuple[StepTiming, ...]
    first_ready: Decimal  # первые изделия из финального этапа (первая партия или «первые k»)
    first_quantity: int
    finish: Decimal
    baseline_finish: Decimal  # всё готово без «первых k раньше»
    baseline_early_ready: Decimal | None  # когда первые k вышли бы без отдельной партии
    delay: Decimal | None  # насколько «первые k раньше» сдвигают конец задания (доля, < 0 — раньше), None — их нет
    share: Decimal | None  # доля «первых k» в тираже
    level: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Run:
    start: Decimal
    completions: tuple[Decimal, ...]
    wait: Decimal

    @property
    def finish(self) -> Decimal:
        return self.completions[-1]


def _order(steps: tuple[FlowStep, ...]) -> tuple[FlowStep, ...]:
    by_id = {step.operation_id: step for step in steps}
    if len(by_id) != len(steps):
        raise ProductionValidationError("Этап задания указан дважды")
    waiting = {step.operation_id: {item for item in step.inputs if item in by_id} for step in steps}
    if any(len(set(step.inputs)) != len(step.inputs) or not set(step.inputs) <= set(by_id) for step in steps):
        raise ProductionValidationError("Вход этапа ссылается на этап, которого нет в задании")
    result: list[FlowStep] = []
    while waiting:
        ready = [by_id[key] for key, sources in waiting.items() if not sources]
        if not ready:
            raise ProductionValidationError("Схема этапов задания замкнута в цикл")
        current = min(ready, key=lambda step: step.position)
        result.append(current)
        del waiting[current.operation_id]
        for sources in waiting.values():
            sources.discard(current.operation_id)
    return tuple(result)


def _sizes(quantity: int, batch: int) -> tuple[int, ...]:
    count = ceil(quantity / batch)
    return (*([batch] * (count - 1)), quantity - (count - 1) * batch)


def _lot(
    order: tuple[FlowStep, ...],
    quantity: int,
    batch: int,
    mode: FlowMode,
    free: dict[UUID, Decimal],
    setup: bool,
) -> dict[UUID, _Run]:
    """Одна партия изделий по всем этапам. free — когда люди этапа освободились (после «первых k»)."""
    sizes = _sizes(quantity, batch)
    runs: dict[UUID, _Run] = {}
    for step in order:
        speed = step.people if step.people > 0 else Decimal(1)
        unit = step.unit_seconds / speed
        work = [unit * size for size in sizes]
        prepare = step.setup_seconds if setup else Decimal(0)
        released = free.get(step.operation_id, Decimal(0))
        arrivals = [
            max((runs[source].completions[index] for source in step.inputs), default=Decimal(0))
            for index in range(len(sizes))
        ]
        if mode is FlowMode.MIXED:
            begin = released + prepare
            done = Decimal(0)
            for index, arrival in enumerate(arrivals):
                begin = max(begin, arrival - done)
                done += work[index]
            moment, completions = begin, []
            for amount in work:
                moment += amount
                completions.append(moment)
            runs[step.operation_id] = _Run(begin - prepare, tuple(completions), Decimal(0))
            continue
        # Последовательно (одна партия) и параллельно: партия начинается, как только пришла и этап свободен.
        ready = max(released + prepare, arrivals[0])
        moment, wait, completions = ready, Decimal(0), []
        for index, amount in enumerate(work):
            begin = max(moment, arrivals[index])
            wait += begin - moment
            moment = begin + amount
            completions.append(moment)
        runs[step.operation_id] = _Run(ready - prepare, tuple(completions), wait)
    return runs


def _final(steps: tuple[FlowStep, ...]) -> FlowStep:
    consumed = {source for step in steps for source in step.inputs}
    return max((step for step in steps if step.operation_id not in consumed), key=lambda step: step.position)


def _percent(value: Decimal) -> str:
    return format((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP), "f")


def plan_flow(
    steps: tuple[FlowStep, ...],
    quantity: int,
    mode: FlowMode,
    transfer_batch: int | None = None,
    early_quantity: int | None = None,
) -> FlowPlan:
    if quantity <= 0:
        raise ProductionValidationError("Количество должно быть больше нуля")
    if not steps:
        raise ProductionValidationError("В задании нет операций")
    mode = FlowMode(mode)
    order = _order(steps)
    final = _final(steps)
    warnings: list[str] = []
    batch = quantity if mode is FlowMode.SEQUENTIAL or transfer_batch is None else max(1, min(transfer_batch, quantity))
    if ceil(quantity / batch) > MAX_BATCHES:
        batch = ceil(quantity / MAX_BATCHES)
        warnings.append(f"Передаточная партия укрупнена до {batch} шт: мельче план не считается")

    def lot_batch(size: int) -> int:
        return size if mode is FlowMode.SEQUENTIAL else min(batch, size)

    base = _lot(order, quantity, batch, mode, {}, True)
    base_finish = max(run.finish for run in base.values())
    if early_quantity is None:
        return FlowPlan(
            mode=mode, quantity=quantity, transfer_batch=batch, early_quantity=None,
            steps=tuple(StepTiming(
                step.operation_id, step.position, base[step.operation_id].start,
                base[step.operation_id].completions[0], base[step.operation_id].finish, base[step.operation_id].wait,
            ) for step in steps),
            first_ready=base[final.operation_id].completions[0], first_quantity=_sizes(quantity, batch)[0],
            finish=base_finish, baseline_finish=base_finish, baseline_early_ready=None, delay=None, share=None,
            level=LEVEL_NONE, warnings=tuple(warnings),
        )
    if not 1 <= early_quantity < quantity:
        raise ProductionValidationError(f"«Первые раньше» — от 1 до {quantity - 1} шт")
    first = _lot(order, early_quantity, lot_batch(early_quantity), mode, {}, True)
    rest = _lot(
        order, quantity - early_quantity, lot_batch(quantity - early_quantity), mode,
        {key: run.finish for key, run in first.items()}, False,
    )
    finish = max(run.finish for run in rest.values())
    early_ready = first[final.operation_id].finish
    # Без отдельной партии первые k выходят с той передаточной партией финального этапа, где набирается k.
    baseline_early = base[final.operation_id].completions[min(ceil(early_quantity / batch), len(_sizes(quantity, batch))) - 1]
    delay = (finish - base_finish) / base_finish if base_finish > 0 else Decimal(0)
    share = Decimal(early_quantity) / Decimal(quantity)
    level = LEVEL_LIMIT if share > EARLY_LIMIT else LEVEL_NOTICE if share > EARLY_NOTICE else LEVEL_OK
    if level == LEVEL_NOTICE:
        warnings.append(
            f"«Первые {early_quantity} шт раньше» — {_percent(share)} % тиража (больше {_percent(EARLY_NOTICE)} %): "
            f"проверьте, что остальные успевают к сроку"
        )
    elif level == LEVEL_LIMIT:
        warnings.append(
            f"«Первые {early_quantity} шт раньше» — {_percent(share)} % тиража (больше {_percent(EARLY_LIMIT)} %): "
            f"лучше запустить {early_quantity} шт отдельным заданием"
        )
    if early_ready >= baseline_early:
        warnings.append(
            f"«Первые {early_quantity} шт раньше» ничего не дают: при этой передаточной партии они и так выходят не позже"
        )
    if delay > 0:
        warnings.append(f"Отдельная партия первых {early_quantity} шт сдвигает конец задания на {_percent(delay)} %")
    return FlowPlan(
        mode=mode, quantity=quantity, transfer_batch=batch, early_quantity=early_quantity,
        steps=tuple(StepTiming(
            step.operation_id, step.position, first[step.operation_id].start,
            first[step.operation_id].completions[0], rest[step.operation_id].finish,
            first[step.operation_id].wait + rest[step.operation_id].wait
            + max(Decimal(0), rest[step.operation_id].start - first[step.operation_id].finish),
        ) for step in steps),
        first_ready=early_ready, first_quantity=early_quantity,
        finish=finish, baseline_finish=base_finish, baseline_early_ready=baseline_early, delay=delay, share=share,
        level=level, warnings=tuple(warnings),
    )
