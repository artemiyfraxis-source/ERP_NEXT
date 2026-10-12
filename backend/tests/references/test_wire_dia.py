"""«Ø изоляции по маркам» from the база монтажных проводов (owner 12.10.2026: «вся инфа должна идти из монтажных
кабелей»): the view the ETK generator reads instead of the old СПР.КАБ AWG table, compared with that table."""

import importlib.util
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
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.modules.references.technology import TECH_SNAPSHOT_SCHEMA_VERSION, gost_awg_from_wire_table, parse_spr_kab_tsv
from app.modules.references.wire_dia import COLUMNS, SERIES, wire_dia_records, wire_dia_rows
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows import techcard_build
from app.workflows.techcards_all import generator_refs
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
MIRROR = ROOT / "data" / "reference-mirror"
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

def test_one_row_per_size_one_column_per_series_of_the_base():
    g28, g007, g12 = _gauge("28", "0.08", "0.32", 0), _gauge("", "0.07", None, 1), _gauge("12", "4", "2.05", 2)
    rows = wire_dia_rows([g28, g007, g12], [
        _wire("UL1061 28AWG", "UL1061", g28, "0,89"),
        _wire("МС16-13 0,08", "МС16-13", g28, "0,72"),  # parse_hookup's series («МС16-13») → the base key
        _wire("МГТФ 0,07", "МГТФ", g007, "0,75"),
        _wire("UL1015 28AWG", "UL1015", g28, "1,2"),  # moved into монтажные by hand: not a series of the base
        _wire("ПуГВнг(А)-LS 1х4,0", "ПуГВнг(А)-LS", g12, "4,5"),
    ])
    assert list(rows[0]) == list(COLUMNS) == ["AWG", "ГОСТ", "Ø жилы", *SERIES]
    assert SERIES == ("UL1061", "UL1571", "UL1007", "UL3239", "НВ-4", "ПВАМ", "МГТФ", "МГШВ", "МС 16-13", "МС 15-11",
                      "МК 26-13", "ПуГВ")
    assert {key: value for key, value in rows[0].items() if value != "—"} == {
        "AWG": "28", "ГОСТ": "0,08", "Ø жилы": "0,32", "UL1061": "0,89", "МС 16-13": "0,72"}
    assert {key: value for key, value in rows[1].items() if value != "—"} == {"AWG": "", "ГОСТ": "0,07", "МГТФ": "0,75"}
    assert {key: value for key, value in rows[2].items() if value != "—"} == {"AWG": "12", "ГОСТ": "4", "Ø жилы": "2,05"}


def test_wires_of_one_series_and_size_with_different_diameters_give_the_largest():
    g = _gauge("22", "0.35", "0.64")
    rows = wire_dia_rows([g], [_wire("ПВАМ 0,35", "ПВАМ", g, "1,4"), _wire("ПВАМ 0,35 красный", "ПВАМ", g, "1,45"),
                               _wire("ПВАМ 0,35 белый", "ПВАМ", g, None)])
    assert rows[0]["ПВАМ"] == "1,45"


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
    """The bundled reference mirror (БД.ПРОВ, СПР.КАБ…), the 0096 sizes, БД.ПРОВ монтажные structured — production
    before the hook-up wire file (T1) is imported."""
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(MIRROR, factory(), dry_run=False, now=NOW)
    for awg, section, diameter, strands in _migration_gauges():
        save_gauge(SaveGaugeCommand(None, awg, section, diameter, strands, ACTOR, NOW), factory())
    structure_hookups(ACTOR, NOW, factory)
    with factory() as uow:
        gauges, hookups = uow.cable_structure.gauges(), uow.cable_structure.hookups()
    return SimpleNamespace(factory=factory, gauges=gauges, hookups=hookups, rows=wire_dia_rows(gauges, hookups))


def _old_column(series: str, header: list[str]) -> str | None:
    if series in header:
        return series
    return "МС" if series.startswith("МС ") else None  # the old table had one «МС» column for every МС series


def _differences(old_rows, new_rows) -> list[tuple[str, str, str, str]]:
    """(series, size, old Ø, new Ø) for every size and series where the old table and the view disagree."""
    header = list(old_rows[0])
    old_by_awg = {row["AWG"]: row for row in old_rows}
    result = []
    for row in new_rows:
        size = f"{row['AWG']} AWG" if row["AWG"] else f"{row['ГОСТ']} мм²"
        old = old_by_awg.get(row["AWG"]) if row["AWG"] else None
        for series in SERIES:
            column = _old_column(series, header)
            before = (old or {}).get(column, "—") if column else "—"
            if before != row[series]:
                result.append((series, size, before, row[series]))
    return result


# Every difference between the old СПР.КАБ «Ø изоляции» and the база монтажных as it stands before the hook-up wire
# file is imported (T1). The old numbers were generic per AWG; the base holds only what БД.ПРОВ wires state in
# «Наружный диаметр, мм». The old table had one «МС» column for every МС series. A new difference fails on purpose.
# 1. Both have a number and they disagree: (series, size, old, base).
CHANGED = [
    ("UL1061", "30 AWG", "0,8", "0,81"),
    ("UL1061", "28 AWG", "0,9", "0,89"),
    ("МС 16-13", "28 AWG", "0,9", "0,72"),
    ("МС 16-13", "26 AWG", "1", "0,9"),
    ("МГТФ", "24 AWG", "1", "1,04"),
    ("МГШВ", "24 AWG", "1,4", "1,6"),
    ("НВ-4", "22 AWG", "1,7", "1,6"),
    ("МГТФ", "22 AWG", "1,2", "1,19"),
    ("НВ-4", "20 AWG", "1,9", "1,8"),
    ("ПВАМ", "20 AWG", "2", "1,6"),
    ("ПВАМ", "18 AWG", "2,2", "1,9"),
    ("МГШВ", "18 AWG", "2", "2,5"),
    ("ПВАМ", "17 AWG", "2,5", "2,1"),
    ("МГТФ", "17 AWG", "1,7", "1,9"),
    ("НВ-4", "16 AWG", "2,6", "2,5"),
    ("ПВАМ", "16 AWG", "2,8", "2,4"),
    ("ПВАМ", "14 AWG", "3,4", "3"),
]
# 2. Only the base has it (ГОСТ-only sizes the old AWG table could not hold; ПуГВ was not in it): (series, size, base).
ONLY_IN_BASE = [
    ("МГТФ", "0,07 мм²", "0,75"),
    ("МГТФ", "0,14 мм²", "0,9"),
    ("ПВАМ", "22 AWG", "1,4"),
    ("ПуГВ", "17 AWG", "2,5"),
]
# 3. Only the old table has it — no БД.ПРОВ wire of that series and AWG states its Ø yet (the T1 file fills them).
NOT_IN_BASE_YET = {
    "МГТФ": "36 34 32 30 28 26 20 18 16 14",
    "UL1061": "32 26 24 22 20 18 17 16 14 12 10",
    "UL1571": "32 30 28 26 24 22 20 18 17 16 14",
    "МС 16-13": "32 30 24 22 20 18 16 14",
    "МС 15-11": "32 30 28 26 24 22 20 18 16 14",
    "UL1007": "30 28 26 24 22 20 18 17 16 14 12 10",
    "НВ-4": "30 28 17 14 12",
    "МГШВ": "30 28 26 22 20 17 16 14",
    "ПВАМ": "12 10",
}


def test_the_view_against_the_old_spr_kab_table_size_by_size(real_base):
    old = _old_table()
    # the same sizes, sections and core Ø: «Сечения AWG ↔ мм²» was seeded from that table (0096)
    assert [row["AWG"] for row in real_base.rows if row["AWG"]] == [row["AWG"] for row in old]
    for new, before in zip((row for row in real_base.rows if row["AWG"]), old):
        assert Decimal(new["ГОСТ"].replace(",", ".")) == Decimal(before["ГОСТ"].replace(",", "."))
        assert Decimal(new["Ø жилы"].replace(",", ".")) == Decimal(before["Ø жилы"].replace(",", "."))
    differences = _differences(old, real_base.rows)
    assert [item for item in differences if "—" not in item[2:]] == CHANGED
    assert [(series, size, new) for series, size, before, new in differences if before == "—"] == ONLY_IN_BASE
    gone: dict[str, list[str]] = {}
    for series, size, before, new in differences:
        if new == "—":
            gone.setdefault(series, []).append(size.removesuffix(" AWG"))
    assert {series: " ".join(sizes) for series, sizes in gone.items()} == NOT_IN_BASE_YET
    # UL3239 and МК 26-13 are in neither yet
    assert all(row["UL3239"] == row["МК 26-13"] == "—" for row in real_base.rows)


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
