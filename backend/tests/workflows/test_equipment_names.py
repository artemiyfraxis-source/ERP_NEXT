from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.main import app  # noqa: F401 registers schema
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.references.domain import ReferenceValidationError, TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import EquipmentInstrumentBindingRow, SqlAlchemyTechOperationRepository
from app.shared.persistence import Base, AuditEventRow
from app.workflows.equipment_names import ACTOR, apply_reviewed, plan
from app.workflows.equipment_instruments import bind_reviewed, binding_report, instrument_passports
from tests.references.test_api import references_api  # noqa: F401


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'names.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def unit(session, kind="Оборудование", name="Станция  паяльная", extra=None):
    record = TechOperationRecord.create(tab=TechOperationTab.EQUIPMENT, display_text=name, search_text=name,
        source_sheet="", sort_key=name, fields={"Тип": kind, "Наменование": name, "Инв. номер": "ОБ.001", **(extra or {})})
    repository = SqlAlchemyTechOperationRepository(session)
    repository.save(record)
    session.commit()
    return repository.get(record.id)


def test_names_are_reviewed_optimistic_journaled_and_keep_identity(session):
    original = unit(session)
    proposal = plan(session)
    assert len(proposal) == 1 and proposal[0]["confidence"] == "normalization_only"
    assert apply_reviewed(session, proposal) == 0
    proposal[0]["approved"] = True
    assert apply_reviewed(session, proposal) == 1
    session.commit()
    saved = SqlAlchemyTechOperationRepository(session).get(original.id)
    assert saved.fields["Наменование"] == "Станция паяльная"
    assert saved.display_text == proposal[0]["proposed_display"]
    assert saved.equipment_model_id == original.equipment_model_id
    event = session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "references.equipment_name_changed"))
    assert event.details["before_fields"] == original.fields
    with pytest.raises(ReferenceValidationError, match="изменилась"):
        apply_reviewed(session, proposal)


@pytest.mark.parametrize(("kind", "name", "extra", "display"), [
    ("Оборудование", "Станция", {"Модель": "ST100"}, "Станция ST100"),
    ("Аппликаторы", "Аппликатор", {"Код в базе терминалов": "A8"}, "Аппликатор для автомата A8"),
    ("Матрицы", "Матрица", {"Артикул": "D3", "Для инструмента": "ОБ.010"}, "Матрица D3 · ОБ.010"),
])
def test_reviewed_display_is_applied_even_when_name_is_unchanged(session, kind, name, extra, display):
    repository = SqlAlchemyTechOperationRepository(session)
    record = unit(session, kind, name, extra)
    proposal = plan(session)
    assert proposal[0]["proposed_display"] == display
    proposal[0]["approved"] = True
    assert apply_reviewed(session, proposal) == 1
    session.commit()
    assert repository.get(record.id).display_text == display
    assert plan(session) == []


@pytest.mark.parametrize("via_import", [False, True])
def test_reviewed_display_survives_unrelated_edit_or_import(references_api, via_import):
    created = references_api.post("/api/references/tech-operations", json={"tab": "ob", "fields": {
        "Тип": "Оборудование", "Наименование": "Станция  паяльная", "Артикул": "ST100", "Расположение": "ЦАР"}})
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    with references_api.app.state.references_uow_factory().session_factory() as session:
        proposal = plan(session)
        proposal[0]["approved"] = True
        apply_reviewed(session, proposal)
        session.commit()
    if via_import:
        response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
            "equipmentKey": "id:" + identifier, "Расположение": "ЖУК"}}]})
        assert response.status_code == 200 and len(response.json()["updated"]) == 1, response.text
    else:
        response = references_api.patch("/api/references/tech-operations/" + identifier, json={"edits": [
            {"field": "Расположение", "old": "ЦАР", "value": "ЖУК"}]})
        assert response.status_code == 200, response.text
    record = references_api.get("/api/references/tech-operations?tab=ob").json()["records"][0]
    assert record["display_text"] == proposal[0]["proposed_display"]


def test_cli_dry_run_and_reviewed_apply_do_not_boot_http_application(session, tmp_path):
    record = unit(session)
    # A used resource ledger makes HTTP startup refuse ERP_EQUIPMENT_SETUP_ENABLED=0.
    # This data-only CLI must not start/configure the HTTP application at all.
    session.execute(text("INSERT INTO production_resource_generation (id, version) VALUES (1, 1)"))
    session.commit()
    output = tmp_path / "reviewed.json"
    environment = {**os.environ, "ERP_DATABASE_URL": str(session.get_bind().url),
                   "ERP_EQUIPMENT_SETUP_ENABLED": "0", "PYTHONIOENCODING": "utf-8"}

    def run(*args):
        result = subprocess.run([sys.executable, "-m", "app.workflows.equipment_names", *args],
            cwd=Path(__file__).resolve().parents[2], env=environment, capture_output=True, text=True, encoding="utf-8", timeout=45)
        assert result.returncode == 0, result.stderr
        return result

    run("--dry-run", "--out", str(output))
    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert len(proposal) == 1 and proposal[0]["unit_id"] == str(record.id) and proposal[0]["approved"] is False
    session.expire_all()
    assert SqlAlchemyTechOperationRepository(session).get(record.id).fields == record.fields
    proposal[0]["approved"] = True
    output.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    session.rollback()  # release the read transaction before the CLI obtains its write lock
    run("--apply", "--reviewed", str(output))
    session.expire_all()
    assert SqlAlchemyTechOperationRepository(session).get(record.id).display_text == proposal[0]["proposed_display"]
    assert session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "references.equipment_name_changed"))


def test_instrument_binding_is_explicit_and_preserves_registry_evidence(session):
    record = unit(session, "Средства измерений", "Вольтметр")
    instrument = ProductionInstrumentRow(id=uuid4(), version=1, inventory_number="ОБ.001", name="Вольтметр",
        instrument_type="Вольтметр", kind="Средство измерений", serial_number="SERIAL", manufacturer="Maker",
        status="Поверка не подтверждена", vna_app="S2VNA")
    session.add(instrument)
    session.commit()
    report = binding_report(session)
    assert report == [{"unit_id": str(record.id), "instrument_id": None, "candidates": [str(instrument.id)], "resolved": False, "issue": ""}]
    pairs = [{"unit_id": str(record.id), "instrument_id": str(instrument.id)}]
    assert bind_reviewed(session, pairs, ACTOR) == 1
    session.commit()
    assert bind_reviewed(session, pairs, ACTOR) == 0
    assert session.get(EquipmentInstrumentBindingRow, record.id).instrument_id == instrument.id
    passport = instrument_passports(session, [record.id])[record.id]
    assert passport["serial_number"] == "SERIAL" and passport["verification_due"] is None
    assert passport["verified_at"] is None
    assert instrument.version == 1 and instrument.vna_app == "S2VNA"
    with pytest.raises(ReferenceValidationError, match="реестре"):
        SqlAlchemyTechOperationRepository(session).save(replace(record, fields={**record.fields, "Заводской номер": "X"}))

    instrument.status, instrument.verification_number = "Действует", "CERT-2026"
    instrument.verified_at, instrument.verification_due = datetime(2026, 11, 14), datetime(2027, 11, 14)
    session.commit()
    assert instrument_passports(session, [record.id])[record.id]["verified_at"] == "2026-11-14T00:00:00"

    # An existing registry kind change must not masquerade as a resolved measuring instrument.
    parent = ProductionInstrumentRow(id=uuid4(), version=1, inventory_number="СИ.002", name="Прибор",
        instrument_type="Прибор", kind="Средство измерений", serial_number="", manufacturer="",
        status="Поверка не подтверждена", vna_app="")
    session.add(parent)
    session.flush()
    instrument.kind = "Принадлежность"
    instrument.parent_id = parent.id
    instrument.status = "Не используется"
    instrument.verification_number = instrument.verified_at = instrument.verification_due = None
    instrument.vna_app = ""
    session.commit()
    assert binding_report(session)[0]["resolved"] is False
    assert binding_report(session)[0]["issue"]
    assert instrument_passports(session, [record.id]) == {}
