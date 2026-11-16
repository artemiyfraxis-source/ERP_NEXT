"""Real marking payload: rollback, idempotence, human edits and model identities."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.database  # Register every table before the fixture creates the schema.
from app.modules.references.application import CreateRecordCommand, create_record
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import (
    EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow,
    SqlAlchemyReferencesUnitOfWork, SqlAlchemyTechOperationRepository, TechOperationRecordRow,
)
from app.modules.techcards.application import SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.infrastructure import (
    OperationCardTemplateRow, ProjectTechcardRow,
    SqlAlchemyTechcardsUnitOfWork, TechcardRevisionRow,
)
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision
from app.shared.persistence import Base, create_schema
from app.workflows.new_operation import NewOperationUnitOfWork


ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs/data/operation-templates-marking-1116"
SPEC = importlib.util.spec_from_file_location("marking_apply", FOLDER / "apply_templates.py")
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 16, 12)
CODES = {item["code"] for item in PAYLOAD}
LIBRARY = [item for item in json.loads((ROOT / "backend/data/techcard-library-erp.json")
                                      .read_text(encoding="utf-8"))["templates"]
           if item["title"].split(" | ")[0] in CODES]


def _uow(sessions):
    return NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))


def _apply(sessions, payload=None, *, commit=True):
    with _uow(sessions) as uow:
        uow.serialise()
        report = SCRIPT.apply_payload(PAYLOAD if payload is None else payload, uow, who=SCRIPT.actor(), now=NOW)
        if commit:
            uow.commit()
        return report


def _snapshot(sessions):
    with sessions() as session:
        return {table.name: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
                                  for row in session.execute(select(table)).mappings())
                for table in Base.metadata.sorted_tables}


def _unit(sessions, number, *, role="operational"):
    article, name = {"ОБ.102": ("ZCUT-RT660", "Маркиратор проводов трубчатый"),
                     "ОБ.103": ("HRSU-400LL", "Лазерный маркератор")}[number]
    with sessions() as session:
        model = EquipmentModelRow(id=uuid4(), name=name, article=article, maker="", role=role)
        session.add(model)
        session.flush()
        unit_id = uuid4()
        SqlAlchemyTechOperationRepository(session)._put(TechOperationRecord(
            id=unit_id, tab=TechOperationTab.EQUIPMENT, display_text=f"{article}/{name}",
            normalized_search=article.lower(), source_sheet="", sort_key=number,
            fields={"Тип": "Оборудование", "Инв. номер": number, "Для базы": f"{article}/{name}"},
            equipment_model_id=model.id,
        ))
        session.commit()
        return model.id, unit_id


@pytest.fixture()
def base():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    create_record(CreateRecordCommand(TechOperationTab.OPERATION, {
        "Номер": "INS_SLEEVE_coax", "Название": "Монтаж гильзы", "Время Операции": "123",
        "Время подготовки, сек": "456", "Машина": "Старый станок", "Инструмент": "Старая оснастка",
        "Апликаторы / модули": "Матрица аналога", "Формула": "не копировать", "№ Прог.": "старая программа",
    }, SCRIPT.actor(), NOW), SqlAlchemyReferencesUnitOfWork(sessions))
    _unit(sessions, "ОБ.102")
    _unit(sessions, "ОБ.103")
    yield sessions
    engine.dispose()


def _stored_etk(sessions):
    card = ProjectTechcard.create(project="708К.", item_index="КБ1", product_name="Кабель", source_calculation="",
                                  deadline=None, now=NOW)
    document = {"operations": [{"type": "mark", "code": "INS_MARK", "label": "Маркировка"}],
                "custom": "выданная версия"}
    card = replace(card.rebuild(document, NOW).issue(NOW),
                   inputs={"coax": {"cableArt": "RTK 031"}, "ops": [{"opKey": "legacy", "opNum": "INS_MARK"}]},
                   overrides={"route": {"custom": "ручной маршрут"}}, manual_operation_keys=("legacy",))
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(card)
        uow.techcards.add_revision(TechcardRevision(id=uuid4(), project_card_id=card.id, revision=1,
                                                   reason="Выдача", actor_name="Технолог", occurred_at=NOW,
                                                   document=document))
        uow.commit()
    return card.id


def test_dry_run_rolls_back_all_tables_including_audit_links_snapshot_queue_and_etk(base):
    _stored_etk(base)
    before = _snapshot(base)
    report = _apply(base, commit=False)
    assert set(report["created"]) == CODES
    assert report["equipment_linked"]  # Standard listeners may create reference links before explicit linking.
    assert _snapshot(base) == before


def test_second_apply_is_a_complete_noop_and_unknown_norms_stay_empty(base):
    first = _apply(base)
    assert set(first["created"]) == CODES and set(first["rows_created"]) == CODES
    before = _snapshot(base)
    second = _apply(base)
    assert set(second["unchanged"]) == CODES
    assert not any(second[key] for key in ("created", "changed", "rows_created", "equipment_linked"))
    assert _snapshot(base) == before
    with base() as session:
        for row in session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "op")):
            if row.fields["opNumber"] in CODES:
                assert not any(row.fields.get(key) for key in ("tOp", "tPrep", "tMachine", "Формула", "№ Прог."))
                if row.fields["opNumber"] not in {"PRINT_MARK_LABEL", "MARK_CONN_LASER"}:
                    assert not row.fields.get("Машина")


def test_template_and_reference_links_use_model_ids_not_unit_ids(base):
    _apply(base)
    with base() as session:
        models = {row.article: row.id for row in session.scalars(select(EquipmentModelRow))}
        templates = {row.id: row.code for row in session.scalars(select(OperationCardTemplateRow))}
        links = list(session.scalars(select(EquipmentTemplateToolRow)))
        assert {(templates[row.template_id], row.inventory_number, row.model_id, row.candidates) for row in links} == {
            ("PRINT_MARK_LABEL", "ОБ.102", models["ZCUT-RT660"], 1),
            ("MARK_CONN_LASER", "ОБ.103", models["HRSU-400LL"], 1),
        }
        op_links = list(session.scalars(select(EquipmentOperationLinkRow)))
        assert {(row.model_id, row.op_column, row.position) for row in op_links} == {
            (models["ZCUT-RT660"], "Машина", 0), (models["HRSU-400LL"], "Машина", 0),
        }


@pytest.mark.parametrize("signature", ["saved_by", "edited_by", "unsigned_save", "unsigned_edit"])
@pytest.mark.parametrize("code", ["PRINT_MARK_LABEL", "INS_FAKRA_ST_PREP"])
def test_human_sheet_and_record_edits_are_reported_and_preserved(base, signature, code):
    _apply(base)
    with base() as session:
        row = session.scalar(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == code))
        if signature.startswith("unsigned_"):
            prefix = "saved" if signature.endswith("save") else "edited"
            setattr(row, prefix + "_by", "")
            setattr(row, prefix + "_at", NOW)
        else:
            setattr(row, signature, "Технолог Иван")
        session.commit()
    before = _snapshot(base)
    changed = deepcopy(next(item for item in PAYLOAD if item["code"] == code))
    changed["record"]["steps"][0]["text"] = "Новая поставка"
    assert _apply(base, [changed])["protected"] == [code]
    assert _snapshot(base) == before


@pytest.mark.parametrize("seed_first", [True, False])
def test_seed_and_apply_share_stable_ids_and_never_duplicate_codes(base, seed_first):
    def seed():
        return seed_erp_templates(SeedErpTemplatesCommand(tuple(LIBRARY), SCRIPT.actor(), NOW),
                                  SqlAlchemyTechcardsUnitOfWork(base))

    if seed_first:
        seed()
    _apply(base)
    if not seed_first:
        seed()
    with base() as session:
        rows = list(session.scalars(select(OperationCardTemplateRow)))
        assert len(rows) == len(CODES)
        assert {row.id for row in rows} == {item["id"] for item in LIBRARY}
    before = _snapshot(base)
    _apply(base)
    assert _snapshot(base) == before


def test_prep_applied_by_the_14_november_script_gains_only_the_neutral_clarification(base):
    old_folder = ROOT / "docs/data/operation-templates-fakra-1114"
    spec = importlib.util.spec_from_file_location("previous_fakra_apply", old_folder / "apply_templates.py")
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    old_items = [item for item in json.loads((old_folder / "payload.json").read_text(encoding="utf-8"))
                 if item["code"] in SCRIPT.PREP_CODES]
    with _uow(base) as uow:
        previous.apply_payload(old_items, uow, who=previous.actor(), now=NOW, migrate_inputs=False)
        uow.commit()
    with base() as session:
        ids = {row.code: row.id for row in session.scalars(select(OperationCardTemplateRow))}
    report = _apply(base)
    assert set(report["changed"]) == SCRIPT.PREP_CODES
    assert set(report["created"]) == SCRIPT.MARKING_CODES
    with base() as session:
        for code, template_id in ids.items():
            row = session.get(OperationCardTemplateRow, template_id)
            assert row.code == code and row.edited_by == SCRIPT.AUTHOR
            assert sum("Если кабель уже разделан при резке" in step["text"]
                       for step in row.edited["steps"]) == 1


def test_human_edit_on_one_variant_protects_both_siblings(base):
    _apply(base)
    code = "INS_MARK_LABEL"
    with base() as session:
        original = session.scalar(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == code))
        original.side = "A"
        sibling = OperationCardTemplateRow(**{column.name: deepcopy(getattr(original, column.name))
                                             for column in OperationCardTemplateRow.__table__.columns})
        sibling.id += "-b"
        sibling.side, sibling.edited_by = "B", "Технолог Иван"
        session.add(sibling)
        session.commit()
    before = _snapshot(base)
    item = next(item for item in PAYLOAD if item["code"] == code)
    assert _apply(base, [item])["protected"] == [code]
    assert _snapshot(base) == before


@pytest.mark.parametrize("case", ["duplicate", "unrelated"])
def test_payload_cannot_duplicate_or_modify_an_unrelated_operation(base, case):
    items = deepcopy(PAYLOAD[:2])
    if case == "duplicate":
        items[1] = deepcopy(items[0])
    else:
        items[1]["code"] = "STRIP_HFM"
    before = _snapshot(base)
    with pytest.raises(ValueError, match="Повтор кода|Код вне поставки"):
        _apply(base, items)
    assert _snapshot(base) == before


def test_human_reference_norms_and_programs_are_kept_and_reported(base):
    _apply(base)
    with base() as session:
        row = session.scalar(select(TechOperationRecordRow).where(
            TechOperationRecordRow.fields["opNumber"].as_string() == "PRINT_MARK_LABEL"))
        row.fields = {**row.fields, "tOp": "12", "Время Операции": "12", "№ Прог.": "Программа технолога"}
        session.commit()
    before = _snapshot(base)
    report = _apply(base)
    assert "PRINT_MARK_LABEL" in report["rows_kept"]
    assert _snapshot(base) == before


def test_no_saved_etk_inputs_overrides_documents_or_revisions_are_rewired(base):
    card_id = _stored_etk(base)
    before = _snapshot(base)
    _apply(base)
    after = _snapshot(base)
    for table in (ProjectTechcardRow.__tablename__, TechcardRevisionRow.__tablename__):
        assert after[table] == before[table]
    with base() as session:
        assert session.get(ProjectTechcardRow, card_id).inputs["ops"][0]["opNum"] == "INS_MARK"


@pytest.mark.parametrize("case", ["missing", "ambiguous", "supporting"])
def test_unconfirmed_equipment_rolls_back_the_entire_transaction(base, case):
    # Break the second equipment lookup after the first card has already been flushed.
    with base() as session:
        model = session.scalar(select(EquipmentModelRow).where(EquipmentModelRow.article == "HRSU-400LL"))
        if case == "missing":
            from app.modules.references.infrastructure import EquipmentRecordRow
            unit = session.scalar(select(EquipmentRecordRow).where(EquipmentRecordRow.equipment_model_id == model.id))
            unit.fields = {**unit.fields, "Инв. номер": "ОБ.999"}
        elif case == "supporting":
            model.role = "supporting"
        session.commit()
    if case == "ambiguous":
        _unit(base, "ОБ.103")
    before = _snapshot(base)
    with pytest.raises(ValueError, match="Инв. номер"):
        _apply(base)
    assert _snapshot(base) == before


def test_manual_equipment_cell_is_preserved_on_conflict(base):
    _apply(base)
    with base() as session:
        row = session.scalar(select(TechOperationRecordRow).where(
            TechOperationRecordRow.fields["opNumber"].as_string() == "PRINT_MARK_LABEL"))
        row.fields = {**row.fields, "Машина": "Другое оборудование технолога"}
        session.commit()
    before = _snapshot(base)
    with pytest.raises(ValueError, match="ячейка БД.ОП изменена"):
        _apply(base)
    assert _snapshot(base) == before


def test_cli_defaults_to_full_rollback_and_apply_commits_once(base, monkeypatch, capsys):
    with base() as session:
        engine = session.get_bind()
    monkeypatch.setattr(app.database, "build_engine", lambda url: engine)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    before = _snapshot(base)
    assert SCRIPT.main([str(FOLDER / "payload.json")]) == 0
    assert "dry run — rolled back" in capsys.readouterr().out
    assert _snapshot(base) == before
    assert SCRIPT.main([str(FOLDER / "payload.json"), "--apply"]) == 0
    assert "COMMITTED" in capsys.readouterr().out
    applied = _snapshot(base)
    assert applied != before
    assert SCRIPT.main([str(FOLDER / "payload.json"), "--apply"]) == 0
    assert "изменений нет" in capsys.readouterr().out
    assert _snapshot(base) == applied
