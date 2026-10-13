"""Pure production-task and working-calendar rules ported from ProdTasks.gs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from functools import cached_property
from math import ceil
from uuid import UUID, uuid4


class ProductionValidationError(ValueError):
    pass


class ProductionStatus(str, Enum):
    RUNNING = "Запущено"
    CANCELLED = "Отменено"
    # Цель 2: задание закрыто — изделия сданы (ЖУК: «Сдано N шт» на весь тираж; цех: финальный этап сдал тираж).
    DONE = "Выполнено"


class FlowMode(str, Enum):
    """Как партия идёт по этапам задания (виды движения предметов труда)."""
    SEQUENTIAL = "sequential"  # последовательно: следующий этап берёт всю партию, когда предыдущий её закончил
    PARALLEL = "parallel"  # параллельно: каждая передаточная партия уходит сразу, этап может ждать следующую
    MIXED = "mixed"  # параллельно-последовательно: партиями, но этап работает без простоев (как ProdSchedule.gs)


# Переходы статуса задания — только по этой таблице, проверка на сервере (ProductionTask._moved).
STATUS_TRANSITIONS: dict[ProductionStatus, frozenset[ProductionStatus]] = {
    ProductionStatus.RUNNING: frozenset({ProductionStatus.CANCELLED, ProductionStatus.DONE}),
    ProductionStatus.CANCELLED: frozenset({ProductionStatus.RUNNING}),
    ProductionStatus.DONE: frozenset({ProductionStatus.RUNNING}),
}

MAX_TEAM = 50


def _text(value: Decimal | int) -> str:
    return format(Decimal(value).normalize(), "f")


def operation_code_key(code: str) -> str:
    return str(code or "").strip()


FLOW_MODE_NAMES: dict[FlowMode, str] = {
    FlowMode.SEQUENTIAL: "Последовательно — вся партия целиком",
    FlowMode.PARALLEL: "Параллельно — каждая партия сразу",
    FlowMode.MIXED: "Параллельно-последовательно — без простоев",
}


@dataclass(frozen=True)
class BusyInterval:
    label: str
    start: datetime
    finish: datetime


@dataclass(frozen=True)
class PlanResult:
    start: datetime | None
    finish: datetime | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WorkShift:
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    start: time = time(9, 0)
    finish: time = time(17, 30)
    breaks: tuple[tuple[time, time], ...] = ((time(13, 0), time(13, 30)),)
    # Чистое рабочее время в день (владелец, 30.09: смена 9:00–17:30, обед 30 минут, ещё час на туалет и перекуры).
    effective_seconds: int = 7 * 60 * 60

    def __post_init__(self) -> None:
        if not self.weekdays or any(day < 0 or day > 6 for day in self.weekdays):
            raise ProductionValidationError("Нужно выбрать хотя бы один рабочий день")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ProductionValidationError("Рабочий день указан дважды")
        if self.start >= self.finish:
            raise ProductionValidationError("Конец смены должен быть позже начала")
        if self.effective_seconds <= 0 or self.effective_seconds > 24 * 60 * 60:
            raise ProductionValidationError("Эффективные часы должны быть больше нуля")
        for start, finish in self.breaks:
            if start >= finish or start < self.start or finish > self.finish:
                raise ProductionValidationError("Перерыв должен находиться внутри смены")


DEFAULT_SHIFT = WorkShift()

# Площадки без нормирования (владелец, 30.09: ЖУК «работают не нормированно… механизм позже придумаем»): их люди
# не попадают в загрузку, почасовое назначение и автоплан; работа отдаётся площадке целиком.
UNPLANNED_SITES = frozenset({"ЖУК"})


@dataclass(frozen=True)
class OperatorSkill:
    operator_id: UUID
    operation_code: str
    allowed: bool
    stars: int
    coefficient: Decimal

    def __post_init__(self) -> None:
        if not self.operation_code.strip():
            raise ProductionValidationError("Код операции обязателен")
        if self.stars < 0 or self.stars > 5:
            raise ProductionValidationError("Звёзды должны быть целым числом от 0 до 5")
        if not self.coefficient.is_finite() or self.coefficient <= 0 or self.coefficient > 100:
            raise ProductionValidationError("Коэффициент должен быть больше нуля")


@dataclass(frozen=True)
class OperationShare:
    """Доля операции одного монтажника (владелец, 30.09: «дробить операцию между людьми»): свои штуки и своё время.

    quantity None — вся операция бригадой, как в SUPPLYMAP: у всей бригады одно окно, норма делится на сумму
    коэффициентов. started_at / finished_at — факт («взял» — первая работа по доле, «закончил» — сдано всё по доле),
    по нему план монтажника и следующих этапов сдвигается (dispatch.reflow)."""
    operator_id: UUID
    quantity: int | None = None
    planned_start: datetime | None = None
    planned_finish: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.quantity is not None and self.quantity <= 0:
            raise ProductionValidationError("Доля должна быть больше нуля штук")
        if (self.planned_start is None) != (self.planned_finish is None):
            raise ProductionValidationError("У доли нужен плановый интервал целиком")


@dataclass(frozen=True)
class OperationInput:
    """Вход операции в сетевом графике задания (ARCH-TRACK-2): полуфабрикат другой операции того же задания.

    per_unit — сколько его штук идёт на одну штуку этой операции (Василий, 04.10: вручную на схеме, по умолчанию 1).
    transfer_batch — передаточная партия ребра; None — партия задания, как сейчас (решение 4)."""
    input_operation_id: UUID
    per_unit: Decimal = Decimal(1)
    transfer_batch: Decimal | None = None

    def __post_init__(self) -> None:
        per_unit = Decimal(self.per_unit)
        if not per_unit.is_finite() or per_unit <= 0:
            raise ProductionValidationError("Количество входа на одну штуку должно быть больше нуля")
        object.__setattr__(self, "per_unit", per_unit)
        if self.transfer_batch is not None:
            batch = Decimal(self.transfer_batch)
            if not batch.is_finite() or batch <= 0:
                raise ProductionValidationError("Передаточная партия должна быть больше нуля")
            object.__setattr__(self, "transfer_batch", batch)


@dataclass(frozen=True)
class EtkOperation:
    """Операция ЭТК внутри полуфабриката (ПФ, владелец + Василий, 13.10): её номер в ЭТК, код и название этапа.
    Порядок операций в ПФ — порядок блока схемы; номера ЭТК не меняются."""
    position: int
    code: str
    stage: str

    def __post_init__(self) -> None:
        if self.position <= 0:
            raise ProductionValidationError("Номер операции ЭТК должен быть больше нуля")
        object.__setattr__(self, "code", str(self.code or "").strip())
        object.__setattr__(self, "stage", str(self.stage or "").strip())


@dataclass(frozen=True)
class ProductionOperation:
    id: UUID
    position: int
    code: str
    stage: str
    description: str
    seconds_per_unit: Decimal
    setup_seconds: Decimal
    norm_seconds: int
    operator_ids: tuple[UUID, ...] = ()
    planned_start: datetime | None = None
    planned_finish: datetime | None = None
    assigned_by: str | None = None
    # Доли по людям — источник правды; operator_ids и плановое окно операции выводятся из них (окно — от первой
    # доли до последней). Без долей (старые вызовы) доли строятся из operator_ids: бригада с одним окном.
    shares: tuple[OperationShare, ...] = ()
    # Сетевой график (ARCH-TRACK-2): блок схемы полуфабрикатов ЭТК (None — не известен) и входы операции.
    # position остаётся порядком показа и топологическим порядком; связи этапов — только в inputs.
    stage_key: str | None = None
    inputs: tuple[OperationInput, ...] = ()
    # Задание по ПФ (13.10): операция задания — один полуфабрикат схемы, его операции ЭТК в порядке ПФ.
    # Пусто — операция задания и есть одна операция ЭТК (запуск без схемы, задания до миграции 0120).
    etk_ops: tuple[EtkOperation, ...] = ()

    def __post_init__(self) -> None:
        if not self.shares:
            if self.operator_ids:
                object.__setattr__(self, "shares", tuple(
                    OperationShare(operator_id, None, self.planned_start, self.planned_finish)
                    for operator_id in self.operator_ids
                ))
            return
        ids = tuple(share.operator_id for share in self.shares)
        if len(set(ids)) != len(ids):
            raise ProductionValidationError("Оператор выбран дважды")
        if self.operator_ids and self.operator_ids != ids:
            raise ProductionValidationError("Исполнители операции расходятся с долями")
        if len({share.quantity is None for share in self.shares}) > 1:
            raise ProductionValidationError("Операция делится либо бригадой, либо по штукам")
        starts = [share.planned_start for share in self.shares if share.planned_start is not None]
        finishes = [share.planned_finish for share in self.shares if share.planned_finish is not None]
        object.__setattr__(self, "operator_ids", ids)
        object.__setattr__(self, "planned_start", min(starts) if starts else None)
        object.__setattr__(self, "planned_finish", max(finishes) if finishes else None)

    @property
    def skill_codes(self) -> tuple[str, ...]:
        """Коды, к которым нужен допуск: все коды операций ПФ (без пустых и повторов); без ПФ — код операции."""
        codes = tuple(dict.fromkeys(item.code for item in self.etk_ops if item.code))
        return codes or (self.code,)

    @property
    def etk_positions(self) -> tuple[int, ...]:
        """Номера операций ЭТК этой операции задания: операции ПФ по порядку; без ПФ — её номер."""
        return tuple(item.position for item in self.etk_ops) or (self.position,)

    @property
    def split(self) -> bool:
        """Операция раздроблена по штукам между людьми (у каждого своя доля и своё время)."""
        return any(share.quantity is not None for share in self.shares)

    def share_of(self, operator_id: UUID) -> OperationShare | None:
        return next((share for share in self.shares if share.operator_id == operator_id), None)

    def with_shares(self, shares: tuple[OperationShare, ...], assigned_by: str | None) -> ProductionOperation:
        return replace(
            self, shares=shares, operator_ids=(), planned_start=None, planned_finish=None,
            assigned_by=str(assigned_by or "").strip() or None,
        )

    @classmethod
    def create(
        cls,
        *,
        position: int,
        code: str,
        stage: str,
        description: str,
        seconds_per_unit: Decimal,
        setup_seconds: Decimal,
        quantity: int,
        stage_key: str | None = None,
        etk_ops: tuple[EtkOperation, ...] = (),
    ) -> ProductionOperation:
        if position <= 0:
            raise ProductionValidationError("Номер этапа должен быть больше нуля")
        if not str(stage or "").strip():
            raise ProductionValidationError("Название этапа обязательно")
        unit = Decimal(seconds_per_unit)
        setup = Decimal(setup_seconds)
        if not unit.is_finite() or not setup.is_finite() or unit < 0 or setup < 0:
            raise ProductionValidationError("Нормативы операции не могут быть отрицательными")
        etk_ops = tuple(etk_ops)
        if len({item.position for item in etk_ops}) != len(etk_ops):
            raise ProductionValidationError("Операция ЭТК входит в полуфабрикат дважды")
        return cls(
            id=uuid4(),
            position=position,
            code=str(code or "").strip(),
            stage=str(stage).strip(),
            description=str(description or "").strip(),
            seconds_per_unit=unit,
            setup_seconds=setup,
            norm_seconds=ceil(setup + unit * quantity),
            stage_key=str(stage_key or "").strip() or None,
            etk_ops=etk_ops,
        )

    def assign(
        self,
        operator_ids: tuple[UUID, ...],
        plan: PlanResult,
        assigned_by: str,
    ) -> ProductionOperation:
        if len(set(operator_ids)) != len(operator_ids):
            raise ProductionValidationError("Оператор выбран дважды")
        if operator_ids and (plan.start is None or plan.finish is None):
            raise ProductionValidationError("Для назначения нужен плановый интервал")
        if not operator_ids and (plan.start is not None or plan.finish is not None):
            raise ProductionValidationError("Без операторов плановый интервал должен быть пустым")
        # Бригада с одним окном (prodAssign_). Факт «взял»/«закончил» остающихся в бригаде сохраняется.
        kept = {share.operator_id: share for share in self.shares}
        return self.with_shares(tuple(
            OperationShare(
                operator_id, None, plan.start, plan.finish,
                kept[operator_id].started_at if operator_id in kept else None,
                kept[operator_id].finished_at if operator_id in kept else None,
            )
            for operator_id in operator_ids
        ), assigned_by)


def chain_inputs(operations: tuple[ProductionOperation, ...]) -> tuple[ProductionOperation, ...]:
    """Сегодняшняя цепочка: каждая операция, кроме первой по номеру, берёт операцию предыдущего номера (1 на 1)."""
    ordered = sorted(operations, key=lambda operation: operation.position)
    previous = {current.id: earlier.id for earlier, current in zip(ordered, ordered[1:])}
    return tuple(
        replace(operation, inputs=(OperationInput(previous[operation.id]),) if operation.id in previous else ())
        for operation in operations
    )


def link_inputs(
    operations: tuple[ProductionOperation, ...],
    edges: dict[int, tuple[tuple[int, Decimal], ...]],
) -> tuple[ProductionOperation, ...]:
    """Входы по номерам этапов: {номер операции: ((номер операции-входа, штук на одну), …)}. Входы операции
    хранятся по возрастанию номера входа. Номер, которого нет в задании, — ошибка маршрута."""
    by_position = {operation.position: operation for operation in operations}
    if not set(edges) <= set(by_position) or any(
        source not in by_position for sources in edges.values() for source, _ in sources
    ):
        raise ProductionValidationError("Вход операции ссылается на этап, которого нет в маршруте")
    return tuple(
        replace(operation, inputs=tuple(
            OperationInput(by_position[source].id, Decimal(per_unit))
            for source, per_unit in sorted(edges.get(operation.position, ()), key=lambda edge: edge[0])
        ))
        for operation in operations
    )


def final_operation(operations: tuple[ProductionOperation, ...]) -> ProductionOperation:
    """Финальная операция задания — та, чей полуфабрикат не берёт никто (из неё выходит изделие). В проверенном
    графе она одна; если кандидатов несколько, берётся последняя по номеру (как max(position) до графа)."""
    if not operations:
        raise ProductionValidationError("В задании нет операций")
    consumed = {item.input_operation_id for operation in operations for item in operation.inputs}
    return max(
        (operation for operation in operations if operation.id not in consumed),
        key=lambda operation: operation.position,
    )


def unit_factors(operations: tuple[ProductionOperation, ...]) -> dict[UUID, Decimal]:
    """Сколько штук полуфабриката каждой операции идёт на одно изделие («N на изделие» на стрелке схемы): per_unit
    по рёбрам до финальной операции перемножаются, несколько потребителей — сумма. Финальная и цепочка — 1."""
    consumers: dict[UUID, list[tuple[UUID, Decimal]]] = {}
    for item in operations:
        for edge in item.inputs:
            consumers.setdefault(edge.input_operation_id, []).append((item.id, edge.per_unit))
    factors: dict[UUID, Decimal] = {}

    def factor(operation_id: UUID, path: frozenset[UUID]) -> Decimal:
        if operation_id not in factors:
            targets = [(target, per_unit) for target, per_unit in consumers.get(operation_id, ()) if target not in path]
            factors[operation_id] = sum(
                (per_unit * factor(target, path | {operation_id}) for target, per_unit in targets), Decimal(0),
            ) if targets else Decimal(1)
        return factors[operation_id]

    for item in operations:
        factor(item.id, frozenset())
    return factors


def _check_network(operations: tuple[ProductionOperation, ...]) -> None:
    """Граф входов задания: входы — операции этого же задания, без самоссылок и повторов, без циклов, финал один."""
    ids = {operation.id for operation in operations}
    for operation in operations:
        sources = [item.input_operation_id for item in operation.inputs]
        if operation.id in sources or not set(sources) <= ids:
            raise ProductionValidationError(f"Этап {operation.position}: вход должен быть другим этапом этого задания")
        if len(set(sources)) != len(sources):
            raise ProductionValidationError(f"Этап {operation.position}: один вход указан дважды")
    remaining = {operation.id: {item.input_operation_id for item in operation.inputs} for operation in operations}
    while remaining:
        pending = set(remaining)
        ready = [operation_id for operation_id, sources in remaining.items() if not sources & pending]
        if not ready:
            raise ProductionValidationError("Схема этапов задания замкнута в цикл")
        for operation_id in ready:
            del remaining[operation_id]
    consumed = {item.input_operation_id for operation in operations for item in operation.inputs}
    if len(ids - consumed) != 1:
        raise ProductionValidationError("У схемы этапов задания должна быть ровно одна финальная операция")


@dataclass(frozen=True)
class ProductionTask:
    id: UUID
    number: str
    source_idempotency_key: str
    source_fingerprint: str
    version: int
    created_at: datetime
    project: str
    item: str
    name: str
    quantity: int
    fulfillment_request_id: UUID | None
    deadline: date | None
    status: ProductionStatus
    launched_by: str
    operations: tuple[ProductionOperation, ...]
    # Площадка, которой задание отдано целиком (владелец, 30.09: ЖУК работает не нормированно — «отдать все в жук»).
    handed_to: str = ""
    # Цель 2, план задания: техкарта и её ревизия, с которых взяты нормы (None — задание до миграции 0089);
    # режим передачи партии по этапам, передаточная партия (None — вся партия), «первые k раньше» (None — нет)
    # и команда — люди задания из справочника сотрудников (по id, порядок выбора).
    techcard_id: UUID | None = None
    techcard_revision: int | None = None
    flow_mode: FlowMode = FlowMode.MIXED
    transfer_batch: int | None = None
    early_quantity: int | None = None
    team: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        # Задание без единого входа (маршрут без схемы, старые вызовы) идёт сегодняшней цепочкой по номерам:
        # у любого задания граф входов явный, неявного «предыдущего этапа» больше нет.
        if len(self.operations) > 1 and not any(operation.inputs for operation in self.operations):
            object.__setattr__(self, "operations", chain_inputs(self.operations))

    @classmethod
    def create(
        cls,
        *,
        number: str,
        source_idempotency_key: str,
        source_fingerprint: str,
        project: str,
        item: str,
        name: str,
        quantity: int,
        fulfillment_request_id: UUID | None,
        deadline: date | None,
        launched_by: str,
        operations: tuple[ProductionOperation, ...],
        created_at: datetime,
        techcard_id: UUID | None = None,
        techcard_revision: int | None = None,
    ) -> ProductionTask:
        if techcard_revision is not None and techcard_revision < 1:
            raise ProductionValidationError("Ревизия техкарты должна быть больше нуля")
        if quantity <= 0:
            raise ProductionValidationError("Количество должно быть целым числом больше нуля")
        if not operations:
            raise ProductionValidationError(f"Техкарта «{item}» не выдана — запускать нечего")
        if len({operation.position for operation in operations}) != len(operations):
            raise ProductionValidationError("Номера этапов маршрута должны быть уникальны")
        required = (number, source_idempotency_key, source_fingerprint, project, item, launched_by)
        if any(not str(value or "").strip() for value in required):
            raise ProductionValidationError("Номер, проект, изделие и инициатор обязательны")
        task = cls(
            id=uuid4(),
            number=str(number).strip(),
            source_idempotency_key=str(source_idempotency_key).strip(),
            source_fingerprint=str(source_fingerprint).strip(),
            version=1,
            created_at=created_at,
            project=str(project).strip(),
            item=str(item).strip(),
            name=str(name or "").strip(),
            quantity=quantity,
            fulfillment_request_id=fulfillment_request_id,
            deadline=deadline,
            status=ProductionStatus.RUNNING,
            launched_by=str(launched_by).strip(),
            operations=tuple(sorted(operations, key=lambda operation: operation.position)),
            techcard_id=techcard_id,
            techcard_revision=techcard_revision,
        )
        _check_network(task.operations)
        # Норма операции — на её штуки по схеме: ветка «2 на изделие» делает вдвое больше тиража.
        return replace(task, operations=tuple(
            replace(operation, norm_seconds=ceil(
                operation.setup_seconds + operation.seconds_per_unit * task.operation_units(operation.id),
            ))
            for operation in task.operations
        ))

    @property
    def final_operation(self) -> ProductionOperation:
        """Операция, из которой выходит изделие: её полуфабрикат не берёт ни одна другая операция."""
        return final_operation(self.operations)

    @cached_property
    def unit_factors(self) -> dict[UUID, Decimal]:
        """Штук полуфабриката каждой операции на одно изделие (unit_factors): «2 на изделие» — 2."""
        return unit_factors(self.operations)

    def operation_units(self, operation_id: UUID, products: int | Decimal | None = None) -> Decimal:
        """Сколько штук делает операция на products изделий (None — на всё задание): изделия × штук на изделие.
        Доли, факт в изделиях и передаточные партии считаются в изделиях задания; время по норме и «готово» по
        сданному — в штуках операции, как их сдаёт монтажник (execution.operation_quantity)."""
        return Decimal(self.quantity if products is None else products) * self.unit_factors.get(operation_id, Decimal(1))

    def operation(self, operation_id: UUID) -> ProductionOperation:
        operation = next((item for item in self.operations if item.id == operation_id), None)
        if operation is None:
            raise ProductionValidationError("Операция задания не найдена")
        return operation

    def assign(
        self,
        operation_id: UUID,
        operator_ids: tuple[UUID, ...],
        plan: PlanResult,
        assigned_by: str,
    ) -> ProductionTask:
        self._check_open("назначать нельзя")
        if self.handed_to:
            raise ProductionValidationError(f"Задание {self.number} отдано в {self.handed_to} — по людям не расписывается")
        operation = self.operation(operation_id)
        assigned = operation.assign(operator_ids, plan, assigned_by)
        return replace(
            self,
            operations=tuple(assigned if item.id == operation_id else item for item in self.operations),
            version=self.version + 1,
        )

    def apply_schedule(
        self,
        assignments: tuple[tuple[UUID, tuple[UUID, ...], datetime, datetime], ...],
        assigned_by: str,
    ) -> ProductionTask:
        self._check_open("планировать нельзя")
        if self.handed_to:
            raise ProductionValidationError(f"Задание {self.number} отдано в {self.handed_to} — по людям не расписывается")
        by_operation = {item[0]: item[1:] for item in assignments}
        if len(by_operation) != len(assignments) or set(by_operation) != {item.id for item in self.operations}:
            raise ProductionValidationError("Автоплан должен содержать все операции задания")
        operations = tuple(
            # Назначенное раньше автоплан оставляет как есть (replan = false): доли по штукам и факт не теряются.
            operation if (operation.operator_ids, operation.planned_start, operation.planned_finish) == by_operation[operation.id]
            else operation.assign(
                by_operation[operation.id][0],
                PlanResult(by_operation[operation.id][1], by_operation[operation.id][2], ()),
                assigned_by,
            )
            for operation in self.operations
        )
        return replace(self, operations=operations, version=self.version + 1)

    def with_operations(self, operations: tuple[ProductionOperation, ...]) -> ProductionTask:
        """Задание с изменёнными долями операций (раздача по штукам, сдвиг плана по факту)."""
        if self.status is ProductionStatus.CANCELLED:
            raise ProductionValidationError(f"Задание {self.number} отменено — назначать нельзя")
        if self.handed_to:
            raise ProductionValidationError(f"Задание {self.number} отдано в {self.handed_to} — по людям не расписывается")
        changed = {operation.id: operation for operation in operations}
        if not set(changed) <= {operation.id for operation in self.operations}:
            raise ProductionValidationError("Операция задания не найдена")
        return replace(
            self,
            operations=tuple(changed.get(operation.id, operation) for operation in self.operations),
            version=self.version + 1,
        )

    def _check_open(self, action: str) -> None:
        """Менять план можно только у задания «Запущено»: отменённое и выполненное — только вернуть в работу."""
        if self.status is ProductionStatus.CANCELLED:
            raise ProductionValidationError(f"Задание {self.number} отменено — {action}")
        if self.status is ProductionStatus.DONE:
            raise ProductionValidationError(f"Задание {self.number} выполнено — {action}")

    def _moved(self, target: ProductionStatus) -> ProductionTask:
        if target not in STATUS_TRANSITIONS[self.status]:
            raise ProductionValidationError(
                f"Задание {self.number} уже в статусе «{target.value}»" if target is self.status
                else f"Задание {self.number} в статусе «{self.status.value}» — перевести в «{target.value}» нельзя"
            )
        return replace(self, status=target, version=self.version + 1)

    def set_cancelled(self, cancelled: bool) -> ProductionTask:
        if not cancelled and self.status is not ProductionStatus.CANCELLED:
            raise ProductionValidationError(f"Задание {self.number} не отменено — возвращать нечего")
        return self._moved(ProductionStatus.CANCELLED if cancelled else ProductionStatus.RUNNING)

    def complete(self, done: Decimal, required: Decimal) -> ProductionTask:
        """«Выполнено»: сдано не меньше, чем нужно по заданию (ЖУК — «Сдано N шт», цех — годные финального этапа)."""
        if self.status is ProductionStatus.RUNNING and done < required:
            raise ProductionValidationError(
                f"Задание {self.number}: сдано {_text(done)} из {_text(required)} шт — ещё не выполнено"
            )
        return self._moved(ProductionStatus.DONE)

    def reopen(self) -> ProductionTask:
        """Выполненное задание — снова в работу (ошибочно закрыли, отменили сдачу ЖУК)."""
        return self._moved(ProductionStatus.RUNNING)

    def with_flow(
        self,
        *,
        flow_mode: FlowMode,
        transfer_batch: int | None,
        early_quantity: int | None,
        team: tuple[UUID, ...],
    ) -> ProductionTask:
        """План задания: режим передачи, передаточная партия, «первые k раньше» и команда."""
        self._check_open("план не меняется")
        mode = FlowMode(flow_mode)
        if transfer_batch is not None and not 1 <= transfer_batch <= self.quantity:
            raise ProductionValidationError(f"Передаточная партия — от 1 до {self.quantity} шт")
        if early_quantity is not None and not 1 <= early_quantity < self.quantity:
            raise ProductionValidationError(
                f"«Первые раньше» — от 1 до {self.quantity - 1} шт (весь тираж — это просто задание)"
            )
        if len(set(team)) != len(team):
            raise ProductionValidationError("Человек выбран в команду дважды")
        if len(team) > MAX_TEAM:
            raise ProductionValidationError(f"В команде не больше {MAX_TEAM} человек")
        # Последовательно партия идёт целиком — своя передаточная партия не хранится.
        batch = None if mode is FlowMode.SEQUENTIAL else transfer_batch
        if (mode, batch, early_quantity, tuple(team)) == (
            self.flow_mode, self.transfer_batch, self.early_quantity, self.team,
        ):
            return self
        return replace(
            self, flow_mode=mode, transfer_batch=batch, early_quantity=early_quantity, team=tuple(team),
            version=self.version + 1,
        )

    def refresh_norms(
        self,
        norms: dict[int, tuple[str, Decimal, Decimal]],
        techcard_id: UUID,
        revision: int,
    ) -> ProductionTask:
        """Нормы из ЭТК по кнопке (никогда сами): {номер этапа: (код, сек на штуку, подготовка)}. Только нормы —
        этапы, их коды, назначения и факт не меняются; другой состав этапов — перезапуск задания, не обновление."""
        self._check_open("нормы не обновляются")
        if revision < 1:
            raise ProductionValidationError("Ревизия техкарты должна быть больше нуля")
        own = {operation.position: operation_code_key(operation.code) for operation in self.operations}
        fresh = {position: operation_code_key(code) for position, (code, _, _) in norms.items()}
        if own != fresh:
            raise ProductionValidationError(
                "Этапы техкарты изменились (другое число этапов или коды операций) — нормы не обновлены. "
                "Запустите новое задание по выданной техкарте"
            )
        operations = []
        for operation in self.operations:
            _, unit, setup = norms[operation.position]
            unit, setup = Decimal(unit), Decimal(setup)
            if not unit.is_finite() or not setup.is_finite() or unit < 0 or setup < 0:
                raise ProductionValidationError("Нормативы операции не могут быть отрицательными")
            operations.append(replace(
                operation, seconds_per_unit=unit, setup_seconds=setup,
                norm_seconds=ceil(setup + unit * self.operation_units(operation.id)),
            ))
        return replace(
            self, operations=tuple(operations), techcard_id=techcard_id, techcard_revision=revision,
            version=self.version + 1,
        )

    def hand_over(self, site: str, delivered: int = 0) -> ProductionTask:
        """Отдать задание площадке целиком ("" — вернуть на распределение по людям). Назначения и план по часам
        снимаются: площадка без нормирования раскладывает работу сама. Сданное площадкой (delivered) держит
        задание у неё: вернуть на распределение можно, только отменив её сдачи."""
        site = str(site or "").strip()
        if site and site not in UNPLANNED_SITES:
            raise ProductionValidationError(f"Отдать целиком можно только в {', '.join(sorted(UNPLANNED_SITES))}")
        self._check_open("передавать нельзя")
        if not site and delivered > 0:
            raise ProductionValidationError(
                f"{self.handed_to} уже сдал {delivered} шт — вернуть на распределение можно, только отменив сдачи"
            )
        if site == self.handed_to:
            raise ProductionValidationError(
                f"Задание {self.number} уже в {site}" if site else f"Задание {self.number} и так распределяется по людям"
            )
        operations = tuple(item.with_shares((), item.assigned_by) for item in self.operations) if site else self.operations
        return replace(self, handed_to=site, operations=operations, version=self.version + 1)


@dataclass(frozen=True)
class TaskDelivery:
    """«Сдано N шт» площадки без нормирования (ЖУК): задание отдано ей целиком, операций в ERP она не отмечает.
    По умолчанию сдаёт всё одним разом; можно частями. Отмена — отметка, строка остаётся в истории."""
    id: UUID
    task_id: UUID
    site: str
    quantity: int
    delivered_at: datetime
    delivered_by: str
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ProductionValidationError("Сдать можно целое число штук больше нуля")
        if (self.cancelled_at is None) != (self.cancelled_by is None):
            raise ProductionValidationError("У отмены сдачи нужны время и кто отменил")

    @property
    def active(self) -> bool:
        return self.cancelled_at is None


def delivered_total(deliveries) -> int:
    return sum(item.quantity for item in deliveries if item.active)


def operation_duration_seconds(
    operation: ProductionOperation,
    quantity: int | Decimal,
    operators: int | Decimal = 1,
    *,
    operator_rate: Decimal | None = None,
) -> int:
    # prodOpDurationSec_: rate is the sum of the chosen operators' coefficients; only a non-positive rate falls back to 1.
    rate = Decimal(operator_rate if operator_rate is not None else operators)
    rate = rate if rate > 0 else Decimal(1)
    return ceil(operation.setup_seconds + operation.seconds_per_unit * quantity / rate)


@dataclass(frozen=True)
class SkillCheck:
    checked: bool
    allowed: bool
    stars: int
    coefficient: Decimal


def skill_of(
    skills: dict[str, dict[UUID, OperatorSkill]],
    operation_code: str | tuple[str, ...],
    operator_id: UUID,
) -> SkillCheck:
    """prodSkillOf_: no row for the code yet — not checked; a row exists — no allowed row means no admission.

    Several codes (a ПФ task operation, `ProductionOperation.skill_codes`): the fitter needs admission to every one
    of them, and the ПФ goes at his lowest coefficient among them (stars — the lowest of the checked codes)."""
    if not isinstance(operation_code, str):
        checks = [skill_of(skills, code, operator_id) for code in operation_code] or [skill_of(skills, "", operator_id)]
        checked = [item for item in checks if item.checked]
        return SkillCheck(
            bool(checked), all(item.allowed for item in checks), min((item.stars for item in checked), default=0),
            min(item.coefficient for item in checks),
        )
    by_code = skills.get(str(operation_code or ""))
    if not by_code:
        return SkillCheck(False, True, 0, Decimal(1))
    skill = by_code.get(operator_id)
    if skill is None or not skill.allowed:
        return SkillCheck(True, False, skill.stars if skill else 0, Decimal(1))
    return SkillCheck(True, True, skill.stars, skill.coefficient)


def _day_spans(
    moment: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> tuple[tuple[datetime, datetime], ...]:
    if moment.weekday() not in shift.weekdays or moment.date() in holidays:
        return ()
    minute_spans = [(shift.start, shift.finish)]
    for break_start, break_finish in sorted(shift.breaks):
        split: list[tuple[time, time]] = []
        for start, finish in minute_spans:
            if break_finish <= start or break_start >= finish:
                split.append((start, finish))
                continue
            if start < break_start:
                split.append((start, min(finish, break_start)))
            if break_finish < finish:
                split.append((max(start, break_finish), finish))
        minute_spans = split
    result: list[tuple[datetime, datetime]] = []
    for start, finish in minute_spans:
        span_start = datetime.combine(moment.date(), start, moment.tzinfo)
        span_finish = datetime.combine(moment.date(), finish, moment.tzinfo)
        if span_finish > span_start:
            result.append((span_start, span_finish))
    return tuple(result)


def work_rate(shift: WorkShift = DEFAULT_SHIFT) -> Decimal:
    """Доля чистой работы в смене без обеда: мелкие перерывы (туалет, перекуры) размазаны по всему дню,
    поэтому норма 7 ч при 8 ч смены без обеда занимает весь день 9:00–17:30, а не заканчивается в 16:30."""
    available = 0
    spans = [(shift.start, shift.finish)]
    for break_start, break_finish in sorted(shift.breaks):
        split: list[tuple[time, time]] = []
        for start, finish in spans:
            if break_finish <= start or break_start >= finish:
                split.append((start, finish))
                continue
            if start < break_start:
                split.append((start, min(finish, break_start)))
            if break_finish < finish:
                split.append((max(start, break_finish), finish))
        spans = split
    for start, finish in spans:
        available += (finish.hour * 3600 + finish.minute * 60) - (start.hour * 3600 + start.minute * 60)
    if available <= 0:
        return Decimal(1)
    return min(Decimal(1), Decimal(shift.effective_seconds) / Decimal(available))


def next_working_moment(
    moment: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    current = moment
    for _ in range(400):
        for start, finish in _day_spans(current, holidays, shift):
            if current < finish:
                return max(current, start)
        current = datetime.combine(current.date() + timedelta(days=1), time.min, current.tzinfo)
    raise ProductionValidationError("Рабочий момент не найден в пределах календаря")


def clock_seconds(seconds: int | Decimal, shift: WorkShift = DEFAULT_SHIFT) -> int:
    """Чистые секунды нормы → секунды смены на часах: растянуты на долю мелких перерывов, до минуты вверх."""
    remaining = Decimal(max(0, seconds))
    rate = work_rate(shift)
    if rate < 1:
        # Растянутое время округляем до минуты вверх, чтобы в плане не было секунд.
        remaining = Decimal(ceil(remaining / rate / 60) * 60)
    return int(remaining)


def advance_clock(
    moment: datetime,
    seconds: int,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    """Момент через seconds секунд смены на часах (уже растянутых, см. clock_seconds) с первого рабочего момента."""
    current = next_working_moment(moment, holidays, shift)
    remaining = max(0, int(seconds))
    if not remaining:
        return current
    for _ in range(400):
        for start, finish in _day_spans(current, holidays, shift):
            if finish <= current:
                continue
            segment_start = max(current, start)
            available = int((finish - segment_start).total_seconds())
            if remaining <= available:
                return segment_start + timedelta(seconds=remaining)
            remaining -= available
        current = next_working_moment(
            datetime.combine(current.date() + timedelta(days=1), time.min, current.tzinfo),
            holidays,
            shift,
        )
    raise ProductionValidationError("Норматив не помещается в пределы календаря")


def rewind_clock(
    moment: datetime,
    seconds: int,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    """Момент за seconds секунд смены на часах до moment — обратный ход advance_clock."""
    remaining = max(0, int(seconds))
    if not remaining:
        return moment
    day = moment.date()
    for _ in range(400):
        for start, finish in reversed(_day_spans(datetime.combine(day, time.min, moment.tzinfo), holidays, shift)):
            end = min(finish, moment)
            if end <= start:
                continue
            available = int((end - start).total_seconds())
            if remaining <= available:
                return end - timedelta(seconds=remaining)
            remaining -= available
        day -= timedelta(days=1)
    raise ProductionValidationError("Рабочий момент не найден в пределах календаря")


def clock_between(
    start: datetime,
    finish: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> int:
    """Секунды смены на часах между моментами (перерывы смены, выходные и праздники не считаются)."""
    if finish <= start:
        return 0
    total = 0
    current = start
    for _ in range(800):
        if current >= finish:
            break
        for span_start, span_finish in _day_spans(current, holidays, shift):
            lower = max(span_start, current)
            upper = min(span_finish, finish)
            if upper > lower:
                total += int((upper - lower).total_seconds())
        current = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), current.tzinfo)
    return total


def add_work_seconds(
    moment: datetime,
    seconds: int,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    # Норматив — чистые секунды работы; на часах он растягивается на долю перерывов внутри смены.
    return advance_clock(moment, clock_seconds(seconds, shift), holidays, shift)


def plan_operation(
    *,
    operation: ProductionOperation,
    quantity: int | Decimal,
    operator_ids: tuple[UUID, ...],
    requested_start: datetime | None,
    earliest_start: datetime,
    busy: dict[UUID, tuple[BusyInterval, ...]],
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
    operator_rate: Decimal | None = None,
    skills: dict[str, dict[UUID, OperatorSkill]] | None = None,
    names: dict[UUID, str] | None = None,
) -> PlanResult:
    if not operator_ids:
        return PlanResult(None, None, ())
    checks = [skill_of(skills or {}, operation.skill_codes, operator_id) for operator_id in operator_ids]
    if operator_rate is None and skills is not None:
        operator_rate = sum((item.coefficient for item in checks), Decimal(0))
    duration = operation_duration_seconds(
        operation, quantity, len(operator_ids), operator_rate=operator_rate,
    )
    start = next_working_moment(requested_start or earliest_start, holidays, shift)
    if requested_start is None:
        for _ in range(50):
            finish = add_work_seconds(start, duration, holidays, shift)
            collisions = [
                interval.finish
                for operator_id in operator_ids
                for interval in busy.get(operator_id, ())
                if interval.start < finish and start < interval.finish
            ]
            if not collisions:
                break
            start = next_working_moment(min(collisions), holidays, shift)
    finish = add_work_seconds(start, duration, holidays, shift)
    warnings: list[str] = []
    if start < earliest_start:
        warnings.append("Старт раньше окончания предыдущего этапа")
    for operator_id, check in zip(operator_ids, checks):
        name = (names or {}).get(operator_id, "Оператор")
        if not check.allowed:
            warnings.append(f"{name}: нет допуска к операции {', '.join(operation.skill_codes)}")
        for interval in busy.get(operator_id, ()):
            if interval.start < finish and start < interval.finish:
                warnings.append(
                    f"{name} занят(а): {interval.label}, "
                    f"{interval.start:%d.%m.%Y %H:%M}–{interval.finish:%d.%m.%Y %H:%M}"
                )
    return PlanResult(start, finish, tuple(warnings))
