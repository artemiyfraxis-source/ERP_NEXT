"""Коакс-ЭТК называет разъём каждого конца: «SMA-Male (вилка)» / «SMA-Female (розетка)» (владелец 19.10: «какая зачистка
к какому разъёму»). КБ1 «SMA-Male — SMA-Female RG316 275 мм», прод."""
import json
from copy import deepcopy
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk, coax_connector_kind, with_coax_connectors
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = {card.code: card for card in parse_operation_library(LIBRARY["templates"])}


def _config() -> dict:
    ops = [{"type": "coaxCut", "stripSides": ["A", "B"], "units": 1, "templateId": CARDS["CUT_COAX+2strip"].source_id},
           {"type": "coaxPin", "side": "A", "units": 1, "templateId": CARDS["SDR_COAX_pin"].source_id}]
    return {"mode": "coax", "partQty": 2000, "coax": {
        "cableLength": 285, "cableArt": "RG316", "cableName": "RG316",
        "sideA": {"article": "SMA-C174P", "type": "SMA-male", "l1": "2,5", "l2": "3,5", "l3": "7,5", "pinType": "пайка"},
        "sideB": {"article": "SMA-C316F", "type": "SMA-female bulkhead", "l1": "2,5", "l2": "4,8", "l3": "10", "pinType": "пайка"},
    }, "ops": ops}


def _built(config: dict) -> list[dict]:
    return build_etk(config, list(CARDS.values()), operation_library_commit=LIBRARY["sourceCommit"])["operations"]


def test_connector_kind_is_the_base_type_and_the_russian_half_by_contacts():
    assert coax_connector_kind({"type": "SMA-male"}) == ("SMA-Male", "вилка")
    assert coax_connector_kind({"type": "MCX-female R/A "}) == ("MCX-Female R/A", "розетка")
    assert coax_connector_kind({"type": "RP-SMA-female"}) == ("RP-SMA-Female", "розетка")
    assert coax_connector_kind({"type": "MHF1-plug"}) == ("MHF1-plug", "")
    assert coax_connector_kind({}) == ("", "")


def test_strip_result_names_the_connector_of_each_end():
    operations = _built(_config())
    assert operations[0]["result"][0]["name"] == "Кабель RG316 нарезан: 285 мм; конец А — под SMA-Male, конец В — под SMA-Female bulkhead"
    fields = operations[0]["fields"]
    assert (fields["coax"]["connectorType"], fields["coax"]["gender"]) == ("SMA-Male", "вилка")
    assert (fields["coaxB"]["connectorType"], fields["coaxB"]["gender"]) == ("SMA-Female bulkhead", "розетка")
    assert operations[1]["fields"]["coaxEnd"]["gender"] == "вилка"


def test_strip_without_connector_types_keeps_the_old_words():
    config = _config()
    for side in ("sideA", "sideB"):
        del config["coax"][side]["type"]
    assert _built(config)[0]["result"][0]["name"].endswith("; концы А и В разделаны")


def test_an_issued_etk_names_the_connector_halves_on_read():
    config = _config()
    old = deepcopy(_built(config)[0])
    for key in ("coax", "coaxB"):
        old["fields"][key] = {name: value for name, value in old["fields"][key].items() if name not in {"connectorType", "gender"}}
    seen = with_coax_connectors(old, config)
    assert (seen["fields"]["coaxB"]["connectorType"], seen["fields"]["coaxB"]["gender"]) == ("SMA-Female bulkhead", "розетка")
    assert seen["fields"]["coax"]["gender"] == "вилка"
    assert "connectorType" not in old["fields"]["coax"]  # the stored copy is not touched
