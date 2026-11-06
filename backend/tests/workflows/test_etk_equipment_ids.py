"""Оборудование ЭТК по ID (владелец 05.11.2026, migration 0171): строки «Оборудование» и станок резки коаксиала несут
`equipment_model_id`, название и инв. номер — по текущей записи модели в БД.ОБ; ID сам по себе ревизию не выпускает."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.modules.references.domain import TechOperationTab
from app.modules.references.equipment import INV_KEY, NAME_KEYS
from app.modules.references.infrastructure import EquipmentTemplateToolRow, SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.etk_builder import EQUIPMENT_ID, build_etk
from app.modules.techcards.infrastructure import ProjectTechcardRow, SqlAlchemyTechcardsUnitOfWork
from app.workflows.etk_refresh import IDENTITY_KEYS, run_refresh, without_identity
from app.workflows.techcard_build import enrich_equipment, load_refs, with_technology
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)
from tests.workflows.test_etk_refresh import (  # noqa: F401  (fixture `base`)
    CARDS, CONFIG, T1, _derive, _revisions, _row, _settle, _store, base,
)

PIN = "sdr-coax-pin-пайка-a"  # «T21(R) | Станция паяльная», ОБ.071.X — строка 2
SLEEVE = "prs-sleeve-coax-опрессовка"  # «SR-16M | Пресс пневматический…», ОБ.006.1 — строка 0
T21 = {"id": "m-t21", "article": "T21", "name": "Станция паяльная", "role": "operational",
       "display": "T21/Станция паяльная", "inventory_numbers": ("ОБ.071.1", "ОБ.071.2")}
BZW = {"id": "m-bzw", "article": "BZW-886+Q2", "name": "Станок резки провода с роторной зачисткой мал.",
       "role": "operational", "display": "BZW-886+Q2/Станок резки провода с роторной зачисткой мал.",
       "inventory_numbers": ("ОБ.094",)}
PIN_LINK = {"template_id": PIN, "position": 2, "tool_name": "T21(R) | Станция паяльная", "inventory_number": "ОБ.071.X",
            "model_id": "m-t21"}


def _tool(document, template_id: str, position: int = 0) -> dict:
    op = next(item for item in document["operations"] if item["template_id"] == template_id)
    return [tool for tool in op["card"]["tooling"] if tool["section"] == "Оборудование"][position]


def _machine_op(document) -> dict:
    return next(item for item in document["operations"] if item["type"] == "coaxCut")


def _build(models, links=()):
    return build_etk(enrich_equipment(CONFIG, models, links), CARDS)


# --- the builder: pure, models and links given ---

def test_a_linked_row_carries_the_model_id_and_keeps_the_template_text():
    plain = build_etk(CONFIG, CARDS)
    built = _build([T21, BZW], [PIN_LINK])

    tool = _tool(built, PIN)
    assert tool[EQUIPMENT_ID] == "m-t21"
    assert tool["name"] == "T21(R) | Станция паяльная" and tool["inventory_number"] == "ОБ.071.X"  # the model names it
    assert without_identity(built["operations"]) == without_identity(plain["operations"])  # only the ids were added
    assert EQUIPMENT_ID not in _tool(built, PIN, 1)  # «Приспособление зажимное» — no link


def test_a_renamed_model_shows_its_new_name_and_a_renumbered_one_its_unit():
    renamed = {**T21, "name": "Паяльная станция с двумя паяльниками", "inventory_numbers": ("ОБ.171.1", "ОБ.171.2")}

    tool = _tool(_build([renamed, BZW], [PIN_LINK]), PIN)

    assert tool[EQUIPMENT_ID] == "m-t21"
    assert tool["name"] == "T21 | Паяльная станция с двумя паяльниками"
    assert tool["inventory_number"] == "ОБ.171.1"


def test_a_new_article_shows_too_and_the_note_of_the_row_stays():
    link = {"template_id": "cut-coax-2strip-резка-с-разделкой", "position": 2,
            "tool_name": "BZW-886+Q2 | Станок резки провода с роторной зачисткой мал. — кабель Ø до 5,5 мм",
            "inventory_number": "ОБ.094", "model_id": "m-bzw"}
    config = deepcopy(CONFIG)
    for op in config["ops"]:
        op.pop("machine", None)  # the card's own equipment, no machine choice
    renamed = {**BZW, "article": "BZW-886Q3"}

    built = build_etk(enrich_equipment(config, [T21, renamed], [link]), CARDS)

    tool = _tool(built, "cut-coax-2strip-резка-с-разделкой")
    assert tool["name"] == "BZW-886Q3 | Станок резки провода с роторной зачисткой мал. — кабель Ø до 5,5 мм"
    assert tool[EQUIPMENT_ID] == "m-bzw"


def test_own_wording_linked_by_the_number_and_a_typo_are_no_rename():
    own = {**PIN_LINK, "tool_name": "PRS-MHF1/4-LK | Опрессовка MHF1 LK"}
    other = {**T21, "article": "SR-IPEX-L", "name": "Пресс пневматический для радиочастотных разъемов MHF"}
    typo = {**T21, "name": "Станция паяльна"}

    assert enrich_equipment(CONFIG, [other], [own])["ops"][2]["equipmentTools"][0]["name"] == own["tool_name"]
    assert _tool(_build([typo, BZW], [PIN_LINK]), PIN)["name"] == "T21(R) | Станция паяльная"


def test_a_template_edited_after_the_link_is_not_linked():
    stale = {**PIN_LINK, "tool_name": "T21(R) | Станция паяльная (старая)"}

    assert EQUIPMENT_ID not in _tool(_build([T21, BZW], [stale]), PIN)


def test_the_coax_machine_carries_its_model_and_its_current_name():
    built = _build([T21, BZW])
    machine = _machine_op(built)["fields"]["machine"]
    assert machine == {"model": "BZW-886+Q2", "inventory": "ОБ.094", "name": BZW["name"], EQUIPMENT_ID: "m-bzw"}
    assert _tool(built, "cut-coax-2strip-резка-с-разделкой")[EQUIPMENT_ID] == "m-bzw"

    model = {**BZW, "name": "Роторный станок малый", "inventory_numbers": ("ОБ.094.1",)}
    link = {"template_id": "cut-coax-2strip-резка-с-разделкой", "position": 2,
            "tool_name": "BZW-886+Q2 | Станок резки провода с роторной зачисткой мал. — кабель Ø до 5,5 мм",
            "inventory_number": "ОБ.094", "model_id": "m-bzw"}
    renamed = _build([T21, model], [link])
    machine = _machine_op(renamed)["fields"]["machine"]
    assert (machine["name"], machine["inventory"], machine[EQUIPMENT_ID]) == ("Роторный станок малый", "ОБ.094", "m-bzw")
    tool = _tool(renamed, "cut-coax-2strip-резка-с-разделкой")
    assert tool["name"] == "BZW-886+Q2 | Роторный станок малый — кабель Ø до 5,5 мм" and tool[EQUIPMENT_ID] == "m-bzw"
    assert len([item for item in _machine_op(renamed)["card"]["tooling"] if item["section"] == "Оборудование"]) == 1

    machine_only = _tool(_build([T21, model]), "cut-coax-2strip-резка-с-разделкой")  # the card's row is not linked
    assert machine_only[EQUIPMENT_ID] == "m-bzw" and machine_only["name"].startswith("BZW-886+Q2 | Станок резки")


def test_supporting_equipment_is_never_the_operation_machine():
    compressor = {**BZW, "role": "supporting"}

    built = _build([T21, compressor])

    op = _machine_op(built)
    assert "machine" not in op["fields"]
    assert all(EQUIPMENT_ID not in tool for tool in op["card"]["tooling"])
    assert op["card"]["tooling"] == build_etk({**CONFIG, "ops": [
        {key: value for key, value in item.items() if key != "machine"} for item in CONFIG["ops"]]}, CARDS)["operations"][0]["card"]["tooling"]


def test_enrichment_is_refreshing():
    once = enrich_equipment(CONFIG, [T21, BZW], [PIN_LINK])

    assert enrich_equipment(once, [T21, BZW], [PIN_LINK]) == once
    assert "equipmentTools" not in enrich_equipment(once, [T21, BZW], [])["ops"][2]  # a link gone — gone from the op


def test_identity_keys_are_only_the_model_id():
    assert IDENTITY_KEYS == frozenset({EQUIPMENT_ID})
    assert without_identity({"a": [{EQUIPMENT_ID: "x", "name": "n"}], EQUIPMENT_ID: "y"}) == {"a": [{"name": "n"}]}


# --- the refresh job over a stored ETK ---

def _model_of(sessions, inventory: str) -> str:
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        return next(item["id"] for item in uow.equipment.models() if inventory in item["inventory_numbers"])


@pytest.fixture()
def linked(base):
    t21, press = _model_of(base, "ОБ.071.1"), _model_of(base, "ОБ.006.1")
    with SqlAlchemyReferencesUnitOfWork(base) as uow:
        for template_id, position, name, number, model in (
            (PIN, 2, "T21(R) | Станция паяльная", "ОБ.071.X", t21),
            ("sdr-coax-pin-пайка-b", 2, "T21(R) | Станция паяльная", "ОБ.071.X", t21),
            (SLEEVE, 0, "SR-16M | Пресс пневматический для опрессовки кабельных контактов и наконечников", "ОБ.006.1", press),
        ):
            uow.session.add(EquipmentTemplateToolRow(template_id=template_id, position=position, tool_name=name,
                                                     inventory_number=number, candidates=1, model_id=UUID(model)))
        uow.commit()
    return base


def test_a_stored_etk_gets_no_revision_for_the_ids_alone(linked):
    derived = _derive(linked)
    assert _tool(derived, PIN)[EQUIPMENT_ID] and _tool(derived, SLEEVE)[EQUIPMENT_ID]
    assert _machine_op(derived)["fields"]["machine"][EQUIPMENT_ID]
    issued = without_identity(derived)  # the card as issued before the builder wrote the ids
    _store(linked, issued)

    report = run_refresh(linked, now=T1, everything=True)

    assert report.count("revised", "refreshed") == 0, report.text()
    assert [item.revision for item in _revisions(linked)] == [1]
    row = _row(linked)
    assert row.revision == 1 and row.document == issued


def test_a_renamed_model_issues_a_revision_with_the_new_name(linked):
    _store(linked, _derive(linked))
    _settle(linked)
    with SqlAlchemyReferencesUnitOfWork(linked) as uow:
        units = [record for record in uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=10_000)
                 if str(record.fields.get(INV_KEY) or "").startswith("ОБ.071.") and record.fields.get(INV_KEY) != "ОБ.071.X"]
        model = units[0].equipment_model_id
        for record in units:
            key = next(name for name in NAME_KEYS if name in record.fields)
            uow.tech_operations.save(replace(record, fields={**record.fields, key: "Паяльная станция цифровая"}))
        uow.commit()

    report = run_refresh(linked, now=T1)

    assert report.count("revised") == 1, report.text()
    new = _revisions(linked)[-1]
    assert "Инструмент" in {item["field"] for item in new.diff["changes"]}
    tool = _tool(new.document, PIN)
    assert tool["name"] == "T21 | Паяльная станция цифровая" and tool[EQUIPMENT_ID] == str(model)
    with SqlAlchemyTechcardsUnitOfWork(linked) as uow:
        assert uow.session.scalar(select(ProjectTechcardRow.revision)) == 2
        uow.commit()


def test_the_references_give_the_models_and_links_to_the_build(linked):
    refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(linked))
    assert {row["template_id"] for row in refs.template_tools} == {PIN, "sdr-coax-pin-пайка-b", SLEEVE}
    config, _ = with_technology(CONFIG, refs=refs)
    assert config["ops"][2]["equipmentTools"][0][EQUIPMENT_ID] == _model_of(linked, "ОБ.071.1")
