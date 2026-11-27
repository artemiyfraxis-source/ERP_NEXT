"""Physical kit registration, activation of a complete snapshot, the kit manifest and immutable revision downloads.

Bench captures and revisions are written by the Planar «Характеристика мер» session (workflows/vna_bench_api.py)."""

from datetime import datetime
import json
from typing import Annotated, Callable, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.production.api import production_uow_factory
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.production.rf_infrastructure import RfDetailsRow
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


class ActivatePayload(BaseModel):
    """The complete snapshot the screen showed: every piece's latest revision and version (CAS)."""
    model_config = ConfigDict(extra="forbid")
    expected_kit_version: int = Field(ge=1, strict=True)
    revisions: dict[UUID, UUID] = Field(min_length=8, max_length=8)
    expected_standard_versions: dict[UUID, int] = Field(min_length=8, max_length=8)


@router.post("/activate")
def activate_kit(kit_id: UUID, payload: ActivatePayload, principal: Editor, factory: Factory, now: Now):
    """«Активировать набор»: all eight pieces with validated newest revisions become the new generation at once."""
    try:
        with factory() as uow:
            repository = RfKitRepository(uow.session)
            result = repository.activate(kit_id, payload.revisions, expected_kit_version=payload.expected_kit_version,
                                         expected_standard_versions=payload.expected_standard_versions,
                                         actor=principal.actor.name, now=now)
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=kit_id, event_type="production.rf_kit_activated",
                                     actor=principal.actor, occurred_at=now, details=result))
            snapshot = repository.snapshot(kit_id)
            uow.commit()
            return snapshot
    except KeyError as error:
        raise HTTPException(404, "Набор СИ не найден") from error
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except (ConcurrentProductionUpdate, IntegrityError) as error:
        raise HTTPException(409, "Набор или мера изменены — обновите карточку") from error


@router.get("/manifest")
def manifest(kit_id: UUID, _principal: Reader, factory: Factory):
    """The active generation as JSON: piece UUIDs, bands, revisions, file names and SHA-256 of every curve."""
    with factory() as uow:
        details = uow.session.get(RfDetailsRow, kit_id)
        card = uow.session.get(ProductionInstrumentRow, kit_id)
        if details is None or card is None or not details.characterization_generation:
            raise HTTPException(404, "Набор не активирован — манифеста нет")
        pieces = list(uow.session.scalars(select(RfKitStandardRow).where(RfKitStandardRow.kit_instrument_id == kit_id)
                                          .order_by(RfKitStandardRow.piece_label)))
        standards = []
        for piece in pieces:
            revision = uow.session.get(RfStandardCharacterizationRow, piece.current_revision_id)
            standards.append(dict(
                id=str(piece.id), piece_label=piece.piece_label, kind=piece.kind, gender=piece.gender,
                gender2=piece.gender2, f_min_hz=piece.f_min_hz, f_max_hz=piece.f_max_hz,
                revision=dict(id=str(revision.id), revision=revision.revision, format=revision.format,
                              file_name=f"{piece.id}-r{revision.revision}.{revision.format}", sha256=revision.sha256,
                              grid_hash=revision.grid_hash, points=revision.points, f_min_hz=revision.f_min_hz,
                              f_max_hz=revision.f_max_hz, created_at=revision.created_at.isoformat(),
                              created_by=revision.created_by, raw_artifact_id=str(revision.raw_artifact_id)
                              if revision.raw_artifact_id else None,
                              session_id=revision.provenance.get("session_id"),
                              validation=revision.validation)))
        body = dict(schema="sma-mf06-kit-manifest.v1", kit=dict(id=str(kit_id), number=card.inventory_number,
                    name=card.name, kit_definition=details.kit_definition),
                    generation=details.characterization_generation, hash=details.characterization_hash,
                    standards=standards)
        content = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True).encode()
        name = f"{card.inventory_number}-поколение-{details.characterization_generation}.json"
        return Response(content=content, media_type="application/json", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "X-Content-Type-Options": "nosniff"})
