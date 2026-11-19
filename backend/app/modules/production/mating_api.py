"""Authenticated append-only instrument mating ledger and idempotent batches from the VNA program."""

from datetime import datetime
import hashlib
import json
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from .api import production_uow_factory
from .application import ConcurrentProductionUpdate
from .domain import ProductionValidationError
from .mating_infrastructure import (InstrumentMatingRow, MatingBatchRow, MatingRepository, control_result, event_result,
                                    instrument_control)
from .matings import MAX_COUNT

router = APIRouter(prefix="/api/production", tags=["production"])
# The access gate comes before the unit of work in every signature: a refused caller gets 401/403, never 503.
Factory = Annotated[Callable, Depends(production_uow_factory)]
Editor = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]
CONFLICT = "Сочленения изменены другим пользователем — обновите карточку"
BATCH_MISMATCH = "Этот замер уже записан с другим составом сочленений"


class MatingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end: Literal["A", "B"]
    delta: int = Field(strict=True, ge=-MAX_COUNT, le=MAX_COUNT)
    kind: Literal["manual", "initial", "correction"]
    note: str = Field(default="", max_length=2000)
    # Last seen chain position of this side; corrections and initial values are checked against it.
    version: int | None = Field(default=None, ge=0, strict=True)


class BatchEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: UUID | None = None
    inventory_number: str | None = Field(default=None, min_length=1, max_length=100)
    end: Literal["A", "B"]
    delta: int = Field(strict=True, gt=0, le=1000)
    kind: Literal["measurement", "calibration"]
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def one_identity(self):
        if (self.instrument_id is None) == (self.inventory_number is None):
            raise ValueError("Укажите id или инвентарный номер СИ")
        return self


class BatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=200)
    entries: list[BatchEntry] = Field(min_length=1, max_length=200)


def _with_control(session, instrument_id: UUID, snapshot: dict, now: datetime) -> dict:
    """The card's «Следующий контроль»: 12 months or 100 matings since the last measurement."""
    return {**snapshot, "control": control_result(instrument_control(session, instrument_id, now.date()))}


@router.get("/instruments/{instrument_id}/matings")
def get_matings(instrument_id: UUID, _principal: Annotated[Principal, Depends(production_overview_principal)],
                factory: Factory, now: Now):
    with factory() as uow:
        if uow.instruments.get(instrument_id) is None:
            raise HTTPException(404, "СИ не найдено")
        return _with_control(uow.session, instrument_id, MatingRepository(uow.session).snapshot(instrument_id), now)


def _audit(uow, event, principal, now):
    _audit_actor(uow, event, principal.actor, now)


def _audit_actor(uow, event, actor, now):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=event.instrument_id, event_type="production.instrument_mating_added",
                             actor=actor, occurred_at=now,
                             details={"event_id": str(event.id), "end": event.side, "seq": event.seq, "delta": event.delta,
                                      "total_after": event.total_after, "kind": event.kind, "note": event.note,
                                      "source": event.source}))


@router.post("/instruments/{instrument_id}/matings")
def post_mating(instrument_id: UUID, payload: MatingPayload, principal: Editor, factory: Factory, now: Now):
    try:
        with factory() as uow:
            uow.instruments.lock_tree()
            if uow.instruments.get(instrument_id) is None:
                raise HTTPException(404, "СИ не найдено")
            repository = MatingRepository(uow.session)
            event = repository.append(instrument_id, **payload.model_dump(), source=None, actor=principal.actor.name, now=now)
            _audit(uow, event, principal, now)
            result = _with_control(uow.session, instrument_id, repository.snapshot(instrument_id), now)
            uow.commit()
            return result
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except (ConcurrentProductionUpdate, IntegrityError) as error:
        raise HTTPException(409, CONFLICT) from error


class BatchMismatch(ProductionValidationError):
    """The same run was already recorded with other matings (permanent, never a retry)."""


def record_batch(uow, source: str, entries: list[dict], actor, now: datetime) -> dict:
    """One measurement or calibration run = one source; a repeated run with the same entries changes nothing.

    Runs inside the caller's entered unit of work (the HTTP batch below, or the VNA bench session recording a
    measurement together with its file); the caller commits. `entries` carry instrument_id or inventory_number,
    end, delta, kind and note."""
    source = source.strip()
    if not source:
        raise ProductionValidationError("Укажите источник пакета сочленений")
    # The fingerprint is the payload as sent (an inventory number stays a number), so a retry after the number
    # was reassigned is still recognised as the same run instead of being resolved to another instrument.
    sent = [{"instrument_id": str(entry["instrument_id"]) if entry.get("instrument_id") is not None else None,
             "inventory_number": entry["inventory_number"].strip() if entry.get("inventory_number") is not None else None,
             "end": entry["end"], "delta": entry["delta"], "kind": entry["kind"], "note": (entry.get("note") or "").strip()}
            for entry in entries]
    canonical = sorted(json.dumps(entry, sort_keys=True, ensure_ascii=False) for entry in sent)
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode()).hexdigest()
    uow.instruments.lock_tree()
    previous = uow.session.get(MatingBatchRow, source)
    if previous is not None:
        if previous.payload_hash != fingerprint:
            raise BatchMismatch(BATCH_MISMATCH)
        row = InstrumentMatingRow
        rows = uow.session.scalars(select(row).where(row.source == source).order_by(row.instrument_id, row.side, row.seq))
        return {"source": source, "replayed": True, "events": [event_result(event) for event in rows]}
    resolved = []
    for entry in sent:
        item = (uow.instruments.get(UUID(entry["instrument_id"])) if entry["instrument_id"] is not None
                else uow.instruments.by_inventory_number(entry["inventory_number"]))
        if item is None:
            raise KeyError(f"СИ {entry['inventory_number'] or entry['instrument_id']} не найдено")
        resolved.append({**entry, "instrument_id": str(item.id)})
    uow.session.add(MatingBatchRow(source=source, payload_hash=fingerprint, actor=actor.name, created_at=now))
    uow.session.flush()
    repository = MatingRepository(uow.session)
    events = []
    for entry in resolved:
        event = repository.append(UUID(entry["instrument_id"]), end=entry["end"], delta=entry["delta"], kind=entry["kind"],
                                  note=entry["note"], source=source, actor=actor.name, now=now)
        _audit_actor(uow, event, actor, now)
        events.append(event_result(event))
    return {"source": source, "replayed": False, "events": events}


@router.post("/matings/batch")
def post_batch(payload: BatchPayload, principal: Editor, factory: Factory, now: Now):
    """One measurement or calibration run = one source; a repeated POST of the same run changes nothing."""
    if not payload.source.strip():
        raise HTTPException(422, "Укажите источник пакета сочленений")
    try:
        with factory() as uow:
            result = record_batch(uow, payload.source, [entry.model_dump() for entry in payload.entries], principal.actor, now)
            if not result["replayed"]:
                uow.commit()
            return result
    except BatchMismatch as error:
        # Permanent: the same run can never be accepted with another content (422, not a retryable 409).
        raise HTTPException(422, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error.args[0])) from error
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except (ConcurrentProductionUpdate, IntegrityError) as error:
        raise HTTPException(409, CONFLICT) from error
