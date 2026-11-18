"""Passport norms come from backend/data/rf-norms.json: maker spec, else MIL-PRF-55339 sheet, else none."""

from dataclasses import replace

import pytest

from app.modules.production.instrument_norms import (assess, assess_loss, item_spec, loss_uncertainty_for, norm_for, norms_data, uncertainty_for, vna_model)
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
    s50180 = next(entry for entry in data["vna"] if entry["model"] == "S50180")
    assert "табл. 16" in s50180["loss_uncertainty"]["source"] and "табл. 19" in s50180["loss_uncertainty"]["source"]
    assert data["decision_rule_loss"]


def test_loss_norm_follows_the_vswr_source():
    sma = norm_for("", "", pair("SMA", "SMA")).loss
    assert sma.reference == "MIL-PRF-55339/55"
    assert sma.limit(0.4) is None and sma.limit(9) == pytest.approx(0.03 * 3)
    assert sma.limit(18) == pytest.approx(0.03 * 18 ** 0.5) and sma.limit(25) == pytest.approx(0.05 * 25 ** 0.5)
    assert "0,03·√F дБ в полосе 0,5–18 ГГц" in sma.line and "F — частота, ГГц" in sma.line
    maker = norm_for("Переход ADP1A-35F-35F", "", pair("3.5mm", "3.5mm")).loss
    assert maker.reference == "Планар ADP1A, РЭ табл. 2.6"
    assert maker.limit(0.01) == pytest.approx(0.20) and maker.limit(32) == pytest.approx(0.20)
    assert norm_for("ADP1A-NM-NF", "", pair("N", "N")).loss.reference == "Планар ADP1A, РЭ табл. 2.5"
    assert norm_for("ADP1A-NM-35F", "", pair("N", "3.5mm")).loss.limit(18) == pytest.approx(0.15)


@pytest.mark.parametrize("ports,ghz,limit", [(("N", "SMA"), 9, 0.18), (("SMA", "TNC"), 6, 0.06 * 6 ** 0.5),
                                             (("N", "N"), 9, 0.15), (("TNC", "TNC"), 3, 0.12 * 3 ** 0.5),
                                             (("BNC", "SMA"), 3, 0.2), (("BNC", "BNC"), 3, 0.2), (("BNC", "N"), 3, 0.35)])
def test_sheets_tested_at_one_frequency_are_judged_only_there(ports, ghz, limit):
    loss = norm_for("", "", pair(*ports)).loss
    assert not loss.segments and [(point.ghz, point.max_db) for point in loss.points] == [(ghz, pytest.approx(limit))]
    assert loss.limit(ghz) is None  # no banded limit: only the test frequency
    assert f"на частоте испытания {ghz} ГГц" in loss.limit_text


def test_point_norm_texts():
    assert norm_for("", "", pair("N", "SMA")).loss.limit_text == "вносимые потери ≤ 0,18 дБ на частоте испытания 9 ГГц"
    assert norm_for("", "", pair("SMA", "TNC")).loss.limit_text == \
        "вносимые потери ≤ 0,06·√F = 0,147 дБ на частоте испытания 6 ГГц; F — частота, ГГц"


def test_point_norm_uses_the_nearest_measured_point_inside_the_span():
    point = norm_for("", "", pair("N", "SMA")).loss
    result = assess_loss(summary({0.01: 1.02, 8.99: 1.03, 9.2: 1.04}, {0.01: 0.01, 8.99: 0.10, 9.2: 0.5}), point)
    assert result.status == "pass" and result.at_ghz == pytest.approx(8.99) and result.limit == pytest.approx(0.18)
    outside = assess_loss(summary({0.01: 1.02, 6: 1.04}), point)
    assert outside.status == "no_points" and outside.short == "нет точки замера на частоте испытания"


def test_loss_excess_within_the_uncertainty_is_a_border_not_a_failure():
    loss, uncertainty = norm_for("", "", pair("SMA", "SMA")).loss, loss_uncertainty_for("S50180")
    assert uncertainty.at(9) == pytest.approx(0.102) and uncertainty.at(18) == pytest.approx(0.107)
    vswr = {0.01: 1.02, 1: 1.03, 9: 1.04}

    def judged(at_nine, unc=uncertainty):
        return assess_loss(summary(vswr, {0.01: 0.01, 1: 0.02, 9: at_nine}), loss, unc)

    assert judged(0.05).status == "pass" and judged(0.05).border and judged(0.05).short == "соответствует норме (на границе допуска)"
    assert judged(0.15).status == "border" and judged(0.15).short == "на границе допуска — превышение меньше погрешности ВАЦ"
    assert judged(0.15).result_line == "потери 0,15 дБ на 9 ГГц при норме ≤ 0,09 дБ, погрешность ±0,102 дБ"
    failed = judged(0.20)
    assert failed.status == "fail" and failed.short == "не соответствует норме выше 1 ГГц"
    # The VNA model unknown: an excess under 0,05 dB is still a border, not a failure.
    assert judged(0.13, None).status == "border" and judged(0.15, None).status == "fail"
    assert assess_loss(summary(vswr), None).short == "норма не установлена"


def _s2p(points, model="S50180"):
    from datetime import datetime
    from uuid import uuid4

    from app.modules.production.traceability import InstrumentFile, InstrumentFileKind

    rows = "".join(f"{ghz} {s11} 0 {s21} 0 {s21} 0 {s11} 0\n" for ghz, s11, s21 in points)
    content = (f"! S2VNA Planar {model} №123 2026-11-16T12:30:00\n! Калибровка: АКМ №456\n# GHz S MA R 50\n" + rows).encode()
    return InstrumentFile(uuid4(), uuid4(), InstrumentFileKind.OTHER, "Замер ВАЦ 16.11.26.s2p", len(content),
                          datetime(2026, 11, 17), uuid4(), "Иван", content)


def _conclusion(points):
    from app.modules.production.instrument_characterization import characterization
    from app.modules.production.instrument_public import conclusion, public_passport
    from tests.production.test_instrument_passport import instrument, rf

    return conclusion(public_passport(instrument(), rf(), characterization(_s2p(points))))


def test_passport_conclusion_states_the_loss_norm_and_its_uncertainty():
    # SMA–SMA by /55: 0,175 dB at 18 GHz over 0,127 dB — within ±0,107 dB of the S50180: a border, VSWR verdict kept.
    result = _conclusion([(0.01, 0.01, 0.99), (9, 0.01, 0.99), (18, 0.02, 0.98)])
    lines = dict(result.lines)
    assert result.verdict == "Соответствует норме в полосе 9–18 ГГц; вносимые потери на границе допуска"
    assert lines["Норма потерь"].startswith("MIL-PRF-55339/55 — вносимые потери ≤ 0,03·√F дБ в полосе 0,5–18 ГГц")
    assert lines["Результат по потерям"].startswith("на границе допуска")
    assert lines["Погрешность измерения потерь"].startswith("±0,102 дБ до 10 ГГц, ±0,107 дБ выше 10 ГГц (Планар S50180")
    assert "по вносимым потерям" in lines["Правило принятия решения"]


def test_loss_failure_overrides_a_vswr_pass_even_on_the_vswr_border():
    # VSWR 1,13 at 18 GHz: within 0,022 of the /55 limit 1,14 (border pass); |S21| 0,95 = 0,446 dB » 0,127 + 0,107.
    gamma = 0.13 / 2.13
    result = _conclusion([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, gamma, 0.95)])
    assert result.verdict == "Не соответствует норме по вносимым потерям выше 9 ГГц"
    assert dict(result.lines)["Результат"].startswith("худший КСВН 1,13 на 18 ГГц")


def test_both_failures_are_named():
    result = _conclusion([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, 0.2, 0.95)])
    assert result.verdict == "Не соответствует норме выше 9 ГГц; вносимые потери тоже вне нормы"


def test_history_names_vswr_and_loss():
    from app.modules.production.instrument_characterization import characterization
    from app.modules.production.instrument_public import history_entry
    from tests.production.test_instrument_passport import instrument, rf

    entry = history_entry(instrument(), rf(), characterization(_s2p([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, 0.01, 0.99)])))
    assert entry.summary.startswith("КСВН: соответствует норме до 18 ГГц; потери: соответствует норме")
    assert "; применение: КСВН ≤ 1,30 (требование цеха)" in entry.summary
