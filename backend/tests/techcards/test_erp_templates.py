"""Шаблоны карт поставки ERP (backend/data/techcard-library-erp.json) и первая загрузка базовой библиотеки."""

from datetime import datetime, timezone
import json
from pathlib import Path

from app.main import app
from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR, SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.seed_library import ACTOR, seed_base, seed_erp

from .library import load_library
from .test_operation_card_api import _template, operation_library_api  # noqa: F401

ERP_LIBRARY = json.loads((Path(__file__).resolve().parents[2] / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
TIN_ID = "cut-wire-1crimp-tin-автомат-резка-опрессовка-лужение"
COAX_CUT_ID = "cut-coax-auto-резка"
MARK_TUBE_ID = "ins-mark-tube-auto-печать-и-резка-маркировочной-трубки"
CUT_2STRIP_TIN_ID = "cut-wire-2strip-tin-резка-с-зачисткой-двух-концов-и-лужением"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _card(code):
    [card] = [card for card in parse_operation_library(ERP_LIBRARY["templates"]) if card.code == code]
    return card


def _seed(templates) -> dict[str, list[str]]:
    return seed_erp_templates(SeedErpTemplatesCommand(templates=tuple(templates), actor=ACTOR, now=NOW),
                              app.state.techcards_uow_factory())


def test_bundled_erp_template_is_the_cut_crimp_and_tin_card():
    card = _card("CUT_WIRE+1crimp+tin")
    assert (card.source_id, card.code) == (TIN_ID, "CUT_WIRE+1crimp+tin")
    assert [tool.inventory_number for tool in card.tooling if tool.section == "Оборудование"] == ["ОБ.098"]
    steps = " ".join(step.text for step in card.steps)
    assert "штангенциркул" in steps and "микрометр" in steps and "лужени" in steps


def test_bundled_coax_cut_card_speaks_of_coax_not_of_wire_twists():
    """Резка коаксиала без разделки (владелец 16.10): своя карта вместо CUT_WIRE_auto с «проводом» и «скруткой»."""
    card = _card("CUT_COAX_auto")
    assert (card.source_id, card.code, card.category) == (COAX_CUT_ID, "CUT_COAX_auto", "Резка")
    assert [(tool.name, tool.inventory_number) for tool in card.tooling if tool.section == "Оборудование"] == [
        ("EW-05F+M | Автомат мерной резки проводов", "ОБ.015")]
    text = " ".join([card.defects, *(step.text for step in card.steps)]).casefold()
    assert "коаксиальн" in text and "оплёт" in text and "диэлектрик" in text and "волновое сопротивление" in text
    assert "скрутк" not in text and "провод" not in text.replace("проводов", "")
    assert [step.kind for step in card.steps] == ["Операция"] * 3 + ["Контроль"] * 2 + ["Операция"] * 2 + ["Контроль"]


def test_bundled_mark_tube_card_is_found_by_its_code_and_names_the_printer():
    """Печать и резка маркировочной трубки (владелец 16.10): ОБ.102 ZCUT-RT660, строка БД.ОП INS_MARK_TUBE_AUTO уже есть."""
    card = _card("INS_MARK_TUBE_AUTO")
    assert (card.source_id, card.category) == (MARK_TUBE_ID, "Монтаж маркировки")
    assert [(tool.name, tool.inventory_number) for tool in card.tooling if tool.section == "Оборудование"] == [
        ("ZCUT-RT660 | Маркиратор проводов трубчатый", "ОБ.102")]
    assert {"Инструмент", "Расходники"} <= {tool.section for tool in card.tooling}
    text = " ".join(step.text for step in card.steps).casefold()
    assert "кассет" in text and "тестов" in text and "рассортировать по тексту" in text and "длин" in text
    assert [step.kind for step in card.steps] == ["Операция"] * 4 + ["Контроль"] * 2 + ["Операция"] * 2 + ["Контроль"]
    assert "читаем" in card.defects.casefold() and "нечитаем" in card.defects.casefold()


def test_bundled_cut_2strip_tin_card_cuts_strips_both_ends_and_tins_without_crimp():
    """Резка с зачисткой двух концов и лужением (владелец 16.10): ОБ.098 BZW-5.0+Z, обжима нет."""
    card = _card("CUT_WIRE+2strip+tin")
    assert (card.source_id, card.category) == (CUT_2STRIP_TIN_ID, "Автомат резка + опрессовка")
    assert [(tool.name, tool.inventory_number) for tool in card.tooling if tool.section == "Оборудование"] == [
        ("BZW-5.0+Z | Станок резки/ обжим+лужение", "ОБ.098")]
    assert {"Инструмент", "Расходники"} <= {tool.section for tool in card.tooling}
    text = " ".join([card.defects, *(step.text for step in card.steps)]).casefold()
    assert "лужени" in text and "2–3 мм" in text and "с обеих сторон" in text
    assert "микрометр" not in text and "pull-test" not in text and "терминал" not in text
    assert "Контроль" in {step.kind for step in card.steps}


def test_seed_creates_the_template_once_beside_the_base_library(operation_library_api):
    load_library(app.state.techcards_uow_factory, [_template("cut-wire", "CUT_WIRE")])
    assert "добавлены" in seed_erp(app.state.techcards_uow_factory, NOW)
    assert seed_erp(app.state.techcards_uow_factory, NOW) == "Шаблоны ERP: без изменений (6)"

    ids = {item["id"] for item in operation_library_api.get("/api/techcards/templates").json()["items"]}
    assert ids == {"cut-wire", TIN_ID, COAX_CUT_ID, MARK_TUBE_ID, CUT_2STRIP_TIN_ID,
                   "ins-housing-coax-clamp-монтаж-корпуса-с-прижимом-a",
                   "ins-housing-coax-clamp-монтаж-корпуса-с-прижимом-b"}
    detail = operation_library_api.get(f"/api/techcards/templates/{TIN_ID}").json()
    assert detail["code"] == "CUT_WIRE+1crimp+tin"


def test_seed_updates_its_own_template_but_keeps_one_a_person_saved(operation_library_api):
    first = _template("erp-card", "ERP_CARD")
    assert _seed([first])["created"] == ["ERP_CARD | Операция"]

    changed = _template("erp-card", "ERP_CARD")
    changed["rows"][10] = ["Операция", "1", "Новый шаг поставки", "", "", "", "КД"]
    assert _seed([changed])["updated"] == ["ERP_CARD | Операция"]
    with app.state.techcards_uow_factory() as uow:
        assert uow.operation_cards.sheet("erp-card").saved_by == ERP_TEMPLATE_AUTHOR

    draft = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"template_id": "erp-card"}).json()
    rows = changed["rows"]
    rows[10] = ["Операция", "1", "Правка технолога", "", "", "", "КД"]
    operation_library_api.put(f"/api/techcards/template-sheets/drafts/{draft['id']}", json={"rows": rows})
    saved = operation_library_api.post(f"/api/techcards/template-sheets/drafts/{draft['id']}/save",
                                       json={"title": "ERP_CARD | Операция"})
    assert saved.status_code == 200, saved.text

    assert _seed([changed])["kept"] == ["ERP_CARD | Операция"]
    detail = operation_library_api.get("/api/techcards/templates/erp-card").json()
    assert detail["document"]["steps"][0]["text"] == "Правка технолога"


def test_base_library_goes_only_into_an_empty_database(operation_library_api):
    """seed_library on a fresh base loads techcard-library.json; once the library has templates (edited in ERP), a
    rerun loads nothing and leaves them as they are."""
    base = json.loads((Path(__file__).resolve().parents[2] / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    factory = app.state.techcards_uow_factory
    with factory() as uow:
        assert uow.operation_cards.metadata() is None

    assert seed_base(factory, NOW) == f"Библиотека операций: загружено шаблонов {len(base['templates'])}"
    assert set(base) == {"templates"}
    with factory() as uow:
        assert uow.operation_cards.metadata() == {"template_count": len(base["templates"])}
        assert not any(row.saved_at for row in uow.operation_cards.sheets())
    first = base["templates"][0]["id"]
    assert operation_library_api.put(f"/api/techcards/templates/{first}", json={
        "name": "Правка в ERP", "safety": "", "defects": "", "revision": "",
        "steps": [{"kind": "Операция", "text": "Свой шаг", "tolerance": ""}], "tooling": [],
    }).status_code == 200

    assert seed_base(factory, NOW) == "Библиотека операций уже загружена"
    assert operation_library_api.get(f"/api/techcards/templates/{first}").json()["document"]["steps"][0]["text"] == "Свой шаг"


def test_base_library_is_not_loaded_over_templates_made_in_erp(operation_library_api):
    seed_erp(app.state.techcards_uow_factory, NOW)
    assert seed_base(app.state.techcards_uow_factory, NOW) == "Библиотека операций уже загружена"
    with app.state.techcards_uow_factory() as uow:
        assert uow.operation_cards.metadata() == {"template_count": len(ERP_LIBRARY["templates"])}
