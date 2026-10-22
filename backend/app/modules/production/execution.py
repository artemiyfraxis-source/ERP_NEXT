"""Transactional production execution and box confirmation use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.shipping.domain import AcceptanceAct, FinishedGood, MoveContext

from .application import CalendarRepository, ConcurrentProductionUpdate, OperatorRepository, ProductionRepository, SkillRepository, record_fact, release_fact
from .domain import ProductionOperation, ProductionStatus, ProductionTask, ProductionValidationError, operation_duration_seconds
from .launchable import stage_name
from .traceability import (
    ConsumptionEdge,
    DefectDecision,
    DefectDisposition,
    DefectRecord,
    FinishedGoodsReceipt,
    InputStage,
    MaterialConsumption,
    MaterialInput,
    MaterialSource,
    ProductionBox,
    ProductionOutput,
    WorkInput,
    WorkSession,
    WorkStatus,
    _count,
    correction_moves,
    missing_stages,
    stages_text,
    take_inputs,
    validate_box_number,
)
from .worklog import WorkEvent, WorkTimeEntry, earned_norm_seconds


class BoxRepository(Protocol):
    def get(self, box_id: UUID) -> ProductionBox | None: ...
    def by_number(self, number: str) -> ProductionBox | None: ...
    def list(self, *, task_id: UUID | None = None, operator_id: UUID | None = None, limit: int = 500) -> Sequence[ProductionBox]: ...
    def reserve_number(self) -> str: ...
    def reserve_code(self, *, project: str, item: str, operator_id: UUID, day: date) -> str: ...
    def by_code(self, text: str) -> Sequence[ProductionBox]: ...
    def save(self, box: ProductionBox) -> None: ...


class WorkRepository(Protocol):
    def get(self, session_id: UUID) -> WorkSession | None: ...
    def current(self, operator_id: UUID) -> WorkSession | None: ...
    def inputs(self, session_id: UUID) -> Sequence[WorkInput]: ...
    def material_inputs(self, session_id: UUID) -> Sequence[MaterialInput]: ...
    def save(self, session: WorkSession) -> None: ...
    def add_inputs(self, session_id: UUID, inputs: tuple[WorkInput, ...]) -> None: ...
    def add_material_inputs(self, session_id: UUID, inputs: tuple[MaterialInput, ...]) -> None: ...
    def record_card_versions(self, operation_id: UUID, versions: Sequence[dict]) -> bool: ...
    def card_versions(self, operation_ids: Sequence[UUID]) -> dict[UUID, list[dict]]: ...


class OutputRepository(Protocol):
    def add(self, output: ProductionOutput) -> None: ...
    def get(self, output_id: UUID) -> ProductionOutput | None: ...
    def for_session(self, session_id: UUID) -> ProductionOutput | None: ...
    def is_superseded(self, output_id: UUID) -> bool: ...
    def lineage(self, output_id: UUID) -> tuple[UUID, ...]: ...
    def active_for_box(self, box_id: UUID) -> Sequence[ProductionOutput]: ...
    def list_for_task(self, task_id: UUID) -> Sequence[ProductionOutput]: ...
    def total_for_operation(self, operation_id: UUID) -> Decimal: ...
    def active_for_tasks(self, task_ids: tuple[UUID, ...]) -> Sequence[ProductionOutput]: ...


class ConsumptionRepository(Protocol):
    def add(self, edge: ConsumptionEdge) -> None: ...
    def net_into(self, target_box_id: UUID, output_ids: tuple[UUID, ...]) -> Sequence[WorkInput]: ...


class MaterialRepository(Protocol):
    def request_status(self, request_id: UUID) -> FulfillmentStatus | None: ...
    def list_sources(self, request_id: UUID) -> Sequence[MaterialSource]: ...
    def source(self, request_id: UUID, line_id: UUID) -> MaterialSource | None: ...
    def add_consumption(self, item: MaterialConsumption) -> None: ...


class FinishedGoodsRepository(Protocol):
    def get(self, good_id: UUID) -> FinishedGood | None: ...
    def add(self, good: FinishedGood, move: MoveContext | None = None) -> object: ...
    def reserve_grn(self, year: int) -> str: ...
    def record_act(self, good: FinishedGood, moves: object) -> AcceptanceAct | None: ...


class FinishedGoodsReceiptRepository(Protocol):
    def by_box(self, box_id: UUID) -> FinishedGoodsReceipt | None: ...
    def add(self, receipt: FinishedGoodsReceipt) -> None: ...


class QcHandoffRepository(Protocol):
    def by_box(self, box_id: UUID): ...


class SerialHandoffRepository(Protocol):
    def mark_handed(self, box_id: UUID, grn: str) -> None: ...


class DefectRepository(Protocol):
    def get(self, defect_id: UUID) -> DefectRecord | None: ...
    def active_for_outputs(self, output_ids: tuple[UUID, ...]) -> DefectRecord | None: ...
    def list(self, limit: int = 500) -> Sequence[DefectRecord]: ...
    def reserve_number(self) -> str: ...
    def save(self, defect: DefectRecord) -> None: ...
    def add_decision(self, decision: DefectDecision) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class WorkLogRepository(Protocol):
    """Журнал рабочего времени: только дополняется (worklog.py)."""
    def add(self, entry: WorkTimeEntry) -> None: ...
    def by_key(self, idempotency_key: str) -> WorkTimeEntry | None: ...
    def next_sequence(self, session_id: UUID) -> int: ...


class ExecutionUnitOfWork(Protocol):
    production: ProductionRepository
    # Смена, отсутствия и компетенции — чтобы факт «взял»/«закончил» двигал план (application.record_fact).
    calendar: CalendarRepository
    skills: SkillRepository
    operators: OperatorRepository
    boxes: BoxRepository
    work: WorkRepository
    outputs: OutputRepository
    consumption: ConsumptionRepository
    materials: MaterialRepository
    goods: FinishedGoodsRepository
    finished_goods_receipts: FinishedGoodsReceiptRepository
    defects: DefectRepository
    qc: QcHandoffRepository
    serials: SerialHandoffRepository
    audit: AppendOnlyRepository
    work_log: WorkLogRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class StartWorkCommand:
    task_id: UUID
    operation_id: UUID
    box_numbers: tuple[str, ...]
    actor: Actor
    now: datetime
    materials: tuple[MaterialInput, ...] = ()
    # Работа за сотрудника (владелец, 12.10.2026): исполнитель — монтажник, чьи это факт, коробки и время;
    # actor — кто нажал. None — сам actor. Право проверяет API (production/api.py `_performer`).
    performer: Actor | None = field(default=None, kw_only=True)
    # Ключ повтора: тот же ключ второй раз ничего не делает и отдаёт уже записанное (двойной клик, повтор запроса).
    idempotency_key: str | None = field(default=None, kw_only=True)
    # Откуда карточки операций ЭТК этой операции сейчас ([{position, source, changed_at}], etk_live): первое начало
    # операции записывает их один раз. None — ЭТК не читали (база техкарт не настроена): ничего не пишется.
    card_versions: tuple[dict, ...] | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class SessionCommand:
    session_id: UUID
    actor: Actor
    now: datetime
    performer: Actor | None = field(default=None, kw_only=True)
    idempotency_key: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class SubmitWorkCommand(SessionCommand):
    good_quantity: Decimal
    defect_quantity: Decimal
    defect_character: str = ""
    defect_description: str = ""


@dataclass(frozen=True)
class BoxCommand:
    box_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ReturnBoxCommand(BoxCommand):
    reason: str = ""


@dataclass(frozen=True)
class HandoffBoxCommand(BoxCommand):
    finished_good_id: UUID


@dataclass(frozen=True)
class CorrectBoxCommand(BoxCommand):
    output_id: UUID
    good_quantity: Decimal
    defect_quantity: Decimal
    defect_character: str = ""
    defect_description: str = ""
    performer: Actor | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class DecideDefectCommand:
    defect_id: UUID
    expected_version: int
    disposition: DefectDisposition
    act_reference: str
    actor: Actor
    now: datetime


def _audit(aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict, uow: ExecutionUnitOfWork, performer: Actor | None = None) -> None:
    # Работа за сотрудника: в журнале действий — кто нажал (actor) и за кого («Артемий за Богородская Элина»).
    if performer is not None and performer.id != actor.id:
        details = {**details, "performer_id": str(performer.id), "performer_name": performer.name,
                   "on_behalf": f"{actor.name} за {performer.name}"}
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id, event_type=event_type,
        actor=actor, occurred_at=now, details=details,
    ))


def _worker(command) -> Actor:
    """Исполнитель: монтажник, за которого работают, иначе тот, кто нажал."""
    return command.performer or command.actor


def _replayed(command, event: WorkEvent, uow: ExecutionUnitOfWork, session_id: UUID | None = None) -> WorkSession | None:
    """Повтор того же действия (тот же ключ): сессия, которую оно уже записало; другой смысл ключа — отказ."""
    key = str(command.idempotency_key or "").strip()
    if not key:
        return None
    entry = uow.work_log.by_key(key)
    if entry is None:
        return None
    if entry.event is not event or entry.operator_id != _worker(command).id or (session_id is not None and entry.session_id != session_id):
        raise ConcurrentProductionUpdate("Ключ повтора уже использован другим действием — обновите страницу")
    session = uow.work.get(entry.session_id)
    if session is None:
        raise KeyError(f"Work session not found: {entry.session_id}")
    return session


def _log(
    uow: ExecutionUnitOfWork, command, session: WorkSession, event: WorkEvent,
    task: ProductionTask, operation: ProductionOperation, *,
    good: Decimal | None = None, defect: Decimal | None = None,
    running: int | None = None, earned: int | None = None,
) -> None:
    """Строка журнала времени с копией норм операции на этот момент (worklog.py)."""
    worker = _worker(command)
    share = operation.share_of(worker.id)
    sequence = uow.work_log.next_sequence(session.id)
    key = str(command.idempotency_key or "").strip() or f"auto:{session.id}:{sequence}"
    uow.work_log.add(WorkTimeEntry(
        id=uuid4(), idempotency_key=key, session_id=session.id, sequence=sequence, event=event,
        occurred_at=command.now, task_id=task.id, operation_id=operation.id,
        operator_id=worker.id, operator_name=worker.name,
        actor_id=command.actor.id, actor_name=command.actor.name,
        project=task.project, item=task.item, task_number=task.number,
        operation_code=operation.code, stage=operation.stage, position=operation.position,
        seconds_per_unit=operation.seconds_per_unit, setup_seconds=operation.setup_seconds,
        # Плановая норма доли без коэффициента скорости: штуки доли (бригада — всё задание) по норме ЭТК.
        planned_norm_seconds=operation_duration_seconds(
            operation, task.operation_units(operation.id, share.quantity if share is not None else None),
        ),
        good_quantity=good, defect_quantity=defect, running_seconds=running, earned_norm_seconds=earned,
    ))


def _task_operation(task_id: UUID, operation_id: UUID, actor: Actor, uow: ExecutionUnitOfWork):
    task = uow.production.get(task_id)
    if task is None:
        raise KeyError(f"Production task not found: {task_id}")
    if task.status is ProductionStatus.DONE:
        raise ProductionValidationError(f"Задание {task.number} выполнено — работа по нему закрыта")
    if task.status is not ProductionStatus.RUNNING:
        raise ProductionValidationError("Отменённое задание выполнять нельзя")
    operation = task.operation(operation_id)
    if actor.id not in operation.operator_ids:
        raise ProductionValidationError("Операция не назначена этому оператору")
    return task, operation


def input_stages(task: ProductionTask, operation: ProductionOperation) -> tuple[InputStage, ...]:
    """Входы операции по сетевому графику задания (ARCH-TRACK-2): этапы, чьи коробки она берёт, со штуками на одну.
    У цепочки (все задания до графа) это один вход — предыдущий номер, 1 на 1."""
    by_id = {item.id: item for item in task.operations}
    return tuple(InputStage(
        operation_id=edge.input_operation_id,
        position=by_id[edge.input_operation_id].position,
        name=stage_name(by_id[edge.input_operation_id].stage),
        per_unit=edge.per_unit,
    ) for edge in operation.inputs)


def unit_factor(task: ProductionTask, operation: ProductionOperation) -> Decimal:
    """Сколько штук полуфабриката операции идёт на одно изделие: per_unit по рёбрам до финальной операции,
    несколько потребителей — сумма. Финальная и любая операция цепочки — 1."""
    consumers: dict[UUID, list[tuple[UUID, Decimal]]] = {}
    for item in task.operations:
        for edge in item.inputs:
            consumers.setdefault(edge.input_operation_id, []).append((item.id, edge.per_unit))
    factors: dict[UUID, Decimal] = {}

    def factor(operation_id: UUID) -> Decimal:
        if operation_id not in factors:
            targets = consumers.get(operation_id, ())
            factors[operation_id] = sum(
                (per_unit * factor(target) for target, per_unit in targets), Decimal(0),
            ) if targets else Decimal(1)
        return factors[operation_id]

    return factor(operation.id)


def operation_quantity(task: ProductionTask, operation: ProductionOperation) -> Decimal:
    """Сколько штук должна сдать операция по заданию: тираж × штук её полуфабриката на одно изделие.
    Нарезка, которой на сборку идёт 2 на одну, режет вдвое больше тиража; в цепочке — ровно тираж."""
    return Decimal(task.quantity) * unit_factor(task, operation)


def material_operation(task: ProductionTask) -> ProductionOperation:
    """Операция, на которую идут складские партии комплекта. Решение 5 ARCH-TRACK: исходный блок берёт строки
    комплекта, привязанные к нему, непривязанные — первый исходный блок. Строки к блокам в задании пока не
    привязаны, поэтому весь комплект — первой по номеру операции без входов (в цепочке это этап 1, как сегодня)."""
    return min((item for item in task.operations if not item.inputs), key=lambda item: item.position)


def _box_by_label(raw: str, uow: ExecutionUnitOfWork) -> ProductionBox:
    """Коробка по номеру (000123-4) или по коду с коробки (180К.11·КБ1·13.10·Б07·3, разделители любые)."""
    try:
        number = validate_box_number(raw)
    except ProductionValidationError as error:
        found = list(uow.boxes.by_code(raw))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ProductionValidationError(f"Код «{str(raw).strip()}» подходит к нескольким коробкам — введите его с точками, как на коробке.") from error
        raise ProductionValidationError(f"«{str(raw).strip()}» — нет такой коробки: введите код с коробки или номер 000123-4.") from error
    box = uow.boxes.by_number(number)
    if box is None:
        raise ProductionValidationError(f"Коробки {number} нет в учёте.")
    return box


def _input_boxes(numbers: Sequence[str], item: str, previous_position: int | tuple[int, ...], uow: ExecutionUnitOfWork, task_id: UUID | None = None) -> list[ProductionBox]:
    """prodCheckInputs_: boxes by the numbers (or box codes) as typed, repeats skipped, first refusal reason raised."""
    seen: set[UUID] = set()
    boxes: list[ProductionBox] = []
    for raw in numbers:
        if not str(raw or "").strip():
            continue
        box = _box_by_label(raw, uow)
        if box.id in seen:
            continue
        seen.add(box.id)
        error = box.input_error(item=item, previous_position=previous_position, task_id=task_id)
        if error:
            raise ProductionValidationError(error)
        boxes.append(box)
    return boxes


def start_work(command: StartWorkCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    with uow:
        worker = _worker(command)
        replayed = _replayed(command, WorkEvent.START, uow)
        if replayed is not None:
            return replayed
        if uow.work.current(worker.id) is not None:
            raise ProductionValidationError("Сначала завершите или отмените текущую работу")
        task, operation = _task_operation(command.task_id, command.operation_id, worker, uow)
        stages = input_stages(task, operation)
        takes_kit = operation.id == material_operation(task).id
        if not stages and command.box_numbers:
            # Операция без входов (начало ветки) начинается с комплекта, коробок других этапов она не берёт.
            raise ProductionValidationError(
                "Первая операция не берёт коробки предыдущего этапа" if takes_kit
                else f"Этап {operation.position} — начало ветки: коробки других этапов он не берёт",
            )
        if takes_kit:
            # SUPPLYMAP начинает первый этап без выданного комплекта; выданные партии — по желанию, для родословной.
            if command.materials and task.fulfillment_request_id is None:
                raise ProductionValidationError("Задание не связано с заявкой выдачи")
            if command.materials and uow.materials.request_status(task.fulfillment_request_id) is not FulfillmentStatus.ISSUED:
                raise ProductionValidationError("Комплект по заявке ещё не выдан в производство")
            if len({item.fulfillment_line_id for item in command.materials}) != len(command.materials):
                raise ProductionValidationError("Одна позиция выдачи указана дважды")
            for item in command.materials:
                source = uow.materials.source(task.fulfillment_request_id, item.fulfillment_line_id)
                if source is None:
                    raise ProductionValidationError("Позиция выдачи не найдена")
                amount = Decimal(item.quantity)
                if not amount.is_finite() or amount <= 0 or amount > source.available:
                    raise ProductionValidationError("Недостаточно доступного остатка позиции выдачи")
        elif command.materials:
            raise ProductionValidationError("Складские партии берёт только первая операция")
        inputs: tuple[WorkInput, ...] = ()
        if stages:
            # prodWorkStart_: оператор вводит номера входных коробок; штуки берутся из них при сдаче.
            # Сетевой график: коробки — с любого этапа-входа операции, а не только с предыдущего номера.
            if not command.box_numbers:
                raise ProductionValidationError(f"Введи номера входных коробок с {stages_text(stages)}.")
            boxes = _input_boxes(command.box_numbers, task.item, tuple(stage.position for stage in stages), uow, task.id)
            missing = missing_stages(stages, boxes) if len(stages) > 1 else ()
            if missing:
                # Сборка из нескольких полуфабрикатов начинается, только когда есть коробки каждого входа.
                raise ProductionValidationError(
                    f"Нет входных коробок с {stages_text(missing, named=True)}: "
                    "операция собирается из полуфабрикатов всех входящих этапов.",
                )
            inputs = tuple(WorkInput(box.id, box.available) for box in boxes)
        session = WorkSession.start(task.id, operation.id, worker.id, command.now)
        uow.work.save(session)
        uow.work.add_inputs(session.id, inputs)
        uow.work.add_material_inputs(session.id, command.materials)
        _audit(session.id, "production.work_started", command.actor, command.now, {
            "task_id": str(task.id), "operation_id": str(operation.id),
            "inputs": [str(item.box_id) for item in inputs],
            "materials": {str(item.fulfillment_line_id): str(item.quantity) for item in command.materials},
        }, uow, worker)
        _log(uow, command, session, WorkEvent.START, task, operation)
        if command.card_versions is not None:
            # Версия шаблона, по которой начали (владелец 17.10): только первое начало, дальше запись не меняется.
            uow.work.record_card_versions(operation.id, command.card_versions)
        # «Взял»: окно доли начинается сейчас, работа после неё сдвигается или подтягивается.
        record_fact(
            uow, task_id=task.id, operation_id=operation.id, operator_id=worker.id,
            actor=command.actor, now=command.now, started=True,
        )
        uow.commit()
        return session


def _session(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    session = uow.work.get(command.session_id)
    if session is None:
        raise KeyError(f"Work session not found: {command.session_id}")
    if session.operator_id != _worker(command).id:
        raise ProductionValidationError("Работа принадлежит другому оператору")
    return session


def _session_task(session: WorkSession, uow: ExecutionUnitOfWork) -> tuple[ProductionTask, ProductionOperation]:
    task = uow.production.get(session.task_id)
    if task is None:
        raise KeyError(f"Production task not found: {session.task_id}")
    return task, task.operation(session.operation_id)


def _session_step(command: SessionCommand, uow: ExecutionUnitOfWork, event: WorkEvent, audit_type: str) -> WorkSession:
    """Пауза / продолжить: сессия исполнителя, строка журнала времени, запись в журнал действий."""
    with uow:
        replayed = _replayed(command, event, uow, command.session_id)
        if replayed is not None:
            return replayed
        current = _session(command, uow)
        session = current.pause(command.now) if event is WorkEvent.PAUSE else current.resume(command.now)
        uow.work.save(session)
        task, operation = _session_task(session, uow)
        _log(uow, command, session, event, task, operation)
        _audit(session.id, audit_type, command.actor, command.now, {}, uow, _worker(command))
        uow.commit()
        return session


def pause_work(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    return _session_step(command, uow, WorkEvent.PAUSE, "production.work_paused")


def resume_work(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    return _session_step(command, uow, WorkEvent.RESUME, "production.work_resumed")


def cancel_work(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    with uow:
        replayed = _replayed(command, WorkEvent.CANCEL, uow, command.session_id)
        if replayed is not None:
            return replayed
        session = _session(command, uow)
        # prodWorkToggle_ drop: входные коробки при начале не резервируются, отменять нечего.
        cancelled = session.cancel()
        uow.work.save(cancelled)
        task, operation = _session_task(session, uow)
        _log(uow, command, cancelled, WorkEvent.CANCEL, task, operation)
        _audit(cancelled.id, "production.work_cancelled", command.actor, command.now, {}, uow, _worker(command))
        release_fact(uow, task_id=session.task_id, operation_id=session.operation_id, operator_id=session.operator_id, now=command.now)
        uow.commit()
        return cancelled


MOSCOW = ZoneInfo("Europe/Moscow")


def _shop_day(now: datetime) -> date:
    """День цеха для кода коробки: по Москве (наивное время — уже московское)."""
    return now.astimezone(MOSCOW).date() if now.tzinfo is not None else now.date()


def submit_work(command: SubmitWorkCommand, uow: ExecutionUnitOfWork) -> tuple[WorkSession, ProductionBox, ProductionOutput, DefectRecord | None]:
    with uow:
        worker = _worker(command)
        replayed = _replayed(command, WorkEvent.FINISH, uow, command.session_id)
        if replayed is not None:
            output = uow.outputs.for_session(replayed.id)
            box = uow.boxes.get(output.output_box_id) if output is not None else None
            if output is None or box is None:
                raise ConcurrentProductionUpdate("Сдача по этому ключу не найдена — обновите страницу")
            return replayed, box, output, uow.defects.active_for_outputs((output.id,))
        session = _session(command, uow)
        task, operation = _task_operation(session.task_id, session.operation_id, worker, uow)
        good = Decimal(command.good_quantity)
        defect = Decimal(command.defect_quantity)
        if not good.is_finite() or not defect.is_finite() or good < 0 or defect < 0 or good + defect <= 0:
            raise ProductionValidationError("Сумма годных и брака должна быть положительной")
        materials = tuple(uow.work.material_inputs(session.id))
        done = uow.outputs.total_for_operation(operation.id)
        mine = sum((
            item.good_quantity + item.defect_quantity for item in uow.outputs.active_for_tasks((task.id,))
            if item.operation_id == operation.id and item.operator_id == worker.id
        ), Decimal(0))
        # Сетевой график: операция сдаёт тираж × штук её полуфабриката на изделие (в цепочке — тираж).
        factor = unit_factor(task, operation)
        required = operation_quantity(task, operation)
        if done + good + defect > required:
            left = max(Decimal(0), required - done)
            raise ProductionValidationError(
                f"Больше, чем в задании: уже сдано {_count(done)} из {_count(required)}, осталось {_count(left)}.",
            )
        inputs: tuple[WorkInput, ...] = ()
        stages = input_stages(task, operation)
        positions = tuple(stage.position for stage in stages)
        if stages:
            # prodWorkSubmit_: входные проверяем ещё раз — пока шла работа, коробку мог забрать напарник;
            # годные + брак берутся из них по порядку ввода (prodPick_) — с каждого входа × его штук на одну.
            sources = []
            for item in uow.work.inputs(session.id):
                box = uow.boxes.get(item.box_id)
                if box is None:
                    raise ProductionValidationError("Входная коробка исчезла")
                error = box.input_error(item=task.item, previous_position=positions, task_id=task.id)
                if error:
                    raise ProductionValidationError(error)
                sources.append(box)
            inputs = take_inputs(stages, sources, good + defect)
        output_box = ProductionBox.submitted(
            task_id=task.id, operation_id=operation.id, item=task.item,
            operation_position=operation.position, operator_id=worker.id,
            number=uow.boxes.reserve_number(), good=good, actor=worker.name, now=command.now,
            code=uow.boxes.reserve_code(project=task.project, item=task.item, operator_id=worker.id, day=_shop_day(command.now)),
        )
        running = session.running_seconds(command.now)
        completed = session.complete(command.now)
        output = ProductionOutput(
            id=uuid4(), session_id=session.id, task_id=task.id,
            operation_id=operation.id, operator_id=worker.id,
            started_at=session.started_at, finished_at=command.now,
            pause_seconds=completed.pause_seconds, good_quantity=good,
            defect_quantity=defect, output_box_id=output_box.id,
        )
        defect_record = None
        if defect > 0:
            defect_record = DefectRecord.create(
                number=uow.defects.reserve_number(), output_id=output.id,
                source_box_id=output_box.id, task_id=task.id,
                operation_id=operation.id, operator_id=worker.id,
                quantity=defect, character=command.defect_character,
                description=command.defect_description, created_at=command.now,
            )
        for item in inputs:
            source = uow.boxes.get(item.box_id)
            if source is None:
                raise ProductionValidationError("Входная коробка исчезла")
            uow.boxes.save(source.reserve(
                item.quantity, item=task.item, previous_position=positions,
            ).consume(item.quantity))
            uow.consumption.add(ConsumptionEdge(
                id=uuid4(), source_box_id=item.box_id, target_box_id=output_box.id,
                quantity=item.quantity, output_id=output.id,
            ))
        if task.fulfillment_request_id is not None:
            for item in materials:
                source = uow.materials.source(task.fulfillment_request_id, item.fulfillment_line_id)
                if source is None:
                    raise ProductionValidationError("Позиция выдачи исчезла")
                uow.materials.add_consumption(MaterialConsumption(
                    id=uuid4(), fulfillment_line_id=item.fulfillment_line_id,
                    target_box_id=output_box.id, quantity=item.quantity,
                    output_id=output.id, grn=source.grn, package=source.package,
                ))
        uow.boxes.save(output_box)
        uow.work.save(completed)
        uow.outputs.add(output)
        if defect_record is not None:
            uow.defects.save(defect_record)
        _audit(output.id, "production.output_recorded", command.actor, command.now, {
            "task_id": str(task.id), "operation_id": str(operation.id),
            "good": str(good), "defect": str(defect), "box_id": str(output_box.id),
        }, uow, worker)
        # Сдача в журнал времени: факт сессии без пауз и норма на сданные штуки (подготовка — в первой сдаче).
        _log(
            uow, command, completed, WorkEvent.FINISH, task, operation, good=good, defect=defect, running=running,
            earned=earned_norm_seconds(operation.seconds_per_unit, operation.setup_seconds, good + defect, first=mine == 0),
        )
        # Сдал свои штуки — «закончил»: окно доли кончается сейчас; не всё — конец по оставшимся штукам.
        # План считает штуки в изделиях задания: полуфабрикат «2 на одну» переводится в изделия.
        record_fact(
            uow, task_id=task.id, operation_id=operation.id, operator_id=worker.id,
            actor=command.actor, now=command.now,
            mine=(mine + good + defect) / factor, total=(done + good + defect) / factor,
        )
        uow.commit()
        return completed, output_box, output, defect_record


@dataclass(frozen=True)
class ManualOutputCommand:
    """Ввод готовых мастером (владелец, 20.10.2026: «люди уже начали работать и сделали какое-то кол-во — нужен ручной
    ввод и отбития готовых»): штуки, сделанные мимо терминала, без сессии и без времени работы."""
    task_id: UUID
    operation_id: UUID
    good_quantity: Decimal
    actor: Actor
    now: datetime
    # Монтажник, чьи это штуки (назначен на этап); None — без исполнителя, запись мастера.
    performer: Actor | None = field(default=None, kw_only=True)
    # Входов в учёте не хватает — этапы-входы «отбиваются готовыми» на нехватку (по цепочке вверх), запись мастера.
    fill_inputs: bool = field(default=False, kw_only=True)


def _manual_sources(task: ProductionTask, positions: tuple[int, ...], uow: ExecutionUnitOfWork) -> list[ProductionBox]:
    """Коробки этапов-входов задания, которые можно взять, — старые первыми."""
    boxes = [
        box for box in uow.boxes.list(task_id=task.id, limit=10_000)
        if box.operation_position in positions and not box.input_error(item=task.item, previous_position=positions, task_id=task.id)
    ]
    return sorted(boxes, key=lambda box: (box.created_at, box.number or ""))


def _close_open_work(
    task: ProductionTask, operation: ProductionOperation, operator_ids: Sequence[UUID],
    command: ManualOutputCommand, uow: ExecutionUnitOfWork, closed: list[str],
) -> None:
    """Открытая работа этапа, которую закрыл ввод готовых (владелец 20.10: «удали из активных»), отменяется, как
    «Отменить» на терминале: в журнале времени — отмена, «взял» без своих штук снимается."""
    for operator_id in dict.fromkeys(operator_ids):
        session = uow.work.current(operator_id)
        if session is None or session.task_id != task.id or session.operation_id != operation.id:
            continue
        operator = uow.operators.get(operator_id)
        performer = Actor(id=operator_id, name=operator.name if operator else "?", department=operator.department if operator else "")
        step = SessionCommand(session_id=session.id, actor=command.actor, now=command.now, performer=performer)
        cancelled = session.cancel()
        uow.work.save(cancelled)
        _log(uow, step, cancelled, WorkEvent.CANCEL, task, operation)
        _audit(cancelled.id, "production.work_cancelled", command.actor, command.now, {"reason": "ввод готовых"}, uow, performer)
        release_fact(uow, task_id=task.id, operation_id=operation.id, operator_id=operator_id, now=command.now)
        closed.append(f"{performer.name} (этап {operation.position})")


def _enter_output(
    task: ProductionTask, operation: ProductionOperation, worker: Actor, good: Decimal,
    command: ManualOutputCommand, uow: ExecutionUnitOfWork, filled: list[ProductionBox], closed: list[str],
) -> tuple[ProductionBox, ProductionOutput]:
    done = uow.outputs.total_for_operation(operation.id)
    required = operation_quantity(task, operation)
    label = f"Этап {operation.position} «{stage_name(operation.stage)}»"
    if done + good > required:
        left = max(Decimal(0), required - done)
        raise ProductionValidationError(
            f"{label}: уже сдано {_count(done)} из {_count(required)}, осталось {_count(left)}.",
        )
    stages = input_stages(task, operation)
    positions = tuple(stage.position for stage in stages)
    sources = _manual_sources(task, positions, uow) if stages else []
    short = []
    for stage in stages:
        have = sum((box.available for box in sources if box.operation_position == stage.position), Decimal(0))
        need = good * stage.per_unit
        if have >= need:
            continue
        if not command.fill_inputs:
            short.append(f"{stage.label} — есть {_count(have)}, нужно {_count(need)}")
            continue
        box, _ = _enter_output(task, task.operation(stage.operation_id), command.actor, need - have, command, uow, filled, closed)
        filled.append(box)
        sources.append(box)
    if short:
        raise ProductionValidationError(
            f"{label}: в учёте не хватает полуфабрикатов: {'; '.join(short)}. "
            "Введите готовые на этих этапах или отметьте «Пропущенные этапы-входы отбить готовыми».",
        )
    inputs = take_inputs(stages, sources, good) if stages else ()
    output_box = ProductionBox.submitted(
        task_id=task.id, operation_id=operation.id, item=task.item,
        operation_position=operation.position, operator_id=worker.id,
        number=uow.boxes.reserve_number(), good=good, actor=worker.name, now=command.now,
        code=uow.boxes.reserve_code(project=task.project, item=task.item, operator_id=worker.id, day=_shop_day(command.now)),
    )
    output = ProductionOutput(
        id=uuid4(), session_id=None, task_id=task.id,
        operation_id=operation.id, operator_id=worker.id,
        started_at=command.now, finished_at=command.now, pause_seconds=0,
        good_quantity=good, defect_quantity=Decimal(0), output_box_id=output_box.id,
    )
    uow.boxes.save(output_box)
    uow.outputs.add(output)
    for item in inputs:
        source = uow.boxes.get(item.box_id)
        if source is None:
            raise ProductionValidationError("Входная коробка исчезла")
        uow.boxes.save(source.reserve(item.quantity, item=task.item, previous_position=positions).consume(item.quantity))
        uow.consumption.add(ConsumptionEdge(
            id=uuid4(), source_box_id=item.box_id, target_box_id=output_box.id,
            quantity=item.quantity, output_id=output.id,
        ))
    _audit(output.id, "production.output_entered", command.actor, command.now, {
        "task_id": str(task.id), "operation_id": str(operation.id), "good": str(good),
        "box_id": str(output_box.id), "manual": True,
    }, uow, worker)
    # Штуки монтажника двигают его план, как сдача с терминала (без доли — плана нет, record_fact ничего не делает).
    factor = unit_factor(task, operation)
    mine = sum((
        item.good_quantity + item.defect_quantity for item in uow.outputs.active_for_tasks((task.id,))
        if item.operation_id == operation.id and item.operator_id == worker.id
    ), Decimal(0))
    record_fact(
        uow, task_id=task.id, operation_id=operation.id, operator_id=worker.id,
        actor=command.actor, now=command.now, mine=mine / factor, total=(done + good) / factor,
    )
    # Штуки монтажника записаны — его открытая работа по этапу закрывается; этап сдан целиком — у всех на нём.
    _close_open_work(task, operation, operation.operator_ids if done + good >= required else (worker.id,), command, uow, closed)
    return output_box, output


def record_manual_output(command: ManualOutputCommand, uow: ExecutionUnitOfWork) -> tuple[ProductionBox, ProductionOutput, tuple[ProductionBox, ...], tuple[str, ...]]:
    """Готовые штуки этапа без терминала: коробка сданных, входы — из коробок этапов-входов задания (старые первыми).
    Журнал времени не пишется: времени работы нет, «факт / норма» монтажника эти штуки не трогают. Ошибся —
    коробку возвращают и исправляют, как любую сдачу. Открытая работа, которую закрыли эти штуки, отменяется.
    Возвращает коробку, запись, коробки отбитых входов и чья открытая работа снята."""
    with uow:
        result = enter_manual_output(command, uow)
        uow.commit()
        return result


def enter_manual_output(command: ManualOutputCommand, uow: ExecutionUnitOfWork) -> tuple[ProductionBox, ProductionOutput, tuple[ProductionBox, ...], tuple[str, ...]]:
    """Ввод готовых внутри открытой единицы работы, без коммита: тот же путь для «Уже сделано» по заданию и по КБ
    без задания на доске мастера (задание создаётся там же, одной транзакцией — board.record_board_output)."""
    task = uow.production.get(command.task_id)
    if task is None:
        raise KeyError(f"Production task not found: {command.task_id}")
    if task.status is not ProductionStatus.RUNNING:
        raise ProductionValidationError(f"Задание {task.number} не в работе — готовые не вводятся")
    if task.handed_to:
        raise ProductionValidationError(f"Задание {task.number} отдано в {task.handed_to} — там «Сдано N шт»")
    operation = task.operation(command.operation_id)
    if command.performer is not None and command.performer.id not in operation.operator_ids:
        raise ProductionValidationError(f"{command.performer.name} не назначен(а) на этот этап")
    good = Decimal(command.good_quantity)
    if not good.is_finite() or good <= 0 or good != good.to_integral_value():
        raise ProductionValidationError("Готовых — целое число штук больше нуля")
    filled: list[ProductionBox] = []
    closed: list[str] = []
    box, output = _enter_output(task, operation, _worker(command), good, command, uow, filled, closed)
    return box, output, tuple(filled), tuple(closed)


def confirm_box(command: BoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    """Подтверждение мастером — до 13.10.2026, без маршрута API: подтверждает ОТК (quality.inspect_box), а коробки
    остальных операций годятся на вход сразу после сдачи. Остаётся ради старых событий журнала и их отката."""
    with uow:
        box = _box(command, uow)
        updated = box.confirm(command.actor.name, command.now)
        uow.boxes.save(updated)
        _audit(box.id, "production.box_confirmed", command.actor, command.now, {"number": box.number}, uow)
        uow.commit()
        return updated


def return_box(command: ReturnBoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    """«Вернуть…» — действие ОТК (владелец, 13.10.2026): сданная коробка, из которой ещё ничего не брали и которую ОТК
    не принимал, уходит монтажнику на исправление; после «Исправить» она снова «Закрыта оператором» (коробка последней
    операции — снова в очереди ОТК). Взятую на вход или принятую ОТК вернуть нельзя (`return_to_operator`)."""
    with uow:
        box = _box(command, uow)
        if uow.qc.by_box(box.id) is not None:
            raise ProductionValidationError("Коробка уже принята ОТК — вернуть её нельзя")
        updated = box.return_to_operator(command.reason)
        uow.boxes.save(updated)
        _audit(box.id, "production.box_returned", command.actor, command.now, {
            "number": box.number, "reason": str(command.reason or "").strip(),
        }, uow)
        uow.commit()
        return updated


def correct_box(command: CorrectBoxCommand, uow: ExecutionUnitOfWork) -> tuple[ProductionBox, ProductionOutput, DefectRecord | None]:
    """SUPPLYMAP `prodWorkFix_`: a new output replaces one output of a returned box.

    The box quantity is replaced, not topped up; the difference in pieces is taken from or given back to
    the same input boxes; the replaced output stays on record but drops out of every sum.
    """
    with uow:
        box = _box(command, uow)
        replaced = uow.outputs.get(command.output_id)
        if replaced is None or replaced.output_box_id != box.id:
            raise ProductionValidationError("Запись выработки не относится к этой коробке")
        if uow.outputs.is_superseded(replaced.id):
            raise ProductionValidationError("Запись выработки уже исправлена — исправляйте последнюю")
        good = Decimal(command.good_quantity)
        defect = Decimal(command.defect_quantity)
        if not good.is_finite() or not defect.is_finite() or good < 0 or defect < 0 or good + defect <= 0:
            raise ProductionValidationError("Сумма годных и брака должна быть положительной")
        worker = _worker(command)
        corrected_box = box.correct(
            replaced_good=replaced.good_quantity, good=good,
            operator_id=worker.id, actor=worker.name, now=command.now,
        )
        task, operation = _task_operation(box.task_id, box.operation_id, worker, uow)
        replaced_total = replaced.good_quantity + replaced.defect_quantity
        done = uow.outputs.total_for_operation(operation.id) - replaced_total
        required = operation_quantity(task, operation)
        if done + good + defect > required:
            left = max(Decimal(0), required - done)
            raise ProductionValidationError(
                f"Больше, чем в задании: уже сдано {_count(done)} из {_count(required)}, осталось {_count(left)}.",
            )
        output = ProductionOutput(
            id=uuid4(), session_id=None, task_id=task.id,
            operation_id=operation.id, operator_id=worker.id,
            started_at=command.now, finished_at=command.now, pause_seconds=0,
            good_quantity=good, defect_quantity=defect, output_box_id=box.id,
            correction_of_id=replaced.id,
        )
        # Added first so every row below that references it flushes after it.
        uow.outputs.add(output)
        lineage = uow.outputs.lineage(replaced.id)
        delta = good + defect - replaced_total
        moves: tuple[WorkInput, ...] = ()
        stages = input_stages(task, operation)
        if stages and delta != 0:
            # Разница в штуках выхода — с каждого входа × его штук на одну, из тех же коробок.
            taken = tuple(uow.consumption.net_into(box.id, lineage))
            known = {item.box_id: source for item in taken if (source := uow.boxes.get(item.box_id)) is not None}
            moves = correction_moves(stages, taken, known, delta)
            for item in moves:
                source = uow.boxes.get(item.box_id)
                if source is None:
                    raise ProductionValidationError("Входная коробка исчезла")
                if delta > 0:
                    moved = source.reserve(
                        item.quantity, item=task.item, previous_position=tuple(stage.position for stage in stages),
                    ).consume(item.quantity)
                else:
                    moved = source.give_back(item.quantity)
                uow.boxes.save(moved)
                uow.consumption.add(ConsumptionEdge(
                    id=uuid4(), source_box_id=item.box_id, target_box_id=box.id,
                    quantity=item.quantity if delta > 0 else -item.quantity, output_id=output.id,
                ))
        defect_record = None
        if defect != replaced.defect_quantity:
            previous_defect = uow.defects.active_for_outputs(lineage)
            if defect > 0:
                defect_record = DefectRecord.create(
                    number=uow.defects.reserve_number(), output_id=output.id,
                    source_box_id=box.id, task_id=task.id,
                    operation_id=operation.id, operator_id=worker.id,
                    quantity=defect, character=command.defect_character,
                    description=command.defect_description, created_at=command.now,
                )
                uow.defects.save(defect_record)
            if previous_defect is not None:
                uow.defects.save(previous_defect.mark_replaced(output.id))
        uow.boxes.save(corrected_box)
        _audit(box.id, "production.box_corrected", command.actor, command.now, {
            "number": box.number, "replaced_output_id": str(replaced.id), "output_id": str(output.id),
            "good": [str(replaced.good_quantity), str(good)],
            "defect": [str(replaced.defect_quantity), str(defect)],
            "inputs": {str(item.box_id): str(item.quantity if delta > 0 else -item.quantity) for item in moves},
            "defect_number": defect_record.number if defect_record else None,
        }, uow, worker)
        uow.commit()
        return corrected_box, output, defect_record


def _box(command: BoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    box = uow.boxes.get(command.box_id)
    if box is None:
        raise KeyError(f"Production box not found: {command.box_id}")
    if box.version != command.expected_version:
        raise ProductionValidationError("Коробка изменилась после загрузки формы")
    return box


def handoff_box(command: HandoffBoxCommand, uow: ExecutionUnitOfWork) -> tuple[ProductionBox, FinishedGood, FinishedGoodsReceipt]:
    with uow:
        box = _box(command, uow)
        if uow.finished_goods_receipts.by_box(box.id) is not None:
            raise ProductionValidationError("Коробка уже сдана на склад готовой продукции")
        task = uow.production.get(box.task_id)
        if task is None:
            raise KeyError(f"Production task not found: {box.task_id}")
        # Сетевой график: изделие выходит из финальной операции (её полуфабрикат не берёт никто), а не из max номера.
        if box.operation_id != task.final_operation.id:
            raise ProductionValidationError("На склад ГП сдаётся только последняя операция маршрута")
        inspection = uow.qc.by_box(box.id)
        if inspection is None:
            raise ProductionValidationError("Перед сдачей на склад ГП коробку должен принять ОТК")
        good = uow.goods.get(command.finished_good_id)
        if good is None:
            raise KeyError(f"Finished good not found: {command.finished_good_id}")
        task_item = task.item.upper().replace(" ", "")
        good_item = good.item_index.upper().replace(" ", "")
        if task.project != good.project or not (
            task_item == good_item or task_item.endswith(f".{good_item}") or task_item.endswith(f"-{good_item}")
        ):
            raise ProductionValidationError("Коробка не соответствует позиции готовой продукции")
        quantity = box.available
        updated_good = good.accept(quantity, command.now.date(), good.grn or uow.goods.reserve_grn(command.now.year))
        updated_box = box.handoff()
        # Ход журнала ГП «в производстве → принято складом» и акт приёмки со своим ГРН (владелец 19.10.2026) —
        # вместе с коробкой, в той же транзакции; сдача коробки — отдельная приёмка КБ.
        act = uow.goods.record_act(updated_good, uow.goods.add(updated_good, MoveContext(
            actor=command.actor.name, actor_id=command.actor.id, occurred_at=command.now,
            reason=f"коробка {box.number or box.id}",
        )))
        grn = act.grn
        receipt = FinishedGoodsReceipt(
            id=uuid4(), source_box_id=box.id, finished_good_id=good.id,
            grn=grn, quantity=quantity, received_at=command.now,
        )
        uow.boxes.save(updated_box)
        uow.finished_goods_receipts.add(receipt)
        uow.serials.mark_handed(box.id, grn)
        _audit(box.id, "production.box_handed_to_finished_goods", command.actor, command.now, {
            "finished_good_id": str(good.id), "grn": grn, "quantity": str(quantity),
        }, uow)
        uow.commit()
        return updated_box, updated_good, receipt


def decide_defect(command: DecideDefectCommand, uow: ExecutionUnitOfWork) -> tuple[DefectRecord, DefectDecision]:
    with uow:
        defect = uow.defects.get(command.defect_id)
        if defect is None:
            raise KeyError(f"Production defect not found: {command.defect_id}")
        if defect.version != command.expected_version:
            raise ProductionValidationError("Запись брака изменилась после загрузки формы")
        act_reference = str(command.act_reference or "").strip()
        if not act_reference:
            raise ProductionValidationError("Для решения по браку укажите акт или основание")
        updated = defect.decide()
        decision = DefectDecision(
            id=uuid4(), defect_id=defect.id, disposition=command.disposition,
            decided_by=command.actor.name, decided_at=command.now,
            act_reference=act_reference,
        )
        uow.defects.save(updated)
        uow.defects.add_decision(decision)
        _audit(defect.id, "production.defect_decided", command.actor, command.now, {
            "number": defect.number, "disposition": command.disposition.value,
            "act_reference": act_reference,
        }, uow)
        uow.commit()
        return updated, decision
