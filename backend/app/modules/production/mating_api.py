"""Authenticated append-only instrument mating ledger and atomic run receipts."""

from datetime import datetime
import hashlib
import json
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from .api import production_uow_factory
from .domain import ProductionValidationError
from .mating_infrastructure import InstrumentMatingRow, MatingBatchRow, MatingRepository, event_result

router = APIRouter(prefix="/api/production", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Editor = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]


class MatingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end: Literal["A", "B"]
    delta: int = Field(strict=True, ge=-2_147_483_647, le=2_147_483_647)
    kind: Literal["manual", "initial", "correction"]
    note: str = Field(default="", max_length=2000)


class BatchEntry(MatingPayload):
    instrument_id: UUID | None = None
    inventory_number: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["measurement", "calibration"]
    delta: int = Field(strict=True, gt=0, le=2_147_483_647)

    @model_validator(mode="after")
    def one_identity(self):
        if (self.instrument_id is None) == (self.inventory_number is None):
            raise ValueError("Укажите id или инвентарный номер СИ")
        return self


class BatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=200)
    entries: list[BatchEntry] = Field(min_length=1, max_length=200)


@router.get("/instruments/{instrument_id}/matings")
def get_matings(instrument_id: UUID, factory: Factory,
                _principal: Annotated[Principal, Depends(production_overview_principal)]):
    with factory() as uow:
        if uow.instruments.get(instrument_id) is None:
            raise HTTPException(404, "СИ не найдено")
        return MatingRepository(uow.session).snapshot(instrument_id)


def _audit(uow, event, principal, now):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=event.instrument_id, event_type="production.instrument_mating_added",
                            actor=principal.actor, occurred_at=now,
                            details={"event_id": str(event.id), "end": event.end, "delta": event.delta,
                                     "kind": event.kind, "note": event.note, "source": event.source}))


@router.post("/instruments/{instrument_id}/matings")
def post_mating(instrument_id: UUID, payload: MatingPayload, factory: Factory, principal: Editor, now: Now):
    try:
        with factory() as uow:
            uow.instruments.lock_tree()
            if uow.instruments.get(instrument_id) is None:
                raise HTTPException(404, "СИ не найдено")
            repository = MatingRepository(uow.session)
            event = repository.append(instrument_id, **payload.model_dump(), source=None, actor=principal.actor.name, now=now)
            _audit(uow, event, principal, now)
            result = repository.snapshot(instrument_id)
            uow.commit()
            return result
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/matings/batch")
def post_batch(payload: BatchPayload, factory: Factory, principal: Editor, now: Now):
    source = payload.source.strip()
    if not source:
        raise HTTPException(422, "Укажите источник пакета сочленений")
    try:
        with factory() as uow:
            uow.instruments.lock_tree()
            entries = []
            for entry in payload.entries:
                item = (uow.instruments.get(entry.instrument_id) if entry.instrument_id is not None
                        else uow.instruments.by_inventory_number(entry.inventory_number.strip()))
                if item is None:
                    raise HTTPException(404, "СИ пакета не найдено")
                entries.append(dict(instrument_id=str(item.id), end=entry.end, delta=entry.delta, kind=entry.kind, note=entry.note.strip()))
            fingerprint = hashlib.sha256(json.dumps(sorted(entries, key=lambda e: json.dumps(e, sort_keys=True)),
                                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            previous = uow.session.get(MatingBatchRow, source)
            if previous is not None:
                if previous.payload_hash != fingerprint:
                    raise HTTPException(409, "Источник уже использован для другого пакета сочленений")
                rows = uow.session.scalars(select(InstrumentMatingRow).where(InstrumentMatingRow.source == source)
                                           .order_by(InstrumentMatingRow.id))
                return dict(source=source, replayed=True, events=[event_result(row) for row in rows])
            uow.session.add(MatingBatchRow(source=source, payload_hash=fingerprint))
            repository = MatingRepository(uow.session)
            events = []
            for entry in entries:
                entry = {**entry, "instrument_id": UUID(entry["instrument_id"])}
                event = repository.append(**entry, source=source, actor=principal.actor.name, now=now)
                _audit(uow, event, principal, now)
                events.append(event_result(event))
            uow.commit()
            return dict(source=source, replayed=False, events=events)
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
