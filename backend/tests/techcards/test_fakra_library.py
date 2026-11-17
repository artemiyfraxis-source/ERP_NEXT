"""FAKRA/HFM stages ship the same execution semantics through seed and guarded payload paths.

Connector cards follow the short canonical form of «708К — свод инструкций к разъёмам» (owner 17.11.2026):
3–8 one-sentence steps, numbers only in the tolerance field, exactly one control step, no «Вход:/Результат:»
boilerplate. Legacy STRIP_* cards keep their 14.11 form for routes that still strip separately.
"""

import json
from pathlib import Path
import re

import pytest

from app.modules.techcards.operation_cards import normalize_record_edit, parse_operation_library
from app.modules.techcards.template_sheets import make_template_id


ROOT = Path(__file__).resolve().parents[3]
SEED_PATH = ROOT / "backend/data/techcard-library-erp.json"
LEGACY_PATH = ROOT / "docs/data/operation-templates-fakra-1114/payload.json"
SHORT_FOLDER = ROOT / "docs/data/operation-templates-connectors-1118"
SHORT = {item["code"]: item for item in json.loads((SHORT_FOLDER / "payload.json").read_text(encoding="utf-8"))}
ROLLBACK = {item["code"]: item for item in json.loads((SHORT_FOLDER / "rollback.json").read_text(encoding="utf-8"))}
LEGACY = {item["code"]: item for item in json.loads(LEGACY_PATH.read_text(encoding="utf-8")) if item["code"] not in SHORT}
ITEMS = {**LEGACY, **SHORT}
CARDS = {card.code: card for card in parse_operation_library(
    json.loads(SEED_PATH.read_text(encoding="utf-8"))["templates"]
) if card.code in ITEMS}
STRIP = ("STRIP_FAKRA_ST", "STRIP_FAKRA_RA", "STRIP_HFM", "STRIP_FAKRA_BH")
SHORT_CODES = (
    "INS_FAKRA_ST_PREP", "PRS_FAKRA_ST_PIN", "INS_FAKRA_ST_CONTACT", "PRS_FAKRA_ST_SHIELD",
    "INS_FAKRA_ST_HOUSING", "INS_FAKRA_ST_SEAL",
    "INS_FAKRA_RA_PREP", "PRS_FAKRA_RA_PIN", "INS_FAKRA_RA_CONTACT", "PRS_FAKRA_RA_SHIELD",
    "PRS_FAKRA_RA_CAP", "INS_FAKRA_RA_HOUSING",
    "INS_HFM_PREP", "PRS_HFM_PIN", "INS_HFM_CONTACT", "PRS_HFM_SHIELD", "INS_HFM_HOUSING",
    "INS_FAKRA_BH_PREP", "SDR_FAKRA_BH_PIN", "INS_FAKRA_BH_CONTACT", "PRS_FAKRA_BH_SHIELD", "HEAT_FAKRA_BH_TTK",
    "PRS_HFM_SUPPORT",
)


def _text(code):
    return " ".join(step.text for step in CARDS[code].steps)


def _tolerances(code):
    return " ".join(step.tolerance for step in CARDS[code].steps)


def test_every_connector_stage_has_one_seed_card_and_one_delivery():
    assert set(SHORT) == set(SHORT_CODES) and set(LEGACY) == set(STRIP)
    assert set(CARDS) == set(SHORT_CODES) | set(STRIP)
    assert len({tuple((s.kind, s.text) for s in c.steps) for c in CARDS.values()}) == len(CARDS)


@pytest.mark.parametrize("code", (*SHORT_CODES, *STRIP))
def test_seed_and_apply_payload_render_the_same_stage(code):
    item, card = ITEMS[code], CARDS[code]
    edit = normalize_record_edit(item["record"])
    assert card.title == f"{code} | {edit.name}"
    assert card.source_id == make_template_id(card.title, ())
    assert (card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) == (
        edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling,
    )
    assert all(step.tolerance for step in card.steps)
    assert set(item["create"]["row"]["norms"].values()) == {""}
    assert item["metadata"]["production_time"] == "не задано"


@pytest.mark.parametrize("code", SHORT_CODES)
def test_short_canonical_form(code):
    card = CARDS[code]
    assert 3 <= len(card.steps) <= 8
    assert [step.kind for step in card.steps].count("Контроль") == 1 and card.steps[-1].kind == "Контроль"
    for step in card.steps:
        assert not step.text.startswith(("Вход", "Результат")), step.text
        assert "Норма времени" not in step.text and "по КД" not in step.tolerance
        assert not re.search(r"MA_59V0|MA_AMV|https?://|Источник", step.text), step.text
        # Numbers belong to the tolerance field; a step sentence carries no criterion values (tool identities aside).
        assert not re.search(r"\d+,\d+\s*(?:±|мм)", step.text), step.text
        assert step.text.count(". ") == 0, step.text  # one sentence
    assert not any(tool.name == "Расходные материалы — не задано" for tool in card.tooling)
    assert SHORT[code]["expected_before"], "guarded delivery needs the earlier content hashes"
    assert ROLLBACK[code]["expected_before"] and ROLLBACK[code]["record"]["name"] == card.name


def test_every_numeric_criterion_of_the_earlier_cards_survives():
    expected = {
        "PRS_FAKRA_ST_PIN": ("cbm = 1,34 ± 0,05 мм", "ch = 0,88 ± 0,03 мм", "> 60 Н"),
        "INS_FAKRA_ST_CONTACT": ("0,18…0,68 мм", "≤ 5 Н", "≤ 20 Н", "≥ 10 Н"),
        "PRS_FAKRA_ST_SHIELD": ("0,1 ± 0,1 мм", "C–C = 4,30 ± 0,05 мм", "> 110 Н"),
        "PRS_FAKRA_RA_PIN": ("≤ 0,3 мм", "1,10 −0,03 мм"),
        "PRS_FAKRA_RA_SHIELD": ("0,1 ± 0,1 мм", "4,30 −0,05 мм"),
        "PRS_FAKRA_BH_SHIELD": (".178″ = 4,5212 мм",),
        "HEAT_FAKRA_BH_TTK": ("150 °C", "≤ 1,5 мм"),
        "PRS_HFM_PIN": ("1,28 ± 0,05 мм", "0,90 ± 0,03 мм", "15,9 ± 0,2 мм", "≤ 0,6 мм", "≤ 1,5°", "> 60 Н"),
        "INS_HFM_CONTACT": ("1,05 ± 0,15 мм",),
        "PRS_HFM_SHIELD": ("k = 3,70 ± 0,05 × 3,65 ± 0,05 мм", "t = 3,45 ± 0,05 × 3,35 ± 0,05 мм",
                           "l = 3,80 ± 0,05 × 3,80 ± 0,05 мм", "n ≥ 9,9 мм", "Ø ≤ 3,60 мм", "> 120 Н"),
        "INS_HFM_HOUSING": ("2,05 +0,15/−0,10 мм",),
    }
    for code, values in expected.items():
        for value in values:
            assert value in _tolerances(code), (code, value)
    assert "Ø 3,54" in _text("PRS_HFM_SHIELD")


def test_wrong_texts_from_the_audit_are_fixed():
    assert "утопание = 0,18…0,68 мм" in _tolerances("INS_FAKRA_ST_CONTACT")
    assert "щелч" not in _text("INS_HFM_CONTACT")  # the click belongs to the plastic QUAD only
    assert "щелч" in _text("INS_HFM_HOUSING")
    assert all("CPA" not in _text(code) for code in SHORT_CODES)  # CPA order is not defined
    assert SHORT["INS_HFM_HOUSING"]["metadata"]["cpa_sequence"] == "не задано"
    assert all("STRIP_" not in item["metadata"]["input"] for item in SHORT.values())


def test_unresolved_blocks_stay_visible():
    for code in ("INS_FAKRA_ST_HOUSING", "INS_FAKRA_ST_SEAL"):
        assert "MA_59V111" in _text(code) and "операцию не выполнять" in _text(code)
        assert SHORT[code]["metadata"]["assembly_status"] == "Заблокировано до получения ДИ"
    assert "к этому герметичному корпусу не применять" in _text("INS_FAKRA_ST_HOUSING")
    assert "предел утечки — не задано" in _tolerances("INS_FAKRA_ST_SEAL")
    assert "MA_59V111" in _tolerances("INS_FAKRA_ST_PREP")
    assert "RTK 031" in _tolerances("INS_FAKRA_RA_PREP")
    assert "не допуск обжима" in _tolerances("PRS_FAKRA_RA_PIN")
    assert "допуск готового обжима и усилие отрыва — не задано" in _tolerances("PRS_FAKRA_RA_SHIELD")
    assert "усилие и ход пресса — не задано" in _tolerances("PRS_FAKRA_RA_CAP")
    assert "Tпайки и tпайки — не задано" in _tolerances("SDR_FAKRA_BH_PIN") and "не паять" in _text("SDR_FAKRA_BH_PIN")
    assert "испытание герметичности — не задано" in _tolerances("HEAT_FAKRA_BH_TTK")
    assert "процессный размер p — не задано" in _tolerances("PRS_HFM_PIN")
    support = CARDS["PRS_HFM_SUPPORT"]
    assert "полностью разделанный" in _text("PRS_HFM_SUPPORT") and "операцию не выполнять" in _text("PRS_HFM_SUPPORT")
    assert "высота обжима и усилие — не задано" in _tolerances("PRS_HFM_SUPPORT")
    assert any("11WS001_A" in tool.name and "в цеху нет" in tool.name for tool in support.tooling)


def test_only_confirmed_concrete_inventory_units_are_bound():
    bindings = {code: {t.inventory_number for t in card.tooling if t.inventory_number}
                for code, card in CARDS.items()}
    assert {code: units for code, units in bindings.items() if units} == {
        "STRIP_FAKRA_ST": {"ОБ.010"}, "STRIP_FAKRA_RA": {"ОБ.010"}, "STRIP_HFM": {"ОБ.010"},
        "STRIP_FAKRA_BH": {"ОБ.010"}, "SDR_FAKRA_BH_PIN": {"ОБ.071.1"},
        "HEAT_FAKRA_BH_TTK": {"ОБ.070.1"},
    }
    for code in ("PRS_FAKRA_ST_PIN", "PRS_FAKRA_ST_SHIELD", "PRS_FAKRA_RA_PIN",
                 "PRS_FAKRA_RA_SHIELD", "PRS_FAKRA_RA_CAP", "STRIP_HFM", "PRS_HFM_PIN",
                 "PRS_HFM_SHIELD", "PRS_FAKRA_BH_SHIELD", "PRS_HFM_SUPPORT"):
        assert any("требуется оснастка производителя — в цеху нет" in t.name for t in CARDS[code].tooling)
    assert any("Припой и флюс" in t.name for t in CARDS["SDR_FAKRA_BH_PIN"].tooling)
    assert any("Клеевая ТУТ" in t.name for t in CARDS["HEAT_FAKRA_BH_TTK"].tooling)


def test_legacy_hfm_strip_route_keeps_support_sleeve_before_final_strip():
    assert "втулка не обжата" in _text("INS_HFM_PREP")
    actions = [s.text for s in CARDS["STRIP_HFM"].steps if s.kind == "Операция"]
    partial = next(i for i, text in enumerate(actions) if "удалить оболочку частично" in text)
    sleeve = next(i for i, text in enumerate(actions) if "установить и обжать опорную втулку" in text)
    final_strip = next(i for i, text in enumerate(actions) if "Снять оставленную часть оболочки" in text)
    assert partial < sleeve < final_strip
    assert "четыре контакта" in _text("INS_HFM_HOUSING")


def test_routine_retention_is_separate_from_destructive_qualification():
    [routine] = [s for s in CARDS["INS_FAKRA_ST_CONTACT"].steps if "сборочная проверка" in s.text]
    assert routine.kind == "Контроль" and "усилие сборочной проверки ≤ 5 Н" in routine.tolerance
    for code, limit in (("PRS_FAKRA_ST_PIN", "60"), ("PRS_FAKRA_ST_SHIELD", "110"),
                        ("PRS_HFM_PIN", "60"), ("PRS_HFM_SHIELD", "120")):
        [pull] = [s for s in CARDS[code].steps if s.tolerance == f"усилие отрыва > {limit} Н"]
        assert "разрушающ" in pull.text.lower() and "образц" in pull.text and pull.kind == "Операция"


def test_payload_and_seed_use_lf_bytes():
    for path in (SEED_PATH, LEGACY_PATH, SHORT_FOLDER / "payload.json", SHORT_FOLDER / "rollback.json",
                 SHORT_FOLDER / "apply_templates.py"):
        assert b"\r\n" not in path.read_bytes(), path
