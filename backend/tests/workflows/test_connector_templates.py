"""Short connector cards (18.11): guarded upgrade of earlier deliveries, rollback, drift and human-edit protection."""

from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.database  # noqa: F401 - register every table for the in-memory schema.
from app.modules.references.application import CreateRecordCommand, create_record
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import (
    EquipmentModelRow, SqlAlchemyReferencesUnitOfWork, SqlAlchemyTechOperationRepository,
)
from app.modules.techcards.application import SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.infrastructure import OperationCardTemplateRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import normalize_record_edit
from app.shared.persistence import Base, create_schema
from app.workflows.new_operation import NewOperationUnitOfWork

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "docs/data"


def _script(folder):
    spec = importlib.util.spec_from_file_location(f"apply_{folder.replace('-', '_')}", DATA / folder / "apply_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(folder, name="payload.json"):
    return json.loads((DATA / folder / name).read_text(encoding="utf-8"))


SCRIPT = _script("operation-templates-connectors-1118")
PAYLOAD = _json("operation-templates-connectors-1118")
ROLLBACK = _json("operation-templates-connectors-1118", "rollback.json")
CODES = {item["code"] for item in PAYLOAD}
LIBRARY = [item for item in json.loads((ROOT / "backend/data/techcard-library-erp.json").read_text(encoding="utf-8"))["templates"]
           if item["title"].split(" | ", 1)[0] in CODES]
NOW = datetime(2026, 11, 18, 12)
ANALOGS = ("INS_SLEEVE_coax", "STRIP_COAX_hand", "PRS_SLEEVE_coax", "INS_housing_COAX", "PRS_CAP_COAX",
           "SDR_COAX_pin", "HEAT_TTK_short")


def _unit(sessions, inventory):
    with sessions() as session:
        model = EquipmentModelRow(id=uuid4(), name=inventory, article=inventory, maker="", role="operational")
        session.add(model)
        session.flush()
        SqlAlchemyTechOperationRepository(session)._put(TechOperationRecord(
            id=uuid4(), tab=TechOperationTab.EQUIPMENT, display_text=f"{inventory}/Оборудование",
            normalized_search=inventory.lower(), source_sheet="", sort_key=inventory,
            fields={"Тип": "Оборудование", "Инв. номер": inventory, "Для базы": f"{inventory}/Оборудование"},
            equipment_model_id=model.id))
        session.commit()


@pytest.fixture()
def base():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    for code in ANALOGS:
        create_record(CreateRecordCommand(TechOperationTab.OPERATION, {"Номер": code, "Название": code},
                                          SCRIPT.actor(), NOW), SqlAlchemyReferencesUnitOfWork(sessions))
    for inventory in ("ОБ.010", "ОБ.071.1", "ОБ.070.1"):
        _unit(sessions, inventory)
    yield sessions
    engine.dispose()


def _snapshot(sessions):
    with sessions() as session:
        return {table.name: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
                                  for row in session.execute(select(table)).mappings())
                for table in Base.metadata.sorted_tables}


def _apply(sessions, payload=PAYLOAD, script=SCRIPT, *, commit=True, **kwargs):
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        report = script.apply_payload(payload, uow, who=script.actor(), now=NOW, **kwargs)
        if commit:
            uow.commit()
        return report


def _earlier_deliveries(sessions):
    """Production as it may stand: 14.11 FAKRA cards, 16.11 PREP clarification and HFM support card."""
    _apply(sessions, _json("operation-templates-fakra-1114"), _script("operation-templates-fakra-1114"), migrate_inputs=False)
    marking = _script("operation-templates-marking-1116")
    _apply(sessions, [item for item in _json("operation-templates-marking-1116") if item["code"] in marking.PREP_CODES], marking)
    _apply(sessions, _json("operation-templates-hfm-1116"), _script("operation-templates-hfm-1116"))


def _content(sessions, code):
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        rows = [row for row in uow.operation_cards.list() if row.code == code]
    return {(card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) for card in rows}


def _expected(item):
    edit = normalize_record_edit(item["record"])
    return {(edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling)}


def test_dry_run_rolls_back_every_table(base):
    _earlier_deliveries(base)
    before = _snapshot(base)
    assert set(_apply(base, commit=False)["changed"]) == CODES
    assert _snapshot(base) == before


def test_earlier_deliveries_are_replaced_once_and_second_apply_is_a_noop(base):
    _earlier_deliveries(base)
    report = _apply(base)
    assert set(report["changed"]) == CODES
    assert not any(report[key] for key in ("created", "protected", "drifted", "rows_created", "equipment_linked"))
    for item in PAYLOAD:
        assert _content(base, item["code"]) == _expected(item)
    before = _snapshot(base)
    again = _apply(base)
    assert set(again["unchanged"]) == CODES and not again["changed"]
    assert _snapshot(base) == before


def test_rollback_restores_the_previous_text_and_payload_can_be_reapplied(base):
    _earlier_deliveries(base)
    previous = {item["code"]: _content(base, item["code"]) for item in PAYLOAD}
    _apply(base)
    assert set(_apply(base, ROLLBACK)["changed"]) == CODES
    assert {item["code"]: _content(base, item["code"]) for item in PAYLOAD} == previous
    assert set(_apply(base)["changed"]) == CODES


def test_unknown_content_from_a_trusted_author_is_reported_and_kept(base):
    _earlier_deliveries(base)
    drifted = deepcopy(next(item for item in _json("operation-templates-fakra-1114") if item["code"] == "PRS_HFM_PIN"))
    drifted["record"]["steps"][1]["text"] = "Текст, которого не было ни в одной поставке"
    _apply(base, [drifted], _script("operation-templates-fakra-1114"), migrate_inputs=False)
    before = _content(base, "PRS_HFM_PIN")
    report = _apply(base)
    assert report["drifted"] == ["PRS_HFM_PIN"]
    assert set(report["changed"]) == CODES - {"PRS_HFM_PIN"}
    assert _content(base, "PRS_HFM_PIN") == before


@pytest.mark.parametrize("signature", ["saved_by", "edited_by"])
def test_human_edit_on_any_variant_protects_the_code(base, signature):
    _earlier_deliveries(base)
    with base() as session:
        row = session.scalar(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == "INS_HFM_HOUSING"))
        setattr(row, signature, "Технолог Иван")
        session.commit()
    before = _content(base, "INS_HFM_HOUSING")
    report = _apply(base)
    assert report["protected"] == ["INS_HFM_HOUSING"]
    assert _content(base, "INS_HFM_HOUSING") == before


def test_fresh_database_gets_cards_rows_and_only_confirmed_equipment(base):
    report = _apply(base)
    assert set(report["created"]) == CODES and set(report["rows_created"]) == CODES
    assert sorted(link.split(":")[0] for link in report["equipment_linked"] if "БД.ОП" not in link) == [
        "HEAT_FAKRA_BH_TTK", "SDR_FAKRA_BH_PIN"]


@pytest.mark.parametrize("seed_first", [True, False])
def test_seed_and_apply_share_one_stable_identity(base, seed_first):
    def seed():
        seed_erp_templates(SeedErpTemplatesCommand(templates=LIBRARY, now=NOW, actor=SCRIPT.actor()),
                           SqlAlchemyTechcardsUnitOfWork(base))
    if seed_first:
        seed()
    _apply(base)
    seed()
    assert not _apply(base)["changed"]
    with base() as session:
        rows = list(session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code.in_(CODES))))
    assert sorted((row.id, row.code) for row in rows) == sorted(
        (item["id"], item["title"].split(" | ", 1)[0]) for item in LIBRARY)


def test_payload_cannot_touch_a_code_outside_the_delivery(base):
    stray = deepcopy(PAYLOAD[0])
    stray["code"] = "STRIP_FAKRA_ST"
    with pytest.raises(ValueError, match="вне поставки"):
        _apply(base, [stray], commit=False)
