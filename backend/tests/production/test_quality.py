from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.quality import (
    DecideQcRejectionCommand,
    InspectBoxCommand,
    RegisterInstrumentCommand,
    decide_qc_rejection,
    inspect_box,
    register_instrument,
)
from app.modules.production.traceability import BoxStatus, DefectDisposition, InstrumentStatus, MeasurementInstrument, ProductionBox, SerialStatus, format_box_number
from tests.production.test_materials_finished_goods import stage_three_setup


@pytest.mark.parametrize("changes", [{"verification_number": None}, {"verification_number": " "}, {"verified_at": None}, {"verification_due": None}])
def test_active_instrument_with_missing_evidence_fails_closed(changes):
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    active = MeasurementInstrument(
        id=uuid4(), version=1, inventory_number="TEST-VALIDATION", name="Тестовый прибор", instrument_type="Тестер",
        verification_number="TEST-PV", verified_at=now - timedelta(days=1), verification_due=now + timedelta(days=1),
        status=InstrumentStatus.ACTIVE,
    )
    with pytest.raises(ProductionValidationError, match="не подтверждена"):
        replace(active, **changes).validate_for(now)


def test_qc_assigns_serials_and_isolates_rejected_units_with_checked_number():
    engine, _sessions, factory, master, operator, task, _line_id, now = stage_three_setup(fulfillment=False)
    box = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator.id, created_at=now,
    ).add_output(Decimal("5"))
    box = box.close(format_box_number(1), operator.name, now).confirm(master.name, now)
    with factory() as uow:
        uow.boxes.save(box)
        uow.commit()
    instrument = register_instrument(RegisterInstrumentCommand(
        inventory_number="СИ-001", name="Мультиметр", instrument_type="Электрический",
        verification_number="ПВ-77", verified_at=now - timedelta(days=30),
        verification_due=now + timedelta(days=335), actor=master, now=now,
    ), factory())

    updated, inspection, serials = inspect_box(InspectBoxCommand(
        box_id=box.id, expected_version=box.version, accepted=4, rejected=1,
        defect_character="Обрыв", defect_description="Нет контакта",
        instrument_ids=(instrument.id,), actor=master, now=now,
    ), factory())

    assert updated.status is BoxStatus.CONFIRMED
    assert updated.rejected == Decimal("1")
    assert updated.available == Decimal("4")
    assert inspection.defect_number == "Б-000001-8"
    assert [item.status for item in serials].count(SerialStatus.QC_ACCEPTED) == 4
    assert [item.status for item in serials].count(SerialStatus.DEFECT) == 1
    engine.dispose()


@pytest.mark.parametrize("pending", [False, True])
def test_qc_rejects_expired_or_unverified_measurement_instrument(pending):
    engine, _sessions, factory, master, operator, task, _line_id, now = stage_three_setup(fulfillment=False)
    box = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator.id, created_at=now,
    ).add_output(Decimal("1"))
    box = box.close(format_box_number(1), operator.name, now).confirm(master.name, now)
    with factory() as uow:
        uow.boxes.save(box)
        uow.commit()
    instrument = register_instrument(RegisterInstrumentCommand(
        inventory_number="СИ-OLD", name="Штангенциркуль", instrument_type="Линейный",
        verification_number=None if pending else "ПВ-OLD", verified_at=None if pending else now - timedelta(days=400),
        verification_due=None if pending else now - timedelta(days=1), actor=master, now=now,
        status=InstrumentStatus.UNVERIFIED if pending else InstrumentStatus.ACTIVE,
    ), factory())

    with pytest.raises(ProductionValidationError, match="не подтверждена" if pending else "истекла"):
        inspect_box(InspectBoxCommand(
            box_id=box.id, expected_version=box.version, accepted=1, rejected=0,
            defect_character="", defect_description="", instrument_ids=(instrument.id,),
            actor=master, now=now,
        ), factory())
    with factory() as uow:
        assert uow.qc.by_box(box.id) is None
        assert uow.boxes.get(box.id) == box
    engine.dispose()


def test_master_decides_qc_rejection_once_without_rewriting_inspection():
    engine, _sessions, factory, master, operator, task, _line_id, now = stage_three_setup(fulfillment=False)
    box = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator.id, created_at=now,
    ).add_output(Decimal("2"))
    box = box.close(format_box_number(2), operator.name, now).confirm(master.name, now)
    with factory() as uow:
        uow.boxes.save(box)
        uow.commit()
    _updated, inspection, _serials = inspect_box(InspectBoxCommand(
        box_id=box.id, expected_version=box.version, accepted=1, rejected=1,
        defect_character="Обрыв", defect_description="Нет контакта",
        instrument_ids=(), actor=master, now=now,
    ), factory())

    decision = decide_qc_rejection(DecideQcRejectionCommand(
        inspection_id=inspection.id, disposition=DefectDisposition.SCRAP,
        act_reference="Акт НС-12", actor=master, now=now,
    ), factory())

    with factory() as uow:
        persisted = uow.qc.get(inspection.id)
        saved_decision = uow.qc.decision_for(inspection.id)
    assert persisted == inspection
    assert saved_decision == decision
    with pytest.raises(ProductionValidationError, match="уже принято"):
        decide_qc_rejection(DecideQcRejectionCommand(
            inspection_id=inspection.id, disposition=DefectDisposition.REWORK,
            act_reference="Повтор", actor=master, now=now,
        ), factory())
    engine.dispose()
