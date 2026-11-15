"""Versioned RF editing independent of the existing instrument card payload."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from .api import production_uow_factory
from .application import ConcurrentProductionUpdate
from .domain import ProductionValidationError
from .rf_infrastructure import RfRepository
from .rf_interfaces import RfDetails, RfInterface, ROLE_LABELS

router = APIRouter(prefix="/api/production/instruments", tags=["production"])


class InterfacePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    series: str = Field(max_length=32)
    body: str = Field(max_length=16)
    center: str = Field(max_length=16)
    impedance_ohm: float | None = Field(default=None, gt=0, le=1000)
    f_max_ghz: float | None = Field(default=None, gt=0, le=1000)
    f_confirmed: bool = False
    f_source: str = Field(default="", max_length=2000)


class RfPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: int = Field(ge=0, strict=True)
    interfaces: list[InterfacePayload] = Field(max_length=2)
    role: str = Field(max_length=32)
    confirmed: bool = False
    source: str = Field(default="", max_length=2000)
    f_min_ghz: float | None = Field(default=None, ge=0, le=1000)
    max_points: int | None = Field(default=None, ge=2, le=10_000_001, strict=True)
    kit_definition: str = Field(default="", max_length=64)


def rf_result(details: RfDetails):
    return {"rf": asdict(details), "role_labels": ROLE_LABELS}


@router.get("/{instrument_id}/rf-interfaces")
def get_rf(instrument_id: UUID, _principal: Annotated[Principal, Depends(production_overview_principal)],
           factory: Annotated[Callable, Depends(production_uow_factory)]):
    with factory() as uow:
        if uow.instruments.get(instrument_id) is None:
            raise HTTPException(404, "СИ не найдено")
        return rf_result(RfRepository(uow.session).get(instrument_id))


@router.put("/{instrument_id}/rf-interfaces")
def put_rf(instrument_id: UUID, payload: RfPayload,
           principal: Annotated[Principal, Depends(production_quality_principal)],
           now: Annotated[datetime, Depends(moscow_now)],
           factory: Annotated[Callable, Depends(production_uow_factory)]):
    try:
        details = RfDetails(**{**payload.model_dump(), "interfaces": tuple(RfInterface(**p.model_dump()) for p in payload.interfaces)})
        with factory() as uow:
            uow.instruments.lock_tree()
            item = uow.instruments.get(instrument_id)
            if item is None:
                raise HTTPException(404, "СИ не найдено")
            if details.role == "vna" and item.kind.value != "Средство измерений":
                raise ProductionValidationError("ВАЦ должен быть средством измерений")
            repository = RfRepository(uow.session)
            before = repository.get(instrument_id)
            saved = repository.save(instrument_id, details)
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=instrument_id,
                event_type="production.rf_interfaces_updated", actor=principal.actor, occurred_at=now,
                details={"before": asdict(before), "after": asdict(saved)}))
            uow.commit()
            return rf_result(saved)
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except ConcurrentProductionUpdate as error:
        raise HTTPException(409, str(error)) from error
