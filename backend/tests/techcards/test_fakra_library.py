"""FAKRA stages ship the same execution semantics through seed and guarded payload paths."""

import json
from pathlib import Path

import pytest

from app.modules.techcards.operation_cards import normalize_record_edit, parse_operation_library
from app.modules.techcards.template_sheets import make_template_id


ROOT = Path(__file__).resolve().parents[3]
PAYLOAD_PATH = ROOT / "docs/data/operation-templates-fakra-1114/payload.json"
SEED_PATH = ROOT / "backend/data/techcard-library-erp.json"
PAYLOAD = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
ITEMS = {item["code"]: item for item in PAYLOAD}
CARDS = {card.code: card for card in parse_operation_library(
    json.loads(SEED_PATH.read_text(encoding="utf-8"))["templates"]
) if card.code in ITEMS}
EXPECTED = (
    "INS_FAKRA_ST_PREP", "STRIP_FAKRA_ST", "PRS_FAKRA_ST_PIN", "INS_FAKRA_ST_CONTACT",
    "PRS_FAKRA_ST_SHIELD", "INS_FAKRA_ST_HOUSING", "INS_FAKRA_ST_SEAL",
    "INS_FAKRA_RA_PREP", "STRIP_FAKRA_RA", "PRS_FAKRA_RA_PIN", "INS_FAKRA_RA_CONTACT",
    "PRS_FAKRA_RA_SHIELD", "PRS_FAKRA_RA_CAP", "INS_FAKRA_RA_HOUSING",
    "INS_HFM_PREP", "STRIP_HFM", "PRS_HFM_PIN", "INS_HFM_CONTACT", "PRS_HFM_SHIELD", "INS_HFM_HOUSING",
    "INS_FAKRA_BH_PREP", "STRIP_FAKRA_BH", "SDR_FAKRA_BH_PIN", "INS_FAKRA_BH_CONTACT",
    "PRS_FAKRA_BH_SHIELD", "HEAT_FAKRA_BH_TTK",
)


def _text(code):
    return " ".join(step.text for step in CARDS[code].steps)


def test_fakra_stages_have_distinct_codes_and_no_duplicate_seed_entries():
    assert tuple(ITEMS) == EXPECTED
    assert set(CARDS) == set(EXPECTED)
    assert len({tuple((s.kind, s.text) for s in c.steps) for c in CARDS.values()}) == 26


@pytest.mark.parametrize("code", EXPECTED)
def test_seed_and_apply_payload_render_the_same_stage(code):
    item, card = ITEMS[code], CARDS[code]
    edit = normalize_record_edit(item["record"])
    assert card.title == f"{code} | {edit.name}"
    assert card.source_id == make_template_id(card.title, ())
    assert (card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) == (
        edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling,
    )
    assert card.steps[0].text.startswith("Вход: ")
    assert card.steps[-1].text.startswith("Результат: ")
    assert {"Инструмент", "Оборудование", "Расходники"} <= {t.section for t in card.tooling}
    assert all(step.tolerance for step in card.steps)
    assert item["metadata"]["sources"]
    assert set(item["create"]["row"]["norms"].values()) == {""}
    assert item["metadata"]["production_time"] == "не задано"
    assert "Норма времени и расход на настройку — не задано" in card.steps[-1].text


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
                 "PRS_HFM_SHIELD", "PRS_FAKRA_BH_SHIELD"):
        assert any("требуется оснастка производителя — в цеху нет" in t.name for t in CARDS[code].tooling)


def test_hfm_support_sleeve_is_placed_and_crimped_after_partial_stripping():
    prep = _text("INS_HFM_PREP")
    assert "втулка не обжата" in prep
    actions = [s.text for s in CARDS["STRIP_HFM"].steps if s.kind == "Операция"]
    partial = next(i for i, text in enumerate(actions) if "удалить оболочку частично" in text)
    sleeve = next(i for i, text in enumerate(actions) if "установить и обжать опорную втулку" in text)
    final_strip = next(i for i, text in enumerate(actions) if "Снять оставленную часть оболочки" in text)
    assert partial < sleeve < final_strip
    assert "Четыре готовых контакта" in _text("INS_HFM_HOUSING")
    assert ITEMS["INS_HFM_HOUSING"]["metadata"]["cpa_sequence"] == "не задано"


def test_routine_retention_is_separate_from_destructive_qualification():
    routine = [s for s in CARDS["INS_FAKRA_ST_CONTACT"].steps if "сборочная проверка" in s.text]
    assert len(routine) == 1
    assert routine[0].tolerance == "усилие сборочной проверки ≤ 5 Н"
    for code, limit in (("PRS_FAKRA_ST_PIN", "60"), ("PRS_FAKRA_ST_SHIELD", "110"),
                        ("PRS_HFM_PIN", "60"), ("PRS_HFM_SHIELD", "120")):
        [pull] = [s for s in CARDS[code].steps if s.tolerance == f"усилие отрыва > {limit} Н"]
        assert "разрушающ" in pull.text.lower()
        assert "образц" in pull.text
    assert "рекомендуемый размер инструмента" in _text("PRS_FAKRA_RA_PIN")
    assert "допуск готового обжима" in _text("PRS_FAKRA_RA_SHIELD")


@pytest.mark.parametrize("code", ("INS_FAKRA_ST_HOUSING", "INS_FAKRA_ST_SEAL"))
def test_unavailable_sealed_body_instruction_blocks_guessed_assembly(code):
    text = _text(code)
    assert "MA_59V111" in text and "операцию не выполнять" in text
    assert "к этому герметичному корпусу не применять" in text
    assert ITEMS[code]["metadata"]["assembly_status"] == "Заблокировано до получения ДИ"


def test_payload_and_seed_use_lf_bytes():
    assert b"\r\n" not in PAYLOAD_PATH.read_bytes()
    assert b"\r\n" not in SEED_PATH.read_bytes()
