"""Reference-data gaps of 14.10.2026 (owner: «найди значения сам или поставь примерные»): the series МК 26-13 (strand lay,
colours, Ø, standard) and the colour of МГТФ in `hookup-wires.json`, entered as APPROXIMATE — the source of each such value
starts with «примерно: ». They load with `--hookups --fill-empty` (the plain mode of the whole file moves 22 marks out of
«монтажные» and rewrites filled cells, see test_hookup_fill_empty.py); that mode fills the МГТФ rows that exist and refuses
the МК 26-13 sizes until БД.ПРОВ has their rows."""

from pathlib import Path

from app.modules.references.importers import FILL_EMPTY_NO_ROW, import_hookups
from tests.references.test_hookup_fill_empty import (  # noqa: F401  (fixtures and helpers)
    ACTOR, NOW, _factory, _fields, _load, _prod_like, sessions, uow,
)

FULL = Path(__file__).resolve().parents[2] / "data" / "cables" / "hookup-wires.json"
SERIES_NULLS = ("standard", "conductor_material", "insulation_material", "voltage_v", "temp_min_c", "temp_max_c")


def test_every_approximate_value_says_so_in_its_source_and_nothing_else_of_the_series_changed():
    series = {item["key"]: item for item in _load(FULL)["series"]}
    mk, mgtf = series["МК 26-13"], series["МГТФ"]
    for name in SERIES_NULLS + ("colors",):
        assert mk[name]["source"].startswith("примерно: "), name
    for size in mk["sizes"]:
        for name in ("strands", "outer_dia_mm"):
            assert size[name]["source"].startswith("примерно: "), (size["mark"], name)
        assert size["conductor_dia_mm"] is None and size["insulation_thickness_mm"] is None  # no source, not made up
    assert mgtf["colors"]["source"].startswith("примерно: ")
    assert "МК 26-13" in mgtf["colors"]["source"]  # the coloured МГТФ is sold as МК 26-13


def test_fill_empty_gives_the_mgtf_row_its_colour_marked_approximate_and_refuses_mk_until_its_rows_exist(tmp_path, uow):
    _prod_like(tmp_path, uow)
    before = _fields(uow)
    report = import_hookups(_factory(uow)(), _load(FULL), ACTOR, NOW, dry_run=True, fill_empty=True)
    assert report.moved_out_of_montage == [] and report.created == []
    mk = [item for item in report.refused if item["key"].startswith("МК 26-13")]
    assert len(mk) == 5 and {item["reason"] for item in mk} == {FILL_EMPTY_NO_ROW}
    assert _fields(uow) == before  # a dry run writes nothing
