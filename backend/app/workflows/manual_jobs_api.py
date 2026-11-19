"""HTTP «Ручная работа» (plan «Станки вне проекта», slice S1; 19.11.2026). Rules and tables: production/manual_jobs.py.

Read by «Производство» or «Задания» on view, written by them on edit — the same named gates as «Станки»
(`machines_view_principal`, `machines_edit_principal`, tests/identity/test_level_gates.py).
- `GET  /api/production/manual-jobs?date_from=&date_to=&equipment_id=` → `{date_from, date_to, rows, can_edit}`: the
  jobs that overlap the period (a running job started earlier is in it), newest first.
- `GET  /api/production/manual-jobs/options` → machines of БД.ОБ (with the running job, if any), wires and contacts of
  the references, the эталоны of each machine, may this person start one — what the window offers; own values may
  be typed instead.
- `POST /api/production/manual-jobs` `{equipment_id, purpose, wire, contact, length_mm, quantity_planned, reference_id}`
  → the new running job (409 while another runs on that machine).
- `POST /api/production/manual-jobs/{id}/finish` `{version, good, defect, defect_reason, material, material_note}`
  → done; `material` is `not_issued` (with a reason) or `pending` («Ждёт решения склада») until the warehouse slice.
- `POST /api/production/manual-jobs/{id}/cancel` `{version, reason}` → cancelled.
Each change carries the `version` the person saw (409 when it moved) and is journaled on the job."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.equipment_setup.recipes import wire_key
from app.modules.equipment_setup.setup_model_infrastructure import SetupReferenceRow
from app.modules.production import machine_runs as runs
from app.modules.production import manual_jobs as jobs
from app.modules.production.api import production_uow_factory
from app.modules.production.machine_writes import moment
from app.modules.references.domain import TERMINAL_TABS
from app.modules.references.equipment import INV_KEY, NAME_KEYS, field
from app.modules.references.infrastructure import EquipmentMachineRow, TechOperationRecordRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal

from .machine_writes_api import _can_edit, machines_edit_principal
from .machines_api import machines_view_principal
from .ob019_setup import _wires

router = APIRouter(prefix="/api/production/manual-jobs", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Reader = Annotated[Principal, Depends(machines_view_principal)]
Writer = Annotated[Principal, Depends(machines_edit_principal)]
MAX_DAYS = 366


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartPayload(Payload):
    equipment_id: UUID
    purpose: str = Field(min_length=1, max_length=500)
    wire: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=200)
    length_mm: str = Field(default="", max_length=20)
    quantity_planned: int | None = Field(default=None, ge=1, le=jobs.LIMIT)
    reference_id: UUID | None = None


class FinishPayload(Payload):
    version: int = Field(ge=1)
    good: int = Field(ge=0, le=jobs.LIMIT)
    defect: int = Field(default=0, ge=0, le=jobs.LIMIT)
    defect_reason: str = Field(default="", max_length=300)
    material: Literal["pending", "not_issued", "issued"]
    material_note: str = Field(default="", max_length=300)


class CancelPayload(Payload):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=300)


# --- helpers ---

def _inventory(row: EquipmentMachineRow) -> str:
    return "".join(field(row.fields or {}, INV_KEY, "inventoryNumber").split()).upper()


def _machine_name(row: EquipmentMachineRow | None) -> str:
    return "" if row is None else (field(row.fields or {}, *NAME_KEYS) or row.display_text)


def _length(text: str) -> Decimal | None:
    value = " ".join(str(text or "").split()).replace(",", ".").replace(" ", "")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise HTTPException(422, "Длина: нужно число, мм") from None
    if not number.is_finite():
        raise HTTPException(422, "Длина: нужно число, мм")
    return number


def _shown(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f") if value != value.to_integral_value() else format(value.to_integral_value(), "f")
    return text.replace(".", ",")


def view(row: jobs.ManualJobRow, machine: EquipmentMachineRow | None) -> dict:
    status, tone = jobs.STATUS_TEXTS[row.status]
    material, material_tone = jobs.MATERIAL_TEXTS[row.material]
    return {
        "id": str(row.id), "number": jobs.number_text(row.number), "plc_number": jobs.plc_number(row.number),
        "equipment_id": str(row.equipment_id), "inventory": _inventory(machine) if machine else "",
        "machine": _machine_name(machine), "status": row.status, "status_text": status, "tone": tone,
        "purpose": row.purpose, "wire": row.wire, "contact": row.contact, "length_mm": _shown(row.length_mm),
        "quantity_planned": row.quantity_planned, "reference_id": str(row.reference_id) if row.reference_id else None,
        "count_source": row.count_source, "count_text": jobs.COUNT_TEXTS[row.count_source],
        "good": row.good_quantity, "defect": row.defect_quantity, "defect_reason": row.defect_reason,
        "machine_count": row.machine_count,
        "material": row.material, "material_text": material if row.status == "done" else "",
        "material_tone": material_tone, "material_note": row.material_note, "cancel_reason": row.cancel_reason,
        "operator": row.operator_name, "finished_by": row.finished_by_name,
        "started_at": moment(row.started_at), "finished_at": moment(row.finished_at), "version": row.version,
    }


def _machines(session, ids) -> dict[UUID, EquipmentMachineRow]:
    ids = list(set(ids))
    if not ids:
        return {}
    return {row.id: row for row in session.scalars(select(EquipmentMachineRow).where(EquipmentMachineRow.id.in_(ids)))}


def _journal(uow, row: jobs.ManualJobRow, event: str, principal: Principal, now: datetime, text: str) -> None:
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=f"production.manual_job_{event}",
                             actor=principal.actor, occurred_at=now,
                             details={"text": text[:500], "number": jobs.number_text(row.number),
                                      "equipment_id": str(row.equipment_id)}))


def _close_run(session, row, now: datetime, principal, why: str) -> None:
    """The job's open run on the machine closes with it; never under a cutting machine (409, nothing saved)."""
    try:
        runs.close_for_manual(session, row.equipment_id, row.id, now, principal.actor.name, why)
    except runs.RunRefused as error:
        raise HTTPException(409, str(error)) from error


def _lock_run(session, job_id: UUID) -> None:
    """Lock the machine's run row before the job row: the card close and the write check lock run → job, so finish and
    cancel take the same order (no deadlock on PostgreSQL)."""
    job = session.get(jobs.ManualJobRow, job_id)
    if job is not None:
        runs.current(session, job.equipment_id, for_update=True)


def _errors(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(404, error.args[0] if error.args else "Не найдено")
    if isinstance(error, jobs.ManualJobConflict):
        return HTTPException(409, str(error))
    return HTTPException(422, str(error))


def _period(date_from: date | None, date_to: date | None, now: datetime) -> tuple[date, date]:
    today = now.date()
    start, end = date_from or date_to or today, date_to or date_from or today
    if end < start:
        raise HTTPException(422, "Конец периода раньше начала")
    if (end - start).days >= MAX_DAYS:
        raise HTTPException(422, "Период — не больше года")
    return start, end


# --- routes ---

@router.get("")
def journal(principal: Reader, factory: Factory, now: Now, date_from: date | None = None, date_to: date | None = None,
            equipment_id: UUID | None = None) -> dict:
    start, end = _period(date_from, date_to, now)
    zone = now.tzinfo
    with factory() as uow:
        rows = jobs.in_period(uow.session, datetime.combine(start, time(), zone),
                              datetime.combine(end + timedelta(days=1), time(), zone), equipment_id)
        machines = _machines(uow.session, [row.equipment_id for row in rows])
        result = [view(row, machines.get(row.equipment_id)) for row in rows]
    return {"date_from": start.isoformat(), "date_to": end.isoformat(), "rows": result, "can_edit": _can_edit(principal)}


@router.get("/options")
def options(principal: Reader, factory: Factory) -> dict:
    with factory() as uow:
        session = uow.session
        running = {row.equipment_id: row for row in session.scalars(
            select(jobs.ManualJobRow).where(jobs.ManualJobRow.status == "running"))}
        machines = []
        for row in session.scalars(select(EquipmentMachineRow)):
            inventory = _inventory(row)
            if not inventory:
                continue
            job = running.get(row.id)
            machines.append({"id": str(row.id), "inventory": inventory, "name": _machine_name(row),
                             "running": jobs.number_text(job.number) if job else None})
        machines.sort(key=lambda item: (item["inventory"], item["name"]))
        wires = sorted(_wires(session), key=str.casefold)
        contacts: dict[str, dict] = {}
        for row in session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab.in_(
                [tab.value for tab in TERMINAL_TABS]), TechOperationRecordRow.material_archived.is_(False))):
            label = " ".join(field(row.fields or {}, "terArticle").split())
            if label and wire_key(label) not in contacts:
                contacts[wire_key(label)] = {"label": label, "maker": field(row.fields or {}, "terManufacturer")}
        latest: dict[tuple, SetupReferenceRow] = {}
        for row in session.scalars(select(SetupReferenceRow)):
            key = (row.unit_id, row.wire_key, row.contact_key)
            if key not in latest or row.version > latest[key].version:
                latest[key] = row
        references = [{"id": str(row.id), "equipment_id": str(row.unit_id), "wire": row.wire, "contact": row.contact,
                       "version": row.version,
                       "label": " · ".join(part for part in (row.wire, row.contact) if part) + f" · версия {row.version}"}
                      for row in sorted(latest.values(), key=lambda item: (item.wire_key, item.contact_key))]
    return {"machines": machines, "wires": wires,
            "contacts": sorted(contacts.values(), key=lambda item: item["label"].casefold()),
            "references": references, "can_edit": _can_edit(principal)}


@router.post("")
def start(payload: StartPayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        machine = session.get(EquipmentMachineRow, payload.equipment_id)
        if machine is None:
            raise HTTPException(404, "Станок не найден в БД.ОБ")
        wire, contact = jobs.clean(payload.wire, 200), jobs.clean(payload.contact, 200)
        if payload.reference_id is not None:
            reference = session.get(SetupReferenceRow, payload.reference_id)
            if reference is None or reference.unit_id != payload.equipment_id:
                raise HTTPException(422, "Эталон не этого станка")
            wire, contact = wire or reference.wire, contact or reference.contact
        new = jobs.NewJob(payload.equipment_id, payload.purpose, wire, wire_key(wire), contact, wire_key(contact),
                          _length(payload.length_mm), payload.quantity_planned, payload.reference_id)
        try:
            row = jobs.start(session, new, actor_id=principal.actor.id, actor_name=principal.actor.name, now=now)
        except (jobs.ManualJobInvalid, jobs.ManualJobConflict) as error:
            raise _errors(error) from error
        material = " · ".join(part for part in (row.wire, row.contact, f"{_shown(row.length_mm)} мм" if row.length_mm
                                                else "") if part)
        _journal(uow, row, "started", principal, now,
                 f"Начата ручная работа {jobs.number_text(row.number)} на {_inventory(machine)}: {row.purpose}"
                 + (f" ({material})" if material else ""))
        result = view(row, machine)
        uow.commit()
    return result


@router.post("/{job_id}/finish")
def finish(job_id: UUID, payload: FinishPayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        result = jobs.Finish(payload.good, payload.defect, payload.defect_reason, payload.material,
                             payload.material_note)
        _lock_run(session, job_id)
        try:
            row = jobs.finish(session, job_id, payload.version, result, actor_name=principal.actor.name, now=now)
        except (KeyError, jobs.ManualJobInvalid, jobs.ManualJobConflict) as error:
            raise _errors(error) from error
        _close_run(session, row, now, principal, "Ручная работа завершена")
        material = jobs.MATERIAL_TEXTS[row.material][0] + (f": {row.material_note}" if row.material_note else "")
        defect = f", брак {row.defect_quantity} ({row.defect_reason})" if row.defect_quantity else ""
        _journal(uow, row, "finished", principal, now,
                 f"Завершена {jobs.number_text(row.number)}: годных {row.good_quantity}{defect}; материал — {material}")
        answer = view(row, session.get(EquipmentMachineRow, row.equipment_id))
        uow.commit()
    return answer


@router.post("/{job_id}/cancel")
def cancel(job_id: UUID, payload: CancelPayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        _lock_run(session, job_id)
        try:
            row = jobs.cancel(session, job_id, payload.version, payload.reason, actor_name=principal.actor.name, now=now)
        except (KeyError, jobs.ManualJobInvalid, jobs.ManualJobConflict) as error:
            raise _errors(error) from error
        _close_run(session, row, now, principal, "Ручная работа отменена")
        _journal(uow, row, "cancelled", principal, now, f"Отменена {jobs.number_text(row.number)}: {row.cancel_reason}")
        answer = view(row, session.get(EquipmentMachineRow, row.equipment_id))
        uow.commit()
    return answer
