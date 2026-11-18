"""Passport norms come from backend/data/rf-norms.json: maker spec, else MIL-PRF-55339 sheet, else none."""

from dataclasses import replace

import pytest

from app.modules.production.instrument_norms import (assess, assess_loss, item_spec, norm_for, norms_data, uncertainty_for, vna_model)
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


def test_every_vswr_norm_has_a_loss_norm_with_source():
    data = norms_data()
    for holder in [*data["mil_adapters"], *(group for maker in data["makers"] for group in maker["groups"])]:
        loss = holder["loss"]
        assert loss["source"] and (loss.get("segments") or loss.get("points"))


def test_loss_norm_follows_the_vswr_source():
    sma = norm_for("", "", pair("SMA", "SMA")).loss
    assert sma.reference == "MIL-PRF-55339/55"
    assert sma.limit(0.4) is None and sma.limit(9) == pytest.approx(0.03 * 3)
    assert sma.limit(18) == pytest.approx(0.03 * 18 ** 0.5) and sma.limit(25) == pytest.approx(0.05 * 25 ** 0.5)
    assert "0,03·√F дБ в полосе 0,5–18 ГГц" in sma.line and "F — частота, ГГц" in sma.line
    maker = norm_for("Переход ADP1A-35F-35F", "", pair("3.5mm", "3.5mm")).loss
    assert maker.reference == "Планар ADP1A" and maker.limit(0.01) == pytest.approx(0.20) and maker.limit(32) == pytest.approx(0.20)
    assert norm_for("ADP1A-NM-NF", "", pair("N", "N")).loss.limit(18) == pytest.approx(0.15)
    assert norm_for("ADP1A-NM-35F", "", pair("N", "3.5mm")).loss.limit(18) == pytest.approx(0.15)
    assert norm_for("", "", pair("SMA", "TNC")).loss.limit(6) == pytest.approx(0.06 * 6 ** 0.5)
    assert norm_for("", "", pair("BNC", "SMA")).loss.limit(3) == pytest.approx(0.115 * 3 ** 0.5)


def test_sheet_without_formula_is_judged_only_at_its_test_frequency():
    loss = norm_for("", "", pair("N", "SMA")).loss
    assert loss.limit(9) == pytest.approx(0.18) and loss.limit(9.04) == pytest.approx(0.18)
    assert loss.limit(6) is None and loss.limit(12) is None
    assert loss.limit_text == "вносимые потери ≤ 0,18 дБ на 9 ГГц"
    assert norm_for("", "", pair("BNC", "BNC")).loss.limit(3) == pytest.approx(0.2)
    assert norm_for("", "", pair("BNC", "N")).loss.limit(3) == pytest.approx(0.35)


def test_loss_pass_fail_and_missing_points():
    loss = norm_for("", "", pair("SMA", "SMA")).loss
    vswr = {0.01: 1.02, 1: 1.03, 9: 1.04, 18: 1.05}
    passed = assess_loss(summary(vswr, {0.01: 0.01, 1: 0.02, 9: 0.05, 18: 0.10}), loss)
    assert passed.status == "pass" and passed.short == "соответствует норме"
    assert passed.result_line == "худшие потери 0,02 дБ на 1 ГГц при норме ≤ 0,03 дБ"
    failed = assess_loss(summary(vswr, {0.01: 0.01, 1: 0.02, 9: 0.12, 18: 0.10}), loss)
    assert failed.status == "fail" and failed.short == "не соответствует норме выше 1 ГГц"
    assert assess_loss(summary(vswr), None).short == "норма не установлена"
    point = norm_for("", "", pair("N", "SMA")).loss
    assert assess_loss(summary({0.01: 1.02, 6: 1.04}), point).status == "no_points"
    assert assess_loss(summary({0.01: 1.02, 9: 1.04}, {0.01: 0.01, 9: 0.2}), point).status == "fail"


def test_passport_conclusion_states_the_loss_norm_and_fails_on_loss():
    from app.modules.production.instrument_characterization import characterization
    from app.modules.production.instrument_public import conclusion, public_passport
    from tests.production.test_instrument_passport import instrument, measured_file, rf

    # SMA–SMA by MIL-PRF-55339/55: VSWR 1,04 at 18 GHz passes, |S21| 0,98 (0,175 dB) exceeds 0,03·√18 = 0,127 dB.
    result = conclusion(public_passport(instrument(), rf(), characterization(measured_file())))
    lines = dict(result.lines)
    assert result.verdict == "Не соответствует норме по вносимым потерям"
    assert lines["Норма потерь"].startswith("MIL-PRF-55339/55 — вносимые потери ≤ 0,03·√F дБ в полосе 0,5–18 ГГц")
    assert lines["Результат по потерям"] == "не соответствует норме: потери 0,175 дБ на 18 ГГц при норме ≤ 0,127 дБ"
    assert "не задана" in lines["Погрешность измерения потерь"]
