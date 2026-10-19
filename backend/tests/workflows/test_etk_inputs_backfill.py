"""Заполнение inputs/overrides у ЭТК, собранных до автоматических ревизий (владелец 19.10.2026): классы карт,
правки руками, ручная схема ПФ, проверка без записи, `--apply` пишет две колонки и ни одной ревизии, повтор — ничего."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.references.cable_structure import HookupWire, WireGauge
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.technology import TerminalRecord
from app.modules.techcards.application import (
    AutoBuildProjectTechcardCommand, ChangeProjectTechcardCommand, CreateProjectTechcardCommand,
    EditTechcardOperationCommand, issue_project_techcard,
    SaveTechcardRouteCommand, auto_build_project_techcard, create_project_techcard, edit_project_operation,
    save_project_route,
)
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import operation_key
from app.shared.clock import MOSCOW
from app.shared.persistence import create_schema
from app.workflows import etk_inputs_backfill as backfill
from app.workflows.etk_inputs_backfill import DRIFT, FAILED, IDENTICAL, NO_INPUTS, OVERRIDES, run, strip_enriched
from app.workflows.techcard_build import (
    Refs, enrich_coax_depth, enrich_coax_diameters, enrich_coax_hex, enrich_linear_timing, enrich_marking,
    enrich_terminal_norms, enrich_wire_diameters, load_refs, with_technology,
)
from tests.factories import Actor

BACKEND = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((BACKEND / "data" / "techcard-library.json").read_text(encoding="utf-8"))
ERP_LIBRARY = json.loads((BACKEND / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"] + ERP_LIBRARY["templates"])
CONFIG_713 = json.loads((BACKEND / "tests" / "techcards" / "generator_713k4_config.json").read_text(encoding="utf-8"))
CONFIG_COAX = json.loads((BACKEND.parent / "frontend" / "src" / "fixtures" / "coaxSmaRg316Rev1.json")
                         .read_text(encoding="utf-8"))["document"]["build_config"]
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=MOSCOW)
ACTOR = Actor(name="Технолог", department="Цех")


def full_refs(base: Refs) -> Refs:
    """References with a technology snapshot, the cut speeds and the coax tables, so every enricher has something to write."""
    rows = tuple({"opNumber": code, "Номер": code, "Тип операции": "Погонный", "Скорость проката, сек/м": "3",
                  "Скорость работы инструмента": "2,5", "Кол-во операций инструмента": "3"}
                 for code in ("CUT_COAX+2strip", "CUT_TUT_short", "CUT_WIRE+1crimp"))
    coax = tuple({"coaxArticle": article, "coaxWire": "RG-316", "HEX обжима, мм": "3,25", "Заглубление контакта, мм": "0,5"}
                 for article in ("SMA-C174P", "SMA-C316F"))
    return replace(base, snapshot={"schema_version": 1, "source_updated_at": None, "imported_at": "2026-10-19T12:00:00"},
                   operations=rows, coax_rows=coax, dia_rows=({"Кабель": "RG316", "D1": "0,5", "D2": "1,48", "D3": "1,95"},))


class World:
    def __init__(self, tmp_path):
        self.engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'erp.db'}")
        create_schema(self.engine)
        sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.techcards = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
        self.references = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
        with self.techcards() as uow:
            uow.operation_cards.replace(tuple(CARDS), source_commit=LIBRARY["sourceCommit"], imported_at=NOW)
            uow.commit()
        self.refs = full_refs(load_refs(self.references))
        self.minutes = 0

    def change(self, card):
        self.minutes += 1
        return dict(card_id=card.id, expected_version=card.version, actor=ACTOR,
                    now=NOW.replace(hour=12 + self.minutes // 60, minute=self.minutes % 60))

    def old_card(self, project, config):
        """A card as it was built before 0127: the document with an enriched `build_config`, no inputs."""
        card = create_project_techcard(CreateProjectTechcardCommand(
            project=project, item_index="1", product_name="Жгут", source_calculation="", deadline=None,
            actor=ACTOR, now=NOW), self.techcards())
        enriched, stamp = with_technology(config, refs=self.refs)
        return auto_build_project_techcard(AutoBuildProjectTechcardCommand(
            config=enriched, technology_source=stamp, inputs=None, **self.change(card)), self.techcards())

    def get(self, card):
        with self.techcards() as uow:
            return uow.techcards.get(card.id)

    def patch(self, card, **fields):
        with self.techcards() as uow:
            uow.techcards.save(replace(uow.techcards.get(card.id), **fields))
            uow.commit()

    def run(self, **options):
        return run(self.techcards, self.references, refs=self.refs, **options)

    def counts(self):
        with self.engine.connect() as connection:
            return tuple(connection.execute(text(f"select count(*) from {name}")).scalar()
                         for name in ("techcard_projects", "techcard_revisions"))


@pytest.fixture()
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.engine.dispose()


def verdicts(report):
    return {name: count for name, count in report.counts.items() if count}


def test_713k4_and_228k20_classify_as_identical(world):
    world.old_card("713К.4", CONFIG_713)
    world.old_card("228К.20", strip_enriched(CONFIG_COAX))
    report = world.run()
    assert verdicts(report) == {IDENTICAL: 2}, report.text(False)


def test_a_hand_edited_operation_becomes_a_whole_operation_override(world):
    card = world.old_card("713К.4", CONFIG_713)
    card = issue_project_techcard(ChangeProjectTechcardCommand(**world.change(card)), world.techcards())
    target = card.document["operations"][1]
    key = operation_key(target)
    edited = {**target, "card": {**target["card"], "safety": "Перчатки"}}
    saved = edit_project_operation(EditTechcardOperationCommand(
        operation_key=key, operation=edited, **world.change(card)), world.techcards())
    world.patch(saved, inputs=None, overrides=None)  # the card the way it was before the override columns existed
    report = world.run(apply=True)
    assert verdicts(report) == {OVERRIDES: 1}, report.text(True)
    stored = world.get(saved)
    assert list(stored.overrides["operations"]) == [key]
    assert "Перчатки" in stored.overrides["operations"][key]["operation"]["card"]["safety"]
    assert "route" not in stored.overrides and stored.inputs == strip_enriched(stored.document["build_config"])


def test_a_manual_route_becomes_a_route_override(world):
    card = world.old_card("713К.4", CONFIG_713)
    saved = save_project_route(SaveTechcardRouteCommand(route=deepcopy(card.document["route"]), **world.change(card)),
                               world.techcards())
    assert saved.document["route"]["source"] == "manual"
    world.patch(saved, inputs=None, overrides=None)
    report = world.run(apply=True)
    assert verdicts(report) == {OVERRIDES: 1}, report.text(True)
    overrides = world.get(saved).overrides
    assert overrides["route"]["source"] == "manual" and overrides["route"]["blocks"]
    assert "operations" not in overrides


def test_a_card_that_differs_even_after_overrides_is_drift(world):
    card = world.old_card("713К.4", CONFIG_713)
    document = deepcopy(card.document)
    document["operations"][0]["result"] = [{"name": "Кто-то поправил сборку", "qty": 1}]
    world.patch(card, document=document)
    report = world.run()
    assert verdicts(report) == {DRIFT: 1}
    assert "расхождений" in report.lines[0] and "Результат" in report.lines[0]


def test_no_build_config_and_a_failed_derive(world):
    create_project_techcard(CreateProjectTechcardCommand(
        project="П-0", item_index="1", product_name="Без сборки", source_calculation="", deadline=None,
        actor=ACTOR, now=NOW), world.techcards())
    broken = world.old_card("П-1", CONFIG_713)
    world.patch(broken, document={**broken.document, "build_config": {"mode": "wire", "ops": "x"}})
    assert verdicts(world.run()) == {NO_INPUTS: 1, FAILED: 1}


def test_the_dry_run_writes_nothing(world):
    card = world.old_card("713К.4", CONFIG_713)
    world.old_card("228К.20", strip_enriched(CONFIG_COAX))
    before = world.counts()
    report = world.run()
    assert world.counts() == before
    assert report.before == report.after == {"techcard_projects": 2, "techcard_revisions": 0}
    stored = world.get(card)
    assert stored.inputs is None and stored.overrides is None and stored.version == card.version
    assert "будет записано" in report.lines[0] and "Проверка без записи" in report.text(False)


def test_apply_writes_only_the_two_columns_and_no_revisions(world):
    card = world.old_card("713К.4", CONFIG_713)
    world.old_card("228К.20", strip_enriched(CONFIG_COAX))
    before = world.counts()
    card = world.get(card)  # as the database returns it
    report = world.run(apply=True)
    assert report.written == 2 and report.code == 0
    assert world.counts() == before
    after = world.get(card)
    assert after.inputs == strip_enriched(card.document["build_config"]) and after.overrides is None
    for name in ("version", "revision", "document", "status", "updated_at", "manual_operation_keys", "review_note",
                 "derived_hash"):
        assert getattr(after, name) == getattr(card, name), name


def test_a_rerun_is_a_no_op(world):
    world.old_card("713К.4", CONFIG_713)
    world.old_card("228К.20", strip_enriched(CONFIG_COAX))
    world.run(apply=True)
    query = text("select id, version, inputs, overrides, updated_at from techcard_projects order by id")
    with world.engine.connect() as connection:
        snapshot = connection.execute(query).all()
    again = world.run(apply=True)
    assert again.written == 0 and again.skipped == 2 and verdicts(again) == {IDENTICAL: 2}
    with world.engine.connect() as connection:
        assert connection.execute(query).all() == snapshot


def test_a_card_changed_during_the_run_is_not_overwritten(world):
    card = world.old_card("713K.4", CONFIG_713)
    result = backfill.classify(card, lambda config: with_technology(config, refs=world.refs)[0], CARDS,
                               LIBRARY["sourceCommit"], None)
    auto_build_project_techcard(AutoBuildProjectTechcardCommand(
        config=card.document["build_config"], inputs=None, **world.change(card)), world.techcards())
    assert backfill._store(world.techcards, result) is False
    assert world.get(card).inputs is None


def test_strip_gives_back_the_raw_config_the_enrichers_started_from():
    raw = {
        "wires": [{"name": "UL1007 24AWG", "art": "UL1007 24AWG", "qty": 2, "length": 100}],
        "ops": [{"type": "cutWireCrimp", "opNum": "CUT", "wires": [{"name": "UL1007 24AWG", "art": "UL1007 24AWG"}],
                 "ep": {"termArt": "SXH-001T-P0.6N"}},
                {"type": "insMark", "opNum": "MARK", "wires": [], "marks": [{"text": "W1", "target": "Провод W1"}]}],
        "coax": {"cableArt": "RG-316", "sideA": {"article": "S-111F", "wire": "RG-316"}, "sideB": {"article": "S-111F"}},
    }
    gauge = WireGauge.create(awg="24", section_mm2="0.2", conductor_d_mm="0.51")
    hookup = HookupWire.create(mark="UL1007 24AWG", series="UL1007", gauge_id=gauge.id, outer_d_mm="1.4")
    row = {"opNumber": "CUT", "Номер": "CUT", "tOp": "4", "tPrep": "5", "Тип операции": "Погонный",
           "Скорость проката, сек/м": "3", "Скорость работы инструмента": "0,2", "Кол-во операций инструмента": "5"}
    coax = {"coaxArticle": "S-111F", "coaxWire": "RG-316", "HEX обжима, мм": "5,41", "Заглубление контакта, мм": "0,5"}
    record = TerminalRecord(article="SXH-001T-P0.6N", applicator="S5", crimp_height="0,60 - 0,75", awg_from="28", awg_to="22")
    enriched = enrich_terminal_norms(raw, [record], {})
    enriched = enrich_marking(enriched, [("Принтер трубок", {"Инв. номер": "1"})], [row])
    enriched = enrich_linear_timing(enriched, [row])
    enriched = enrich_wire_diameters(enriched, [hookup], [gauge])
    enriched = enrich_coax_hex(enriched, [coax])
    enriched = enrich_coax_depth(enriched, [coax])
    enriched = enrich_coax_diameters(enriched, [{"Кабель": "RG-316", "D1": "0,51", "D2": "1,52", "D3": "2,5"}])
    assert enriched != raw and "refOwned" in enriched
    assert strip_enriched(enriched) == raw
    assert strip_enriched(raw) == raw
