"""Cutting queue per machine (owner 20.11: «Оптовая нарезка по станку, через все проекты»).

Computed, no table of its own: every open cable cut of all projects — active members of open/frozen cut-kind groups and
standalone input-less cut PFs — whose machine (the cut operation's card tooling «Оборудование») is the machine asked for.
The operator goes row by row; rows are ordered so mostly only the length changes (wire → strip/diameter → marking,
then length short → long). The ETK is not fully debugged yet: a missing or odd value never drops a row — the row carries
«нет в ЭТК: …» warnings and the operator works on.

«Готово» posts the row's pieces through the existing manual output path (`enter_manual_output`: its refusals, site and
quantity rules, group freeze) after assigning the acting operator to the PF if they are not on it yet. Idempotency rides
an audit event whose id is derived from the key (primary key = the constraint against a double post)."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from sqlalchemy import select

from app.shared.events import AuditEvent
from app.shared.persistence import AuditEventRow
from .application import AssignProductionCommand, ConcurrentProductionUpdate, assign_task, lock_reflow_projects
from .cutting import CABLE_CUTS, CUT, cutting_project_label, digest, recipe_kind
from .cutting_application import check_access
from .cutting_infrastructure import CuttingGroupRow, CuttingMemberRow, CuttingWorkRow
from .domain import ProductionStatus, ProductionValidationError
from .execution import ManualOutputCommand, enter_manual_output, operation_quantity

# 708К. cuts on the big rotary BZW (owner 20.11): preferred when a card lists several machines. A card that names none
# goes to its own bucket (machine_id null, GET `machine_id=none`), never onto a real machine's list.
DEFAULT_MACHINE = "ОБ.095"
NO_MACHINE = "none"
NO_MACHINE_NAME = "Станок не указан"
NO_DOCUMENT = "нет в ЭТК: выданная карта"
UNCONFIRMED = "Нет ЭТК задания — не понять, резка ли это: готовые вводит мастер"
NOT_A_CUT = "В очереди резки сдаётся только резка кабеля — этот ПФ сдают на его карточке"
RECEIPT = "production.cutting_queue_done"
_RECEIPTS = UUID("6f1c2a52-6d0e-4d43-9a43-2a7c1f0e0951")
MODE_NAMES = {"full": "полная", "partial": "частичная"}
MARKING_NAMES = {"full": "по всей длине", "ends": "по краям"}

DocumentKey = tuple[str, str, int | None]


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def _decimal(value: object) -> Decimal | None:
    """«1000», «1000,0», «12.7 мм» → Decimal; anything else (empty, «-», text) → None."""
    raw = _text(value).replace(",", ".").removesuffix("мм").strip()
    try:
        result = Decimal(raw)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result > 0 else None


def _plain(value: Decimal | None) -> str:
    return format(value.normalize(), "f") if value is not None else ""


def _shown(value: Decimal | None) -> str:
    return _plain(value).replace(".", ",")


def _count(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def document_key(task) -> DocumentKey:
    return (task.project, task.item, task.techcard_revision)


def _cut_ops(document: Mapping | None, operation) -> list[tuple[Mapping, Mapping]]:
    """(issued op, generator op) of the PF's cable cuts, by the PF's ETK positions; lenient — odd entries are skipped."""
    document = _mapping(document)
    config = _mapping(document.get("build_config"))
    raw_ops = [item for item in config.get("ops") or []]
    issued = [item for item in document.get("operations") or []]
    found = []
    for position in operation.etk_positions:
        op = _mapping(issued[position - 1]) if 0 < position <= len(issued) else {}
        raw = _mapping(raw_ops[position - 1]) if 0 < position <= len(raw_ops) else {}
        if (op.get("type") or raw.get("type")) in CABLE_CUTS:
            found.append((op, raw))
    return found


def _machine(op: Mapping, warnings: list[str]) -> tuple[str | None, str]:
    machines: dict[str, str] = {}
    for tool in _mapping(op.get("card")).get("tooling") or []:
        tool = _mapping(tool)
        identity = _text(tool.get("inventory_number") or tool.get("equipment_model_id"))
        if tool.get("section") == "Оборудование" and identity:
            machines.setdefault(identity, _text(tool.get("name")))
    if not machines:
        warnings.append("нет в ЭТК: станок")
        return None, NO_MACHINE_NAME
    identity = DEFAULT_MACHINE if DEFAULT_MACHINE in machines else next(iter(machines))
    if len(machines) > 1:
        warnings.append("в ЭТК ещё станки: " + ", ".join(other for other in machines if other != identity))
    return identity, machines[identity]


_RU = {"A": "А", "B": "Б"}  # side letters on the screen are Cyrillic, as the «Зачистка А/Б» columns


def _coax_side(fields: Mapping, coax: Mapping, side: str, warnings: list[str]) -> dict:
    sizes = _mapping(fields.get("coax" if side == "A" else "coaxB"))
    if not sizes:
        warnings.append(f"нет в ЭТК: зачистка {_RU[side]}")
        return {"value": "", "mode": "", "program": "absent", "diameters": ""}
    program = sizes.get("stripProgram") if isinstance(sizes.get("stripProgram"), Mapping) else None
    boundaries = [_mapping(item) for item in (program or {}).get("boundaries") or []]
    if boundaries:
        values = [_shown(_decimal(item.get("value"))) for item in boundaries]
    else:
        values = [_shown(_decimal(sizes.get(name))) for name in ("l1", "l2", "l3")]
    if not any(values):
        warnings.append(f"нет в ЭТК: размеры зачистки {_RU[side]}")
    unconfirmed = not boundaries or any(phrase in str((program or {}).get("note", "")).casefold()
                                        for phrase in ("не рассчитан", "подтвердить"))
    mode = _text(sizes.get("stripMode") or (program or {}).get("mode")) or (
        "full" if coax.get("fakraCutStrip") is True else "")
    if mode not in MODE_NAMES:
        warnings.append(f"нет в ЭТК: режим зачистки {_RU[side]}")
        mode = ""
    return {"value": "/".join(value or "—" for value in values) if any(values) else "", "mode": mode,
            "program": "absent" if program is None else "unconfirmed" if unconfirmed else "confirmed",
            "diameters": "/".join(_shown(_decimal(sizes.get(name))) or "—" for name in ("d1", "d2", "d3"))}


def _wire_side(strip: Mapping, side: str, warnings: list[str]) -> dict | None:
    if side not in strip:
        warnings.append(f"нет в ЭТК: зачистка {_RU[side.upper()]}")
        return {"value": "", "mode": "", "program": "", "diameters": ""}
    if not strip.get(side):
        return None
    value = _decimal(strip.get("len" + side.upper()))
    mode = _text(strip.get("mode" + side.upper()))
    if value is None:
        warnings.append(f"нет в ЭТК: длина зачистки {_RU[side.upper()]}")
    if mode not in MODE_NAMES:
        warnings.append(f"нет в ЭТК: режим зачистки {_RU[side.upper()]}")
        mode = ""
    return {"value": _shown(value), "mode": mode, "program": "", "diameters": ""}


def cut_values(document: Mapping | None, operation, *, known_cut: bool = False) -> dict | None:
    """What the operator sets on the machine for this PF; None — no cable cut is known for it (not a cutting row).
    `known_cut` — a member of a cut-kind group: it stays a row even when its frozen ETK lacks the cut."""
    if document is None and not known_cut:
        # No ETK to read (no issued card, revision missing): the PF is kept, but whether it is a cut is unknown.
        return {"machine_id": None, "machine_name": NO_MACHINE_NAME, "wire": "", "wire_name": "", "length": None,
                "sides": {"A": None, "B": None}, "marking": "", "warnings": [NO_DOCUMENT], "confirmed": False}
    cuts = _cut_ops(document, operation)
    warnings: list[str] = []
    if not cuts:
        if not known_cut:
            return None
        warnings.append("нет в ЭТК: резка кабеля")
        cuts = [({}, {})]
    if len(cuts) > 1:
        warnings.append("в ЭТК несколько резок кабеля в ПФ — показана первая")
    op, raw = cuts[0]
    kind = op.get("type") or raw.get("type")
    fields = _mapping(op.get("fields"))
    config = _mapping(_mapping(document).get("build_config"))
    coax = _mapping(config.get("coax"))
    machine_id, machine_name = _machine(op, warnings)
    wire, wire_name, length = "", "", None
    sides: dict[str, dict | None] = {"A": None, "B": None}
    if kind == "coaxCut":
        wire, wire_name = _text(coax.get("cableArt")), _text(coax.get("cableName"))
        length = _decimal(coax.get("cableLength"))
        chosen = raw.get("stripSides")
        if not isinstance(chosen, list):
            warnings.append("нет в ЭТК: стороны зачистки")
            chosen = []
        for side in ("A", "B"):
            if side in chosen:
                sides[side] = _coax_side(fields, coax, side, warnings)
    elif kind in CABLE_CUTS:
        wires = [_mapping(item) for item in fields.get("wires") or config.get("wires") or []]
        if len(wires) > 1:
            warnings.append("в ЭТК несколько проводов — показан первый")
        first = wires[0] if wires else {}
        wire, wire_name = _text(first.get("art")), _text(first.get("name"))
        length = _decimal(first.get("length"))
        strip = _mapping(fields.get("strip") or raw.get("strip") or config.get("strip"))
        if not strip:
            warnings.append("нет в ЭТК: зачистка")
        else:
            sides = {side.upper(): _wire_side(strip, side, warnings) for side in ("a", "b")}
    if not wire and not wire_name:
        warnings.append("нет в ЭТК: провод")
    if length is None:
        warnings.append("нет в ЭТК: длина")
    laser = raw.get("laserMark") if isinstance(raw.get("laserMark"), Mapping) else fields.get("laserMark")
    marking = ("full" if _mapping(laser).get("mode") == "full" else "ends") if isinstance(laser, Mapping) else ""
    return {"machine_id": machine_id, "machine_name": machine_name, "wire": wire, "wire_name": wire_name,
            "length": length, "sides": sides, "marking": marking, "warnings": warnings, "confirmed": True}


def _side_key(side: dict | None) -> tuple[str, str, str]:
    return ("", "", "") if side is None else (side["value"], side["mode"], side["diameters"])


def _setup_text(values: dict) -> str:
    wire = values["wire"] or values["wire_name"] or "провод (нет в ЭТК)"
    parts = [f"Заправьте {wire}"]
    for name, side in values["sides"].items():
        if side is not None:
            mode = MODE_NAMES.get(side["mode"], "режим не задан")
            parts.append(f"зачистка {_RU[name]}: {side['value'] or '—'} мм, {mode}")
    if all(side is None for side in values["sides"].values()):
        parts.append("без зачистки")
    if values["marking"]:
        parts.append(f"лазер {MARKING_NAMES[values['marking']]}")
    return " · ".join(parts)


def row_project(task, document: Mapping | None) -> str:
    """The project as the row and the «Готово» notice show it: the issued spelling of the ETK, else the task's."""
    return cutting_project_label(task.project, document)


class _People:
    """Operators read once per request; inactive ones (missing from the list) are read on demand."""

    def __init__(self, uow):
        self.uow = uow
        self.known = {operator.id: operator for operator in uow.operators.list()}

    def get(self, identifier):
        if identifier not in self.known:
            self.known[identifier] = self.uow.operators.get(identifier)
        return self.known[identifier]

    def name(self, identifier) -> str:
        operator = self.get(identifier)
        return operator.name if operator else ""


def _site_allowed(task, operation, people, actor) -> bool:
    viewer = people.get(actor.id)
    site = viewer.site if viewer else ""
    if not site:
        return True
    sites = {task.handed_to} - {"", None}
    for identifier in operation.operator_ids:
        operator = people.get(identifier)
        if operator and operator.site:
            sites.add(operator.site)
    return all(other == site for other in sites)


def collect(uow, actor) -> list[dict]:
    """Open cable cut PFs the viewer's site may see, read once per request: active members of open/frozen cut-kind groups
    (with their frozen document) and input-less PFs of running tasks without an active membership (their ETK is read by
    the caller, `document_keys`). Closed/handed-over tasks and PFs whose good + defect reached the quantity are dropped
    before any group access check or snapshot read, so a finished frozen group costs only its member ids."""
    people = _People(uow)
    running = {task.id: task for task in uow.production.with_status(ProductionStatus.RUNNING) if not task.handed_to}
    made: dict[UUID, Decimal] = {}
    for output in uow.outputs.active_for_tasks(tuple(running)) if running else ():
        made[output.operation_id] = made.get(output.operation_id, Decimal(0)) + output.good_quantity + output.defect_quantity

    def open_work(task, operation):
        planned = operation_quantity(task, operation)
        done = made.get(operation.id, Decimal(0))
        return (planned, done) if done < planned else None

    member_ops = set(uow.session.scalars(select(CuttingMemberRow.operation_id).where(CuttingMemberRow.state == "active")))
    groups = [group for group in uow.session.scalars(select(CuttingGroupRow).where(
        CuttingGroupRow.state.in_(("open", "frozen"))).order_by(CuttingGroupRow.created_at, CuttingGroupRow.id))
        if recipe_kind(group.recipe) == CUT]
    members = uow.session.execute(select(
        CuttingMemberRow.id, CuttingMemberRow.group_id, CuttingMemberRow.task_id, CuttingMemberRow.operation_id,
    ).where(CuttingMemberRow.state == "active", CuttingMemberRow.group_id.in_([group.id for group in groups]))
        .order_by(CuttingMemberRow.position)).all() if groups else []
    kept: dict[UUID, list] = {}
    for member_id, group_id, task_id, operation_id in members:
        task = running.get(task_id)
        operation = task.operation(operation_id) if task else None
        left = open_work(task, operation) if operation else None
        if left:
            kept.setdefault(group_id, []).append((member_id, task, operation, left))
    visible = []
    for group in groups:
        if group.id not in kept:
            continue
        try:
            check_access(group, uow, actor)
        except PermissionError:
            continue
        visible.append(group)
    member_ids = [row[0] for group in visible for row in kept[group.id]]
    snapshots = dict(uow.session.execute(select(CuttingMemberRow.id, CuttingMemberRow.row_snapshot).where(
        CuttingMemberRow.id.in_(member_ids))).all()) if member_ids else {}
    works = list(uow.session.scalars(select(CuttingWorkRow).where(
        CuttingWorkRow.closed.is_(False), CuttingWorkRow.group_id.in_([group.id for group in visible])))) if visible else []
    sessions = {work.group_id: uow.work.get(work.session_id) for work in works}
    result = []
    for group in visible:
        blocked = None
        if group.id in sessions:
            session = sessions[group.id]
            who = people.name(session.operator_id) if session else ""
            blocked = f"Группа {group.number} начата на карточке группы{f' ({who})' if who else ''} — сдают там"
        for member_id, task, operation, (planned, done) in kept[group.id]:
            result.append({"task": task, "operation": operation, "planned": planned, "done": done,
                           "group": {"id": str(group.id), "number": group.number, "state": group.state},
                           "document": _mapping(snapshots.get(member_id)).get("document"), "blocked": blocked,
                           "names": [people.name(identifier) for identifier in operation.operator_ids]})
    for task in running.values():
        for operation in task.operations:
            if operation.inputs or not operation.etk_ops or operation.id in member_ops:
                continue
            left = open_work(task, operation)
            if left and _site_allowed(task, operation, people, actor):
                result.append({"task": task, "operation": operation, "planned": left[0], "done": left[1], "group": None,
                               "document": None, "blocked": None,
                               "names": [people.name(identifier) for identifier in operation.operator_ids]})
    return result


def document_keys(candidates: list[dict]) -> set[DocumentKey]:
    """Issued ETK revisions the standalone candidates are read from (grouped rows carry their frozen document)."""
    return {document_key(item["task"]) for item in candidates if item["group"] is None}


def queue_rows(candidates: list[dict], documents: Mapping[DocumentKey, Mapping | None],
               machine_id: str | None = None) -> list[dict]:
    """Rows from `collect` plus the standalone ETKs; pure — no unit of work."""
    wanted = None if machine_id == NO_MACHINE else machine_id
    rows = []
    for item in candidates:
        task, operation, group = item["task"], item["operation"], item["group"]
        document = item["document"] if group is not None else documents.get(document_key(task))
        values = cut_values(document, operation, known_cut=group is not None)
        if values is None or (machine_id is not None and values["machine_id"] != wanted):
            continue
        sides = values["sides"]
        project = row_project(task, document)
        rows.append({
            "key": f"{task.id}:{operation.id}", "task_id": str(task.id), "operation_id": str(operation.id),
            "task_version": task.version, "task_number": task.number, "project": project, "item": task.item,
            "group_id": group["id"] if group else None, "group_number": group["number"] if group else None,
            "group_state": group["state"] if group else None,
            "machine_id": values["machine_id"], "machine_name": values["machine_name"],
            "wire": values["wire"], "wire_name": values["wire_name"], "length_mm": _plain(values["length"]) or None,
            "planned": _count(item["planned"]), "done": _count(item["done"]),
            "left": _count(item["planned"] - item["done"]),
            **{f"strip_{name.lower()}{suffix}": (side or {}).get(field) or None for name, side in sides.items()
               for suffix, field in (("", "value"), ("_mode", "mode"), ("_program", "program"))},
            "diameters": next((side["diameters"] for side in sides.values() if side and side["diameters"]), None),
            "laser_mark": bool(values["marking"]), "marking_mode": values["marking"] or None,
            "operator_ids": [str(identifier) for identifier in operation.operator_ids],
            "operator_names": item["names"],
            "warnings": values["warnings"],
            "blocked": item["blocked"] or (None if values["confirmed"] else UNCONFIRMED),
            "setup_text": _setup_text(values),
            "_setup": ((values["wire"] or values["wire_name"]).casefold(), _side_key(sides["A"]), _side_key(sides["B"]),
                       values["marking"]),
            "_order": (values["length"] is None, values["length"] or Decimal(0), project, task.item, task.number),
        })
    rows.sort(key=lambda row: (row["_setup"], row["_order"]))
    previous = None
    for row in rows:
        setup = row.pop("_setup")
        row.pop("_order")
        row["setup_change"] = setup != previous
        previous = setup
    return rows


def queue_version(rows: list[dict]) -> str:
    return digest([[row["key"], row["left"], row["task_version"], row["group_state"], row["machine_id"],
                    row["length_mm"], row["setup_text"], row["warnings"], row["blocked"]] for row in rows])


def machines(rows: list[dict]) -> list[dict]:
    """Machines with open cut work: rows, pieces left, projects."""
    found: dict[str | None, dict] = {}
    for row in rows:
        entry = found.setdefault(row["machine_id"], {"machine_id": row["machine_id"], "machine_name": row["machine_name"],
                                                     "rows": 0, "left": Decimal(0), "projects": set()})
        entry["rows"] += 1
        entry["left"] += Decimal(row["left"])
        entry["projects"].add(row["project"])
    return [{**entry, "left": _count(entry["left"]), "projects": len(entry["projects"])}
            for _key, entry in sorted(found.items(), key=lambda item: (item[0] is None, item[0] or ""))]


def receipt_id(key: str) -> UUID:
    return uuid5(_RECEIPTS, f"cutting-queue:{key}")


def mark_done(task_id: UUID, operation_id: UUID, quantity: int, key: str, actor, worker, now: datetime, uow, *,
              document: Mapping | None = None) -> dict:
    """«Готово» on a queue row: the row's pieces as the PF's manual output, in one transaction with the receipt.
    `document` — the task's pinned issued ETK (standalone PFs: proves the PF is a cable cut; the notice's project)."""
    fingerprint = digest([str(task_id), str(operation_id), str(quantity), str(worker.id)])
    with uow:
        if uow.production.get(task_id) is None:
            raise KeyError("Задание не найдено")
        lock_reflow_projects(uow, (task_id,), now)
        stored = uow.session.get(AuditEventRow, receipt_id(key))
        if stored is not None:
            if stored.event_type != RECEIPT or (stored.details or {}).get("fingerprint") != fingerprint:
                raise ConcurrentProductionUpdate("Ключ повтора использован другим действием")
            return deepcopy(stored.details["result"])
        task = uow.production.get(task_id)
        operation = next((item for item in task.operations if item.id == operation_id), None)
        if operation is None:
            raise KeyError("ПФ нет в задании")
        member = uow.cutting.for_operation(operation_id)
        if member is not None:
            # Lock order of every cutting path: project → group → task.
            group = uow.cutting.get(member.group_id, lock=True)
            if group.state not in ("open", "frozen") or recipe_kind(group.recipe) != CUT:
                raise ProductionValidationError(NOT_A_CUT)
            check_access(group, uow, actor)
            document = member.row_snapshot.get("document")
        else:
            # Only queue rows: an input-less PF whose issued ETK holds a cable cut (labels, tubes, assembly — refused).
            if operation.inputs or not operation.etk_ops:
                raise ProductionValidationError(NOT_A_CUT)
            if document is None:
                raise ProductionValidationError(UNCONFIRMED)
            if not _cut_ops(document, operation):
                raise ProductionValidationError(NOT_A_CUT)
            if not _site_allowed(task, operation, _People(uow), actor):
                raise PermissionError("Задание другой площадки")
        uow.production.lock(task_id)
        task = uow.production.get(task_id)
        operation = task.operation(operation_id)
        assigned = False
        if worker.id not in operation.operator_ids:
            # Owner 20.11: anyone at the machine may press «Готово» — the operator joins the PF's assignees first.
            assign_task(AssignProductionCommand(
                task_id=task.id, expected_version=task.version, operation_id=operation.id,
                operator_ids=(*operation.operator_ids, worker.id),
                requested_start=operation.planned_start if operation.operator_ids else None, actor=actor, now=now), uow)
            assigned = True
        box, output, _filled, _closed = enter_manual_output(ManualOutputCommand(
            task_id=task_id, operation_id=operation_id, good_quantity=Decimal(quantity), actor=actor, now=now,
            performer=worker), uow)
        task = uow.production.get(task_id)
        operation = task.operation(operation_id)
        planned, made = operation_quantity(task, operation), uow.outputs.total_for_operation(operation_id)
        project = row_project(task, document)
        result = {"notice": f"Готово: {project} · {task.item} · {_count(Decimal(quantity))} шт",
                  "assigned": assigned,
                  "box": {"id": str(box.id), "number": box.number, "code": box.code},
                  "output_id": str(output.id),
                  "row": {"key": f"{task.id}:{operation_id}", "task_id": str(task.id), "operation_id": str(operation_id),
                          "task_version": task.version, "planned": _count(planned), "done": _count(made),
                          "left": _count(max(Decimal(0), planned - made))}}
        uow.audit.add(AuditEvent(id=receipt_id(key), aggregate_id=output.id, event_type=RECEIPT, actor=actor,
                                 occurred_at=now, details={"key": key, "fingerprint": fingerprint,
                                                           "operator_id": str(worker.id), "task_id": str(task_id),
                                                           "operation_id": str(operation_id), "quantity": quantity,
                                                           "result": result}))
        uow.session.flush()
        uow.commit()
        return result
