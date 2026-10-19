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


def test_a_read_gives_the_tube_and_sleeve_operations_both_ends_with_their_connectors():
    """«Надеть ТУТ / втулку»: эскиз рисует оба разделанных конца со своими разъёмами (владелец 19.10) — и у выданного ЭТК."""
    config = _config()
    config["ops"] = [{"type": "coaxInsTut", "units": 2, "templateId": CARDS["INS_TUT"].source_id}]
    config["coax"].update({"tutArt": "ТУТ 4/2мм, черная", "tutLength": 20})
    operation = _built(config)[0]
    assert "ends" not in operation["fields"]["coaxParts"]  # сборка не меняется: эталоны ЭТК те же
    seen = with_coax_connectors(operation, config)
    ends = seen["fields"]["coaxParts"]["ends"]
    assert [(end["side"], end["connectorType"], end["gender"], end["l3"]) for end in ends] == [
        ("А", "SMA-Male", "вилка", "7,5"), ("В", "SMA-Female bulkhead", "розетка", "10")]
    assert "ends" not in operation["fields"]["coaxParts"]


def _housings(depth_a: str = "", depth_b: str = "") -> list[dict]:
    config = _config()
    config["ops"] = [{"type": "coaxHousing", "side": side, "units": 1, "templateId": CARDS["INS_housing_COAX"].source_id} for side in ("A", "B")]
    for side, depth in (("sideA", depth_a), ("sideB", depth_b)):
        if depth:
            config["coax"][side]["contactDepth"] = depth
    return _built(config)


def test_housing_gets_the_contact_depth_of_its_connector_on_the_sketch_and_in_the_control_step():
    """Заглубление контакта от торца корпуса (БД.КОАКС, владелец 19.10): SMA-C174P вилка, SMA-C316F розетка."""
    male, female = _housings("0,38…1,14", "1,88…2,41")
    assert male["fields"]["coaxEnd"]["contactDepth"] == "0,38…1,14" and male["fields"]["contactDepth"] == "0,38…1,14"
    assert female["fields"]["coaxEnd"]["contactDepth"] == "1,88…2,41"
    for operation, depth in ((male, "0,38…1,14"), (female, "1,88…2,41")):
        control = [step["text"] for step in operation["card"]["steps"] if step["kind"] == "Контроль"]
        assert len(control) == 1 and control[0].endswith(f"Проверить заглубление контакта от торца корпуса: {depth} мм.")
        assert "дюйм" not in control[0] and "″" not in control[0]


def test_housing_without_a_depth_draws_and_says_nothing():
    """Угловой / не найденный в документах разъём — значения нет: ни ключа на эскизе, ни строки в контроле."""
    for operation in _housings():
        assert "contactDepth" not in operation["fields"]["coaxEnd"] and "contactDepth" not in operation["fields"]
        assert all("заглублен" not in step["text"] for step in operation["card"]["steps"])


def test_a_read_gives_an_issued_housing_the_depth_from_its_config():
    config = _config()
    config["coax"]["sideB"]["contactDepth"] = "1,88…2,41"
    operation = _housings()[1]
    seen = with_coax_connectors(operation, config)
    assert seen["fields"]["coaxEnd"]["contactDepth"] == "1,88…2,41"
    assert "contactDepth" not in operation["fields"]["coaxEnd"]
