"""Full stock name of a coaxial connector: house style kept, only distinguishing words added (owner 27.11.2026).

The cases are real prod lots (snapshot 27.11.2026) with their БД.КОАКС records (export 23.11.2026).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.workflows.stock_name_coax import coax_name
from app.workflows.stock_name_types import LotHints

FIXTURE = Path(__file__).parent / "fixtures" / "stock-names-coax-1127.json"
CASES = {case["article"]: case for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}
COAX = "Коаксиальный соединитель"


def record(fields: dict[str, str]) -> TechOperationRecord:
    fields = {**fields, "coaxArticle": fields.get("Артикул", ""), "coaxType": fields.get("Тип/Серия", ""),
              "coaxWire": fields.get("Провод", "")}
    return TechOperationRecord(
        id=uuid4(), tab=TechOperationTab.COAX, display_text="", normalized_search="", source_sheet="", sort_key="",
        fields=fields,
    )


def build(article: str):
    case = CASES[article]
    hints = LotHints(
        case["article"], case["item_type"], case["manufacturer"], case["name"], case["upd_name"], case["description"],
    )
    return coax_name(hints, [record(fields) for fields in case["records"]])


@pytest.mark.parametrize(("article", "name"), [
    # with a БД.КОАКС record
    ("SMA-C58P", "SMA-male RG-58"),
    ("S-231L", "SMA-female RG-174/RG-316"),
    ("N-111L", "N-male RG-174/RG-316"),
    ("S-A211/5D", "RP-SMA-female RG-6"),
    # angled
    ("S-221F", "SMA-female R/A RG-58"),
    ("SMA-LC174P", "SMA-male R/A RG-174/RG-316"),
    ("SMA-JWB2-F", "SMA-male R/A RG-405"),
    ("59K25H-1M4A4", "FAKRA-female R/A (тип Z) RTK 031"),
    # bulkhead / flange / PCB
    ("SMA-C316F", "SMA-female bulkhead RG-316"),
    ("N-245F female фланец", "N-female bulkhead RG-58"),
    ("901-9610-3SF", "SMA-female bulkhead LMR-100"),
    ("SMA-female на плату", "SMA-female на плату"),
    # without a record: code, then words
    ("SMA-Male RG178", "SMA-male RG-178"),
    ("SMA-Female RG-58", "SMA-female RG-58"),
    ("RHT-612-0646", "SMA-female RG-316/RG-174"),
    # RP-* keeps the house style
    ("RP-SMA-Female RG1,37", "RP-SMA-female"),
    ("ISMA-KB2-F", "SMA-female RG-405"),
])
def test_complete_names(article, name):
    result = build(article)
    assert (result.name, result.complete, result.missing) == (name, True, ())


def test_mounting_and_maker_go_to_attrs_not_to_the_name():
    crimp, solder = build("SMA-C58P"), build("ISMA-KB2-F")
    assert (crimp.attrs["mount"], crimp.attrs["maker"], crimp.attrs["gender"]) == ("обжим", "RUICHI", "pin")
    assert (solder.attrs["mount"], solder.attrs["maker"], solder.attrs["gender"]) == ("пайка", "TRL", "socket")
    assert build("S-221F").attrs["execution"] == "R/A"
    assert build("SMA-C316F").attrs["variant"] == "bulkhead" and build("SMA-C316F").attrs["cable"] == "RG-316"
    assert build("SMA-Male RG178").attrs["mount"] == ""  # no record, the text does not say


def test_reference_and_article_code_disagree_on_sex_goes_to_review():
    for article in ("SMA-C58J GOLD", "TNC-C58J"):  # J = plug in the code, the record says socket
        result = build(article)
        assert not result.complete and result.missing == ("пол: БД.КОАКС и код артикула расходятся",)


@pytest.mark.parametrize(("article", "missing"), [
    ("BNC RG58 05-3001", ("серия",)),
    ("HSDCF-107-Z", ("серия",)),  # HSD is not a coax family the builder knows
    ("переходник BNC male - SMA female", ("переходник: два соединителя",)),
])
def test_unrecognised_items_go_to_review(article, missing):
    result = build(article)
    assert not result.complete and result.missing == missing


def test_unknown_sex_is_never_guessed():
    hints = LotHints("TEST-SMA-1", COAX, name="SMA")
    result = coax_name(hints, [record({"Артикул": "TEST-SMA-1", "Тип/Серия": "SMA", "Провод": "RG-58"})])
    assert (result.name, result.complete, result.missing) == ("SMA RG-58", False, ("пол",))


def test_reverse_polarity_without_sex_reports_rp_sex():
    hints = LotHints("TEST-RP-1", COAX)
    result = coax_name(hints, [record({"Артикул": "TEST-RP-1", "Тип/Серия": "RP-SMA", "Провод": "RG-174"})])
    assert not result.complete and result.missing == ("RP-пол",)
    reverse = coax_name(hints, [record({
        "Артикул": "TEST-RP-1", "Тип/Серия": "SMA", "Пол": "гнездо", "Полярность": "обратная (RP)", "Провод": "RG-174",
    })])
    assert (reverse.name, reverse.complete) == ("RP-SMA-female RG-174", True)


def test_record_with_several_cables_lists_standard_ones_only():
    records = [
        record({"Артикул": "TEST-1", "Тип/Серия": "SMA-male", "Провод": wire})
        for wire in ("RG-316", "RG174", "SM047")
    ]
    result = coax_name(LotHints("TEST-1", COAX), records)
    assert result.name == "SMA-male"  # a synonym mark among the wires: the cable is not guessed


def test_other_item_types_are_not_coax():
    assert coax_name(LotHints("SMA-C58P", "Соединитель"), []) is None
