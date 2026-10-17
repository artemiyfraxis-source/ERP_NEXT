"""База монтажных проводов (owner 12.10.2026): the allow-list of 12 series, the file validator and its importer."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references import load_cables
from app.modules.references.application import (
    SaveHookupCommand, SetCableKindCommand, cable_catalog, save_hookup, set_cable_kind, structure_hookups,
)
from app.modules.references.cables import NOT_HOOKUP_REASON, CableKind, auto_kind, hookup_series
from app.modules.references.domain import TechOperationTab, gost_awg_map
from app.modules.references.importers import HookupSeedError, hookup_seed, import_hookups
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.references.test_cable_structure import _seed_gauges
from tests.references.test_cable_structure_migration import _config
from tests.references.test_cables import _mirror
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)

FIXTURE = Path(__file__).with_name("hookup-wires.fixture.json")

HEADER = ["Марка", "Тип провода", "Стандарт / производитель", "Сечение, мм2", "AWG", "Жила: проволок × диаметр, мм",
          "Число жил", "Цвета", "Наружный диаметр, мм", "Изоляция", "Темп. от, °C", "Темп. до, °C", "Напряжение, В",
          "Источник данных"]
ROWS = [
    ["МГТФ 0,20", "монтажный (термостойкий)", "ТУ (старое)", "0,20", "", "", "1", "", "", "ФТ-4", "", "", "", "Справочник"],
    ["UL1061 28AWG", "монтажный", "UL AWM Style 1061, SR-PVC", "0,08", "28", "", "1", "", "0,89", "ПВХ", "-10", "80", "", ""],
    ["ПВАМ 0,50", "силовой", "", "0,5", "", "", "1", "", "1,6", "ПВХ", "-40", "105", "48 (ном.) / 1000 (испыт.)", ""],
    ["МС16-13 0,08", "монтажный (высокотемпературный)", "ТУ 16-505.083-78", "0,08", "", "", "1", "", "", "", "", "", "", ""],
    ["M22759/16 26AWG", "монтажный (AWM)", "SAE AS22759/16", "0,13", "26", "", "1", "", "", "", "", "", "", ""],
    ["GXL Core 0,3", "силиконовый (аккумуляторный/RC)", "", "0,3", "", "", "", "", "", "", "", "", "", ""],
    ["FLRY-B 0,12", "монтажный", "", "0,12", "", "", "1", "", "", "", "", "", "", ""],
    ["TXL 0,35", "монтажный", "", "0,35", "", "", "1", "", "", "", "", "", "", ""],
    ["SXL 10AWG", "монтажный", "", "5,26", "10", "", "1", "", "", "", "", "", "", ""],
    ["RG316", "коаксиальный", "", "", "", "", "1", "", "2,45", "", "", "", "", ""],
    ["КСПВГ 4x0,20", "многожильный", "", "0,20", "", "", "4", "", "", "", "", "", "", ""],
]
PROV = "\n".join("\t".join(row) for row in [HEADER, *ROWS]) + "\n"
MOVED_OUT = {"M22759/16 26AWG", "GXL Core 0,3", "FLRY-B 0,12", "TXL 0,35", "SXL 10AWG"}


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- the allow-list ---

@pytest.mark.parametrize(("mark", "series"), [
    ("UL1061 28AWG", "UL1061"), ("UL1571 26AWG", "UL1571"), ("UL1007 30AWG", "UL1007"), ("UL 3239 22AWG", "UL3239"),
    ("НВ-4 0,20", "НВ-4"), ("НВ-4 600 0,35", "НВ-4"), ("НВ4 0,5", "НВ-4"), ("ПВАМ 0,50", "ПВАМ"), ("МГТФ 0,07", "МГТФ"),
    ("MГТФ 0,2", "МГТФ"), ("МГШВ 0,75", "МГШВ"), ("МС16-13 0,08", "МС 16-13"), ("МС 16-13 0,12", "МС 16-13"),
    ("МС 15-11 0,12", "МС 15-11"), ("МК 26-13 0,35", "МК 26-13"), ("ПуГВ 1,0", "ПуГВ"), ("ПУГВ 1х0,75", "ПуГВ"),
    ("провод МГТФ 0,35", "МГТФ"),
])
def test_the_twelve_series_are_the_base(mark, series):
    assert hookup_series(mark) == series


# Owner 15.10.2026: «Прочие кабели» closed — the single-core series it held are series of the base now.
@pytest.mark.parametrize(("mark", "series"), [
    ("UL1015 26AWG", "UL1015"), ("UL 1015 10AWG", "UL1015"), ("UL3135 22AWG", "UL3135"), ("UL758 10AWG", "UL758"),
    ("НВМ 0,35", "НВМ"), ("МНВ-4 0,12", "МНВ-4"), ("МНВ4 0,2", "МНВ-4"), ("ПГВА 0,50", "ПГВА"),
    ("ПуГВнг(А)-LS 1х1,0", "ПуГВнг(А)-LS"), ("ПУГВНГ(A)-LS 1х2,5", "ПуГВнг(А)-LS"),
    ("Силиконовый провод 28AWG", "Силиконовый провод"), ("AGR2 Core 0,3", "AGR2"),
    ("Кабель высоковольтный 150kV 0,75мм2 8мм", "Кабель высоковольтный"),
])
def test_the_series_moved_from_other_cables(mark, series):
    assert hookup_series(mark) == series
    assert auto_kind({"Тип провода": "силовой", "Число жил": "1"}, mark, frozenset())[0] == CableKind.WIRE


@pytest.mark.parametrize("mark", [
    "МГТФЭ 0,2", "UL10610 28AWG", "UL10150 22AWG", "UL7580 10AWG", "НВМЭ 0,35", "ПГВАЭ 0,5", "ПуГВнг(А)-HF 1х1,0",
    "МГШВЭ 0,5", "M22759/16 22AWG", "FLRY-B 0,35", "Силиконовый кабель 2x0,5", "",
])
def test_other_brands_are_not(mark):
    assert hookup_series(mark) is None


@pytest.mark.parametrize("mark", ["M22759/16 26AWG", "FLRY-B 0,12", "GXL Core 0,3", "TXL 0,35", "SXL 10AWG"])
def test_a_single_core_brand_outside_the_base_goes_to_others(mark):
    fields = {"Тип провода": "монтажный", "Число жил": "1"}
    assert auto_kind(fields, mark, frozenset()) == (CableKind.OTHER, NOT_HOOKUP_REASON)


@pytest.mark.parametrize("mark", [
    "UL1061 28AWG", "UL1571 26AWG", "UL1007 30AWG", "UL3239 22AWG", "НВ-4 600 0,2", "ПВАМ 0,50", "МГТФ 0,2",
    "МГШВ 0,2", "МС 16-13 0,08", "МС 15-11 0,12", "МК 26-13 0,35", "ПуГВ 1,0",
])
def test_the_base_goes_to_hookup_wires_whatever_the_row_type(mark):
    assert auto_kind({"Тип провода": "монтажный", "Число жил": "1"}, mark, frozenset())[0] == CableKind.WIRE
    # ПВАМ is typed «силовой», a row may carry no core count: the series decides
    assert auto_kind({"Тип провода": "силовой", "Число жил": ""}, mark, frozenset()) == (
        CableKind.WIRE, f"серия {hookup_series(mark)} из базы монтажных")
    # a multi-core cable and a coax stay where their shape puts them
    assert auto_kind({"Тип провода": "", "Число жил": "4"}, mark, frozenset())[0] == CableKind.MULTI


def test_a_manual_move_still_wins_both_ways(tmp_path, uow):
    load_mirror(_mirror(tmp_path, prov=PROV), uow, dry_run=False, now=NOW)
    rows ={item.cable: item for item in cable_catalog(uow).rows}
    moved = set_cable_kind(SetCableKindCommand(rows["M22759/16 26AWG"].record.id, CableKind.WIRE, ACTOR, NOW), uow)
    assert (moved.kind, moved.auto, moved.manual) == (CableKind.WIRE, CableKind.OTHER, True)
    out = set_cable_kind(SetCableKindCommand(rows["МГТФ 0,20"].record.id, CableKind.OTHER, ACTOR, NOW), uow)
    assert (out.kind, out.manual) == (CableKind.OTHER, True)


# --- the file ---

def test_fixture_validates_and_refuses_bad_rows_alone():
    seed = hookup_seed(_payload())
    assert [series.key for series in seed.series] == ["МГТФ", "UL1061", "МС 16-13", "ПВАМ"]
    assert [size.mark for size in seed.series[0].sizes] == ["МГТФ 0,2", "МГТФ 0,07", "МГТФ 0,6"]
    small = seed.series[0].sizes[1]
    assert small.colors.value == "белый" and small.voltage_v.value == 250  # own colours, the series' voltage
    assert seed.series[0].sizes[0].colors.value == "белый, красный, чёрный"
    assert small.insulation_thickness_mm.value == Decimal("0.15")
    refused = {item["key"]: item["reason"] for item in seed.refused}
    assert set(refused) == {"0,01 мм²", "UL1007 26AWG", "UL1061 30AWG", "M22759 22AWG"}
    assert "не марка серии UL1061" in refused["UL1007 26AWG"]
    assert "«от» должна быть ниже «до»" in refused["UL1061 30AWG"]
    assert "не марка из базы монтажных" in refused["M22759 22AWG"]
    assert [gauge.awg for gauge in seed.gauges] == ["24", "28", "26", None]


@pytest.mark.parametrize("broken", [
    [], {"version": 2, "gauges": [], "series": []}, {"version": 1, "series": []}, {"version": 1, "gauges": [], "series": {}},
])
def test_a_file_that_is_not_the_format_is_refused_whole(broken):
    with pytest.raises(HookupSeedError):
        hookup_seed(broken)


@pytest.mark.parametrize(("change", "reason"), [
    (lambda size: size.update(outer_dia_mm={"value": -1, "source": "x"}), "больше нуля"),
    (lambda size: size.update(outer_dia_mm=0.9), "\"value\", \"source\""),
    (lambda size: size.update(strands={"value": "7×0,19", "source": ""}), "непустая строка"),
    (lambda size: size.pop("strands"), "нет поля"),
    (lambda size: size.update(section_mm2="много"), "нужно число"),
    (lambda size: size.update(mark="МГТФ 0,07"), "уже есть в файле"),
])
def test_a_bad_size_is_refused_with_its_reason(change, reason):
    payload = _payload()
    change(payload["series"][0]["sizes"][0])
    seed = hookup_seed(payload)
    refused = [item for item in seed.refused if item["key"].startswith("МГТФ")]
    assert len(refused) == 1 and reason in refused[0]["reason"]
    assert len(seed.series[0].sizes) == 2


def test_a_bad_series_value_refuses_every_size_of_it():
    payload = _payload()
    payload["series"][1]["voltage_v"] = {"value": 12.5, "source": "x"}
    seed = hookup_seed(payload)
    assert {item["key"] for item in seed.refused} >= {"UL1061 28AWG", "UL1007 26AWG", "UL1061 30AWG"}
    assert "UL1061" not in [series.key for series in seed.series]


# --- the import ---

def _factory(uow):
    return lambda: SqlAlchemyReferencesUnitOfWork(uow.session_factory)  # noqa: E731


def _prepared(tmp_path, uow):
    load_mirror(_mirror(tmp_path, prov=PROV), uow, dry_run=False, now=NOW)
    gauges = _seed_gauges(uow)
    structure_hookups(ACTOR, NOW, _factory(uow))  # UL1061 28AWG, ПВАМ 0,50, МС16-13 0,08 get a structure
    return gauges


def _records(uow):
    with uow:
        return {cable: record for record in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=1000)
                for cable in [str(record.fields.get("Марка") or record.display_text)]}


def test_dry_run_reports_and_writes_nothing(tmp_path, uow):
    _prepared(tmp_path, uow)
    before = {mark: (record.id, dict(record.fields), record.edited_in_erp) for mark, record in _records(uow).items()}
    hookups_before = cable_catalog(_factory(uow)()).hookups

    report = import_hookups(_factory(uow)(), _payload(), ACTOR, NOW, dry_run=True)

    assert report.dry_run and report.created == ["МГТФ 0,07"] and report.gauges_added == ["24 AWG · 0,2 мм²"]
    after = {mark: (record.id, dict(record.fields), record.edited_in_erp) for mark, record in _records(uow).items()}
    assert after == before
    catalog = cable_catalog(_factory(uow)())
    assert catalog.hookups == hookups_before and len(catalog.gauges) == 7


def test_import_updates_in_place_creates_missing_sizes_and_a_second_run_changes_nothing(tmp_path, uow):
    gauges = _prepared(tmp_path, uow)
    ids = {mark: record.id for mark, record in _records(uow).items()}

    report = import_hookups(_factory(uow)(), _payload(), ACTOR, NOW)

    # 1. sizes: 24 AWG added (and the ГОСТ → AWG map with it); 26 AWG differs from the file — only reported
    assert report.gauges_added == ["24 AWG · 0,2 мм²"]
    assert report.gauge_mismatches == ["26 AWG · 0,13 мм²: в таблице 0,12 мм²"]
    catalog = cable_catalog(_factory(uow)())
    by_awg = {gauge.awg: gauge for gauge in catalog.gauges}
    assert by_awg["26"].section_mm2 == Decimal("0.12") and by_awg["24"].conductor_d_mm == Decimal("0.51")
    with uow:
        assert gost_awg_map(uow.references.list())["0.2"] == 24

    # 2. rows: matched by series + size, not by the mark text; same id, same mark, edited in ERP
    assert report.created == ["МГТФ 0,07"]
    assert {item["key"] for item in report.updated} == {"МГТФ 0,20", "UL1061 28AWG", "МС16-13 0,08", "ПВАМ 0,50"}
    assert {item["key"] for item in report.refused} == {
        "0,01 мм²", "UL1007 26AWG", "UL1061 30AWG", "M22759 22AWG", "МГТФ 0,6"}
    assert report.unchanged == 0 and report.hookups_created == 2 and report.hookups_updated == 2  # ПВАМ: 48 V already
    records = _records(uow)
    assert all(records[mark].id == record_id for mark, record_id in ids.items())  # nothing renamed or deleted
    mgtf = records["МГТФ 0,20"]
    assert mgtf.edited_in_erp and mgtf.fields["Стандарт"] == "ТУ 16-505.185-71"
    assert mgtf.fields["Жила: проволок × диаметр, мм"] == "7×0,19"
    assert mgtf.fields["Цвета"] == "белый, красный, чёрный" and mgtf.fields["Изоляция"] == "фторопласт-4"
    assert (mgtf.fields["Наружный диаметр, мм"], mgtf.fields["Темп. от, °C"], mgtf.fields["Темп. до, °C"]) == ("0,97", "-60", "220")
    assert mgtf.fields["Сечение, мм2"] == "0,20"  # the same size: the cell stays as typed
    assert mgtf.fields["Источник данных"].startswith("Справочник; ТУ 16-505.185-71; ТУ 16-505.185-71, п. 1.2")
    changes = next(item["changes"] for item in report.updated if item["key"] == "МГТФ 0,20")
    assert changes["Стандарт"] == ["ТУ (старое)", "ТУ 16-505.185-71"]
    assert changes["структура: материал жилы"] == ["", "медь посеребрённая"]

    ul = records["UL1061 28AWG"]
    # null in the file leaves the value: Ø 0,89, ПВХ and -10 stay
    assert (ul.fields["Наружный диаметр, мм"], ul.fields["Изоляция"], ul.fields["Темп. от, °C"]) == ("0,89", "ПВХ", "-10")
    assert ul.fields["Напряжение, В"] == "300" and ul.fields["Жила: проволок × диаметр, мм"] == "7×0,127"
    pvam = records["ПВАМ 0,50"]
    assert pvam.fields["Напряжение, В"] == "48 (ном.) / 1000 (испыт.)"  # 48 V already: stays as typed
    assert pvam.fields["Источник данных"] == "ТУ 16.К19-12-2003"

    hookups = catalog.hookups
    assert (hookups["ul106128awg"].conductor_material, hookups["ul106128awg"].insulation_thickness_mm) == ("медь луженая", Decimal("0.23"))
    assert hookups["ul106128awg"].outer_d_mm == Decimal("0.89") and hookups["ul106128awg"].voltage_v == 300
    assert hookups["мс16130,08"].series == "МС 16-13"
    assert hookups["мгтф0,20"].gauge_id == by_awg["24"].id and hookups["мгтф0,20"].insulation_thickness_mm == Decimal("0.2")
    new = records["МГТФ 0,07"]
    assert new.edited_in_erp and new.fields["Тип провода"] == "монтажный" and new.fields["Число жил"] == "1"
    assert new.fields["Цвета"] == "белый" and new.fields["Сечение, мм2"] == "0,07"
    assert hookups["мгтф0,07"].gauge_id == next(gauge.id for gauge in catalog.gauges if gauge.awg is None)
    kinds = {row.cable: row.kind for row in catalog.rows}
    assert kinds["МГТФ 0,07"] == kinds["МГТФ 0,20"] == CableKind.WIRE

    # 3. the marks the база монтажных sends to «прочие»
    assert set(report.moved_out_of_montage) == MOVED_OUT
    assert {row.cable for row in catalog.rows if row.kind == CableKind.WIRE} == {
        "МГТФ 0,20", "МГТФ 0,07", "UL1061 28AWG", "ПВАМ 0,50", "МС16-13 0,08"}

    again = import_hookups(_factory(uow)(), _payload(), ACTOR, NOW)
    assert (again.changed, again.created, again.updated, again.gauges_added) == (0, [], [], [])
    assert again.unchanged == 5 and again.hookups_created == again.hookups_updated == 0

    # a mirror reload keeps what the import wrote
    load_mirror(_mirror(tmp_path / "again", prov=PROV), uow, dry_run=False, now=NOW)
    records = _records(uow)
    assert records["МГТФ 0,20"].fields["Стандарт"] == "ТУ 16-505.185-71" and "МГТФ 0,07" in records
    assert import_hookups(_factory(uow)(), _payload(), ACTOR, NOW).changed == 0


def test_a_row_moved_away_by_hand_is_left_alone(tmp_path, uow):
    _prepared(tmp_path, uow)
    pvam = _records(uow)["ПВАМ 0,50"]
    set_cable_kind(SetCableKindCommand(pvam.id, CableKind.OTHER, ACTOR, NOW), uow)
    report = import_hookups(_factory(uow)(), _payload(), ACTOR, NOW)
    assert {"key": "ПВАМ 0,50", "reason": "перенесён вручную в другой список — не тронут"} in report.refused
    assert "ПВАМ 0,50" not in report.created and _records(uow)["ПВАМ 0,50"].fields == pvam.fields


def test_the_screen_save_keeps_what_the_import_filled(tmp_path, uow):
    _prepared(tmp_path, uow)
    import_hookups(_factory(uow)(), _payload(), ACTOR, NOW)
    catalog = cable_catalog(_factory(uow)())
    wire = catalog.hookups["ul106128awg"]
    record = _records(uow)["UL1061 28AWG"]
    saved, _ = save_hookup(SaveHookupCommand(
        record_id=record.id, series=wire.series, gauge_id=wire.gauge_id, insulation=wire.insulation, outer_d_mm="0,9",
        voltage_v="300", temp_min_c="-10", temp_max_c="80", old=wire, actor=ACTOR, now=NOW), _factory(uow)())
    assert (saved.conductor_material, saved.insulation_thickness_mm, saved.outer_d_mm) == ("медь луженая", Decimal("0.23"), Decimal("0.9"))


def test_cli_prints_every_step_and_dry_run_writes_nothing(tmp_path, uow, monkeypatch, capsys):
    _prepared(tmp_path, uow)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr("app.database.build_engine", lambda url: None)
    monkeypatch.setattr("app.database.build_references_uow_factory", lambda engine: _factory(uow))

    assert load_cables.main(["--hookups", "--dry-run", "--file", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "проверка, ничего не записано" in out and "добавлено 1" in out and "создано 1" in out
    assert "M22759/16 26AWG" in out and "26 AWG · 0,13 мм²: в таблице 0,12 мм²" in out
    assert "МГТФ 0,07" not in _records(uow)

    assert load_cables.main(["--hookups", "--file", str(FIXTURE)]) == 0
    assert "МГТФ 0,07" in _records(uow)
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"version": 2}), encoding="utf-8")
    assert load_cables.main(["--hookups", "--file", str(broken)]) == 1


# --- HTTP ---

def test_api_hookup_carries_conductor_material_and_insulation_thickness(tmp_path, sessions):  # noqa: F811
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    _prepared(tmp_path, uow)
    import_hookups(SqlAlchemyReferencesUnitOfWork(sessions), _payload(), ACTOR, NOW)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    try:
        client = TestClient(app)
        rows = {row["cable"]: row for row in client.get("/api/references/cables").json()["rows"]}
        hookup = rows["UL1061 28AWG"]["hookup"]
        assert (hookup["conductor_material"], hookup["insulation_thickness_mm"]) == ("медь луженая", 0.23)
        saved = client.put(f"/api/references/cables/{rows['UL1061 28AWG']['id']}/hookup", json={
            "series": hookup["series"], "gauge_id": hookup["gauge_id"], "insulation": hookup["insulation"],
            "outer_d_mm": "0,89", "voltage_v": "300", "temp_min_c": "-10", "temp_max_c": "80",
            "insulation_thickness_mm": "0,25", "old": {**hookup, "outer_d_mm": "0,89", "voltage_v": "300",
                                                       "temp_min_c": "-10", "temp_max_c": "80"}})
        assert saved.status_code == 200
        assert (saved.json()["hookup"]["conductor_material"], saved.json()["hookup"]["insulation_thickness_mm"]) == ("медь луженая", 0.25)
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        app.dependency_overrides.pop(moscow_now, None)
        del app.state.references_uow_factory


# --- migration 0103 ---

def test_migration_0103_adds_two_columns_and_keeps_every_hookup(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, "0102_techcard_generator_drafts")
    with sqlite3.connect(database) as connection:
        gauge = connection.execute("SELECT id FROM wire_gauges WHERE awg = '28'").fetchone()[0]
        connection.execute(
            "INSERT INTO hookup_wires (id, mark, mark_key, series, gauge_id, insulation, outer_d_mm, voltage_v,"
            " temp_min_c, temp_max_c, updated_at) VALUES (?, 'UL1061 28AWG', 'ul106128awg', 'UL1061', ?, 'ПВХ', 0.89,"
            " 300, -10, 80, '2026-10-12')", (uuid4().hex, gauge))
        before = connection.execute("SELECT * FROM hookup_wires").fetchall()
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("hookup_wires", "wire_gauges", "tech_operation_records", "reference_entries")}

    command.upgrade(config, "0103_hookup_wire_conductor_insulation")

    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT * FROM hookup_wires").fetchall()
        columns = [row[1] for row in connection.execute("PRAGMA table_info(hookup_wires)")]
        assert columns[-2:] == ["conductor_material", "insulation_thickness_mm"]
        assert [row[:-2] for row in rows] == [tuple(item) for item in before] and rows[0][-2:] == ("", None)
        assert {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in counts} == counts
        connection.execute("UPDATE hookup_wires SET conductor_material = 'медь луженая', insulation_thickness_mm = 0.23")
        for bad in ("UPDATE hookup_wires SET insulation_thickness_mm = 0", "UPDATE hookup_wires SET conductor_material = NULL"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(bad)

    command.downgrade(config, "0102_techcard_generator_drafts")
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(hookup_wires)")]
        assert "conductor_material" not in columns and "insulation_thickness_mm" not in columns
        assert connection.execute("SELECT COUNT(*) FROM hookup_wires").fetchone()[0] == 1
