"""Group controls use child assignment/execution; no synthetic quantities or resource claims."""

from uuid import uuid4

from app.shared.events import AuditEvent
from .application import AssignProductionCommand, ConcurrentProductionUpdate, _checked_names, assign_task, lock_reflow_projects
from .domain import ProductionStatus, ProductionValidationError
from .cutting import cutting_recipe
from .plan import normalize_project_number


def common_assignment(rows):
    """Group metadata never overwrites individual reservations or assignment windows."""
    assignees = {tuple(sorted(row["operator_ids"])) for row in rows}
    windows = {(row["planned_start"], row["planned_finish"]) for row in rows}
    same_people, same_window = len(assignees) == 1, len(windows) == 1
    return {"operator_ids": list(next(iter(assignees))) if same_people else [],
            "assignment_consistent": same_people, "planning_consistent": same_window,
            "common_planned_start": rows[0]["planned_start"] if same_window else None,
            "common_planned_finish": rows[0]["planned_finish"] if same_window else None}


def _existing_candidate(task, load_card, actor, uow):
    if not task.operations:
        raise ProductionValidationError("Нет первого ПФ")
    first = min(task.operations, key=lambda op: op.position)
    sites = {task.handed_to} - {""}
    for identifier in first.operator_ids:
        operator = uow.operators.get(identifier)
        if operator is None or not operator.site:
            raise ProductionValidationError("Не определена площадка исполнителя")
        sites.add(operator.site)
    if not sites:
        sites = {op.site for op in uow.operators.list() if op.name == task.launched_by and op.site}
    viewer = uow.operators.get(actor.id)
    if viewer and viewer.site and any(site != viewer.site for site in sites):
        raise PermissionError("Задание другой площадки")
    if len(sites) != 1:
        raise ProductionValidationError("Не определена единая площадка ПФ")
    site = next(iter(sites))
    if task.status is not ProductionStatus.RUNNING:
        raise ProductionValidationError("Задание не запущено или уже закрыто")
    if uow.cutting.for_operation(first.id):
        return None
    reason = uow.cutting.execution_reason(first)
    if reason:
        raise ProductionValidationError(reason)
    document, version = load_card(task)
    recipe, reason = cutting_recipe(task.project, site, document, task.operations)
    if recipe is None:
        raise ProductionValidationError(reason)
    if [op.code for op in first.etk_ops] != [op["code"] for op in recipe.snapshot["operations"]]:
        raise ProductionValidationError("Операции первого ПФ расходятся с выданной ЭТК")
    return task, recipe, document, version


def _existing_proposal(candidates):
    first = candidates[0][1]
    members = []
    for task, recipe, _document, _version in candidates:
        operation = task.operation(recipe.operation_id)
        members.append({"task_id": str(task.id), "task_number": task.number, "item": task.item,
            "operation_id": str(operation.id), "quantity": task.quantity, **recipe.row,
            "operator_ids": [str(identifier) for identifier in operation.operator_ids],
            "planned_start": operation.planned_start.isoformat() if operation.planned_start else None,
            "planned_finish": operation.planned_finish.isoformat() if operation.planned_finish else None})
    assignment = common_assignment(members)
    warnings = []
    if not assignment["assignment_consistent"]:
        warnings.append("Исполнители различаются: общий исполнитель не назначен; назначения строк сохранены")
    if not assignment["planning_consistent"]:
        warnings.append("Плановые окна различаются: общее окно не задано; планы строк сохранены")
    return {"recipe_key": first.key, "recipe_version": first.snapshot["version"],
            "project": first.snapshot["project"], "site": first.snapshot["site"],
            "recipe_summary": {"cable": first.snapshot["cable"],
                "cables_per_assembly": first.snapshot["cables_per_assembly"],
                "operations": [op["code"] for op in first.snapshot["operations"]],
                "settings": [{"code": op["code"], "parameters": op["parameters"], "fields": op["fields"],
                              "tooling": op["card"]["tooling"]} for op in first.snapshot["operations"]]},
            "members": members, "warnings": warnings, **assignment}


def group_existing(project, task_ids, dry_run, load_card, actor, now, factory):
    """Discover without writes, then recheck and create each recipe in its own transaction."""
    project = normalize_project_number(project)
    if not project:
        raise ProductionValidationError("Укажите проект")
    result = {"project": project, "dry_run": dry_run, "groups": [], "created": [], "refused": [], "skipped": []}
    buckets = {}

    def collect(task, uow):
        identity = {"task_id": str(task.id), "task_number": task.number, "item": task.item}
        try:
            candidate = _existing_candidate(task, load_card, actor, uow)
        except ProductionValidationError as error:
            result["refused"].append({**identity, "reason": str(error)})
            return None
        if candidate is None:
            result["skipped"].append({**identity, "reason": "Первый ПФ уже в группе"})
        return candidate

    with factory() as uow:
        tasks = uow.production.for_project(project)
        if task_ids is not None:
            selected = set(task_ids)
            if not selected <= {task.id for task in tasks}:
                raise ProductionValidationError("Выбранное задание не найдено в проекте")
            tasks = [task for task in tasks if task.id in selected]
        for task in tasks:
            candidate = collect(task, uow)
            if candidate:
                buckets.setdefault(candidate[1].key, []).append(candidate)
        if dry_run:
            result["groups"] = [_existing_proposal(candidates) for candidates in buckets.values()]
            return result

    for key, candidates in buckets.items():
        with factory() as uow:
            uow.production.lock_projects(projects=(project,))
            for identifier in sorted((item[0].id for item in candidates), key=str):
                uow.production.lock(identifier)
            current = []
            for old_task, _recipe, _document, _version in candidates:
                task = uow.production.get(old_task.id)
                if task is None or normalize_project_number(task.project) != project:
                    raise ConcurrentProductionUpdate("Задание изменилось — повторите группировку")
                candidate = collect(task, uow)
                if candidate:
                    if candidate[1].key != key:
                        raise ConcurrentProductionUpdate("Рецепт изменился — повторите группировку")
                    current.append(candidate)
            if not current:
                continue
            group = uow.cutting.create_existing(current, now)
            audit(group, "from_existing", uow, actor, now, tasks=[str(item[0].id) for item in current])
            proposal = {**_existing_proposal(current), "group_id": str(group.id), "number": group.number}
            uow.commit()
            result["groups"].append(proposal)
            result["created"].append(str(group.id))
    return result


def planning_groups(uow, new_tasks, free, site, team, actor):
    """The same recipe key and late-join rule as issue, expressed in preview operation IDs."""
    groups = {}
    joinable = {}
    operator_options = {}
    if getattr(uow, "cutting", None):
        for group in uow.cutting.list():
            members = uow.cutting.members(group.id)
            identifiers = [member.operation_id for member in members]
            try:
                check_access(group, uow, actor)
                accessible = not site or not group.site or site == group.site
            except PermissionError:
                accessible = False
            for member in members:
                if member.operation_id not in free:
                    continue
                operation = uow.production.get(member.task_id).operation(member.operation_id)
                allowed = set()
                for operator_id in team if accessible else ():
                    try:
                        _checked_names(uow, operation, (operator_id,), actor)
                    except ProductionValidationError:
                        continue
                    allowed.add(operator_id)
                operator_options[operation.id] = frozenset(allowed)
            if not accessible:
                continue
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
            operator_options[recipe.operation_id] = frozenset(
                identifier for identifier in team if not site or uow.operators.get(identifier).site == site)
    return tuple(tuple(identifiers) for identifiers in groups.values() if len(identifiers) > 1), operator_options


def check_access(group, uow, actor):
    viewer = uow.operators.get(actor.id)
    site = viewer.site if viewer else ""
    if site and group.site and site != group.site:
        raise PermissionError("Группа содержит работу другой площадки")
    for member in uow.cutting.members(group.id):
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
            **common_assignment(active),
            "planned_start": min(starts) if starts else None, "planned_finish": max(finishes) if finishes else None,
            "norm_seconds": sum(row["norm_seconds"] for row in active)}
