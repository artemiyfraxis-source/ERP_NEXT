"""«Скачать для правки» / «Загрузить исправленную» and the row loads of БД.ПРОВ by cable list (owner 20.10.2026: «у
каждого своя структура»): the file of a list carries only that list's rows and columns — its sheet columns, its own
columns, its extras under the screen's Russian titles — never another list's column; a row created from the file goes
to that list; a column of another list in the file is reported as ignored and not written. A row load (`import_rows`,
the API and the ssh path alike) takes the list from «Вид кабеля» or by the rule and never creates a row without one."""

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.domain import CABLE_COLUMNS, TechOperationTab
from app.modules.references.importers import import_rows
from app.modules.references.roundtrip import COAX_TITLES, HOOKUP_TITLES, NOT_FOUND_REASON
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter, ImportRowIn
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.references.test_roundtrip import _download, _edited, _rows, _upload

COLUMNS = "/api/references/tech-operations/columns"
OWN = {"coax": "Материал диэлектрика", "wire": "Полка", "multi": "Материал изоляции жилы"}
ROWS = [
    {"Марка": "RG316", "Тип провода": "коаксиальный", "Волновое сопротивление, Ом": "50"},
    {"Марка": "UL1061 28AWG", "Тип провода": "монтажный", "AWG": "28", "Напряжение, В": "300"},
    {"Марка": "КСПВГ 4x0,20", "Тип провода": "многожильный", "Число жил": "4", "Экран": "нет"},
]
MARK_OF = {"coax": "RG316", "wire": "UL1061 28AWG", "multi": "КСПВГ 4x0,20"}
EXTRAS = {"coax": list(COAX_TITLES.values()), "wire": list(HOOKUP_TITLES.values()), "multi": []}


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


def _records(client: TestClient) -> dict[str, dict]:
    return {record["fields"]["Марка"]: record for record in
            client.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"]}


def _seed(client: TestClient) -> dict[str, dict]:
    """One row of each list and an own column of each list, filled on that list's row."""
    assert client.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": row["Марка"], "source_sheet": "БД.ПРОВ", "fields": row} for row in ROWS
    ]}).status_code == 200
    for kind, name in OWN.items():
        assert client.post(COLUMNS, json={"tab": "prov", "cable_kind": kind, "name": name}).status_code == 201
    records = _records(client)
    assert {mark: records[mark]["cable_kind"] for mark in MARK_OF.values()} == {v: k for k, v in MARK_OF.items()}
    for kind, name in OWN.items():
        record = records[MARK_OF[kind]]
        response = client.patch(f"/api/references/tech-operations/{record['id']}",
                                 json={"edits": [{"field": name, "old": "", "value": f"своё {kind}"}]})
        assert response.status_code == 200, response.text
    return _records(client)


@pytest.mark.parametrize("kind", ["coax", "wire", "multi"])
def test_the_file_of_a_list_carries_only_that_list(references_api, kind):
    records = _seed(references_api)
    rows = _rows(_download(references_api, kind=kind))
    header = rows[0][2:]

    # that list's sheet columns, its own column, then its extras under the screen's titles — nothing else
    assert header == [*CABLE_COLUMNS[kind], OWN[kind], *EXTRAS[kind]]
    # (the list's own column may be another list's sheet column: «Материал изоляции жилы» is one of ETHERNET, 25.10.2026)
    others = {name for other, columns in CABLE_COLUMNS.items() if other != kind for name in columns} - set(CABLE_COLUMNS[kind]) - {OWN[kind]}
    assert not others & set(header)
    assert not {name for other, name in OWN.items() if other != kind} & set(header)
    assert not any(title.startswith(("hookup_", "D1,")) for title in header)
    # only the rows of the list; its own column read back
    assert [row[0] for row in rows[1:]] == [records[MARK_OF[kind]]["id"]]
    assert rows[1][2 + header.index(OWN[kind])] == f"своё {kind}"
    fields = references_api.get("/api/references/import/prov/file/fields", params={"kind": kind}).json()
    assert fields["fields"] == [*CABLE_COLUMNS[kind], OWN[kind], *fields["titles"]]
    assert list(fields["titles"].values()) == EXTRAS[kind]


def test_a_moved_row_keeps_its_filled_value_out_of_the_new_list_file(references_api):
    """A row moved to the coax list keeps its filled AWG (never dropped), but AWG is no column of the coax file."""
    records = _seed(references_api)
    wire = records["UL1061 28AWG"]
    assert references_api.put(f"/api/references/cables/{wire['id']}/kind", json={"kind": "coax"}).status_code == 200

    rows = _rows(_download(references_api, kind="coax"))
    body = _upload(references_api, _download(references_api, kind="coax"), dry_run=0).json()

    assert "AWG" not in rows[0] and wire["id"] in {row[0] for row in rows[1:]}
    assert body["unchanged"] == 2 and not (body["updated"] or body["errors"])
    assert _records(references_api)["UL1061 28AWG"]["fields"]["AWG"] == "28"


def test_a_row_created_from_a_list_file_goes_to_that_list(references_api):
    _seed(references_api)
    coax = _download(references_api, kind="coax")
    multi = _download(references_api, kind="multi")

    def add(mark: str, cells: dict):
        def edit(rows):
            new = [""] * len(rows[0])
            new[rows[0].index("Марка")] = mark
            for title, value in cells.items():
                new[rows[0].index(title)] = value
            rows.append(new)
        return edit

    # no type, no cores: the rule would call either of them монтажный
    made_coax = _upload(references_api, _edited(coax, add("RG174", {"D1 жила, мм": "0,48"})), dry_run=0).json()
    made_multi = _upload(references_api, _edited(multi, add("ШВВП 2x0,5", {OWN["multi"]: "ПВХ"})), dry_run=0).json()

    assert not made_coax["errors"] and [item["title"] for item in made_coax["created"]] == ["RG174"]
    assert not made_multi["errors"] and [item["title"] for item in made_multi["created"]] == ["ШВВП 2x0,5"]
    records = _records(references_api)
    assert records["RG174"]["cable_kind"] == "coax" and records["ШВВП 2x0,5"]["cable_kind"] == "multi"
    assert "D1" not in records["RG174"]["fields"]  # the extra went to the D table, not into a БД.ПРОВ cell
    assert set(records["RG174"]["fields"]) <= {*CABLE_COLUMNS["coax"], OWN["coax"]}
    assert records["ШВВП 2x0,5"]["fields"][OWN["multi"]] == "ПВХ"
    cable = next(row for row in references_api.get("/api/references/cables").json()["rows"] if row["cable"] == "RG174")
    assert cable["kind"] == "coax" and cable["dia"]["D1"] == "0,48"


def test_a_column_of_another_list_in_the_file_is_ignored_and_not_written(references_api):
    records = _seed(references_api)
    content = _download(references_api, kind="coax")

    def edit(rows):
        rows[0] += ["AWG", OWN["wire"]]
        for row in rows[1:]:
            row += ["24", "Б-1"]
        rows[1][rows[0].index("Волновое сопротивление, Ом")] = "75"

    body = _upload(references_api, _edited(content, edit), dry_run=0).json()
    after = _records(references_api)["RG316"]["fields"]

    assert body["ignored_columns"] == ["AWG", OWN["wire"]]
    assert [item["id"] for item in body["updated"]] == [records["RG316"]["id"]]
    assert [change["field"] for change in body["updated"][0]["changes"]] == ["Волновое сопротивление, Ом"]
    assert after["Волновое сопротивление, Ом"] == "75" and "AWG" not in after and OWN["wire"] not in after


def test_a_row_of_another_list_in_the_file_is_not_found(references_api):
    records = _seed(references_api)
    content = _download(references_api, kind="coax")

    def edit(rows):
        stranger = list(rows[1])
        stranger[0] = records["КСПВГ 4x0,20"]["id"]
        rows.append(stranger)

    body = _upload(references_api, _edited(content, edit), dry_run=1).json()

    assert [(item["id"], item["reason"]) for item in body["not_found"]] == [(records["КСПВГ 4x0,20"]["id"], NOT_FOUND_REASON)]


def test_a_closed_list_is_refused(references_api):
    _seed(references_api)
    response = references_api.post("/api/references/import/prov/file/export", json={"titles": {}, "kind": "other"})
    assert response.status_code == 422


# ── Row loads: POST /api/references/import/prov and the ssh `import_rows` path ─────────────────────────────────────


def _load(client: TestClient, rows: list[dict], dry_run: int = 0) -> dict:
    response = client.post("/api/references/import/prov", params={"dry_run": dry_run},
                           json={"rows": [{"fields": fields} for fields in rows]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_row_load_takes_the_list_from_the_kind_field_or_by_the_rule(references_api):
    _seed(references_api)
    report = _load(references_api, [
        {"Марка": "RG400", "Вид кабеля": "Коаксиальные"},
        {"Марка": "LiYY 2x0,5", "Вид кабеля": "multi", OWN["multi"]: "ПВХ"},
        {"Марка": "МГТФ 0,35", "Тип провода": "монтажный"},  # no list given: the rule
        {"Марка": "FLRY 0,5", "Вид кабеля": "Прочие кабели"},
        {"Марка": "RG58", "Вид кабеля": "coax", "AWG": "20"},
        {"Марка": "UL1061 28AWG", "Вид кабеля": "Многожильные кабели"},
        {"Марка": "КСПВГ 4x0,20", "Вид кабеля": "многожильные", "Экран": "общий"},
        # a column removed from the list 23.10.2026 («Число жил» of a монтажный wire) is no column of a new row
        {"Марка": "МГТФ 0,5", "Вид кабеля": "wire", "Число жил": "1"},
    ])
    records = _records(references_api)

    assert sorted(report["created"]) == ["LiYY 2x0,5", "RG400", "МГТФ 0,35"]
    assert [item["key"] for item in report["updated"]] == ["КСПВГ 4x0,20"]  # the same list: no move, the cell written
    refused = {item["key"]: item["reason"] for item in report["refused"]}
    assert set(refused) == {"FLRY 0,5", "RG58", "UL1061 28AWG", "МГТФ 0,5"}
    assert "Вид кабеля" in refused["FLRY 0,5"]
    assert "«AWG» — не столбец списка «Коаксиальные кабели»" in refused["RG58"]
    assert "«Число жил» — не столбец списка «Монтажные провода»" in refused["МГТФ 0,5"]
    assert "Кабели и провода" in refused["UL1061 28AWG"]
    assert {mark: records[mark]["cable_kind"] for mark in ("RG400", "LiYY 2x0,5", "МГТФ 0,35", "UL1061 28AWG")} == {
        "RG400": "coax", "LiYY 2x0,5": "multi", "МГТФ 0,35": "wire", "UL1061 28AWG": "wire"}
    assert "Вид кабеля" not in records["RG400"]["fields"]  # it chooses the list, it is no cell
    assert set(records["RG400"]["fields"]) == {*CABLE_COLUMNS["coax"], OWN["coax"]}
    assert records["LiYY 2x0,5"]["fields"][OWN["multi"]] == "ПВХ"
    assert records["КСПВГ 4x0,20"]["fields"]["Экран"] == "общий"
    assert _load(references_api, [{"Марка": "RG400", "Вид кабеля": "coax"}])["unchanged"] == 1


def test_a_filled_value_a_row_already_carries_stays_editable_by_a_load(references_api):
    """«Ток, А» is a column of no list since 23.10.2026, but a row that kept a filled current may still have it
    corrected — also after a move to another list."""
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": "UL1061 28AWG", "source_sheet": "БД.ПРОВ",
         "fields": {"Марка": "UL1061 28AWG", "Тип провода": "монтажный", "Ток, А": "1,5"}}]}).status_code == 200
    wire = _records(references_api)["UL1061 28AWG"]
    assert references_api.put(f"/api/references/cables/{wire['id']}/kind", json={"kind": "multi"}).status_code == 200

    report = _load(references_api, [{"Марка": "UL1061 28AWG", "Ток, А": "2"}])

    assert [item["key"] for item in report["updated"]] == ["UL1061 28AWG"] and not report["refused"]
    assert _records(references_api)["UL1061 28AWG"]["fields"]["Ток, А"] == "2"


def test_the_ssh_import_rows_path_creates_rows_with_a_list(references_api):
    _seed(references_api)
    actor = Actor(id=UUID(int=5), name="Сервер", department="ERP")
    uow = app.state.references_uow_factory()

    report = import_rows("prov", uow, [ImportRowIn(fields={"Марка": "RG142", "Вид кабеля": "coax"}),
                                       ImportRowIn(fields={"Марка": "ПуГВ 1,5", "Тип провода": "монтажный"})],
                         actor, datetime(2026, 10, 20, 12, 0), dry_run=False, limiter=None)

    assert sorted(report.created) == ["RG142", "ПуГВ 1,5"] and not report.refused
    with uow:
        rows = {record.display_text: record for record in
                uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100)}
    assert all(record.cable_kind for record in rows.values())
    assert (rows["RG142"].cable_kind, rows["ПуГВ 1,5"].cable_kind) == ("coax", "wire")


def test_the_schema_of_the_prov_load_names_the_kind_field_and_the_columns_of_each_list(references_api):
    _seed(references_api)
    from app.modules.references.importers import import_schema

    schema = import_schema("prov", app.state.references_uow_factory())

    assert schema["kind_field"] == "Вид кабеля" and "Вид кабеля" in schema["fields"]
    assert set(OWN.values()) <= set(schema["fields"])
    # ETHERNET (25.10.2026) has no own column here
    assert schema["columns_by_kind"] == {kind: [*CABLE_COLUMNS[kind], *([OWN[kind]] if kind in OWN else [])] for kind in CABLE_COLUMNS}
