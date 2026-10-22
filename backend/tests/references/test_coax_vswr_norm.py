"""Норма КСВН коаксиальной сборки по комплектующим (владелец 22.10.2026): КСВ разъёмов из БД.КОАКС, затухание и рабочая
частота кабеля из «Коаксиальных кабелей», никогда не общая «≤1,3»."""

import pytest

from app.modules.references.cable_structure import card_from_seed
from app.modules.references.coax_vswr import (
    attenuation_curve, cable_card, connector_cells, loss_db_m, parse_vswr, vswr_norm,
)


def _row(article: str, vswr: str = "", max_ghz: str = "", wire: str = "RG-316") -> dict:
    return {"coaxArticle": article, "coaxWire": wire, "КСВ": vswr, "Частота до, ГГц": max_ghz}


def _card(name: str = "RG-316", max_ghz: object = 3, points: tuple = ((100, "0,2"), (1000, "0,6325"))):
    return card_from_seed({"name": name, "max_operating_ghz": max_ghz,
                           "attenuation": [{"freq_mhz": freq, "value": value, "unit": "dB/m"} for freq, value in points]}, 0)


ENDS = [("A", "SMA-C174P"), ("B", "SMA-C316F")]


@pytest.mark.parametrize(("text", "value"), [
    ("≤1,43", 1.43), ("<2", 2.0), ("<2 (до 6 ГГц)", 2.0), ("1,3", 1.3), ("1.35", 1.35), (" ≤ 1,2 : 1", 1.2),
    ("", None), ("по запросу", None), ("0,9", None),
])
def test_vswr_cell_is_free_text_with_a_decimal_comma(text, value):
    assert parse_vswr(text) == value


def test_two_connectors_1_35_on_a_short_cable_give_about_1_85():
    rows = [_row("SMA-C174P", "1,35", "6"), _row("SMA-C316F", "≤1,35", "6")]
    norm = vswr_norm(ENDS, rows, None, "RG-316", 150)
    assert norm["status"] == "ok" and norm["vswrMax"] == 1.85
    assert norm["bandMhz"] == [1.0, 6000.0] and norm["lengthM"] == 0.15
    assert norm["cable"] == {"name": "RG-316", "lossDbM": None} and norm["s21Db"] is None


def test_1_43_and_1_41_give_about_2_06_and_the_contract_keys():
    rows = [_row("SMA-C174P", "≤1,43", "6"), _row("SMA-C316F", "≤1,41", "18")]
    norm = vswr_norm(ENDS, rows, _card(), "RG316", "285")
    assert norm["status"] == "ok" and norm["vswrMax"] == 2.06 and norm["reason"] == ""
    assert set(norm) == {"status", "reason", "vswrMax", "bandMhz", "s21Db", "lengthM", "cable", "ends"}
    assert norm["ends"] == [
        {"side": "A", "article": "SMA-C174P", "vswrText": "≤1,43", "vswr": 1.43, "maxGhz": 6.0},
        {"side": "B", "article": "SMA-C316F", "vswrText": "≤1,41", "vswr": 1.41, "maxGhz": 18.0},
    ]
    assert norm["cable"]["name"] == "RG-316"


def test_missing_vswr_is_undefined_with_the_reason_never_a_default():
    rows = [_row("SMA-C174P", "", "6"), _row("SMA-C316F", "≤1,41", "6")]
    norm = vswr_norm(ENDS, rows, None, "RG-316", 285)
    assert norm["status"] == "undefined" and norm["vswrMax"] is None
    assert norm["reason"] == "у SMA-C174P нет КСВ в БД.КОАКС"
    both = vswr_norm(ENDS, [], None, "RG-316", 285)
    assert both["reason"] == "у SMA-C174P, SMA-C316F нет КСВ в БД.КОАКС" and both["bandMhz"] == [1.0, None]
    unreadable = vswr_norm(ENDS, [_row("SMA-C174P", "по запросу"), _row("SMA-C316F", "1,3")], None, "", 0)
    assert unreadable["status"] == "undefined" and "«по запросу»" in unreadable["reason"]


def test_interpolation_is_log_log_and_sqrt_beyond_one_point():
    curve = attenuation_curve(_card().attenuation)
    assert curve == ((100.0, 0.2), (1000.0, 0.6325))
    assert loss_db_m(curve, 400) == pytest.approx(0.4, rel=1e-3)
    assert loss_db_m(curve, 1) == pytest.approx(0.02, rel=1e-3)   # below the points — the slope of the first pair
    assert loss_db_m(curve, 4000) == pytest.approx(1.265, rel=1e-3)
    assert loss_db_m(((100.0, 0.2),), 400) == pytest.approx(0.4)
    assert loss_db_m((), 400) is None
    # Two kinds at one frequency — the smaller loss (the stricter norm).
    two = card_from_seed({"name": "RG-316", "attenuation": [{"freq_mhz": 100, "nominal": "0,3", "max": "0,2", "unit": "dB/m"}]}, 0)
    assert len(two.attenuation) == 2 and attenuation_curve(two.attenuation) == ((100.0, 0.2),)


def test_band_upper_edge_is_the_lowest_limit_and_s21_is_taken_there():
    rows = [_row("SMA-C174P", "1,3", "6"), _row("SMA-C316F", "1,3", "18")]
    norm = vswr_norm(ENDS, rows, _card(max_ghz=3), "RG-316", 1000)
    assert norm["bandMhz"] == [1.0, 3000.0]
    assert norm["cable"]["lossDbM"] == pytest.approx(0.2 * 30 ** 0.5, rel=1e-3)
    assert norm["s21Db"] == round(norm["cable"]["lossDbM"] * 1.0, 2)
    # Нет частот ни у разъёмов, ни у кабеля — верхняя граница не определена, норма есть.
    open_band = vswr_norm(ENDS, [_row("SMA-C174P", "1,3"), _row("SMA-C316F", "1,3")], _card(max_ghz=None), "RG-316", 1000)
    assert open_band["status"] == "ok" and open_band["bandMhz"] == [1.0, None] and open_band["s21Db"] is None
    assert "верхняя граница" in open_band["reason"]


def test_cable_loss_only_tightens_the_norm():
    rows = [_row("SMA-C174P", "1,5", "6"), _row("SMA-C316F", "1,5", "6")]
    lossy = _card(points=((1, "5"), (10, "15")))
    without = vswr_norm(ENDS, rows, None, "RG-316", 10_000)["vswrMax"]
    assert vswr_norm(ENDS, rows, lossy, "RG-316", 10_000)["vswrMax"] < without
    assert vswr_norm(ENDS, rows, lossy, "RG-316", 0)["vswrMax"] == without


def test_disagreeing_rows_give_the_worst_and_a_family_card_is_not_the_cable():
    rows = [_row("SMA-C174P", "1,2", "18", wire="RG-174"), _row("sma-c174p", "≤1,3", "6")]
    assert connector_cells(rows, "SMA-C174P") == ("≤1,3", 1.3, 6.0)
    assert connector_cells(rows, "N-111L") == ("", None, None)
    cards = [_card("RG-58")]
    assert cable_card(cards, "RG 58") is cards[0]
    assert cable_card(cards, "RG-58 A/U") is None
