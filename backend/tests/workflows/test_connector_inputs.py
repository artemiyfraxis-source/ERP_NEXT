"""Saved 708К. inputs follow the short connector cards (18.11): titles and full strip at the cut, one revision wave,
plan/rollback guards, nothing outside the connector ends."""

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

import app.database  # noqa: F401 - register every table for the in-memory schema.
from app.modules.references.application import CreateRecordCommand, create_record
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import (
    EquipmentModelRow, SqlAlchemyReferencesUnitOfWork, SqlAlchemyTechOperationRepository,
)
from app.modules.techcards import etk_derive
from app.modules.techcards.infrastructure import ProjectTechcardRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision
from app.shared.persistence import Base, create_schema
from app.workflows.etk_refresh import run_refresh
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs, with_technology

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "docs/data"
FIXTURES = ROOT / "frontend/src/__fixtures__"
NOW = datetime(2026, 11, 18, 12)
LATER = datetime(2026, 11, 18, 13)


def _module(folder, name):
    spec = importlib.util.spec_from_file_location(f"{name}_{folder.replace('-', '_')}", DATA / folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


SCRIPT = _module("operation-templates-connectors-1118", "migrate_inputs")
TEMPLATES = _module("operation-templates-connectors-1118", "apply_templates")
PAYLOAD = _json(DATA / "operation-templates-connectors-1118/payload.json")
ROLLBACK = _json(DATA / "operation-templates-connectors-1118/rollback.json")
# The current generator output (tcGenFakra.test.ts asserts the generator reproduces these configs exactly).
BATCH = {row["kb"]: row["config"] for row in _json(FIXTURES / "fakraBatchAConfigs.json")}
FRESH = {row["kb"]: row["config"] for row in _json(FIXTURES / "fakra1116Configs.json")}
LEGACY = {row["kb"]: row["config"] for row in _json(FIXTURES / "fakra708Configs.json")}
LIBRARY = [*_json(ROOT / "backend/data/techcard-library.json")["templates"],
           *_json(ROOT / "backend/data/techcard-library-erp.json")["templates"]]
LIBRARY_CODE = {item["id"]: item["title"].split(" | ", 1)[0] for item in LIBRARY}
CARDS = tuple(parse_operation_library(LIBRARY))
CODE_OF = SCRIPT.code_resolver(CARDS)
DELIVERED = {item["code"] for item in _json(DATA / "operation-templates-fakra-1114/payload.json")} | {"PRS_HFM_SUPPORT"}
ANALOGS = ("INS_SLEEVE_coax", "STRIP_COAX_hand", "PRS_SLEEVE_coax", "INS_housing_COAX", "PRS_CAP_COAX",
           "SDR_COAX_pin", "HEAT_TTK_short")
OLD_WORDS = ("до щелчка", "0,18 мм", "CPA")


def _old_titles(config):
    """Production as issued on 17.11: the same recipe with the generator titles from before the short cards."""
    config = deepcopy(config)
    for op in config["ops"]:
        end = config["coax"].get(f"side{op.get('side')}") or {}
        rule = SCRIPT.TITLES.get((end.get("kind"), str(op.get("opKey") or "")[:-1]))
        if rule and op["label"].startswith(rule[2]):
            op["label"] = rule[1] + op["label"][len(rule[2]):]
    return config


def _separate_amphenol_strip(config):
    """КБ28 as PR #854 built it: only the straight end stripped at the cut, Amphenol stripped separately."""
    config = deepcopy(config)
    ops = config["ops"]
    ops[0].update(opKey="coaxCutStrip", stripSides=["B"], label="Резка с разделкой (В)")
    prep = {op["side"]: op for op in ops if op["type"] == "coaxInsSleeve"}
    strip = {"type": "coaxStrip", "side": "A", "opKey": "coaxSideStripA", "label": "Разделка коаксиала (А)", "units": 1,
             "templateId": "STRIP_FAKRA_BH", "opNum": "STRIP_FAKRA_BH", "tPrep": "", "tOp": "", "tMachine": ""}
    ordered, seen = [], set()
    for op in ops:
        if op["type"] == "coaxInsSleeve":
            continue
        side = op.get("side")
        if side and side not in seen:
            seen.add(side)
            ordered += [prep[side], strip] if side == "A" else [prep[side]]
        ordered.append(op)
    config["ops"] = ordered
    return config


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


def _uow(sessions):
    return NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))


def _deliver(sessions, folder, payload=None, **kwargs):
    script = _module(folder, "apply_templates")
    with _uow(sessions) as uow:
        uow.serialise()
        script.apply_payload(payload or _json(DATA / folder / "payload.json"), uow, who=script.actor(), now=NOW, **kwargs)
        uow.commit()


@pytest.fixture()
def prod():
    """The library as production holds it before the short cards: generic seeds plus the 14.11/16.11 deliveries."""
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with _uow(sessions) as uow:
        uow.operation_cards.load(tuple(card for card in CARDS if card.code not in DELIVERED), imported_at=NOW)
        uow.commit()
    for code in ANALOGS:
        create_record(CreateRecordCommand(TechOperationTab.OPERATION, {"Номер": code, "Название": code},
                                          SCRIPT.actor(), NOW), SqlAlchemyReferencesUnitOfWork(sessions))
    for inventory in ("ОБ.010", "ОБ.071.1", "ОБ.070.1"):
        _unit(sessions, inventory)
    _deliver(sessions, "operation-templates-fakra-1114", migrate_inputs=False)
    marking = _module("operation-templates-marking-1116", "apply_templates")
    _deliver(sessions, "operation-templates-marking-1116", [
        item for item in _json(DATA / "operation-templates-marking-1116/payload.json") if item["code"] in marking.PREP_CODES])
    _deliver(sessions, "operation-templates-hfm-1116")
    run_refresh(sessions, now=NOW)  # the deliveries' own refresh wave is history, not part of this test
    yield sessions
    engine.dispose()


def _templates(sessions):
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        return tuple(uow.operation_cards.list())


def _bind(sessions, config):
    """Fixture ops name cards by code; stored inputs name them by the database template id."""
    by_code = {}
    for card in _templates(sessions):
        by_code.setdefault(card.code, card)
    config = deepcopy(config)
    for op in config["ops"]:
        template_id = str(op.get("templateId") or "")
        code = template_id if template_id in by_code else LIBRARY_CODE.get(template_id) or str(op.get("opNum") or "")
        if code in by_code:
            op["templateId"] = by_code[code].source_id
    return config


def _refs(sessions):
    return load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))


def _store(sessions, kb, config, project="708К.", overrides=None):
    config = _bind(sessions, config)
    refs = _refs(sessions)
    document, conflicts = etk_derive.derive(config, None, lambda cfg: with_technology(cfg, refs=refs)[0], _templates(sessions))
    assert document and not conflicts, conflicts
    card = ProjectTechcard.create(project=project, item_index=kb, product_name="Кабель", source_calculation="",
                                  deadline=None, now=NOW).rebuild(document, NOW).issue(NOW)
    card = replace(card, inputs=config, overrides=overrides)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(card)
        uow.techcards.add_revision(TechcardRevision(id=uuid4(), project_card_id=card.id, revision=1, reason="Первая выдача",
                                                   actor_name="Технолог", occurred_at=NOW, document=document))
        uow.commit()
    return card


def _store_production(sessions):
    """КБ1/КБ2 straight+angled (tape), КБ3 HFM, КБ28 Amphenol+straight with the 17.11 titles; КБ11 ordinary coax;
    the same КБ1 recipe in another project."""
    cards = {kb: _store(sessions, kb, _old_titles(config)) for kb, config in
             (("КБ1", FRESH["КБ1"]), ("КБ2", FRESH["КБ2"]), ("КБ3", BATCH["КБ3"]), ("КБ28", BATCH["КБ28"]))}
    cards["КБ11"] = _store(sessions, "КБ11", LEGACY["КБ11"])
    cards["other"] = _store(sessions, "КБ1", _old_titles(FRESH["КБ1"]), project="709К.")
    return cards


def _run(sessions, *, templates=None, expect=None, commit=True):
    refs = _refs(sessions)  # before the transaction: in-memory SQLite shares one connection between sessions
    with _uow(sessions) as uow:
        uow.serialise()
        if templates is not None:
            TEMPLATES.apply_payload(templates, uow, who=TEMPLATES.actor(), now=NOW)
        out = SCRIPT.migrate_inputs(uow, who=SCRIPT.actor(), now=NOW, expect=expect, refs=refs)
        if commit:
            uow.commit()
        return out


def _rollback(sessions, data, *, templates=None):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        if templates is not None:
            TEMPLATES.apply_payload(templates, uow, who=TEMPLATES.actor(), now=NOW)
        out = SCRIPT.rollback_inputs(json.loads(json.dumps(data)), uow, who=SCRIPT.actor(), now=NOW, refs=refs)
        uow.commit()
        return out


def _snapshot(sessions):
    with sessions() as session:
        return {table.name: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
                                  for row in session.execute(select(table)).mappings())
                for table in Base.metadata.sorted_tables}


def _row(sessions, card):
    with sessions() as session:
        return session.get(ProjectTechcardRow, card.id)


def _quantities(document):
    """Everything a launched task counts from: per operation the semi-finished norms, parts, marks and serial rows."""
    norms = lambda rows: [(row.get("name"), row.get("norm"), row.get("unit")) for row in rows or []]  # noqa: E731
    return [(op["code"], op.get("side"), norms(op.get("result")), norms(op.get("semis")), op.get("components"),
             (op.get("fields") or {}).get("coaxMarks"), (op.get("fields") or {}).get("marks"), op.get("time"))
            for op in document["operations"]]


def test_dry_run_reports_the_production_shape_and_writes_nothing(prod):
    _store_production(prod)
    before = _snapshot(prod)
    result, plan, _ = _run(prod, commit=False)
    assert _snapshot(prod) == before
    assert {item["item_index"]: len(item["changes"]) for item in plan["cards"]} == {"КБ1": 2, "КБ2": 2, "КБ3": 4, "КБ28": 2}
    assert [line.split(":")[0].split(" ")[0] for line in result["kept"]] == ["КБ11"]
    assert not result["rebuild"] and not result["protected"]
    # The refresh diff ignores operation titles: without the card content these cards would never be re-issued.
    assert len(result["inputs_only"]) == 4 and not result["revised"]


def test_titles_alone_are_refused_because_no_revision_would_show_them(prod):
    _store_production(prod)
    _, plan, _ = _run(prod, commit=False)
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="--templates"):
        _run(prod, expect=plan)
    assert _snapshot(prod) == before


def test_one_transaction_with_the_short_cards_issues_one_revision_matching_the_generator(prod):
    cards = _store_production(prod)
    _, plan, _ = _run(prod, templates=PAYLOAD, commit=False)
    result, _, rollback = _run(prod, templates=PAYLOAD, expect=plan)
    assert len(result["revised"]) == 4 and not result["inputs_only"]
    assert {item["item_index"] for item in rollback["cards"]} == {"КБ1", "КБ2", "КБ3", "КБ28"}
    pending = lambda: len(list(prod().scalars(select(RefreshRequestRow).where(  # noqa: E731
        RefreshRequestRow.processed_at.is_(None)))))
    assert pending() >= 1
    report = run_refresh(prod, now=LATER)
    assert pending() == 0
    assert report.count("revised") == 5 and report.count("failed", "conflict", "skipped") == 0, report.text()
    for kb, source in (("КБ1", FRESH["КБ1"]), ("КБ2", FRESH["КБ2"]), ("КБ3", BATCH["КБ3"]), ("КБ28", BATCH["КБ28"])):
        row = _row(prod, cards[kb])
        assert row.revision == 2
        assert row.inputs == _bind(prod, source)  # exactly what the generator builds today
        titles = [op["card"]["name"] for op in row.document["operations"] if op["card"]]
        assert not [title for title in titles if any(word in title for word in OLD_WORDS)]
        assert _quantities(row.document) == _quantities(cards[kb].document)
    other = _row(prod, cards["other"])
    assert other.inputs == cards["other"].inputs and other.revision == 2  # card text only: another project's titles stay
    assert _row(prod, cards["КБ11"]).revision == 1
    assert run_refresh(prod, now=LATER, everything=True).count("revised", "refreshed", "failed", "conflict") == 0
    again, plan_again, _ = _run(prod, templates=PAYLOAD, commit=False)
    assert not again["changed"] and plan_again["cards"] == []


def test_apply_follows_the_reviewed_plan_or_writes_nothing(prod):
    cards = _store_production(prod)
    _, plan, _ = _run(prod, templates=PAYLOAD, commit=False)
    with prod() as session:  # a technologist saves КБ3 after the review
        row = session.get(ProjectTechcardRow, cards["КБ3"].id)
        row.inputs = {**row.inputs, "partQty": 16}
        row.version += 1
        session.commit()
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="изменилась после пробного прогона"):
        _run(prod, templates=PAYLOAD, expect=plan)
    _, fresh, _ = _run(prod, templates=PAYLOAD, commit=False)
    with pytest.raises(ValueError, match="Состав карт изменился"):
        _run(prod, templates=PAYLOAD, expect={**fresh, "cards": fresh["cards"][1:]})
    assert _snapshot(prod) == before


def test_rollback_restores_inputs_with_the_previous_cards_and_refuses_a_second_time(prod):
    cards = _store_production(prod)
    _, plan, _ = _run(prod, templates=PAYLOAD, commit=False)
    _, _, rollback = _run(prod, templates=PAYLOAD, expect=plan)
    run_refresh(prod, now=LATER)
    result = _rollback(prod, rollback, templates=ROLLBACK)
    assert len(result["changed"]) == 4 and len(result["revised"]) == 4
    for kb in ("КБ1", "КБ2", "КБ3", "КБ28"):
        assert _row(prod, cards[kb]).inputs == cards[kb].inputs
    run_refresh(prod, now=LATER)
    assert _row(prod, cards["КБ3"]).revision == 3
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="откат не выполнен"):
        _rollback(prod, rollback)
    assert _snapshot(prod) == before


def test_amphenol_separate_strip_folds_into_the_cut_exactly_like_the_generator():
    migrated, changes, notes, rebuild, _ = SCRIPT.migrate_config(_old_titles(_separate_amphenol_strip(BATCH["КБ28"])), CODE_OF)
    assert migrated == BATCH["КБ28"]
    assert not notes and not rebuild
    assert changes[-1].startswith("coaxSideStripA: отдельная разделка убрана")
    # Tape flow: the label op stays after both preparations.
    fresh = deepcopy(FRESH["КБ1"])
    assert SCRIPT.migrate_config(fresh, CODE_OF)[0] == fresh


def test_a_hand_cut_scheme_gets_titles_only_and_keeps_its_separate_strip(prod):
    config = _old_titles(_separate_amphenol_strip(BATCH["КБ28"]))
    stored = _store(prod, "КБ28", config)
    document = deepcopy(stored.document)
    document["route"]["source"] = "manual"
    route = etk_derive.route_override(document)
    with prod() as session:
        session.get(ProjectTechcardRow, stored.id).overrides = {"route": route}
        session.commit()
    result, plan, _ = _run(prod, templates=PAYLOAD, commit=False)
    assert len(result["rebuild"]) == 1 and "ручные правки" in result["rebuild"][0]
    _run(prod, templates=PAYLOAD, expect=plan)
    row = _row(prod, stored)
    assert [op["opKey"] for op in row.inputs["ops"]] == [op["opKey"] for op in config["ops"]]
    assert row.overrides == {"route": route}
    report = run_refresh(prod, now=LATER)
    assert report.count("revised") == 1 and report.count("conflict", "failed") == 0, report.text()


def test_hfm_strip_custom_titles_bindings_and_foreign_ends_stay():
    hfm = _old_titles(BATCH["КБ3"])
    for side in ("A", "B"):
        at = next(i for i, op in enumerate(hfm["ops"]) if op["opKey"] == f"coaxSupportCrimp{side}")
        hfm["ops"].insert(at, {"type": "coaxStrip", "side": side, "opKey": f"coaxSideStrip{side}", "units": 4,
                               "label": f"Разделка коаксиала ×4 ({'А' if side == 'A' else 'В'})", "templateId": "STRIP_HFM"})
    migrated, changes, _, rebuild, _ = SCRIPT.migrate_config(hfm, CODE_OF)
    assert len(changes) == 4 and len(rebuild) == 2 and all("HFM" in line for line in rebuild)
    assert sum(op["type"] == "coaxStrip" for op in migrated["ops"]) == 2
    custom = _old_titles(FRESH["КБ1"])
    body = next(op for op in custom["ops"] if op["opKey"] == "coaxBodyA")
    body["label"] = "Корпус — по указанию технолога (А)"
    insert = next(op for op in custom["ops"] if op["opKey"] == "coaxInsertA")
    rebound = deepcopy(custom)
    next(op for op in rebound["ops"] if op["opKey"] == "coaxInsertA")["templateId"] = "INS_housing_COAX"
    migrated, changes, notes, _, _ = SCRIPT.migrate_config(custom, CODE_OF)
    assert len(changes) == 1 and changes[0].startswith("coaxInsertA") and "coaxBodyA" in notes[0]
    assert next(op for op in migrated["ops"] if op["opKey"] == "coaxBodyA") == body
    assert insert["label"] != next(op for op in migrated["ops"] if op["opKey"] == "coaxInsertA")["label"]
    assert SCRIPT.migrate_config(rebound, CODE_OF)[1] == []
    for change in ({"cableArt": "RG316"}, {"sideA": {**custom["coax"]["sideA"], "article": "59K13O-1M4A4-X"}}):
        foreign = {**custom, "coax": {**custom["coax"], **change}}
        assert SCRIPT.migrate_config(foreign, CODE_OF)[1] == []
    assert SCRIPT.migrate_config(LEGACY["КБ11"], CODE_OF)[1:] == ([], [], [], "другой кабель или нет артикула")


def test_command_line_plan_apply_and_rollback_files_round_trip(prod, tmp_path, monkeypatch, capsys):
    cards = _store_production(prod)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(app.database, "build_engine", lambda url: prod.kw["bind"])
    payload, plan, rollback = (str(DATA / "operation-templates-connectors-1118/payload.json"),
                               tmp_path / "plan.json", tmp_path / "inputs-rollback.json")
    before = _snapshot(prod)
    assert SCRIPT.main(["--templates", payload, "--plan-out", str(plan)]) == 0
    assert _snapshot(prod) == before and len(_json(plan)["cards"]) == 4
    with pytest.raises(SystemExit):
        SCRIPT.main(["--templates", payload, "--apply"])  # no reviewed plan, no rollback file
    assert SCRIPT.main(["--templates", payload, "--expect", str(plan), "--rollback-out", str(rollback), "--apply"]) == 0
    assert "COMMITTED" in capsys.readouterr().out
    assert {item["item_index"] for item in _json(rollback)["cards"]} == {"КБ1", "КБ2", "КБ3", "КБ28"}
    with pytest.raises(SystemExit):
        SCRIPT.main(["--expect", str(plan), "--rollback-out", str(rollback), "--apply"])  # never overwrites a rollback
    rollback_cards = str(DATA / "operation-templates-connectors-1118/rollback.json")
    assert SCRIPT.main(["--rollback", str(rollback), "--templates", rollback_cards, "--apply"]) == 0
    assert all(_row(prod, cards[kb]).inputs == cards[kb].inputs for kb in ("КБ1", "КБ2", "КБ3", "КБ28"))
