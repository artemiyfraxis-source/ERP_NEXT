"""The marking payload and ERP seed have identical stable, documented cards."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.modules.techcards.operation_cards import normalize_record_edit, parse_operation_library
from app.modules.techcards.template_sheets import make_template_id


ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs/data/operation-templates-marking-1116"
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
LIBRARY = json.loads((ROOT / "backend/data/techcard-library-erp.json").read_text(encoding="utf-8"))
CARDS = {card.code: card for card in parse_operation_library(LIBRARY["templates"])}
NAMES = {
    "PRINT_MARK_LABEL": "Печать этикеток маркировки",
    "INS_MARK_LABEL": "Наклейка этикеток маркировки",
    "MARK_CONN_PAINT": "Цветовая маркировка разъёмов маркером-краской",
    "MARK_CONN_LASER": "Цветовая маркировка разъёмов лазерной гравировкой",
    "MARK_CONN_TAPE": "Цветовая маркировка разъёмов цветной лентой",
}
PREP_CODES = {"INS_FAKRA_ST_PREP", "INS_FAKRA_RA_PREP"}
NEUTRAL_LINE = ("Если кабель уже разделан при резке, надеть предварительные детали, не нарушая оплётку "
                "и не повреждая уплотнение; сохранить ориентацию деталей.")


# The two FAKRA PREP cards were superseded by the short connector cards of 18.11 (test_fakra_library).
@pytest.mark.parametrize("item", [item for item in PAYLOAD if item["code"] in NAMES], ids=lambda item: item["code"])
def test_payload_matches_the_seed_card_and_has_stable_identity(item):
    edit = normalize_record_edit(item["record"])
    card = CARDS[item["code"]]
    assert card.source_id == make_template_id(f"{item['code']} | {edit.name}", ())
    assert (card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) == (
        edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling,
    )
    assert card.steps[0].text.startswith("Вход: ")
    assert card.steps[-1].text.startswith("Результат: ")
    assert {"Инструмент", "Оборудование", "Расходники"} <= {tool.section for tool in card.tooling}
    assert all(step.tolerance for step in card.steps)
    assert set(item["create"]["row"]["norms"].values()) == {""}
    assert item["metadata"]["production_time"] == "не задано"


def test_exact_russian_names_equipment_and_defect_sources():
    assert {item["code"] for item in PAYLOAD} == NAMES.keys() | PREP_CODES
    assert len({template["id"] for template in LIBRARY["templates"]}) == len(LIBRARY["templates"])
    codes = [card.code for card in parse_operation_library(LIBRARY["templates"])]
    assert all(codes.count(code) == 1 for code in NAMES)
    for code, name in NAMES.items():
        card = CARDS[code]
        assert card.name == name
        assert "ИОТ" in card.safety
        assert "СТО ФРАК-001-2026, раздел 4.6 «Маркировка»" in card.defects
    assert {code: [(tool.name, tool.inventory_number) for tool in CARDS[code].tooling
                   if tool.inventory_number] for code in NAMES} == {
        "PRINT_MARK_LABEL": [("ZCUT-RT660 | Маркиратор проводов трубчатый", "ОБ.102")],
        "INS_MARK_LABEL": [], "MARK_CONN_PAINT": [],
        "MARK_CONN_LASER": [("HRSU-400LL | Лазерный маркератор", "ОБ.103")],
        "MARK_CONN_TAPE": [],
    }


@pytest.mark.parametrize("code", sorted(PREP_CODES))
def test_prep_only_gains_one_neutral_line_and_keeps_the_historical_payload(code):
    old = next(item for item in json.loads((ROOT / "docs/data/operation-templates-fakra-1114/payload.json")
                                          .read_text(encoding="utf-8")) if item["code"] == code)
    updated = deepcopy(next(item for item in PAYLOAD if item["code"] == code))
    changed = [step for step in updated["record"]["steps"] if NEUTRAL_LINE in step["text"]]
    assert len(changed) == 1
    changed[0]["text"] = changed[0]["text"].removesuffix("\n" + NEUTRAL_LINE)
    assert updated == old


def test_label_mounting_is_separate_from_shrinking_and_laser_does_not_claim_colour():
    def text(code):
        return " ".join(step.text for step in CARDS[code].steps)

    assert "Vell VL-S641" in text("PRINT_MARK_LABEL")
    install = text("INS_MARK_LABEL")
    assert all(word in install for word in ("в середине", "FROM", "TO", "расстояние ≥", "без складок"))
    assert "Усадка — отдельная операция" in install
    assert "до касания" in text("MARK_CONN_PAINT") and "протирании" in text("MARK_CONN_PAINT")
    laser = text("MARK_CONN_LASER")
    assert "не обеспечивает заданный цвет" in laser
    assert "параметры — не задано" in laser
    assert "операцию не выполнять" in laser


def test_marking_artifacts_have_lf_line_endings():
    for path in (FOLDER / "payload.json", FOLDER / "apply_templates.py"):
        assert b"\r\n" not in path.read_bytes()
