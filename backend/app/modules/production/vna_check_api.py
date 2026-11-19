"""«Журнал проверок ВАЦ»: records from the VNA program (idempotent per source) and the card's journal with due dates."""

from datetime import datetime, timedelta
import hashlib
import json
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.shared.clock import as_utc, moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from .api import production_uow_factory
from .application import ConcurrentProductionUpdate
from .domain import ProductionValidationError
from .infrastructure import ProductionInstrumentRow
from .rf_infrastructure import RfRepository
from .vna_check_infrastructure import VALUE_FIELDS, VnaCheckRepository, VnaCheckRow, record_result
from .vna_checks import expected_verdict

router = APIRouter(prefix="/api/production", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Editor = Annotated[Principal, Depends(production_quality_principal)]
Viewer = Annotated[Principal, Depends(production_overview_principal)]
Now = Annotated[datetime, Depends(moscow_now)]
REPLAY_MISMATCH = "Эта проверка уже записана с другими результатами"
CONFLICT = "Журнал проверок ВАЦ изменён другим пользователем — повторите отправку"
Dev = Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)]
Vswr = Annotated[float, Field(ge=1, le=1000, allow_inf_nan=False)]
Limit = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)]
Temperature = Annotated[float, Field(ge=-40, le=125, allow_inf_nan=False)]


class VnaCheckPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=200)
    vna_serial: str = Field(min_length=1, max_length=128)
    kind: Literal["session", "flex", "control_standard", "kit_standards"]
    passed: bool
    performed_at: datetime
    operator: str = Field(default="", max_length=200)
    calibration_id: str = Field(default="", max_length=64)
    acm_serial: str = Field(default="", max_length=128)
    profile: str = Field(default="", max_length=300)
    vna_temperature_c: Temperature | None = None
    acm_temperature_c: Temperature | None = None
    confidence_passed: bool | None = None
    confidence_tx_dev_db: Dev | None = None
    confidence_rx_dev_db: Dev | None = None
    load_p1_vswr: Vswr | None = None
    load_p2_vswr: Vswr | None = None
    standard_inventory: str | None = Field(default=None, max_length=100)
    reference: str = Field(default="", max_length=300)
    vswr_dev: Dev | None = None
    vswr_limit: Limit | None = None
    s21_dev_db: Dev | None = None
    s21_limit_db: Limit | None = None
    kit_ratio: Dev | None = None
    note: str = Field(default="", max_length=2000)


def _snapshot(uow, instrument_id: UUID, now: datetime) -> dict:
    repository = VnaCheckRepository(uow.session)
    rf = RfRepository(uow.session).get(instrument_id)
    row = uow.session.get(ProductionInstrumentRow, instrument_id)
    applicable = rf.role == "vna" or bool(row is not None and row.vna_app) or repository.has_records(instrument_id)
    return {"instrument_id": str(instrument_id), "applicable": applicable,
            "due": repository.due(instrument_id, now), "records": repository.journal(instrument_id)}


def _vna_by_serial(uow, serial: str):
    serial = serial.strip()
    found = uow.session.scalar(select(ProductionInstrumentRow.id).where(ProductionInstrumentRow.serial_number == serial)) if serial else None
    if found is None:
        raise HTTPException(404, f"Карточка ВАЦ с зав. № {serial} не найдена — укажите зав. № в карточке СИ")
    return found


@router.get("/instruments/{instrument_id}/vna-checks")
def get_vna_checks(instrument_id: UUID, _principal: Viewer, factory: Factory, now: Now):
    with factory() as uow:
        if uow.instruments.get(instrument_id) is None:
            raise HTTPException(404, "СИ не найдено")
        return _snapshot(uow, instrument_id, now)


@router.get("/vna-checks/by-serial/{serial}")
def get_vna_checks_by_serial(serial: str, _principal: Viewer, factory: Factory, now: Now):
    """The VNA program's reminder: weekly and monthly control state of the connected VNA."""
    with factory() as uow:
        instrument_id = _vna_by_serial(uow, serial)
        snapshot = _snapshot(uow, instrument_id, now)
        snapshot["records"] = snapshot["records"][:20]
        return snapshot


@router.post("/vna-checks")
def post_vna_check(payload: VnaCheckPayload, principal: Editor, factory: Factory, now: Now):
    values = payload.model_dump()
    source = values["source"].strip()
    if not source:
        raise HTTPException(422, "Укажите источник записи проверки")
    values["source"] = source
    values["standard_inventory"] = (values["standard_inventory"] or "").strip() or None
    values["performed_at"] = as_utc(values["performed_at"])
    if values["performed_at"] > as_utc(now) + timedelta(minutes=10):
        raise HTTPException(422, "Время проверки в будущем — проверьте часы ПК замера")
    try:
        verdict = expected_verdict(values["kind"], values)
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    if verdict != values["passed"]:
        raise HTTPException(422, "Итог проверки не совпадает с её результатами")
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    try:
        with factory() as uow:
            uow.instruments.lock_tree()
            repository = VnaCheckRepository(uow.session)
            previous = repository.by_source(source)
            if previous is not None:
                if previous.payload_hash != fingerprint:
                    raise HTTPException(422, REPLAY_MISMATCH)
                return {"replayed": True, "record": record_result(previous)}
            instrument_id = _vna_by_serial(uow, values["vna_serial"])
            row = repository.add(VnaCheckRow(
                id=uuid4(), instrument_id=instrument_id, kind=values["kind"], passed=values["passed"],
                performed_at=values["performed_at"], recorded_at=now, source=source, payload_hash=fingerprint,
                operator=values["operator"].strip(), actor=principal.actor.name, calibration_id=values["calibration_id"].strip(),
                acm_serial=values["acm_serial"].strip(), profile=values["profile"].strip(),
                vna_temperature_c=values["vna_temperature_c"], acm_temperature_c=values["acm_temperature_c"],
                **{name: values[name] for name in VALUE_FIELDS if name != "reference"}, reference=values["reference"].strip(),
                note=values["note"].strip()))
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=instrument_id, event_type="production.vna_check_recorded",
                                     actor=principal.actor, occurred_at=now,
                                     details={"check_id": str(row.id), "kind": row.kind, "passed": row.passed, "source": source}))
            result = record_result(row)
            uow.commit()
            return {"replayed": False, "record": result}
    except (ConcurrentProductionUpdate, IntegrityError) as error:
        raise HTTPException(409, CONFLICT) from error
