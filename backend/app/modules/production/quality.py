"""Quality-control, serial-number, and measurement-instrument use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .application import ProductionRepository
from .domain import ProductionValidationError
from .execution import AppendOnlyRepository, BoxRepository, DefectRepository
from .traceability import (
    DefectDisposition,
    InstrumentStatus,
    MeasurementInstrument,
    ProductionBox,
    QcDecision,
    QcInspection,
    SerialUnit,
)


class InstrumentRepository(Protocol):
    def get(self, instrument_id: UUID) -> MeasurementInstrument | None: ...
    def list(self) -> Sequence[MeasurementInstrument]: ...
    def by_inventory_number(self, number: str) -> MeasurementInstrument | None: ...
    def save(self, instrument: MeasurementInstrument) -> None: ...


class SerialRepository(Protocol):
    def ensure_task(self, task_id: UUID, task_number: str, quantity: int) -> bool: ...
    def list_for_task(self, task_id: UUID) -> Sequence[SerialUnit]: ...
    def assign_qc(self, task_id: UUID, box_id: UUID, accepted: int, rejected: int) -> Sequence[SerialUnit]: ...


class QcRepository(Protocol):
    def by_box(self, box_id: UUID) -> QcInspection | None: ...
    def get(self, inspection_id: UUID) -> QcInspection | None: ...
    def list_rejections(self) -> Sequence[QcInspection]: ...
    def inspected_box_ids(self) -> set[UUID]: ...
    def decision_for(self, inspection_id: UUID) -> QcDecision | None: ...
    def add(self, inspection: QcInspection) -> None: ...
    def add_decision(self, decision: QcDecision) -> None: ...


class QualityUnitOfWork(Protocol):
    production: ProductionRepository
    boxes: BoxRepository
    defects: DefectRepository
    instruments: InstrumentRepository
    serials: SerialRepository
    qc: QcRepository
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class RegisterInstrumentCommand:
    inventory_number: str
    name: str
    instrument_type: str
    verification_number: str
    verified_at: datetime
    verification_due: datetime
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class InspectBoxCommand:
    box_id: UUID
    expected_version: int
    accepted: int
    rejected: int
    defect_character: str
    defect_description: str
    instrument_ids: tuple[UUID, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class DecideQcRejectionCommand:
    inspection_id: UUID
    disposition: DefectDisposition
    act_reference: str
    actor: Actor
    now: datetime


def _audit(aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict, uow: QualityUnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id, event_type=event_type,
        actor=actor, occurred_at=now, details=details,
    ))


def register_instrument(command: RegisterInstrumentCommand, uow: QualityUnitOfWork) -> MeasurementInstrument:
    with uow:
        inventory_number = str(command.inventory_number or "").strip()
        name = str(command.name or "").strip()
        instrument_type = str(command.instrument_type or "").strip()
        verification_number = str(command.verification_number or "").strip()
        if not all((inventory_number, name, instrument_type, verification_number)):
            raise ProductionValidationError("Для СИ обязательны номер, наименование, тип и поверка")
        verified_at = command.verified_at
        verification_due = command.verification_due
        if verified_at.tzinfo is None and verification_due.tzinfo is not None:
            verified_at = verified_at.replace(tzinfo=verification_due.tzinfo)
        elif verified_at.tzinfo is not None and verification_due.tzinfo is None:
            verification_due = verification_due.replace(tzinfo=verified_at.tzinfo)
        if verification_due < verified_at:
            raise ProductionValidationError("Дата окончания поверки раньше даты поверки")
        if uow.instruments.by_inventory_number(inventory_number) is not None:
            raise ProductionValidationError("СИ с таким инвентарным номером уже есть")
        instrument = MeasurementInstrument(
            id=uuid4(), version=1, inventory_number=inventory_number,
            name=name, instrument_type=instrument_type,
            verification_number=verification_number, verified_at=verified_at,
            verification_due=verification_due, status=InstrumentStatus.ACTIVE,
        )
        uow.instruments.save(instrument)
        _audit(instrument.id, "production.instrument_registered", command.actor, command.now, {
            "inventory_number": instrument.inventory_number,
        }, uow)
        uow.commit()
        return instrument


def inspect_box(command: InspectBoxCommand, uow: QualityUnitOfWork) -> tuple[ProductionBox, QcInspection, Sequence[SerialUnit]]:
    with uow:
        box = uow.boxes.get(command.box_id)
        if box is None:
            raise KeyError(f"Production box not found: {command.box_id}")
        if box.version != command.expected_version:
            raise ProductionValidationError("Коробка изменилась после загрузки формы")
        if uow.qc.by_box(box.id) is not None:
            raise ProductionValidationError("Коробка уже принята ОТК")
        task = uow.production.get(box.task_id)
        if task is None:
            raise KeyError(f"Production task not found: {box.task_id}")
        # Сетевой график: ОТК принимает выход финальной операции (из неё выходит изделие), а не max номера.
        if box.operation_id != task.final_operation.id:
            raise ProductionValidationError("ОТК принимает только выход последней операции")
        if box.available != box.available.to_integral_value():
            raise ProductionValidationError("Поштучный контроль требует целого количества")
        if command.accepted < 0 or command.rejected < 0 or command.accepted + command.rejected <= 0:
            raise ProductionValidationError("Укажите количество годных и брака ОТК")
        if command.accepted + command.rejected != int(box.available):
            raise ProductionValidationError("Годные и брак ОТК должны покрывать остаток коробки")
        if len(set(command.instrument_ids)) != len(command.instrument_ids):
            raise ProductionValidationError("Одно СИ выбрано дважды")
        for instrument_id in command.instrument_ids:
            instrument = uow.instruments.get(instrument_id)
            if instrument is None:
                raise ProductionValidationError("Средство измерения не найдено")
            instrument.validate_for(command.now)
        character = str(command.defect_character or "").strip()
        description = str(command.defect_description or "").strip()
        if command.rejected and (not character or not description):
            raise ProductionValidationError("Для брака ОТК обязательны характер и описание")
        defect_number = f"Б-{uow.defects.reserve_number()}" if command.rejected else None
        # Приёмка ОТК и есть подтверждение коробки (владелец, 13.10): мастер её больше не подтверждает.
        updated_box = box.qc_accept(Decimal(command.rejected), command.actor.name, command.now)
        uow.serials.ensure_task(task.id, task.number, task.quantity)
        serials = uow.serials.assign_qc(task.id, box.id, command.accepted, command.rejected)
        inspection = QcInspection(
            id=uuid4(), box_id=box.id, inspector_id=command.actor.id,
            inspected_at=command.now, accepted=command.accepted,
            rejected=command.rejected, defect_number=defect_number,
            defect_character=character, defect_description=description,
            instrument_ids=command.instrument_ids,
        )
        uow.boxes.save(updated_box)
        uow.qc.add(inspection)
        _audit(box.id, "production.qc_accepted", command.actor, command.now, {
            "number": box.number, "status": [box.status.value, updated_box.status.value],
            "accepted": command.accepted, "rejected": command.rejected,
            "defect_number": defect_number,
            "instrument_ids": [str(value) for value in command.instrument_ids],
        }, uow)
        uow.commit()
        return updated_box, inspection, serials


def decide_qc_rejection(command: DecideQcRejectionCommand, uow: QualityUnitOfWork) -> QcDecision:
    with uow:
        inspection = uow.qc.get(command.inspection_id)
        if inspection is None:
            raise KeyError(f"QC inspection not found: {command.inspection_id}")
        if inspection.rejected <= 0:
            raise ProductionValidationError("В проверке ОТК нет забракованных изделий")
        if uow.qc.decision_for(inspection.id) is not None:
            raise ProductionValidationError("Решение по браку ОТК уже принято")
        act_reference = str(command.act_reference or "").strip()
        if not act_reference:
            raise ProductionValidationError("Для решения по браку ОТК укажите акт или основание")
        decision = QcDecision(
            id=uuid4(), inspection_id=inspection.id,
            disposition=command.disposition, decided_by=command.actor.name,
            decided_at=command.now, act_reference=act_reference,
        )
        uow.qc.add_decision(decision)
        _audit(inspection.id, "production.qc_rejection_decided", command.actor, command.now, {
            "defect_number": inspection.defect_number,
            "disposition": command.disposition.value,
            "act_reference": act_reference,
        }, uow)
        uow.commit()
        return decision
