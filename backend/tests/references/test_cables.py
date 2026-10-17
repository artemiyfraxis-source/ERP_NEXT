"""«Кабели и провода» (owner 04.10.2026): БД.ПРОВ + coax D1·D2·D3 as коаксиальные / монтажные / прочие."""

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.application import (
    SetCableKindCommand, SetCoaxDiametersCommand, StaleRecordError, list_cables, set_cable_kind, set_coax_diameters,
)
from app.modules.references.cables import CableKind, auto_kind, cable_counts, cable_rows, family_dia
from app.modules.references.domain import ReferenceKind, ReferenceValidationError, TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.references.test_load_mirror import NOW, mirror, rows, sessions, uow  # noqa: F401  (fixtures/helpers)

PROV_HEADER = ["Марка", "Тип провода", "Сечение, мм2", "AWG", "Число жил", "Наружный диаметр, мм", "Волновое сопротивление, Ом"]
PROV_ROWS = [
    ["RG316", "коаксиальный", "", "", "1", "2,45", "50"],
    ["RG-58 A/U", "коаксиальный", "", "", "1", "4,95", "50"],
    ["UL1061 28AWG", "монтажный", "0,08", "28", "1", "0,89", ""],
    ["ПВАМ 0,50", "силовой", "0,5", "", "1", "1,6", ""],
    ["FLRY-B 0,35", "автомобильный (ISO 6722)", "0,35", "", "", "", ""],
    ["КСПВГ 4x0,20", "многожильный (сигнальный/контрольный)", "0,20", "", "4", "3,6", ""],
    ["SDI мультикор", "многожильный композитный кабель (5 коаксиалов + доп.жилы)", "", "", "5 коакс. + 4 доп.", "10", "75"],
    ["RG-179", "", "", "", "", "", ""],
]
PROV = "\n".join("\t".join(row) for row in [PROV_HEADER, *PROV_ROWS]) + "\n"
SPR_KAB = (
    "Кабель\tD1\tD2\tD3\nRG-316\t0,5\t1,48\t1,95\nRG58\t1\t2,95\t3,5\nRG-142\t0,9\t3\t3,9\nRG-142\t0,94\t3\t3,95\n"
    "\n\nAWG\tГОСТ\tØ жилы\tМГТФ\n22\t0,35\t0,64\t1,2\n"
)


def _record(tab: str, fields: dict[str, str], sort_key: str = "") -> TechOperationRecord:
    display = next(value for value in fields.values() if value)
    return TechOperationRecord.create(tab=TechOperationTab(tab), display_text=display, search_text="", source_sheet="",
                                      sort_key=sort_key or display, fields=fields)


def _wire(*cells: str) -> TechOperationRecord:
    return _record("prov", dict(zip(PROV_HEADER, cells)))


def _dia(cable: str, d1: str, d2: str, d3: str, sort_key: str) -> TechOperationRecord:
    return _record("kab_coax", {"Кабель": cable, "D1": d1, "D2": d2, "D3": d3}, sort_key)


# --- the rule ---

@pytest.mark.parametrize(("cells", "kind", "reason"), [
    (PROV_ROWS[0], CableKind.COAX, "тип «коаксиальный»"),
    (PROV_ROWS[2], CableKind.WIRE, "одна жила"),
    (PROV_ROWS[3], CableKind.WIRE, "одна жила"),  # «силовой» single core: ПВАМ is a column of the AWG table
    (PROV_ROWS[4], CableKind.OTHER, "марка не из базы монтажных"),  # owner 12.10.2026: only the база монтажных
    (PROV_ROWS[5], CableKind.MULTI, "жил: 4"),  # owner 10.10.2026: multi-core → own tab
    (PROV_ROWS[6], CableKind.MULTI, "жил: 5"),  # «5 коаксиалов» inside a multi-core cable is not a coax cable
    (PROV_ROWS[7], CableKind.COAX, "марка коаксиального кабеля"),  # no type: the generator's RG-… marker
])
def test_auto_kind_rules(cells, kind, reason):
    fields = dict(zip(PROV_HEADER, cells))
    assert auto_kind(fields, fields["Марка"], frozenset()) == (kind, reason)


def test_a_mark_in_the_diameter_table_is_coax_whatever_its_type():
    fields = dict(zip(PROV_HEADER, ["RG 58", "кабель", "", "", "", "", ""]))
    assert auto_kind(fields, "RG 58", frozenset({"rg58"})) == (CableKind.COAX, "есть в таблице D1 · D2 · D3")
    assert auto_kind({"Тип провода": "", "Число жил": ""}, "Проволока", frozenset())[0] == CableKind.OTHER


def test_rows_join_the_generator_diameter_row_and_keep_diameter_only_cables():
    wires = [_wire(*row) for row in PROV_ROWS]
    diameters = [_dia("RG-316", "0,5", "1,48", "1,95", "00001"), _dia("RG58", "1", "2,95", "3,5", "00002"),
                 _dia("RG-142", "0,9", "3", "3,9", "00003"), _dia("RG-142", "0,94", "3", "3,95", "00004")]

    result = cable_rows(wires, diameters, {})
    by_cable = {row.cable: row for row in result}

    assert cable_counts(result) == {"coax": 5, "wire": 2, "multi": 2, "other": 1}
    assert by_cable["RG316"].dia.fields["D3"] == "1,95"  # «RG316» ↔ «RG-316»: the generator's key
    assert by_cable["RG-58 A/U"].dia is None and by_cable["RG-58 A/U"].dia_hint.fields["Кабель"] == "RG58"
    assert by_cable["RG58"].record is None and by_cable["RG58"].dia.fields["D1"] == "1"  # only in the D-table
    assert [row.dia.fields["D3"] for row in result if row.cable == "RG-142"] == ["3,95"]  # one row, the last wins
    assert "RG-316" not in by_cable  # joined into «RG316», not repeated


def test_family_hint_needs_a_non_digit_after_the_prefix():
    index = {"rg14": _dia("RG14", "1", "2", "3", "1"), "rg58": _dia("RG58", "1", "2", "3", "2")}
    assert family_dia("rg142", index) is None
    assert family_dia("rg58c/u", index).fields["Кабель"] == "RG58"


def test_manual_kind_beats_the_rule():
    [row] = cable_rows([_wire(*PROV_ROWS[3])], [], {"пвам0,50": CableKind.OTHER})
    assert (row.kind, row.auto, row.manual, row.reason) == (CableKind.OTHER, CableKind.WIRE, True, "перенесён вручную")


# --- application + mirror ---

def _mirror(tmp_path, prov=PROV, spr_kab=SPR_KAB):
    source = mirror(tmp_path, БД_ПРОВ=prov)
    (source / "_local" / "db" / "СПР.КАБ.tsv").write_text(spr_kab, encoding="utf-8", newline="")
    return source


def _row(uow, cable):
    [row] = [item for item in list_cables(uow) if item.cable == cable]
    return row


def _kab(uow):
    with uow:
        return [item.fields for item in uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=100)]


def test_edit_diameters_of_a_joined_cable_then_mirror_reload_keeps_them(tmp_path, uow):
    load_mirror(_mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    row = _row(uow, "RG316")

    saved = set_coax_diameters(SetCoaxDiametersCommand(
        "RG316", row.dia.id, {"D1": "0,5", "D2": "1,48", "D3": "1,95"}, {"D1": "0,51", "D2": "1,48", "D3": "2"}, ACTOR, NOW), uow)
    load_mirror(_mirror(tmp_path / "b"), uow, dry_run=False, now=NOW)

    assert saved.id == row.dia.id and saved.edited_in_erp
    kab = _kab(uow)
    assert [fields for fields in kab if fields["Кабель"] == "RG-316"] == [{"Кабель": "RG-316", "D1": "0,51", "D2": "1,48", "D3": "2"}]
    assert list(kab[0]) == ["Кабель", "D1", "D2", "D3"]  # the generator takes the cable from the first column
    assert _row(uow, "RG316").dia.fields["D1"] == "0,51"


def test_diameters_for_a_cable_without_a_row_create_one_named_as_the_cable(tmp_path, uow):
    load_mirror(_mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)

    created = set_coax_diameters(SetCoaxDiametersCommand(
        "RG-58 A/U", None, {}, {"D1": "0,9", "D2": "2,95", "D3": "3,5"}, ACTOR, NOW), uow)
    load_mirror(_mirror(tmp_path / "b"), uow, dry_run=False, now=NOW)

    assert list(created.fields.items()) == [("Кабель", "RG-58 A/U"), ("D1", "0,9"), ("D2", "2,95"), ("D3", "3,5")]
    row = _row(uow, "RG-58 A/U")
    assert row.dia.id == created.id and row.dia_hint is None
    assert sum(1 for fields in _kab(uow) if fields["Кабель"] == "RG-58 A/U") == 1  # survives, not doubled


def test_diameters_refuse_stale_values_formulas_and_text(tmp_path, uow):
    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    row = _row(uow, "RG316")
    with pytest.raises(StaleRecordError):
        set_coax_diameters(SetCoaxDiametersCommand("RG316", row.dia.id, {"D1": "9"}, {"D1": "1"}, ACTOR, NOW), uow)
    with pytest.raises(StaleRecordError):  # the screen saw no row, but there is one now
        set_coax_diameters(SetCoaxDiametersCommand("RG316", None, {}, {"D1": "1"}, ACTOR, NOW), uow)
    for bad in ("=A1", "один", "1,2,3"):
        with pytest.raises(ReferenceValidationError):
            set_coax_diameters(SetCoaxDiametersCommand("RG316", row.dia.id, {}, {"D1": bad}, ACTOR, NOW), uow)
    with pytest.raises(ReferenceValidationError):
        set_coax_diameters(SetCoaxDiametersCommand("КВСФ-75", None, {}, {"D1": "", "D2": "", "D3": ""}, ACTOR, NOW), uow)
    assert _row(uow, "RG316").dia.fields["D1"] == "0,5"


def test_move_between_lists_survives_a_mirror_reload_and_moving_back_clears_it(tmp_path, uow):
    load_mirror(_mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    pvam = _row(uow, "ПВАМ 0,50")

    moved = set_cable_kind(SetCableKindCommand(pvam.record.id, CableKind.OTHER, ACTOR, NOW), uow)
    load_mirror(_mirror(tmp_path / "b", prov=PROV.replace("1,6", "1,7")), uow, dry_run=False, now=NOW)

    assert moved.kind == CableKind.OTHER and moved.manual
    again = _row(uow, "ПВАМ 0,50")
    assert (again.kind, again.manual) == (CableKind.OTHER, True)
    assert again.record.fields["Наружный диаметр, мм"] == "1,7"  # the row itself still follows the mirror
    assert not again.record.edited_in_erp

    back = set_cable_kind(SetCableKindCommand(again.record.id, CableKind.WIRE, ACTOR, NOW), uow)
    assert (back.kind, back.manual) == (CableKind.WIRE, False)
    with uow:
        assert not [entry for entry in uow.references.list() if entry.kind == ReferenceKind.CABLE_KIND]


def test_move_needs_a_wire_record(tmp_path, uow):
    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    diameter_only = _row(uow, "RG58")
    with pytest.raises(LookupError):
        set_cable_kind(SetCableKindCommand(diameter_only.dia.id, CableKind.WIRE, ACTOR, NOW), uow)


# --- HTTP ---

@pytest.fixture()
def cables_api(sessions):  # noqa: F811
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit", "procurement": "edit"})
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 4, 10, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.references_uow_factory


def test_api_lists_moves_and_edits_cables(tmp_path, sessions, cables_api):  # noqa: F811
    load_mirror(_mirror(tmp_path), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)

    listed = cables_api.get("/api/references/cables").json()
    assert listed["counts"] == {"coax": 5, "wire": 2, "multi": 2, "other": 1}
    assert listed["columns"][:2] == ["Марка", "Тип провода"]
    rg316 = next(row for row in listed["rows"] if row["cable"] == "RG316")
    assert rg316["kind"] == "coax" and rg316["dia"]["cable"] == "RG-316" and rg316["record"]["fields"]["Волновое сопротивление, Ом"] == "50"
    rg58 = next(row for row in listed["rows"] if row["cable"] == "RG58")
    assert rg58["record"] is None and rg58["id"] == f"dia:{rg58['dia']['id']}"
    hinted = next(row for row in listed["rows"] if row["cable"] == "RG-58 A/U")
    assert hinted["dia"] is None and hinted["dia_hint"]["D3"] == "3,5"

    saved = cables_api.put("/api/references/cables/diameters", json={
        "cable": "RG316", "dia_id": rg316["dia"]["id"], "old": {"D1": "0,5", "D2": "1,48", "D3": "1,95"},
        "value": {"D1": "0,5", "D2": "1,5", "D3": "1,95"}})
    stale = cables_api.put("/api/references/cables/diameters", json={
        "cable": "RG316", "dia_id": rg316["dia"]["id"], "old": {"D1": "0,5", "D2": "1,48", "D3": "1,95"},
        "value": {"D1": "0,5", "D2": "1,6", "D3": "1,95"}})
    bad = cables_api.put("/api/references/cables/diameters", json={"cable": "RG-179", "value": {"D1": "x"}})
    assert saved.status_code == 200 and saved.json()["D2"] == "1,5"
    assert stale.status_code == 409 and bad.status_code == 422
    # what the ETK generator reads (tcGenLogic coaxDiaFromRecords: first column = cable, D1–D3 by name)
    kab = cables_api.get("/api/references/tech-operations", params={"tab": "kab_coax"}).json()["records"]
    assert [list(item["fields"].values()) for item in kab if item["display_text"] == "RG-316"] == [["RG-316", "0,5", "1,5", "1,95"]]

    pvam = next(row for row in listed["rows"] if row["cable"] == "ПВАМ 0,50")
    moved = cables_api.put(f"/api/references/cables/{pvam['id']}/kind", json={"kind": "coax"})
    assert moved.status_code == 200 and moved.json()["kind"] == "coax" and moved.json()["manual"]
    assert cables_api.put(f"/api/references/cables/{rg58['dia']['id']}/kind", json={"kind": "wire"}).status_code == 404
    assert cables_api.put(f"/api/references/cables/{pvam['id']}/kind", json={"kind": "кабель"}).status_code == 422
    after = cables_api.get("/api/references/cables").json()
    assert after["counts"] == {"coax": 6, "wire": 1, "multi": 2, "other": 1}

    # the move is not a supplier/country map row, and saving the maps does not drop it
    maps = cables_api.get("/api/references/maps").json()
    assert "ПВАМ 0,50" not in str(maps)
    cables_api.put("/api/references/maps", json={"suppliers": [{"key": "склад", "value": "Склад"}], "countries": [], "gost_awg": []})
    assert cables_api.get("/api/references/cables").json()["counts"]["coax"] == 6

    bases = cables_api.get("/api/references/bases").json()
    assert bases["cables"] == {"coax": 6, "wire": 1, "multi": 2, "other": 1}
    assert bases["tabs"]["kab_awg"] == 1 and bases["tabs"]["kab_coax"] == 4 and bases["tabs"]["prov"] == len(PROV_ROWS)


def test_api_cable_edits_need_the_reference_edit_right(tmp_path, sessions, cables_api):  # noqa: F811
    load_mirror(_mirror(tmp_path), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
    pvam = next(row for row in cables_api.get("/api/references/cables").json()["rows"] if row["cable"] == "ПВАМ 0,50")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="viewer@example.test", roles=frozenset({"receiver"}), permissions={"erpref": "view"})

    assert cables_api.get("/api/references/cables").status_code == 200
    assert cables_api.put(f"/api/references/cables/{pvam['id']}/kind", json={"kind": "other"}).status_code == 403
    assert cables_api.put("/api/references/cables/diameters", json={"cable": "RG-179", "value": {"D1": "1"}}).status_code == 403


def test_record_ids_are_uuids_for_wire_rows(tmp_path, sessions, cables_api):  # noqa: F811
    load_mirror(_mirror(tmp_path), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
    for row in cables_api.get("/api/references/cables").json()["rows"]:
        if row["record"]:
            assert UUID(row["id"]) and row["record"]["tab"] == "prov"
