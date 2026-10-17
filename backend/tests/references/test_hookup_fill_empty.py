"""База монтажных проводов «только в пустые ячейки» (owner 13.10.2026): a prod dry run of hookup-wires.json would have
rewritten 63 filled cells (Стандарт «UL 758; AWM Style 1061» → «UL 758, AWM Style 1061», the real colours of ПВАМ 0,35
→ the series palette, «Источник данных», one strand lay) and listed 22 marks as gone from монтажные. The fix: the
import mode `fill_empty` and the payload `hookup-wires.strands-colors.json` (strand lay of НВ-4, ПВАМ, МС 15-11, ПуГВ,
МГШВ and the series colours of ПВАМ / ПуГВ — what commit 1bdc8bd2 added). The fixture rebuilds the prod state: those
cells already filled, the marks outside the base present."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references import load_cables
from app.modules.references.application import SaveGaugeCommand, cable_catalog, save_gauge, structure_hookups
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import FILL_EMPTY_NO_ROW, hookup_seed, import_hookups
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.references.test_cable_structure import _seed_gauges
from tests.references.test_cables import _mirror
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)

DATA = Path(__file__).resolve().parents[2] / "data" / "cables"
FULL = DATA / "hookup-wires.json"
CUT = DATA / "hookup-wires.strands-colors.json"
STRAND_SERIES = ("НВ-4 (600 В)", "ПВАМ", "МС 15-11", "ПуГВ", "МГШВ")
COLOUR_SERIES = ("ПВАМ", "ПуГВ")

HEADER = ["Марка", "Тип провода", "Стандарт", "Сечение, мм2", "AWG", "Жила: проволок × диаметр, мм", "Число жил",
          "Цвета", "Наружный диаметр, мм", "Изоляция", "Темп. от, °C", "Темп. до, °C", "Напряжение, В",
          "Источник данных"]
# What prod holds: cells the earlier loads and people filled, cells still empty, marks outside the base.
ROWS = [
    ["UL1061 28AWG", "монтажный", "UL 758; AWM Style 1061", "0,08", "28", "7×0,127", "1", "", "0,89", "ПВХ", "-10",
     "80", "300", "Справочник"],
    ["ПВАМ 0,35", "силовой", "", "0,35", "", "", "1", "красный", "", "ПВХ", "", "", "", "Склад ЦАР"],
    ["ПВАМ 0,50", "силовой", "", "0,5", "", "", "1", "", "1,6", "ПВХ", "-40", "105", "48 (ном.) / 1000 (испыт.)", ""],
    ["НВ-4 0,20", "монтажный", "ГОСТ 17515-72", "0,20", "", "7×0,2", "1", "", "", "", "", "", "", "ГОСТ 17515-72"],
    ["МС 15-11 0,12", "монтажный", "", "0,12", "", "", "1", "", "", "", "", "", "", "ТУ 16-505.083-78"],
    ["ПуГВ 1,0", "силовой", "", "1", "", "", "1", "", "", "", "", "", "", ""],
    ["M22759/16 26AWG", "монтажный (AWM)", "SAE AS22759/16", "0,12", "26", "", "1", "", "", "", "", "", "", ""],
    ["TXL 10AWG", "монтажный", "", "5,26", "10", "", "1", "", "", "", "", "", "", ""],
    ["FLRY-B 0,12", "монтажный", "", "0,12", "", "", "1", "", "", "", "", "", "", ""],
    ["GXL Core 0,3", "силиконовый (аккумуляторный/RC)", "", "0,3", "", "", "", "", "", "", "", "", "", ""],
    ["Провод ПВКВ 0,75мм2 8мм", "монтажный", "", "0,75", "", "", "1", "", "8", "", "", "", "", ""],
]
PROV = "\n".join("\t".join(row) for row in [HEADER, *ROWS]) + "\n"
OUTSIDE = {"M22759/16 26AWG", "TXL 10AWG", "FLRY-B 0,12", "GXL Core 0,3", "Провод ПВКВ 0,75мм2 8мм"}
STRANDS = "Жила: проволок × диаметр, мм"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _factory(uow):
    return lambda: SqlAlchemyReferencesUnitOfWork(uow.session_factory)  # noqa: E731


def _prod_like(tmp_path, uow):
    load_mirror(_mirror(tmp_path, prov=PROV), uow, dry_run=False, now=NOW)
    _seed_gauges(uow)
    save_gauge(SaveGaugeCommand(None, "24", "0,2", "0,51", ACTOR, NOW), uow)
    structure_hookups(ACTOR, NOW, _factory(uow))


def _fields(uow) -> dict[str, dict]:
    with uow:
        return {str(record.fields.get("Марка")): dict(record.fields)
                for record in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=1000)}


def _state(uow):
    catalog = cable_catalog(_factory(uow)())
    return _fields(uow), catalog.hookups, {row.cable: row.kind for row in catalog.rows}, len(catalog.gauges)


def _only_empty_to_value(report) -> None:
    for item in report.updated:
        for what, (old, new) in item["changes"].items():
            assert old == "" and new, (item["key"], what, old, new)


# --- the payload ---

def test_the_payload_is_exactly_the_strands_and_colours_of_the_full_file():
    full = {item["key"]: item for item in _load(FULL)["series"]}
    cut = _load(CUT)
    assert cut["gauges"] == [] and [item["key"] for item in cut["series"]] == [key for key in full if key in STRAND_SERIES]
    assert set(STRAND_SERIES) <= set(full)
    for item in cut["series"]:
        source = full[item["key"]]
        assert item["colors"] == (source["colors"] if item["key"] in COLOUR_SERIES else None)
        assert all(item[name] is None for name in ("standard", "conductor_material", "insulation_material",
                                                   "voltage_v", "temp_min_c", "temp_max_c"))
        assert [size["mark"] for size in item["sizes"]] == [size["mark"] for size in source["sizes"]]
        for size, whole in zip(item["sizes"], source["sizes"]):
            assert (size["awg"], size["section_mm2"], size["strands"]) == (whole["awg"], whole["section_mm2"], whole["strands"])
            assert size["strands"] and size["strands"]["value"].strip(), size["mark"]
            assert size["conductor_dia_mm"] is size["insulation_thickness_mm"] is size["outer_dia_mm"] is None
            assert not {"voltage_v", "temp_min_c", "temp_max_c", "colors"} & set(size)
    seed = hookup_seed(cut)
    assert seed.refused == () and seed.sizes == sum(len(item["sizes"]) for item in cut["series"])


# --- the prod state: what the full load would do, what the fill does ---

def test_the_full_load_rewrites_filled_cells_and_lists_marks_outside_the_base(tmp_path, uow):
    """The prod dry run, rebuilt: why the plain mode is not for this load."""
    _prod_like(tmp_path, uow)
    report = import_hookups(_factory(uow)(), _load(FULL), ACTOR, NOW, dry_run=True)
    changes = {item["key"]: item["changes"] for item in report.updated}
    assert changes["UL1061 28AWG"]["Стандарт"][0] == "UL 758; AWM Style 1061"
    assert changes["ПВАМ 0,35"]["Цвета"][0] == "красный"
    assert changes["НВ-4 0,20"][STRANDS][0] == "7×0,2"
    assert changes["ПВАМ 0,35"]["Источник данных"][0] == "Склад ЦАР"
    assert set(report.moved_out_of_montage) == OUTSIDE


@pytest.mark.parametrize("payload", [CUT, FULL], ids=["strands-colors", "full-file"])
def test_fill_empty_dry_run_shows_only_empty_to_value_and_moves_nothing(tmp_path, uow, payload):
    _prod_like(tmp_path, uow)
    before = _state(uow)

    report = import_hookups(_factory(uow)(), _load(payload), ACTOR, NOW, dry_run=True, fill_empty=True)

    assert report.dry_run and report.fill_empty and _state(uow) == before
    assert report.moved_out_of_montage == [] and report.created == [] and report.gauges_added == []
    assert report.updated and report.counts()["moved_out_of_montage"] == 0
    _only_empty_to_value(report)
    touched = {item["key"] for item in report.updated}
    assert not touched & OUTSIDE
    assert all(item["reason"] for item in report.refused)
    if payload == CUT:
        assert {item["reason"] for item in report.refused} == {FILL_EMPTY_NO_ROW, "размера нет в таблице сечений"}
        allowed = {STRANDS, "Цвета", "Источник данных"}
        assert {what for item in report.updated for what in item["changes"]} <= allowed


def test_fill_empty_writes_only_the_empty_cells_and_a_second_run_changes_nothing(tmp_path, uow):
    _prod_like(tmp_path, uow)
    fields, hookups, kinds, gauges = _state(uow)

    report = import_hookups(_factory(uow)(), _load(CUT), ACTOR, NOW, fill_empty=True)

    after, hookups_after, kinds_after, gauges_after = _state(uow)
    assert kinds_after == kinds and gauges_after == gauges and hookups_after == hookups  # no structure value in the cut
    assert {item["key"] for item in report.updated} == {"ПВАМ 0,35", "ПВАМ 0,50", "МС 15-11 0,12", "ПуГВ 1,0"}
    # filled cells stay exactly as they were
    assert after["ПВАМ 0,35"]["Цвета"] == "красный" and after["ПВАМ 0,35"]["Источник данных"] == "Склад ЦАР"
    assert after["НВ-4 0,20"] == fields["НВ-4 0,20"] and after["UL1061 28AWG"] == fields["UL1061 28AWG"]
    assert after["МС 15-11 0,12"]["Источник данных"] == "ТУ 16-505.083-78"
    for mark in OUTSIDE:
        assert after[mark] == fields[mark]
    # empty cells got the file's values, «Источник данных» only where it was blank — with the sources of the fill
    cut = {item["key"]: item for item in _load(CUT)["series"]}
    pvam = after["ПВАМ 0,50"]
    assert pvam[STRANDS] == cut["ПВАМ"]["sizes"][1]["strands"]["value"]
    assert pvam["Цвета"] == ", ".join(cut["ПВАМ"]["colors"]["value"])
    assert pvam["Источник данных"] == cut["ПВАМ"]["colors"]["source"]  # strands and colours: one source
    assert pvam["Напряжение, В"] == "48 (ном.) / 1000 (испыт.)" and pvam["Наружный диаметр, мм"] == "1,6"
    assert after["ПВАМ 0,35"][STRANDS] and after["МС 15-11 0,12"][STRANDS].startswith("многопроволочная")
    pugv = after["ПуГВ 1,0"]
    assert pugv[STRANDS] and pugv["Цвета"] == ", ".join(cut["ПуГВ"]["colors"]["value"]) and pugv["Источник данных"]
    assert all(pugv[name] == "" for name in ("Стандарт", "Наружный диаметр, мм", "Изоляция", "Напряжение, В"))

    again = import_hookups(_factory(uow)(), _load(CUT), ACTOR, NOW, fill_empty=True)
    assert (again.changed, again.updated, again.hookups_created, again.hookups_updated) == (0, [], 0, 0)
    assert _state(uow) == (after, hookups_after, kinds_after, gauges_after)


def test_fill_empty_fills_only_unknown_structure_values_and_keeps_its_series_and_size(tmp_path, uow):
    _prod_like(tmp_path, uow)
    _, hookups, _, _ = _state(uow)
    ul = hookups["ul106128awg"]
    assert ul.conductor_material == "" and ul.temp_min_c == -10

    report = import_hookups(_factory(uow)(), _load(FULL), ACTOR, NOW, fill_empty=True)

    _, after, _, _ = _state(uow)
    changes = next(item["changes"] for item in report.updated if item["key"] == "UL1061 28AWG")
    assert {what for what in changes if what.startswith("структура")} == {
        "структура: материал жилы", "структура: толщина изоляции, мм"}
    assert {what for what in changes if not what.startswith("структура")} == {"Цвета"}  # the only empty cell
    wire = after["ul106128awg"]
    assert (wire.series, wire.gauge_id, wire.temp_min_c, wire.outer_d_mm) == (ul.series, ul.gauge_id, -10, ul.outer_d_mm)
    assert wire.conductor_material and wire.insulation_thickness_mm
    again = import_hookups(_factory(uow)(), _load(FULL), ACTOR, NOW, fill_empty=True)
    assert (again.changed, again.hookups_updated) == (0, 0)


def test_cli_fill_empty_dry_run_says_so_and_writes_nothing(tmp_path, uow, monkeypatch, capsys):
    _prod_like(tmp_path, uow)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr("app.database.build_engine", lambda url: None)
    monkeypatch.setattr("app.database.build_references_uow_factory", lambda engine: _factory(uow))
    before = _state(uow)

    assert load_cables.main(["--hookups", "--fill-empty", "--dry-run", "--file", str(CUT)]) == 0

    out = capsys.readouterr().out
    assert "проверка, ничего не записано, только пустые ячейки" in out and "не считается" in out
    assert "ПВАМ 0,50: Жила: проволок × диаметр, мм «» → «" in out and "M22759" not in out
    assert _state(uow) == before


# --- HTTP ---

def test_api_fill_empty_dry_run_then_write(tmp_path, sessions):  # noqa: F811
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    _prod_like(tmp_path, uow)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    try:
        client = TestClient(app)
        payload = _load(CUT)
        checked = client.post("/api/references/import/hookups?dry_run=1&fill_empty=1", json=payload)
        assert checked.status_code == 200, checked.text
        body = checked.json()
        assert body["fill_empty"] is True and body["counts"]["moved_out_of_montage"] == 0
        assert all(old == "" for item in body["updated"] for old, _new in item["changes"].values())
        applied = client.post("/api/references/import/hookups?dry_run=0&fill_empty=1", json=payload).json()
        assert {**applied, "dry_run": True} == body
        again = client.post("/api/references/import/hookups?dry_run=1&fill_empty=1", json=payload).json()
        assert again["updated"] == [] and again["counts"]["moved_out_of_montage"] == 0
        assert client.post("/api/references/import/coax?dry_run=1&fill_empty=1", json={"rows": []}).status_code == 422
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        app.dependency_overrides.pop(moscow_now, None)
        del app.state.references_uow_factory
