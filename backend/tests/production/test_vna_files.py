import math
from pathlib import Path

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.vna_files import VSWR_CEILING, VnaFileError, parse_vna_file, vna_header, vswr_from_gamma

ARINST = Path(__file__).parent / "fixtures" / "arinst_vna_dl.s1p"


def test_real_arinst_vna_dl_export():
    """Выгрузка Arinst VNA-DL (22.10.2026): шапка «!…», «# Hz S RI R 50», строки через табуляцию; 1 МГц…3 ГГц.
    Независимый расчёт: максимум КСВН 1,747 около 2274 МГц (экран прибора: 1,74 на 2279,52 МГц), 1,316 около 1000 МГц."""
    content = ARINST.read_bytes()
    points = parse_vna_file(content, ARINST.name)
    assert len(points) == 1000
    assert points[0][0] == pytest.approx(1e6) and points[-1][0] == pytest.approx(3e9)
    frequency, vswr = max(points, key=lambda point: point[1])
    assert vswr == pytest.approx(1.747, abs=5e-4)
    assert frequency == pytest.approx(2274e6, abs=3.5e6)
    near_1ghz = min(points, key=lambda point: abs(point[0] - 1e9))
    assert near_1ghz[1] == pytest.approx(1.316, abs=5e-4)
    # Расширение не нужно: строка опций узнаётся сама.
    assert parse_vna_file(content, "") == points
    assert vna_header(content) == {"start_hz": 1e6, "stop_hz": 3e9, "points": 1000, "if_bandwidth_hz": 1000}


def test_header_is_optional_and_tolerates_other_units():
    assert vna_header(b"# MHz S RI R 50\n1 0 0\n") == {}
    assert vna_header("Полоса ПЧ: x\n!Bandwidth (kHz): 10\n!Start (MHz) = 1,5\n".encode()) == {
        "if_bandwidth_hz": 10_000, "start_hz": 1.5e6,
    }
    assert vna_header(b"\x00\x01") == {}


def _gamma(vswr: float) -> float:
    return (vswr - 1) / (vswr + 1)


def _approx(points, expected):
    assert len(points) == len(expected)
    for (frequency, vswr), (want_frequency, want_vswr) in zip(points, expected):
        assert frequency == pytest.approx(want_frequency)
        assert vswr == pytest.approx(want_vswr, abs=1e-6)


def test_vswr_from_gamma_and_ceiling():
    assert vswr_from_gamma(0) == 1
    assert vswr_from_gamma(_gamma(1.5)) == pytest.approx(1.5)
    assert vswr_from_gamma(1.0) == VSWR_CEILING
    assert vswr_from_gamma(1.2) == VSWR_CEILING
    with pytest.raises(VnaFileError):
        vswr_from_gamma(-0.1)


def test_touchstone_ri_in_mhz_with_comments():
    g = _gamma(1.25)
    content = (
        "! Arinst VNA-DL\n"
        "! S11 measurement\n"
        "# MHz S RI R 50\n"
        f"1000 {g:.9f} 0  ! comment after data\n"
        f"2000 0 {-g:.9f}\n"
        f"3000 {g / math.sqrt(2):.9f} {g / math.sqrt(2):.9f}\n"
    ).encode()
    _approx(parse_vna_file(content, "cable.s1p"), [(1e9, 1.25), (2e9, 1.25), (3e9, 1.25)])


def test_touchstone_db_and_ma_formats_and_units():
    db = (f"# GHz S DB R 50\n1.0 {20 * math.log10(_gamma(1.5)):.9f} 45\n2.5 {20 * math.log10(_gamma(2)):.9f} -10\n").encode()
    _approx(parse_vna_file(db, "a.s1p"), [(1e9, 1.5), (2.5e9, 2.0)])
    ma = f"# kHz S MA R 50\n500 {_gamma(1.1):.9f} 0\n1500 {_gamma(1.3):.9f} 90\n".encode()
    _approx(parse_vna_file(ma, "a.s1p"), [(5e5, 1.1), (1.5e6, 1.3)])
    hz = f"# HZ S ri R 75\n1E9 {_gamma(1.2):.9f} 0\n2E9 {_gamma(1.4):.9f} 0\n".encode()
    _approx(parse_vna_file(hz, "a.S1P"), [(1e9, 1.2), (2e9, 1.4)])


def test_touchstone_without_option_line_uses_ghz_ma_default():
    content = f"! no options\n1 {_gamma(1.2):.9f} 0\n2 {_gamma(1.6):.9f} 10\n".encode()
    _approx(parse_vna_file(content, "plain.s1p"), [(1e9, 1.2), (2e9, 1.6)])


def test_touchstone_two_port_takes_s11():
    content = (
        "# GHz S MA R 50\n"
        f"1 {_gamma(1.3):.9f} 0 0.9 -30 0.9 -30 0.1 0\n"
        f"2 {_gamma(1.5):.9f} 0 0.8 -60 0.8 -60 0.1 0\n"
    ).encode()
    _approx(parse_vna_file(content, "a.s2p"), [(1e9, 1.3), (2e9, 1.5)])


def test_touchstone_rejects_non_s_parameters():
    with pytest.raises(VnaFileError, match="не S-параметры"):
        parse_vna_file(b"# MHz Z RI R 50\n1 50 0\n2 50 0\n", "z.s1p")


def test_csv_semicolon_decimal_comma_russian_header_mhz():
    content = "Частота, МГц;КСВН\n1000;1,25\n2400,5;1,40\n3000;1,10\n".encode("cp1251")
    _approx(parse_vna_file(content, "замер.csv"), [(1e9, 1.25), (2400.5e6, 1.40), (3e9, 1.10)])


def test_csv_comma_separator_with_dot_decimal_and_hz_header():
    content = b"Frequency (Hz),VSWR\n1000000000,1.25\n2000000000,1.5\n"
    _approx(parse_vna_file(content, "a.csv"), [(1e9, 1.25), (2e9, 1.5)])


def test_tab_separated_ghz_s11_db_negative():
    content = f"Freq [GHz]\tS11 [dB]\n1,0\t{20 * math.log10(_gamma(1.2)):.6f}\n2,0\t{20 * math.log10(_gamma(1.8)):.6f}\n"
    _approx(parse_vna_file(content.encode(), "a.txt"), [(1e9, 1.2), (2e9, 1.8)])


def test_return_loss_positive_db_is_flipped():
    rl = [-20 * math.log10(_gamma(v)) for v in (1.15, 1.35)]
    content = f"Частота (кГц);Обратные потери (дБ)\n100000;{rl[0]:.6f}\n200000;{rl[1]:.6f}\n".replace(".", ",")
    _approx(parse_vna_file(content.encode(), "a.csv"), [(1e8, 1.15), (2e8, 1.35)])
    english = f"Freq(MHz);Return Loss(dB)\n100;{rl[0]:.6f}\n200;{rl[1]:.6f}\n"
    _approx(parse_vna_file(english.encode(), "a.csv"), [(1e8, 1.15), (2e8, 1.35)])


def test_complex_re_im_columns():
    g = _gamma(1.5)
    content = f"f, MHz;Re S11;Im S11\n100;{g:.9f};0\n200;0;{-g:.9f}\n".encode()
    _approx(parse_vna_file(content, "a.csv"), [(1e8, 1.5), (2e8, 1.5)])


def test_headerless_columns_frequency_unit_by_range():
    # Без заголовка: > 10⁶ — Гц, от 100 — МГц, меньше — ГГц; КСВ (все ≥ 1) или S11 дБ (отрицательные).
    _approx(parse_vna_file(b"1000000000 1.2\n2000000000 1.3\n", "a.txt"), [(1e9, 1.2), (2e9, 1.3)])
    _approx(parse_vna_file(b"1000 1,2\n8800 1,3\n", "a.txt"), [(1e9, 1.2), (8.8e9, 1.3)])
    db = f"1,0;{20 * math.log10(_gamma(1.4)):.6f}\n8,8;{20 * math.log10(_gamma(1.6)):.6f}\n".replace(".", ",")
    _approx(parse_vna_file(db.encode(), "a.csv"), [(1e9, 1.4), (8.8e9, 1.6)])


def test_points_come_sorted_by_frequency_and_metadata_lines_skipped():
    content = "Arinst VNA-DL\nStart: 1 MHz\n\nFreq(MHz);SWR\n300;1,3\n100;1,1\n200;1,2\n".encode()
    _approx(parse_vna_file(content, "a.csv"), [(1e8, 1.1), (2e8, 1.2), (3e8, 1.3)])


@pytest.mark.parametrize("content, message", [
    (b"", "пустой"),
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00", "не текстовый"),
    ("Это просто письмо\nбез чисел\n".encode(), "не похож на выгрузку ВАЦ"),
    (b"Freq;VSWR\n100;1.2\n", "не похож на выгрузку ВАЦ"),
    (b"Freq(MHz);VSWR\n100;0,5\n200;0,7\n", "меньше 1"),
    (b"1 0,5\n2 3\n", "Не понять"),
])
def test_garbage_is_a_russian_domain_error(content, message):
    with pytest.raises(VnaFileError, match=message) as error:
        parse_vna_file(content, "x.csv")
    assert isinstance(error.value, ProductionValidationError)


def test_touchstone_garbage_line_is_named():
    with pytest.raises(VnaFileError, match="Строка 3"):
        parse_vna_file(b"# MHz S RI R 50\n100 0.1 0\nfoo bar baz\n", "a.s1p")
