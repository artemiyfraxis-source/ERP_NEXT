"""«Ручная работа»: the material of a finished job (plan «Станки вне проекта», slice S3, 20.11.2026).

A job never writes stock off by itself. A finished job whose material «Ждёт решения склада» is decided here, once:
- `GET  /api/production/manual-jobs/{id}/material?q=` → the job, what it used (pieces = good + defect, the length), the
  lots that may be its wire / contact (shared/article_match over the lots with something available, the machine's
  warehouse first), the lots a search `q` finds, the rows already written off, and what this person may do.
- `POST /api/production/manual-jobs/{id}/material/issue` `{version, lines: [{lot_id, lot_version, quantity}]}` —
  «Списать с партии»: «Склад» on edit only (`warehouse_edit_principal`, the gate of every write-off).
- `POST /api/production/manual-jobs/{id}/material/not-issued` `{version, note}` — «Не списывать» with a reason, by
  «Производство», «Задания» or «Склад» on edit.

The issue is one transaction: first the job's compare-and-swap (done, pending, `version` → issued; it locks the job row,
so a parallel decision waits and then gets 409), then per line — lots in id order, one lock order for everyone — the
warehouse's own `write_off` (lot version, balance and reserve rules, the СПИСАНИЕ row with `source_key`
«manual-job:{job}:{lot}» that the journal holds UNIQUE, the warehouse audit event) and a `production_manual_job_issues`
row bound to that write-off. Any refusal rolls all of it back. The same lines sent again after success answer the job
as it is and write nothing; other lines get 409."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.production import manual_jobs as jobs
from app.modules.production.api import production_uow_factory
from app.modules.production.machine_writes import moment
from app.modules.references.equipment import field
from app.modules.references.infrastructure import EquipmentMachineRow
from app.modules.warehouse.application import ConcurrentWarehouseUpdate, WriteOffCommand, write_off
from app.modules.warehouse.domain import WAREHOUSES, StockLot, WarehouseValidationError
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository, SqlAlchemyWriteOffJournal, WriteOffEntryRow
from app.shared import article_match
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, warehouse_edit_principal
from app.shared.persistence import SqlAlchemyAuditRepository

from .manual_jobs_api import (JournalReader, _inventory, _journal, _shown, can_decide, can_issue,
                              manual_jobs_material_principal, view)

router = APIRouter(prefix="/api/production/manual-jobs", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Decider = Annotated[Principal, Depends(manual_jobs_material_principal)]
Issuer = Annotated[Principal, Depends(warehouse_edit_principal)]
LOT_LIMIT = 30
MAX_LINES = 20
SOURCE_PREFIX = "manual-job"
STALE = "Данные изменились: обновите окно"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueLine(Payload):
    lot_id: UUID
    lot_version: int = Field(ge=1)
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class IssuePayload(Payload):
    version: int = Field(ge=1)
    lines: list[IssueLine] = Field(min_length=1, max_length=MAX_LINES)


class NotIssuedPayload(Payload):
    version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=300)


class _StockUnitOfWork:
    """The warehouse use case inside this workflow's transaction: its `commit` only flushes, the workflow alone commits
    or rolls back (the pattern of workflows/instrument_from_stock.py)."""

    def __init__(self, session: Session):
        self.session = session
        self.warehouse = SqlAlchemyWarehouseRepository(session)
        self.writeoffs = SqlAlchemyWriteOffJournal(session)
        self.audit = SqlAlchemyAuditRepository(session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def commit(self):
        self.session.flush()


def source_key(job_id: UUID, lot_id: UUID) -> str:
    return f"{SOURCE_PREFIX}:{job_id}:{lot_id}"


def _num(value: Decimal | None) -> str:
    return "" if value is None else format(Decimal(value).normalize(), "f")


def _shown_qty(value: Decimal, unit: str) -> str:
    return f"{_num(value).replace('.', ',')} {unit}".strip()


def _q6(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.000001"))


def _site(machine: EquipmentMachineRow | None) -> str:
    """ЦАР / ЖУК from the machine's «Расположение» in БД.ОБ; "" — not known."""
    if machine is None:
        return ""
    fields = {" ".join(str(key).split()).casefold(): value for key, value in (machine.fields or {}).items()}
    site = field(fields, "расположение").upper()
    return site if site in WAREHOUSES else ""


def _label(lot: StockLot) -> str:
    return f"{lot.warehouse} {lot.grn}" + (f" (пакет {lot.package})" if lot.package else "")


def _lot(lot: StockLot, site: str, match: str = "") -> dict:
    return {"id": str(lot.id), "version": lot.version, "warehouse": lot.warehouse, "grn": lot.grn,
            "package": lot.package, "article": lot.article, "name": lot.name, "unit": lot.unit,
            "storage_location": lot.storage_location, "balance": _num(lot.balance), "reserved": _num(lot.reserved),
            "available": _num(lot.available), "here": bool(site) and lot.warehouse == site, "match": match}


def _order(site: str):
    return lambda lot: (bool(site) and lot.warehouse != site,)


def offered(lots: list[StockLot], text: str, site: str) -> list[dict]:
    """The lots that may be the job's wire / contact (by the name the job carries), with something available; the
    machine's warehouse first, then the best match."""
    if not text:
        return []
    found = [(match, lot) for match, lot in article_match.candidates(text, text, lots) if lot.available > 0]
    found.sort(key=lambda pair: (bool(site) and pair[1].warehouse != site, article_match.rank(pair[0])))
    return [_lot(lot, site, match.kind) for match, lot in found[:LOT_LIMIT]]


def _issues(session: Session, job_id: UUID) -> list[dict]:
    result = []
    for issue in jobs.issues_of(session, job_id):
        entry = session.get(WriteOffEntryRow, issue.writeoff_id)
        result.append({
            "lot_id": str(issue.lot_id), "writeoff_id": str(issue.writeoff_id), "quantity": _num(issue.quantity),
            "warehouse": entry.warehouse if entry else "", "grn": entry.grn if entry else "",
            "package": entry.package if entry else "", "name": entry.name if entry else "",
            "unit": entry.unit if entry else "", "by": issue.created_by_name, "at": moment(issue.created_at)})
    return result


def material_view(session: Session, row: jobs.ManualJobRow, principal: Principal, query: str = "") -> dict:
    machine = session.get(EquipmentMachineRow, row.equipment_id)
    site = _site(machine)
    open_ = row.status == "done" and row.material == "pending"
    stock = SqlAlchemyWarehouseRepository(session)
    lots = stock.filtered() if open_ and (row.wire or row.contact) else []
    found: list[dict] = []
    if open_ and query.strip():
        hits = sorted((lot for lot in stock.filtered(query=query) if lot.available > 0), key=_order(site))
        found = [_lot(lot, site) for lot in hits[:LOT_LIMIT]]
    pieces = (row.good_quantity or 0) + (row.defect_quantity or 0) if row.status == "done" else None
    return {
        "job": view(row, machine), "site": site, "pieces": pieces, "length_mm": _shown(row.length_mm),
        "wire": {"label": row.wire, "lots": offered(lots, row.wire, site)},
        "contact": {"label": row.contact, "lots": offered(lots, row.contact, site)},
        "found": found, "issues": _issues(session, row.id),
        "can_issue": can_issue(principal), "can_decide": can_decide(principal),
    }


def _job(session: Session, job_id: UUID) -> jobs.ManualJobRow:
    row = session.get(jobs.ManualJobRow, job_id)
    if row is None:
        raise KeyError("Ручная работа не найдена")
    return row


def issue_lots(sessions, job_id: UUID, payload: IssuePayload, principal: Principal, now: datetime) -> dict:
    lines = sorted(payload.lines, key=lambda line: line.lot_id)
    if len({line.lot_id for line in lines}) != len(lines):
        raise jobs.ManualJobInvalid("Одна партия указана дважды — сложите количество в одну строку")
    asked = {(line.lot_id, _q6(line.quantity)) for line in lines}
    with sessions.begin() as session:
        uow = _StockUnitOfWork(session)
        row = _job(session, job_id)
        if row.material == "issued":
            # The same request again (a lost answer, a double click): nothing more is written off.
            if {(issue.lot_id, _q6(issue.quantity)) for issue in jobs.issues_of(session, row.id)} == asked:
                return material_view(session, row, principal)
            raise jobs.ManualJobConflict(f"Материал по {jobs.number_text(row.number)} уже списан с партии — обновите окно")
        row = jobs.decide_material(session, job_id, payload.version, "issued")
        number = jobs.number_text(row.number)
        machine = session.get(EquipmentMachineRow, row.equipment_id)
        inventory = _inventory(machine) if machine else ""
        reason = (f"Ручная работа {number}" + (f", {inventory}" if inventory else "") + f": {row.purpose}")[:500]
        written: dict[UUID, str] = {}
        for line in lines:
            lot = uow.warehouse.get(line.lot_id)
            if lot is None:
                raise KeyError("Складская партия не найдена — обновите окно")
            if lot.version != line.lot_version:
                raise ConcurrentWarehouseUpdate(f"Партия {_label(lot)} изменилась — обновите окно")
            if line.quantity > lot.available:
                raise jobs.ManualJobInvalid(f"Партия {_label(lot)}: доступно {_shown_qty(lot.available, lot.unit)}, "
                                            f"а списываете {_shown_qty(line.quantity, lot.unit)}")
            command = WriteOffCommand(lot_id=lot.id, expected_version=line.lot_version, quantity=line.quantity,
                                      purpose=f"Ручная работа {number}", reason=reason, actor=principal.actor, now=now,
                                      source_key=source_key(row.id, lot.id))
            write_off(command, uow)
            session.add(jobs.ManualJobIssueRow(id=uuid4(), job_id=row.id, lot_id=lot.id, quantity=line.quantity,
                                               writeoff_id=command.writeoff_id,
                                               created_by_name=jobs.clean(principal.actor.name, 200), created_at=now))
            written[lot.id] = f"{_label(lot)} — {_shown_qty(line.quantity, lot.unit)}"
        session.flush()
        _journal(uow, row, "material_issued", principal, now, f"Списано с партии по {number}: "
                 + "; ".join(written[line.lot_id] for line in payload.lines))  # in the order the person listed them
        return material_view(session, row, principal)


def _refused(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(404, error.args[0] if error.args else "Не найдено")
    if isinstance(error, (jobs.ManualJobConflict, ConcurrentWarehouseUpdate)):
        return HTTPException(409, str(error))
    if isinstance(error, (IntegrityError, StaleDataError)):
        return HTTPException(409, STALE)
    return HTTPException(422, str(error))


# --- routes ---

@router.get("/{job_id}/material")
def material(job_id: UUID, principal: JournalReader, factory: Factory,
             q: Annotated[str, Query(max_length=255)] = "") -> dict:
    with factory() as uow:
        row = uow.session.get(jobs.ManualJobRow, job_id)
        if row is None:
            raise HTTPException(404, "Ручная работа не найдена")
        return material_view(uow.session, row, principal, q)


@router.post("/{job_id}/material/issue")
def issue(job_id: UUID, payload: IssuePayload, principal: Issuer, factory: Factory, now: Now) -> dict:
    sessions = getattr(factory(), "session_factory", None)
    if sessions is None:
        raise HTTPException(503, "База ERP не настроена")
    try:
        return issue_lots(sessions, job_id, payload, principal, now)
    except (KeyError, jobs.ManualJobInvalid, jobs.ManualJobConflict, WarehouseValidationError,
            ConcurrentWarehouseUpdate, IntegrityError, StaleDataError) as error:
        raise _refused(error) from error


@router.post("/{job_id}/material/not-issued")
def not_issued(job_id: UUID, payload: NotIssuedPayload, principal: Decider, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        try:
            row = _job(session, job_id)
            if row.material == "not_issued" and row.material_note == jobs.clean(payload.note, 300):
                return material_view(session, row, principal)  # the same answer again: nothing changes
            row = jobs.decide_material(session, job_id, payload.version, "not_issued", payload.note)
        except (KeyError, jobs.ManualJobInvalid, jobs.ManualJobConflict) as error:
            raise _refused(error) from error
        _journal(uow, row, "material_not_issued", principal, now,
                 f"Материал по {jobs.number_text(row.number)} не списывается: {row.material_note}")
        answer = material_view(session, row, principal)
        uow.commit()
    return answer
