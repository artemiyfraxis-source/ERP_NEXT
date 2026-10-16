"""HTTP contract for production resource planning."""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from app.modules.techcards.etk_read import IssuedLots, card_views, issued_card_for_item, saved_harness
from app.workflows.etk_lots import IssuedKitLots
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import MOSCOW, moscow_now
from app.shared.identity import Actor
from app.shared.stored_files import file_response
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
    CancelDeliveryCommand,
    FlowView,
    RecordDeliveryCommand,
    RefreshNormsCommand,
    SetTaskDoneCommand,
    UpdateFlowCommand,
    cancel_delivery,
    flow_view,
    record_delivery,
    refresh_norms,
    set_task_done,
    update_flow,
)
from .domain import (
    FLOW_MODE_NAMES, FlowMode, OperatorSkill, ProductionTask, ProductionValidationError, TaskDelivery, WorkShift, delivered_total,
    mark_serial_range,
)
from .flow import EARLY_LIMIT, EARLY_NOTICE
from .launchable import (
    item_id, launch_operations, launch_route, launched_quantity, operation_code, operation_codes, ready_cards,
    route_from_document, semifinished_steps, stage_name,
)
from .plan import kit_stage_key
from .scheduling import ScheduleVariant
from .execution import (
    CorrectBoxCommand,
    DecideDefectCommand,
    HandoffBoxCommand,
    ReturnBoxCommand,
    SessionCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    cancel_work,
    correct_box,
    decide_defect,
    handoff_box,
    input_stages,
    material_operation,
    operation_quantity,
    pause_work,
    resume_work,
    return_box,
    start_work,
    submit_work,
)
from .traceability import TAKEABLE_BOX, BoxStatus, DefectDisposition, DefectRecord, MaterialInput, ProductionBox, ProductionOutput, WorkSession
from .worklog import SUMMARY_KEYS, WorkTimeEntry, percent, time_fact
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


class FlowPayload(BaseModel):
    # План задания: режим передачи, передаточная партия (null — вся партия), «первые k раньше» (null — нет), команда.
    version: int = Field(ge=1)
    flow_mode: FlowMode
    transfer_batch: int | None = Field(default=None, ge=1, le=1_000_000)
    early_quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    team: list[UUID] = Field(default_factory=list, max_length=50)


class DeliveryPayload(BaseModel):
    # «Сдано N шт»; null — весь остаток одним разом.
    version: int = Field(ge=1)
    quantity: int | None = Field(default=None, ge=1, le=1_000_000)


class TaskDonePayload(BaseModel):
    version: int = Field(ge=1)
    done: bool


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


def etk_issued_lots(request: Request) -> IssuedLots | None:
    """Lots handed to the shop for the КБ, so the fitter's ETK names the ГРН bound after it was issued."""
    factory = getattr(request.app.state, "fulfillment_uow_factory", None)
    return IssuedKitLots(factory) if callable(factory) else None


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _op_state(processed: Decimal, quantity: Decimal) -> str:
    # prodOpState_: the operation status is not stored — it is computed from output against the task quantity
    # (сетевой график: тираж × штук полуфабриката операции на одно изделие).
    if not processed:
        return "не начата"
    return "выполнена" if processed >= quantity else "в работе"


def _facts(uow, tasks, boxes=None, outputs=None) -> dict:
    """prodWorkFacts_ + prodWip_: per operation — good/scrap/processed and pieces in usable or waiting boxes.

    «ok» — штуки, которые можно брать дальше: сданные коробки операций без ОТК и принятые ОТК коробки последней
    операции; «closed» — ждут: коробки последней операции до ОТК, возвращённые на исправление, открытые.
    boxes/outputs — уже прочитанные строки этих заданий (экран «Мои задания» читает только свои ПЗ)."""
    task_ids = {task.id for task in tasks}
    finals = set()
    for task in tasks:
        try:
            finals.add(task.final_operation.id)
        except ProductionValidationError:
            continue
    facts: dict[UUID, dict] = {}
    for output in (outputs if outputs is not None else uow.outputs.active_for_tasks(tuple(task_ids))):
        fact = facts.setdefault(output.operation_id, {"good": Decimal(0), "defect": Decimal(0), "ok": Decimal(0), "closed": Decimal(0)})
        fact["good"] += output.good_quantity
        fact["defect"] += output.defect_quantity
    for box in (boxes if boxes is not None else uow.boxes.list(limit=100_000)):
        if box.task_id not in task_ids or box.available <= 0:
            continue
        fact = facts.setdefault(box.operation_id, {"good": Decimal(0), "defect": Decimal(0), "ok": Decimal(0), "closed": Decimal(0)})
        if box.status is BoxStatus.CONFIRMED or (box.status is BoxStatus.OPERATOR_CLOSED and box.operation_id not in finals):
            fact["ok"] += box.available
        elif box.status in {BoxStatus.OPEN, BoxStatus.OPERATOR_CLOSED, BoxStatus.RETURNED}:
            # Решено агентом: у SUPPLYMAP нет «Открытой» коробки, её штуки тоже ждут.
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


def _result(task: ProductionTask, facts: dict | None = None, delivered: int | None = None) -> dict:
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
        # План задания (Цель 2): техкарта и ревизия норм, режим передачи, партия, «первые k раньше», команда;
        # delivered — «Сдано N шт» площадки (без отменённых), null — не считалось в этом ответе.
        "techcard_id": str(task.techcard_id) if task.techcard_id else None,
        "techcard_revision": task.techcard_revision,
        "flow_mode": task.flow_mode.value, "transfer_batch": task.transfer_batch,
        "early_quantity": task.early_quantity, "team": [str(value) for value in task.team],
        "delivered": delivered,
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
            # Задание по ПФ (13.10): операции ЭТК этого полуфабриката по порядку; пусто — операция задания и есть
            # одна операция ЭТК (номер ЭТК = position).
            "etk_ops": [{
                "position": item.position, "code": operation_code(item.code), "stage": stage_name(item.stage),
            } for item in operation.etk_ops],
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
        "id": str(box.id), "number": box.number, "code": box.code, "version": box.version,
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
    except ConcurrentProductionUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
        totals = uow.deliveries.totals(task.id for task in page) if hasattr(uow, "deliveries") else {}
    return {
        "tasks": [_result(task, facts, totals.get(task.id, 0)) for task in page],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
    }


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


def _may_act_for(principal: Principal, viewer, target) -> bool:
    """Работать за сотрудника (владелец, 12.10.2026: «мне нужен полный функционал от неё»): полный доступ — за любого,
    мастер (правка «Ресурсного планирования») — за монтажника своей площадки; без площадки у мастера — за любого."""
    if principal.full_access:
        return True
    if not section_allowed(principal, ("production",), "edit"):
        return False
    return viewer is None or not viewer.site or target.site == viewer.site


def _performer(uow_factory: Callable[[], UnitOfWork], principal: Principal, on_behalf_of: UUID | None) -> Actor | None:
    """За кого нажата кнопка «Заданий». Правку «Заданий» уже проверил гейт маршрута (production_view_work_principal);
    без on_behalf_of (или себя) — сам. За другого — сверх гейта право `_may_act_for`, проверенное здесь, а не на
    экране; возвращает исполнителя."""
    if on_behalf_of is None or on_behalf_of == principal.actor.id:
        return None
    with uow_factory() as uow:
        target = uow.operators.get(on_behalf_of)
        viewer = uow.operators.get(principal.actor.id)
    if target is None or not target.active:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    if not _may_act_for(principal, viewer, target):
        raise HTTPException(status_code=403, detail="Работать за сотрудника может мастер его площадки или администратор.")
    return Actor(id=target.id, name=target.name, department=target.department)


def _replay_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if len(key) > 128:
        raise HTTPException(status_code=422, detail="Ключ повтора длиннее 128 символов")
    return key or None


def _time_result(entry: WorkTimeEntry) -> dict:
    return {
        "id": str(entry.id), "session_id": str(entry.session_id), "sequence": entry.sequence,
        "event": entry.event.value, "occurred_at": entry.occurred_at.isoformat(),
        "task_id": str(entry.task_id), "operation_id": str(entry.operation_id),
        "operator_id": str(entry.operator_id), "operator_name": entry.operator_name,
        "actor_id": str(entry.actor_id), "actor_name": entry.actor_name, "on_behalf": entry.on_behalf,
        "project": entry.project, "item": entry.item, "task_number": entry.task_number,
        "operation_code": entry.operation_code, "stage": entry.stage, "position": entry.position,
        "seconds_per_unit": _number(entry.seconds_per_unit), "setup_seconds": _number(entry.setup_seconds),
        "planned_norm_seconds": entry.planned_norm_seconds,
        "good_quantity": _number(entry.good_quantity) if entry.good_quantity is not None else None,
        "defect_quantity": _number(entry.defect_quantity) if entry.defect_quantity is not None else None,
        "running_seconds": entry.running_seconds, "earned_norm_seconds": entry.earned_norm_seconds,
    }


def _period(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Дни периода по Москве включительно: [00:00 первого дня, 00:00 дня после последнего)."""
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="Конец периода раньше начала")
    if (date_to - date_from).days > 731:
        raise HTTPException(status_code=422, detail="Период не длиннее двух лет")
    return (
        datetime.combine(date_from, time(0), tzinfo=MOSCOW),
        datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=MOSCOW),
    )


_MY_TASK_LIMIT = 300
_MY_BOX_LIMIT = 2_000


def _readiness(task: ProductionTask, operation, boxes, state: str, progress: dict[str, dict] | None = None) -> dict:
    """Готовность входов операции для карточки монтажника: можно ли начинать и какие сданные коробки
    этого ПЗ можно взять. Коробки другого ПЗ сюда не попадают.

    progress — строки операций задания из `_result` по id (сдано и сколько сдать). Готово — `available`: сколько штук
    этой операции дают коробки входов (у сборки — по самому бедному входу). Ждёт — с какого этапа и сколько тот сдал
    из нужного (владелец, 16.10: «ждёт: … не сдан» должно говорить, сколько не хватает)."""
    if state == "выполнена":
        return {"state": "done", "text": "выполнена", "boxes": []}
    if not operation.inputs:
        return {"state": "ready", "text": "можно начинать", "boxes": []}
    by_id = {item.id: item for item in task.operations}
    ready: list[dict] = []
    missing = []
    pieces: list[Decimal] = []
    for edge in operation.inputs:
        source = by_id.get(edge.input_operation_id)
        position = source.position if source else 0
        found = [box for box in boxes if box.operation_id == edge.input_operation_id and box.status in TAKEABLE_BOX and box.available > 0]
        if found:
            ready.extend({
                "id": str(box.id), "number": box.number, "code": box.code, "quantity": _number(box.available),
                "operation_position": box.operation_position,
                "stage": (stage_name(source.stage) if source else "") or f"этап {position}",
            } for box in sorted(found, key=lambda item: (item.created_at, item.number or "")))
            per_unit = edge.per_unit if edge.per_unit > 0 else Decimal(1)
            pieces.append((sum((box.available for box in found), Decimal(0)) / per_unit).to_integral_value(rounding=ROUND_FLOOR))
        else:
            missing.append((edge, source, position))
    if not missing:
        return {"state": "assembly" if len(operation.inputs) > 1 else "ready",
                "text": "готово к сборке" if len(operation.inputs) > 1 else "можно начинать", "boxes": ready,
                "available": _number(min(pieces))}
    edge, source, position = missing[0]
    label = (stage_name(source.stage) if source else "") or f"этап {position}"
    where = f"этап {position} «{label}»" if position else label
    # Сданные коробки входа берутся сразу (подтверждения мастера больше нет); ждать можно только коробку,
    # которую монтажник ещё исправляет. Состояние «waiting_master» — прежнее имя в API.
    at_master = any(
        box.operation_id == edge.input_operation_id and box.available > 0
        and box.status in {BoxStatus.OPEN, BoxStatus.RETURNED}
        for box in boxes
    )
    row = (progress or {}).get(str(edge.input_operation_id)) or {}
    handed = Decimal(row.get("fact", {}).get("good") or 0)
    need = Decimal(row.get("required_quantity") or 0)
    if at_master:
        text = f"ждёт {where} — коробку исправляют"
    elif need and handed < need:
        text = f"ждёт {where}: сдано {_number(handed)} из {_number(need)}"
        if handed > 0:
            text += f", не хватает {_number(need - handed)} шт"
    elif need:
        # Этап-вход сдал всё, но его коробки уже взяли в работу (напарник по операции).
        text = f"ждёт {where}: всё сданное уже взято в работу"
    else:
        text = f"ждёт {where}: не сдан"
    return {
        "state": "waiting_master" if at_master else "waiting_stage",
        "text": text,
        "boxes": ready,
        "source_position": position or None,
        "handed": _number(handed),
        "need": _number(need),
    }


def _remaining(task: ProductionTask, operation, viewed: UUID, outputs, result: dict) -> str:
    """Сколько штук осталось сдать этому монтажнику: своя доля минус его сдача; без доли — остаток операции."""
    share = operation.share_of(viewed)
    if share is not None and share.quantity is not None:
        mine = sum((
            item.good_quantity + item.defect_quantity for item in outputs
            if item.operation_id == operation.id and item.operator_id == viewed
        ), Decimal(0))
        return _number(max(Decimal(0), Decimal(share.quantity) - mine))
    left = Decimal(result["required_quantity"]) - Decimal(result["fact"]["processed"])
    return _number(max(Decimal(0), left))


@router.get("/my")
def my_operations(
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    operator_id: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        viewed = _viewed_operator(uow, principal, operator_id)
        # Только ПЗ этого монтажника (запрос по операторам операций), коробки и выпуск — только этих ПЗ.
        mine = [task for task in uow.production.for_operator(viewed, limit=_MY_TASK_LIMIT) if task.status.value != "Отменено"]
        mine_ids = {task.id for task in mine}
        boxes = [box for task in mine for box in uow.boxes.list(task_id=task.id, limit=_MY_BOX_LIMIT)]
        outputs = list(uow.outputs.active_for_tasks(tuple(mine_ids)))
        facts = _facts(uow, mine, boxes, outputs)
        names = {item.id: item.name for item in uow.operators.list()}
        # «Факт / норма» сданного: сдачи исполнителя из журнала времени по его операциям.
        own_operations = tuple(operation.id for task in mine for operation in task.operations if viewed in operation.operator_ids)
        logged: dict[UUID, list[WorkTimeEntry]] = {}
        for entry in uow.work_log.entries(operator_id=viewed, operation_ids=own_operations):
            logged.setdefault(entry.operation_id, []).append(entry)
        # Свои задания — сам; чужие — работает ли за него смотрящий (иначе экран остаётся только просмотром).
        # Действия идут через гейт правки «Заданий» (production_view_work_principal) — без неё только просмотр.
        can_act = section_allowed(principal, ("myops",), "edit") and (viewed == principal.actor.id or _may_act_for(
            principal, uow.operators.get(principal.actor.id), uow.operators.get(viewed),
        ))
    boxes_by_task: dict[UUID, list[ProductionBox]] = {}
    for box in boxes:
        boxes_by_task.setdefault(box.task_id, []).append(box)
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
                    "readiness": _readiness(
                        task, operation, boxes_by_task.get(task.id, ()), result["operations"][index]["state"],
                        {row["id"]: row for row in result["operations"]},
                    ),
                    "remaining": _remaining(task, operation, viewed, outputs, result["operations"][index]),
                    "time_fact": _time_fact_result(logged.get(operation.id, ())),
                }]})
    operations.sort(key=lambda item: (
        (item["operations"][0]["my_share"] or {}).get("planned_start") or item["operations"][0]["planned_start"] or "9999",
        item["number"], item["operations"][0]["position"],
    ))
    return {"tasks": operations, "can_act": can_act}


def _time_fact_result(entries) -> dict | None:
    fact = time_fact(entries)
    if fact is None:
        return None
    return {"actual_seconds": fact.actual_seconds, "norm_seconds": fact.norm_seconds, "percent": fact.percent, "finishes": fact.finishes}


@router.get("/tasks/{task_id}/operations/{operation_id}/inputs")
def available_inputs(
    task_id: UUID,
    operation_id: UUID,
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    operator_id: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        # Работа за сотрудника: входы его операции — тому, кто видит его задания.
        viewed = _viewed_operator(uow, principal, operator_id)
        task = uow.production.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        try:
            operation = task.operation(operation_id)
        except ProductionValidationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if viewed not in operation.operator_ids:
            raise HTTPException(status_code=403, detail="Операция не назначена сотруднику")
        # Сетевой график: коробки всех этапов-входов операции (у сборки — каждой ветки), не только предыдущего номера.
        sources = {item.input_operation_id for item in operation.inputs}
        rows = [box for box in uow.boxes.list(task_id=task_id) if (
            box.item == task.item
            and box.operation_id in sources
            and box.status in TAKEABLE_BOX
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


def _etk_task_access(task_id: UUID, operation_id: UUID, principal: Principal, uow_factory: Callable[[], UnitOfWork]):
    """Задание и операция для ЭТК: монтажнику — своя операция, планировщику — любая."""
    with uow_factory() as uow:
        task = uow.production.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        try:
            operation = task.operation(operation_id)
        except ProductionValidationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if principal.actor.id not in operation.operator_ids and not section_allowed(principal, ("production",), "view"):
            raise HTTPException(status_code=403, detail="Операция не назначена сотруднику")
    return task, operation


@router.get("/my/tasks/{task_id}/operations/{operation_id}/etk")
def operation_etk(
    task_id: UUID,
    operation_id: UUID,
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
    issued_lots: Annotated[IssuedLots | None, Depends(etk_issued_lots)],
) -> dict:
    """ЭТК операции для монтажника без доступа к разделу техкарт: только выданная карта проекта и КБ этого ПЗ
    и операция, на которой её открыть. Монтажнику — своя операция, планировщику — любая."""
    task, operation = _etk_task_access(task_id, operation_id, principal, uow_factory)
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    with techcards_factory() as techcards:
        card = issued_card_for_item(techcards, task.project, task.item)
        result = card_views(techcards, [card], issued=issued_lots)[0] if card is not None else None
    if result is None:
        raise HTTPException(status_code=404, detail="Нет выданной ЭТК по этой позиции")
    # position — операция ЭТК, на которой открыть карту (у ПФ — его первая); positions — все операции ЭТК этой
    # операции задания по порядку ПФ (без ПФ — одна).
    positions = list(operation.etk_positions)
    # serial — номера изделий задания для «№ изделия» в надписях меток (сквозь задания позиции, mark_serial_range).
    with uow_factory() as uow:
        first, last = mark_serial_range(task, uow.production.for_project(task.project))
    return {"card": result, "focus": {
        "position": positions[0], "positions": positions, "code": operation_code(operation.code),
    }, "serial": {"first": first, "last": last, "task": task.number}}


@router.get("/my/tasks/{task_id}/harness")
def task_harness(
    task_id: UUID,
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> dict:
    """Сохранённый чертёж жгута позиции задания — только чтение, без истории версий; раздел «Проекты» монтажнику не
    нужен. Монтажнику — задание, где он назначен на любую операцию; мастеру с правом «Производство» — любое."""
    with uow_factory() as uow:
        task = uow.production.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        mine = any(principal.actor.id in operation.operator_ids for operation in task.operations)
        if not mine and not section_allowed(principal, ("production",), "view"):
            raise HTTPException(status_code=403, detail="Задание не назначено сотруднику")
        project, item = task.project, task.item
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    with techcards_factory() as techcards:
        return saved_harness(techcards, project, item)


@router.get("/my/tasks/{task_id}/operations/{operation_id}/etk/sketches")
def operation_etk_sketches(
    task_id: UUID,
    operation_id: UUID,
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> dict:
    """Эскизы выданной ЭТК этого задания: тот же доступ, что у самой ЭТК; раздел техкарт монтажнику не нужен."""
    task, _ = _etk_task_access(task_id, operation_id, principal, uow_factory)
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    with techcards_factory() as techcards:
        card = issued_card_for_item(techcards, task.project, task.item)
        if card is None:
            raise HTTPException(status_code=404, detail="Нет выданной ЭТК по этой позиции")
        sketches = techcards.techcards.sketches(card.id)
    return {"items": [{
        "operation_key": sketch.operation_key, "file_name": sketch.file_name, "size": sketch.size,
        "uploaded_by": sketch.uploaded_by, "uploaded_at": sketch.uploaded_at.isoformat(),
    } for sketch in sketches]}


@router.get("/my/tasks/{task_id}/operations/{operation_id}/etk/sketches/{operation_key:path}")
def operation_etk_sketch(
    task_id: UUID,
    operation_id: UUID,
    operation_key: str,
    principal: Annotated[Principal, Depends(production_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> Response:
    """Картинка эскиза: только из выданной ЭТК этого задания, ключ ищется среди её эскизов."""
    task, _ = _etk_task_access(task_id, operation_id, principal, uow_factory)
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    with techcards_factory() as techcards:
        card = issued_card_for_item(techcards, task.project, task.item)
        found = None
        if card is not None and operation_key in {sketch.operation_key for sketch in techcards.techcards.sketches(card.id)}:
            found = techcards.techcards.sketch_content(card.id, operation_key)
    if found is None:
        raise HTTPException(status_code=404, detail="Эскиз не найден")
    sketch, content = found
    return file_response(content, sketch.file_name)


def _input_limit(uow, session: WorkSession) -> str | None:
    """Сколько штук выхода можно сдать из взятых входных коробок: по каждому входу остаток штук ÷ штук на одну
    (как take_inputs), берём наименьшее. Нет входных коробок (первая операция) — None."""
    taken = list(uow.work.inputs(session.id))
    task = uow.production.get(session.task_id)
    if not taken or task is None:
        return None
    operation = next((item for item in task.operations if item.id == session.operation_id), None)
    if operation is None:
        return None
    boxes = [box for box in (uow.boxes.get(item.box_id) for item in taken) if box is not None]
    limits = []
    for stage in input_stages(task, operation):
        have = sum((box.available for box in boxes if box.operation_position == stage.position), Decimal(0))
        limits.append(have / stage.per_unit)
    return _number(min(limits)) if limits else None


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
        limit = _input_limit(uow, current) if current else None
    return {
        "session": {**_session_result(current), "input_limit": limit} if current else None,
        "boxes": [_box_result(box) for box in boxes],
    }


@router.get("/boxes")
def boxes(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    task_id: UUID | None = None,
    q: str = "",
) -> dict:
    """Коробки; q — поиск по коду с коробки (без учёта точек и пробелов) или по номеру."""
    with uow_factory() as uow:
        rows = uow.boxes.search(q) if q.strip() else uow.boxes.list(task_id=task_id)
        if q.strip() and task_id is not None:
            rows = [box for box in rows if box.task_id == task_id]
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


# «Задания»: start / pause / resume / cancel / submit / correct. Свои — по правке «Заданий»; за сотрудника
# (on_behalf_of) — по праву `_may_act_for`: исполнитель — он (факт, коробки, время), нажавший — в журналах.
# Idempotency-Key: тот же ключ второй раз отдаёт уже записанное, не делая действие дважды.
@router.post("/tasks/{task_id}/operations/{operation_id}/work/start")
def start_execution(
    task_id: UUID,
    operation_id: UUID,
    payload: StartWorkPayload,
    principal: Annotated[Principal, Depends(production_view_work_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    on_behalf_of: UUID | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    performer = _performer(uow_factory, principal, on_behalf_of)
    session = _execution(lambda: start_work(StartWorkCommand(
        task_id=task_id, operation_id=operation_id,
        box_numbers=tuple(payload.inputs),
        actor=principal.actor, now=now,
        materials=tuple(MaterialInput(item.fulfillment_line_id, item.quantity) for item in payload.materials),
        performer=performer, idempotency_key=_replay_key(idempotency_key),
    ), uow_factory()))
    return {"session": _session_result(session)}


def _session_command(session_id: UUID, principal: Principal, now: datetime, uow_factory, on_behalf_of: UUID | None, idempotency_key: str | None) -> SessionCommand:
    return SessionCommand(
        session_id, principal.actor, now,
        performer=_performer(uow_factory, principal, on_behalf_of), idempotency_key=_replay_key(idempotency_key),
    )


@router.post("/work/{session_id}/pause")
def pause_execution(session_id: UUID, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)], on_behalf_of: UUID | None = None, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict:
    command = _session_command(session_id, principal, now, uow_factory, on_behalf_of, idempotency_key)
    session = _execution(lambda: pause_work(command, uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/resume")
def resume_execution(session_id: UUID, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)], on_behalf_of: UUID | None = None, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict:
    command = _session_command(session_id, principal, now, uow_factory, on_behalf_of, idempotency_key)
    session = _execution(lambda: resume_work(command, uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/cancel")
def cancel_execution(session_id: UUID, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)], on_behalf_of: UUID | None = None, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict:
    command = _session_command(session_id, principal, now, uow_factory, on_behalf_of, idempotency_key)
    session = _execution(lambda: cancel_work(command, uow_factory()))
    return {"session": _session_result(session)}


@router.post("/work/{session_id}/submit")
def submit_execution(session_id: UUID, payload: SubmitWorkPayload, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)], on_behalf_of: UUID | None = None, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict:
    performer = _performer(uow_factory, principal, on_behalf_of)
    session, box, output, defect = _execution(lambda: submit_work(SubmitWorkCommand(
        session_id=session_id, actor=principal.actor, now=now,
        good_quantity=payload.good_quantity, defect_quantity=payload.defect_quantity,
        defect_character=payload.defect_character,
        defect_description=payload.defect_description,
        performer=performer, idempotency_key=_replay_key(idempotency_key),
    ), uow_factory()))
    return {
        "session": _session_result(session), "box": _box_result(box),
        "output_id": str(output.id), "defect": _defect_result(defect) if defect else None,
    }


@router.get("/work-time")
def work_time_log(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    date_from: date,
    date_to: date,
    operator_id: UUID | None = None,
) -> dict:
    """Журнал рабочего времени за дни периода (по Москве): каждое начало / пауза / продолжение / сдача / отмена."""
    since, until = _period(date_from, date_to)
    with uow_factory() as uow:
        rows = uow.work_log.entries(since=since, until=until, operator_id=operator_id)
    return {"entries": [_time_result(item) for item in rows]}


@router.get("/work-time/summary")
def work_time_summary(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    date_from: date,
    date_to: date,
    by: str = "operation_code",
) -> dict:
    """Сводка «факт / норма» сдач за период: по коду операции, исполнителю или проекту. Экрана пока нет (владелец,
    12.10.2026: данные копятся, сводка — через пару месяцев)."""
    if by not in SUMMARY_KEYS:
        raise HTTPException(status_code=422, detail="Сводка группируется по коду операции, исполнителю или проекту")
    since, until = _period(date_from, date_to)
    with uow_factory() as uow:
        rows = uow.work_log.summary(by, since=since, until=until)
    return {"by": by, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "rows": [{
        "key": str(row["key"]), "label": row["label"], "finishes": int(row["finishes"]),
        "quantity": _number(Decimal(row["quantity"])), "actual_seconds": int(row["actual_seconds"]),
        "norm_seconds": int(row["norm_seconds"]), "percent": percent(int(row["actual_seconds"]), int(row["norm_seconds"])),
    } for row in rows]}


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
def correct_execution_box(box_id: UUID, payload: CorrectBoxPayload, principal: Annotated[Principal, Depends(production_view_work_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)], on_behalf_of: UUID | None = None) -> dict:
    performer = _performer(uow_factory, principal, on_behalf_of)
    box, output, defect = _execution(lambda: correct_box(CorrectBoxCommand(
        box_id=box_id, expected_version=payload.version, actor=principal.actor, now=now, performer=performer,
        output_id=payload.output_id, good_quantity=payload.good_quantity,
        defect_quantity=payload.defect_quantity,
        defect_character=payload.defect_character,
        defect_description=payload.defect_description,
    ), uow_factory()))
    return {
        "box": _box_result(box), "output": _output_result(output),
        "defect": _defect_result(defect) if defect else None,
    }


# Подтверждения мастером нет (владелец, 13.10.2026: «ОТК подтверждает и все»): маршрута /confirm больше нет,
# «Вернуть…» — действие очереди ОТК, по правке «ОТК».
@router.post("/boxes/{box_id}/return")
def return_execution_box(box_id: UUID, payload: ReturnBoxPayload, principal: Annotated[Principal, Depends(production_quality_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]) -> dict:
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
                "box_code": box.code if box else None,
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
    with uow_factory() as uow:
        launched = launched_quantity(uow.production.list(limit=10_000))
    items = [{
        "techcard_id": str(card.techcard_id), "project": card.project, "index": card.index,
        "item": item_id(card.project, card.index), "name": card.name, "customer": card.customer, "run": card.run,
        "deadline": card.deadline.isoformat() if card.deadline else None,
        "launched": launched.get(card.key, 0), "operations": len(card.route.steps),
        "warnings": list(card.route.warnings),
    } for card in ready_cards(cards, kitsets)]
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
    planned = launch_route(card.document if card.status is TechcardStatus.ISSUED else None)
    route = planned.steps
    if not route:
        raise HTTPException(status_code=422, detail=f"Техкарта «{item}» не выдана — запускать нечего")
    try:
        operations = launch_operations(route, payload.quantity)
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # Решено агентом: ERP хранит в задании изделие индексом КБ (как план и приёмка ГП), а не «проект+индекс».
    response = _run(lambda: launch_production(LaunchProductionCommand(
        idempotency_key=idempotency_key, project=card.project, item=card.item_index,
        name=card.product_name, quantity=payload.quantity,
        fulfillment_request_id=payload.fulfillment_request_id, deadline=payload.deadline,
        operations=operations, actor=principal.actor, now=now,
        techcard_id=card.id, techcard_revision=card.revision if card.revision >= 1 else None,
    ), uow_factory()))
    # Схема полуфабрикатов не подошла — задание идёт цепочкой; экран заданий показывает warnings мастеру.
    response["warnings"] = list(planned.warnings)
    return response


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
        # A leave-request absence may belong to anyone (office staff too), not only to the operators the screen lists.
        names = {operator.id: operator.name for operator in uow.operators.list()}
    return {
        "holidays": [day.isoformat() for day in sorted(holidays)],
        "shift": _shift_result(shift),
        "absences": [{
            "id": str(item.id), "operator_id": str(item.operator_id),
            "from_date": item.from_date.isoformat(), "to_date": item.to_date.isoformat(), "kind": item.kind,
            # Set — the absence of a registered leave request: shown, never edited or saved by the calendar screen.
            "leave_request_id": str(item.leave_request_id) if item.leave_request_id else None,
            "operator_name": names.get(item.operator_id),
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


# ── Цель 2: план задания, «Сдано N шт», «Выполнено» ─────────────────────────────────────────────────────────


def _delivery_result(item: TaskDelivery) -> dict:
    return {
        "id": str(item.id), "site": item.site, "quantity": item.quantity,
        "delivered_at": item.delivered_at.isoformat(), "delivered_by": item.delivered_by,
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
        "cancelled_by": item.cancelled_by,
    }


def _card_of(task: ProductionTask, techcards_factory):
    """Техкарта задания: по сохранённому id, у заданий до 0089 — выданная карта проекта и КБ."""
    if techcards_factory is None:
        return None
    with techcards_factory() as techcards:
        card = techcards.techcards.get(task.techcard_id) if task.techcard_id else None
        return card if card is not None else issued_card_for_item(techcards, task.project, task.item)


def _flow_result(view: FlowView, card) -> dict:
    task, plan = view.task, view.plan
    stages = {operation.id: operation for operation in task.operations}
    issued = card is not None and card.status is TechcardStatus.ISSUED
    current = card.revision if issued else None
    return {
        "task": _result(task, delivered=delivered_total(view.deliveries)),
        "modes": [{"value": mode.value, "name": FLOW_MODE_NAMES[mode]} for mode in FlowMode],
        "thresholds": {"notice": _number(EARLY_NOTICE * 100), "limit": _number(EARLY_LIMIT * 100)},
        "plan": {
            "anchor": view.anchor.isoformat(),
            "transfer_batch": plan.transfer_batch,
            "first_ready": view.first_ready.isoformat(), "first_quantity": plan.first_quantity,
            "finish": view.finish.isoformat(),
            "baseline_finish": view.baseline_finish.isoformat(),
            "baseline_early_ready": view.baseline_early_ready.isoformat() if view.baseline_early_ready else None,
            "delay_percent": _number((plan.delay * 100).quantize(Decimal("0.1"))) if plan.delay is not None else None,
            "level": plan.level,
            "work_seconds": int(plan.finish),
            "warnings": list(view.warnings),
            "steps": [{
                "operation_id": str(item.operation_id), "position": item.position,
                "stage": stage_name(stages[item.operation_id].stage),
                "code": operation_code(stages[item.operation_id].code),
                "people": _number(item.people),
                "operator_ids": [str(value) for value in stages[item.operation_id].operator_ids],
                "start": item.start.isoformat(), "first_out": item.first_out.isoformat(),
                "finish": item.finish.isoformat(), "wait_seconds": item.wait_seconds,
            } for item in view.steps],
        },
        "deliveries": [_delivery_result(item) for item in view.deliveries],
        "delivered": delivered_total(view.deliveries),
        "norms": {
            "techcard_id": str(card.id) if card is not None else None,
            "revision": task.techcard_revision,
            "current_revision": current,
            # Нормы задания старее выданной ЭТК — мастер обновляет кнопкой, сами нормы не меняются.
            "outdated": current is not None and (task.techcard_revision is None or current > task.techcard_revision),
        },
    }


@router.get("/tasks/{task_id}/flow")
def task_flow(
    task_id: UUID,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> dict:
    view = _execution(lambda: flow_view(task_id, uow_factory(), now))
    return _flow_result(view, _card_of(view.task, techcards_factory))


@router.put("/tasks/{task_id}/flow")
def update_task_flow(
    task_id: UUID,
    payload: FlowPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    return _run(lambda: update_flow(UpdateFlowCommand(
        task_id=task_id, expected_version=payload.version, flow_mode=payload.flow_mode,
        transfer_batch=payload.transfer_batch, early_quantity=payload.early_quantity,
        team=tuple(payload.team), actor=principal.actor, now=now,
    ), uow_factory()))


@router.post("/tasks/{task_id}/norms/refresh")
def refresh_task_norms(
    task_id: UUID,
    payload: BoxVersionPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        task = uow.production.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    card = _card_of(task, techcards_factory)
    if card is None or card.status is not TechcardStatus.ISSUED:
        raise HTTPException(status_code=422, detail=f"Техкарта «{task.project} {task.item}» не выдана — нормы брать неоткуда")
    steps = launch_route(card.document).steps
    if any(operation.etk_ops for operation in task.operations):
        # Задание по ПФ: нормы ПФ — суммы его операций; состав ПФ (операции ЭТК и их коды) должен быть прежним.
        try:
            steps = semifinished_steps(steps)
        except ProductionValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        fresh ={step.position: [(item.position, item.code) for item in step.etk_ops] for step in steps}
        own = {operation.position: [(item.position, item.code) for item in operation.etk_ops] for operation in task.operations}
        if fresh != own:
            raise HTTPException(status_code=422, detail=(
                "Полуфабрикаты техкарты изменились (другой состав ПФ или коды операций) — нормы не обновлены. "
                "Запустите новое задание по выданной техкарте"
            ))
    norms = {step.position: (step.code, step.seconds_per_unit, step.setup_seconds) for step in steps}
    return _run(lambda: refresh_norms(RefreshNormsCommand(
        task_id=task_id, expected_version=payload.version, techcard_id=card.id, revision=card.revision,
        norms=norms, actor=principal.actor, now=now,
    ), uow_factory()))


@router.post("/tasks/{task_id}/deliveries")
def deliver(
    task_id: UUID,
    payload: DeliveryPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    result: dict = {}

    def execute():
        task, delivery = record_delivery(RecordDeliveryCommand(
            task_id=task_id, expected_version=payload.version, quantity=payload.quantity,
            actor=principal.actor, now=now,
        ), uow_factory())
        result["delivery"] = _delivery_result(delivery)
        return task

    response = _run(execute)
    response.update(result)
    return response


@router.post("/tasks/{task_id}/deliveries/{delivery_id}/cancel")
def cancel_task_delivery(
    task_id: UUID,
    delivery_id: UUID,
    payload: BoxVersionPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    return _run(lambda: cancel_delivery(CancelDeliveryCommand(
        task_id=task_id, expected_version=payload.version, delivery_id=delivery_id,
        actor=principal.actor, now=now,
    ), uow_factory()))


@router.post("/tasks/{task_id}/status")
def set_task_status(
    task_id: UUID,
    payload: TaskDonePayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    return _run(lambda: set_task_done(SetTaskDoneCommand(
        task_id=task_id, expected_version=payload.version, done=payload.done,
        actor=principal.actor, now=now,
    ), uow_factory()))
