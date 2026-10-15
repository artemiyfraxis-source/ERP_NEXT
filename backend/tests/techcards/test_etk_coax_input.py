import json
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])

CODES = {
    "coaxCut": "CUT_WIRE_auto", "coaxStrip": "STRIP_COAX_auto", "coaxCutTut": "CUT_TUT_short", "coaxPin": "SDR_COAX_pin",
    "coaxInsTut": "INS_TUT", "coaxInsSleeve": "INS_SLEEVE_coax", "coaxHousing": "INS_housing_COAX",
    "coaxShield": "PRS_SLEEVE_coax", "coaxHeat": "HEAT_TYT_short",
}
PAIRS = {"coaxCutTut", "coaxInsTut", "coaxInsSleeve", "coaxShield", "coaxHeat"}


def _owner_coax_etk(**coax_extra: object) -> list[dict]:
    """Кабельная сборка MMCX-Male — N-Male на RG316, 1 м, 20 шт. (проект 796К.2, владелец 15.10): сборка вкладки «Коакс»."""
    cards = {card.code: card for card in CARDS}
    sequence = [("coaxCut", ""), ("coaxStrip", "A"), ("coaxStrip", "B"), ("coaxCutTut", ""), ("coaxPin", "A"), ("coaxInsTut", ""),
                ("coaxInsSleeve", ""), ("coaxHousing", "A"), ("coaxPin", "B"), ("coaxHousing", "B"), ("coaxShield", ""), ("coaxHeat", "")]
    ops = [{"type": kind, "side": end, "units": 2 if kind in PAIRS else 1, "templateId": cards[CODES[kind]].source_id}
           for kind, end in sequence]
    config = {"mode": "coax", "partQty": 20, "coax": {
        "cableLength": 1010, "tutLength": 20, "tutArt": "ТУТ 3/1.5", "tutName": "Термоусадочная трубка 3/1,5",
        "cableArt": "RG316", "cableName": "Кабель RG316", "sleeveArt": "SL-316", "sleeveName": "Втулка экрана RG316",
        "sideA": {"article": "MMCX-Male", "connName": "Разъём MMCX-Male"},
        "sideB": {"article": "N-Male", "connName": "Разъём N-Male"}, **coax_extra,
    }, "ops": ops}
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])["operations"]


def _inputs(operation: dict) -> dict[str, tuple[str, str]]:
    return {row["art"]: (row["norm"], row["unit"]) for row in operation["components"]}


def test_coax_cut_input_is_the_cable_in_meters_and_params_carry_the_cut_length():
    # Владелец 15.10: «не появилось кол-во» — «Вход» резки коаксиала был пуст, в «Общих» не было длины и допуска.
    cut = _owner_coax_etk()[0]
    assert _inputs(cut) == {"RG316": ("20,2", "М.")}
    assert cut["fields"]["lengthKd"] == "1010мм"
    assert cut["fields"]["cutTolerance"] == "±8 мм"


def test_every_coax_operation_gets_its_parts_in_the_input():
    by_type: dict[tuple[str, str], dict] = {}
    for operation in _owner_coax_etk():
        by_type.setdefault((operation["type"], operation["side"]), operation)
    assert _inputs(by_type[("coaxCutTut", "")]) == {"ТУТ 3/1.5": ("40", "Шт.")}
    assert _inputs(by_type[("coaxPin", "A")]) == {"MMCX-Male": ("20", "Шт.")}
    assert _inputs(by_type[("coaxPin", "B")]) == {"N-Male": ("20", "Шт.")}
    assert _inputs(by_type[("coaxInsSleeve", "")]) == {"SL-316": ("40", "Шт.")}
    assert by_type[("coaxStrip", "A")]["components"] == []


def test_old_coax_config_without_cable_length_builds_without_input_rows():
    operations = _owner_coax_etk(cableLength="", cableArt="", sleeveArt="", sleeveName="")
    assert operations[0]["components"] == [] and "lengthKd" not in operations[0]["fields"]
