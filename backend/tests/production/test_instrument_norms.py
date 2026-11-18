"""Passport norms come from backend/data/rf-norms.json: maker spec, else MIL-PRF-55339 sheet, else none."""

from dataclasses import replace

import pytest

from app.modules.production.instrument_norms import (assess, item_spec, norm_for, norms_data, uncertainty_for, vna_model)
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from tests.production.test_instrument_application import summary


def port(series, male=True, ohm=50):
    return RfInterface(series, "вилка" if male else "розетка", "штырь" if male else "гнездо", ohm)


def pair(a, b, ohm=50):
    return RfDetails((port(a, ohm=ohm), port(b, False, ohm)), "adapter")


def test_every_number_has_a_source():
    data = norms_data()
    assert all(maker["source"] and maker["url"] for maker in data["makers"])
    assert all(sheet["sheet"].startswith("MIL-PRF-55339/") and sheet["url"] for sheet in data["mil_adapters"])
    assert all(vna["source"] for vna in data["vna"])
    assert data["shop_requirement"] == {**data["shop_requirement"], "vswr_max": 1.30, "label": "требование цеха"}


def test_maker_spec_wins_over_mil_sheet():
    norm = norm_for("Переход коаксиальный ADP1A-35F-35F", "", pair("3.5mm", "3.5mm"))
    assert norm.maker and norm.limit(18) == pytest.approx(1.06) and norm.limit(21) == pytest.approx(1.10)
    assert norm_for("ADP1A-NM-NF", "", pair("N", "N")).limit(18) == pytest.approx(1.08)


def test_mil_sheet_by_connector_pair_with_formula():
    norm = norm_for("Переходник SN-321", "", pair("N", "SMA"))
    assert norm.reference == "MIL-PRF-55339/54B" and not norm.maker
    assert norm.limit(12) == pytest.approx(1.12) and norm.limit(18) == pytest.approx(0.83 + 0.023 * 18)
    assert norm.limit(19) is None
    sma = norm_for("", "", pair("SMA", "SMA"))
    assert sma.limit(0.4) is None and sma.limit(18) == pytest.approx(1.14)


@pytest.mark.parametrize("details", [pair("RP-SMA", "SMA"), pair("3.5mm", "3.5mm"), pair("BNC", "BNC", ohm=75), RfDetails()])
def test_no_norm_without_maker_data_or_sheet(details):
    assert norm_for("Переходник", "", details) is None
    assert assess(summary({0.01: 1.02, 18: 1.04}), None).verdict == \
        "Норма не установлена — заключение о соответствии не выдаётся"


def test_pass_fail_and_border_against_the_norm():
    norm = norm_for("", "", pair("N", "SMA"))
    passed = assess(summary({0.01: 1.02, 6: 1.05, 18: 1.10}), norm)
    assert passed.status == "pass" and passed.verdict == "Соответствует норме в полосе 0,01–18 ГГц"
    failed = assess(summary({0.01: 1.02, 6: 1.05, 13: 1.30, 18: 1.10}), norm)
    assert failed.verdict == "Не соответствует норме выше 6 ГГц"
    assert failed.result_line == "КСВН 1,3 на 13 ГГц при норме ≤ 1,129"
    border = assess(summary({0.01: 1.02, 6: 1.085}), norm, uncertainty_for("S50180"))
    assert border.status == "pass" and border.verdict.endswith("(на границе допуска)")


def test_uncertainty_is_stored_per_vna_model_and_resolved_by_serial():
    u = uncertainty_for("S50180")
    assert (u.at(5), u.at(12)) == (0.015, 0.022) and u.line.startswith("±0,015 до 10 ГГц, ±0,022 выше 10 ГГц")
    assert uncertainty_for("") is None
    assert vna_model("Planar S50180 №123") == "S50180"

    class Vna:
        serial_number, name, doc_designation = "22123456", "Анализатор цепей Planar S50180", ""
    assert vna_model("ВАЦ №22123456", [Vna()]) == "S50180"
    assert vna_model("ВАЦ №1", [Vna()]) == ""


def test_item_spec_tag_over_datasheet_and_mil_default():
    tag = item_spec("Переход коаксиальный ADP1A-35F-35F", "", pair("3.5mm", "3.5mm"))
    assert (tag.temp_min_c, tag.temp_max_c, tag.mating_resource) == (-60, 85, 3000)
    assert tag.temperature_line == "−60…+85 °C" and "бирка на изделии" in tag.temp_source
    series = item_spec("ADP1A-35M-35F", "", pair("3.5mm", "3.5mm"))
    assert (series.temp_max_c, series.mating_resource) == (110, 3000)
    stock = item_spec("Переходник S-322", "S-322", pair("SMA", "SMA"))
    assert (stock.temp_min_c, stock.temp_max_c, stock.mating_resource) == (-65, 165, 500)
    assert stock.temp_source == stock.mating_source == "MIL-PRF-55339/55"
    assert item_spec("Переходник", "", pair("RP-SMA", "SMA")) is None


def test_instrument_card_resource_wins_when_present():
    details = pair("SMA", "SMA")
    object.__setattr__(details, "mating_resource", 800)  # PR #913 stores it on RfDetails
    spec = item_spec("Переходник S-322", "", details)
    assert (spec.mating_resource, spec.mating_source, spec.temp_max_c) == (800, "карточка СИ", 165)
    assert replace(spec, mating_resource=None).mating_resource is None
