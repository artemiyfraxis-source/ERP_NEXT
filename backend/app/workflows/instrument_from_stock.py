"""Register an instrument accessory and its warehouse write-off in one transaction."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.production.api import instrument_result, production_uow_factory
from app.modules.production.domain import ProductionValidationError
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.infrastructure import SqlAlchemyInstrumentFileRepository, SqlAlchemyInstrumentRepository
from app.modules.production.quality import RegisterInstrumentCommand, next_instrument_number, register_instrument
from app.modules.production.traceability import InstrumentKind, InstrumentStatus
from app.modules.warehouse.application import ConcurrentWarehouseUpdate, ReturnWriteOffCommand, WriteOffCommand, return_writeoff, write_off
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository, SqlAlchemyWriteOffJournal, WriteOffEntryRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_quality_principal
from app.shared.persistence import SqlAlchemyAuditRepository

router = APIRouter(prefix="/api/production/instruments", tags=["production"])


class InstrumentFromStockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: UUID
    lot_id: UUID
    lot_version: int = Field(ge=1)
    quantity: int = Field(ge=1, le=2_147_483_647, strict=True)
    inventory_number: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    doc_designation: str = ""
    storage_location: str = Field(default="ОТК", max_length=255)


class InstrumentReturnToStockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class _SharedUnitOfWork:
    """Use-case commits flush; the workflow alone owns commit, rollback and close."""

    def __init__(self, session: Session):
        self.session = session
        self.instruments = SqlAlchemyInstrumentRepository(session)
        self.warehouse = SqlAlchemyWarehouseRepository(session)
        self.writeoffs = SqlAlchemyWriteOffJournal(session)
        self.audit = SqlAlchemyAuditRepository(session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def commit(self):
        self.session.flush()


def instrument_from_stock(payload: InstrumentFromStockPayload, actor, now: datetime, sessions):
    with sessions.begin() as session:
        uow = _SharedUnitOfWork(session)
        uow.instruments.lock_tree()
        parent = uow.instruments.get(payload.parent_id)
        if parent is None or parent.kind is not InstrumentKind.MEASURING:
            raise ProductionValidationError("Родителем может быть только существующее средство измерений")
        lot = uow.warehouse.get(payload.lot_id)
        if lot is None:
            raise KeyError("Складская партия не найдена")
        purpose = f"Принадлежности СИ {parent.inventory_number}"
        command = WriteOffCommand(
            lot_id=lot.id, expected_version=payload.lot_version, quantity=Decimal(payload.quantity),
            purpose=purpose, reason=purpose, actor=actor, now=now,
        )
        updated = write_off(command, uow)
        item = register_instrument(RegisterInstrumentCommand(
            inventory_number=payload.inventory_number if payload.inventory_number is not None else next_instrument_number(uow.instruments),
            name=payload.name if payload.name is not None else lot.name,
            instrument_type="Принадлежность", kind=InstrumentKind.ACCESSORY, parent_id=parent.id,
            quantity=payload.quantity, warehouse_grn=lot.grn, doc_designation=payload.doc_designation,
            warehouse_writeoff_id=command.writeoff_id,
            storage_location=payload.storage_location, manufacturer=lot.manufacturer,
            verification_number=None, verified_at=None, verification_due=None, actor=actor, now=now,
        ), uow)
        return item, updated


def instrument_return_to_stock(instrument_id: UUID, version: int, actor, now: datetime, sessions):
    with sessions.begin() as session:
        uow = _SharedUnitOfWork(session)
        uow.instruments.lock_tree()
        item = uow.instruments.get(instrument_id)
        if item is None:
            raise KeyError("СИ не найдено")
        if item.version != version:
            raise ConcurrentProductionUpdate("СИ изменилось после загрузки формы")
        if item.kind is not InstrumentKind.ACCESSORY or item.warehouse_writeoff_id is None:
            raise ProductionValidationError("У принадлежности нет исходного списания со склада")
        entry = session.get(WriteOffEntryRow, item.warehouse_writeoff_id)
        if entry is None or entry.lot_id is None:
            raise ProductionValidationError("Не найдено исходное списание принадлежности СИ")
        lot = uow.warehouse.get(entry.lot_id)
        if lot is None:
            raise ProductionValidationError("Исходная складская партия не найдена")
        updated = return_writeoff(ReturnWriteOffCommand(
            lot_id=lot.id, expected_version=lot.version, quantity=entry.quantity, source=entry.purpose,
            reason="Возврат принадлежности СИ", actor=actor, now=now, instrument_writeoff_id=entry.id,
        ), uow)
        returned = replace(item, status=InstrumentStatus.INACTIVE, version=item.version + 1)
        uow.instruments.save(returned)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=item.id, event_type="production.instrument_returned_to_stock",
            actor=actor, occurred_at=now, details={
                "warehouse_writeoff_id": str(entry.id), "lot_id": str(lot.id), "quantity": str(entry.quantity),
                "version": [item.version, returned.version], "status": [item.status.value, returned.status.value],
            },
        ))
        files = [file for file in SqlAlchemyInstrumentFileRepository(session).list() if file.instrument_id == item.id]
        return returned, updated, files


@router.post("/from-stock")
def create_instrument_from_stock(
    payload: InstrumentFromStockPayload,
    principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory=Depends(production_uow_factory),
) -> dict:
    sessions = getattr(factory(), "session_factory", None)
    if sessions is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    try:
        item, lot = instrument_from_stock(payload, principal.actor, now, sessions)
    except (ProductionValidationError, WarehouseValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ConcurrentProductionUpdate, ConcurrentWarehouseUpdate, IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Данные изменились: обновите форму") from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"instrument": instrument_result(item), "lot_id": str(lot.id), "lot_version": lot.version}


@router.post("/{instrument_id}/return-to-stock")
def return_instrument_to_stock(
    instrument_id: UUID,
    payload: InstrumentReturnToStockPayload,
    principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory=Depends(production_uow_factory),
) -> dict:
    sessions = getattr(factory(), "session_factory", None)
    if sessions is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    try:
        item, lot, files = instrument_return_to_stock(instrument_id, payload.version, principal.actor, now, sessions)
    except (ProductionValidationError, WarehouseValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ConcurrentProductionUpdate, ConcurrentWarehouseUpdate, IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Данные изменились: обновите форму") from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"instrument": instrument_result(item, files), "lot_id": str(lot.id), "lot_version": lot.version}
