"""Group controls use child assignment/execution; no synthetic quantities or resource claims."""

from uuid import uuid4

from app.shared.events import AuditEvent
from .application import AssignProductionCommand, ConcurrentProductionUpdate, assign_task, lock_reflow_projects
from .domain import ProductionValidationError
from .cutting import cutting_recipe


def planning_groups(uow, new_tasks, free, site):
    """The same recipe key and late-join rule as issue, expressed in preview operation IDs."""
    groups = {}
    joinable = {}
    if getattr(uow, "cutting", None):
        for group in uow.cutting.list():
            members = uow.cutting.members(group.id)
            identifiers = [member.operation_id for member in members]
            if group.state != "open" or not identifiers or not set(identifiers) <= free:
                continue
            groups[str(group.id)] = identifiers
            if group.joinable and all(not uow.production.get(m.task_id).operation(m.operation_id).operator_ids for m in members):
                joinable.setdefault(group.recipe_key, str(group.id))
    for new in sorted(new_tasks.values(), key=lambda n: (n.card.project, n.card.index)):
        recipe, _ = cutting_recipe(new.card.project, site, new.card.document, new.task.operations)
        if recipe and recipe.operation_id in free:
            key = joinable.get(recipe.key, recipe.key)
            groups.setdefault(key, []).append(recipe.operation_id)
    return tuple(tuple(identifiers) for identifiers in groups.values() if len(identifiers) > 1)


def check_access(group, uow, actor):
    viewer = uow.operators.get(actor.id)
    site = viewer.site if viewer else ""
    for member in uow.cutting.members(group.id, active=False):
        task = uow.production.get(member.task_id)
        sites = {group.site, task.handed_to} - {""}
        for identifier in task.operation(member.operation_id).operator_ids:
            operator = uow.operators.get(identifier)
            if operator and operator.site:
                sites.add(operator.site)
        if site and any(other != site for other in sites):
            raise PermissionError("Группа содержит работу другой площадки")


def locked_group(group_id, version, uow, actor, now):
    initial = uow.cutting.get(group_id)
    if initial is None:
        raise KeyError("Группа резки не найдена")
    # Follow the existing resource/project/task locking order, including the possible reflow scope.
    lock_reflow_projects(uow, (), now, projects=(initial.project_key,))
    group = uow.cutting.get(group_id, lock=True)
    if group.version != version:
        raise ConcurrentProductionUpdate("Группа изменилась — обновите список")
    if group.state == "cancelled":
        raise ProductionValidationError("Группа отменена")
    for task_id in sorted({m.task_id for m in uow.cutting.members(group.id, active=False)}, key=str):
        uow.production.lock(task_id)
    check_access(group, uow, actor)
    return group


def audit(group, action, uow, actor, now, **details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=group.id, event_type=f"production.cutting_{action}",
                            actor=actor, occurred_at=now, details=details))


def assign_group(group_id, version, operator_ids, requested_start, bindings, actor, now, uow):
    with uow:
        group = locked_group(group_id, version, uow, actor, now)
        if group.state != "open":
            raise ProductionValidationError("Начатую группу нельзя переназначить целиком")
        members = uow.cutting.members(group.id)
        if not members or not operator_ids:
            raise ProductionValidationError("Выберите исполнителей группы")
        sites = {operator.site for identifier in operator_ids if (operator := uow.operators.get(identifier))}
        if len(sites) != 1 or (group.site and sites != {group.site}):
            raise ProductionValidationError("Исполнители группы должны работать на одной площадке")
        if set(bindings) - {m.operation_id for m in members}:
            raise ProductionValidationError("Оборудование указано для чужой строки")
        cursor = requested_start
        for member in members:
            task = uow.production.get(member.task_id)
            _, plan = assign_task(AssignProductionCommand(task_id=task.id, expected_version=task.version,
                operation_id=member.operation_id, operator_ids=tuple(operator_ids), requested_start=cursor,
                actor=actor, now=now, machine_bindings=bindings.get(member.operation_id)), uow)
            cursor = plan.finish
        audit(group, "assigned", uow, actor, now, operators=[str(i) for i in operator_ids])
        result = describe_group(group, uow)
        uow.commit()
        return result


def split_group(group_id, version, member_ids, actor, now, uow, *, cancel=False):
    with uow:
        group = locked_group(group_id, version, uow, actor, now)
        if group.state != "open":
            raise ProductionValidationError("Состав группы зафиксирован первым началом работы")
        members = uow.cutting.members(group.id)
        selected = set(member_ids) if not cancel else {m.id for m in members}
        if not selected or not selected <= {m.id for m in members}:
            raise ProductionValidationError("Выберите целые строки этой группы")
        for member in members:
            if member.id in selected:
                # Split means standalone work; keep all child assignments and quantities intact.
                member.state = "split"
        group.version += 1
        if cancel or selected == {m.id for m in members}:
            group.state = "cancelled"
        audit(group, "cancelled" if cancel else "split", uow, actor, now, members=[str(i) for i in selected])
        result = describe_group(group, uow)
        uow.commit()
        return result


def revalidate_group(group_id, version, current, actor, now, uow):
    with uow:
        group = locked_group(group_id, version, uow, actor, now)
        if group.state != "open":
            raise ProductionValidationError("Начатая группа работает по закреплённому снимку ЭТК")
        for member in uow.cutting.members(group.id):
            task = uow.production.get(member.task_id)
            document, card_version = current.get(task.id, (None, None))
            recipe, reason = cutting_recipe(task.project, group.site, document, task.operations)
            if recipe is None or recipe.key != group.recipe_key or any(
                member.row_snapshot.get(key) != value for key, value in recipe.row.items()
            ):
                raise ProductionValidationError(f"{task.item}: параметры изменились; выдайте новую работу отдельно. {reason}")
            member.row_snapshot = {**member.row_snapshot, "validated_card_version": card_version}
        group.version += 1
        audit(group, "revalidated", uow, actor, now)
        result = describe_group(group, uow)
        uow.commit()
        return result


def describe_group(group, uow):
    members = uow.cutting.members(group.id, active=False)
    task_ids = tuple({m.task_id for m in members})
    outputs = uow.outputs.active_for_tasks(task_ids)
    rows = []
    for member in members:
        task = uow.production.get(member.task_id)
        operation = task.operation(member.operation_id)
        own = [item for item in outputs if item.operation_id == operation.id]
        boxes = [box for box in uow.boxes.list(task_id=task.id, limit=10000) if box.operation_id == operation.id]
        good = sum(item.good_quantity for item in own)
        defects = sum(item.defect_quantity for item in own)
        target = task.operation_units(operation.id)
        state = (member.state if member.state != "active" else "done" if good >= target else
                 "started" if own or any(s.started_at for s in operation.shares) else
                 "assigned" if operation.operator_ids else "waiting")
        rows.append({"id": str(member.id), "task_id": str(task.id), "operation_id": str(operation.id),
                     "task_version": task.version, "task_number": task.number, "project": task.project,
                     "item": task.item, "name": task.name, "position": member.position,
                     "assembly_quantity": member.assembly_quantity, "blank_quantity": str(member.blank_quantity),
                     "operation_quantity": str(target), **{k: v for k, v in member.row_snapshot.items()
                                                            if k not in {"document", "validated_card_version"}},
                     "good": str(good), "defects": str(defects), "state": state, "membership_state": member.state,
                     "operator_ids": [str(i) for i in operation.operator_ids],
                     "planned_start": operation.planned_start.isoformat() if operation.planned_start else None,
                     "planned_finish": operation.planned_finish.isoformat() if operation.planned_finish else None,
                     "norm_seconds": operation.norm_seconds,
                     "boxes": [{"id": str(b.id), "number": b.number, "quantity": str(b.quantity)} for b in boxes],
                     "etk_url": f"/api/production/my/tasks/{task.id}/operations/{operation.id}/etk"})
    active = [row for row in rows if row["membership_state"] == "active"]
    starts = [row["planned_start"] for row in active if row["planned_start"]]
    finishes = [row["planned_finish"] for row in active if row["planned_finish"]]
    return {"id": str(group.id), "number": group.number, "version": group.version,
            "recipe_version": group.recipe_version, "recipe_key": group.recipe_key,
            "project": group.project_key, "site": group.site, "state": group.state,
            "membership_frozen": group.state == "frozen", "rows": rows,
            "planned_start": min(starts) if starts else None, "planned_finish": max(finishes) if finishes else None,
            "norm_seconds": sum(row["norm_seconds"] for row in active)}
