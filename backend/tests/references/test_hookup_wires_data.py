"""Справочник монтажных проводов `backend/data/cables/hookup-wires.json`: файл читается и держит форму данных."""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "cables" / "hookup-wires.json"

SERIES_P = ("standard", "conductor_material", "insulation_material", "voltage_v", "temp_min_c", "temp_max_c", "colors")
SIZE_P = ("strands", "conductor_dia_mm", "insulation_thickness_mm", "outer_dia_mm")
NUMERIC = {"voltage_v", "temp_min_c", "temp_max_c", "conductor_dia_mm", "insulation_thickness_mm", "outer_dia_mm"}
SERIES_KEYS = {"UL1061", "UL1571", "UL1007", "UL3239", "UL3135", "НВ-4 (600 В)", "ПВАМ", "МГТФ", "МГШВ", "МС 16-13", "МС 15-11", "МК 26-13", "ПуГВ"}


def _load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _check_p(name, p):
    assert p is None or (isinstance(p, dict) and set(p) == {"value", "source"}), name
    if p is not None:
        assert isinstance(p["source"], str) and p["source"].strip(), name
        if name in NUMERIC:
            assert isinstance(p["value"], (int, float)) and not isinstance(p["value"], bool), name


def test_file_parses_and_lists_exactly_the_thirteen_series():
    doc = _load()
    assert doc["version"] == 1
    assert {s["key"] for s in doc["series"]} == SERIES_KEYS


def test_every_value_is_null_or_sourced_and_numbers_are_numbers():
    doc = _load()
    for g in doc["gauges"]:
        assert g["awg"] is None or isinstance(g["awg"], str)
        assert isinstance(g["section_mm2"], (int, float)) and g["section_mm2"] > 0
        _check_p("conductor_dia_mm", g["conductor_dia_mm"])
        assert g["source"].strip()
    for s in doc["series"]:
        assert s["aliases"] and all(isinstance(a, str) for a in s["aliases"])
        for f in SERIES_P:
            _check_p(f, s[f])
        assert s["sizes"]
        for z in s["sizes"]:
            assert z["mark"].strip() and isinstance(z["section_mm2"], (int, float)) and z["section_mm2"] > 0
            for f in SIZE_P:
                _check_p(f, z[f])
            for f in ("voltage_v", "temp_min_c", "temp_max_c", "colors"):
                if f in z:
                    _check_p(f, z[f])


def test_marks_are_unique_and_was_points_at_a_known_series_or_mark():
    doc = _load()
    marks = [z["mark"] for s in doc["series"] for z in s["sizes"]]
    assert len(marks) == len(set(marks))
    aliases = [a.replace(" ", "") for s in doc["series"] for a in s["aliases"]]
    for w in doc["was"]:
        assert w["old_from"] in ("БД.ПРОВ", "СПР.КАБ Ø изоляции")
        assert w["source"].strip() and w["new"] is not None
        assert any(w["mark"].replace(" ", "").startswith(a) for a in aliases), w["mark"]


def test_strand_lay_and_series_colours_are_filled_where_a_source_gives_them():
    """T6 (13.10.2026): скрутка у НВ-4, ПВАМ, МС 15-11, ПуГВ, МГШВ — у каждого размера; цвета серий ПВАМ и ПуГВ.
    МК 26-13 (скрутка, цвета) и цвета МГТФ первичного источника не имеют — остаются пустыми, а не додуманными."""
    series = {s["key"]: s for s in _load()["series"]}
    for key in ("НВ-4 (600 В)", "ПВАМ", "МС 15-11", "ПуГВ", "МГШВ"):
        assert all(size["strands"] and size["strands"]["value"].strip() for size in series[key]["sizes"]), key
    assert {size["strands"]["value"] for size in series["МС 15-11"]["sizes"]} == {
        "многопроволочная, медь лужёная, класс 4 по ГОСТ 22483", "многопроволочная, медь лужёная, класс 5 по ГОСТ 22483"}
    for key in ("ПВАМ", "ПуГВ"):
        colors = series[key]["colors"]["value"]
        assert len(colors) >= 11 and len(set(colors)) == len(colors) and "чёрный" in colors, key
    # 14.10.2026 (владелец: «найди значения сам или поставь примерные»): МК 26-13 and the МГТФ colour are filled, each
    # one marked «примерно» in its source — never passed off as a first-hand value.
    mk = series["МК 26-13"]
    assert len(mk["colors"]["value"]) == 8 and mk["colors"]["source"].startswith("примерно: ")
    assert mk["standard"]["value"] == "ТУ 16.К05-016-2003" and mk["standard"]["source"].startswith("примерно: ")
    for size in mk["sizes"]:
        assert size["strands"]["source"].startswith("примерно: ") and size["outer_dia_mm"]["source"].startswith("примерно: ")
    assert [size["strands"]["value"] for size in mk["sizes"]] == ["14×0,08", "24×0,08", "19×0,12", "19×0,15", "19×0,18"]
    assert series["МГТФ"]["colors"]["value"] == ["белый"] and series["МГТФ"]["colors"]["source"].startswith("примерно: ")
