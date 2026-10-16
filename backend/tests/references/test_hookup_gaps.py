"""Reference-data gaps of 14.10.2026, closed 16.10.2026 (owner: «"примерно" не надо, будем на деле проверять»): the series
МК 26-13 (strand lay, colours, Ø, standard) by the Chuvashkabel technical description and the colour of МГТФ in
`hookup-wires.json` — plain values, no «примерно» anywhere in the file. They load with `--hookups --fill-empty` (the plain mode of the whole file moves 22 marks out of
«монтажные» and rewrites filled cells, see test_hookup_fill_empty.py); that mode fills the МГТФ rows that exist and refuses
the МК 26-13 sizes until БД.ПРОВ has their rows."""

from pathlib import Path

from app.modules.references.importers import FILL_EMPTY_NO_ROW, import_hookups
from tests.references.test_hookup_fill_empty import (  # noqa: F401  (fixtures and helpers)
    ACTOR, NOW, _factory, _fields, _load, _prod_like, sessions, uow,
)

FULL = Path(__file__).resolve().parents[2] / "data" / "cables" / "hookup-wires.json"
SERIES_NULLS = ("standard", "conductor_material", "insulation_material", "voltage_v", "temp_min_c", "temp_max_c")


def test_mk_26_13_and_the_mgtf_colour_come_from_the_makers_description_and_say_nothing_approximate():
    assert "примерно" not in FULL.read_text(encoding="utf-8")
    series = {item["key"]: item for item in _load(FULL)["series"]}
    mk, mgtf = series["МК 26-13"], series["МГТФ"]
    for name in SERIES_NULLS + ("colors",):
        assert "chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf" in mk[name]["source"], name
    for size in mk["sizes"]:
        for name in ("strands", "outer_dia_mm"):
            assert "chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf" in size[name]["source"], (size["mark"], name)
        assert size["conductor_dia_mm"] is None and size["insulation_thickness_mm"] is None  # no source, not made up
    assert mgtf["colors"]["value"] == ["натуральный"]
    assert "МК 26-13" in mgtf["colors"]["source"]  # the coloured fluoroplastic wire is МК 26-13, МГТФ is not dyed


def test_fill_empty_gives_the_mgtf_row_its_colour_marked_approximate_and_refuses_mk_until_its_rows_exist(tmp_path, uow):
    _prod_like(tmp_path, uow)
    before = _fields(uow)
    report = import_hookups(_factory(uow)(), _load(FULL), ACTOR, NOW, dry_run=True, fill_empty=True)
    assert report.moved_out_of_montage == [] and report.created == []
    mk = [item for item in report.refused if item["key"].startswith("МК 26-13")]
    assert len(mk) == 5 and {item["reason"] for item in mk} == {FILL_EMPTY_NO_ROW}
    assert _fields(uow) == before  # a dry run writes nothing
