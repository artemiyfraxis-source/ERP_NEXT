"""D1–D3 коакс-сборки — только у кабеля (владелец 16.10.2026: «разъём тянет L1–L3, а провод — свои данные»): ЭТК
берёт их из СПР.КАБ по кабелю сборки, а не из строки разъёма и не из того, что сохранённая конфигурация взяла у разъёма."""

import json
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.workflows.techcard_build import enrich_coax_diameters

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])

DIA_ROWS = [
    {"Кабель": "RG-316", "D1": "0,51", "D2": "1,52", "D3": "2,5"},
    {"Кабель": "RG-174", "D1": "0,48", "D2": "1,52", "D3": "2,23"},
    {"Кабель": "Кабель RG-174 ПЭ", "D1": "0,48", "D2": "1,52", "D3": "2,23"},  # другое имя кабеля (coax_dia_rows)
]


def _config(cable_art: str = "RG316", cable_name: str = "", side_b: bool = True) -> dict:
    # Конфигурация, сохранённая старым генератором: D у концов — из строки разъёма («9»).
    coax = {"cableArt": cable_art, "cableName": cable_name,
            "sideA": {"article": "SMA-J", "l1": "4", "l2": "3", "l3": "2", "d1": "9", "d2": "9", "d3": "9"}}
    if side_b:
        coax["sideB"] = {"article": "N-M", "l1": "5", "d1": "9"}
    return {"mode": "coax", "coax": coax, "ops": []}


def test_ends_get_the_cable_diameters_whatever_the_connector_row_said():
    coax = enrich_coax_diameters(_config(), DIA_ROWS)["coax"]
    assert {key: coax["sideA"][key] for key in ("d1", "d2", "d3", "l1")} == {"d1": "0,51", "d2": "1,52", "d3": "2,5", "l1": "4"}
    assert (coax["sideB"]["d1"], coax["sideB"]["d3"]) == ("0,51", "2,5")
    # The cable by its other name, and by the name when the article is empty — the generator's key.
    assert enrich_coax_diameters(_config("", "Кабель RG-174 ПЭ"), DIA_ROWS)["coax"]["sideA"]["d3"] == "2,23"


def test_a_cable_without_a_d_row_leaves_no_diameters_on_the_ends():
    coax = enrich_coax_diameters(_config("RG-213"), DIA_ROWS)["coax"]
    assert not {"d1", "d2", "d3"} & set(coax["sideA"]) and "d1" not in coax["sideB"]
    assert coax["sideA"]["l1"] == "4"
    assert not {"d1", "d2", "d3"} & set(enrich_coax_diameters(_config(""), DIA_ROWS)["coax"]["sideA"])


def test_configs_without_coax_pass_and_the_source_is_not_changed():
    config = _config()
    enrich_coax_diameters(config, DIA_ROWS)
    assert config["coax"]["sideA"]["d1"] == "9"
    assert enrich_coax_diameters({"mode": "inter", "ops": []}, DIA_ROWS) == {"mode": "inter", "ops": []}


def test_etk_strip_sketch_draws_the_cable_diameters():
    strip = next(card for card in CARDS if card.code == "STRIP_COAX_auto")
    config = _config("RG-174", side_b=False)
    config["ops"] = [{"type": "coaxStrip", "side": "A", "units": 1, "templateId": strip.source_id}]
    operation = build_etk(enrich_coax_diameters(config, DIA_ROWS), CARDS,
                          operation_library_commit=LIBRARY["sourceCommit"])["operations"][0]
    assert operation["fields"]["coax"] == {"cable": "RG-174", "connector": "SMA-J", "d1": "0,48", "d2": "1,52", "d3": "2,23",
                                           "l1": "4", "l2": "3", "l3": "2", "lPlus": "", "lMinus": ""}
