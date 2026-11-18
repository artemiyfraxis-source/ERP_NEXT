"""Compose equipment identity, pinned production marks and the targeted print queue."""

import base64
from datetime import datetime
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
from app.modules.references.equipment import INV_KEY
from app.modules.references.infrastructure import EquipmentMachineRow
from app.modules.techcards.etk_read import issued_card_for_item, pinned_card
from app.modules.techcards.infrastructure import ProjectTechcardRow, SqlAlchemyTechcardRepository
from app.modules.warehouse.devices import PrinterRow, printer_online
from app.modules.warehouse.label_jobs import LabelJobRow, add_marking_job, expire_marking
from app.modules.warehouse.marking_profiles import MarkingBatchRow, MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.marking_render import Geometry, halves, render
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
                 "failed": "Ошибка печати", "unknown": "Результат неизвестен"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def checked(action):
    try:
        return action()
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, error.args[0]) from error
    except ConcurrentProductionUpdate as error:
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


def binding_view(row):
    return {"version": row.version, "machine_id": row.machine_id, "printer": row.printer} if row else None


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
        profiles = list(uow.session.scalars(select(MarkingProfileRow).order_by(MarkingProfileRow.inventory_number, MarkingProfileRow.name)))
        grouped = {}
        for profile in profiles:
            machine = catalog.get(profile.equipment_id)
            if machine is None:
                continue
            item = grouped.setdefault(str(machine.id), {"equipment_id": str(machine.id),
                "inventory_number": machine.fields.get(INV_KEY, profile.inventory_number), "name": machine.display_text,
                "binding": binding_view(uow.session.get(MarkingBindingRow, machine.id)), "profiles": []})
            item["profiles"].append(profile_view(profile))
        printers = []
        for printer, pc in uow.session.execute(select(PrinterRow, ScanAgentRow).join(ScanAgentRow)):
            seen = printer.last_seen.replace(tzinfo=now.tzinfo) if printer.last_seen.tzinfo is None else printer.last_seen
            # Cloned PCs share a hostname: the IP and machine id tell them apart in the printer list.
            printers.append({"machine_id": pc.machine_id, "pc": pc.pc_name, "ip": pc.ip_lan or pc.ip_seen, "name": printer.name,
                "driver": printer.driver, "port": printer.port, "kind": printer.kind, "present": printer.present,
                "online": printer.present and seen >= now - LIVE_FOR})
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


@router.put("/profiles/{profile_id}")
def save_profile(profile_id: UUID, payload: ProfileInput, principal: ConfigUser, factory: Factory, now: Now):
    def save():
        with factory() as uow:
            profile = uow.session.get(MarkingProfileRow, profile_id)
            if profile is None:
                raise KeyError("Профиль не найден")
            geometry = payload.geometry.model_dump()
            calibrated = payload.calibrated and geometry == profile.geometry
            name = profile_name(uow.session, profile, payload.name)
            renamed = {"name": name, "previous_name": profile.name} if name != profile.name else {}
            changed = uow.session.execute(update(MarkingProfileRow).where(MarkingProfileRow.id == profile_id,
                MarkingProfileRow.version == payload.version).values(name=name, geometry=geometry, calibrated=calibrated,
                                                                    version=payload.version + 1))
            if changed.rowcount != 1:
                raise ConcurrentProductionUpdate(STALE)
            audit(uow, principal.actor, now, profile_id, "profile_saved", version=payload.version + 1, calibrated=calibrated, **renamed)
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


def issued_document(task, uow):
    techcards = SimpleNamespace(techcards=SqlAlchemyTechcardRepository(uow.session))
    card = issued_card_for_item(techcards, task.project, task.item)
    if card is None:
        raise KeyError("Нет выданной ЭТК по этой позиции")
    uow.session.scalar(select(ProjectTechcardRow).where(ProjectTechcardRow.id == card.id).with_for_update()
                       .execution_options(populate_existing=True))
    card = issued_card_for_item(techcards, task.project, task.item)
    if card is None:
        raise ConcurrentProductionUpdate(STALE)
    shown, revision = pinned_card(techcards, card, task.techcard_revision)
    if task.techcard_revision is not None and revision["fallback"]:
        raise ConcurrentProductionUpdate("Закреплённая ревизия ЭТК не найдена")
    return shown.document, {"card_id": str(card.id), "card_version": card.version, "revision": shown.revision}


def source_rows(source, selected, principal, uow, now):
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
        expanded = labels(document, task, serial, selected)
        if any(row["serial"] is not None for row in expanded):
            issue_mark_serials_in(task, True, principal.actor, now, uow)
        rows.extend(expanded)
        snapshots.append({"task_id": str(task.id), "operation_id": str(operation_id), "version": task.version,
            "quantity": task.quantity, "serial": list(serial) if serial else None,
            "document_hash": digest(document), **revision})
    if selected and set(selected) != {row["key"] for row in rows}:
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
    rows, source = source_rows(payload.source, payload.selected, principal, uow, now)
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


def batch_view(batch, session):
    jobs = list(session.scalars(select(LabelJobRow).where(LabelJobRow.batch_id == batch.id).order_by(LabelJobRow.sequence)))
    statuses = {job.status for job in jobs}
    status = next((value for value in ("unknown", "failed", "taken", "pending") if value in statuses), "spooled")
    return {"id": str(batch.id), "status": status, "status_label": STATUS_LABELS[status], "total": batch.total,
        "reprint_of": str(batch.reprint_of) if batch.reprint_of else None,
        "jobs": [{"id": str(job.id), "sequence": job.sequence, "status": job.status,
            "status_label": STATUS_LABELS[job.status], "message": job_message(job),
            "spool_job_id": (job.receipt or {}).get("spool_job_id")} for job in jobs]}


def job_message(job):
    receipt = job.receipt or {}
    if job.status == "unknown" and receipt.get("outcome") in ("spooled", "failed"):
        # The agent answered after its lease ran out: the operator decides, the agent's word is a hint.
        late = STATUS_LABELS[receipt["outcome"]] + (f": {receipt['message']}" if receipt.get("message") else "")
        return f"{job.error or ''} Агент сообщил позже — {late}".strip()
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
                return batch_view(old, uow.session)
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
            result = batch_view(batch, uow.session)
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
                return batch_view(existing, uow.session)
    return checked(race_safe)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: UUID, principal: ReadUser, factory: Factory, now: Now):
    def run():
        with factory() as uow:
            batch = uow.session.get(MarkingBatchRow, batch_id)
            batch_access(batch, principal, uow)
            expire_marking(uow.session, now)
            result = batch_view(batch, uow.session)
            uow.commit()
            return result
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
            changed = uow.session.execute(update(LabelJobRow).where(LabelJobRow.id == job_id,
                LabelJobRow.batch_id == batch_id, LabelJobRow.status == "unknown").values(status=payload.outcome,
                    finished_at=now, error=payload.reason if payload.outcome == "failed" else None))
            if changed.rowcount != 1:
                raise ConcurrentProductionUpdate("Задание не ожидает проверки результата")
            audit(uow, principal.actor, now, batch_id, "reconciled", job_id=str(job_id), outcome=payload.outcome, reason=payload.reason)
            result = batch_view(batch, uow.session)
            uow.commit()
            return result
    return checked(run)
