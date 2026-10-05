"""HTTP contract for production resource planning."""

from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal, production_quality_principal, production_view_principal, production_view_work_principal, section_allowed, shipping_warehouse_principal

from .application import (
    Absence,
    AssignProductionCommand,
    AssignShareCommand,
    AutoScheduleCommand,
    ConcurrentProductionUpdate,
    LaunchProductionCommand,
    ReplaceProductionCalendarCommand,
    ReplaceOperatorSkillsCommand,
    SetProductionCancelledCommand,
    UnitOfWork,
    assign_production,
    assign_share,
    apply_auto_schedule,
    launch_production,
    preview_auto_schedule,
    replace_production_calendar,
    replace_operator_skills,
    set_production_cancelled,
    hand_over_production,
    HandOverProductionCommand,
)
from .domain import OperatorSkill, ProductionTask, ProductionValidationError, WorkShift
from .launchable import item_id, launch_operations, launched_quantity, operation_code, operation_codes, route_from_document, stage_name
from .plan import kit_stage_key
from .scheduling import ScheduleVariant
from .execution import (
    BoxCommand,
    CorrectBoxCommand,
    DecideDefectCommand,
    HandoffBoxCommand,
    ReturnBoxCommand,
    SessionCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    cancel_work,
    confirm_box,
    correct_box,
    decide_defect,
    handoff_box,
    material_operation,
    operation_quantity,
    pause_work,
    resume_work,
    return_box,
    start_work,
    submit_work,
)
from .traceability import BoxStatus, DefectDisposition, DefectRecord, MaterialInput, ProductionBox, ProductionOutput, WorkSession
from .quality import (
    DecideQcRejectionCommand,
    InspectBoxCommand,
    RegisterInstrumentCommand,
    decide_qc_rejection,
    inspect_box,
    register_instrument,
)


router = APIRouter(prefix="/api/production", tags=["production"])
NonnegativeDecimal = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]


class LaunchPayload(BaseModel):
    # prodLaunch_: a task is launched only from an issued techcard — the route is its snapshot, never typed by hand.
    techcard_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)
    fulfillment_request_id: UUID | None = None
    deadline: date | None = None


class AssignPayload(BaseModel):
    version: int = Field(ge=1)
    operator_ids: list[UUID] = Field(max_length=100)
    requested_start: datetime | None = None
    dry: bool = False


class SharePayload(BaseModel):
    # Экран мастера: доля операции монтажнику — штуки и момент, с которого ставить; remove — снять долю.
    version: int = Field(ge=1)
    operator_id: UUID
    quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    requested_start: datetime | None = None
    remove: bool = False


class HandOverPayload(BaseModel):
    version: int = Field(ge=1)
    site: str = Field(default="", max_length=32)


class CancelPayload(BaseModel):
    version: int = Field(ge=1)
    cancelled: bool


class AbsencePayload(BaseModel):
    operator_id: UUID
    from_date: date
    to_date: date
    kind: str = Field(min_length=1, max_length=32)


class ShiftPayload(BaseModel):
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start: time
    finish: time
    breaks: list[tuple[time, time]] = Field(default_factory=list, max_length=20)
    effective_hours: Decimal = Field(gt=0, le=24, max_digits=5, decimal_places=2)


class CalendarPayload(BaseModel):
    holidays: list[date] = Field(max_length=500)
    absences: list[AbsencePayload] = Field(max_length=2000)
    shift: ShiftPayload | None = None


class SkillPayload(BaseModel):
    operation_code: str = Field(min_length=1, max_length=255)
    allowed: bool
    stars: int = Field(ge=0, le=5)
    coefficient: Decimal = Field(gt=0, le=100, max_digits=18, decimal_places=6)


class ReplaceSkillsPayload(BaseModel):
    skills: list[SkillPayload] = Field(max_length=5000)


class AutoSchedulePayload(BaseModel):
    version: int = Field(ge=1)
    replan: bool = False
    max_crew: int = Field(default=1, ge=1, le=3)  # prodAutoPlanModal_: бригада до 3


class ApplySchedulePayload(AutoSchedulePayload):
    transfer_batch: int = Field(gt=0, le=1_000_000)


class StartWorkPayload(BaseModel):
    # prodWorkStart_: номера входных коробок, как их ввёл оператор (через запятую или с новой строки).
    inputs: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=100)
    materials: list["MaterialInputPayload"] = Field(default_factory=list, max_length=500)


class MaterialInputPayload(BaseModel):
    fulfillment_line_id: UUID
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class SubmitWorkPayload(BaseModel):
    good_quantity: NonnegativeDecimal = Decimal("0")
    defect_quantity: NonnegativeDecimal = Decimal("0")
    defect_character: str = Field(default="", max_length=255)
    defect_description: str = Field(default="", max_length=5000)


class BoxVersionPayload(BaseModel):
    version: int = Field(ge=1)


class CorrectBoxPayload(BoxVersionPayload):
    output_id: UUID
    good_quantity: NonnegativeDecimal = Decimal("0")
    defect_quantity: NonnegativeDecimal = Decimal("0")
    defect_character: str = Field(default="", max_length=255)
    defect_description: str = Field(default="", max_length=5000)


class ReturnBoxPayload(BoxVersionPayload):
    reason: str = Field(min_length=1, max_length=2000)


class HandoffBoxPayload(BoxVersionPayload):
    finished_good_id: UUID


class DecideDefectPayload(BoxVersionPayload):
    disposition: DefectDisposition
    act_reference: str = Field(min_length=1, max_length=2000)


class RegisterInstrumentPayload(BaseModel):
    inventory_number: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    instrument_type: str = Field(min_length=1, max_length=255)
    verification_number: str = Field(min_length=1, max_length=255)
    verified_at: datetime
    verification_due: datetime


class InspectBoxPayload(BoxVersionPayload):
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    defect_character: str = Field(default="", max_length=255)
    defect_description: str = Field(default="", max_length=5000)
    instrument_ids: list[UUID] = Field(default_factory=list, max_length=100)


class DecideQcRejectionPayload(BaseModel):
    disposition: DefectDisposition
    act_reference: str = Field(min_length=1, max_length=2000)


def production_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "production_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def techcards_uow_factory(request: Request) -> Callable[[], TechcardsUnitOfWork] | None:
    factory = getattr(request.app.state, "techcards_uow_factory", None)
    return factory if callable(factory) else None


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _op_state(processed: Decimal, quantity: Decimal) -> str:
    # prodOpState_: the operation status is not stored — it is computed from output against the task quantity
    # (сетевой график: тираж × штук полуфабриката операции на одно изделие).
    if not processed:
        return "не начата"
    return "выполнена" if processed >= quantity else "в работе"


def _facts(uow, tasks) -> dict:
    """prodWorkFacts_ + prodWip_: per operation — good/scrap/processed and pieces in confirmed or waiting boxes."""
    task_ids = {task.id for task in tasks}
    facts: dict[UUID, dict] = {}
    for output in uow.outputs.active_for_tasks(tuple(task_ids)):
        fact = facts.setdefault(output.operation_id, {"good": Decimal(0), "defect": Decimal(0), "ok": Decimal(0), "closed": Decimal(0)})
        fact["good"] += output.good_quantity
        fact["defect"] += output.defect_quantity
    for box in uow.boxes.list(limit=100_000):
        if box.task_id not in task_ids or box.available <= 0:
            continue
        fact = facts.setdefault(box.operation_id, {"good": Decimal(0), "defect": Decimal(0), "ok": Decimal(0), "closed": Decimal(0)})
        if box.status is BoxStatus.CONFIRMED:
            fact["ok"] += box.available
        elif box.status in {BoxStatus.OPEN, BoxStatus.OPERATOR_CLOSED, BoxStatus.RETURNED}:
            # Решено агентом: у SUPPLYMAP нет «Открытой» коробки, её штуки тоже ждут подтверждения мастера.
            fact["closed"] += box.available
    return facts


def _fact_result(fact: dict | None, quantity: Decimal) -> dict:
    fact = fact or {"good": Decimal(0), "defect": Decimal(0), "ok": Decimal(0), "closed": Decimal(0)}
    processed = fact["good"] + fact["defect"]
    return {
        "fact": {"good": _number(fact["good"]), "defect": _number(fact["defect"]), "processed": _number(processed)},
        "state": _op_state(processed, quantity),
        "wip": {"ok": _number(fact["ok"]), "closed": _number(fact["closed"])},
    }


def _result(task: ProductionTask, facts: dict | None = None) -> dict:
    starts = [operation.planned_start for operation in task.operations if operation.planned_start]
    finishes = [operation.planned_finish for operation in task.operations if operation.planned_finish]
    positions = {operation.id: operation.position for operation in task.operations}
    stages = {operation.id: operation.stage for operation in task.operations}
    required = {operation.id: operation_quantity(task, operation) for operation in task.operations}
    final_id = task.final_operation.id if task.operations else None
    return {
        "id": str(task.id), "number": task.number, "version": task.version,
        "created_at": task.created_at.isoformat(), "project": task.project,
        "item": task.item, "name": task.name, "quantity": task.quantity,
        "fulfillment_request_id": str(task.fulfillment_request_id) if task.fulfillment_request_id else None,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "status": task.status.value, "launched_by": task.launched_by, "handed_to": task.handed_to,
        "planned_start": min(starts).isoformat() if starts else None,
        "planned_finish": max(finishes).isoformat() if finishes else None,
        "assigned": sum(bool(operation.operator_ids) for operation in task.operations),
        "operations": [{
            "id": str(operation.id), "position": operation.position,
            "code": operation_code(operation.code), "stage": stage_name(operation.stage),
            "description": stage_name(operation.description),
            "seconds_per_unit": _number(operation.seconds_per_unit),
            "setup_seconds": _number(operation.setup_seconds),
            "norm_seconds": operation.norm_seconds,
            "operator_ids": [str(value) for value in operation.operator_ids],
            "planned_start": operation.planned_start.isoformat() if operation.planned_start else None,
            "planned_finish": operation.planned_finish.isoformat() if operation.planned_finish else None,
            "assigned_by": operation.assigned_by,
            # Сетевой график (ARCH-TRACK-2): блок схемы, этапы-входы (штук на одну, передаточная партия ребра;
            # null — партия задания), финальная ли операция (её выход идёт в ОТК и на склад ГП) и сколько штук
            # она сдаёт по заданию (тираж × штук её полуфабриката на изделие; в цепочке — тираж).
            "stage_key": operation.stage_key,
            "required_quantity": _number(required[operation.id]),
            "inputs": [{
                "operation_id": str(item.input_operation_id), "position": positions.get(item.input_operation_id),
                "stage": stage_name(stages.get(item.input_operation_id, "")),
                "per_unit": _number(item.per_unit),
                "transfer_batch": _number(item.transfer_batch) if item.transfer_batch is not None else None,
            } for item in operation.inputs],
            "final": operation.id == final_id,
            # Доли по людям: штуки (None — вся операция бригадой), своё окно, факт «взял»/«закончил».
            "shares": [{
                "operator_id": str(share.operator_id), "quantity": share.quantity,
                "planned_start": share.planned_start.isoformat() if share.planned_start else None,
                "planned_finish": share.planned_finish.isoformat() if share.planned_finish else None,
                "started_at": share.started_at.isoformat() if share.started_at else None,
                "finished_at": share.finished_at.isoformat() if share.finished_at else None,
            } for share in operation.shares],
            **(_fact_result(facts.get(operation.id), required[operation.id]) if facts is not None else {}),
        } for operation in task.operations],
    }


def _shift_result(shift: WorkShift) -> dict:
    return {
        "weekdays": list(shift.weekdays),
        "start": shift.start.isoformat(timespec="minutes"),
        "finish": shift.finish.isoformat(timespec="minutes"),
        "breaks": [
            [start.isoformat(timespec="minutes"), finish.isoformat(timespec="minutes")]
            for start, finish in shift.breaks
        ],
        "effective_hours": str(Decimal(shift.effective_seconds) / Decimal(3600)),
    }


def _variant_result(variant: ScheduleVariant) -> dict:
    return {
        "transfer_batch": variant.transfer_batch,
        "first_ready": variant.first_ready.isoformat(),
        "start": variant.start.isoformat(),
        "finish": variant.finish.isoformat(),
        "idle_seconds": variant.idle_seconds,
        "warnings": list(variant.warnings),
        "operations": [{
            "operation_id": str(item.operation_id), "position": item.position,
            "operator_ids": [str(value) for value in item.operator_ids],
            "start": item.start.isoformat(), "finish": item.finish.isoformat(),
            "automatic": item.automatic,
        } for item in variant.operations],
    }


def _run(operation) -> dict:
    try:
        task = operation()
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentProductionUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error
    return {"task": _result(task)}


def _box_result(box: ProductionBox) -> dict:
    return {
        "id": str(box.id), "number": box.number, "version": box.version,
        "task_id": str(box.task_id), "operation_id": str(box.operation_id),
        "item": box.item, "operation_position": box.operation_position,
        "operator_id": str(box.operator_id), "quantity": _number(box.quantity),
        "consumed": _number(box.consumed), "reserved": _number(box.reserved),
        "rejected": _number(box.rejected),
        "available": _number(box.available), "status": box.status.value,
        "created_at": box.created_at.isoformat(),
        "closed_at": box.closed_at.isoformat() if box.closed_at else None,
        "closed_by": box.closed_by, "confirmed_at": box.confirmed_at.isoformat() if box.confirmed_at else None,
        "confirmed_by": box.confirmed_by, "return_reason": box.return_reason,
    }


def _session_result(session: WorkSession) -> dict:
    return {
        "id": str(session.id), "task_id": str(session.task_id),
        "operation_id": str(session.operation_id), "operator_id": str(session.operator_id),
        "started_at": session.started_at.isoformat(), "status": session.status.value,
        "pause_started_at": session.pause_started_at.isoformat() if session.pause_started_at else None,
        "pause_seconds": session.pause_seconds,
    }


def _defect_result(defect: DefectRecord) -> dict:
    return {
        "id": str(defect.id), "number": defect.number, "version": defect.version,
        "created_at": defect.created_at.isoformat(), "output_id": str(defect.output_id),
        "source_box_id": str(defect.source_box_id), "task_id": str(defect.task_id),
        "operation_id": str(defect.operation_id), "operator_id": str(defect.operator_id),
        "quantity": _number(defect.quantity), "character": defect.character,
        "description": defect.description, "status": defect.status.value,
        "replaced_by_output_id": str(defect.replaced_by_output_id) if defect.replaced_by_output_id else None,
    }


def _output_result(output: ProductionOutput) -> dict:
    return {
        "id": str(output.id), "good_quantity": _number(output.good_quantity),
        "defect_quantity": _number(output.defect_quantity),
        "finished_at": output.finished_at.isoformat(),
        "correction_of_id": str(output.correction_of_id) if output.correction_of_id else None,
    }


def _execution(operation):
    try:
        return operation()
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error


@router.get("/tasks")
def tasks(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        if after is not None and uow.production.get(after) is None:
            raise HTTPException(status_code=404, detail="Курсор задания не найден")
        rows = list(uow.production.list(limit + 1, after))
        page = rows[:limit]
        facts = _facts(uow, page)
    return {"tasks": [_result(task, facts) for task in page], "next_cursor": str(page[-1].id) if len(rows) > limit else None}


@router.get("/traceability")
def traceability(
    q: Annotated[str, Query(min_length=1, max_length=128)],
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        nodes, edges = uow.genealogy.search(q)
    return {
        "nodes": [{
            "key": item.key, "kind": item.kind,
            "label": item.label, "details": item.details,
        } for item in nodes],
        "edges": [{
            "source": item.source, "target": item.target,
            "quantity": _number(item.quantity), "relation": item.relation,
        } for item in edges],
    }


def _viewed_operator(uow: UnitOfWork, principal: Principal, operator_id: UUID | None) -> UUID:
    """Чьи задания показываем. Без operator_id — свои. Чужие (вид сотрудника, только чтение) — тому, кто видит
    «Ресурсное планирование»; мастер площадки — только монтажников своей площадки, полный доступ — любых."""
    if operator_id is None or operator_id == principal.actor.id:
        return principal.actor.id
    if not section_allowed(principal, ("production",), "view"):
        raise HTTPException(status_code=403, detail="Смотреть задания другого сотрудника может только планировщик.")
    target = uow.operators.get(operator_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    viewer = uow.operators.get(principal.actor.id)
    if not principal.full_access and viewer is not None and viewer.site and target.site != viewer.site:
        raise HTTPException(status_code=403, detail="Задания сотрудника другой площадки недоступны.")
    return operator_id


@router.get("/my")
def my_operations(
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    operator_id: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        viewed = _viewed_operator(uow, principal, operator_id)
        rows = [task for task in uow.production.list(limit=10_000) if task.status.value != "Отменено"]
        mine = [task for task in rows if any(viewed in operation.operator_ids for operation in task.operations)]
        facts = _facts(uow, mine)
        names = {item.id: item.name for item in uow.operators.list()}
    operations = []
    for task in mine:
        result = _result(task, facts)
        for index, operation in enumerate(task.operations):
            if viewed in operation.operator_ids:
                # prodMyOps_: напарники по операции и первый ли это этап (вход без коробок). Сетевой график:
                # без коробок начинается любая операция без входов (начало ветки), в цепочке — только этап 1.
                share = operation.share_of(viewed)
                operations.append({**result, "operations": [{
                    **result["operations"][index],
                    # Своя доля монтажника: у раздробленной операции своё окно и свои штуки.
                    "my_share": next(item for item in result["operations"][index]["shares"] if item["operator_id"] == str(share.operator_id)) if share else None,
                    "first": not operation.inputs,
                    "mates": [names.get(item, str(item)) for item in operation.operator_ids if item != viewed],
                }]})
    operations.sort(key=lambda item: (
        (item["operations"][0]["my_share"] or {}).get("planned_start") or item["operations"][0]["planned_start"] or "9999",
        item["number"], item["operations"][0]["position"],
    ))
    return {"tasks": operations}


@router.get("/tasks/{task_id}/operations/{operation_id}/inputs")
def available_inputs(
    task_id: UUID,
    operation_id: UUID,
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        task = uow.production.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        try:
            operation = task.operation(operation_id)
        except ProductionValidationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if principal.actor.id not in operation.operator_ids:
            raise HTTPException(status_code=403, detail="Операция не назначена сотруднику")
        # Сетевой график: коробки всех этапов-входов операции (у сборки — каждой ветки), не только предыдущего номера.
        sources = {item.input_operation_id for item in operation.inputs}
        rows = [box for box in uow.boxes.list(task_id=task_id) if (
            box.item == task.item
            and box.operation_id in sources
            and box.status is BoxStatus.CONFIRMED
            and box.available > 0
        )]
        materials = []
        if operation.id == material_operation(task).id and task.fulfillment_request_id is not None:
            materials = [source for source in uow.materials.list_sources(task.fulfillment_request_id) if source.available > 0]
    return {
        "boxes": [_box_result(box) for box in rows],
        "materials": [{
            "fulfillment_line_id": str(item.fulfillment_line_id),
            "article": item.article, "name": item.name, "unit": item.unit,
            "grn": item.grn, "package": item.package,
            "issued": _number(item.issued), "consumed": _number(item.consumed),
            "reserved": _number(item.reserved), "available": _number(item.available),
        } for item in materials],
    }


@router.get("/my/execution")
def my_execution(
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    operator_id: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        viewed = _viewed_operator(uow, principal, operator_id)
        current = uow.work.current(viewed)
        boxes = uow.boxes.list(operator_id=viewed)
    return {
        "session": _session_result(current) if current else None,
        "boxes": [_box_result(box) for box in boxes],
    }


@router.get("/boxes")
def boxes(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    task_id: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        rows = uow.boxes.list(task_id=task_id)
        result = [{**_box_result(box), "qc_inspected": uow.qc.by_box(box.id) is not None} for box in rows]
    return {"boxes": result}


@router.get("/finished-goods-ready")
def finished_goods_ready(
    _principal: Annotated[Principal, Depends(shipping_warehouse_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        tasks = {task.id: task for task in uow.production.list(limit=10_000)}
        rows = []
        for box in uow.boxes.list(limit=10_000):
            task = tasks.get(box.task_id)
            if task is None or box.status is not BoxStatus.CONFIRMED:
                continue
            if box.operation_id != task.final_operation.id:
                continue
            if uow.qc.by_box(box.id) is None:
                continue
            rows.append({**_box_result(box), "project": task.project, "task_item": task.item})
    return {"boxes": rows}


@router.post("/tasks/{task_id}/operations/{operation_id}/work/start")
def start_execution(
    task_id: UUID,
    operation_id: UUID,
    payload: StartWorkPayload,
    principal: Annotated[Principal, Depends(production_view_work_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    session = _execution(lambda: start_work(StartWorkCommand(
        task_id=task_id, operation_id=operation_id,
        box_numbers=tuple(payload.inputs),
        actor=principal.actor, now=now,
        materials=tuple(MaterialInput(item.fulfillment_line_id, item.quantity) for item in payload.materials),
    ), uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/pause")
def pause_execution(session_id: UUID, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    session = _execution(lambda: pause_work(SessionCommand(session_id, principal.actor, now), uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/resume")
def resume_execution(session_id: UUID, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    session = _execution(lambda: resume_work(SessionCommand(session_id, principal.actor, now), uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/cancel")
def cancel_execution(session_id: UUID, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    session = _execution(lambda: cancel_work(SessionCommand(session_id, principal.actor, now), uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/submit")
def submit_execution(session_id: UUID, payload: SubmitWorkPayload, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    session, box, output, defect = _execution(lambda: submit_work(SubmitWorkCommand(
        session_id=session_id, actor=principal.actor, now=now,
        good_quantity=payload.good_quantity, defect_quantity=payload.defect_quantity,
        defect_character=payload.defect_character,
        defect_description=payload.defect_description,
    ), uow_factory()))
    return {
        "session": _session_result(session), "box": _box_result(box),
        "output_id": str(output.id), "defect": _defect_result(defect) if defect else None,
    }


@router.get("/boxes/{box_id}/outputs")
def box_outputs(
    box_id: UUID,
    _principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        rows = uow.outputs.active_for_box(box_id)
    return {"outputs": [_output_result(item) for item in rows]}


@router.post("/boxes/{box_id}/correct")
def correct_execution_box(box_id: UUID, payload: CorrectBoxPayload, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    box, output, defect = _execution(lambda: correct_box(CorrectBoxCommand(
        box_id=box_id, expected_version=payload.version, actor=principal.actor, now=now,
        output_id=payload.output_id, good_quantity=payload.good_quantity,
        defect_quantity=payload.defect_quantity,
        defect_character=payload.defect_character,
        defect_description=payload.defect_description,
    ), uow_factory()))
    return {
        "box": _box_result(box), "output": _output_result(output),
        "defect": _defect_result(defect) if defect else None,
    }


@router.post("/boxes/{box_id}/confirm")
def confirm_execution_box(box_id: UUID, payload: BoxVersionPayload, principal: Annotated[Principal, Depends(production_edit_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    box = _execution(lambda: confirm_box(BoxCommand(box_id, payload.version, principal.actor, now), uow_factory()))
    return {"box": _box_result(box)}


@router.post("/boxes/{box_id}/return")
def return_execution_box(box_id: UUID, payload: ReturnBoxPayload, principal: Annotated[Principal, Depends(production_edit_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    box = _execution(lambda: return_box(ReturnBoxCommand(
        box_id=box_id, expected_version=payload.version, actor=principal.actor,
        now=now, reason=payload.reason,
    ), uow_factory()))
    return {"box": _box_result(box)}


@router.post("/boxes/{box_id}/handoff")
def handoff_execution_box(box_id: UUID, payload: HandoffBoxPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
    box, good, receipt = _execution(lambda: handoff_box(HandoffBoxCommand(
        box_id=box_id, expected_version=payload.version, actor=principal.actor,
        now=now, finished_good_id=payload.finished_good_id,
    ), uow_factory()))
    return {
        "box": _box_result(box),
        "finished_good": {
            "id": str(good.id), "version": good.version, "grn": good.grn,
            "accepted": _number(good.accepted), "remaining": _number(good.remaining),
        },
        "receipt_id": str(receipt.id),
    }


@router.get("/defects")
def defects(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        rows = uow.defects.list()
    return {"defects": [_defect_result(item) for item in rows]}


@router.post("/defects/{defect_id}/decide")
def decide_execution_defect(
    defect_id: UUID,
    payload: DecideDefectPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    defect, decision = _execution(lambda: decide_defect(DecideDefectCommand(
        defect_id=defect_id, expected_version=payload.version,
        disposition=payload.disposition, act_reference=payload.act_reference,
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"defect": _defect_result(defect), "decision_id": str(decision.id)}


@router.get("/instruments")
def instruments(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        rows = uow.instruments.list()
    return {"instruments": [{
        "id": str(item.id), "version": item.version,
        "inventory_number": item.inventory_number, "name": item.name,
        "instrument_type": item.instrument_type,
        "verification_number": item.verification_number,
        "verified_at": item.verified_at.isoformat(),
        "verification_due": item.verification_due.isoformat(),
        "status": item.status.value,
    } for item in rows]}


@router.post("/instruments")
def create_instrument(
    payload: RegisterInstrumentPayload,
    principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    item = _execution(lambda: register_instrument(RegisterInstrumentCommand(
        **payload.model_dump(), actor=principal.actor, now=now,
    ), uow_factory()))
    return {"instrument": {
        "id": str(item.id), "version": item.version,
        "inventory_number": item.inventory_number, "name": item.name,
        "instrument_type": item.instrument_type,
        "verification_number": item.verification_number,
        "verified_at": item.verified_at.isoformat(),
        "verification_due": item.verification_due.isoformat(),
        "status": item.status.value,
    }}


@router.post("/boxes/{box_id}/qc")
def inspect_execution_box(
    box_id: UUID,
    payload: InspectBoxPayload,
    principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    box, inspection, serials = _execution(lambda: inspect_box(InspectBoxCommand(
        box_id=box_id, expected_version=payload.version,
        accepted=payload.accepted, rejected=payload.rejected,
        defect_character=payload.defect_character,
        defect_description=payload.defect_description,
        instrument_ids=tuple(payload.instrument_ids), actor=principal.actor, now=now,
    ), uow_factory()))
    return {
        "box": _box_result(box), "inspection_id": str(inspection.id),
        "defect_number": inspection.defect_number,
        "serials": [{"serial_number": item.serial_number, "status": item.status.value} for item in serials],
    }


@router.get("/qc/rejections")
def qc_rejections(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        rows = []
        for inspection in uow.qc.list_rejections():
            box = uow.boxes.get(inspection.box_id)
            decision = uow.qc.decision_for(inspection.id)
            rows.append({
                "id": str(inspection.id), "box_id": str(inspection.box_id),
                "box_number": box.number if box else None,
                "item": box.item if box else "",
                "inspected_at": inspection.inspected_at.isoformat(),
                "rejected": inspection.rejected,
                "defect_number": inspection.defect_number,
                "defect_character": inspection.defect_character,
                "defect_description": inspection.defect_description,
                "status": "Решение принято" if decision else "Ожидает решения",
                "decision": ({
                    "id": str(decision.id), "disposition": decision.disposition.value,
                    "decided_by": decision.decided_by,
                    "decided_at": decision.decided_at.isoformat(),
                    "act_reference": decision.act_reference,
                } if decision else None),
            })
    return {"rejections": rows}


@router.post("/qc/rejections/{inspection_id}/decide")
def decide_qc_rejection_route(
    inspection_id: UUID,
    payload: DecideQcRejectionPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    decision = _execution(lambda: decide_qc_rejection(DecideQcRejectionCommand(
        inspection_id=inspection_id, disposition=payload.disposition,
        act_reference=payload.act_reference, actor=principal.actor, now=now,
    ), uow_factory()))
    return {
        "decision": {
            "id": str(decision.id), "inspection_id": str(decision.inspection_id),
            "disposition": decision.disposition.value, "decided_by": decision.decided_by,
            "decided_at": decision.decided_at.isoformat(),
            "act_reference": decision.act_reference,
        },
    }


@router.get("/tasks/{task_id}/serials")
def task_serials(
    task_id: UUID,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        task = uow.production.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        rows = uow.serials.list_for_task(task.id)
    return {"serials": [{
        "id": str(item.id), "serial_number": item.serial_number,
        "position": item.position, "status": item.status.value,
        "current_box_id": str(item.current_box_id) if item.current_box_id else None,
        "finished_goods_grn": item.finished_goods_grn,
    } for item in rows]}


@router.get("/launchable")
def launchable(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> dict:
    """prodLaunchable_: kit positions with an issued techcard, with the run and what is already launched."""
    if techcards_factory is None:
        return {"items": []}
    with techcards_factory() as techcards:
        cards = [card for card in techcards.techcards.list() if card.status is TechcardStatus.ISSUED]
        kitsets = tuple(techcards.kitsets.list()) if hasattr(techcards, "kitsets") else ()
    # Тираж и заказчик — из карточки набора (prodLaunchable_ читает «Наборы»); нет набора — партия ЭТК.
    kit_rows = {
        kit_stage_key(kitset.project, assembly.index): (kitset.customer, assembly.run)
        for kitset in kitsets for assembly in kitset.assemblies
    }
    with uow_factory() as uow:
        launched = launched_quantity(uow.production.list(limit=10_000))
    items = []
    for card in cards:
        route = route_from_document(card.document)
        if not route:
            continue
        item = item_id(card.project, card.item_index)
        kit = (card.document or {}).get("kit") if isinstance(card.document, dict) else None
        customer, run = kit_rows.get(kit_stage_key(card.project, card.item_index), ("", None))
        items.append({
            "techcard_id": str(card.id), "project": card.project, "index": card.item_index,
            "item": item, "name": card.product_name, "customer": customer,
            "run": _number(run) if run else str((kit or {}).get("part_quantity") or ""),
            "deadline": card.deadline.isoformat() if card.deadline else None,
            "launched": launched.get(kit_stage_key(card.project, card.item_index), 0), "operations": len(route),
        })
    items.sort(key=lambda value: (value["project"], value["index"]))
    return {"items": items}


@router.get("/operators")
def operators(
    principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        rows = uow.operators.list()
        viewer = uow.operators.get(principal.actor.id)
    return {"viewer_site": viewer.site if viewer is not None else "", "operators": [
        {"id": str(item.id), "name": item.name, "site": item.site, "department": item.department}
        for item in rows if item.installer
    ]}


@router.post("/tasks")
def launch(
    payload: LaunchPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> dict:
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База техкарт не настроена")
    with techcards_factory() as techcards:
        card = techcards.techcards.get(payload.techcard_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Техкарта не найдена")
    item = item_id(card.project, card.item_index)
    route = route_from_document(card.document) if card.status is TechcardStatus.ISSUED else ()
    if not route:
        raise HTTPException(status_code=422, detail=f"Техкарта «{item}» не выдана — запускать нечего")
    try:
        operations = launch_operations(route, payload.quantity)
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # Решено агентом: ERP хранит в задании изделие индексом КБ (как план и приёмка ГП), а не «проект+индекс».
    return _run(lambda: launch_production(LaunchProductionCommand(
        idempotency_key=idempotency_key, project=card.project, item=card.item_index,
        name=card.product_name, quantity=payload.quantity,
        fulfillment_request_id=payload.fulfillment_request_id, deadline=payload.deadline,
        operations=operations, actor=principal.actor, now=now,
    ), uow_factory()))


@router.post("/tasks/{task_id}/operations/{operation_id}/assign")
def assign(
    task_id: UUID,
    operation_id: UUID,
    payload: AssignPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    result: dict = {}
    def execute():
        task, plan = assign_production(AssignProductionCommand(
            task_id=task_id, expected_version=payload.version, operation_id=operation_id,
            operator_ids=tuple(payload.operator_ids), requested_start=payload.requested_start,
            actor=principal.actor, now=now, dry=payload.dry,
        ), uow_factory())
        result["warnings"] = list(plan.warnings)
        result["plan"] = {
            "start": plan.start.isoformat() if plan.start else None,
            "finish": plan.finish.isoformat() if plan.finish else None,
            "warnings": list(plan.warnings),
        }
        return task
    response = _run(execute)
    response["warnings"] = result["warnings"]
    response["plan"] = result["plan"]
    response["dry"] = payload.dry
    return response


@router.post("/tasks/{task_id}/operations/{operation_id}/share")
def assign_operation_share(
    task_id: UUID,
    operation_id: UUID,
    payload: SharePayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    result: dict = {}
    def execute():
        task, plan, moved = assign_share(AssignShareCommand(
            task_id=task_id, expected_version=payload.version, operation_id=operation_id,
            operator_id=payload.operator_id, quantity=payload.quantity,
            requested_start=payload.requested_start, remove=payload.remove,
            actor=principal.actor, now=now,
        ), uow_factory())
        result["plan"] = {
            "start": plan.start.isoformat() if plan.start else None,
            "finish": plan.finish.isoformat() if plan.finish else None,
            "warnings": list(plan.warnings),
        }
        # Экран мастера показывает warnings строкой уведомления: что встало и что сдвинулось.
        result["warnings"] = [*plan.warnings, *moved[:5], *([f"…и ещё сдвинуто: {len(moved) - 5}"] if len(moved) > 5 else [])]
        return task
    response = _run(execute)
    response.update(result)
    return response


@router.post("/tasks/{task_id}/cancel")
def cancel(
    task_id: UUID,
    payload: CancelPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    return _run(lambda: set_production_cancelled(SetProductionCancelledCommand(
        task_id=task_id, expected_version=payload.version, cancelled=payload.cancelled,
        actor=principal.actor, now=now,
    ), uow_factory()))


@router.post("/tasks/{task_id}/handover")
def hand_over(
    task_id: UUID,
    payload: HandOverPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    return _run(lambda: hand_over_production(HandOverProductionCommand(
        task_id=task_id, expected_version=payload.version, site=payload.site,
        actor=principal.actor, now=now,
    ), uow_factory()))


@router.post("/tasks/{task_id}/schedule")
def auto_schedule(
    task_id: UUID,
    payload: AutoSchedulePayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    try:
        variants = preview_auto_schedule(AutoScheduleCommand(
            task_id=task_id, expected_version=payload.version,
            replan=payload.replan, max_crew=payload.max_crew,
            actor=principal.actor, now=now,
        ), uow_factory())
        return {"variants": [_variant_result(item) for item in variants]}
    except (ProductionValidationError, ConcurrentProductionUpdate) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/tasks/{task_id}/schedule/apply")
def apply_schedule(
    task_id: UUID,
    payload: ApplySchedulePayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    try:
        task, variant = apply_auto_schedule(AutoScheduleCommand(
            task_id=task_id, expected_version=payload.version,
            replan=payload.replan, max_crew=payload.max_crew,
            actor=principal.actor, now=now,
        ), payload.transfer_batch, uow_factory())
        return {"task": _result(task), "variant": _variant_result(variant)}
    except (ProductionValidationError, ConcurrentProductionUpdate) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/calendar")
def calendar(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        holidays = uow.calendar.holidays()
        absences = uow.calendar.absences()
        shift = uow.calendar.shift()
    return {
        "holidays": [day.isoformat() for day in sorted(holidays)],
        "shift": _shift_result(shift),
        "absences": [{
            "id": str(item.id), "operator_id": str(item.operator_id),
            "from_date": item.from_date.isoformat(), "to_date": item.to_date.isoformat(), "kind": item.kind,
        } for item in absences],
    }


@router.put("/calendar", status_code=204)
def replace_calendar(
    payload: CalendarPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> None:
    try:
        if payload.shift is None:
            with uow_factory() as uow:
                shift = uow.calendar.shift()
        else:
            shift = WorkShift(
                weekdays=tuple(payload.shift.weekdays),
                start=payload.shift.start, finish=payload.shift.finish,
                breaks=tuple(payload.shift.breaks),
                effective_seconds=int(payload.shift.effective_hours * 3600),
            )
        replace_production_calendar(ReplaceProductionCalendarCommand(
            holidays=frozenset(payload.holidays),
            absences=tuple(Absence(uuid4(), item.operator_id, item.from_date, item.to_date, item.kind) for item in payload.absences),
            actor=principal.actor, now=now,
            shift=shift,
        ), uow_factory())
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/skills")
def skills(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)] = None,
) -> dict:
    with uow_factory() as uow:
        rows = uow.skills.list()
    routes = []
    if techcards_factory is not None:
        with techcards_factory() as techcards:
            routes = [
                route_from_document(card.document) for card in techcards.techcards.list()
                if card.status is TechcardStatus.ISSUED
            ]
    codes = operation_codes(routes, [(item.operation_code, "") for item in rows])
    return {"operation_codes": codes, "skills": [{
        "operator_id": str(item.operator_id), "operation_code": item.operation_code,
        "allowed": item.allowed, "stars": item.stars,
        "coefficient": str(item.coefficient),
    } for item in rows]}


@router.put("/skills/{operator_id}", status_code=204)
def replace_skills(
    operator_id: UUID,
    payload: ReplaceSkillsPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> None:
    try:
        replace_operator_skills(ReplaceOperatorSkillsCommand(
            operator_id=operator_id,
            skills=tuple(OperatorSkill(
                operator_id=operator_id, operation_code=item.operation_code,
                allowed=item.allowed, stars=item.stars, coefficient=item.coefficient,
            ) for item in payload.skills),
            actor=principal.actor, now=now,
        ), uow_factory())
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
