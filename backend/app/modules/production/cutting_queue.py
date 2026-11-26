"""Cutting queue per machine (owner 20.11: «Оптовая нарезка по станку, через все проекты»).

Computed, no table of its own: every open cable cut of all projects — active members of open/frozen cut-kind groups and
standalone input-less cut PFs — whose machine (the cut operation's card tooling «Оборудование») is the machine asked for.
The operator goes row by row; rows are ordered so mostly only the length changes (wire → strip/diameter → marking,
then length short → long). The ETK is not fully debugged yet: a missing or odd value never drops a row — the row carries
«нет в ЭТК: …» warnings and the operator works on.

«Готово» posts the row's pieces through the existing manual output path (`enter_manual_output`: its refusals, site and
quantity rules, group freeze); a монтажник not yet on the PF is assigned first, anyone else with access to the site's
tasks posts as «Ввод готовых» (under the presser, nobody assigned). Idempotency rides an audit event whose id is derived
from the key (primary key = the constraint against a double post); it is also the «Журнал» line of the press."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from sqlalchemy import select

from app.shared.events import AuditEvent
from app.shared.persistence import AuditEventRow
from .application import AssignProductionCommand, ConcurrentProductionUpdate, assign_task, lock_reflow_projects
from .cutting import CABLE_CUTS, CUT, cutting_project_label, digest, recipe_kind
from .cutting_application import check_access, check_group_sites
from .cutting_infrastructure import CuttingGroupRow, CuttingMemberRow, CuttingWorkRow
from .domain import ProductionStatus, ProductionValidationError
from .equipment_catalog import EMPTY, EquipmentCatalog
from .execution import ManualOutputCommand, enter_manual_output, operation_quantity
from .infrastructure import ProductionWorkSessionRow
from .traceability import WorkStatus

# Machine of a row = what its ETK tooling names (owner 20.11, final): a card listing several machines puts the row on
# EACH of their lists (same key — one «Готово» removes it everywhere); a card that names none goes to its own bucket
# (machine_id null, GET `machine_id=none`), never onto a real machine's list. `machine_id=all` — every row once.
NO_MACHINE = "none"
NO_MACHINE_NAME = "Станок не указан"
ALL_MACHINES = "all"
ALL_MACHINES_NAME = "Все станки"
NO_DOCUMENT = "нет в ЭТК: выданная карта"
UNCONFIRMED = "Нет ЭТК задания — не понять, резка ли это: готовые вводит мастер"
WORK_OPEN = "По этому ПФ идёт работа на карточке — сдайте там"
GROUP_STARTED = "Группа {number} начата на карточке — сдайте там"
NO_RECORD = "Вас нет в справочнике сотрудников — «Готово» без него вводит мастер"
NO_SITE = "У вас в справочнике сотрудников не указана площадка — «Готово» без неё вводит мастер"
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


WIRE_NAME_MAX = 40


def _wire_name(name: str, art: str) -> str:
    """Short name for the queue row: a long marketing description (supplier text in the cable card) is never shown.

    A short name stays; a long one yields to the article (the cable mark, «RTK 031»); with no article it is cut at a word.
    """
    if len(name) <= WIRE_NAME_MAX:
        return name
    if art:
        return art  # the article is shown in full, never cut
    return name[:WIRE_NAME_MAX].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "…"


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


def _number(value: object) -> str:
    return "".join(str(value or "").split()).upper().replace("OБ", "ОБ")


def _pool_machine(tool: Mapping, catalog: EquipmentCatalog) -> tuple[str, str | None] | None:
    """(key, label) of a tooling row that names «any unit of a model»: the catalog resolves it to a model with several
    physical units, none of them the row's exact number («ОБ.100.X», bare «ОБ.100», a model id only). A model with one
    physical unit is that unit: (its inventory number, None) — the ETK name stays the label. None — keep the raw
    identity (a physical unit named exactly, or a model the catalog cannot resolve)."""
    machine = catalog.machine(tool)
    model = catalog.models.get(machine.key) if machine is not None and machine.model_id else None
    physical = {unit.number for unit in model.physical} if model else set()
    if not physical or _number(tool.get("inventory_number")) in physical:
        return None
    if len(physical) == 1:
        return next(iter(physical)), None
    # The operator knows a machine by its model («ОБ.100 BZW-D2.0S»), not by the long catalogue name.
    article = model.article.replace("(R)", "").strip()
    label = f"{machine.code} {article}" if article else machine.label
    return machine.key, f"{label} · любой свободный ({len(physical)})"


def _machines(op: Mapping, warnings: list[str], catalog: EquipmentCatalog = EMPTY) -> list[tuple[str, str]]:
    """Every machine the ETK tooling names, in card order (the first is the row's default; owner 20.11, final — no
    preferred machine); a model pool («ОБ.100.X») is keyed by the model; none → «нет в ЭТК: станок»."""
    machines: dict[str, str] = {}
    for tool in _mapping(op.get("card")).get("tooling") or []:
        tool = _mapping(tool)
        if tool.get("section") != "Оборудование":
            continue
        pool = _pool_machine(tool, catalog)
        identity = pool[0] if pool else _text(tool.get("inventory_number") or tool.get("equipment_model_id"))
        if identity:
            machines.setdefault(identity, pool[1] if pool and pool[1] else _text(tool.get("name")))
    if not machines:
        warnings.append("нет в ЭТК: станок")
    return list(machines.items())


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


def cut_values(document: Mapping | None, operation, *, known_cut: bool = False,
               catalog: EquipmentCatalog = EMPTY) -> dict | None:
    """What the operator sets on the machine for this PF; None — no cable cut is known for it (not a cutting row).
    `known_cut` — a member of a cut-kind group: it stays a row even when its frozen ETK lacks the cut."""
    if document is None and not known_cut:
        # No ETK to read (no issued card, revision missing): the PF is kept, but whether it is a cut is unknown.
        return {"machines": [], "wire": "", "wire_name": "", "length": None,
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
    machines = _machines(op, warnings, catalog)
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
    return {"machines": machines, "wire": wire, "wire_name": wire_name,
            "length": length, "sides": sides, "marking": marking, "warnings": warnings, "confirmed": True}


def _side_key(side: dict | None) -> tuple[str, str, str]:
    return ("", "", "") if side is None else (side["value"], side["mode"], side["diameters"])


def _setup_text(values: dict) -> str:
    wire = values["wire"] or _wire_name(values["wire_name"], "") or "провод (нет в ЭТК)"
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


def _sites(task, operation, people) -> set[str]:
    """Sites the PF belongs to: the one it is handed to and those of its assignees."""
    sites = {task.handed_to} - {"", None}
    for identifier in operation.operator_ids:
        operator = people.get(identifier)
        if operator and operator.site:
            sites.add(operator.site)
    return sites


def _site_allowed(task, operation, people, actor) -> bool:
    viewer = people.get(actor.id)
    site = viewer.site if viewer else ""
    if not site:
        return True
    return all(other == site for other in _sites(task, operation, people))


@dataclass(frozen=True)
class PoolUnit:
    """A physical БД.ОБ unit asked for by the queue: `accept` — the row machine ids it serves (its own inventory number
    for a single-unit row, its model's key for a pool row)."""
    id: UUID
    number: str
    site: str
    accept: frozenset[str]


def resolve_unit(record, catalog: EquipmentCatalog) -> PoolUnit:
    """The БД.ОБ record as a physical unit of the catalog; unknown, a calculation row «.X» / «(R)» or a non-machine is
    refused. Everything is read from the catalog (model, site) — the record only names the unit."""
    if record is None:
        raise ProductionValidationError("Станка нет в БД.ОБ")
    fields = {" ".join(str(key).split()).casefold(): value for key, value in (record.fields or {}).items()}
    number = _number(fields.get("инв. номер") or fields.get("inventorynumber"))
    machine = catalog.machine({"inventory_number": number, "equipment_model_id": str(record.equipment_model_id or "")})
    model = catalog.models.get(machine.key) if machine is not None else None
    unit = next((item for item in model.physical if item.number == number), None) if model else None
    if unit is None or not unit.machine_type:  # a hand tool with an ОБ number (ОБ.048 DT-8920) is no machine
        raise ProductionValidationError("Это не физический станок БД.ОБ — выберите станок с инвентарным номером")
    return PoolUnit(record.id, unit.number, unit.site, frozenset({unit.number, machine.key}))


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
    active: dict[UUID, list] = {}
    for member in members:
        active.setdefault(member.group_id, []).append(member)
        task = running.get(member.task_id)
        operation = task.operation(member.operation_id) if task else None
        left = open_work(task, operation) if operation else None
        if left:
            kept.setdefault(member.group_id, []).append((member.id, task, operation, left))
    # `check_access` for every group with open work at once: the members above are its active members, their tasks are
    # the running ones already read plus one batched read of the rest (handed over, done); a viewer without a site
    # sees every group and reads nothing more.
    viewer = people.get(actor.id)
    site = viewer.site if viewer else ""
    tasks = dict(running)
    if site:
        others = {member.task_id for group_id in kept for member in active[group_id]} - set(tasks)
        tasks.update(uow.production.get_many(others) if others else {})
    visible = []
    for group in groups:
        if group.id not in kept:
            continue
        try:
            check_group_sites(group, site, active[group.id] if site else (), tasks.get, people.get)
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
    # A standalone PF worked on its own card (open individual session, active or paused) is listed but blocked — the
    # screen keeps its card, and «Готово» refuses it with the same text (`mark_done`).
    worked = set(uow.session.scalars(select(ProductionWorkSessionRow.operation_id).where(
        ProductionWorkSessionRow.task_id.in_(tuple(running)),
        ProductionWorkSessionRow.status.in_((WorkStatus.ACTIVE.value, WorkStatus.PAUSED.value))))) if running else set()
    for task in running.values():
        for operation in task.operations:
            if operation.inputs or not operation.etk_ops or operation.id in member_ops:
                continue
            left = open_work(task, operation)
            if left and _site_allowed(task, operation, people, actor):
                result.append({"task": task, "operation": operation, "planned": left[0], "done": left[1], "group": None,
                               "document": None, "blocked": WORK_OPEN if operation.id in worked else None,
                               "names": [people.name(identifier) for identifier in operation.operator_ids]})
    return result


def document_keys(candidates: list[dict]) -> set[DocumentKey]:
    """Issued ETK revisions the standalone candidates are read from (grouped rows carry their frozen document)."""
    return {document_key(item["task"]) for item in candidates if item["group"] is None}


def queue_rows(candidates: list[dict], documents: Mapping[DocumentKey, Mapping | None],
               machine_id: str | None = None, *, unit: PoolUnit | None = None,
               catalog: EquipmentCatalog = EMPTY) -> list[dict]:
    """Rows from `collect` plus the standalone ETKs; pure — no unit of work. `machine_id`: None or `all` — every row once
    (its machine = the first the ETK names); `none` — rows whose ETK names no machine; an id — rows whose ETK names it
    among its machines (a row naming several is on each of their lists, with that machine as its own). `unit` (wins
    over `machine_id`) — the rows of that physical unit plus its model's pool rows (owner 20.11: «любой свободный»)."""
    rows = []
    for item in candidates:
        task, operation, group = item["task"], item["operation"], item["group"]
        document = item["document"] if group is not None else documents.get(document_key(task))
        values = cut_values(document, operation, known_cut=group is not None, catalog=catalog)
        if values is None:
            continue
        named = dict(values["machines"])
        if unit is not None:
            shown = next((identity for identity in named if identity in unit.accept), None)
            if shown is None:
                continue
        elif machine_id in (None, ALL_MACHINES):
            shown = next(iter(named), None)
        elif machine_id == NO_MACHINE:
            if named:
                continue
            shown = None
        elif machine_id in named:
            shown = machine_id
        else:
            continue
        sides = values["sides"]
        project = row_project(task, document)
        rows.append({
            "key": f"{task.id}:{operation.id}", "task_id": str(task.id), "operation_id": str(operation.id),
            "task_version": task.version, "task_number": task.number, "project": project, "item": task.item,
            "group_id": group["id"] if group else None, "group_number": group["number"] if group else None,
            "group_state": group["state"] if group else None,
            "machine_id": shown, "machine_name": named[shown] if shown is not None else NO_MACHINE_NAME,
            "machines": [{"machine_id": identity, "machine_name": name} for identity, name in named.items()],
            "wire": values["wire"], "wire_name": _wire_name(values["wire_name"], values["wire"]),
            "wire_title": values["wire_name"] or values["wire"], "length_mm": _plain(values["length"]) or None,
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
        # Opaque setup identity: the screen filters the «Все станки» snapshot per machine and marks setup changes again.
        row["setup_key"] = digest(setup)[:16]
        row["setup_change"] = setup != previous
        previous = setup
    return rows


def queue_version(rows: list[dict]) -> str:
    """Revision of everything the rows show (names, permissions, warnings, setup values included): the screen keeps its
    rows while it is unchanged."""
    return digest(rows)


def snapshot(rows: list[dict], catalog: EquipmentCatalog = EMPTY) -> dict:
    """«Все станки» rows (every row once, `queue_rows` without a machine) with the machine choices and the version, from
    one read: the screen derives each machine's list from it (a row is on every machine its `machines` names; none —
    the «Станок не указан» bucket), the same rows the per-machine route returns."""
    return {"machine_id": ALL_MACHINES, "equipment_id": None, "version": queue_version(rows), "rows": rows,
            "machines": machines(rows, catalog)}


def machines(rows: list[dict], catalog: EquipmentCatalog = EMPTY) -> list[dict]:
    """Machines with open cut work: rows, pieces left, projects. `rows` — every row once (`queue_rows` without a machine);
    a row naming several machines counts on each. «Все станки» (`machine_id: "all"`) first, with every row once; then
    by catalog code and label (a pool's key is a model uuid); no open cut work at all — an empty list."""
    codes = catalog.codes()
    def entry(identity, name):
        return {"machine_id": identity, "machine_name": name, "rows": 0, "left": Decimal(0), "projects": set()}

    def add(target, row):
        target["rows"] += 1
        target["left"] += Decimal(row["left"])
        target["projects"].add(row["project"])

    total = entry(ALL_MACHINES, ALL_MACHINES_NAME)
    found: dict[str | None, dict] = {}
    for row in rows:
        add(total, row)
        for machine in row["machines"] or [{"machine_id": None, "machine_name": NO_MACHINE_NAME}]:
            add(found.setdefault(machine["machine_id"], entry(machine["machine_id"], machine["machine_name"])), row)
    listed = ([total] if rows else []) + [found[key] for key in sorted(found, key=lambda key: (
        key is None, codes.get(key or "", key or ""), found[key]["machine_name"], key or ""))]
    return [{**item, "left": _count(item["left"]), "projects": len(item["projects"])} for item in listed]


def receipt_id(key: str) -> UUID:
    return uuid5(_RECEIPTS, f"cutting-queue:{key}")


def mark_done(task_id: UUID, operation_id: UUID, quantity: int, key: str, actor, worker, now: datetime, uow, *,
              document: Mapping | None = None, manual_output: bool = False, unit: PoolUnit | None = None,
              catalog: EquipmentCatalog = EMPTY) -> dict:
    """«Готово» on a queue row: the row's pieces as the PF's manual output, in one transaction with the receipt.
    `document` — the task's pinned issued ETK (standalone PFs: proves the PF is a cable cut; the notice's project).
    `manual_output` — the presser may enter finished pieces («Ввод готовых»: production edit); required of an account
    without an employee record or with an empty site, which has no site to limit it.
    `unit` — the physical machine that made it: must serve one of the row's machines/models and its site; goes into the
    receipt."""
    fingerprint = digest([str(task_id), str(operation_id), str(quantity), str(worker.id)] + ([str(unit.id)] if unit else []))
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
        presser = uow.operators.get(actor.id)
        # No record or no site = no site to limit the presser (`_site_allowed` lets every site through): only someone
        # who may enter finished pieces («Ввод готовых», production edit) presses «Готово» then.
        if not manual_output and (presser is None or not presser.site):
            raise PermissionError(NO_RECORD if presser is None else NO_SITE)
        member = uow.cutting.for_operation(operation_id)
        if member is not None:
            # Lock order of every cutting path: project → group → task.
            group = uow.cutting.get(member.group_id, lock=True)
            if group.state not in ("open", "frozen") or recipe_kind(group.recipe) != CUT:
                raise ProductionValidationError(NOT_A_CUT)
            check_access(group, uow, actor)
            if uow.session.scalar(select(CuttingWorkRow.session_id).where(CuttingWorkRow.group_id == group.id,
                                                                   CuttingWorkRow.closed.is_(False)).limit(1)) is not None:
                raise ProductionValidationError(GROUP_STARTED.format(number=group.number))
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
        if unit is not None:
            values = cut_values(document, operation, known_cut=member is not None, catalog=catalog)
            named = dict((values or {}).get("machines") or [])
            if not any(identity in unit.accept for identity in named):
                raise ProductionValidationError(
                    f"Станок {unit.number} другой модели, чем в ЭТК этой строки "
                    f"({', '.join(named.values()) or NO_MACHINE_NAME})")
            people = _People(uow)
            sites = _sites(task, operation, people)
            # The worker's hands at the machine count even before the PF has assignees; the actor's own site does not —
            # an admin may enter «Готово» for a worker of another site (his access is checked by _site_allowed).
            known = people.get(worker.id)
            sites |= {known.site} - {"", None} if known else set()
            if unit.site and any(other != unit.site for other in sites):
                raise ProductionValidationError(f"Станок {unit.number} стоит на площадке {unit.site}, а задание — другой площадки")
        if member is None and uow.session.scalar(select(ProductionWorkSessionRow.id).where(ProductionWorkSessionRow.operation_id == operation_id,
                ProductionWorkSessionRow.status.in_((WorkStatus.ACTIVE.value, WorkStatus.PAUSED.value))).limit(1)) is not None:
            raise ProductionValidationError(WORK_OPEN)
        uow.production.lock(task_id)
        task = uow.production.get(task_id)
        operation = task.operation(operation_id)
        assigned = False
        performer = worker
        if worker.id not in operation.operator_ids:
            if worker.id == actor.id and (presser is None or not presser.installer):
                # Owner 20.11 (final): anyone with access to the site's tasks may press «Готово». Not a монтажник
                # (master, technologist): the pieces are posted as «Ввод готовых» — under the presser, nobody assigned.
                performer = None
            else:
                # A монтажник (himself or the one worked for) joins the PF's assignees first; `assign_task` refusals apply.
                assign_task(AssignProductionCommand(
                    task_id=task.id, expected_version=task.version, operation_id=operation.id,
                    operator_ids=(*operation.operator_ids, worker.id),
                    requested_start=operation.planned_start if operation.operator_ids else None, actor=actor, now=now), uow)
                assigned = True
        box, output, _filled, _closed = enter_manual_output(ManualOutputCommand(
            task_id=task_id, operation_id=operation_id, good_quantity=Decimal(quantity), actor=actor, now=now,
            performer=performer), uow)
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
        # The «Журнал» line: who pressed (the event's actor, and by name), whose pieces, project, КБ, task, quantity.
        made_by = performer or actor
        uow.audit.add(AuditEvent(id=receipt_id(key), aggregate_id=output.id, event_type=RECEIPT, actor=actor,
                                 occurred_at=now, details={
                                     "project": project, "item_index": task.item, "task_number": task.number,
                                     "quantity": quantity, "performer": made_by.name, "pressed_by": actor.name,
                                     "operator_id": str(made_by.id), "pressed_by_id": str(actor.id),
                                     "task_id": str(task_id), "operation_id": str(operation_id),
                                     "replay_key": key, "fingerprint": fingerprint, "result": result,
                                     **({"equipment_id": str(unit.id), "inventory_number": unit.number} if unit else {})}))
        uow.session.flush()
        uow.commit()
        return result
