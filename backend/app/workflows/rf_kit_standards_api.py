"""Physical kit registration and immutable revision downloads; bench writes belong to T3."""

from datetime import datetime
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.production.api import production_uow_factory
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.domain import ProductionValidationError
from app.modules.production.rf_kit_characterization import KitStandard
from app.modules.production.rf_kit_characterization_infrastructure import (
    RfKitRepository, RfKitStandardRow, RfStandardCharacterizationRow,
)
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from app.shared.stored_files import file_response

router = APIRouter(prefix="/api/production/instruments/{kit_id}/standards", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Reader = Annotated[Principal, Depends(production_overview_principal)]
Editor = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]


class RegisterStandardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_kit_version: int = Field(ge=1, strict=True)
    piece_label: str = Field(min_length=1, max_length=100)
    kind: Literal["OPEN", "SHORT", "LOAD", "THRU"]
    gender: Literal["male", "female"]
    gender2: Literal["male", "female"] | None = None
    f_min_hz: float = Field(ge=0)
    f_max_hz: float = Field(gt=0, le=6e9)


@router.get("")
def list_standards(kit_id: UUID, _principal: Reader, factory: Factory):
    try:
        with factory() as uow:
            return RfKitRepository(uow.session).snapshot(kit_id)
    except KeyError as error:
        raise HTTPException(404, "Набор СИ не найден") from error
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except ConcurrentProductionUpdate as error:
        raise HTTPException(409, str(error)) from error


@router.post("", status_code=201)
def register_standard(kit_id: UUID, payload: RegisterStandardPayload, principal: Editor, factory: Factory, now: Now):
    try:
        standard = KitStandard(**payload.model_dump(exclude={"expected_kit_version"}))
        with factory() as uow:
            repository = RfKitRepository(uow.session)
            row = repository.register(kit_id, standard, expected_kit_version=payload.expected_kit_version,
                                      actor=principal.actor.name, now=now)
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=kit_id,
                event_type="production.rf_kit_standard_registered", actor=principal.actor, occurred_at=now,
                details={"standard_id": str(row.id), **payload.model_dump()}))
            result = repository.snapshot(kit_id)
            uow.commit()
            return result
    except KeyError as error:
        raise HTTPException(404, "Набор СИ не найден") from error
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except (ConcurrentProductionUpdate, IntegrityError) as error:
        raise HTTPException(409, "Набор или мера изменены — обновите карточку") from error


@router.get("/{standard_id}/revisions/{revision_id}/file")
def download_revision(kit_id: UUID, standard_id: UUID, revision_id: UUID, _principal: Reader, factory: Factory):
    with factory() as uow:
        row = uow.session.scalar(select(RfStandardCharacterizationRow).join(
            RfKitStandardRow, RfKitStandardRow.id == RfStandardCharacterizationRow.standard_id,
        ).where(RfKitStandardRow.kit_instrument_id == kit_id, RfKitStandardRow.id == standard_id,
                RfStandardCharacterizationRow.id == revision_id))
        if row is None:
            raise HTTPException(404, "Ревизия меры не найдена")
        return file_response(row.content, f"{standard_id}-r{row.revision}.{row.format}")
