"""Passport norms come from backend/data/rf-norms.json: only the norm ASSIGNED to the item (norm_id), else none."""

from dataclasses import replace

import pytest

from app.modules.production.instrument_norms import (assess, assess_loss, item_spec, loss_uncertainty_for, norm_by_id, norm_for, norms_data,
                                                     resolve_vna, uncertainty_for, vna_model)
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from tests.production.test_instrument_application import summary


def port(series, male=True, ohm=50):
    return RfInterface(series, "вилка" if male else "розетка", "штырь" if male else "гнездо", ohm)


def pair(a, b, ohm=50, norm=""):
    return RfDetails((port(a, ohm=ohm), port(b, False, ohm)), "adapter", norm_id=norm)


def test_every_number_has_a_source():
    data = norms_data()
    assert all(maker["source"] and maker["url"] for maker in data["makers"])
    assert all(sheet["sheet"].startswith("MIL-PRF-55339/") and sheet["url"] for sheet in data["mil_adapters"])
    assert all(vna["source"] for vna in data["vna"])
    assert "shop_requirement" not in data  # owner 18.11: 1,30 was never a requirement; the item's own norm decides


def test_assigned_maker_norm():
    norm = norm_for("Переход коаксиальный ADP1A-35F-35F", "", pair("3.5mm", "3.5mm", norm="adp1a-35"))
    assert norm.maker and norm.limit(18) == pytest.approx(1.06) and norm.limit(21) == pytest.approx(1.10)
    assert norm_for("ADP1A-NM-NF", "", pair("N", "N", norm="adp1a-n")).limit(18) == pytest.approx(1.08)


def test_mil_sheet_by_connector_pair_with_formula():
    norm = norm_for("Переходник SN-321", "", pair("N", "SMA", norm="mil-55339-54b"))
    assert norm.reference == "MIL-PRF-55339/54B" and not norm.maker
    assert norm.limit(12) == pytest.approx(1.12) and norm.limit(18) == pytest.approx(0.83 + 0.023 * 18)
    assert norm.limit(19) is None
    sma = norm_by_id("mil-55339-55")
    assert sma.limit(0.4) is None and sma.limit(18) == pytest.approx(1.14)


@pytest.mark.parametrize("details", [pair("SMA", "SMA"), pair("3.5mm", "3.5mm"), pair("N", "N"), RfDetails(),
                                     replace(pair("SMA", "SMA"), norm_id="no-such-norm")])
def test_no_norm_unless_assigned_no_run_time_fallback(details):
    # Even an ADP1A by name or an SMA pair with a MIL sheet gets no norm until one is assigned to the item.
    assert norm_for("Переход ADP1A-35F-35F", "ADP1A-35F-35F", details) is None
    assert item_spec("Переход ADP1A-35F-35F", "", details) is None
    assert assess(summary({0.01: 1.02, 18: 1.04}), None).verdict == \
        "Норма не назначена — заключение о соответствии не выдаётся"


def test_pass_fail_and_border_against_the_norm():
    norm = norm_by_id("mil-55339-54b")
    passed = assess(summary({0.01: 1.02, 6: 1.05, 18: 1.10}), norm)
    assert passed.status == "pass" and passed.verdict == "Соответствует норме в полосе 0,01–18 ГГц"
    failed = assess(summary({0.01: 1.02, 6: 1.05, 13: 1.30, 18: 1.10}), norm)
    assert failed.verdict == "Не соответствует норме выше 6 ГГц"
    assert failed.result_line == "КСВН 1,3 на 13 ГГц при норме ≤ 1,129"


@pytest.mark.parametrize("offset,once_status,final_status", [
    (-0.03, "pass", "pass"), (-0.0201, "pass", "pass"), (-0.0199, "border", "pass"), (0.0, "border", "pass"),
    (0.0199, "border", "pass"),    # within limit + U: «пару соток» over the norm conform after the repeat
    (0.0201, "fail", "fail"),      # beyond limit + U: a real excess, before and after the repeat
    (0.03, "fail", "fail")])
def test_vswr_decision_zone_is_the_limit_extended_by_the_uncertainty(offset, once_status, final_status):
    # СТО ФРАК-002-2026 (owner 18.11): |КСВН − предел| ≤ U triggers the 3-connection repeat; after it the worst
    # connection conforms when ≤ limit + U (ILAC-G8 zone extended by U). U for the norm value (13.3): limit 1,09 at
    # 6 GHz → 0,022 · 1,09 / 1,2 = 0,01998 (S50180, РЭ табл. 23).
    norm, u = norm_by_id("mil-55339-54b"), uncertainty_for("S50180")
    measured = summary({0.01: 1.02, 3: 1.02, 6: round(norm.limit(6) + offset, 4)})
    once = assess(measured, norm, u)
    assert once.status == once_status
    if once_status == "border":
        assert once.verdict == "На границе допуска выше 3 ГГц — нужен повторный замер (3 подключения с поворотом 120°)"
        assert once.brief.endswith("погрешность ±0,02")
    final = assess(measured, norm, u, repeated=True)
    assert final.status == final_status
    assert final.verdict == ("Соответствует норме в полосе 0,01–6 ГГц" if final_status == "pass"
                             else "Не соответствует норме выше 3 ГГц")


def test_uncertainty_is_stored_per_vna_model_and_resolved_by_serial():
    u = uncertainty_for("S50180")
    assert (u.at(5), u.at(12)) == (0.022, 0.029) and u.line.startswith("±0,022 до 10 ГГц, ±0,029 выше 10 ГГц")
    assert "табл. 23" in u.source  # РЭ S-серия, табл. 23 (methodology review PR #928)
    assert uncertainty_for("") is None
    assert vna_model("Planar S50180 №123") == "S50180"

    class Vna:
        serial_number, name, doc_designation = "22123456", "Анализатор цепей Planar S50180", ""
    assert vna_model("ВАЦ №22123456", [Vna()]) == "S50180"
    assert vna_model("ВАЦ №1", [Vna()]) == ""


def test_vna_resolved_from_the_file_name_or_the_only_registry_entry_of_that_model():
    class Vna:
        inventory_number, serial_number, name, doc_designation = "СИ.002", "22051234", "Анализатор цепей векторный S50180", ""
    vna = Vna()
    # The tool writes «VNA serial=…» that differs from the card's serial; the file name still names the model.
    header = ("VNA serial=2205123; ACM serial=2104056", "Замер ВАЦ S50180, АКМ, 16.11.2026, СИ.006.s2p")
    assert resolve_vna(header, [vna]) == ("S50180", vna)
    assert resolve_vna(("VNA serial=22051234; ACM serial=1",), [vna]) == ("S50180", vna)
    assert resolve_vna(("Замер ВАЦ.s2p",), [vna]) == ("", None)
    assert resolve_vna(header, [vna, vna]) == ("S50180", None)  # two of that model: no guess


def test_brief_norm_names_only_the_bands_the_measurement_touches():
    adp = norm_by_id("adp1a-35")
    assert adp.brief(0.01, 18) == "РЭ Планар ADP1A, табл. 2.6 — КСВН ≤ 1,06 до 20 ГГц; потери ≤ 0,2 дБ до 32 ГГц"
    assert adp.brief(0.01, 26) == "РЭ Планар ADP1A, табл. 2.6 — КСВН ≤ 1,06 до 20 ГГц; ≤ 1,1 до 26,5 ГГц; потери ≤ 0,2 дБ до 32 ГГц"
    mil = norm_by_id("mil-55339-55")
    assert mil.brief(0.01, 18) == ("MIL-PRF-55339/55 — КСВН ≤ 1,05 + 0,005·F в полосе 0,5–27 ГГц; "
                                   "потери ≤ 0,03·√F дБ в полосе 0,5–18 ГГц")
    assert "http" not in adp.brief(0.01, 18) + mil.brief(0.01, 18)


def test_item_spec_tag_over_datasheet_and_mil_default():
    tag = item_spec("Переход коаксиальный ADP1A-35F-35F", "", pair("3.5mm", "3.5mm", norm="adp1a-35"))
    assert (tag.temp_min_c, tag.temp_max_c, tag.mating_resource) == (-60, 85, 3000)
    assert tag.temperature_line == "−60…+85 °C" and "бирка на изделии" in tag.temp_source
    series = item_spec("ADP1A-35M-35F", "", pair("3.5mm", "3.5mm", norm="adp1a-35"))
    assert (series.temp_max_c, series.mating_resource) == (110, 3000)
    stock = item_spec("Переходник S-322", "S-322", pair("SMA", "SMA", norm="mil-55339-55"))
    assert (stock.temp_min_c, stock.temp_max_c, stock.mating_resource) == (-65, 165, 500)
    assert stock.temp_source == stock.mating_source == "MIL-PRF-55339/55"
    assert item_spec("Переходник", "", pair("RP-SMA", "SMA")) is None


def test_instrument_card_resource_wins_when_present():
    details = pair("SMA", "SMA", norm="mil-55339-55")
    object.__setattr__(details, "mating_resource", 800)  # PR #913 stores it on RfDetails
    spec = item_spec("Переходник S-322", "", details)
    assert (spec.mating_resource, spec.mating_source, spec.temp_max_c) == (800, "карточка СИ", 165)
    assert replace(spec, mating_resource=None).mating_resource is None


def test_every_vswr_norm_has_a_loss_norm_with_source():
    data = norms_data()
    type_norms = [entry for entry in data["type_norms"] if entry["level"] != "iec"]  # IEC 61169-8 sets no loss
    for holder in [*data["mil_adapters"], *type_norms, *(group for maker in data["makers"] for group in maker["groups"])]:
        loss = holder["loss"]
        assert loss["source"] and (loss.get("segments") or loss.get("points"))
    s50180 = next(entry for entry in data["vna"] if entry["model"] == "S50180")
    assert "табл. 23" in s50180["loss_uncertainty"]["source"]
    assert [step["value_db"] for step in s50180["loss_uncertainty"]["steps"]] == [0.1, 0.1]
    assert data["decision_rule_loss"]


def test_loss_norm_follows_the_vswr_source():
    sma = norm_by_id("mil-55339-55").loss
    assert sma.reference == "MIL-PRF-55339/55"
    assert sma.limit(0.4) is None and sma.limit(9) == pytest.approx(0.03 * 3)
    assert sma.limit(18) == pytest.approx(0.03 * 18 ** 0.5) and sma.limit(25) == pytest.approx(0.05 * 25 ** 0.5)
    assert "0,03·√F дБ в полосе 0,5–18 ГГц" in sma.line and "F — частота, ГГц" in sma.line
    maker = norm_by_id("adp1a-35").loss
    assert maker.reference == "РЭ Планар ADP1A, табл. 2.6"
    assert maker.limit(0.01) == pytest.approx(0.20) and maker.limit(32) == pytest.approx(0.20)
    assert norm_by_id("adp1a-n").loss.reference == "РЭ Планар ADP1A, табл. 2.5"
    assert norm_by_id("adp1a-n-35").loss.limit(18) == pytest.approx(0.15)


@pytest.mark.parametrize("sheet,ghz,limit", [("54b", 9, 0.18), ("40b-43b", 6, 0.06 * 6 ** 0.5), ("5a", 9, 0.15),
                                             ("33a", 3, 0.12 * 3 ** 0.5), ("44c", 3, 0.2), ("13b", 3, 0.2), ("20a", 3, 0.35),
                                             ("37a", 3, 0.2)])
def test_sheets_tested_at_one_frequency_are_judged_only_there(sheet, ghz, limit):
    loss = norm_by_id(f"mil-55339-{sheet}").loss
    assert not loss.segments and [(point.ghz, point.max_db) for point in loss.points] == [(ghz, pytest.approx(limit))]
    assert loss.limit(ghz) is None  # no banded limit: only the test frequency
    assert f"на частоте испытания {ghz} ГГц" in loss.limit_text


def test_point_norm_texts():
    assert norm_by_id("mil-55339-54b").loss.limit_text == "вносимые потери ≤ 0,18 дБ на частоте испытания 9 ГГц"
    assert norm_by_id("mil-55339-40b-43b").loss.limit_text == \
        "вносимые потери ≤ 0,06·√F = 0,147 дБ на частоте испытания 6 ГГц; F — частота, ГГц"


def test_point_norm_uses_the_nearest_measured_point_inside_the_span():
    point = norm_by_id("mil-55339-54b").loss
    result = assess_loss(summary({0.01: 1.02, 8.99: 1.03, 9.2: 1.04}, {0.01: 0.01, 8.99: 0.10, 9.2: 0.5}), point)
    assert result.status == "pass" and result.at_ghz == pytest.approx(8.99) and result.limit == pytest.approx(0.18)
    outside = assess_loss(summary({0.01: 1.02, 6: 1.04}), point)
    assert outside.status == "no_points" and outside.short == "нет точки замера на частоте испытания"


def test_loss_limit_within_twice_the_vna_uncertainty_is_not_judged():
    # /55 at 9 GHz: 0,03·√9 = 0,09 dB ≤ 2 · 0,1 dB (S50180, РЭ ч. 1 табл. 23): the VNA cannot resolve it — reported, not judged.
    loss, uncertainty = norm_by_id("mil-55339-55").loss, loss_uncertainty_for("S50180")
    assert uncertainty.at(9) == pytest.approx(0.1) and uncertainty.at(18) == pytest.approx(0.1)
    vswr = {0.01: 1.02, 9: 1.04}

    def judged(at_nine, unc=uncertainty):
        return assess_loss(summary(vswr, {0.01: 0.01, 9: at_nine}), loss, unc)

    for value in (0.05, 0.5):
        assert judged(value).status == "below_accuracy"
    assert judged(0.05).brief == "потери 0,05 дБ (норма 0,09 дБ ниже точности ВАЦ — не оценивается)"
    assert judged(0.05).short == "норма ниже точности ВАЦ — не оценивается"
    # The VNA model unknown (±0,05 dB): 0,09 dB ≤ 0,1 dB — still not judged.
    assert judged(0.5, None).status == "below_accuracy"
    assert assess_loss(summary(vswr), None).short == "норма не назначена"


def test_resolvable_loss_follows_the_same_decision_zone():
    from app.modules.production.instrument_norms import LossNorm, LossSegment

    loss, uncertainty = LossNorm("Лист", 0.0, (LossSegment(18, 0.5),)), loss_uncertainty_for("S50180")
    vswr = {0.01: 1.02, 3: 1.02, 9: 1.04}

    def judged(at_nine, repeated=False):
        return assess_loss(summary(vswr, {0.01: 0.01, 3: 0.02, 9: at_nine}), loss, uncertainty, repeated=repeated)

    assert judged(0.3).status == "pass" and judged(0.3).brief == "потери ≤ 0,3 дБ (9 ГГц)"
    assert judged(0.45).status == judged(0.55).status == judged(0.6).status == "border"
    assert judged(0.55).short == "на границе допуска выше 3 ГГц — нужен повторный замер (3 подключения с поворотом 120°)"
    assert judged(0.55).brief == "потери 0,55 дБ на 9 ГГц при норме ≤ 0,5 дБ, погрешность ±0,1 дБ"
    assert judged(0.61).status == "fail" and judged(0.61).short == "не соответствует норме выше 3 ГГц"
    # After the repeat: ≤ 0,5 + 0,1 dB conforms, above it does not.
    assert [judged(value, repeated=True).status for value in (0.45, 0.55, 0.6, 0.61)] == ["pass", "pass", "pass", "fail"]


def test_vswr_limit_within_the_uncertainty_is_not_judged():
    from app.modules.production.instrument_norms import Norm, Segment

    tight = Norm("Лист", "", 0.0, (Segment(18, 1.015),))  # 1,015 − 1 ≤ U(1,015) = 0,0186: the VNA cannot resolve it
    result = assess(summary({0.01: 1.01, 6: 1.012}), tight, uncertainty_for("S50180"))
    assert result.status == "below_accuracy"
    assert result.verdict == "Норма ниже точности ВАЦ — заключение о соответствии не выдаётся"
    assert result.brief == "КСВН 1,012 (норма 1,015 ниже точности ВАЦ — не оценивается)"
    # 1,02 − 1 = 0,02 > U(1,02) = 0,0187: resolvable now that U is computed for the norm value.
    assert assess(summary({0.01: 1.01, 6: 1.015}), Norm("Лист", "", 0.0, (Segment(18, 1.02),)),
                  uncertainty_for("S50180")).status == "border"


@pytest.mark.parametrize("limit", [1.10, 1.20, 1.30])
@pytest.mark.parametrize("ghz,stated", [(5, 0.022), (10, 0.022), (10.01, 0.029), (12, 0.029)])
def test_vswr_uncertainty_is_computed_for_the_norm_value(limit, ghz, stated):
    """СТО ФРАК-002-2026, 4.1 изм. 1 и 13.3: U(КСВН) = U(1,2) · КСВН / 1,2; the 10 GHz edge belongs to the lower band."""
    from app.modules.production.instrument_norms import Norm, Segment

    u, norm = uncertainty_for("S50180"), Norm("Лист", "", 0.0, (Segment(18, limit),))
    expected = stated * limit / 1.2
    assert u.for_value(ghz, limit) == pytest.approx(expected)
    inside = assess(summary({0.01: 1.01, ghz: limit + expected - 1e-4}), norm, u)
    assert inside.status == "border" and inside.uncertainty == pytest.approx(expected)
    assert assess(summary({0.01: 1.01, ghz: limit + expected - 1e-4}), norm, u, repeated=True).status == "pass"
    assert assess(summary({0.01: 1.01, ghz: limit + expected + 1e-4}), norm, u).status == "fail"
    assert assess(summary({0.01: 1.01, ghz: limit - expected - 1e-4}), norm, u).status == "pass"


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


def test_passport_conclusion_is_short_and_reports_an_unresolvable_loss():
    # SMA–SMA by /55: the loss limit (0,03·√F ≤ 0,127 dB) is within 2·U of the S50180 — reported, not judged.
    result = _conclusion([(0.01, 0.01, 0.99), (9, 0.01, 0.99), (18, 0.02, 0.98)])
    lines = dict(result.lines)
    assert result.title == "Заключение"
    assert result.verdict == "Соответствует норме в полосе 9–18 ГГц"
    assert lines["Норма"].startswith("MIL-PRF-55339/55 — КСВН ≤ 1,05 + 0,005·F в полосе 0,5–27 ГГц; потери ≤ 0,03·√F дБ")
    assert lines["Результат"] == ("КСВН ≤ 1,041 (18 ГГц); потери 0,175 дБ (норма 0,127 дБ ниже точности ВАЦ — "
                                  "не оценивается)")
    assert lines[""] == "Пригоден до 18 ГГц" and lines["Методика"] == "СТО ФРАК-002-2026"
    assert set(lines) == {"Норма", "Результат", "", "Методика"}
    assert result.closing == ("Утвердил: Генеральный директор ______________ (подпись)   «____» __________ 20___ г.",)


@pytest.mark.parametrize("repeat_vswr,after", [(1.10, "Соответствует норме"), (1.165, "Соответствует норме"),
                                                (1.175, "Не соответствует норме")])
def test_a_border_asks_for_three_connections_then_the_worst_decides_within_limit_plus_u(repeat_vswr, after):
    from app.modules.production.instrument_characterization import characterization
    from app.modules.production.instrument_public import conclusion, public_passport
    from tests.production.test_instrument_passport import instrument, rf

    def gamma(vswr):
        return (vswr - 1) / (vswr + 1)

    # /55 at 18 GHz: limit 1,14, U ±0,029 → the decision zone ends at 1,169.
    first = characterization(_s2p([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, gamma(1.15), 0.999)]))
    once = conclusion(public_passport(instrument(), rf(), first))
    assert once.verdict == "На границе допуска (КСВН) выше 9 ГГц — нужен повторный замер (3 подключения с поворотом 120°)"
    repeat = characterization(_s2p([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, gamma(repeat_vswr), 0.999)]))
    final = conclusion(public_passport(instrument(), rf(), first, repeats=(repeat, repeat)))
    assert final.verdict.startswith(after)
    assert "погрешност" not in final.verdict and "границ" not in final.verdict  # only the verdict, no extra wording
    assert (dict(final.lines).get("") == "Пригоден до 18 ГГц") == (after == "Соответствует норме")
    two = conclusion(public_passport(instrument(), rf(), first, repeats=(repeat,)))
    # Two connections are not the three the rule asks for; a connection beyond limit + U fails at once.
    assert two.verdict.startswith("На границе допуска" if repeat_vswr <= 1.169 else "Не соответствует норме")


def test_loss_failure_overrides_a_vswr_border():
    from app.modules.production.instrument_characterization import application
    from app.modules.production.instrument_norms import LossNorm, LossSegment, Norm, Segment
    from app.modules.production.instrument_public import combined_verdict

    norm = Norm("Лист", "", 0.0, (Segment(18, 1.10),), loss=LossNorm("Лист", 0.0, (LossSegment(18, 0.5),)))
    measured = summary({0.01: 1.02, 3: 1.02, 9: 1.09}, {0.01: 0.01, 3: 0.02, 9: 0.9})
    usage = application(measured, (), norm, uncertainty=uncertainty_for("S50180"),
                        loss_uncertainty=loss_uncertainty_for("S50180"))
    assert usage.check.status == "border" and usage.loss_check.status == "fail"
    assert combined_verdict(usage.check, usage.loss_check) == "Не соответствует норме по вносимым потерям выше 3 ГГц"
    assert usage.fit_ghz == 3


def test_loss_is_judged_on_the_worse_of_s21_and_s12():
    """СТО ФРАК-002-2026 п. 13.1: a clean S21 does not hide an S12 over the norm; label steps and passport lines too."""
    from app.modules.production.instrument_characterization import application
    from app.modules.production.instrument_norms import LossNorm, LossSegment, Norm, Segment
    from app.modules.production.instrument_passport import band_rows

    norm = Norm("Лист", "", 0.0, (Segment(18, 1.30),), loss=LossNorm("Лист", 0.0, (LossSegment(18, 0.5),)))
    vswr = {0.01: 1.02, 3: 1.02, 9: 1.02}
    forward = {0.01: 0.01, 3: 0.02, 9: 0.05}
    measured = summary(vswr, forward, reverse_loss_by_ghz={0.01: 0.01, 3: 0.02, 9: 0.9})
    clean = summary(vswr, forward)
    loss = assess_loss(measured, norm.loss, loss_uncertainty_for("S50180"))
    assert loss.status == "fail" and loss.at_ghz == 9 and loss.loss == pytest.approx(0.9)
    assert assess_loss(clean, norm.loss, loss_uncertainty_for("S50180")).status == "pass"
    usage = application(measured, (), norm, uncertainty=uncertainty_for("S50180"), loss_uncertainty=loss_uncertainty_for("S50180"))
    assert usage.steps[-1].loss_db == pytest.approx(0.9) and usage.fit_ghz == 3
    assert measured.loss_line == "Потери ≤ 0,91 дБ"  # 0,9 dB rounded up
    assert list(band_rows(measured))[-1][-1] == "0,91"
    swapped = summary(vswr, {0.01: 0.01, 3: 0.02, 9: 0.9}, reverse_loss_by_ghz=forward)
    assert assess_loss(swapped, norm.loss, loss_uncertainty_for("S50180")).loss == pytest.approx(0.9)


def test_both_failures_are_named():
    result = _conclusion([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, 0.2, 0.95)])
    assert result.verdict == "Не соответствует норме выше 9 ГГц"  # the /55 loss is below the VNA's accuracy


def test_history_names_vswr_and_loss():
    from app.modules.production.instrument_characterization import characterization
    from app.modules.production.instrument_public import history_entry
    from tests.production.test_instrument_passport import instrument, rf

    entry = history_entry(instrument(), rf(), characterization(_s2p([(0.01, 0.01, 0.999), (9, 0.01, 0.999), (18, 0.01, 0.99)])))
    assert entry.summary == ("КСВН: соответствует норме до 18 ГГц; потери: норма ниже точности ВАЦ — не оценивается; "
                             "пригоден до 18 ГГц")