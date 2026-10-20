"""БД.ПРОВ / БД.МАТ import (REF-PROV-MAT): sheet tables next to the v14 snapshot."""

import pytest

from app.modules.references.technology import import_supplymap_snapshot, parse_mirror_tsv
from tests.references.test_api import references_api  # noqa: F401  (fixture)

PROV_TSV = (
    "Марка\tТип провода\tСечение, мм2\tAWG\n"
    "RG316\tкоаксиальный\t\t\n"
    "UL1061 28AWG\tмонтажный\t0,08\t28\n"
    "\t\t\t\n"
    "МГТФ\tмонтажный\n"
)
MAT_TSV = "Наименование\tГруппа\tЕд. изм.\nТУТнг 4/2 черная\tТУТ\tм\n"
META = {"schemaVersion": 14, "updatedAt": "2026-09-28T18:20:00.000Z"}


def _snapshot(**tables):
    return {"meta": META, "records": [], "tables": tables}


def test_parses_mirror_tsv_rows_keyed_by_header_with_empty_cells_kept():
    snapshot = import_supplymap_snapshot(_snapshot(prov=parse_mirror_tsv(PROV_TSV)))

    assert [item.display_text for item in snapshot.records] == ["RG316", "UL1061 28AWG", "МГТФ"]
    rg, ul, mgtf = snapshot.records
    assert rg.tab == "prov" and rg.source_sheet == "БД.ПРОВ" and rg.sort_key == "RG316"
    assert rg.fields == {"Марка": "RG316", "Тип провода": "коаксиальный", "Сечение, мм2": "", "AWG": ""}
    assert ul.fields["AWG"] == "28"
    assert mgtf.fields["Сечение, мм2"] == ""  # short row is padded, blank filler row skipped
    assert snapshot.declared_tabs == ("prov",)


def test_formula_columns_are_left_out_and_formula_only_rows_are_filler():
    table = {"header": ["Марка", "Сумма"], "formulaCols": [1], "rows": [["A", "=1+1"], ["", "=2+2"]]}

    records = import_supplymap_snapshot(_snapshot(mat=table)).records

    assert [item.fields for item in records] == [{"Марка": "A"}]


@pytest.mark.parametrize("table", [
    {"header": ["A", "A"], "rows": []},
    {"header": ["A"], "rows": [["1", "лишнее"]]},
    {"header": ["A"]},
    "не таблица",
])
def test_rejects_malformed_tables(table):
    with pytest.raises(ValueError):
        import_supplymap_snapshot(_snapshot(prov=table))


def test_rejects_unknown_table():
    with pytest.raises(ValueError):
        import_supplymap_snapshot(_snapshot(ter={"header": ["A"], "rows": []}))


def _put(client, **tables):
    return client.put("/api/references/tech-operations/supplymap-snapshot", json=_snapshot(**tables))


def _rows(client, tab):
    return client.get("/api/references/tech-operations", params={"tab": tab}).json()


def test_imports_both_bases_and_exposes_schema(references_api):
    response = _put(references_api, prov=parse_mirror_tsv(PROV_TSV), mat=parse_mirror_tsv(MAT_TSV))

    assert response.status_code == 200 and response.json() == {"records": 4, "schema_version": 14}
    prov, mat = _rows(references_api, "prov"), _rows(references_api, "mat")
    assert len(prov["records"]) == 3 and len(mat["records"]) == 1
    assert prov["columns"][0] == "Марка" and mat["columns"][0] == "Наименование"
    found = references_api.get("/api/references/tech-operations", params={"tab": "mat", "q": "тутнг"}).json()
    assert found["records"][0]["fields"]["Ед. изм."] == "м"


def test_empty_tab_still_reports_columns_and_accepts_create(references_api):
    assert _put(references_api, prov={"header": ["Марка"], "rows": []}).status_code == 200
    empty = _rows(references_api, "prov")
    created = references_api.post("/api/references/tech-operations", json={
        "tab": "prov", "fields": {name: "МГТФ" if name == "Марка" else "" for name in empty["columns"]},
    })

    assert empty["records"] == [] and len(empty["columns"]) == 25  # 24 sheet columns + the монтажный «Цвет» (0130)
    assert created.status_code == 201 and len(_rows(references_api, "prov")["records"]) == 1


def test_snapshot_without_tables_keeps_prov_and_mat_but_replaces_other_bases(references_api):
    _put(references_api, prov=parse_mirror_tsv(PROV_TSV))
    op = {"tabKey": "op", "displayText": "Резка", "normalizedSearch": "резка", "sourceSheet": "БД.ОП"}
    references_api.put("/api/references/tech-operations/supplymap-snapshot", json={"meta": META, "records": [op]})
    references_api.put("/api/references/tech-operations/supplymap-snapshot", json={"meta": META, "records": []})

    assert len(_rows(references_api, "prov")["records"]) == 3
    assert _rows(references_api, "op")["records"] == []
    assert _rows(references_api, "prov")["metadata"]["record_count"] == 3


def test_declared_empty_table_clears_the_base(references_api):
    _put(references_api, prov=parse_mirror_tsv(PROV_TSV))
    _put(references_api, prov={"header": ["Марка"], "rows": []})

    assert _rows(references_api, "prov")["records"] == []


def test_bad_table_is_422(references_api):
    assert _put(references_api, prov={"header": ["A", "A"], "rows": []}).status_code == 422
