"""Transactional production execution and box confirmation use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.shipping.domain import FinishedGood

from .application import ProductionRepository
from .domain import ProductionStatus, ProductionValidationError
from .traceability import (
    ConsumptionEdge,
    DefectDecision,
    DefectDisposition,
    DefectRecord,
    FinishedGoodsReceipt,
    MaterialConsumption,
    MaterialInput,
    MaterialSource,
    ProductionBox,
    ProductionOutput,
    WorkInput,
    WorkSession,
    WorkStatus,
    give_back_inputs,
    pick_inputs,
)


class BoxRepository(Protocol):
    def get(self, box_id: UUID) -> ProductionBox | None: ...
    def open_for(self, task_id: UUID, operation_id: UUID, operator_id: UUID) -> ProductionBox | None: ...
    def list(self, *, task_id: UUID | None = None, operator_id: UUID | None = None, limit: int = 500) -> Sequence[ProductionBox]: ...
    def reserve_number(self) -> str: ...
    def save(self, box: ProductionBox) -> None: ...


class WorkRepository(Protocol):
    def get(self, session_id: UUID) -> WorkSession | None: ...
    def current(self, operator_id: UUID) -> WorkSession | None: ...
    def inputs(self, session_id: UUID) -> Sequence[WorkInput]: ...
    def material_inputs(self, session_id: UUID) -> Sequence[MaterialInput]: ...
    def save(self, session: WorkSession) -> None: ...
    def add_inputs(self, session_id: UUID, inputs: tuple[WorkInput, ...]) -> None: ...
    def add_material_inputs(self, session_id: UUID, inputs: tuple[MaterialInput, ...]) -> None: ...


class OutputRepository(Protocol):
    def add(self, output: ProductionOutput) -> None: ...
    def get(self, output_id: UUID) -> ProductionOutput | None: ...
    def is_superseded(self, output_id: UUID) -> bool: ...
    def lineage(self, output_id: UUID) -> tuple[UUID, ...]: ...
    def active_for_box(self, box_id: UUID) -> Sequence[ProductionOutput]: ...
    def list_for_task(self, task_id: UUID) -> Sequence[ProductionOutput]: ...
    def total_for_operation(self, operation_id: UUID) -> Decimal: ...


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
    def add(self, good: FinishedGood) -> None: ...
    def reserve_grn(self, year: int) -> str: ...


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


class ExecutionUnitOfWork(Protocol):
    production: ProductionRepository
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
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class StartWorkCommand:
    task_id: UUID
    operation_id: UUID
    inputs: tuple[WorkInput, ...]
    actor: Actor
    now: datetime
    materials: tuple[MaterialInput, ...] = ()


@dataclass(frozen=True)
class SessionCommand:
    session_id: UUID
    actor: Actor
    now: datetime


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


@dataclass(frozen=True)
class DecideDefectCommand:
    defect_id: UUID
    expected_version: int
    disposition: DefectDisposition
    act_reference: str
    actor: Actor
    now: datetime


def _audit(aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict, uow: ExecutionUnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id, event_type=event_type,
        actor=actor, occurred_at=now, details=details,
    ))


def _task_operation(task_id: UUID, operation_id: UUID, actor: Actor, uow: ExecutionUnitOfWork):
    task = uow.production.get(task_id)
    if task is None:
        raise KeyError(f"Production task not found: {task_id}")
    if task.status is not ProductionStatus.RUNNING:
        raise ProductionValidationError("Отменённое задание выполнять нельзя")
    operation = task.operation(operation_id)
    if actor.id not in operation.operator_ids:
        raise ProductionValidationError("Операция не назначена этому оператору")
    return task, operation


def start_work(command: StartWorkCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    with uow:
        if uow.work.current(command.actor.id) is not None:
            raise ProductionValidationError("Сначала завершите или отмените текущую работу")
        task, operation = _task_operation(command.task_id, command.operation_id, command.actor, uow)
        if operation.position == 1:
            # SUPPLYMAP начинает первый этап без выданного комплекта; выданные партии — по желанию, для родословной.
            if command.inputs:
                raise ProductionValidationError("Первая операция не берёт коробки предыдущего этапа")
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
        elif command.materials or not command.inputs:
            raise ProductionValidationError("Укажите входные коробки предыдущего этапа")
        if len({item.box_id for item in command.inputs}) != len(command.inputs):
            raise ProductionValidationError("Одна входная коробка указана дважды")
        reserved: list[tuple[ProductionBox, WorkInput]] = []
        for item in command.inputs:
            box = uow.boxes.get(item.box_id)
            if box is None:
                raise ProductionValidationError("Входная коробка не найдена")
            updated = box.reserve(
                item.quantity,
                item=task.item,
                previous_position=operation.position - 1,
            )
            reserved.append((updated, item))
        session = WorkSession.start(task.id, operation.id, command.actor.id, command.now)
        for box, _item in reserved:
            uow.boxes.save(box)
        uow.work.save(session)
        uow.work.add_inputs(session.id, command.inputs)
        uow.work.add_material_inputs(session.id, command.materials)
        _audit(session.id, "production.work_started", command.actor, command.now, {
            "task_id": str(task.id), "operation_id": str(operation.id),
            "inputs": {str(item.box_id): str(item.quantity) for item in command.inputs},
            "materials": {str(item.fulfillment_line_id): str(item.quantity) for item in command.materials},
        }, uow)
        uow.commit()
        return session


def _session(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    session = uow.work.get(command.session_id)
    if session is None:
        raise KeyError(f"Work session not found: {command.session_id}")
    if session.operator_id != command.actor.id:
        raise ProductionValidationError("Работа принадлежит другому оператору")
    return session


def pause_work(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    with uow:
        session = _session(command, uow).pause(command.now)
        uow.work.save(session)
        _audit(session.id, "production.work_paused", command.actor, command.now, {}, uow)
        uow.commit()
        return session


def resume_work(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    with uow:
        session = _session(command, uow).resume(command.now)
        uow.work.save(session)
        _audit(session.id, "production.work_resumed", command.actor, command.now, {}, uow)
        uow.commit()
        return session


def cancel_work(command: SessionCommand, uow: ExecutionUnitOfWork) -> WorkSession:
    with uow:
        session = _session(command, uow)
        inputs = tuple(uow.work.inputs(session.id))
        for item in inputs:
            box = uow.boxes.get(item.box_id)
            if box is None:
                raise ProductionValidationError("Входная коробка исчезла")
            uow.boxes.save(box.release(item.quantity))
        cancelled = session.cancel()
        uow.work.save(cancelled)
        _audit(cancelled.id, "production.work_cancelled", command.actor, command.now, {
            "released_inputs": {str(item.box_id): str(item.quantity) for item in inputs},
        }, uow)
        uow.commit()
        return cancelled


def submit_work(command: SubmitWorkCommand, uow: ExecutionUnitOfWork) -> tuple[WorkSession, ProductionBox, ProductionOutput, DefectRecord | None]:
    with uow:
        session = _session(command, uow)
        task, operation = _task_operation(session.task_id, session.operation_id, command.actor, uow)
        good = Decimal(command.good_quantity)
        defect = Decimal(command.defect_quantity)
        if not good.is_finite() or not defect.is_finite() or good < 0 or defect < 0 or good + defect <= 0:
            raise ProductionValidationError("Сумма годных и брака должна быть положительной")
        inputs = tuple(uow.work.inputs(session.id))
        materials = tuple(uow.work.material_inputs(session.id))
        input_total = sum((item.quantity for item in inputs), Decimal("0"))
        if operation.position > 1 and good + defect != input_total:
            raise ProductionValidationError("Годные плюс брак должны совпадать с количеством входа")
        if operation.position == 1:
            produced = uow.outputs.total_for_operation(operation.id)
            if produced + good + defect > Decimal(task.quantity):
                raise ProductionValidationError("Выработка превышает количество производственного задания")
        output_box = uow.boxes.open_for(task.id, operation.id, command.actor.id)
        if output_box is None:
            output_box = ProductionBox.create(
                task_id=task.id, operation_id=operation.id, item=task.item,
                operation_position=operation.position, operator_id=command.actor.id,
                created_at=command.now,
            )
        if good > 0:
            output_box = output_box.add_output(good)
        completed = session.complete(command.now)
        output = ProductionOutput(
            id=uuid4(), session_id=session.id, task_id=task.id,
            operation_id=operation.id, operator_id=command.actor.id,
            started_at=session.started_at, finished_at=command.now,
            pause_seconds=completed.pause_seconds, good_quantity=good,
            defect_quantity=defect, output_box_id=output_box.id,
        )
        defect_record = None
        if defect > 0:
            defect_record = DefectRecord.create(
                number=uow.defects.reserve_number(), output_id=output.id,
                source_box_id=output_box.id, task_id=task.id,
                operation_id=operation.id, operator_id=command.actor.id,
                quantity=defect, character=command.defect_character,
                description=command.defect_description, created_at=command.now,
            )
        for item in inputs:
            source = uow.boxes.get(item.box_id)
            if source is None:
                raise ProductionValidationError("Входная коробка исчезла")
            uow.boxes.save(source.consume(item.quantity))
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
        }, uow)
        uow.commit()
        return completed, output_box, output, defect_record


def close_box(command: BoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    with uow:
        box = _box(command, uow)
        if box.operator_id != command.actor.id:
            raise ProductionValidationError("Закрыть коробку может только её оператор")
        number = box.number or uow.boxes.reserve_number()
        updated = box.close(number, command.actor.name, command.now)
        uow.boxes.save(updated)
        _audit(box.id, "production.box_closed", command.actor, command.now, {"number": number}, uow)
        uow.commit()
        return updated


def confirm_box(command: BoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    with uow:
        box = _box(command, uow)
        updated = box.confirm(command.actor.name, command.now)
        uow.boxes.save(updated)
        _audit(box.id, "production.box_confirmed", command.actor, command.now, {"number": box.number}, uow)
        uow.commit()
        return updated


def return_box(command: ReturnBoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    with uow:
        box = _box(command, uow)
        updated = box.return_to_operator(command.reason)
        uow.boxes.save(updated)
        _audit(box.id, "production.box_returned", command.actor, command.now, {"reason": command.reason}, uow)
        uow.commit()
        return updated


def reopen_box(command: BoxCommand, uow: ExecutionUnitOfWork) -> ProductionBox:
    with uow:
        box = _box(command, uow)
        updated = box.reopen(command.actor.id)
        uow.boxes.save(updated)
        _audit(box.id, "production.box_reopened", command.actor, command.now, {}, uow)
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
        corrected_box = box.correct(
            replaced_good=replaced.good_quantity, good=good,
            operator_id=command.actor.id, actor=command.actor.name, now=command.now,
        )
        task, operation = _task_operation(box.task_id, box.operation_id, command.actor, uow)
        replaced_total = replaced.good_quantity + replaced.defect_quantity
        done = uow.outputs.total_for_operation(operation.id) - replaced_total
        if done + good + defect > Decimal(task.quantity):
            raise ProductionValidationError("Выработка превышает количество производственного задания")
        output = ProductionOutput(
            id=uuid4(), session_id=None, task_id=task.id,
            operation_id=operation.id, operator_id=command.actor.id,
            started_at=command.now, finished_at=command.now, pause_seconds=0,
            good_quantity=good, defect_quantity=defect, output_box_id=box.id,
            correction_of_id=replaced.id,
        )
        # Added first so every row below that references it flushes after it.
        uow.outputs.add(output)
        lineage = uow.outputs.lineage(replaced.id)
        delta = good + defect - replaced_total
        moves: tuple[WorkInput, ...] = ()
        if operation.position > 1 and delta != 0:
            taken = tuple(uow.consumption.net_into(box.id, lineage))
            if delta > 0:
                sources = tuple(source for item in taken if (source := uow.boxes.get(item.box_id)) is not None)
                moves = pick_inputs(sources, delta)
            else:
                moves = give_back_inputs(taken, -delta)
            for item in moves:
                source = uow.boxes.get(item.box_id)
                if source is None:
                    raise ProductionValidationError("Входная коробка исчезла")
                if delta > 0:
                    moved = source.reserve(
                        item.quantity, item=task.item, previous_position=operation.position - 1,
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
                    operation_id=operation.id, operator_id=command.actor.id,
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
        }, uow)
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
        last_position = max(operation.position for operation in task.operations)
        if box.operation_position != last_position:
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
        grn = good.grn or uow.goods.reserve_grn(command.now.year)
        updated_good = good.accept(quantity, command.now.date(), grn)
        updated_box = box.handoff()
        receipt = FinishedGoodsReceipt(
            id=uuid4(), source_box_id=box.id, finished_good_id=good.id,
            grn=grn, quantity=quantity, received_at=command.now,
        )
        uow.goods.add(updated_good)
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
