from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.main import app  # noqa: F401 registers schema
from app.modules.references.domain import ReferenceValidationError, TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository
from app.shared.persistence import Base, AuditEventRow
from app.workflows.equipment_names import ACTOR, apply_reviewed, plan
from app.workflows.equipment_instruments import binding_report
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


def test_legacy_instrument_creation_is_refused(session):
    with pytest.raises(ReferenceValidationError, match="едином реестре"):
        unit(session, "Средства измерений", "Вольтметр")
    session.rollback()
    assert not binding_report(session)
