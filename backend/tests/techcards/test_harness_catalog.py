"""Каталог редактора жгута из наших справочников (H-T4): отображение на зеркале backend/data/reference-mirror/
и ручка GET /api/techcards/harness-catalog."""

from datetime import datetime
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import BUNDLED_MIRROR, load_mirror, read_mirror
from app.modules.techcards.harness_catalog import (
    COAX_CABLES, COAX_TERMINATIONS, CONNECTORS, DATABASE, TERMINALS, WIRES, build_harness_catalog, number,
    reference_book,
)
from app.shared.persistence import create_schema

SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture(scope="module")
def mirror_tables():
    snapshot = read_mirror(BUNDLED_MIRROR)
    return {tab: [record.fields for record in records] for tab, records in snapshot.tables.items()}


@pytest.fixture(scope="module")
def catalog(mirror_tables):
    return build_harness_catalog(mirror_tables, captured_utc="2026-09-29T09:23:00+00:00")


def _snapshot(catalog, source_id):
    return next(snapshot for snapshot in catalog["snapshots"] if snapshot["sourceId"] == source_id)


def _record(catalog, source_id, key):
    return next(record for record in _snapshot(catalog, source_id)["records"] if record["sourceKey"] == key)


def test_every_editor_source_has_a_snapshot_with_exact_identity(catalog):
    assert [snapshot["sourceId"] for snapshot in catalog["snapshots"]] == [
        WIRES, TERMINALS, COAX_CABLES, COAX_TERMINATIONS, DATABASE, CONNECTORS,
    ]
    for snapshot in catalog["snapshots"]:
        assert UUID.match(snapshot["snapshotId"]) and SHA256.match(snapshot["sha256"])
        assert snapshot["contractVersion"] == 1 and snapshot["sourceKind"] == "erp-references"
        assert snapshot["records"], snapshot["sourceId"]
        keys = [(record["entityType"], record["sourceKey"]) for record in snapshot["records"]]
        assert len(keys) == len(set(keys)), f"{snapshot['sourceId']}: ключи записей повторяются"
        assert all(SHA256.match(record["recordId"]) for record in snapshot["records"])


def test_same_references_give_the_same_snapshot(mirror_tables, catalog):
    again = build_harness_catalog(mirror_tables, captured_utc="другая дата")
    assert [s["snapshotId"] for s in again["snapshots"]] == [s["snapshotId"] for s in catalog["snapshots"]]
    edited = {**mirror_tables, "prov": [{**mirror_tables["prov"][0], "Наружный диаметр, мм": "9,99"},
                                        *mirror_tables["prov"][1:]]}
    changed = build_harness_catalog(edited, captured_utc="")
    assert _snapshot(changed, WIRES)["sha256"] != _snapshot(catalog, WIRES)["sha256"]
    assert _snapshot(changed, TERMINALS)["sha256"] == _snapshot(catalog, TERMINALS)["sha256"]


def test_terminals_carry_l_plus_and_l_minus_for_the_cut_length(catalog, mirror_tables):
    terminals = _snapshot(catalog, TERMINALS)["records"]
    source_rows = [row for row in mirror_tables["ter"] if row.get("terArticle") or row.get("Артикул контакта (BAG)")]
    assert len(terminals) == len(source_rows) > 500
    assert {record["entityType"] for record in terminals} == {"terminal"}
    with_plus = [row for row in source_rows if row.get("L+")]
    assert sum("lengthPlusMm" in record["payload"] for record in terminals) == len(with_plus) > 100
    spox = _record(catalog, TERMINALS, "08701039")["payload"]
    assert spox["lengthPlusMm"] == 5.0 and spox["manufacturer"] == "Molex" and spox["series"] == "5264"
    assert (spox["awgFrom"], spox["awgTo"], spox["sectionFromMm2"], spox["sectionToMm2"]) == (28.0, 22.0, 0.08, 0.35)
    assert spox["reelArticle"] == "08701039" and spox["bagArticle"] == "8701040"


def test_terminal_l_minus_is_a_magnitude_and_ranges_stay_text():
    catalog = build_harness_catalog({"ter": [
        {"terArticle": "T-1", "L+": "2,5", "L-": "-1,5", "Длина зачистки, мм": "3,00 - 3,50", "Шаг разьема": "2,54"},
        {"terArticle": "", "Артикул контакта (BAG)": "BAG-2", "L+": "нет данных"},
        {"terArticle": "", "Артикул контакта (BAG)": ""},
        {"terArticle": "T-1", "L+": "9"},
        {"terArticle": "T-3", "L+": "пять"},
    ]}, captured_utc="")
    snapshot = _snapshot(catalog, TERMINALS)
    first = _record(catalog, TERMINALS, "T-1")["payload"]
    assert (first["lengthPlusMm"], first["lengthMinusMm"]) == (2.5, 1.5)
    assert first["stripLengthMm"] == "3,00 - 3,50" and first["pitchMm"] == 2.54
    assert "lengthPlusMm" not in _record(catalog, TERMINALS, "BAG-2")["payload"]
    assert "lengthPlusMm" not in _record(catalog, TERMINALS, "T-3")["payload"]
    codes = sorted(diagnostic["code"] for diagnostic in snapshot["diagnostics"])
    assert codes == ["terminal-article-missing", "terminal-duplicate", "value-not-number"]
    assert [d["field"] for d in snapshot["diagnostics"] if d["code"] == "value-not-number"] == ["L+"]


def test_wires_and_multicore_cables_from_bd_prov(catalog, mirror_tables):
    wires = _snapshot(catalog, WIRES)["records"]
    assert len(wires) == len([row for row in mirror_tables["prov"] if row.get("Марка")])
    single = _record(catalog, WIRES, "UL1061 28AWG")
    assert single["entityType"] == "wire"
    assert single["payload"]["sectionMm2"] == "0,08" and single["payload"]["outerDiameterMm"] == 0.89
    cable = _record(catalog, WIRES, "ПВВС 4×0,35")
    assert cable["entityType"] == "cable"
    assert (cable["payload"]["Core"], cable["payload"]["Сечение C"]) == ("4", "0,35")
    flat = [d for d in _snapshot(catalog, WIRES)["diagnostics"] if d["sourceKey"] == "ШВПМ 2x0,5"]
    assert flat and flat[0]["field"] == "Наружный диаметр, мм"


def test_coax_cables_from_spr_kab_with_d_layers(catalog, mirror_tables):
    cables = _snapshot(catalog, COAX_CABLES)["records"]
    assert len(cables) == len({row["Кабель"] for row in mirror_tables["kab_coax"] if row.get("Кабель")})
    rf = _record(catalog, COAX_CABLES, "RF 1.13")["payload"]
    assert rf["layers"] == [{"index": 1, "diameterMm": 0.24}, {"index": 2, "diameterMm": 0.7},
                            {"index": 3, "diameterMm": 0.92}]
    assert rf["outerDiameterMm"] == 0.92


def test_coax_terminations_keep_strip_layers_and_split_shared_articles(catalog, mirror_tables):
    records = _snapshot(catalog, COAX_TERMINATIONS)["records"]
    assert len(records) == len([row for row in mirror_tables["coax"] if row.get("coaxArticle")])
    mcx = _record(catalog, COAX_TERMINATIONS, "11-111L")["payload"]
    assert mcx["layers"] == [
        {"index": 1, "diameterMm": 0.48, "stripLengthMm": 2.0},
        {"index": 2, "diameterMm": 1.52, "stripLengthMm": 4.0},
        {"index": 3, "diameterMm": 1.9, "stripLengthMm": 10.5},
    ]
    # One article for two cables: the cable is part of the key, both rows stay.
    assert _record(catalog, COAX_TERMINATIONS, "SMA-C174P / RG-174")["payload"]["cable"] == "RG-174"
    assert _record(catalog, COAX_TERMINATIONS, "SMA-C174P / RG-316")["payload"]["cable"] == "RG-316"
    # A row without strip lengths keeps its diameters; the editor marks it «данные неполные».
    bare = _record(catalog, COAX_TERMINATIONS, "MCX-female RG-174")["payload"]["layers"]
    assert bare[0] == {"index": 1, "diameterMm": 0.48, "stripLengthMm": None}


def test_coax_termination_takes_diameters_only_from_spr_kab():
    # Владелец 16.10.2026: D1–D3 — у кабеля; D, оставшиеся в строке разъёма, не читаются.
    catalog = build_harness_catalog({
        "kab_coax": [{"Кабель": "RG-316", "D1": "0,51", "D2": "1,52", "D3": "2,5"}],
        "coax": [{"coaxArticle": "SMA-X", "coaxWire": "RG 316", "D1": "9", "D2": "Не найдено", "L1": "2", "L2": "4", "L3": "9"},
                 {"coaxArticle": "SMA-Y", "coaxWire": "RG-213", "D1": "9", "D2": "9", "D3": "9", "L1": "3"}],
    }, captured_utc="")
    payload = _record(catalog, COAX_TERMINATIONS, "SMA-X")["payload"]
    assert [layer["diameterMm"] for layer in payload["layers"]] == [0.51, 1.52, 2.5]
    assert payload["diametersFrom"] == "СПР.КАБ"
    no_cable = _record(catalog, COAX_TERMINATIONS, "SMA-Y")["payload"]
    assert no_cable["layers"] == [{"index": 1, "diameterMm": None, "stripLengthMm": 3.0}]
    assert not no_cable.get("diametersFrom")
    assert _snapshot(catalog, COAX_TERMINATIONS)["diagnostics"] == []


def test_coverings_are_only_protective_materials_of_bd_mat(catalog, mirror_tables):
    coverings = [r for r in _snapshot(catalog, DATABASE)["records"] if r["entityType"] == "protective-covering"]
    groups = {record["payload"]["group"] for record in coverings}
    assert {"ТУТ", "оплётка", "лента"} <= groups
    assert not groups & {"стяжка", "хомут", "припой/флюс", "клей/герметик", "бирка"}
    tut = _record(catalog, DATABASE, "ТУТнг 4/2 черная")["payload"]
    assert tut["coveringKind"] == "heat-shrink" and tut["size"] == "4/2 мм" and tut["color"] == "черный"
    assert (tut["diameterBeforeMm"], tut["diameterAfterMm"]) == (4.0, 2.0)
    # The universal base also lists wires and coax cables, with the same record identity as their own sources.
    database_ids = {record["recordId"] for record in _snapshot(catalog, DATABASE)["records"]}
    assert {record["recordId"] for record in _snapshot(catalog, WIRES)["records"]} <= database_ids
    assert {record["recordId"] for record in _snapshot(catalog, COAX_CABLES)["records"]} <= database_ids


def test_connector_families_with_patterns_and_aliases(catalog):
    records = _snapshot(catalog, CONNECTORS)["records"]
    families = {r["payload"]["familyId"]: r["payload"] for r in records if r["entityType"] == "connector-family"}
    xh = families["jst-xh"]
    assert xh["pattern"] == r"^XHP-(\d+)" and xh["pitchMm"] == 2.5 and xh["contactCount"] == 4
    # The drawing's «Реальные размеры, мм» measures the library's JST SH drawings against this pitch.
    sh = families["jst-sh"]
    assert sh["pitchMm"] == 1.0 and re.search(sh["pattern"], "SHR-04V-S", re.I)
    aliases = {r["sourceKey"]: r["payload"] for r in records if r["entityType"] == "connector-alias"}
    assert aliases["LP-12"]["replacement"] == "LP-12-C02PE"
    assert aliases[r"^BL2018P-0*(\d+)"]["replacement"] == "SMP-0$1V-BC"


def test_reference_book_holds_what_the_pickers_offer(catalog, mirror_tables):
    """A drawing may reference what the editor's pickers offer: the save checks new references against this."""
    book = reference_book(mirror_tables)
    assert {"UL1061 28AWG", "ПВВС 4×0,35", "RF 1.13"} <= book.wires
    assert "11-111L" in book.strips
    terminal = _snapshot(catalog, TERMINALS)["records"][0]
    assert terminal["sourceKey"] in book.terminals
    assert {terminal["payload"].get("reelArticle"), terminal["payload"].get("bagArticle")} - {None} <= book.terminals
    covering = next(record for record in _snapshot(catalog, DATABASE)["records"]
                    if record["entityType"] == "protective-covering")
    assert covering["sourceKey"] in book.coverings
    assert book.missing({("wire", "ul1061  28awg"): "Провод 1", ("wire", "НЕТ ТАКОГО"): "Провод 2"}) == [
        "Провод 2: провода «НЕТ ТАКОГО» нет в БД.ПРОВ"]


def test_number_reads_sheet_cells():
    assert number("2,45") == 2.45 and number("−1,5") == -1.5 and number("35 000,00") == 35000.0
    assert number("3 мм") == 3.0
    assert number("4,8×2,34") is None and number("") is None and number("—") is None


@pytest.fixture()
def catalog_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    from uuid import UUID as Uuid

    from app.shared.http_auth import Principal, authenticated_principal
    from app.shared.identity import Actor

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=Uuid(int=8), name="Технолог", department="Производство"), email="tech@example.test",
        roles=frozenset({"staff"}), permissions={"projects": "view"},
    )
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    del app.state.references_uow_factory
    engine.dispose()


def test_catalog_endpoint_serves_the_loaded_references(catalog_api, catalog):
    load_mirror(BUNDLED_MIRROR, app.state.references_uow_factory(), dry_run=False, now=datetime(2026, 10, 6, 12, 0))
    response = catalog_api.get("/api/techcards/harness-catalog")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"] == catalog["counts"]
    assert _snapshot(body, TERMINALS)["sha256"] == _snapshot(catalog, TERMINALS)["sha256"]
    assert body["capturedUtc"].startswith("2026-09-29")


def test_catalog_endpoint_without_references_is_empty_not_an_error(catalog_api):
    response = catalog_api.get("/api/techcards/harness-catalog")
    assert response.status_code == 200
    assert all(count == 0 for source, count in response.json()["counts"].items() if source != CONNECTORS)


@pytest.mark.parametrize("permissions, status", [
    ({"projects": "view"}, 200),     # the drawing's own right: whoever reads the drawing reads its pickers
    ({"warehouse": "edit"}, 403),
    ({"techcards": "view"}, 403),    # without «Проекты» the drawing itself does not open
])
def test_catalog_endpoint_reads_with_the_drawing_right(catalog_api, permissions, status):
    from uuid import UUID as Uuid

    from app.shared.http_auth import Principal, authenticated_principal
    from app.shared.identity import Actor

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=Uuid(int=7), name="Сотрудник", department="Отдел"), email="staff@example.test",
        roles=frozenset({"staff"}), permissions=permissions,
    )
    try:
        assert catalog_api.get("/api/techcards/harness-catalog").status_code == status
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
