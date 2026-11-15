"""FAKRA apply is transactional, conservative with human work, and migrates stored inputs."""

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

from app.modules.references.application import CreateRecordCommand, create_record
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import (
    EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow, SqlAlchemyReferencesUnitOfWork,
    TechOperationRecordRow,
)
from app.modules.techcards.application import SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.etk_builder import _resolve_card
from app.modules.techcards import etk_derive
from app.modules.techcards.infrastructure import (
    OperationCardTemplateRow, ProjectTechcardRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork, TechcardRevisionRow,
)
from app.modules.techcards.operation_cards import parse_operation_library, parse_operation_template
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision
from app.modules.techcards.template_sheets import make_template_id
from app.shared.persistence import Base, create_schema
from app.workflows.etk_refresh import run_refresh
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs, with_technology

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs/data/operation-templates-fakra-1114"
SPEC = importlib.util.spec_from_file_location("fakra_apply", FOLDER / "apply_templates.py")
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)
NOW = datetime(2026, 11, 14, 12)
LATER = datetime(2026, 11, 14, 13)
CONFIGS = {row["kb"]: row["config"] for row in json.loads(
    (ROOT / "frontend/src/__fixtures__/fakra708Configs.json").read_text(encoding="utf-8"))}
SOURCE_ITEMS = [item for name in ("techcard-library.json", "techcard-library-erp.json")
                for item in json.loads((ROOT / "backend/data" / name).read_text(encoding="utf-8"))["templates"]]
SOURCE_IDS = {op["templateId"] for cfg in CONFIGS.values() for op in cfg["ops"]}
BASE_ITEMS = [item for item in SOURCE_ITEMS if item["id"] in SOURCE_IDS]
BASE_CARDS = tuple(parse_operation_library(BASE_ITEMS))


def _payload():
    return [{"code": code, "create": {"category": "FAKRA", "row": {"copy_of": "ANALOG", "name": code,
            "norms": {"Время Операции": "", "Время подготовки, сек": "", "Время машины, сек/оп; сек/м": ""}}},
             "record": {"name": code, "revision": "1", "safety": "Безопасность", "defects": "Дефекты",
                        "steps": [{"number": "1", "kind": "Контроль", "text": "Контроль HEX по изготовителю",
                                   "tolerance": "не задано"}], "tooling": []}}
            for mapping in SCRIPT.STAGES.values() for code in mapping.values()]


def _uow(sessions):
    return NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))


def _apply(sessions, payload=None, *, commit=True):
    with _uow(sessions) as uow:
        uow.serialise()
        result = SCRIPT.apply_payload(payload or _payload(), uow, who=SCRIPT.actor(), now=NOW)
        if commit:
            uow.commit()
        return result


def _snapshot(sessions):
    with sessions() as session:
        return {table.name: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
                                  for row in session.execute(select(table)).mappings())
                for table in Base.metadata.sorted_tables}


@pytest.fixture()
def base():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with _uow(sessions) as uow:
        uow.operation_cards.load(BASE_CARDS, imported_at=NOW)
        uow.commit()
    fields = {"Номер": "ANALOG", "Название": "Аналог", "Время Операции": "120", "Время подготовки, сек": "90",
              "Время машины, сек/оп; сек/м": "40", "Машина": "Старый станок", "Инструмент": "Старая оснастка",
              "Апликаторы / модули": "Неизвестная матрица", "Скорость проката": "123", "Формула": "другая"}
    create_record(CreateRecordCommand(TechOperationTab.OPERATION, fields, SCRIPT.actor(), NOW),
                  SqlAlchemyReferencesUnitOfWork(sessions))
    yield sessions
    engine.dispose()


def _store(sessions, kb="КБ1", project="708К.", *, overrides=None, manual_keys=(), config=None):
    config = deepcopy(config or CONFIGS[kb])
    refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        cards = tuple(uow.operation_cards.list())
    document, conflicts = etk_derive.derive(config, None, lambda cfg: with_technology(cfg, refs=refs)[0], cards)
    assert document and not conflicts
    card = ProjectTechcard.create(project=project, item_index=kb, product_name="Кабель", source_calculation="",
                                  deadline=None, now=NOW).rebuild(document, NOW).issue(NOW)
    card = replace(card, inputs=config, overrides=overrides, manual_operation_keys=manual_keys)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(card)
        uow.techcards.add_revision(TechcardRevision(id=uuid4(), project_card_id=card.id, revision=1,
                                                   reason="Первая выдача", actor_name="Технолог", occurred_at=NOW,
                                                   document=document))
        uow.commit()
    return card


def test_dry_run_rolls_back_every_table_including_inputs_audit_snapshot_and_refresh(base):
    _store(base)
    before = _snapshot(base)
    report = _apply(base, commit=False)
    assert len(report["created"]) == 26 and len(report["etk_changed"]) == 1
    assert _snapshot(base) == before


def test_second_apply_is_byte_identical_and_analog_process_settings_are_not_copied(base):
    report = _apply(base)
    before = _snapshot(base)
    again = _apply(base)
    assert len(report["created"]) == 26 and len(again["unchanged"]) == 26
    assert not any(again[key] for key in ("created", "changed", "rows_created", "equipment_linked", "etk_changed"))
    assert _snapshot(base) == before
    with base() as session:
        for row in session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "op")):
            if row.fields["opNumber"] != "ANALOG":
                assert all(not row.fields[key] for key in ("tOp", "tPrep", "tMachine", "Машина", "Инструмент",
                                                          "Апликаторы / модули", "Скорость проката", "Формула"))


@pytest.mark.parametrize("signature", ["saved_by", "edited_by"])
@pytest.mark.parametrize("side", ["A", "B"])
def test_a_human_sheet_or_record_edit_on_either_variant_protects_all_siblings(base, signature, side):
    _apply(base)
    item = _payload()[0]
    with base() as session:
        original = session.scalar(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == item["code"]))
        original.side = "A"
        duplicate = OperationCardTemplateRow(**{column.name: deepcopy(getattr(original, column.name))
                                               for column in OperationCardTemplateRow.__table__.columns})
        duplicate.id += "-b"
        duplicate.side = "B"
        setattr(original if side == "A" else duplicate, signature, "Технолог Иван")
        session.add(duplicate)
        session.commit()
    before = _snapshot(base)
    item["record"]["steps"][0]["text"] = "Текст новой поставки"
    report = _apply(base, [item])
    assert report["protected"] == [item["code"]]
    assert _snapshot(base) == before


@pytest.mark.parametrize("seed_first", [False, True])
def test_seed_and_apply_orders_share_the_same_template_id_without_duplicate_codes(base, seed_first):
    item = _payload()[0]
    title = f"{item['code']} | {item['record']['name']}"
    entry = {"id": make_template_id(title, ()), "title": title, "category": "FAKRA",
             "rows": [[title], ["Ход операции"], ["Контроль", "1", "Сид", "", "", "", "не задано"]]}
    command = SeedErpTemplatesCommand((entry,), SCRIPT.actor(), NOW)
    if seed_first:
        seed_erp_templates(command, SqlAlchemyTechcardsUnitOfWork(base))
    _apply(base, [item])
    if not seed_first:
        seed_erp_templates(command, SqlAlchemyTechcardsUnitOfWork(base))
    with base() as session:
        rows = list(session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == item["code"])))
        assert len(rows) == 1 and rows[0].id == entry["id"]
        assert rows[0].edited_by == SCRIPT.AUTHOR


def test_existing_seeded_templates_gain_missing_operation_rows(base):
    _apply(base)
    with base() as session:
        row = session.scalar(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "op",
                             TechOperationRecordRow.fields["opNumber"].as_string() == "INS_HFM_PREP"))
        session.delete(row)
        session.commit()
    report = _apply(base)
    assert report["rows_created"] == ["INS_HFM_PREP"]


def _unit(sessions, inventory="ОБ.010", role="operational"):
    with sessions() as session:
        model = EquipmentModelRow(id=uuid4(), name="Стриппер", article="EW-1165", maker="", role=role)
        session.add(model)
        session.flush()
        row = TechOperationRecordRow(id=uuid4(), tab="ob", display_text="EW-1165/Стриппер", normalized_search="stripper",
                                    source_sheet="", sort_key="1", fields={"Инв. номер": inventory,
                                    "Для базы": "EW-1165/Стриппер"}, equipment_model_id=model.id)
        session.add(row)
        session.commit()
        return model.id


def _tool_payload():
    item = next(item for item in _payload() if item["code"] == "STRIP_FAKRA_ST")
    item["record"]["tooling"] = [{"section": "Оборудование", "number": "1", "name": "EW-1165 | Стриппер",
                                  "inventory_number": "ОБ.010", "program": "", "parameters": "", "norm": "", "unit": ""}]
    return [item]


def test_equipment_binds_exact_inventory_to_existing_model_and_is_idempotent(base):
    model_id = _unit(base)
    report = _apply(base, _tool_payload())
    assert report["equipment_linked"]
    with base() as session:
        link = session.scalars(select(EquipmentTemplateToolRow)).one()
        assert link.model_id == model_id and link.candidates == 1 and link.inventory_number == "ОБ.010"
        links = list(session.scalars(select(EquipmentOperationLinkRow)))
        assert len(links) == 1 and links[0].model_id == model_id and links[0].op_column == "Машина"
    before = _snapshot(base)
    _apply(base, _tool_payload())
    assert _snapshot(base) == before


@pytest.mark.parametrize("case", ["missing", "ambiguous", "supporting"])
def test_equipment_missing_duplicate_or_supporting_unit_rolls_back_entire_apply(base, case):
    if case == "ambiguous":
        _unit(base)
        _unit(base)
    if case == "supporting":
        _unit(base, role="supporting")
    before = _snapshot(base)
    with pytest.raises(ValueError, match="Инв. номер"):
        _apply(base, _tool_payload())
    assert _snapshot(base) == before


def test_existing_manual_operation_equipment_cell_is_never_silently_linked(base):
    _unit(base)
    _apply(base, _tool_payload())
    with base() as session:
        row = session.scalar(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "op",
                             TechOperationRecordRow.fields["opNumber"].as_string() == "STRIP_FAKRA_ST"))
        row.fields = {**row.fields, "Машина": "Ручная оснастка технолога"}
        session.commit()
    before = _snapshot(base)
    with pytest.raises(ValueError, match="ячейка"):
        _apply(base, _tool_payload())
    assert _snapshot(base) == before


def test_an_operation_link_only_repair_is_audited_and_enqueues_refresh(base):
    _unit(base)
    _apply(base, _tool_payload())
    with base() as session:
        session.delete(session.scalars(select(EquipmentOperationLinkRow)).one())
        for row in session.scalars(select(RefreshRequestRow)):
            row.processed_at = NOW
        session.commit()
    before = _snapshot(base)
    report = _apply(base, _tool_payload())
    after = _snapshot(base)
    assert report["equipment_linked"] and report["unchanged"] == ["STRIP_FAKRA_ST"]
    assert len(after["audit_events"]) == len(before["audit_events"]) + 1
    with base() as session:
        pending = list(session.scalars(select(RefreshRequestRow).where(RefreshRequestRow.processed_at.is_(None))))
        assert len(pending) == 1 and pending[0].reason == "reference"


def test_all_real_ends_rebind_without_losing_operations_quantities_parts_or_other_products(base):
    stored = {kb: _store(base, kb) for kb in CONFIGS}
    other = _store(base, project="713К.4")
    with base() as session:
        other_before = deepcopy(session.get(ProjectTechcardRow, other.id).inputs)
    report = _apply(base)
    assert len(report["etk_changed"]) == 3 and len(report["etk_kept"]) == 1
    with base() as session:
        for kb, card in stored.items():
            config = session.get(ProjectTechcardRow, card.id).inputs
            original = CONFIGS[kb]
            if kb == "КБ11":
                assert config == original
                continue
            assert len(config["ops"]) == len(original["ops"])
            assert {key: val for key, val in config.items() if key != "ops"} == {
                key: val for key, val in original.items() if key != "ops"}
            for old, new in zip(original["ops"], config["ops"]):
                assert {key: old.get(key) for key in ("opKey", "type", "side", "units", "parts")} == {
                    key: new.get(key) for key in ("opKey", "type", "side", "units", "parts")}
                if new["opNum"] != old["opNum"]:
                    assert all(new[key] == "" for key in ("tOp", "tPrep", "tMachine"))
                    assert not any(key in new for key in SCRIPT.STALE_EQUIPMENT)
                    assert bool(new.get("noTooling")) == (new["opNum"] == "INS_FAKRA_ST_SEAL")
                if new["opNum"] == "INS_HFM_PREP":
                    assert new["label"].startswith("Подготовить опорные гильзы")
                elif new["opNum"] == "INS_FAKRA_ST_PREP":
                    assert new["label"].startswith("Подготовить гильзу, уплотнитель и крышку")
            expected = {"КБ1": 14, "КБ28": 13, "КБ3": 12}[kb]
            assert sum(op["opNum"] in {value for m in SCRIPT.STAGES.values() for value in m.values()}
                       for op in config["ops"]) == expected
        assert session.get(ProjectTechcardRow, other.id).inputs == other_before


@pytest.mark.parametrize("case", ["override", "legacy_manual", "custom_binding", "wrong_connector"])
def test_manual_edits_custom_binding_and_wrong_connector_survive(base, case):
    config = deepcopy(CONFIGS["КБ1"])
    kwargs = {}
    if case == "custom_binding":
        config["ops"][3]["templateId"] = config["ops"][0]["templateId"]
    elif case == "wrong_connector":
        config["coax"]["sideA"]["article"] = "OTHER"
        config["coax"]["sideB"]["article"] = "OTHER"
    elif case == "override":
        kwargs["overrides"] = {"operations": {"custom-key": {"operation": {"note": "Ручная правка"}}}}
    else:
        kwargs["manual_keys"] = ("custom-key",)
    card = _store(base, config=config, **kwargs)
    _apply(base)
    with base() as session:
        row = session.get(ProjectTechcardRow, card.id)
        assert row.inputs == config and row.overrides == card.overrides


def test_saved_inputs_refresh_once_and_then_have_no_extra_revision(base):
    card = _store(base)
    _apply(base)
    report = run_refresh(base, now=LATER)
    assert report.count("revised") == 1, report.text()
    _apply(base)
    again = run_refresh(base, now=LATER, everything=True)
    assert again.count("revised", "refreshed", "failed", "conflict") == 0, again.text()
    with base() as session:
        row = session.get(ProjectTechcardRow, card.id)
        assert row.revision == 2
        assert "INS_FAKRA_ST_PREP" in {op["code"] for op in row.document["operations"]}
        assert len(list(session.scalars(select(TechcardRevisionRow)))) == 2


def test_manual_route_is_rekeyed_without_changing_its_shape(base):
    card = _store(base)
    document = deepcopy(card.document)
    document["route"]["source"] = "manual"
    route = etk_derive.route_override(document)
    with base() as session:
        row = session.get(ProjectTechcardRow, card.id)
        row.overrides = {"route": route}
        session.commit()
    report = _apply(base)
    assert len(report["etk_changed"]) == 1
    with base() as session:
        row = session.get(ProjectTechcardRow, card.id)
        mapped = row.overrides["route"]
        assert mapped["edges"] == route["edges"]
        assert [{k: v for k, v in block.items() if k != "ops"} for block in mapped["blocks"]] == [
            {k: v for k, v in block.items() if k != "ops"} for block in route["blocks"]]
        assert [len(b["ops"]) for b in mapped["blocks"]] == [len(b["ops"]) for b in route["blocks"]]
    refreshed = run_refresh(base, now=LATER)
    assert refreshed.count("revised") == 1 and refreshed.count("conflict", "failed") == 0, refreshed.text()


def test_dedicated_dimensions_are_not_overwritten_by_generic_connector_enrichment():
    for code in ("INS_FAKRA_ST_CONTACT", "INS_HFM_HOUSING", "PRS_FAKRA_BH_SHIELD"):
        card = parse_operation_template(source_id=code, title=f"{code} | Монтаж", category="FAKRA",
                                        rows=[[f"{code} | Монтаж"], ["Ход операции"],
                                              ["Контроль", "1", "HEX и заглубление изготовителя", "", "", "", "1,05 ±0,15 мм"]])
        resolved = _resolve_card(card, {}, {"contactDepth": "99", "crimpHex": [{"hex": "88", "side": "A"}]})
        assert "99" not in json.dumps(resolved, ensure_ascii=False)
        assert "88" not in json.dumps(resolved, ensure_ascii=False)
        assert resolved["steps"][0]["tolerance"] == "1,05 ±0,15 мм"


def test_actual_payload_seed_first_migrates_fixture_inputs_and_links_only_confirmed_inventory(base):
    payload = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
    codes = {item["code"] for item in payload}
    with _uow(base) as uow:
        analog = SCRIPT._rows(uow)["ANALOG"]
        for source in sorted({item["create"]["row"]["copy_of"] for item in payload}):
            uow.tech_operations.save(replace(analog, id=uuid4(), fields={**analog.fields, "Номер": source, "opNumber": source}))
        uow.commit()
    inventories = {tool["inventory_number"] for item in payload for tool in item["record"]["tooling"]
                   if tool.get("inventory_number")}
    models = {number: _unit(base, number) for number in inventories}
    seed = tuple(item for item in SOURCE_ITEMS if item["title"].split(" |", 1)[0] in codes)
    assert len(seed) == 26
    seed_erp_templates(SeedErpTemplatesCommand(seed, SCRIPT.actor(), NOW), SqlAlchemyTechcardsUnitOfWork(base))
    for kb in CONFIGS:
        _store(base, kb)
    before = _snapshot(base)
    assert len(_apply(base, payload, commit=False)["etk_changed"]) == 3
    assert _snapshot(base) == before
    report = _apply(base, payload)
    assert len(report["rows_created"]) == 26 and report["created"] == []
    assert len(report["etk_changed"]) == 3 and len(report["etk_kept"]) == 1
    with base() as session:
        tools = list(session.scalars(select(EquipmentTemplateToolRow)))
        assert len(tools) == 6
        assert all(tool.model_id == models[tool.inventory_number] and tool.candidates == 1 for tool in tools)
        assert len(list(session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code.in_(codes))))) == 26
    before_second = _snapshot(base)
    _apply(base, payload)
    assert _snapshot(base) == before_second


def test_a_manual_route_with_unknown_operation_is_kept_instead_of_zipping_by_position(base):
    card = _store(base)
    document = deepcopy(card.document)
    document["route"]["source"] = "manual"
    route = etk_derive.route_override(document)
    route["blocks"][0]["ops"][0] = "unknown::custom::A"
    with base() as session:
        session.get(ProjectTechcardRow, card.id).overrides = {"route": route}
        session.commit()
    report = _apply(base)
    assert len(report["etk_protected"]) == 1 and not report["etk_changed"]
    with base() as session:
        row = session.get(ProjectTechcardRow, card.id)
        assert row.inputs == card.inputs and row.overrides == {"route": route}
