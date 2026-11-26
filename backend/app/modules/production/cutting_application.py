"""Group controls use child assignment/execution; no synthetic quantities or resource claims."""

from collections import Counter
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from .application import AssignProductionCommand, ConcurrentProductionUpdate, _audit, _checked_names, assign_task, lock_reflow_projects
from .domain import ProductionStatus, ProductionValidationError, restructured_operations
from .cutting import (KIND_NAMES, TUBES, cutting_project_label, cutting_recipes, number, recipe_for_operation,
                      recipe_kind)
from .plan import normalize_project_number


PARKED = 100000  # temporary stage numbers while a task's PFs are rebuilt in place


def common_assignment(rows):
    """Group metadata never overwrites individual reservations or assignment windows."""
    assignees = {tuple(sorted(row["operator_ids"])) for row in rows}
    windows = {(row["planned_start"], row["planned_finish"]) for row in rows}
    same_people, same_window = len(assignees) == 1, len(windows) == 1
    return {"operator_ids": list(next(iter(assignees))) if same_people else [],
            "assignment_consistent": same_people, "planning_consistent": same_window,
            "common_planned_start": rows[0]["planned_start"] if same_window else None,
            "common_planned_finish": rows[0]["planned_finish"] if same_window else None}


def _site(task, operation, uow, actor):
    """The PF's site: its assignees and the task handoff; unassigned — the launching employee's site."""
    sites = {task.handed_to} - {""}
    for identifier in operation.operator_ids:
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
    return next(iter(sites))


def _existing_candidates(task, load_card, actor, uow):
    """Every ungrouped, unstarted preparation PF of the task (cable cut, labels, tubes) with its recipe.
    Returns (candidates, refusal reasons, number of PFs already grouped)."""
    if not task.operations:
        raise ProductionValidationError("Нет первого ПФ")
    first = min(task.operations, key=lambda op: op.position)
    site = _site(task, first, uow, actor)
    if task.status is not ProductionStatus.RUNNING:
        raise ProductionValidationError("Задание не запущено или уже закрыто")
    if uow.cutting.for_operation(first.id) and not any(
            not op.inputs and op is not first and not uow.cutting.for_operation(op.id) for op in task.operations):
        return [], [], 1
    document, version = load_card(task)
    recipes, refused = cutting_recipes(task.project, site, document, task.operations)
    if not recipes and not refused:
        raise ProductionValidationError("Нет начального ПФ резки без входов")
    reasons = [reason for _operation, reason in refused]
    candidates, grouped = [], 0
    for recipe in recipes:
        operation = task.operation(recipe.operation_id)
        if uow.cutting.for_operation(operation.id):
            grouped += 1
            continue
        reason = uow.cutting.execution_reason(operation)
        if not reason and operation is not first and _site(task, operation, uow, actor) != site:
            reason = "ПФ задания на разных площадках"
        if not reason and [op.code for op in operation.etk_ops] != [op["code"] for op in recipe.snapshot["operations"]]:
            reason = "Операции ПФ расходятся с выданной ЭТК"
        if reason:
            reasons.append(reason)
        else:
            candidates.append((task, recipe, document, version))
    return candidates, reasons, grouped


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
    return {"recipe_key": first.key, "recipe_version": first.snapshot["version"], "kind": first.kind,
            "kind_name": KIND_NAMES[first.kind],
            "project": cutting_project_label(candidates[0][0].project, candidates[0][2]), "site": first.snapshot["site"],
            "recipe_summary": {"cable": first.snapshot["cable"],
                "cables_per_assembly": first.snapshot["cables_per_assembly"],
                "operations": [op["code"] for op in first.snapshot["operations"]],
                "settings": [{"code": op["code"], "parameters": op["parameters"], "fields": op["fields"],
                              "tooling": first.tooling[i] if i < len(first.tooling) else op["card"]["tooling"]}
                             for i, op in enumerate(first.snapshot["operations"])]},
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
            candidates, reasons, grouped = _existing_candidates(task, load_card, actor, uow)
        except ProductionValidationError as error:
            result["refused"].append({**identity, "reason": str(error)})
            return []
        if reasons and not candidates:
            result["refused"].append({**identity, "reason": "; ".join(dict.fromkeys(reasons))})
        elif grouped and not candidates:
            result["skipped"].append({**identity, "reason": "Первый ПФ уже в группе"})
        return candidates

    with factory() as uow:
        tasks = uow.production.for_project(project)
        if task_ids is not None:
            selected = set(task_ids)
            if not selected <= {task.id for task in tasks}:
                raise ProductionValidationError("Выбранное задание не найдено в проекте")
            tasks = [task for task in tasks if task.id in selected]
        for task in tasks:
            for candidate in collect(task, uow):
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
            for old_task, old_recipe, _document, _version in candidates:
                task = uow.production.get(old_task.id)
                if task is None or normalize_project_number(task.project) != project:
                    raise ConcurrentProductionUpdate("Задание изменилось — повторите группировку")
                try:
                    found, reasons, _grouped = _existing_candidates(task, load_card, actor, uow)
                except ProductionValidationError as error:
                    found, reasons = [], [str(error)]
                candidate = next((item for item in found if item[1].operation_id == old_recipe.operation_id), None)
                if candidate is None and not uow.cutting.for_operation(old_recipe.operation_id):
                    result["refused"].append({"task_id": str(task.id), "task_number": task.number, "item": task.item,
                                              "reason": "; ".join(dict.fromkeys(reasons)) or "ПФ изменился"})
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
        recipes, _ = cutting_recipes(new.card.project, site, new.card.document, new.task.operations)
        for recipe in recipes:
            if recipe.operation_id not in free:
                continue
            key = joinable.get(recipe.key, recipe.key)
            groups.setdefault(key, []).append(recipe.operation_id)
            operator_options[recipe.operation_id] = frozenset(
                identifier for identifier in team if not site or uow.operators.get(identifier).site == site)
    return tuple(tuple(identifiers) for identifiers in groups.values() if len(identifiers) > 1), operator_options


def check_access(group, uow, actor):
    viewer = uow.operators.get(actor.id)
    site = viewer.site if viewer else ""
    check_group_sites(group, site, uow.cutting.members(group.id), uow.production.get, uow.operators.get)


def check_group_sites(group, site, members, task_of, operator_of):
    """The whole-group site rule of `check_access` over already-read data (the queue checks every visible group in
    one pass): a viewer with a site sees a group only when the group, each active member's task (handed to) and each
    member's assignees are all of that site; a viewer without a site sees every group (the queue then passes no
    members). `members` — the group's active members (`task_id`, `operation_id`); `task_of`/`operator_of` — lookups by
    id (an inactive operator included)."""
    if site and group.site and site != group.site:
        raise PermissionError("Группа содержит работу другой площадки")
    for member in members:
        task = task_of(member.task_id)
        sites = {group.site, task.handed_to} - {""}
        for identifier in task.operation(member.operation_id).operator_ids:
            operator = operator_of(identifier)
            if operator and operator.site:
                sites.add(operator.site)
        if site and any(other != site for other in sites):
            raise PermissionError("Группа содержит работу другой площадки")


def locked_group(group_id, version, uow, actor, now, *, project_groups=False):
    """Lock in the one order every cutting path uses: project → groups (by id) → tasks (by id). `project_groups` —
    every group of the project (a refresh may move rows between them), otherwise only this one."""
    initial = uow.cutting.get(group_id)
    if initial is None:
        raise KeyError("Группа резки не найдена")
    # Follow the existing resource/project/task locking order, including the possible reflow scope.
    lock_reflow_projects(uow, (), now, projects=(initial.project_key,))
    if project_groups:
        lock_groups(uow, initial.project_key)
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


def _baseline_key(group, member, task):
    """The member's pinned recipe key computed with the CURRENT code (the stored group key may come from older code)."""
    pinned = member.row_snapshot.get("document")
    if pinned:
        recipe, _ = recipe_for_operation(task.project, group.site, pinned, task.operations, member.operation_id)
        if recipe is not None:
            return recipe.key
    return group.recipe_key


def _adopt_key(group, recipe):
    group.recipe_key, group.recipe, group.recipe_version = recipe.key, recipe.snapshot, recipe.snapshot["version"]


def _keeps_key(group, key, moving, uow):
    """Whether the group may take recipe `key`: every active row of it outside `moving` (PF ids regrouped now) already
    has that key — rows of tasks whose rebuild was refused, outside the selection or not running keep their recipe."""
    if group.recipe_key == key:
        return True
    return all(_baseline_key(group, member, uow.production.get(member.task_id)) == key
               for member in uow.cutting.members(group.id) if member.operation_id not in moving)


def issued_separately(group):
    """A group of a launch made with «Выдать отдельно» (`attach(separate=True)`): the master's separation — regrouping
    never moves its PF elsewhere and never puts other PFs into it. Explicit groups (from existing work, regrouping) are
    not joinable either but carry the `cutting-existing:` key."""
    return not group.joinable and str(group.idempotency_key).startswith("cutting:")


SPLIT_BY = "split_by"  # row_snapshot marker of a split the system made (rebuild, regroup), not the master


def _system_split(member, reason):
    member.state = "split"
    member.row_snapshot = {**member.row_snapshot, SPLIT_BY: reason}


def _master_splits(operation_id, uow):
    """Rows the master split off (split on the group card, or the group cancelled); splits made by a rebuild or a
    regroup carry `split_by` and do not count."""
    from sqlalchemy import select

    from .cutting_infrastructure import CuttingMemberRow

    rows = uow.session.scalars(select(CuttingMemberRow).where(
        CuttingMemberRow.operation_id == operation_id, CuttingMemberRow.state == "split"))
    return [row for row in rows if not (row.row_snapshot or {}).get(SPLIT_BY)]


def _separated(task, operation_id, key, uow):
    """The master took this ungrouped PF out of a group and its recipe is still that row's recipe: regrouping honours
    the separation."""
    return any(_baseline_key(uow.cutting.get(row.group_id), row, task) == key for row in _master_splits(operation_id, uow))


def revalidate_group(group_id, version, current, actor, now, uow):
    with uow:
        group = locked_group(group_id, version, uow, actor, now)
        if group.state != "open":
            raise ProductionValidationError("Начатая группа работает по закреплённому снимку ЭТК")
        checked = []
        for member in uow.cutting.members(group.id):
            task = uow.production.get(member.task_id)
            if row_behind(member, task):
                # The task was re-pinned to a newer ETK while the row keeps its frozen one: acknowledging the card
                # version alone would let the group start and then refuse every later change of the task.
                raise ProductionValidationError(
                    f"{task.item}: задание перекреплено к другой ревизии ЭТК, а строка группы работает по прежней — "
                    "нажмите «Обновить из ЭТК»")
            document, card_version = current.get(task.id, (None, None))
            recipe, reason = recipe_for_operation(task.project, group.site, document, task.operations, member.operation_id)
            if recipe is None or recipe.key != _baseline_key(group, member, task) or any(
                member.row_snapshot.get(key) != value for key, value in recipe.row.items()
            ):
                raise ProductionValidationError(f"{task.item}: параметры изменились; выдайте новую работу отдельно. {reason}")
            member.row_snapshot = {**member.row_snapshot, "validated_card_version": card_version}
            checked.append(recipe)
        if checked and len({recipe.key for recipe in checked}) != 1:
            raise ProductionValidationError("Рецепт резки у КБ группы расходится; выдайте новую работу отдельно")
        if checked:
            _adopt_key(group, checked[0])
        group.version += 1
        audit(group, "revalidated", uow, actor, now)
        result = describe_group(group, uow)
        uow.commit()
        return result


def plan_etk_refresh(group, members, cards, uow):
    """Per member: its task, the current card entry and the recipe it yields; plus problems that block the refresh.

    `cards` maps task id -> (card_id, revision, document, card_version, norms) or an error text. A member whose new
    recipe differs from the group is not a problem any more: the refresh moves it to the group of its recipe.
    norms None — the PF composition changed: the step carries recipe None and the refresh rebuilds that task in place
    (`rebuild_task`, the same rules as the CLI restructure).
    """
    steps, problems = [], []
    for member in members:
        task = uow.production.get(member.task_id)
        entry = cards.get(task.id)
        if entry is None or isinstance(entry, str):
            problems.append(f"{task.item}: {entry or 'нет выданной ЭТК'}")
            continue
        if needs_rebuild(entry):
            steps.append((member, task, entry, None))
            continue
        document = entry[2]
        recipe, reason = recipe_for_operation(task.project, group.site, document, task.operations, member.operation_id)
        if recipe is None:
            problems.append(f"{task.item}: {reason}")
        else:
            steps.append((member, task, entry, recipe))
    return steps, problems


def needs_rebuild(entry):
    """A refresh card entry whose PF composition changed (norms None): the task is rebuilt in place, not re-pinned."""
    return not isinstance(entry, str) and len(entry) > 4 and entry[4] is None


def row_behind(member, task):
    """The task was re-pinned (`tasks/{id}/norms/refresh`) to another ETK revision than the row's frozen one: the row
    must be moved by «Обновить из ЭТК» before the group may start."""
    return task.techcard_revision != member.row_snapshot.get("techcard_revision")


def _stale(member, task, entry):
    _card_id, revision, _document, card_version, _norms = entry
    return (task.techcard_revision != revision or member.row_snapshot.get("validated_card_version") != card_version
            or row_behind(member, task))


def refresh_group_etk(group_id, version, cards, actor, now, uow):
    """Re-pin every active member to its current issued ETK in one transaction. Members keeping one recipe stay in the
    group (its id and number; the most common new recipe keeps it); a member whose recipe now differs moves to an open
    unstarted group of that recipe in the project, or to a new one (owner 20.11 — instead of refusing the refresh).
    A member whose PF composition changed (`needs_rebuild`) has its task rebuilt in place by the same `rebuild_task` as
    the CLI restructure, and all its unstarted preparation PFs are regrouped; any refusal writes nothing."""
    with uow:
        group = locked_group(group_id, version, uow, actor, now, project_groups=True)
        if group.state != "open" or uow.cutting.work(group.id):
            raise ProductionValidationError("Начатая группа работает по закреплённому снимку ЭТК — обновить её нельзя")
        members = uow.cutting.members(group.id)
        steps, problems = plan_etk_refresh(group, members, cards, uow)
        if problems:
            raise ProductionValidationError(
                "Группа не обновлена: у этих КБ новая ЭТК не даёт рецепта резки — " + "; ".join(problems)
                + ". Исправьте ЭТК или выдайте их отдельно («Выдать отдельно») и обновите остальные.")
        rebuild = list({step[1].id: step for step in steps if step[3] is None}.values())
        steps = [step for step in steps if step[3] is not None]
        todo = [item for item in steps if _stale(*item[:3])]
        if not rebuild and not todo and {step[3].key for step in steps} <= {group.recipe_key}:
            return describe_group(group, uow)
        homes, origins, rebuilt_entries, rebuilt = {}, {}, [], []
        for _member, task, entry, _recipe in sorted(rebuild, key=lambda step: (step[1].item, step[1].number)):
            updated, reason = rebuild_task(task, entry, uow, actor, now, homes, origins)
            if reason:
                raise ProductionValidationError(f"Группа не обновлена: {task.item}: {reason}")
            found, refused = task_regroup_entries(updated, entry[2], entry[3], uow, actor, homes, origins)
            if not found:
                raise ProductionValidationError(
                    f"Группа не обновлена: у {task.item} новая ЭТК не даёт рецепта резки — "
                    + ("; ".join(refused) or "нет ПФ подготовки") + ".")
            rebuilt_entries.extend(found)
            rebuilt.append({"task_id": str(updated.id), "item": updated.item, "refused": refused})
        tasks = {}
        for member, task, entry, recipe in todo:
            card_id, revision, _document, _card_version, norms = entry
            task = tasks.get(task.id) or uow.production.get(task.id)
            if task.techcard_revision != revision:
                was = task.techcard_revision
                task = task.refresh_norms(norms, card_id, revision)
                uow.production.add(task)
                _audit(task, "production.norms_refreshed", actor, now, {
                    "techcard_id": str(card_id), "from_revision": was, "revision": revision,
                    "cutting_group_id": str(group.id)}, uow)
            tasks[task.id] = task
        entries = [(tasks.get(task.id) or task, recipe, entry[2], entry[3]) for _member, task, entry, recipe in steps]
        before = group.version
        moved = regroup(entries + rebuilt_entries, uow, actor, now, homes=homes, origins=origins)
        group = uow.cutting.get(group.id)
        if group.version == before:
            group.version += 1
        audit(group, "etk_refreshed", uow, actor, now, tasks=[str(item[1].id) for item in todo],
              moved=moved["moved"], restructured=rebuilt)
        result = describe_group(group, uow)
        uow.commit()
        return result


def lock_groups(uow, project_key):
    """Lock every group of the project (by id) after the project lock and before any task lock: regrouping may move
    members between them."""
    key = normalize_project_number(project_key)
    for group in sorted((g for g in uow.cutting.list() if normalize_project_number(g.project_key) == key),
                        key=lambda g: str(g.id)):
        uow.cutting.get(group.id, lock=True)


def _regroupable(group, recipe, uow):
    return (group.state == "open" and not uow.cutting.work(group.id)
            and normalize_project_number(group.project_key) == recipe.snapshot["project"]
            and group.site == recipe.snapshot["site"] and recipe_kind(group.recipe) == recipe.kind)


def regroup(entries, uow, actor, now, *, homes=None, origins=None):
    """Put unstarted preparation PFs into one open group per recipe key; the caller holds the project, group and task
    locks. `entries` — (task, recipe, document, card version); `homes` — {PF id: group id} for PFs the caller already
    took out of their group (restructure). Per key, the largest set first: it keeps the group that already holds most of
    its PFs (id and number stay), else joins an open unstarted group of the project with that recipe, else gets a new
    group. A group takes a new recipe key only when all its active rows outside `entries` already have it
    (`_keeps_key`); otherwise the PFs go to another group. A PF of a «Выдать отдельно» group stays in that group alone
    (`issued_separately`), and so does every PF a rebuild split out of it (`origins`, {new PF id: old PF id}): each in
    its own separate group, as a separate launch makes; a separate group that cannot take its PF back is replaced by a
    new separate one. A PF leaving a group is `split` there; a group left without active rows is cancelled."""
    homes, origins = dict(homes or {}), dict(origins or {})
    position = {}
    for _task, recipe, _document, _version in entries:
        member = uow.cutting.for_operation(recipe.operation_id)
        if member:
            homes[recipe.operation_id] = member.group_id
            position[recipe.operation_id] = member.position
    moving = {entry[1].operation_id for entry in entries}
    separate = {group_id for group_id in set(homes.values()) if issued_separately(uow.cutting.get(group_id))}

    # Tasks with a PF of a «Выдать отдельно» group (now or taken out of it by this rebuild).
    separate_tasks = {task.id for task, _recipe, _document, _version in entries
                      if any(homes.get(operation.id) in separate for operation in task.operations)}
    task_of = {recipe.operation_id: task.id for task, recipe, _document, _version in entries}

    def alone(operation_id):
        """The separate group this PF must stay in, ("new", PF id) for its own new separate group, or None. A PF of
        unknown lineage (`origins` None) in a separately issued task never joins a shared group either."""
        home = homes.get(operation_id)
        if home in separate:
            return home
        if homes.get(origins.get(operation_id)) in separate:
            return ("new", operation_id)
        if operation_id in origins and origins[operation_id] is None and task_of.get(operation_id) in separate_tasks:
            return ("new", operation_id)
        return None

    buckets = {}
    for entry in entries:
        buckets.setdefault((entry[1].key, alone(entry[1].operation_id)), []).append(entry)
    order = sorted(buckets.items(), key=lambda item: (
        item[0][1] is None, -len(item[1]), min(position.get(entry[1].operation_id, 10 ** 9) for entry in item[1]),
        item[0][0], str(item[0][1])))
    claimed, left, result = set(), set(homes.values()), {"groups": [], "moved": [], "cancelled": []}
    for (key, alone), items in order:
        recipe = items[0][1]
        counts = Counter(homes[e[1].operation_id] for e in items if e[1].operation_id in homes)
        group = None
        for group_id, _count in sorted(counts.items(), key=lambda item: (-item[1], str(item[0]))):
            candidate = uow.cutting.get(group_id)
            if (candidate.id not in claimed and (candidate.id in separate) == (alone is not None)
                    and _regroupable(candidate, recipe, uow) and _keeps_key(candidate, key, moving, uow)):
                group = candidate
                break
        if group is None and alone is None:
            group = next((g for g in uow.cutting.list() if g.id not in claimed and g.recipe_key == key
                          and not issued_separately(g) and _regroupable(g, recipe, uow)), None)
        created = group is None
        if created:
            group = (uow.cutting.create_separate(recipe, now) if alone is not None
                     else uow.cutting.create_explicit(recipe, now))
        claimed.add(group.id)
        changed = group.recipe_key != key
        if changed:
            _adopt_key(group, recipe)
        for task, item_recipe, document, card_version in items:
            member = uow.cutting.for_operation(item_recipe.operation_id)
            if member and member.group_id != group.id:
                _system_split(member, "regroup")
                old = uow.cutting.get(member.group_id)
                old.version += 1
                left.add(old.id)
                result["moved"].append({"item": task.item, "from": old.number, "to": group.number})
            elif not member and homes.get(item_recipe.operation_id) not in (None, group.id):
                result["moved"].append({"item": task.item, "from": uow.cutting.get(homes[item_recipe.operation_id]).number,
                                        "to": group.number})
            changed = uow.cutting.place(group, task, item_recipe, document, card_version) or changed
        if changed and not created:
            group.version += 1
        if changed or created:
            audit(group, "regrouped", uow, actor, now, tasks=[str(e[0].id) for e in items])
        result["groups"].append({"group_id": str(group.id), "number": group.number, "kind": recipe.kind,
                                 "recipe_key": key, "created": created, "items": [e[0].item for e in items]})
    for group_id in sorted(left - claimed, key=str):
        group = uow.cutting.get(group_id)
        if group.state == "open" and not uow.cutting.members(group.id):
            group.state = "cancelled"
            group.version += 1
            audit(group, "cancelled", uow, actor, now, reason="regrouped")
            result["cancelled"].append(group.number)
    return result


def restructure_reason(task, uow, kept=None, operations=(), *, release=False):
    """Why the task's PFs may not be rebuilt in place, or "" (owner 20.11: only before any work or output).
    `kept` — old PF ids the rebuilt route keeps, `operations` — the rebuilt PFs."""
    if task.status is not ProductionStatus.RUNNING:
        return "Задание не в работе — состав ПФ не меняется"
    if uow.production.worked([task.id]):
        return "По заданию уже есть работа, выпуск или коробки — состав ПФ не меняется; запустите новое задание"
    from .cutting_infrastructure import CuttingMemberRow
    from .resource_assignment_infrastructure import ResourceAllocationRow
    from sqlalchemy import select

    for operation in task.operations:
        member = uow.cutting.for_operation(operation.id)
        if member:
            group = uow.cutting.get(member.group_id)
            if group.state != "open" or uow.cutting.work(group.id):
                return f"ПФ «{operation.stage}» в начатой группе резки {group.number} — состав ПФ не меняется"
    if kept is not None:
        dropped = {operation.id for operation in task.operations} - set(kept)
        history = uow.session.scalars(select(CuttingMemberRow.operation_id).where(
            CuttingMemberRow.task_id == task.id, CuttingMemberRow.operation_id.in_(dropped))).first() if dropped else None
        if history is not None:
            return "ПФ с историей группы резки исчезает из новой ЭТК — состав ПФ не меняется"
        allocated = uow.session.scalars(select(ResourceAllocationRow.operation_id).where(
            ResourceAllocationRow.operation_id.in_(dropped))).first() if dropped else None
        if allocated is not None:
            return "У исчезающего ПФ назначено оборудование — снимите назначение и повторите"
        return binding_moves(task, operations, kept, uow, release=release)[0]
    return ""


def binding_moves(task, operations, kept, uow, *, release=False):
    """Equipment bound to ETK operations of the kept PFs (the old «Заготовки» id keeps the cut), followed into the
    rebuilt route. Only current allocations count (one with a current member); retired ones are history and stay as
    they are. Inside the PF an ETK operation is the same when its code and its occurrence among the PF's operations
    with that code match: a reissued ETK that inserts an operation elsewhere shifts the numbers, not the identity.
    Returns (refusal, renumbering [(allocation id, old ETK number, new ETK number)]): a bound operation that leaves the
    PF refuses, and so does one whose code occurs a different number of times in the PF now (which of them is meant is
    not known — the master binds it again); one that stays under a new number is renumbered. A bound number that is not
    an ETK operation of the PF stays allowed while the rebuilt PF still has that number.
    `release` (CLI `--release-moved-equipment`): a bound operation that moved to a NEW rebuilt PF (labels, tubes — not
    a kept one) split out of its own PF (`_parents`) is not a refusal but is returned in `released` [(allocation id, PF
    id, ETK number, code, machine id, new PF id, new ETK number)] for removal; it is found by its occurrence among that
    code's operations of those new PFs,
    and a different count there, a code only in kept PFs or a disappearing operation still refuses. Renumbering that
    would put two bindings of one allocation on one number refuses too (the master binds them again).
    Returns (refusal, renumbering, released)."""
    from sqlalchemy import select

    from .resource_assignment_infrastructure import (ResourceAllocationMachineRow, ResourceAllocationMemberRow,
                                                     ResourceAllocationRow)

    kept_ids = [operation.id for operation in operations if operation.id in set(kept)]
    current = select(ResourceAllocationMemberRow.allocation_id).where(ResourceAllocationMemberRow.current.is_(True))
    rows = uow.session.execute(select(
        ResourceAllocationRow.id, ResourceAllocationRow.operation_id, ResourceAllocationMachineRow.etk_position,
        ResourceAllocationMachineRow.machine_id)
        .join(ResourceAllocationMachineRow, ResourceAllocationMachineRow.allocation_id == ResourceAllocationRow.id)
        .where(ResourceAllocationRow.operation_id.in_(kept_ids), ResourceAllocationRow.id.in_(current))
        .order_by(ResourceAllocationRow.id, ResourceAllocationMachineRow.etk_position)).all() if kept_ids else []

    def numbers(operation, code):
        return sorted(item.position for item in operation.etk_ops if item.code == code)

    renumber, released = [], []
    parents = _parents(task, operations, kept)
    # New PFs split out of each kept PF: a bound operation can only have moved into one of its own.
    fresh = sorted((item.position, op.id, item.code, parents.get(op.id)) for op in operations if op.id not in set(kept)
                   for item in op.etk_ops)
    for allocation_id, operation_id, position, machine_id in rows:
        before = task.operation(operation_id)
        after = next(op for op in operations if op.id == operation_id)
        code = next((item.code for item in before.etk_ops if item.position == position), None)
        if code is None:
            if position not in after.etk_positions:
                return (f"У ПФ «{before.stage}» назначено оборудование на операции №{position}, которой нет в новой ЭТК",
                        [], [])
            continue
        old, new = numbers(before, code), numbers(after, code)
        if not new:
            landed = [(number, op_id) for number, op_id, other, parent in fresh if other == code and parent == operation_id]
            if release and landed and len(landed) == len(old):
                target_position, target_op = landed[old.index(position)]
                released.append((allocation_id, operation_id, position, code, machine_id, target_op, target_position))
                continue
            return (f"У ПФ «{before.stage}» назначено оборудование на операцию {code}, которая переходит в другой ПФ "
                    "или исчезает из новой ЭТК — снимите назначение и повторите"), [], []
        if len(new) != len(old):
            return (f"У ПФ «{before.stage}» назначено оборудование на операцию {code}, а таких операций в ПФ теперь "
                    f"{len(new)} вместо {len(old)} — неясно, к какой оно относится; снимите назначение, повторите и "
                    "назначьте оборудование заново"), [], []
        target = new[old.index(position)]
        if target != position:
            renumber.append((allocation_id, position, target))
    gone = {(item[0], item[2]) for item in released}
    moves = {(allocation_id, old): new for allocation_id, old, new in renumber}
    final = {}
    for allocation_id, operation_id, position, _machine in rows:
        if (allocation_id, position) in gone:
            continue
        number = moves.get((allocation_id, position), position)
        if number in final.setdefault(allocation_id, set()):
            return (f"У ПФ «{task.operation(operation_id).stage}» после перенумерации две привязки оборудования "
                    f"попадают на операцию №{number} — снимите назначение, повторите и назначьте оборудование "
                    "заново"), [], []
        final[allocation_id].add(number)
    return "", renumber, released


def _parents(task, operations, kept):
    """{rebuilt PF id: the old PF it was split out of, or None — unknown}. Covers every new PF and every kept PF whose
    ETK operations changed and whose lineage is not its own id (the position rule of `restructured_operations` may hand
    an old id to a PF made of another PF's operations). Only operations whose code existed before count. A PF whose
    known codes all occur in one old PF only has that parent. Otherwise the position rule (numbers a strict subset of
    one old PF's) and the code-occurrence match (code and occurrence among the whole task's operations with that code,
    ETK order) must agree; if they disagree or either is missing the parent is unknown (None): a separated task then
    keeps that PF standalone, and equipment bound to its operations is never released."""
    holders = {}
    for operation in task.operations:
        for item in operation.etk_ops:
            holders.setdefault(item.code, set()).add(operation.id)

    def keyed(ops):
        seen, result = {}, {}
        for op_id, item in sorted(((op.id, item) for op in ops for item in op.etk_ops), key=lambda pair: pair[1].position):
            occurrence = seen[item.code] = seen.get(item.code, -1) + 1
            result.setdefault(op_id, []).append((item.code, occurrence, item.position))
        return result

    old = {(code, occurrence): op_id for op_id, items in keyed(task.operations).items()
           for code, occurrence, _position in items}
    before = {operation.id: [(item.position, item.code) for item in operation.etk_ops] for operation in task.operations}
    result = {}
    for op_id, items in keyed(operations).items():
        if op_id in kept and before.get(op_id) == [(position, code) for code, _occurrence, position in items]:
            continue
        known = [item for item in items if item[0] in holders]
        owners = set().union(*(holders[code] for code, _occurrence, _position in known)) if known else set()
        if len(owners) == 1:
            parent = owners.pop()
        else:
            positions = {position for _code, _occurrence, position in items}
            by_position = next((item.id for item in task.operations
                                if item.etk_ops and positions < set(item.etk_positions)), None)
            matched = {old.get((code, occurrence)) for code, occurrence, _position in known} - {None}
            by_code = next(iter(matched)) if len(matched) == 1 else None
            parent = by_position if by_position is not None and by_position == by_code else None
        if op_id in kept and parent == op_id:
            continue
        result[op_id] = parent
    return result


def _lineage(task, uow):
    """{PF id: the PF it was split out of} stored by every rebuild of the task (audit `production.restructured`)."""
    from sqlalchemy import select

    from app.shared.persistence import AuditEventRow

    found = {}
    for details in uow.session.scalars(select(AuditEventRow.details).where(
            AuditEventRow.aggregate_id == task.id, AuditEventRow.event_type == "production.restructured")
            .order_by(AuditEventRow.occurred_at)):
        for child, parent in ((details or {}).get("origins") or {}).items():
            found[UUID(child)] = UUID(parent) if parent else None  # None: lineage unknown
    return found


def rebuild_task(task, entry, uow, actor, now, homes, origins=None, released=None):
    """Rebuild one locked, unstarted task on its reissued ETK in place: same task id, number, quantity, serials and
    assignments (`restructured_operations`). Its PFs leave their groups first (recorded in `homes`, {PF id: group id})
    so `regroup` can put the rebuilt PFs back; `origins` gets {new PF id: the old PF it was split out of}. Returns
    (rebuilt task, "") or (task untouched, refusal reason). `released` — a list: equipment bound to operations that moved
    to another PF is removed from the kept PF instead of refusing (CLI `--release-moved-equipment`; audit
    `production.equipment_released` per binding) and listed there.
    Shared by the CLI restructure and «Обновить из ЭТК»; the caller holds the project, group and task locks."""
    from .launchable import launch_operations, launch_route

    card_id, revision, document = entry[:3]
    reason = restructure_reason(task, uow)
    operations = kept = None
    if not reason:
        try:
            fresh = launch_operations(launch_route(document).steps, task.quantity)
            operations, kept = restructured_operations(task.operations, fresh)
        except ProductionValidationError as error:
            reason = str(error)
    reason = reason or restructure_reason(task, uow, kept, operations, release=released is not None)
    if reason:
        return task, reason
    for operation in task.operations:
        member = uow.cutting.for_operation(operation.id)
        if member:
            # Leave the old group first: the rebuilt PF is regrouped below (task_changed must not see it).
            homes[operation.id] = member.group_id
            _system_split(member, "rebuild")
            uow.cutting.get(member.group_id).version += 1
    parents = _parents(task, operations, kept)
    if origins is not None:
        origins.update(parents)
    _reason, renumber, freed = binding_moves(task, operations, kept, uow, release=released is not None)
    _release_bindings(task, freed, uow, actor, now, released)
    _renumber_bindings(renumber, uow)
    before = [(op.stage, len(op.etk_ops)) for op in task.operations]
    # Stage numbers are unique per task: park the old ones out of the way before the new route takes them.
    uow.production.add(replace(task, operations=tuple(
        replace(op, position=op.position + PARKED) for op in task.operations)))
    uow.session.flush()
    updated = task.restructure(operations, card_id, revision)
    uow.production.add(updated)
    _audit(updated, "production.restructured", actor, now, {
        "techcard_id": str(card_id), "from_revision": task.techcard_revision, "revision": revision,
        "operations": [[op.stage, len(op.etk_ops)] for op in updated.operations],
        "before": before, "origins": {str(child): str(parent) if parent else None
                                      for child, parent in parents.items()}}, uow)
    return updated, ""


def _release_bindings(task, freed, uow, actor, now, released):
    """Remove machine bindings of operations that moved to another PF from the kept PF's current allocation; the new PF
    stays unbound (planning places it). One audit event per binding."""
    from sqlalchemy import delete

    from .resource_assignment_infrastructure import ResourceAllocationMachineRow

    for allocation_id, operation_id, position, code, machine_id, target_op, target_position in freed:
        uow.session.execute(delete(ResourceAllocationMachineRow).where(
            ResourceAllocationMachineRow.allocation_id == allocation_id,
            ResourceAllocationMachineRow.etk_position == position))
        details = {"task_id": str(task.id), "task_number": task.number, "item": task.item,
                   "operation_id": str(operation_id), "stage": task.operation(operation_id).stage, "code": code,
                   "etk_position": position, "machine_id": str(machine_id), "allocation_id": str(allocation_id),
                   "to_operation_id": str(target_op), "to_etk_position": target_position}
        _audit(task, "production.equipment_released", actor, now, details, uow)
        released.append(details)
    uow.session.flush()


def _renumber_bindings(renumber, uow):
    """Point machine bindings at the same ETK operation under its new number (all rows of an allocation are rewritten
    at once: shifted numbers may swap)."""
    from sqlalchemy import delete, select

    from .resource_assignment_infrastructure import ResourceAllocationMachineRow

    for allocation_id in {item[0] for item in renumber}:
        moves = {old: new for identifier, old, new in renumber if identifier == allocation_id}
        rows = [(row.etk_position, row.machine_id) for row in uow.session.scalars(select(ResourceAllocationMachineRow)
                .where(ResourceAllocationMachineRow.allocation_id == allocation_id))]
        uow.session.execute(delete(ResourceAllocationMachineRow).where(
            ResourceAllocationMachineRow.allocation_id == allocation_id))
        uow.session.flush()
        for position, machine_id in rows:
            uow.session.add(ResourceAllocationMachineRow(allocation_id=allocation_id,
                                                         etk_position=moves.get(position, position), machine_id=machine_id))
        uow.session.flush()


def _separated_parent(operation_id, origins, homes, standalone, current, uow):
    """Whether a PF split out of others (following `origins` back) comes from a PF the master separated: one left
    standalone now, or one no longer a preparation PF that has no group and was split off by the master."""
    seen, parent = set(), origins.get(operation_id)
    while parent is not None and parent not in seen and parent not in homes:
        seen.add(parent)
        if parent in standalone or (parent not in current and uow.cutting.for_operation(parent) is None
                                    and _master_splits(parent, uow)):
            return True
        parent = origins.get(parent)
    return False


def task_regroup_entries(task, document, card_version, uow, actor, homes=(), origins=None):
    """Regroup entries (task, recipe, document, card version) for every unstarted preparation PF of the task, and the
    grouping refusals. PFs with work or in a started group stay where they are; so does a PF the master separated
    (`_separated`) unless its recipe changed, and every PF a rebuild split out of such a PF (`origins`). `homes` — PFs
    this rebuild itself took out of their groups."""
    try:
        site = _site(task, min(task.operations, key=lambda op: op.position), uow, actor)
    except (ProductionValidationError, PermissionError) as error:
        return [], [str(error)]
    recipes, refused = cutting_recipes(task.project, site, document, task.operations)
    entries, standalone = [], set()
    origins = {**_lineage(task, uow), **(origins or {})}  # stored by earlier rebuilds: a rerun keeps the separation
    for recipe in recipes:
        if (uow.cutting.for_operation(recipe.operation_id) is None and recipe.operation_id not in homes
                and _separated(task, recipe.operation_id, recipe.key, uow)):
            standalone.add(recipe.operation_id)
    master_split = bool(standalone) or any(
        uow.cutting.for_operation(operation.id) is None and operation.id not in homes and _master_splits(operation.id, uow)
        for operation in task.operations)
    for recipe in recipes:
        operation = task.operation(recipe.operation_id)
        member = uow.cutting.for_operation(operation.id)
        if member is None and uow.cutting.execution_reason(operation):
            continue
        if operation.id in standalone or (member is None and _separated_parent(
                operation.id, origins, homes, standalone, {item.operation_id for item in recipes}, uow)):
            continue
        if member is None and master_split and operation.id in origins and origins[operation.id] is None:
            continue  # lineage unknown in a task the master separated: stay standalone rather than join a group
        if member is not None:
            group = uow.cutting.get(member.group_id)
            if group.state != "open" or uow.cutting.work(group.id):
                continue
        entries.append((task, recipe, document, card_version))
    return entries, [reason for _operation, reason in refused]


def restructure_project(project, cards, actor, now, uow, *, task_ids=None, release_moved_equipment=False):
    """Rebuild unstarted tasks of the project whose issued ETK changed (same task id, number, quantity, serials and
    assignments; `restructured_operations`) and regroup all their unstarted preparation PFs by kind and recipe.

    `cards` maps task id -> (card_id, revision, document, card_version[, ...]) of the issued ETK, or an error text.
    One transaction: the caller commits (apply) or leaves it uncommitted (dry run rolls back).
    `release_moved_equipment` — remove bindings of operations that moved to a new PF instead of refusing the task
    (`released` in the result, per binding)."""
    key = normalize_project_number(project)
    if not key:
        raise ProductionValidationError("Укажите проект")
    lock_reflow_projects(uow, (), now, projects=(key,))
    lock_groups(uow, key)
    tasks = [task for task in uow.production.for_project(key) if task.status is ProductionStatus.RUNNING]
    if task_ids is not None:
        tasks = [task for task in tasks if task.id in set(task_ids)]
    for task in sorted(tasks, key=lambda item: str(item.id)):
        uow.production.lock(task.id)
    result = {"project": key, "restructured": [], "unchanged": [], "refused": [], "groups": [], "moved": [],
              "cancelled": [], "released": []}
    released = result["released"] if release_moved_equipment else None
    homes, origins, entries = {}, {}, []
    for task in sorted((uow.production.get(item.id) for item in tasks), key=lambda item: (item.item, item.number)):
        identity = {"task_id": str(task.id), "task_number": task.number, "item": task.item}
        entry = cards.get(task.id)
        if entry is None or isinstance(entry, str):
            result["refused"].append({**identity, "reason": entry or "нет выданной ЭТК"})
            continue
        _card_id, revision, document, card_version = entry[:4]
        if revision != task.techcard_revision:
            updated, reason = rebuild_task(task, entry, uow, actor, now, homes, origins, released)
            if reason:
                result["refused"].append({**identity, "reason": reason})
                continue
            result["restructured"].append({**identity, "from_revision": task.techcard_revision, "revision": revision,
                                           "pf_before": len(task.operations), "pf_after": len(updated.operations)})
            task = updated
        else:
            result["unchanged"].append(identity)
        found, refused = task_regroup_entries(task, document, card_version, uow, actor, homes, origins)
        result["refused"].extend({**identity, "reason": f"группировка: {reason}"} for reason in refused)
        entries.extend(found)
    moved = regroup(entries, uow, actor, now, homes=homes, origins=origins)
    result.update(groups=moved["groups"], moved=moved["moved"], cancelled=moved["cancelled"])
    return result


def row_tubes(recipe, assembly_quantity):
    """Tube pieces of one КБ row of a tubes group, from the group's frozen recipe: [{material, length_mm, quantity}]
    for the row's whole assembly quantity, merged by material and length («Резка трубок»). Other kinds: []."""
    if recipe_kind(recipe) != TUBES:
        return []
    pieces = {}
    for op in recipe.get("operations") or []:
        parameters = op.get("parameters") or {}
        tube = parameters.get("tube")
        if not tube:
            continue
        try:
            cables = Decimal(number(recipe.get("cables_per_assembly") or 1))
            groups = (op.get("fields") or {}).get("coaxProtectiveTubes") or []
            if groups:  # per-assembly cut groups written into the issued card (protective tubes)
                per = [(group.get("length"), Decimal(number(group.get("units")))) for group in groups]
            elif tube.get("lengths"):  # protective tubes of an older card: one piece per label place
                marks = tube.get("marks") or [{"place": place} for place in ("A", "B", "middle")]
                per = [(tube["lengths"].get(mark.get("place")),
                        Decimal(1) if mark.get("scope") == "assembly" else cables) for mark in marks]
            else:
                per = [(tube.get("length"), Decimal(number(parameters.get("units"))))]
            found = [((str(tube.get("article") or ""), number(length)), units) for length, units in per]
        except (ValueError, TypeError, AttributeError):
            continue  # an incomplete snapshot never breaks the group view; the row just shows no tube data
        for key, units in found:
            pieces[key] = pieces.get(key, Decimal(0)) + units * assembly_quantity
    return [{"material": material, "length_mm": length, "quantity": number(quantity)}
            for (material, length), quantity in sorted(pieces.items(), key=lambda item: (item[0][0], Decimal(item[0][1])))]


def describe_group(group, uow):
    members = uow.cutting.members(group.id, active=False)
    task_ids = tuple({m.task_id for m in members})
    outputs = uow.outputs.active_for_tasks(task_ids)
    rows = []
    project = group.project_key
    for member in members:
        task = uow.production.get(member.task_id)
        if not rows:
            project = cutting_project_label(task.project, member.row_snapshot.get("document"))
        operation = task.operation(member.operation_id)
        own = [item for item in outputs if item.operation_id == operation.id]
        boxes = [box for box in uow.boxes.list(task_id=task.id, limit=10000) if box.operation_id == operation.id]
        good = sum(item.good_quantity for item in own)
        defects = sum(item.defect_quantity for item in own)
        target = task.operation_units(operation.id)
        state = (member.state if member.state != "active" else "done" if good + defects >= target else
                 "started" if own or any(s.started_at for s in operation.shares) else
                 "assigned" if operation.operator_ids else "waiting")
        rows.append({"id": str(member.id), "task_id": str(task.id), "operation_id": str(operation.id),
                     "task_version": task.version, "task_number": task.number, "project": task.project,
                     "item": task.item, "name": task.name, "position": member.position,
                     "assembly_quantity": member.assembly_quantity, "blank_quantity": str(member.blank_quantity),
                     "operation_quantity": str(target), **{k: v for k, v in member.row_snapshot.items()
                                                            if k not in {"document", "validated_card_version", SPLIT_BY}},
                     "tubes": row_tubes(group.recipe, member.assembly_quantity),
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
    work = uow.cutting.work(group.id)
    session = uow.work.get(work.session_id) if work else None
    runner = uow.operators.get(session.operator_id) if session else None  # «Группу ведёт <имя>» on the card
    downtime = None
    if work and work.downtime_id:
        from .downtime_infrastructure import SqlAlchemyDowntimeRepository
        item = SqlAlchemyDowntimeRepository(uow.session).get(work.downtime_id)
        downtime = {"id": str(item.id), "reason": item.reason.value, "reason_name": item.reason_name,
                    "comment": item.comment, "started_at": item.started_at.isoformat()}
    return {"id": str(group.id), "number": group.number, "version": group.version,
            "session": {"id": str(session.id), "task_id": str(session.task_id),
                "operation_id": str(session.operation_id), "operator_id": str(session.operator_id),
                "operator_name": runner.name if runner else "", "cutting_group_id": str(group.id), "started_at": session.started_at.isoformat(),
                "status": session.status.value, "pause_seconds": session.pause_seconds,
                "pause_started_at": session.pause_started_at.isoformat() if session.pause_started_at else None} if session else None,
            "downtime": downtime,
            "recipe_version": group.recipe_version, "recipe_key": group.recipe_key,
            "kind": recipe_kind(group.recipe), "kind_name": KIND_NAMES[recipe_kind(group.recipe)],
            "project": project, "site": group.site, "state": group.state,
            "membership_frozen": group.state == "frozen", "rows": rows,
            **common_assignment(active),
            "planned_start": min(starts) if starts else None, "planned_finish": max(finishes) if finishes else None,
            "norm_seconds": sum(row["norm_seconds"] for row in active)}
