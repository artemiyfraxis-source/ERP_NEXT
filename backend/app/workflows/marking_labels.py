"""Compose equipment identity, pinned production marks and the targeted print queue."""

import base64
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.modules.production.api import production_uow_factory
from app.modules.production.application import ConcurrentProductionUpdate, issue_mark_serials_in
from app.modules.production.cutting_application import locked_group
from app.modules.production.domain import ProductionStatus
from app.modules.production.marking_sources import check_materials, labels
from app.workflows.marking_progress import printed_counts, remaining_rows, serial_range_of, task_progress
from app.modules.references.equipment import INV_KEY
from app.modules.references.infrastructure import EquipmentMachineRow
from app.modules.techcards.etk_read import issued_card_for_item, pinned_card
from app.modules.techcards.infrastructure import ProjectTechcardRow, SqlAlchemyTechcardRepository
from app.modules.warehouse.devices import PrinterRow, printer_online
from app.modules.warehouse.label_jobs import (
    JobFinished, LabelJobRow, add_marking_job, ancestor_sequence, cancel_pending_marking, expire_marking, marking_halts,
    pending_marking_by_printer, reprint_tree,
)
from app.modules.warehouse.marking_profiles import MarkingBatchRow, MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.marking_render import Geometry, halves, render
from app.modules.warehouse.printer_commands import (
    MIN_AGENT_VERSION, CommandBusy, agent_executes_commands, command_view, expire_commands, latest_command, marking_printers,
    request_clear,
)
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_edit_principal, require_section, section_allowed

router = APIRouter(prefix="/api/production/marking", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
# Named gates of the access contract (tests/identity/test_level_gates.py DEPENDENCIES), added after the levels transfer.
marking_work_principal = require_section(("myops", "production"), "view")
marking_print_principal = require_section("print", "edit")
marking_config_principal = require_section("erpref", "edit")
marking_read_principal = require_section(("myops", "production", "erpref"), "view")


def print_principal(
    principal: Annotated[Principal, Depends(marking_work_principal)],
    _printing: Annotated[Principal, Depends(marking_print_principal)],
):
    return principal


PrintUser = Annotated[Principal, Depends(print_principal)]
ConfigUser = Annotated[Principal, Depends(marking_config_principal)]
ReadUser = Annotated[Principal, Depends(marking_read_principal)]
STALE = "Источник или профиль изменился — обновите предпросмотр"
STATUS_LABELS = {"pending": "В очереди", "taken": "Печатается", "spooled": "Передано принтеру",
                 "failed": "Ошибка печати", "unknown": "Результат неизвестен", "cancelled": "Отменено"}


# Spooled jobs the agent (2.9.6+) keeps watching in the Windows queue; the printer may hold them (peel-off mode).
PRINT_STATE_LABELS = {"queued": "В очереди принтера", "printed": "Напечатано", "error": "Ошибка принтера",
                      "cancelled": "Отменено", "untracked": "Передано принтеру"}
STUCK_AFTER = timedelta(minutes=3)
STUCK_TEXT = "Принтер ждёт (снимите этикетку / проверьте принтер)"
OLD_AGENT_HOLD = ("Агент печати на ПК принтера — версии {version}: печать с отделителем выполняет агент "
                  f"{'.'.join(map(str, MIN_AGENT_VERSION))} и новее, задания ждут его обновления")
HALT_TEXT = ("Печать партии остановлена на этикетке №{sequence}: она не напечатана — не прошла проверка отделителя Godex. "
             "Включите отделитель, затем «Перепечатать номера…» этой этикетки (партия продолжится после неё), "
             "«Продолжить без неё» или «Отменить оставшиеся».")


def print_state(job):
    if job.status != "spooled":
        return None
    receipt = job.receipt or {}
    if not receipt.get("tracked"):
        return "untracked"
    return receipt.get("print_state") or "queued"


def _moment(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=None) if value is not None and value.tzinfo else value


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def checked(action):
    try:
        return action()
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, error.args[0]) from error
    except (ConcurrentProductionUpdate, JobFinished, CommandBusy) as error:
        raise HTTPException(409, str(error)) from error
    except IntegrityError as error:
        raise HTTPException(409, "Данные изменились — повторите запрос") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def audit(uow, actor, now, identifier, action, **details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=identifier, event_type=f"production.marking_{action}",
                            actor=actor, occurred_at=now, details=details))


def profile_view(row):
    return {"id": str(row.id), "equipment_id": str(row.equipment_id) if row.equipment_id else None,
            "name": row.name, "version": row.version, "calibrated": row.calibrated, "geometry": row.geometry}


def binding_view(row, session=None):
    if row is None:
        return None
    result = {"version": row.version, "machine_id": row.machine_id, "printer": row.printer}
    if session is not None:
        # «Очистить принтер»: the latest command on this queue and how it ended; batch labels still waiting for it.
        result["command"] = command_view(latest_command(session, row.machine_id, row.printer))
        result["pending_jobs"] = sum(len(items) for items in pending_marking_by_printer(session, row.machine_id, row.printer).values())
    return result


def raster_geometry(value):
    """What calibration proves: everything but the peel-off flag (a driver setting, not the raster)."""
    return {key: item for key, item in Geometry.model_validate(value).model_dump().items() if key != "peel"}


def resolve_equipment(session):
    """Inventory is a bootstrap locator only; existing stable UUID bindings never move."""
    machines = list(session.scalars(select(EquipmentMachineRow)))
    by_inventory = {}
    for machine in machines:
        inventory = str(machine.fields.get(INV_KEY) or machine.fields.get("inventoryNumber") or "").strip().upper()
        by_inventory.setdefault(inventory, []).append(machine)
    for profile in session.scalars(select(MarkingProfileRow).where(MarkingProfileRow.equipment_id.is_(None))):
        candidates = by_inventory.get(profile.inventory_number, [])
        if len(candidates) == 1:
            session.execute(update(MarkingProfileRow).where(MarkingProfileRow.id == profile.id,
                MarkingProfileRow.equipment_id.is_(None)).values(equipment_id=candidates[0].id))
    session.flush()
    return {machine.id: machine for machine in machines}


@router.get("/machines")
def machines(_principal: ReadUser, factory: Factory, now: Now):
    with factory() as uow:
        catalog = resolve_equipment(uow.session)
        expire_commands(uow.session, now)
        profiles = list(uow.session.scalars(select(MarkingProfileRow).order_by(MarkingProfileRow.inventory_number, MarkingProfileRow.name)))
        grouped = {}
        for profile in profiles:
            machine = catalog.get(profile.equipment_id)
            if machine is None:
                continue
            item = grouped.setdefault(str(machine.id), {"equipment_id": str(machine.id),
                "inventory_number": machine.fields.get(INV_KEY, profile.inventory_number), "name": machine.display_text,
                "binding": binding_view(uow.session.get(MarkingBindingRow, machine.id), uow.session), "profiles": []})
            item["profiles"].append(profile_view(profile))
        printers, peel = [], {}
        for printer, pc in uow.session.execute(select(PrinterRow, ScanAgentRow).join(ScanAgentRow)):
            seen = printer.last_seen.replace(tzinfo=now.tzinfo) if printer.last_seen.tzinfo is None else printer.last_seen
            online = printer.present and seen >= now - LIVE_FOR
            hold = printer.hold if online else ""
            if printer.kind == "gdi_png" and not hold and not agent_executes_commands(pc.version):
                if pc.machine_id not in peel:
                    peel[pc.machine_id] = {item["name"] for item in marking_printers(uow.session, pc.machine_id) if item["peel"]}
                if printer.name in peel[pc.machine_id]:
                    hold = OLD_AGENT_HOLD.format(version=pc.version or "без номера")  # claim_marking holds the jobs
            # Cloned PCs share a hostname: the IP and machine id tell them apart in the printer list.
            printers.append({"machine_id": pc.machine_id, "pc": pc.pc_name, "ip": pc.ip_lan or pc.ip_seen, "name": printer.name,
                "driver": printer.driver, "port": printer.port, "kind": printer.kind, "present": printer.present,
                "online": online, "hold": hold})
        uow.commit()
        return {"machines": list(grouped.values()), "printers": printers}


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    calibrated: bool
    geometry: Geometry
    # The material name the operator picks («Флажок 100×20»); absent — unchanged.
    name: str | None = Field(default=None, max_length=200)


def profile_name(session, profile, requested):
    """A renamed profile keeps its calibration; the name is unique per machine (equipment or inventory number)."""
    if requested is None:
        return profile.name
    name = " ".join(requested.split())
    if not name:
        raise ValueError("Укажите название профиля")
    if len(name) > 80:
        raise ValueError("Название профиля — не длиннее 80 знаков")
    owner = MarkingProfileRow.inventory_number == profile.inventory_number
    if profile.equipment_id:
        owner = owner | (MarkingProfileRow.equipment_id == profile.equipment_id)
    taken = session.scalars(select(MarkingProfileRow.name).where(owner, MarkingProfileRow.id != profile.id))
    if any(other.casefold() == name.casefold() for other in taken):
        raise ValueError(f"У этого станка уже есть профиль «{name}»")
    return name


class NewProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)
    copy_from: UUID | None = None
    geometry: Geometry | None = None


@router.post("/machines/{equipment_id}/profiles")
def create_profile(equipment_id: UUID, payload: NewProfileInput, principal: ConfigUser, factory: Factory, now: Now):
    """A new label size for a machine. Never calibrated: a copied calibration is not valid for another size or printer."""
    def create():
        with factory() as uow:
            machine = resolve_equipment(uow.session).get(equipment_id)
            if machine is None:
                raise KeyError("Оборудование маркировки не найдено")
            inventory = str(machine.fields.get(INV_KEY) or machine.fields.get("inventoryNumber") or "").strip().upper()
            if not inventory:
                raise ValueError("У станка нет инвентарного номера — профиль маркировки не заводится")
            owner = (MarkingProfileRow.inventory_number == inventory) | (MarkingProfileRow.equipment_id == equipment_id)
            own = list(uow.session.scalars(select(MarkingProfileRow).where(owner)))
            # Only a marking machine (it already has a profile or a bound printer) gets one: not a crimper by its UUID.
            if not own and uow.session.scalar(select(MarkingBindingRow.equipment_id)
                                               .where(MarkingBindingRow.equipment_id == equipment_id)) is None:
                raise ValueError("Это не станок маркировки: у него нет ни профилей, ни привязанного принтера")
            name = " ".join(payload.name.split())
            if not name:
                raise ValueError("Укажите название профиля")
            if len(name) > 80:
                raise ValueError("Название профиля — не длиннее 80 знаков")
            source = None
            if payload.copy_from is not None:
                source = uow.session.get(MarkingProfileRow, payload.copy_from)
                if source is None:
                    raise KeyError("Профиль-образец не найден")
            if payload.geometry is not None:
                geometry = payload.geometry.model_dump()
                if source is not None and "peel" not in payload.geometry.model_fields_set:
                    geometry["peel"] = bool((source.geometry or {}).get("peel"))
            elif source is not None:
                geometry = Geometry.model_validate(source.geometry).model_dump()
            else:
                raise ValueError("Укажите разметку или профиль, с которого её копировать")
            if source is not None and source.equipment_id != equipment_id:
                # Offsets are the other printer's calibration result: the new profile starts from zero and is calibrated
                # on its own printer.
                geometry["offset_x_mm"] = geometry["offset_y_mm"] = 0
            if own:
                # The peel-off dispenser belongs to the machine, not to the copied layout.
                geometry["peel"] = any(bool((row.geometry or {}).get("peel")) for row in own)
            zones = geometry["zones"]
            if len(zones) == 2 and zones[0]["x"] >= zones[1]["x"]:
                raise ValueError("Текст 1 — левая половина флажка: его зона должна начинаться левее зоны «Текст 2»")
            if any(other.name.casefold() == name.casefold() for other in own):
                raise ConcurrentProductionUpdate(f"У этого станка уже есть профиль «{name}»")
            row = MarkingProfileRow(id=uuid4(), equipment_id=equipment_id, inventory_number=inventory, name=name,
                                    version=1, calibrated=False, geometry=geometry)
            uow.session.add(row)
            try:
                uow.session.flush()
            except IntegrityError as error:
                raise ConcurrentProductionUpdate(f"У этого станка уже есть профиль «{name}»") from error
            audit(uow, principal.actor, now, row.id, "profile_created", equipment_id=str(equipment_id), name=name,
                  copy_from=str(payload.copy_from) if payload.copy_from else None,
                  width_mm=geometry["width_mm"], height_mm=geometry["height_mm"])
            result = profile_view(row)
            uow.commit()
            return result
    return checked(create)


@router.put("/profiles/{profile_id}")
def save_profile(profile_id: UUID, payload: ProfileInput, principal: ConfigUser, factory: Factory, now: Now):
    def save():
        with factory() as uow:
            profile = uow.session.get(MarkingProfileRow, profile_id)
            if profile is None:
                raise KeyError("Профиль не найден")
            zones = payload.geometry.zones
            if len(zones) == 2 and zones[0].x >= zones[1].x:
                raise ValueError("Текст 1 — левая половина флажка: его зона должна начинаться левее зоны «Текст 2»")
            geometry = payload.geometry.model_dump()
            calibrated = payload.calibrated and raster_geometry(geometry) == raster_geometry(profile.geometry)
            name = profile_name(uow.session, profile, payload.name)
            renamed = {"name": name, "previous_name": profile.name} if name != profile.name else {}
            peel = {"peel": geometry["peel"]} if bool((profile.geometry or {}).get("peel")) != geometry["peel"] else {}
            try:
                changed = uow.session.execute(update(MarkingProfileRow).where(MarkingProfileRow.id == profile_id,
                    MarkingProfileRow.version == payload.version).values(name=name, geometry=geometry, calibrated=calibrated,
                                                                        version=payload.version + 1))
            except IntegrityError as error:
                # Another session took the name between our check and this write (uq_marking_profile_preset).
                raise ValueError(f"У этого станка уже есть профиль «{name}»") from error
            if changed.rowcount != 1:
                raise ConcurrentProductionUpdate(STALE)
            audit(uow, principal.actor, now, profile_id, "profile_saved", version=payload.version + 1, calibrated=calibrated,
                  **renamed, **peel)
            uow.session.refresh(profile)
            result = profile_view(profile)
            uow.commit()
            return result
    return checked(save)


class BindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, strict=True)
    machine_id: str = Field(min_length=1, max_length=80)
    printer: str = Field(min_length=1, max_length=255)


@router.put("/machines/{equipment_id}/binding")
def save_binding(equipment_id: UUID, payload: BindingInput, principal: ConfigUser, factory: Factory, now: Now):
    def save():
        with factory() as uow:
            resolve_equipment(uow.session)
            if not uow.session.scalar(select(MarkingProfileRow.id).where(MarkingProfileRow.equipment_id == equipment_id)):
                raise KeyError("Оборудование маркировки не найдено")
            if not printer_online(uow.session, payload.machine_id, payload.printer, now):
                raise ConcurrentProductionUpdate("Принтер не на связи")
            if payload.version == 0:
                row = MarkingBindingRow(equipment_id=equipment_id, version=1, machine_id=payload.machine_id, printer=payload.printer)
                uow.session.add(row)
                uow.session.flush()
            else:
                changed = uow.session.execute(update(MarkingBindingRow).where(MarkingBindingRow.equipment_id == equipment_id,
                    MarkingBindingRow.version == payload.version).values(version=payload.version + 1,
                        machine_id=payload.machine_id, printer=payload.printer))
                if changed.rowcount != 1:
                    raise ConcurrentProductionUpdate(STALE)
                row = uow.session.get(MarkingBindingRow, equipment_id)
            audit(uow, principal.actor, now, equipment_id, "binding_saved", **binding_view(row))
            result = binding_view(row)
            uow.commit()
            return result
    return checked(save)


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID | None = None
    operation_id: UUID | None = None
    group_id: UUID | None = None
    version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def one_source(self):
        if not ((self.task_id is not None and self.operation_id is not None and self.group_id is None)
                or (self.group_id is not None and self.task_id is None and self.operation_id is None)):
            raise ValueError("Укажите задание и операцию либо группу резки")
        return self


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: UUID
    profile_version: int = Field(ge=1, strict=True)
    source: SourceInput
    selected: list[str] = Field(default_factory=list, max_length=500)
    # «Чередовать по изделию»: per item, fixed marks then numbered ones (XS1, A1 №1, XS1, A1 №2 …).
    interleave: bool = False
    # «Печатать оставшиеся» (owner 20.11): skip labels already printed or queued for these tasks (marking_progress).
    remaining: bool = False


class BatchInput(PreviewInput):
    manifest_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"\S")
    reprint_of: UUID | None = None
    reason: str = Field(default="", max_length=1000)


def task_access(task, operation_id, principal, uow, *, historical=False):
    if task is None:
        raise KeyError("Задание не найдено")
    operation = task.operation(operation_id)
    if principal.actor.id not in operation.operator_ids and not section_allowed(principal, ("production",), "view"):
        raise PermissionError("Операция не назначена сотруднику")
    viewer = uow.operators.get(principal.actor.id)
    sites = {task.handed_to} - {""}
    for identifier in operation.operator_ids:
        operator = uow.operators.get(identifier)
        if operator and operator.site:
            sites.add(operator.site)
    if viewer and viewer.site and any(site != viewer.site for site in sites):
        raise PermissionError("Задание другой площадки")
    if not historical and task.status is ProductionStatus.CANCELLED:
        raise ConcurrentProductionUpdate("Задание отменено")


def issued_document(task, uow, *, lock=True):
    """The task's pinned ETK document. `lock=False` — a plain read for views (GET /progress): no FOR UPDATE on the card."""
    techcards = SimpleNamespace(techcards=SqlAlchemyTechcardRepository(uow.session))
    card = issued_card_for_item(techcards, task.project, task.item)
    if card is None:
        raise KeyError("Нет выданной ЭТК по этой позиции")
    if lock:
        uow.session.scalar(select(ProjectTechcardRow).where(ProjectTechcardRow.id == card.id).with_for_update()
                           .execution_options(populate_existing=True))
        card = issued_card_for_item(techcards, task.project, task.item)
        if card is None:
            raise ConcurrentProductionUpdate(STALE)
    shown, revision = pinned_card(techcards, card, task.techcard_revision)
    if task.techcard_revision is not None and revision["fallback"]:
        raise ConcurrentProductionUpdate("Закреплённая ревизия ЭТК не найдена")
    return shown.document, {"card_id": str(card.id), "card_version": card.version, "revision": shown.revision}


def source_rows(source, selected, principal, uow, now, interleave=False, remaining=False):
    if len(set(selected)) != len(selected):
        raise ValueError("Надпись выбрана несколько раз")
    if source.group_id:
        group = locked_group(source.group_id, source.version, uow, principal.actor, now)
        members = uow.cutting.members(group.id)
        entries = [(uow.production.get(member.task_id), member.operation_id, member) for member in members]
        group_snapshot = {"group_id": str(group.id), "version": group.version, "state": group.state}
    else:
        # Group operations always lock project -> group -> tasks, including this individual view.
        member = uow.cutting.for_operation(source.operation_id)
        initial_member_id = member.id if member else None
        group = uow.cutting.get(member.group_id) if member else None
        if group:
            group = locked_group(group.id, group.version, uow, principal.actor, now)
        else:
            uow.production.lock(source.task_id)
        task = uow.production.get(source.task_id)
        if task is None:
            raise KeyError("Задание не найдено")
        if task.version != source.version:
            raise ConcurrentProductionUpdate(STALE)
        member = uow.cutting.for_operation(source.operation_id)
        if (member.id if member else None) != initial_member_id:
            raise ConcurrentProductionUpdate("Состав группы изменился — обновите предпросмотр")
        entries, group_snapshot = [(task, source.operation_id, member)], None
    rows, snapshots = [], []
    # One print-queue read for every task of the source (keys carry the task id), not one per task under the locks.
    printed = printed_counts(uow.session, [task.id for task, _operation, _member in entries if task]) if remaining else None
    for task, operation_id, member in entries:
        task_access(task, operation_id, principal, uow)
        if member:
            document = member.row_snapshot.get("document")
            if not document:
                raise ConcurrentProductionUpdate("В группе нет сохранённой ЭТК")
            if group.state == "open":
                _current_document, current = issued_document(task, uow)
                if current["card_version"] != member.row_snapshot.get("validated_card_version"):
                    raise ConcurrentProductionUpdate("ЭТК группы изменилась — требуется повторная проверка группы резки")
            revision = {"member_id": str(member.id), "revision": member.row_snapshot.get("techcard_revision")}
        else:
            document, revision = issued_document(task, uow)
        # Calculate and reserve through the established issuance semantics in this same transaction.
        serial = issue_mark_serials_in(task, False, principal.actor, now, uow)
        expanded = labels(document, task, serial, selected, interleave)
        if remaining:
            expanded = remaining_rows(expanded, printed)
        if any(row["serial"] is not None for row in expanded):
            issue_mark_serials_in(task, True, principal.actor, now, uow)
        rows.extend(expanded)
        snapshots.append({"task_id": str(task.id), "operation_id": str(operation_id), "version": task.version,
            "quantity": task.quantity, "serial": list(serial) if serial else None,
            "document_hash": digest(document), **revision})
    if remaining and not rows:
        raise ValueError("Все выбранные надписи уже напечатаны или стоят в очереди печати")
    if selected and not remaining and set(selected) != {row["key"] for row in rows}:
        raise ConcurrentProductionUpdate("Выбранные надписи изменились — обновите предпросмотр")
    if not rows:
        raise ValueError("В ЭТК нет выбранных надписей fields.marks для печати")
    if sum(row["quantity"] for row in rows) > 500:
        raise ValueError("За один раз — не более 500 этикеток")
    check_materials(rows)
    return rows, {"group": group_snapshot, "tasks": snapshots}


def profile_target(session, profile_id, version, now, *, calibration=False, online=True):
    profile = session.scalar(select(MarkingProfileRow).where(MarkingProfileRow.id == profile_id).with_for_update())
    if profile is None:
        raise KeyError("Профиль не найден")
    if profile.version != version:
        raise ConcurrentProductionUpdate(STALE)
    if not profile.equipment_id:
        raise ConcurrentProductionUpdate("Профиль не связан с оборудованием")
    if not calibration and not profile.calibrated:
        raise ConcurrentProductionUpdate("Профиль не откалиброван")
    binding = session.scalar(select(MarkingBindingRow).where(MarkingBindingRow.equipment_id == profile.equipment_id).with_for_update())
    if not binding:
        raise ConcurrentProductionUpdate("Оборудование не связано с принтером")
    if online and not printer_online(session, binding.machine_id, binding.printer, now):
        raise ConcurrentProductionUpdate("Принтер не на связи")
    return profile, binding


def build_preview(payload, principal, uow, now):
    rows, source = source_rows(payload.source, payload.selected, principal, uow, now, payload.interleave,
                               payload.remaining)
    profile, binding = profile_target(uow.session, payload.profile_id, payload.profile_version, now, online=False)
    geometry = Geometry.model_validate(profile.geometry)
    rendered, cache = [], {}
    for row in rows:
        if row["text"] not in cache:
            png, dimensions = render(row["text"], geometry)
            cache[row["text"]] = {"png_b64": base64.b64encode(png).decode("ascii"), "sha256": hashlib.sha256(png).hexdigest(),
                                  "halves": halves(row["text"], len(geometry.zones))}
        rendered.append({**row, **cache[row["text"]]})
    result = {"profile_id": str(profile.id), "profile_version": profile.version, "binding_version": binding.version,
        "profile_snapshot": {"equipment_id": str(profile.equipment_id), "geometry": profile.geometry},
        "machine_id": binding.machine_id, "printer": binding.printer, "source_snapshot": source,
        "labels": rendered, "total": sum(row["quantity"] for row in rows), **dimensions}
    return {**result, "manifest_hash": digest(result)}, binding


@router.post("/preview")
def preview(payload: PreviewInput, principal: PrintUser, factory: Factory, now: Now):
    def run():
        with factory() as uow:
            result, _binding = build_preview(payload, principal, uow, now)
            uow.commit()
            return result
    return checked(run)


def label_rows(manifest):
    """The manifest label of every job, by sequence − 1 (persist_batch expands each label by its quantity)."""
    return [label for label in manifest.get("labels", []) for _ in range(int(label.get("quantity") or 1))]


def batch_view(batch, session, now=None):
    jobs = list(session.scalars(select(LabelJobRow).where(LabelJobRow.batch_id == batch.id).order_by(LabelJobRow.sequence)))
    rows = label_rows(batch.manifest)
    statuses = {job.status for job in jobs}
    withdrawn = sum(1 for job in jobs if job.status == "cancelled")
    live = [job for job in jobs if job.status != "cancelled"]
    status = next((value for value in ("unknown", "failed", "taken", "pending") if value in statuses),
                  "cancelled" if jobs and not live else "spooled")
    states = {job.id: print_state(job) for job in jobs}
    counts = {key: sum(1 for value in states.values() if value == key) for key in PRINT_STATE_LABELS}
    status_label = STATUS_LABELS[status]
    if status == "spooled" and live and counts["printed"] == len(live):
        status_label = PRINT_STATE_LABELS["printed"]
    elif status == "spooled" and (counts["queued"] or counts["error"]):
        status_label = "Печатается"
    # «Принтер ждёт»: nothing finished for a few minutes while tracked labels still sit in the printer's queue.
    stuck = False
    if now is not None and counts["queued"]:
        marks = [_moment((job.receipt or {}).get("print_state_at")) for job in jobs if states[job.id] == "printed"]
        marks += [_moment(job.finished_at) for job in jobs if states[job.id] in ("queued", "error") and job.finished_at]
        last = max((mark for mark in marks if mark), default=None)
        stuck = last is not None and _moment(now) - last > STUCK_AFTER
    progress = f"Напечатано {counts['printed']} из {batch.total - withdrawn} · в очереди {counts['queued'] + counts['error']}"
    if withdrawn:
        progress += f" · отменено {withdrawn}"
    # A failed peel-off check stops the rest of the batch (label_jobs.marking_halts); nothing to stop once none wait.
    halts = set(marking_halts(session, [batch.id]).get(batch.id, []))
    pending = sum(1 for job in jobs if job.status == "pending")
    halt_label = HALT_TEXT.format(sequence=min(halts)) if halts and pending else None
    return {"id": str(batch.id), "status": status, "status_label": status_label, "total": batch.total,
        "reprint_of": str(batch.reprint_of) if batch.reprint_of else None,
        "printed": counts["printed"], "queued": counts["queued"], "print_errors": counts["error"],
        "cancelled": counts["cancelled"], "untracked": counts["untracked"], "tracked": counts["untracked"] == 0,
        "progress_label": progress if status == "spooled" and live and counts["untracked"] != len(live) else None,
        "withdrawn": withdrawn, "pending": pending,
        "reason": batch.reason or "",
        "stuck": stuck, "stuck_label": STUCK_TEXT if stuck else None,
        "halted": halt_label is not None, "halt_label": halt_label,
        "jobs": [{"id": str(job.id), "sequence": job.sequence, "status": job.status, "halted": job.sequence in halts,
            "text": rows[job.sequence - 1].get("text", "") if job.sequence <= len(rows) else "",
            "serial": rows[job.sequence - 1].get("serial") if job.sequence <= len(rows) else None,
            "print_state": states[job.id],
            "status_label": PRINT_STATE_LABELS[states[job.id]] if states[job.id] else STATUS_LABELS[job.status],
            "message": job_message(job),
            "spool_job_id": (job.receipt or {}).get("spool_job_id")} for job in jobs]}


def job_message(job):
    receipt = job.receipt or {}
    if job.status == "unknown" and receipt.get("outcome") in ("spooled", "failed"):
        # The agent answered after its lease ran out: the operator decides, the agent's word is a hint.
        late = STATUS_LABELS[receipt["outcome"]] + (f": {receipt['message']}" if receipt.get("message") else "")
        return f"{job.error or ''} Агент сообщил позже — {late}".strip()
    if job.status == "spooled" and receipt.get("print_state") in ("error", "cancelled") and receipt.get("print_message"):
        return receipt["print_message"]
    return job.error or receipt.get("message", "")


def batch_access(batch, principal, uow):
    if batch is None:
        raise KeyError("Партия печати не найдена")
    tasks = batch.manifest.get("source_snapshot", {}).get("tasks", [])
    if not tasks and not section_allowed(principal, ("erpref",), "edit"):
        raise PermissionError("Пробная печать доступна только настройщику оборудования")
    for item in tasks:
        task_access(uow.production.get(UUID(item["task_id"])), UUID(item["operation_id"]), principal, uow, historical=True)


def persist_batch(uow, manifest, binding, payload, principal, now, fingerprint):
    batch = MarkingBatchRow(id=uuid4(), created_by=principal.actor.id, created_at=now,
        idempotency_key=payload.idempotency_key, fingerprint=fingerprint, profile_id=UUID(manifest["profile_id"]),
        manifest=manifest, total=manifest["total"], reprint_of=getattr(payload, "reprint_of", None), reason=payload.reason)
    uow.session.add(batch)
    uow.session.flush()
    sequence = 0
    for label in manifest["labels"]:
        png = base64.b64decode(label["png_b64"])
        for _ in range(label["quantity"]):
            sequence += 1
            add_marking_job(uow.session, batch_id=batch.id, sequence=sequence, png=png, geometry=manifest,
                machine_id=binding.machine_id, printer=binding.printer, actor=principal.actor, now=now)
    audit(uow, principal.actor, now, batch.id, "queued", total=batch.total, manifest_hash=manifest["manifest_hash"],
          reprint_of=str(batch.reprint_of) if batch.reprint_of else None, reason=batch.reason)
    uow.session.flush()
    return batch


@router.post("/batches")
def enqueue(payload: BatchInput, principal: PrintUser, factory: Factory, now: Now):
    def run():
        fingerprint = digest(payload.model_dump(mode="json"))
        with factory() as uow:
            old = uow.session.scalar(select(MarkingBatchRow).where(MarkingBatchRow.created_by == principal.actor.id,
                MarkingBatchRow.idempotency_key == payload.idempotency_key))
            if old:
                batch_access(old, principal, uow)
                if old.fingerprint != fingerprint:
                    raise ConcurrentProductionUpdate("Ключ запроса уже использован")
                return batch_view(old, uow.session, now)
            manifest, binding = build_preview(payload, principal, uow, now)
            if manifest["manifest_hash"] != payload.manifest_hash:
                raise ConcurrentProductionUpdate(STALE)
            if not printer_online(uow.session, binding.machine_id, binding.printer, now):
                raise ConcurrentProductionUpdate("Принтер не на связи")
            if payload.reprint_of:
                original = uow.session.get(MarkingBatchRow, payload.reprint_of)
                batch_access(original, principal, uow)
                if not payload.reason.strip():
                    raise ValueError("Укажите причину повторной печати")
                content = lambda m: [(r["task_id"], r["key"], r["text"], r["quantity"], r["serial"]) for r in m["labels"]]
                if content(original.manifest) != content(manifest):
                    raise ConcurrentProductionUpdate("Надписи повторной печати расходятся с исходной партией")
            batch = persist_batch(uow, manifest, binding, payload, principal, now, fingerprint)
            result = batch_view(batch, uow.session, now)
            uow.commit()
            return result
    def race_safe():
        try:
            return run()
        except IntegrityError:
            # A concurrent identical request can win after our initial lookup. Its immutable
            # batch is the response, including when it has already started printing.
            with factory() as uow:
                existing = uow.session.scalar(select(MarkingBatchRow).where(MarkingBatchRow.created_by == principal.actor.id,
                    MarkingBatchRow.idempotency_key == payload.idempotency_key))
                if existing is None:
                    raise
                batch_access(existing, principal, uow)
                if existing.fingerprint != digest(payload.model_dump(mode="json")):
                    raise ConcurrentProductionUpdate("Ключ запроса уже использован")
                return batch_view(existing, uow.session, now)
    return checked(race_safe)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: UUID, principal: ReadUser, factory: Factory, now: Now):
    def run():
        with factory() as uow:
            batch = uow.session.get(MarkingBatchRow, batch_id)
            batch_access(batch, principal, uow)
            expire_marking(uow.session, now)
            result = batch_view(batch, uow.session, now)
            uow.commit()
            return result
    return checked(run)


@router.get("/progress")
def progress(principal: ReadUser, factory: Factory, group_id: UUID | None = None, task_id: UUID | None = None,
             operation_id: UUID | None = None):
    """Printed / remaining items per KB of a cutting group (labels group) or of one task operation (owner 20.11)."""
    def run():
        with factory() as uow:
            if group_id is not None:
                group = uow.cutting.get(group_id)
                if group is None:
                    raise KeyError("Группа резки не найдена")
                entries = [(uow.production.get(m.task_id), m.operation_id, m.row_snapshot.get("document"))
                           for m in uow.cutting.members(group.id)]
            elif task_id is not None and operation_id is not None:
                task = uow.production.get(task_id)
                entries = [(task, operation_id, None)]
            else:
                raise ValueError("Укажите группу резки или задание и операцию")
            counts = printed_counts(uow.session, [task.id for task, _op, _doc in entries if task is not None])
            rows = []
            for task, operation, document in entries:
                task_access(task, operation, principal, uow, historical=True)
                rows.append(task_progress(task, document or issued_document(task, uow, lock=False)[0],
                                          serial_range_of(task, uow), counts))
            return {"rows": rows, "printed": sum(row["printed"] for row in rows),
                    "remaining": sum(row["remaining"] for row in rows)}
    return checked(run)


class CalibrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    text: str = Field(default="АБВ 123", min_length=1, max_length=1000)
    print: bool = False
    idempotency_key: str = Field(default="", max_length=128)


@router.post("/profiles/{profile_id}/calibration")
def calibration(profile_id: UUID, payload: CalibrationInput, principal: ConfigUser, factory: Factory, now: Now):
    def run():
        if payload.print and not section_allowed(principal, ("print",), "edit"):
            raise PermissionError("Печать документов вам не открыта")
        with factory() as uow:
            fingerprint = digest({"calibration": str(profile_id), **payload.model_dump()})
            if payload.print:
                if not payload.idempotency_key.strip():
                    raise ValueError("Для пробной печати нужен ключ запроса")
                old = uow.session.scalar(select(MarkingBatchRow).where(MarkingBatchRow.created_by == principal.actor.id,
                    MarkingBatchRow.idempotency_key == payload.idempotency_key))
                if old:
                    if old.fingerprint != fingerprint:
                        raise ConcurrentProductionUpdate("Ключ запроса уже использован")
                    return {**old.manifest["labels"][0], **{k: old.manifest[k] for k in ("width_mm", "height_mm", "orientation", "dpi")},
                            "batch_id": str(old.id)}
            if payload.print:
                profile, binding = profile_target(uow.session, profile_id, payload.version, now, calibration=True)
            else:
                profile = uow.session.get(MarkingProfileRow, profile_id)
                if profile is None:
                    raise KeyError("Профиль не найден")
                if profile.version != payload.version:
                    raise ConcurrentProductionUpdate(STALE)
            png, dimensions = render(payload.text, Geometry.model_validate(profile.geometry), calibration=True)
            result = {"png_b64": base64.b64encode(png).decode("ascii"), "sha256": hashlib.sha256(png).hexdigest(), **dimensions}
            if payload.print:
                manifest = {"profile_id": str(profile_id), "profile_version": profile.version, "binding_version": binding.version,
                    "profile_snapshot": {"equipment_id": str(profile.equipment_id), "geometry": profile.geometry},
                    "source_snapshot": {"tasks": []}, "labels": [{**result, "quantity": 1, "text": payload.text}],
                    "total": 1, **dimensions}
                manifest["manifest_hash"] = digest(manifest)
                request = SimpleNamespace(idempotency_key=payload.idempotency_key, reason="Пробная печать")
                batch = persist_batch(uow, manifest, binding, request, principal, now, fingerprint)
                result["batch_id"] = str(batch.id)
                uow.commit()
            return result
    def race_safe():
        try:
            return run()
        except IntegrityError:
            with factory() as uow:
                existing = uow.session.scalar(select(MarkingBatchRow.id).where(MarkingBatchRow.created_by == principal.actor.id,
                    MarkingBatchRow.idempotency_key == payload.idempotency_key))
            if existing is None:
                raise
            return run()  # verifies the winning fingerprint before returning its stored PNG
    return checked(race_safe)


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["spooled", "failed"]
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


@router.post("/batches/{batch_id}/jobs/{job_id}/resolve")
def resolve(batch_id: UUID, job_id: UUID, payload: ResolveInput,
            principal: Annotated[Principal, Depends(production_edit_principal)], factory: Factory, now: Now):
    def run():
        with factory() as uow:
            batch = uow.session.get(MarkingBatchRow, batch_id)
            batch_access(batch, principal, uow)
            expire_marking(uow.session, now)
            job = uow.session.scalar(select(LabelJobRow).where(LabelJobRow.id == job_id, LabelJobRow.batch_id == batch_id)
                                     .with_for_update())
            if job is not None and job.status == "failed" and job.sequence in marking_halts(uow.session, [batch_id]).get(batch_id, []):
                # «Продолжить без неё»: the label failed its peel-off check and was never printed; the rest of the batch goes on.
                if payload.outcome != "failed":
                    raise ConcurrentProductionUpdate("Эта этикетка не печаталась — её можно только перепечатать или продолжить без неё")
                job.receipt = {**(job.receipt or {}), "halt_released": True, "halt_released_reason": " ".join(payload.reason.split())}
                uow.session.flush()
                audit(uow, principal.actor, now, batch_id, "halt_released", job_id=str(job_id), sequence=job.sequence,
                      reason=payload.reason)
                result = batch_view(batch, uow.session, now)
                uow.commit()
                return result
            changed = uow.session.execute(update(LabelJobRow).where(LabelJobRow.id == job_id,
                LabelJobRow.batch_id == batch_id, LabelJobRow.status == "unknown").values(status=payload.outcome,
                    finished_at=now, error=payload.reason if payload.outcome == "failed" else None))
            if changed.rowcount != 1:
                raise ConcurrentProductionUpdate("Задание не ожидает проверки результата")
            audit(uow, principal.actor, now, batch_id, "reconciled", job_id=str(job_id), outcome=payload.outcome, reason=payload.reason)
            result = batch_view(batch, uow.session, now)
            uow.commit()
            return result
    return checked(run)



class CancelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


@router.post("/batches/{batch_id}/cancel")
def cancel_rest(batch_id: UUID, payload: CancelInput,
                principal: Annotated[Principal, Depends(production_edit_principal)], factory: Factory, now: Now):
    """«Отменить оставшиеся»: the batch's jobs not yet handed to the agent never print. Taken/spooled ones stay."""
    def run():
        with factory() as uow:
            batch = uow.session.get(MarkingBatchRow, batch_id)
            batch_access(batch, principal, uow)
            expire_marking(uow.session, now)
            sequences = cancel_pending_marking(uow.session, batch_id, reason=payload.reason, now=now)
            if not sequences:
                raise ConcurrentProductionUpdate("В партии нет заданий, ожидающих печати")
            audit(uow, principal.actor, now, batch_id, "cancelled", sequences=sequences, count=len(sequences),
                  reason=" ".join(payload.reason.split()))
            result = batch_view(batch, uow.session, now)
            uow.commit()
            return result
    return checked(run)


class ReprintInput(BaseModel):
    """Selected labels (job numbers of the batch) or a serial range; never new serial numbers."""
    model_config = ConfigDict(extra="forbid")
    sequences: list[int] = Field(default_factory=list, max_length=500)
    serial_from: int | None = Field(default=None, ge=0, strict=True)
    serial_to: int | None = Field(default=None, ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"\S")

    @model_validator(mode="after")
    def one_selection(self):
        ranged = self.serial_from is not None or self.serial_to is not None
        if bool(self.sequences) == ranged:
            raise ValueError("Укажите этикетки партии либо диапазон номеров")
        if ranged and (self.serial_from is None or self.serial_to is None or self.serial_from > self.serial_to):
            raise ValueError("Диапазон номеров: «с» не больше «по»")
        if any(value < 1 for value in self.sequences) or len(set(self.sequences)) != len(self.sequences):
            raise ValueError("Номера этикеток партии — разные, от 1")
        return self


def reprint_selection(batch, jobs, payload):
    rows = label_rows(batch.manifest)
    if payload.sequences:
        chosen = sorted(payload.sequences)
        if chosen[-1] > len(rows):
            raise ValueError(f"В партии {len(rows)} этикеток — номера {chosen[-1]} нет")
    else:
        chosen = [index + 1 for index, row in enumerate(rows)
                  if row.get("serial") is not None and payload.serial_from <= row["serial"] <= payload.serial_to]
        if not chosen:
            raise ValueError(f"В партии нет этикеток с номерами {payload.serial_from}–{payload.serial_to}")
    picked = set(chosen)
    waiting = sorted(job.sequence for job in jobs if job.sequence in picked and job.status in ("pending", "taken"))
    if waiting:
        raise ConcurrentProductionUpdate(f"Этикетка №{waiting[0]} партии ещё в очереди — дождитесь печати или отмените оставшиеся")
    return chosen, rows


def reprint_root(session, batch):
    """The first batch of a reprint chain (reprint of a reprint … of it)."""
    seen = {batch.id}
    while batch.reprint_of is not None:
        parent = session.get(MarkingBatchRow, batch.reprint_of)
        if parent is None or parent.id in seen:
            break
        seen.add(parent.id)
        batch = parent
    return batch


def reprint_not_in_flight(session, root, original, chosen):
    """Two reprints of the same labels must not both print (a duplicate serial). The whole chain counts — a reprint of
    a reprint prints the root's label too: while any other batch below the root still has that label waiting, taken or
    unresolved, it is refused. Finished labels free it. The caller holds the root's row lock."""
    tree = reprint_tree(session, root)
    picked = {ancestor_sequence(tree, original, sequence, root.id): sequence for sequence in chosen}
    for other in tree.values():
        if other.id == original.id:
            continue  # its own waiting labels are refused by reprint_selection
        open_jobs = session.scalars(select(LabelJobRow.sequence).where(LabelJobRow.batch_id == other.id,
            LabelJobRow.status.in_(("pending", "taken", "unknown"))))
        busy = sorted(picked[source] for source in {ancestor_sequence(tree, other, index, root.id) for index in open_jobs}
                      if source in picked)
        if busy:
            raise ConcurrentProductionUpdate(f"Этикетка №{busy[0]} уже перепечатывается другой партией — дождитесь её печати "
                                             "или отмените оставшиеся в ней")


def reprint_tasks_still_own_serials(batch, principal, uow, now):
    """The reprint reuses the serials already issued to the tasks; a task that released them (cancelled without work)
    or a moved range refuses it."""
    for item in batch.manifest.get("source_snapshot", {}).get("tasks", []):
        task = uow.production.get(UUID(item["task_id"]))
        # A finished task still owns its serials: a lost flag of it is reprinted (like batch_access reads it).
        task_access(task, UUID(item["operation_id"]), principal, uow, historical=True)
        if item.get("serial"):
            current = issue_mark_serials_in(task, False, principal.actor, now, uow)  # numbered=False never issues
            if list(current or ()) != list(item["serial"]):
                raise ConcurrentProductionUpdate("Номера задания изменились — повторная печать прежних номеров недоступна")


@router.post("/batches/{batch_id}/reprint")
def reprint_labels(batch_id: UUID, payload: ReprintInput, principal: PrintUser, factory: Factory, now: Now):
    """«Перепечатать номера…»: a new batch (reprint_of + reason) of selected labels or a serial range of this batch,
    rendered with the profile's current geometry from the stored texts. Serials are reused, none issued."""
    def run():
        fingerprint = digest({"reprint": str(batch_id), **payload.model_dump(mode="json")})
        def replay(uow):
            old = uow.session.scalar(select(MarkingBatchRow).where(MarkingBatchRow.created_by == principal.actor.id,
                MarkingBatchRow.idempotency_key == payload.idempotency_key))
            if old is None:
                return None
            batch_access(old, principal, uow)
            if old.fingerprint != fingerprint:
                raise ConcurrentProductionUpdate("Ключ запроса уже использован")
            return batch_view(old, uow.session, now)

        with factory() as uow:
            if (result := replay(uow)) is not None:
                return result
            original = uow.session.get(MarkingBatchRow, batch_id)
            batch_access(original, principal, uow)
            # The root batch row of the reprint chain serializes every reprint in it (PostgreSQL; SQLite writes one at
            # a time anyway). A retry with the same key that waited for the lock answers the batch the first one made.
            root = reprint_root(uow.session, original)
            uow.session.execute(select(MarkingBatchRow.id).where(MarkingBatchRow.id == root.id).with_for_update())
            if (result := replay(uow)) is not None:
                return result
            expire_marking(uow.session, now)
            jobs = list(uow.session.scalars(select(LabelJobRow).where(LabelJobRow.batch_id == batch_id)))
            chosen, rows = reprint_selection(original, jobs, payload)
            reprint_not_in_flight(uow.session, root, original, chosen)
            reprint_tasks_still_own_serials(original, principal, uow, now)
            trial = not original.manifest.get("source_snapshot", {}).get("tasks")
            current = uow.session.get(MarkingProfileRow, original.profile_id)
            if current is None:
                raise KeyError("Профиль не найден")
            profile, binding = profile_target(uow.session, current.id, current.version, now, calibration=trial)
            geometry = Geometry.model_validate(profile.geometry)
            labels, cache, dimensions = [], {}, {}
            for sequence in chosen:
                row = rows[sequence - 1]
                if row["text"] not in cache:
                    png, dimensions = render(row["text"], geometry, calibration=trial)
                    cache[row["text"]] = {"png_b64": base64.b64encode(png).decode("ascii"),
                        "sha256": hashlib.sha256(png).hexdigest(), "halves": halves(row["text"], len(geometry.zones))}
                kept = {key: row[key] for key in ("key", "task_id", "text", "material", "serial") if key in row}
                labels.append({**kept, **cache[row["text"]], "quantity": 1, "source_sequence": sequence})
            serials = sorted({row["serial"] for row in labels if row.get("serial") is not None})
            manifest = {"profile_id": str(profile.id), "profile_version": profile.version, "binding_version": binding.version,
                "profile_snapshot": {"equipment_id": str(profile.equipment_id), "geometry": profile.geometry},
                "machine_id": binding.machine_id, "printer": binding.printer,
                "source_snapshot": original.manifest.get("source_snapshot", {"tasks": []}), "labels": labels,
                "total": len(labels), "reprint": {"of": str(original.id), "sequences": chosen}, **dimensions}
            manifest["manifest_hash"] = digest(manifest)
            request = SimpleNamespace(idempotency_key=payload.idempotency_key, reason=" ".join(payload.reason.split()),
                                      reprint_of=original.id)
            batch = persist_batch(uow, manifest, binding, request, principal, now, fingerprint)
            audit(uow, principal.actor, now, original.id, "reprinted", batch_id=str(batch.id), sequences=chosen,
                  serials=[serials[0], serials[-1]] if serials else None, reason=request.reason)
            result = batch_view(batch, uow.session, now)
            uow.commit()
            return result
    def race_safe():
        try:
            return run()
        except IntegrityError:
            with factory() as uow:
                existing = uow.session.scalar(select(MarkingBatchRow.id).where(MarkingBatchRow.created_by == principal.actor.id,
                    MarkingBatchRow.idempotency_key == payload.idempotency_key))
            if existing is None:
                raise
            return run()  # answers the winning batch after checking its fingerprint
    return checked(race_safe)


class ClearInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    # Also «Отменить оставшиеся» of every batch on this queue: otherwise they keep printing after the clearing.
    cancel_pending: bool = False


@router.post("/machines/{equipment_id}/printer/clear")
def clear_printer(equipment_id: UUID, payload: ClearInput,
                  principal: Annotated[Principal, Depends(production_edit_principal)], factory: Factory, now: Now):
    """«Очистить принтер»: the agent removes the queue's ERP marking jobs from Windows and empties the Godex buffer
    (~S,BUFCLR). Jobs it removed are reported «Отменено» by the spool watch. The batches' pending jobs keep printing after
    the clearing unless `cancel_pending` cancels them too (journaled per batch like «Отменить оставшиеся»)."""
    def run():
        with factory() as uow:
            binding = uow.session.get(MarkingBindingRow, equipment_id)
            if binding is None:
                raise KeyError("Принтер станка не привязан")
            row = request_clear(uow.session, equipment_id=equipment_id, machine_id=binding.machine_id, printer=binding.printer,
                                reason=payload.reason, actor=principal.actor, now=now)
            cancelled = 0
            if payload.cancel_pending:
                expire_marking(uow.session, now)
                for batch_id in pending_marking_by_printer(uow.session, binding.machine_id, binding.printer):
                    sequences = cancel_pending_marking(uow.session, batch_id, reason=f"очистка принтера: {row.reason}", now=now)
                    if sequences:
                        cancelled += len(sequences)
                        audit(uow, principal.actor, now, batch_id, "cancelled", sequences=sequences, count=len(sequences),
                              reason=f"Очистка принтера: {row.reason}")
            audit(uow, principal.actor, now, equipment_id, "printer_clear_requested", command_id=str(row.id),
                  machine_id=binding.machine_id, printer=binding.printer, reason=row.reason, cancelled_jobs=cancelled)
            result = {**command_view(row), "cancelled_jobs": cancelled}
            uow.commit()
            return result
    return checked(run)


@router.get("/machines/{equipment_id}/printer/command")
def printer_command(equipment_id: UUID, _principal: ReadUser, factory: Factory, now: Now):
    def run():
        with factory() as uow:
            binding = uow.session.get(MarkingBindingRow, equipment_id)
            if binding is None:
                raise KeyError("Принтер станка не привязан")
            expire_commands(uow.session, now)
            result = command_view(latest_command(uow.session, binding.machine_id, binding.printer))
            uow.commit()
            return result
    return checked(run)
