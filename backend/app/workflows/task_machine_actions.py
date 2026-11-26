"""Read-only navigation from an assigned PF's pinned instruction to an exact machine source.

Do not call ETK rendering, source_rows, machine control views or equipment discovery here:
those paths can issue serials, settle runs or persist bindings. Write handlers revalidate
the returned sources when the operator explicitly loads/prints/starts work.
"""

from datetime import datetime
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.modules.production import machine_queue, machine_runs, machine_writes, machines, ob099
from app.modules.production.api import _etk_task_access, production_uow_factory, techcards_uow_factory
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.cutting import CABLE_CUTS
from app.modules.production.cutting_api import equipment_rows as cutting_rows
from app.modules.production.domain import EtkOperation, ProductionStatus, ProductionValidationError
from app.modules.production.launchable import route_from_document
from app.modules.production.marking_sources import MAX_LABELS, check_materials, labels
from app.modules.production.machine_queue_api import equipment_rows as machine_rows
from app.modules.production.resource_assignment_infrastructure import (
    ResourceAllocationMachineRow, ResourceAllocationMemberRow, ResourceAllocationRow,
)
from app.modules.production.resource_claims import ResourceConflict
from app.modules.warehouse.devices import PrinterRow, printer_online
from app.modules.warehouse.marking_profiles import MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.printer_commands import agent_executes_commands, marking_printers
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_view_principal, section_allowed

from .etk_setup_units import ready_for_operation
from .machine_writes_api import _can_edit, _length, _transfer_plan, marking_writes, refuse_while_manual
from .marking_labels import issued_document, task_access
from .marking_progress import serial_range_of
from .ob019_setup import GROUP_STATES
from .production_resource_context import SqlAlchemyProductionResourceContext, machine_units

router = APIRouter(tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
UNRESOLVED = "Не определена конкретная единица оборудования — уточните назначение или наладку"


def _bindings(session, task, operation):
    current = select(ResourceAllocationMemberRow.allocation_id).where(
        ResourceAllocationMemberRow.operation_id == operation.id, ResourceAllocationMemberRow.current.is_(True))
    rows = session.execute(select(ResourceAllocationMachineRow.etk_position, ResourceAllocationMachineRow.machine_id)
        .join(ResourceAllocationRow).where(ResourceAllocationRow.task_id == task.id,
            ResourceAllocationRow.operation_id == operation.id, ResourceAllocationRow.id.in_(current)))
    result = {}
    for position, machine_id in rows:
        result.setdefault(position, set()).add(machine_id)
    return result


def _document(uow, task, operation):
    """Issued/pinned instruction, with the same frozen member overlay as the task ETK."""
    member = uow.cutting.for_operation(operation.id)
    if member:
        group = uow.cutting.get(member.group_id)
        document = member.row_snapshot.get("document")
        if group is None or not document:
            raise ValueError("В группе нет сохранённой ЭТК")
        if group.state == "open":
            _current, revision = issued_document(task, uow, lock=False)
            if revision["card_version"] != member.row_snapshot.get("validated_card_version"):
                raise ValueError("ЭТК группы изменилась — требуется повторная проверка группы резки")
        return document, member, group
    document, _revision = issued_document(task, uow, lock=False)
    return document, None, None


def _unit_for(part, built, units, bindings, setups, task):
    chosen = set(bindings.get(part.position, ()))
    setup = setups.get(part.position)
    if setup:
        ref = (setup.snapshot or {}).get("operation") or {}
        if ((task.techcard_revision is not None and ref.get("card_revision") != task.techcard_revision)
                or (task.techcard_id is not None and ref.get("card_id") != str(task.techcard_id))):
            return None, "Наладка относится к другой ревизии ЭТК"
        chosen.add(setup.machine_id)
    if not chosen:
        # A concrete issued inventory is allowed only when it resolves uniquely. A model
        # pool never chooses its first unit; supporting devices are not operation executors.
        numbers = {str(tool.get("inventory_number") or "").strip().upper()
                   for tool in (built.get("card") or {}).get("tooling", [])
                   if tool.get("section") == "Оборудование"}
        chosen = {unit.id for unit in units.values() if unit.operational and unit.inventory_number.upper() in numbers}
    if len(chosen) != 1:
        return None, UNRESOLVED
    unit = units.get(next(iter(chosen)))
    if unit is None or not unit.operational:
        return None, "Назначенное оборудование отсутствует или не является исполнителем операции"
    try:
        models = {SqlAlchemyProductionResourceContext._tool_model(tool, tuple(units.values()))
                  for tool in (built.get("card") or {}).get("tooling", [])
                  if tool.get("section") == "Оборудование"}
    except ResourceConflict as error:
        return None, str(error)
    if unit.model_id not in models - {None}:
        return None, "Назначенный станок не соответствует оборудованию закреплённой ЭТК"
    return unit, None


def _kind(session, unit):
    link = session.get(machines.MachineLinkRow, unit.id)
    if link:
        return ("ob019" if link.kind == machines.KIND_OB019 else "ob099" if link.kind == ob099.KIND else "generic"), link
    if session.get(MarkingBindingRow, unit.id) or session.scalar(select(MarkingProfileRow.id).where(
            MarkingProfileRow.equipment_id == unit.id)):
        return "marking", None
    expected = machines.SUPPORTED.get(unit.inventory_number)
    return ("ob019" if expected == machines.KIND_OB019 else "ob099" if expected == ob099.KIND else "generic"), None


def _connectivity(session, unit, kind, link, now):
    if kind in ("ob019", "ob099"):
        snapshot = session.get(machines.MachineSnapshotRow, unit.id) if link else None
        state = machines.link_status(snapshot, now)
        return {"state": state, "fresh": snapshot is not None and state != "stale",
                "last_seen": snapshot.reported_at.isoformat() if snapshot else None}, snapshot
    if kind == "marking":
        binding = session.get(MarkingBindingRow, unit.id)
        printer = session.get(PrinterRow, (binding.machine_id, binding.printer)) if binding else None
        agent = session.get(ScanAgentRow, binding.machine_id) if binding else None
        online = bool(binding and printer_online(session, binding.machine_id, binding.printer, now)
                      and agent and machines.since(agent.last_seen, now) <= LIVE_FOR)
        return {"state": "ok" if online else "offline", "fresh": online,
                "last_seen": printer.last_seen.isoformat() if printer else None}, printer
    return {"state": "not_connected", "fresh": False, "last_seen": None}, None


def _group_members(uow, group, principal):
    """Validate every member before disclosing a group-wide adapter source."""
    result = []
    for member in uow.cutting.members(group.id):
        task = uow.production.get(member.task_id)
        task_access(task, member.operation_id, principal, uow)
        operation = task.operation(member.operation_id)
        _document(uow, task, operation)
        result.append((member, task))
    if not result:
        raise ValueError("В группе нет действующих заданий")
    return result


def _read_problem(check):
    """Readiness errors disable loading but keep the validated navigation source."""
    try:
        return check()
    except HTTPException as error:
        return str(error.detail)
    except (ValueError, ProductionValidationError, machine_writes.MachineCommandRefused,
            machines.MachineWriteInvalid) as error:
        return str(error)


def _ob019_problem(uow, link, unit, group, source, now):
    snapshot = uow.session.get(machines.MachineSnapshotRow, unit.id)
    problem = machine_writes.idle_problem(link, snapshot, now)
    if problem:
        return problem
    refuse_while_manual(uow.session, unit.id)
    plan = _transfer_plan(uow.session, link, group.id, source["length_mm"], now)
    if plan["blocking"]:
        return "В ЭТК не задано: " + ", ".join(plan["blocking"])
    if plan["quantity"] <= 0:
        return "Задание резки уже выполнено"
    marking_writes(plan)  # validates unsupported laser content; does not enqueue
    machine_writes.quantity_as_target([machine_writes.FieldWrite("total_set", plan["quantity"])], snapshot, now)
    current = machine_runs.current(uow.session, unit.id)
    if machine_runs.is_open(current) and not machine_runs.same_job(machine_runs.info(current),
            machine_runs.cutting(group.id, source["length_mm"], "", None)):
        return "На станке уже открыто задание — проверьте его в карточке"
    if uow.session.scalar(select(machine_writes.MachineWriteRow.id).where(
            machine_writes.MachineWriteRow.equipment_id == unit.id,
            machine_writes.MachineWriteRow.status.in_(machine_writes.OPEN))):
        return "Станок ещё выполняет предыдущую команду"
    return None


def _source(uow, task, operation, part, built, member, group, kind, link, unit, principal,
            now, techcards_factory, references_factory):
    if task.status is not ProductionStatus.RUNNING:
        raise ValueError("Задание не запущено или уже закрыто")
    if kind == "ob019":
        if str(built.get("type") or "") not in CABLE_CUTS or member is None:
            raise ValueError("Для загрузки резки нужна группа с точной длиной провода")
        if group.state not in GROUP_STATES:
            raise ValueError("Группа резки уже закрыта")
        members = _group_members(uow, group, principal)
        raw = member.row_snapshot.get("cut_length_mm")
        length = _length(str(raw or ""))
        same = [(m, t) for m, t in members if _length(str(m.row_snapshot.get("cut_length_mm") or "")) == length]
        source = {"kind": "ob019", "group_id": str(group.id), "version": group.version,
                  "length_mm": format(length, "f"), "members": [
                      {"task_id": str(t.id), "operation_id": str(m.operation_id), "item": t.item,
                       "quantity": str(m.blank_quantity)} for m, t in same]}
        return source, _read_problem(lambda: _ob019_problem(uow, link, unit, group, source, now))
    if kind == "marking":
        selected = [f"{task.id}:{part.position - 1}:{i}"
                    for i, mark in enumerate((built.get("fields") or {}).get("marks") or [])
                    if str(mark.get("text") or "").strip()]
        if not selected:
            raise ValueError("В операции ЭТК нет надписей для печати этикеток")
        if member:
            if group.state not in GROUP_STATES:
                raise ValueError("Группа маркировки уже закрыта")
            members = _group_members(uow, group, principal)
            selected = []
            ordinal = operation.etk_positions.index(part.position)
            documents = []
            for m, t in members:
                member_op = t.operation(m.operation_id)
                parts = member_op.etk_ops or (EtkOperation(member_op.position, member_op.code, member_op.stage),)
                if ordinal >= len(parts) or parts[ordinal].code != part.code:
                    raise ValueError("Состав операций группы изменился — обновите инструкцию")
                pos = parts[ordinal].position
                doc = m.row_snapshot["document"]
                built_ops = doc.get("operations") or []
                if pos < 1 or pos > len(built_ops):
                    raise ValueError("Операции группы нет в сохранённой ЭТК")
                selected.extend(f"{t.id}:{pos - 1}:{i}" for i, mark in enumerate(
                    (built_ops[pos - 1].get("fields") or {}).get("marks") or [])
                    if str(mark.get("text") or "").strip())
                documents.append((t, doc))
            source = {"group_id": str(group.id), "version": group.version}
        else:
            source = {"task_id": str(task.id), "operation_id": str(operation.id), "version": task.version}
            doc, _revision = issued_document(task, uow, lock=False)
            documents = [(task, doc)]
        expanded = [row for t, doc in documents for row in labels(doc, t, serial_range_of(t, uow), selected)]
        if not expanded or sum(row["quantity"] for row in expanded) > MAX_LABELS:
            raise ValueError("Для выбранной операции требуется от 1 до 500 этикеток")
        check_materials(expanded)
        profiles = list(uow.session.scalars(select(MarkingProfileRow).where(
            MarkingProfileRow.equipment_id == unit.id, MarkingProfileRow.calibrated.is_(True))))
        binding = uow.session.get(MarkingBindingRow, unit.id)
        printer = printer_online(uow.session, binding.machine_id, binding.printer, now) if binding else None
        problem = "Нет откалиброванного профиля печати" if not profiles else None
        if not binding:
            problem = "Оборудование не связано с принтером"
        elif not printer:
            problem = "Принтер не на связи"
        elif printer.hold:
            problem = printer.hold
        else:
            agent = uow.session.get(ScanAgentRow, binding.machine_id)
            if agent and not agent_executes_commands(agent.version) and any(
                    item["name"] == binding.printer and item["peel"] for item in marking_printers(uow.session, binding.machine_id)):
                problem = "Обновите агент печати для работы с отделителем Godex"
        return {"kind": "marking", "source": source, "selected": selected}, problem
    if kind == "ob099":
        return None, "Этот станок поддерживает только наблюдение — загрузка задания не предусмотрена"
    cutting = str(built.get("type") or "") in CABLE_CUTS
    # On THIS uow: a second production UoW here would wait forever for the resource lock this one holds (prod 23.11).
    rows = (cutting_rows(uow, principal, techcards_factory, references_factory, unit.id)
            if cutting else machine_rows(uow, unit.id, principal, techcards_factory, references_factory))
    row = next((row for row in rows if row["task_id"] == str(task.id)
                and row["operation_id"] == str(operation.id)
                and (cutting or row["etk_position"] == part.position)), None)
    if row is None:
        raise ValueError("Операции нет в текущей очереди этого станка — обновите инструкцию")
    problem = row["blocked"] if cutting else _read_problem(
        lambda: machine_queue.check_source(uow, task.id, operation.id, part.position) and None)
    return {"kind": "generic", "task_id": str(task.id), "operation_id": str(operation.id),
            "etk_position": part.position, "queue_kind": "cutting" if cutting else "machine"}, (
                problem or "У станка нет адаптера загрузки — работа учитывается вручную")


@router.get("/api/production/my/tasks/{task_id}/operations/{operation_id}/etk/machine-actions")
def operation_machine_actions(task_id: UUID, operation_id: UUID,
        principal: Annotated[Principal, Depends(production_view_principal)], factory: Factory,
        now: Annotated[datetime, Depends(moscow_now)],
        techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)],
        references_factory: Annotated[Callable | None, Depends(references_uow_factory)]) -> dict:
    task, operation = _etk_task_access(task_id, operation_id, principal, factory)
    with factory() as uow:
        session = uow.session
        try:
            task_access(task, operation.id, principal, uow, historical=True)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        problem = None
        try:
            document, member, group = _document(uow, task, operation)
        except (KeyError, ValueError, ConcurrentProductionUpdate) as error:
            document, member, group = {}, None, None
            problem = error.args[0]
        route = route_from_document(document)
        units = {unit.id: unit for unit in machine_units(session)}
        bindings, setups = _bindings(session, task, operation), ready_for_operation(session, task.id, operation.id)
        actions = []
        for part in operation.etk_ops or (EtkOperation(operation.position, operation.code, operation.stage),):
            index = part.position - 1
            built = (document.get("operations") or [])[index] if 0 <= index < len(document.get("operations") or []) else {}
            stale = problem or ("Операция задания не совпадает с закреплённой ЭТК" if not built or index >= len(route)
                                or route[index].code != part.code else None)
            unit, unresolved = _unit_for(part, built, units, bindings, setups, task)
            kind, link = _kind(session, unit) if unit else ("none", None)
            connectivity, _snapshot = _connectivity(session, unit, kind, link, now) if unit else (
                {"state": "unresolved", "fresh": False, "last_seen": None}, None)
            open_permitted = not principal.service and section_allowed(principal, ("production", "myops"), "view")
            load_permitted = (not principal.service and section_allowed(principal, ("print",), "edit")) if kind == "marking" else _can_edit(principal)
            source, blocker = None, stale or unresolved
            if not blocker:
                try:
                    source, blocker = _source(uow, task, operation, part, built, member, group, kind, link, unit,
                        principal, now, techcards_factory, references_factory)
                except (KeyError, ValueError, ArithmeticError, PermissionError, ProductionValidationError,
                        ConcurrentProductionUpdate, machine_writes.MachineCommandRefused, machines.MachineWriteInvalid) as error:
                    blocker = str(error.args[0])
                except HTTPException as error:
                    blocker = str(error.detail)
            if not open_permitted:
                blocker = "Нет права просмотра оборудования"
            elif not load_permitted and kind in ("ob019", "marking"):
                blocker = "Нет права печати этикеток" if kind == "marking" else "Нет права загрузки задания в станок"
            if kind == "marking" and not connectivity["fresh"] and not blocker:
                blocker = "Принтер не на связи"
            actions.append({"etk_position": part.position, "equipment_id": str(unit.id) if unit else None,
                "inventory_number": unit.inventory_number if unit else None,
                "display_name": unit.name if unit else None, "model": unit.model if unit else None,
                "card_kind": kind, "connectivity": connectivity, "open_permitted": open_permitted,
                "load_permitted": load_permitted,
                "can_open": bool(unit and open_permitted and not stale and (source is not None or kind == "ob099")),
                "can_load": bool(source and load_permitted and not blocker and kind in ("ob019", "marking")),
                "blocker": blocker, "source": source})
    return {"task_id": str(task.id), "operation_id": str(operation.id), "task_version": task.version, "actions": actions}
