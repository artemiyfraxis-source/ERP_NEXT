"""Transactional execution of one cutting job with separate outputs for its original PFs."""

from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select

from app.modules.fulfillment.domain import FulfillmentStatus
from .application import ConcurrentProductionUpdate, lock_reflow_projects, record_fact
from .cutting import cutting_project_label, digest
from .cutting_application import audit, check_access, describe_group, locked_group, row_behind
from .cutting_infrastructure import CuttingActionRow, CuttingOutputRow, CuttingWorkRow
from .domain import ProductionStatus, ProductionValidationError
from .downtime import Downtime, DowntimeReason
from .downtime_infrastructure import SqlAlchemyDowntimeRepository
from .execution import SessionCommand, SubmitWorkCommand, _card_versions, _log, operation_quantity, record_work_output
from .traceability import MaterialInput, WorkSession, WorkStatus
from .worklog import WorkEvent


def _members(group, worker, uow):
    check_access(group, uow, worker)
    members = uow.cutting.members(group.id)
    if not members:
        raise ProductionValidationError("В группе нет активных КБ")
    result = []
    for member in members:
        task = uow.production.get(member.task_id)
        operation = task.operation(member.operation_id)
        if worker.id not in operation.operator_ids:
            raise PermissionError("Вся группа должна быть назначена одному исполнителю")
        if task.status not in (ProductionStatus.RUNNING, ProductionStatus.DONE):
            raise ProductionValidationError("Задание группы отменено")
        result.append((member, task, operation))
    return result


def _same_machines(members, worker, uow):
    if getattr(uow, "resources", None) is None:
        return
    from .resource_assignment_infrastructure import ResourceAllocationMemberRow, ResourceAllocationMachineRow
    bindings = []
    for _, _, operation in members:
        allocation = uow.session.scalar(select(ResourceAllocationMemberRow).where(
            ResourceAllocationMemberRow.operation_id == operation.id,
            ResourceAllocationMemberRow.operator_id == worker.id,
            ResourceAllocationMemberRow.current.is_(True)))
        if allocation is None:
            raise ProductionValidationError("Назначьте исполнителя и оборудование всей группы")
        bindings.append(set(uow.session.execute(select(
            ResourceAllocationMachineRow.etk_position, ResourceAllocationMachineRow.machine_id,
        ).where(ResourceAllocationMachineRow.allocation_id == allocation.allocation_id)).all()))
    if any(binding != bindings[0] for binding in bindings[1:]):
        raise ProductionValidationError("Для общей резки назначьте одинаковое оборудование всем КБ")


def _start(group, members, actor, worker, now, key, uow):
    from .infrastructure import ProductionWorkSessionRow
    if uow.work.current(worker.id) or uow.cutting.work(group.id):
        raise ProductionValidationError("Сначала завершите текущую работу")
    ids = [operation.id for _, _, operation in members]
    if uow.session.scalar(select(ProductionWorkSessionRow.id).where(
            ProductionWorkSessionRow.operation_id.in_(ids),
            ProductionWorkSessionRow.status.in_((WorkStatus.ACTIVE.value, WorkStatus.PAUSED.value))).limit(1)):
        raise ProductionValidationError("КБ группы уже начата другим исполнителем")
    if all(uow.outputs.total_for_operation(op.id) >= operation_quantity(task, op) for _, task, op in members):
        raise ProductionValidationError("Группа уже выполнена")
    _same_machines(members, worker, uow)
    _, task, operation = next((member, task, op) for member, task, op in members
                              if uow.outputs.total_for_operation(op.id) < operation_quantity(task, op))
    session = WorkSession.start(task.id, operation.id, worker.id, now)
    uow.work.save(session)
    uow.session.flush()
    work = CuttingWorkRow(session_id=session.id, group_id=group.id, closed=False, accounted_seconds=0)
    uow.session.add(work)
    group.state = "frozen"
    group.started_at = group.started_at or now
    _log(uow, SessionCommand(session.id, actor, now, performer=worker, idempotency_key=f"cut:{digest(key)}"),
         session, WorkEvent.START, task, operation)
    for member, child, operation in members:
        versions = _card_versions({"document": member.row_snapshot["document"]}, tuple(etk.position for etk in operation.etk_ops))
        uow.work.record_card_versions(operation.id, versions)
        if uow.outputs.total_for_operation(operation.id) < operation_quantity(child, operation):
            record_fact(uow, task_id=child.id, operation_id=operation.id, operator_id=worker.id,
                        actor=actor, now=now, started=True)


def _materials(task, values, uow):
    """Selections are per submission and per KB; never borrow another request's entitlement."""
    materials = tuple(MaterialInput(UUID(item["fulfillment_line_id"]), Decimal(item["quantity"])) for item in values)
    if not materials:
        return ()
    if task.fulfillment_request_id is None or uow.materials.request_status(task.fulfillment_request_id) is not FulfillmentStatus.ISSUED:
        raise ProductionValidationError("Комплект по заявке ещё не выдан в производство")
    if len({item.fulfillment_line_id for item in materials}) != len(materials):
        raise ProductionValidationError("Одна позиция выдачи указана дважды")
    for item in materials:
        source = uow.materials.source(task.fulfillment_request_id, item.fulfillment_line_id)
        if source is None or not item.quantity.is_finite() or item.quantity <= 0 or item.quantity > source.available:
            raise ProductionValidationError("Недостаточно доступного остатка позиции выдачи этого КБ")
    return materials


def _submit(group, members, work, session, payload, actor, worker, now, key, uow):
    if session.status is not WorkStatus.ACTIVE:
        raise ProductionValidationError("Сначала закончите простой группы")
    rows = payload["rows"]
    selected = {row["member_id"]: row for row in rows}
    known = {str(member.id): (member, task, op) for member, task, op in members}
    if len(selected) != len(rows) or set(selected) - known.keys():
        raise ProductionValidationError("Выберите разные КБ этой группы")
    positive = []
    for identifier, row in selected.items():
        good, defect = Decimal(row["good_quantity"]), Decimal(row["defect_quantity"])
        if not good.is_finite() or not defect.is_finite() or good < 0 or defect < 0:
            raise ProductionValidationError("Укажите неотрицательные количества")
        if good + defect:
            positive.append((known[identifier], row, good, defect))
        elif row.get("materials"):
            raise ProductionValidationError("Материалы указывают только для сданных КБ")
    if not positive:
        raise ProductionValidationError("Укажите количество хотя бы для одного КБ")
    elapsed = session.running_seconds(now)
    interval = max(0, elapsed - work.accounted_seconds)
    remaining = interval
    # Allocate only the interval since the previous submission, once across its outputs.
    weights = [max(Decimal(1), op.seconds_per_unit * (good + defect)) for (_, _, op), _, good, defect in positive]
    total_weight = sum(weights)
    results = []
    for index, ((member, task, op), row, good, defect) in enumerate(positive):
        if task.status is not ProductionStatus.RUNNING:
            raise ProductionValidationError("КБ уже закрыта")
        seconds = remaining if index == len(positive) - 1 else int(interval * weights[index] / total_weight)
        remaining -= seconds
        command = SubmitWorkCommand(session.id, actor, now, good, defect,
            row.get("defect_character", ""), row.get("defect_description", ""), performer=worker,
            idempotency_key=f"cut:{digest([key, str(member.id)])}")
        _, box, output, _ = record_work_output(command, uow, session, task, op,
            materials=_materials(task, row.get("materials", []), uow), close_session=False, running=seconds)
        uow.session.flush()
        uow.session.add(CuttingOutputRow(output_id=output.id, session_id=session.id,
                                        member_id=member.id, action_key=key))
        results.append({"member_id": str(member.id), "output_id": str(output.id),
                        "box_id": str(box.id), "box_number": box.number})
    work.accounted_seconds = elapsed
    if all(uow.outputs.total_for_operation(op.id) >= operation_quantity(task, op) for _, task, op in members):
        uow.work.save(session.complete(now))
        work.closed = True
    return results


def _downtime(group, members, work, session, action, payload, actor, worker, now, key, uow):
    repository = SqlAlchemyDowntimeRepository(uow.session)
    member, task, operation = next(row for row in members if row[2].id == session.operation_id)
    if action == "downtime/start":
        if work.downtime_id or session.status is not WorkStatus.ACTIVE:
            raise ProductionValidationError("У группы уже открыт простой")
        if any(repository.open_for_task(child.id) for _, child, _ in members):
            raise ProductionValidationError("У КБ уже открыт простой — сначала закончите его")
        item = Downtime(id=uuid4(), task_id=task.id, operation_id=operation.id,
            operator_id=worker.id, operator_name=worker.name, reason=DowntimeReason(payload["reason"]),
            comment=payload.get("comment", ""), started_at=now, started_by_id=actor.id,
            started_by_name=actor.name, project=cutting_project_label(task.project, member.row_snapshot.get("document")),
            task_number=group.number,
            item="Группа резки", stage="Заготовки")
        repository.add(item)
        work.downtime_id = item.id
        changed, event = session.pause(now), WorkEvent.PAUSE
    else:
        item = repository.get(work.downtime_id) if work.downtime_id else None
        if item is None:
            raise ProductionValidationError("У группы нет открытого простоя")
        repository.close(item.close(now, actor.id, actor.name))
        work.downtime_id = None
        changed, event = session.resume(now), WorkEvent.RESUME
    uow.work.save(changed)
    _log(uow, SessionCommand(session.id, actor, now, performer=worker, idempotency_key=f"cut:{digest(key)}"),
         changed, event, task, operation)


def execute_group(group_id, payload, action, key, actor, worker, now, uow, *, current_cards=None):
    with uow:
        initial = uow.cutting.get(group_id)
        if initial is None:
            raise KeyError("Группа резки не найдена")
        lock_reflow_projects(uow, (), now, projects=(initial.project_key,))
        initial = uow.cutting.get(group_id, lock=True)
        check_access(initial, uow, actor)
        members = _members(initial, worker, uow)
        fingerprint = digest([action, payload])
        replay = uow.session.get(CuttingActionRow, key)
        if replay:
            if replay.group_id != group_id or replay.operator_id != worker.id or replay.fingerprint != fingerprint:
                raise ConcurrentProductionUpdate("Ключ повтора использован другим действием")
            # Keep the recorded outcome, but display migrated numbers and the issued project spelling.
            result = deepcopy(replay.result)
            result["group"].update(number=initial.number, project=describe_group(initial, uow)["project"])
            return result
        group = locked_group(group_id, payload["version"], uow, actor, now)
        if action == "start" and group.state == "open":
            if any(member.task_id not in (current_cards or {}) or current_cards[member.task_id][1] !=
                   member.row_snapshot.get("validated_card_version") or row_behind(member, task)
                   for member, task, _ in members):
                raise ConcurrentProductionUpdate("ЭТК группы изменилась — требуется повторная проверка группы резки")
        entry = CuttingActionRow(key=key, group_id=group.id, operator_id=worker.id, fingerprint=fingerprint, result={})
        uow.session.add(entry)
        uow.session.flush()
        outputs = []
        if action == "start":
            _start(group, members, actor, worker, now, key, uow)
        else:
            work = uow.cutting.work(group.id)
            session = uow.work.get(work.session_id) if work else None
            if session is None or session.operator_id != worker.id:
                raise ProductionValidationError("Группа не начата этим исполнителем")
            if action == "submit":
                outputs = _submit(group, members, work, session, payload, actor, worker, now, key, uow)
            else:
                _downtime(group, members, work, session, action, payload, actor, worker, now, key, uow)
        group.version += 1
        audit(group, action.replace("/", "_"), uow, actor, now, operator_id=str(worker.id), key=key)
        uow.session.flush()
        result = {"group": describe_group(group, uow), "outputs": outputs}
        entry.result = result
        uow.commit()
        return result
