"""«Ø изоляции по маркам» from the база монтажных проводов (owner 12.10.2026: «вся инфа должна идти из монтажных
кабелей»): the view the ETK generator reads instead of the old СПР.КАБ AWG table, compared with that table."""

import importlib.util
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.application import SaveGaugeCommand, save_gauge, structure_hookups
from app.modules.references.cable_structure import HookupWire, WireGauge, gauge_gost_awg
from app.modules.references.domain import gost_awg_map
from app.modules.references.importers import import_hookups
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.modules.references.technology import TECH_SNAPSHOT_SCHEMA_VERSION, gost_awg_from_wire_table, parse_spr_kab_tsv
from app.modules.references.wire_dia import (
    COLUMNS, OLD_COLUMN, SERIES, old_column, read_wire_dia, wire_dia_records, wire_dia_rows,
)
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows import techcard_build
from app.workflows.techcards_all import generator_refs
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
MIRROR = ROOT / "data" / "reference-mirror"
HOOKUPS = ROOT / "data" / "cables" / "hookup-wires.json"  # T1: the sourced база монтажных проводов
NOW = datetime(2026, 10, 12, 12, 0)


def _migration_gauges() -> tuple:
    """The sizes 0096 seeded «Сечения AWG ↔ мм²» with — the table production has."""
    path = ROOT / "migrations" / "versions" / "0096_cable_structure.py"
    spec = importlib.util.spec_from_file_location("migration_0096", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GAUGES


def _gauge(awg, section, diameter=None, position=0) -> WireGauge:
    return WireGauge.create(awg=awg, section_mm2=section, conductor_d_mm=diameter or "", position=position)


def _wire(mark, series, gauge, outer) -> HookupWire:
    return HookupWire.create(mark=mark, series=series, gauge_id=gauge.id, outer_d_mm=outer)


# --- the view ---

def _filled(row) -> dict[str, str]:
    return {key: value for key, value in row.items() if value not in ("—", "")}


def test_one_row_per_size_one_column_per_series_of_the_base():
    g28, g007, g12 = _gauge("28", "0.08", "0.32", 0), _gauge("", "0.07", None, 1), _gauge("12", "4", "2.05", 2)
    rows = wire_dia_rows([g28, g007, g12], [
        _wire("UL1061 28AWG", "UL1061", g28, "0,89"),
        _wire("МС16-13 0,08", "МС16-13", g28, "0,72"),  # parse_hookup's series («МС16-13») → the base key
        _wire("МГТФ 0,07", "МГТФ", g007, "0,75"),
        _wire("UL1015 28AWG", "UL1015", g28, "1,2"),  # moved into монтажные by hand: not a series of the base
        _wire("ПуГВнг(А)-LS 1х4,0", "ПуГВнг(А)-LS", g12, "4,5"),
    ])
    assert list(rows[0]) == list(COLUMNS) == ["По старой таблице", "AWG", "ГОСТ", "Ø жилы", *SERIES]
    assert SERIES == ("UL1061", "UL1571", "UL1007", "UL3239", "НВ-4", "ПВАМ", "МГТФ", "МГШВ", "МС 16-13", "МС 15-11",
                      "МК 26-13", "ПуГВ")
    assert _filled(rows[0]) == {"AWG": "28", "ГОСТ": "0,08", "Ø жилы": "0,32", "UL1061": "0,89", "МС 16-13": "0,72"}
    assert _filled(rows[1]) == {"ГОСТ": "0,07", "МГТФ": "0,75"} and rows[1]["AWG"] == ""
    assert _filled(rows[2]) == {"AWG": "12", "ГОСТ": "4", "Ø жилы": "2,05"}


def test_wires_of_one_series_and_size_with_different_diameters_give_the_largest():
    g = _gauge("22", "0.35", "0.64")
    rows = wire_dia_rows([g], [_wire("ПВАМ 0,35", "ПВАМ", g, "1,4"), _wire("ПВАМ 0,35 красный", "ПВАМ", g, "1,45"),
                               _wire("ПВАМ 0,35 белый", "ПВАМ", g, None)])
    assert rows[0]["ПВАМ"] == "1,45"


def test_a_size_the_base_has_no_diameter_for_keeps_the_old_number_flagged():
    g12, g22, g007 = _gauge("12", "4", "2.05", 0), _gauge("22", "0.35", "0.64", 1), _gauge("", "0.07", None, 2)
    old = [{"AWG": "12", "ГОСТ": "4", "Ø жилы": "2,05", "ПВАМ": "4,2", "МС": "—", "UL1015": "3,9"},
           {"AWG": "22", "ГОСТ": "0,35", "Ø жилы": "0,64", "ПВАМ": "—", "МГТФ": "1,2", "МС": "1,3"}]
    rows = wire_dia_rows([g12, g22, g007], [_wire("МГТФ 0,35", "МГТФ", g22, "1,19")], old)
    assert _filled(rows[0]) == {"По старой таблице": "ПВАМ", "AWG": "12", "ГОСТ": "4", "Ø жилы": "2,05", "ПВАМ": "4,2"}
    # the base wins (МГТФ 1,19, not 1,2); the old «МС» column stood for both МС series; UL1015 has no column
    assert _filled(rows[1]) == {"По старой таблице": "МС 16-13, МС 15-11", "AWG": "22", "ГОСТ": "0,35", "Ø жилы": "0,64",
                                "МГТФ": "1,19", "МС 16-13": "1,3", "МС 15-11": "1,3"}
    assert rows[2][OLD_COLUMN] == ""  # a ГОСТ-only size was never in the AWG table


def test_records_carry_the_rows_in_column_order():
    g = _gauge("", "0.14")
    [record] = wire_dia_records(wire_dia_rows([g], []))
    assert record["display_text"] == "0,14" and record["source_sheet"] == "База монтажных проводов"
    assert list(record["fields"]) == list(COLUMNS)


# --- old СПР.КАБ table vs the view over the real reference base ---

def _old_table() -> list[dict[str, str]]:
    table = parse_spr_kab_tsv((MIRROR / "СПР.КАБ.tsv").read_text(encoding="utf-8"))["kab_awg"]
    return [dict(zip(table["header"], row)) for row in table["rows"]]


@pytest.fixture()
def real_base(sessions):  # noqa: F811
    """Production after the release: the bundled reference mirror (БД.ПРОВ, СПР.КАБ…), the 0096 sizes, БД.ПРОВ
    монтажные structured (deploy), then `load_cables --hookups` with the real hookup-wires.json (T1)."""
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(MIRROR, factory(), dry_run=False, now=NOW)
    for awg, section, diameter, strands in _migration_gauges():
        save_gauge(SaveGaugeCommand(None, awg, section, diameter, strands, ACTOR, NOW), factory())
    structure_hookups(ACTOR, NOW, factory)
    report = import_hookups(factory(), json.loads(HOOKUPS.read_text(encoding="utf-8")), ACTOR, NOW)
    with factory() as uow:
        gauges, hookups, rows = uow.cable_structure.gauges(), uow.cable_structure.hookups(), read_wire_dia(uow)
    return SimpleNamespace(factory=factory, gauges=gauges, hookups=hookups, report=report, rows=rows,
                           base_rows=wire_dia_rows(gauges, hookups))


def _differences(old_rows, new_rows) -> list[tuple[str, str, str, str]]:
    """(series, size, old Ø, new Ø) for every size and series where the old table and the view disagree."""
    old_by_awg = {row["AWG"]: row for row in old_rows}
    result = []
    for row in new_rows:
        size = f"{row['AWG']} AWG" if row["AWG"] else f"{row['ГОСТ']} мм²"
        old = old_by_awg.get(row["AWG"]) if row["AWG"] else None
        for series in SERIES:
            column = old_column(series, old or {})
            before = (old or {}).get(column, "—") if column else "—"
            if before != row[series]:
                result.append((series, size, before, row[series]))
    return result


# The old СПР.КАБ «Ø изоляции» against the база монтажных after the T1 file (backend/data/cables/hookup-wires.json) is
# imported, the base alone (no fallback). The old numbers were generic per AWG, the file's are sourced per series
# (its «was» block names the source of each change). The old table had one «МС» column for every МС series. A new
# difference fails on purpose — re-pin it and say where it comes from.
# 1. Both have a number and they disagree: (series, size, old, base).
CHANGED = [
    ("UL1571", "32 AWG", "0,7", "0,54"), ("МГТФ", "32 AWG", "0,55", "0,56"),
    ("UL1061", "30 AWG", "0,8", "0,81"), ("UL1571", "30 AWG", "0,85", "0,75"), ("UL1007", "30 AWG", "1,1", "1,15"),
    ("МГТФ", "30 AWG", "0,65", "0,62"), ("МС 16-13", "30 AWG", "0,8", "0,66"),
    ("UL1061", "28 AWG", "0,9", "0,89"), ("UL1571", "28 AWG", "0,95", "0,85"), ("UL1007", "28 AWG", "1,2", "1,14"),
    ("МС 16-13", "28 AWG", "0,9", "0,72"), ("МС 15-11", "28 AWG", "0,9", "0,8"),
    ("UL1061", "26 AWG", "1", "0,99"), ("UL1571", "26 AWG", "1,05", "0,98"), ("UL1007", "26 AWG", "1,3", "1,22"),
    ("МГТФ", "26 AWG", "0,85", "0,87"), ("МГШВ", "26 AWG", "1,2", "1,3"), ("МС 16-13", "26 AWG", "1", "0,81"),
    ("МС 15-11", "26 AWG", "1", "0,89"),
    ("UL1061", "24 AWG", "1,15", "1,14"), ("UL1571", "24 AWG", "1,2", "1,11"), ("UL1007", "24 AWG", "1,4", "1,45"),
    ("МГТФ", "24 AWG", "1", "1,04"), ("МГШВ", "24 AWG", "1,4", "1,6"), ("МС 16-13", "24 AWG", "1,1", "0,96"),
    ("МС 15-11", "24 AWG", "1,1", "1,04"),
    ("UL1061", "22 AWG", "1,35", "1,32"), ("UL1571", "22 AWG", "1,4", "1,3"), ("UL1007", "22 AWG", "1,6", "1,65"),
    ("НВ-4", "22 AWG", "1,7", "1,6"), ("МГТФ", "22 AWG", "1,2", "1,19"), ("МГШВ", "22 AWG", "1,6", "1,9"),
    ("МС 16-13", "22 AWG", "1,3", "1,25"), ("МС 15-11", "22 AWG", "1,3", "1,29"),
    ("UL1061", "20 AWG", "1,6", "1,42"), ("UL1007", "20 AWG", "1,8", "1,83"), ("НВ-4", "20 AWG", "1,9", "1,8"),
    ("ПВАМ", "20 AWG", "2", "1,6"), ("МГШВ", "20 AWG", "1,8", "2,2"), ("МС 16-13", "20 AWG", "1,5", "1,37"),
    ("МС 15-11", "20 AWG", "1,5", "1,4"),
    ("UL1061", "18 AWG", "1,9", "1,75"), ("UL1007", "18 AWG", "2,1", "2,03"), ("ПВАМ", "18 AWG", "2,2", "1,9"),
    ("МГШВ", "18 AWG", "2", "2,5"),
    ("НВ-4", "17 AWG", "2,3", "2,2"), ("ПВАМ", "17 AWG", "2,5", "2,1"), ("МГТФ", "17 AWG", "1,7", "1,9"),
    ("МГШВ", "17 AWG", "2,1", "2,8"),
    ("UL1061", "16 AWG", "2,4", "2,08"), ("UL1007", "16 AWG", "2,6", "2,39"), ("НВ-4", "16 AWG", "2,6", "2,5"),
    ("ПВАМ", "16 AWG", "2,8", "2,4"), ("МГШВ", "16 AWG", "2,4", "3"),
    ("ПВАМ", "14 AWG", "3,4", "3"),
]
# 2. Only the base has it: (series, size, base) — new series, sizes the old AWG table could not hold (ГОСТ-only).
ONLY_IN_BASE = [
    ("UL1571", "36 AWG", "0,45"), ("МС 16-13", "34 AWG", "0,56"), ("МГТФ", "0,07 мм²", "0,75"),
    ("МГТФ", "0,14 мм²", "0,9"), ("МГШВ", "0,14 мм²", "1,4"), ("UL3239", "24 AWG", "1,9"), ("UL3239", "22 AWG", "2,05"),
    ("ПВАМ", "22 AWG", "1,4"), ("UL3239", "20 AWG", "2,25"), ("ПуГВ", "20 AWG", "2,5"), ("UL3239", "18 AWG", "2,5"),
    ("ПуГВ", "18 AWG", "2,7"), ("ПуГВ", "17 AWG", "2,8"), ("UL3239", "16 AWG", "2,8"), ("ПуГВ", "16 AWG", "3,4"),
    ("UL3239", "14 AWG", "3,3"), ("ПуГВ", "14 AWG", "4,1"), ("UL3239", "12 AWG", "4"), ("ПуГВ", "12 AWG", "4,8"),
    ("UL3239", "10 AWG", "4,8"), ("ПуГВ", "10 AWG", "5,3"), ("ПуГВ", "8 AWG", "6,8"), ("ПуГВ", "6 AWG", "8,1"),
    ("МГТФ", "0,1 мм²", "0,85"),
]
# 3. Regressions: the old table had a number, the base has none — no size of the file (or БД.ПРОВ) gives that
# series and AWG a Ø. These the view keeps from the old table, flagged «По старой таблице» (test below).
REGRESSED = {
    "МГТФ": "36 34 28 20 18 16 14",
    "UL1061": "32 17 14 12 10",
    "МС 15-11": "32 30 18 16 14",
    "НВ-4": "30 14 12",
    "МГШВ": "30 28 14",
    "UL1571": "20 18 17 16 14",
    "МС 16-13": "18 16 14",
    "UL1007": "17 14 12 10",
    "ПВАМ": "12 10",
}


def test_the_view_against_the_old_spr_kab_table_size_by_size(real_base):
    old = _old_table()
    assert real_base.report.refused == [] and real_base.report.gauges_added == ["0,1 мм²"]
    # the same sizes, sections and core Ø: «Сечения AWG ↔ мм²» was seeded from that table (0096); the file's own
    # AWG areas (0,0509 for 30 AWG…) are only reported by the import, the table is the owner's
    assert [row["AWG"] for row in real_base.base_rows if row["AWG"]] == [row["AWG"] for row in old]
    for new, before in zip((row for row in real_base.base_rows if row["AWG"]), old):
        assert Decimal(new["ГОСТ"].replace(",", ".")) == Decimal(before["ГОСТ"].replace(",", "."))
        assert Decimal(new["Ø жилы"].replace(",", ".")) == Decimal(before["Ø жилы"].replace(",", "."))
    differences = _differences(old, real_base.base_rows)
    assert [item for item in differences if "—" not in item[2:]] == CHANGED
    assert [(series, size, new) for series, size, before, new in differences if before == "—"] == ONLY_IN_BASE
    gone: dict[str, list[str]] = {}
    for series, size, _before, new in differences:
        if new == "—":
            gone.setdefault(series, []).append(size.removesuffix(" AWG"))
    assert {series: " ".join(sizes) for series, sizes in gone.items()} == REGRESSED
    assert all(row["МК 26-13"] == "—" for row in real_base.base_rows)  # the file has no МК 26-13 Ø yet


def test_the_generator_never_loses_a_diameter_the_old_table_had(real_base):
    differences = _differences(_old_table(), real_base.rows)
    assert [item for item in differences if item[3] == "—"] == []
    flagged: dict[str, list[str]] = {}
    for row in real_base.rows:
        for series in filter(None, row[OLD_COLUMN].split(", ")):
            flagged.setdefault(series, []).append(row["AWG"])
            base = next(item for item in real_base.base_rows if item["ГОСТ"] == row["ГОСТ"])
            assert base[series] == "—"  # flagged only where the base has nothing
    assert {series: " ".join(sizes) for series, sizes in flagged.items()} == {
        series: REGRESSED[series] for series in flagged} and set(flagged) == set(REGRESSED)
    # everything else is the base's own number
    for row, base in zip(real_base.rows, real_base.base_rows):
        flagged_here = set(filter(None, row[OLD_COLUMN].split(", ")))
        assert {series: row[series] for series in SERIES if series not in flagged_here} == {
            series: base[series] for series in SERIES if series not in flagged_here}


def test_gost_awg_is_the_old_one_for_every_whole_awg_row(real_base):
    old = gost_awg_from_wire_table(_old_table())
    assert gauge_gost_awg(real_base.gauges) == old
    assert gost_awg_from_wire_table(real_base.rows) == old  # the generator's own map (wireDiaFromRecords) too


# --- the readers: the HTTP view, the batch generator's refs, the ETK build ---

def test_the_endpoint_serves_the_view(real_base):
    app.state.references_uow_factory = real_base.factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"erpref": "view"})
    try:
        response = TestClient(app).get("/api/references/wire-dia")
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        del app.state.references_uow_factory
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == list(COLUMNS) and body["series"] == list(SERIES)
    assert [record["fields"] for record in body["records"]] == real_base.rows
    assert list(body["records"][0]["fields"]) == list(COLUMNS)  # the generator reads the columns by their order


class _Techcards:
    operation_cards = SimpleNamespace(list=lambda: [])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_the_batch_generator_gets_the_view_instead_of_the_old_table(real_base):
    refs = generator_refs(_Techcards, real_base.factory)
    assert refs["wire_dia"] == real_base.rows
    assert "kab_awg" not in refs and refs["kab_coax"]
    assert generator_refs(_Techcards, None)["wire_dia"] == []


def test_the_etk_build_takes_gost_awg_from_the_sizes_table_else_the_stored_map(real_base, monkeypatch):
    seen = []
    monkeypatch.setattr(techcard_build, "enrich_terminal_norms",
                        lambda config, records, gost_awg: seen.append(dict(gost_awg)) or dict(config))
    _config, stamp = techcard_build.with_technology({"ops": []}, real_base.factory)
    assert stamp["schema_version"] == TECH_SNAPSHOT_SCHEMA_VERSION
    assert seen[-1] == gauge_gost_awg(real_base.gauges) == gost_awg_from_wire_table(_old_table())

    class NoSizes:  # the sizes table is empty: the stored gost_awg map, as before
        def __init__(self):
            self.inner = real_base.factory()

        def __enter__(self):
            uow = self.inner.__enter__()
            uow.cable_structure = SimpleNamespace(gauges=lambda: [])
            return uow

        def __exit__(self, *exc):
            return self.inner.__exit__(*exc)

    techcard_build.with_technology({"ops": []}, NoSizes)
    with real_base.factory() as uow:
        stored = gost_awg_map(uow.references.list())
    assert stored and seen[-1] == stored


def test_the_build_does_not_touch_wire_lengths_or_the_twist_diameter_of_a_config(real_base):
    """Issued ETKs are snapshots: the twist allowance and the wire Ø live in the saved build_config the generator
    sent; the build (and the «Автопересборка» refresh, which reuses that config) only adds terminal norms."""
    config = {"wires": [{"name": "МГТФ 0,12", "art": "МГТФ 0,12", "qty": 2, "length": 163}],
              "twist": {"enabled": True, "pitch": "20", "wireDia": "0,9", "mode": "all", "pairs": [], "wireIndices": [0]},
              "ops": [{"type": "cutWire", "wires": [{"art": "МГТФ 0,12"}]}]}
    built, _stamp = techcard_build.with_technology(config, real_base.factory)
    assert built["wires"] == config["wires"] and built["twist"] == config["twist"]
