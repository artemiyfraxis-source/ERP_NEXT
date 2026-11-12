"""Quality-control, serial-number, and measurement-instrument use cases."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.clock import MOSCOW
from app.shared.identity import Actor

from .application import ConcurrentProductionUpdate, ProductionRepository
from .domain import ProductionValidationError
from .execution import AppendOnlyRepository, BoxRepository, DefectRepository
from .traceability import (
    DefectDisposition,
    InstrumentStatus,
    InstrumentKind,
    InstrumentFile,
    InstrumentFileKind,
    MeasurementInstrument,
    ProductionBox,
    QcDecision,
    QcInspection,
    SerialUnit,
)
from .vswr import VswrRepository


class InstrumentRepository(Protocol):
    def lock_tree(self) -> None: ...
    def get(self, instrument_id: UUID) -> MeasurementInstrument | None: ...
    def list(self) -> Sequence[MeasurementInstrument]: ...
    def by_inventory_number(self, number: str) -> MeasurementInstrument | None: ...
    def save(self, instrument: MeasurementInstrument) -> None: ...


class InstrumentFileRepository(Protocol):
    def list(self) -> Sequence[InstrumentFile]: ...
    def get(self, instrument_id: UUID, file_id: UUID) -> InstrumentFile | None: ...
    def add(self, file: InstrumentFile) -> None: ...
    def delete(self, instrument_id: UUID, file_id: UUID) -> bool: ...


class SerialRepository(Protocol):
    def ensure_task(self, task_id: UUID, task_number: str, quantity: int) -> bool: ...
    def list_for_task(self, task_id: UUID) -> Sequence[SerialUnit]: ...
    def by_position(self, task_id: UUID, position: int) -> SerialUnit | None: ...
    # Изделия с последним замером КСВН «не годно» идут в брак первыми (vswr.py).
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
    instrument_files: InstrumentFileRepository
    serials: SerialRepository
    qc: QcRepository
    vswr: VswrRepository
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class RegisterInstrumentCommand:
    inventory_number: str
    name: str
    instrument_type: str
    verification_number: str | None
    verified_at: datetime | None
    verification_due: datetime | None
    actor: Actor
    now: datetime
    status: InstrumentStatus = InstrumentStatus.ACTIVE
    kind: InstrumentKind = InstrumentKind.MEASURING
    parent_id: UUID | None = None
    serial_number: str = ""
    manufacturer: str = ""
    doc_designation: str = ""
    quantity: int = 1
    storage_location: str = ""
    warehouse_grn: str = ""
    warehouse_writeoff_id: UUID | None = None
    vna_app: str = ""


@dataclass(frozen=True)
class UpdateInstrumentCommand:
    instrument_id: UUID
    expected_version: int
    changes: dict
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


INSTRUMENT_EDITABLE = frozenset(field.name for field in fields(MeasurementInstrument)) - {"id", "version", "warehouse_writeoff_id"}
MAX_INSTRUMENT_FILE_SIZE = 20 * 1024 * 1024


def next_instrument_number(repository: InstrumentRepository) -> str:
    numbers = [int(match[1]) for item in repository.list()
               if (match := re.fullmatch(r"СИ\.([0-9]{3,})", item.inventory_number))]
    return f"СИ.{max(numbers, default=0) + 1:03d}"


def _validated_instrument(item: MeasurementInstrument, repository: InstrumentRepository) -> MeasurementInstrument:
    limits = {"inventory_number": 128, "name": 255, "instrument_type": 255, "serial_number": 128,
              "manufacturer": 255, "storage_location": 255, "warehouse_grn": 32}
    cleaned = {}
    for key in (*limits, "doc_designation"):
        value = getattr(item, key)
        if not isinstance(value, str) or (key in limits and len(value.strip()) > limits[key]):
            raise ProductionValidationError("Некорректное текстовое поле СИ")
        cleaned[key] = value.strip()
    if item.kind is InstrumentKind.ACCESSORY and not cleaned["instrument_type"]:
        cleaned["instrument_type"] = "Принадлежность"
    item = replace(item, **cleaned)
    if not all((item.inventory_number, item.name, item.instrument_type)):
        raise ProductionValidationError("Для СИ обязательны номер, наименование и тип")
    if not isinstance(item.kind, InstrumentKind) or not isinstance(item.status, InstrumentStatus):
        raise ProductionValidationError("Недопустимый вид или статус СИ")
    if item.vna_app not in ("", "S2VNA", "Arinst VL"):
        raise ProductionValidationError("Недопустимая программа ВАЦ")
    if item.vna_app and item.kind is not InstrumentKind.MEASURING:
        raise ProductionValidationError("Программу ВАЦ можно указать только для средства измерений")
    if type(item.quantity) is not int or not 1 <= item.quantity <= 2_147_483_647:
        raise ProductionValidationError("Количество должно быть целым положительным числом")
    if item.kind is InstrumentKind.MEASURING and item.quantity != 1:
        raise ProductionValidationError("Средство измерений учитывается поштучно: количество 1")
    evidence = (item.verification_number, item.verified_at, item.verification_due)
    if item.kind is InstrumentKind.ACCESSORY:
        if item.parent_id is None:
            raise ProductionValidationError("Для принадлежности укажите родительское СИ")
        if any(value is not None for value in evidence) or item.status is InstrumentStatus.UNVERIFIED:
            raise ProductionValidationError("Принадлежность не подлежит поверке")
        if any(child.parent_id == item.id for child in repository.list()):
            raise ProductionValidationError("СИ с дочерними записями нельзя сделать принадлежностью")
    elif item.status is InstrumentStatus.UNVERIFIED:
        if any(value is not None for value in evidence):
            raise ProductionValidationError("Для неподтверждённой поверки оставьте номер и даты пустыми")
    elif item.status is InstrumentStatus.ACTIVE:
        if not item.verification_number or not item.verification_number.strip() or item.verified_at is None or item.verification_due is None:
            raise ProductionValidationError("Для действующего СИ обязательны номер и обе даты поверки")
    verified_at, due = item.verified_at, item.verification_due
    if verified_at is not None and due is not None:
        if verified_at.tzinfo is None and due.tzinfo is not None:
            verified_at = verified_at.replace(tzinfo=due.tzinfo)
        elif verified_at.tzinfo is not None and due.tzinfo is None:
            due = due.replace(tzinfo=verified_at.tzinfo)
        if due < verified_at:
            raise ProductionValidationError("Дата окончания поверки раньше даты поверки")
    item = replace(item, verified_at=verified_at, verification_due=due,
                   verification_number=item.verification_number.strip() if item.verification_number is not None else None)
    seen = {item.id}
    parent_id = item.parent_id
    while parent_id is not None:
        if parent_id in seen:
            raise ProductionValidationError("Циклическая связь родительских СИ запрещена")
        seen.add(parent_id)
        parent = repository.get(parent_id)
        if parent is None:
            raise ProductionValidationError("Родительское СИ не найдено")
        if parent.kind is not InstrumentKind.MEASURING:
            raise ProductionValidationError("Родителем может быть только средство измерений")
        parent_id = parent.parent_id
    duplicate = repository.by_inventory_number(item.inventory_number)
    if duplicate is not None and duplicate.id != item.id:
        raise ProductionValidationError("СИ с таким инвентарным номером уже есть")
    return item


def register_instrument(command: RegisterInstrumentCommand, uow: QualityUnitOfWork) -> MeasurementInstrument:
    with uow:
        uow.instruments.lock_tree()
        if command.kind is InstrumentKind.MEASURING and command.status is InstrumentStatus.INACTIVE:
            raise ProductionValidationError("Недопустимый статус регистрации СИ")
        item = MeasurementInstrument(id=uuid4(), version=1, warehouse_writeoff_id=command.warehouse_writeoff_id, **{
            name: getattr(command, name) for name in INSTRUMENT_EDITABLE
        })
        item = _validated_instrument(item, uow.instruments)
        uow.instruments.save(item)
        _audit(item.id, "production.instrument_registered", command.actor, command.now, {
            "inventory_number": item.inventory_number, "kind": item.kind.value,
            "parent_id": str(item.parent_id) if item.parent_id else None,
            "quantity": item.quantity, "warehouse_grn": item.warehouse_grn,
            "warehouse_writeoff_id": str(item.warehouse_writeoff_id) if item.warehouse_writeoff_id else None,
        }, uow)
        uow.commit()
        return item


def update_instrument(command: UpdateInstrumentCommand, uow: QualityUnitOfWork) -> MeasurementInstrument:
    with uow:
        uow.instruments.lock_tree()
        current = uow.instruments.get(command.instrument_id)
        if current is None:
            raise KeyError("СИ не найдено")
        if current.version != command.expected_version:
            raise ConcurrentProductionUpdate("СИ изменилось после загрузки формы")
        if command.changes.keys() - INSTRUMENT_EDITABLE:
            raise ProductionValidationError("Недопустимые поля СИ")
        if current.warehouse_writeoff_id is not None and any(
                key in command.changes and command.changes[key] != getattr(current, key)
                for key in ("quantity", "kind", "warehouse_grn")):
            raise ProductionValidationError(
                "Количество, вид и ГРН принадлежности со склада меняются только возвратом на склад")
        item = replace(current, **command.changes, version=current.version + 1)
        if (current.status is InstrumentStatus.UNVERIFIED and "status" not in command.changes
                and item.kind is InstrumentKind.MEASURING and item.verification_number
                and item.verified_at is not None and item.verification_due is not None):
            item = replace(item, status=InstrumentStatus.ACTIVE)
        item = _validated_instrument(item, uow.instruments)
        uow.instruments.save(item)
        _audit(item.id, "production.instrument_updated", command.actor, command.now, {
            "version": [current.version, item.version],
            "changes": {key: [str(getattr(current, key)), str(getattr(item, key))]
                        for key in INSTRUMENT_EDITABLE if getattr(current, key) != getattr(item, key)},
        }, uow)
        uow.commit()
        return item


def attach_instrument_file(instrument_id: UUID, kind: InstrumentFileKind, file_name: str, content: bytes,
                           actor: Actor, now: datetime, uow: QualityUnitOfWork) -> InstrumentFile:
    file_name = file_name.strip()
    if not file_name or len(file_name) > 255:
        raise ProductionValidationError("Укажите имя файла длиной до 255 символов")
    if not 1 <= len(content) <= MAX_INSTRUMENT_FILE_SIZE:
        raise ProductionValidationError("Размер файла должен быть от 1 байта до 20 МиБ")
    uploaded_at = (now if now.tzinfo is not None else now.replace(tzinfo=MOSCOW)).astimezone(timezone.utc)
    with uow:
        if uow.instruments.get(instrument_id) is None:
            raise KeyError("СИ не найдено")
        item = InstrumentFile(id=uuid4(), instrument_id=instrument_id, kind=kind, file_name=file_name,
                              size=len(content), content=content, uploaded_at=uploaded_at, uploaded_by=actor.id,
                              uploaded_by_name=actor.name)
        uow.instrument_files.add(item)
        _audit(instrument_id, "production.instrument_file_uploaded", actor, now, {
            "file_id": str(item.id), "kind": kind.value, "file_name": file_name, "size": item.size,
        }, uow)
        uow.commit()
        return item


def delete_instrument_file(instrument_id: UUID, file_id: UUID, actor: Actor, now: datetime,
                           uow: QualityUnitOfWork) -> None:
    with uow:
        if not uow.instrument_files.delete(instrument_id, file_id):
            raise KeyError("Документ СИ не найден")
        _audit(instrument_id, "production.instrument_file_deleted", actor, now, {"file_id": str(file_id)}, uow)
        uow.commit()


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
