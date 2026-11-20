"""Generic machine operation queue («Оборудование», machine without control; plan T4 2026-11-21).

Computed, no table of its own: every open ETK step of the running tasks whose ETK tooling names this physical unit or its
model pool (same matching as the cutting queue), except cable cuts - those live in the cutting queue. A missing ETK value
never drops a row: the row carries a warning and the operator works on. «Начать» is a manual job whose `params.source`
points back at the (task, PF, ETK step); `done` of a row is what the finished source jobs made plus the running one."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from sqlalchemy import String, cast, select

from .cutting import CABLE_CUTS, digest
from .cutting_queue import PoolUnit, _count, _machines, _mapping, _People, _site_allowed, _sites, row_project
from .domain import EtkOperation, ProductionStatus, ProductionValidationError
from .equipment_catalog import EMPTY, EquipmentCatalog
from .execution import operation_quantity
from .launchable import stage_name
from .manual_jobs import ManualJobRow, number_text

SOURCE = "source"
MACHINE_SECTION = "Оборудование"
NO_MODE = "нет в ЭТК: режим станка"
NOT_RUNNING = "Задание не запущено — работу по нему начать нельзя"
PF_DONE = "ПФ уже сдан полностью — работу по нему начать нельзя"


def _step_name(part: EtkOperation) -> str:
    return stage_name(part.stage) or part.code or f"Операция ЭТК {part.position}"


def _parts(operation) -> tuple[EtkOperation, ...]:
    return operation.etk_ops or (EtkOperation(operation.position, operation.code, operation.stage),)


def _config_type(document: Mapping, index: int) -> str:
    ops = _mapping(document.get("build_config")).get("ops") or []
    return str(_mapping(ops[index]).get("type") or "") if 0 <= index < len(ops) else ""


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def etk_values(built: Mapping, warnings: list[str]) -> list[dict]:
    """«Режим по ЭТК»: what the step's tooling says (program, parameters), read-only. A step whose tooling gives no
    program or parameters at all shows «Режим станка» as not set (source «missing») with a warning."""
    result = []
    for tool in _mapping(built.get("card")).get("tooling") or []:
        tool = _mapping(tool)
        value = " · ".join(part for part in (_text(tool.get("program")), _text(tool.get("parameters"))) if part)
        if value:
            result.append({"label": _text(tool.get("name") or tool.get("section")) or "Оснастка", "value": value,
                           "source": "etk"})
    if not result:
        result.append({"label": "Режим станка", "value": "", "source": "missing"})
        warnings.append(NO_MODE)
    return result


def source_of(params: Mapping | None) -> dict | None:
    """The job's source as the responses show it: {task_id, operation_id, etk_position, label}; None for a free job."""
    value = _mapping(params).get(SOURCE)
    if not isinstance(value, Mapping) or not value.get("task_id"):
        return None
    return {"task_id": str(value["task_id"]), "operation_id": str(value.get("operation_id") or ""),
            "etk_position": value.get("etk_position"), "label": str(value.get("label") or "")}


def check_source(uow, task_id: UUID, operation_id: UUID, etk_position: int) -> dict:
    """Validated source for a new job: unknown task/PF/step -> KeyError (404); task not running or PF done -> 422."""
    task = uow.production.get(task_id)
    if task is None:
        raise KeyError("Задание не найдено")
    operation = next((item for item in task.operations if item.id == operation_id), None)
    if operation is None:
        raise KeyError("ПФ нет в задании")
    part = next((item for item in _parts(operation) if item.position == etk_position), None)
    if part is None:
        raise KeyError("Операции ЭТК нет в этом ПФ")
    if task.status is not ProductionStatus.RUNNING:
        raise ProductionValidationError(NOT_RUNNING)
    if uow.outputs.total_for_operation(operation_id) >= operation_quantity(task, operation):
        raise ProductionValidationError(PF_DONE)
    return {"task_id": str(task_id), "operation_id": str(operation_id), "etk_position": etk_position,
            "label": f"{task.project} · {task.item} · {_step_name(part)}"}


def _source_jobs(session, task_ids: set[UUID]) -> list[tuple[dict, dict]]:
    """Running and finished jobs that carry a source of one of the tasks (params is JSON: filtered in Python)."""
    rows = session.scalars(select(ManualJobRow).where(ManualJobRow.status.in_(("running", "done")),
                                                      cast(ManualJobRow.params, String).like(f'%"{SOURCE}"%')))
    result = []
    for row in rows:
        source = source_of(row.params)
        if source is not None and UUID(source["task_id"]) in task_ids:
            result.append(({"id": row.id, "number": row.number, "status": row.status, "good": row.good_quantity or 0,
                            "progress": row.progress_quantity}, source))
    return result


def collect(uow, actor, unit: PoolUnit) -> list[dict]:
    """Open steps of running tasks that the unit's site (and the viewer's) may take, with their source jobs."""
    people = _People(uow)
    running = list(uow.production.with_status(ProductionStatus.RUNNING))
    made: dict[UUID, Decimal] = {}
    for output in uow.outputs.active_for_tasks(tuple(task.id for task in running)) if running else ():
        made[output.operation_id] = made.get(output.operation_id, Decimal(0)) + output.good_quantity + output.defect_quantity
    jobs = _source_jobs(uow.session, {task.id for task in running}) if running else []
    result = []
    for task in running:
        for operation in task.operations:
            planned = operation_quantity(task, operation)
            if made.get(operation.id, Decimal(0)) >= planned:
                continue  # the PF is handed over in full
            if unit.site and any(site != unit.site for site in _sites(task, operation, people)):
                continue
            if not _site_allowed(task, operation, people, actor):
                continue
            for part in _parts(operation):
                mine = [row for row, source in jobs if source["operation_id"] == str(operation.id)
                        and source["etk_position"] == part.position]
                running_job = next((row for row in mine if row["status"] == "running"), None)
                result.append({"task": task, "operation": operation, "part": part, "planned": planned,
                               "finished": sum(row["good"] for row in mine if row["status"] == "done"),
                               "running": running_job})
    return result


def queue_rows(candidates: list[dict], documents: Mapping, unit: PoolUnit,
               catalog: EquipmentCatalog = EMPTY) -> list[dict]:
    """Rows from `collect` plus the pinned ETKs; pure. Cable cuts and steps of other machines are dropped; a finished
    step (its source jobs made the whole plan, nothing running) too."""
    rows = []
    for item in candidates:
        task, operation, part = item["task"], item["operation"], item["part"]
        document = documents.get((task.project, task.item, task.techcard_revision))
        if not isinstance(document, Mapping):
            continue  # no readable ETK - nothing says which machine the step needs
        index = part.position - 1
        built_ops = document.get("operations") or []
        built = _mapping(built_ops[index]) if 0 <= index < len(built_ops) else {}
        if not built or str(built.get("type") or _config_type(document, index)) in CABLE_CUTS:
            continue
        named = dict(_machines(built, [], catalog))
        if not any(identity in unit.accept for identity in named):
            continue
        running = item["running"]
        planned = int(item["planned"])
        done = item["finished"] + ((running["progress"] or 0) if running else 0)
        left = max(0, planned - done)
        if left == 0 and running is None:
            continue
        warnings: list[str] = []
        project = row_project(task, document)
        rows.append({
            "key": f"{task.id}:{operation.id}:{part.position}", "task_id": str(task.id), "operation_id": str(operation.id),
            "task_number": task.number, "project": project, "item": task.item,
            "pf": operation.stage, "etk_position": part.position, "step": _step_name(part),
            "planned": _count(Decimal(planned)), "done": _count(Decimal(done)), "left": _count(Decimal(left)),
            "etk": etk_values(built, warnings), "warnings": warnings,
            "job": ({"id": str(running["id"]), "number": number_text(running["number"]),
                     "progress_quantity": running["progress"]} if running else None),
            "_order": (project, task.item, task.number, operation.position, part.position),
        })
    rows.sort(key=lambda row: row["_order"])
    for row in rows:
        row.pop("_order")
    return rows


def queue_version(rows: list[dict]) -> str:
    return digest([[row["key"], row["planned"], row["done"], row["left"], row["etk"], row["warnings"], row["job"]]
                   for row in rows])
