"""HFM card delivery: rollback, idempotence, human edits and seed/apply identity."""

from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.database  # Register every table for the in-memory schema.
from app.modules.references.infrastructure import EquipmentOperationLinkRow, EquipmentTemplateToolRow, TechOperationRecordRow
from app.modules.techcards.application import SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.infrastructure import OperationCardTemplateRow, SqlAlchemyTechcardsUnitOfWork
from app.shared.persistence import Base, create_schema
from app.workflows.new_operation import NewOperationUnitOfWork

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs/data/operation-templates-hfm-1116"
SPEC = importlib.util.spec_from_file_location("hfm_apply", FOLDER / "apply_templates.py")
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
LIBRARY = [item for item in json.loads((ROOT / "backend/data/techcard-library-erp.json").read_text(encoding="utf-8"))["templates"]
           if item["title"].startswith("PRS_HFM_SUPPORT |")]
NOW = datetime(2026, 11, 16, 12)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def snapshot(sessions):
    with sessions() as session:
        return {table.name: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
                                  for row in session.execute(select(table)).mappings()) for table in Base.metadata.sorted_tables}


def apply(sessions, payload=PAYLOAD, *, commit=True):
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        report = SCRIPT.apply_payload(payload, uow, who=SCRIPT.actor(), now=NOW)
        if commit:
            uow.commit()
        return report


def test_dry_run_rolls_back_every_table(sessions):
    before = snapshot(sessions)
    assert apply(sessions, commit=False)["created"] == ["PRS_HFM_SUPPORT"]
    assert snapshot(sessions) == before


def test_apply_is_idempotent_and_does_not_invent_norms_or_equipment_links(sessions):
    assert apply(sessions)["created"] == ["PRS_HFM_SUPPORT"]
    before = snapshot(sessions)
    result = apply(sessions)
    assert result["unchanged"] == ["PRS_HFM_SUPPORT"]
    assert not any(result[key] for key in ("created", "changed", "rows_created", "equipment_linked"))
    assert snapshot(sessions) == before
    with sessions() as session:
        op = session.scalar(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "op"))
        assert op.fields["opNumber"] == "PRS_HFM_SUPPORT"
        assert not any(op.fields.get(key) for key in ("tOp", "tPrep", "tMachine", "Машина", "Инструмент", "Апликаторы / модули"))
        assert not list(session.scalars(select(EquipmentOperationLinkRow)))
        assert not list(session.scalars(select(EquipmentTemplateToolRow)))


@pytest.mark.parametrize("signature", ["saved_by", "edited_by", "unsigned_save", "unsigned_edit"])
def test_human_edits_are_preserved(sessions, signature):
    apply(sessions)
    with sessions() as session:
        row = session.scalar(select(OperationCardTemplateRow))
        if signature.startswith("unsigned_"):
            prefix = "saved" if signature.endswith("save") else "edited"
            setattr(row, prefix + "_by", "")
            setattr(row, prefix + "_at", NOW)
        else:
            setattr(row, signature, "Технолог Иван")
        session.commit()
    before = snapshot(sessions)
    changed = deepcopy(PAYLOAD)
    changed[0]["record"]["steps"][0]["text"] = "Не заменять ручную правку"
    assert apply(sessions, changed)["protected"] == ["PRS_HFM_SUPPORT"]
    assert snapshot(sessions) == before


@pytest.mark.parametrize("seed_first", [True, False])
def test_seed_and_apply_share_one_stable_identity(sessions, seed_first):
    def seed():
        return seed_erp_templates(SeedErpTemplatesCommand(templates=LIBRARY, now=NOW, actor=SCRIPT.actor()), SqlAlchemyTechcardsUnitOfWork(sessions))
    if seed_first:
        seed()
    apply(sessions)
    seed()
    apply(sessions)
    with sessions() as session:
        rows = list(session.scalars(select(OperationCardTemplateRow)))
        assert [(row.id, row.code) for row in rows] == [(LIBRARY[0]["id"], "PRS_HFM_SUPPORT")]
