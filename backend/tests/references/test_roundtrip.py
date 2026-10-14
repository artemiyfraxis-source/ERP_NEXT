"""«Скачать для правки» / «Загрузить исправленную» (owner 14.10.2026: «во всех базах данных должна быть возможность
скачать таблицу и вставить исправленную»): the downloaded file loads back with no change; an edited cell is previewed
(field, before → after) without a write and applied with the edit form's audit; a row without an ID is created; an
unknown ID, a record changed after the download (conflict) and a row the validators refuse are reported and skipped
while the other rows apply; a viewer downloads but does not load."""

import base64
import io
import zipfile
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.references.roundtrip import CONFLICT_REASON, NOT_FOUND_REASON
from app.modules.references.xlsx_file import Sheet, read_xlsx, write_xlsx
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)

PROV = [
    {"Марка": "МГТФ 0,2", "Тип провода": "монтажный", "Сечение, мм2": "0,2", "Цвета": "белый"},
    {"Марка": "МГШВ 0,5", "Тип провода": "монтажный", "Сечение, мм2": "0,5", "Цвета": "красный"},
]


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


def _seed(client: TestClient) -> list[dict]:
    response = client.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "records": [{"tab": "prov", "display_text": row["Марка"], "source_sheet": "БД.ПРОВ", "fields": row}
                    for row in PROV],
    })
    assert response.status_code == 200, response.text
    return client.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"]


def _download(client: TestClient, table: str = "prov", titles: dict | None = None, kind: str = "") -> bytes:
    response = client.post(f"/api/references/import/{table}/file/export", json={"titles": titles or {}, "kind": kind})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_type"].startswith("application/vnd.openxmlformats") and body["file_name"].endswith(".xlsx")
    return base64.b64decode(body["content_base64"])


def _rows(content: bytes) -> list[list[str]]:
    main = read_xlsx(content)[0]
    return [["" if cell is None else str(cell) for cell in row] for row in main.rows]


def _edited(content: bytes, edit) -> bytes:
    """The file as a user saves it again: the same sheets, the main one changed by `edit(rows)`."""
    main, service = read_xlsx(content)
    rows = [["" if cell is None else cell for cell in row] for row in main.rows]
    edit(rows)
    rows = [[cell if isinstance(cell, str) else _number(cell) for cell in row] for row in rows]
    return write_xlsx([Sheet(main.name, rows, hidden_columns={1}),
                       Sheet(service.name, [[str(cell or "") for cell in row] for row in service.rows], hidden=True)])


def _number(value) -> str:
    return str(value)


def _upload(client: TestClient, content: bytes, *, dry_run: int, table: str = "prov"):
    return client.post(f"/api/references/import/{table}/file", params={"dry_run": dry_run},
                       json={"file_name": "правка.xlsx", "content_base64": base64.b64encode(content).decode()})


def _column(rows: list[list[str]], title: str) -> int:
    return rows[0].index(title)


def _audit(event_type: str) -> list[AuditEventRow]:
    sessions = app.state.references_uow_factory().session_factory
    with sessions() as session:
        return session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type)).all()


def _prov(client: TestClient) -> dict[str, dict]:
    records = client.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"]
    return {record["id"]: record["fields"] for record in records}


def test_the_downloaded_file_carries_id_version_every_field_and_the_screen_titles(references_api):
    records = _seed(references_api)
    rows = _rows(_download(references_api, titles={"Цвета": "Цвет изоляции"}))

    assert rows[0][:2] == ["ID записи", "Версия"]
    assert "Марка" in rows[0] and "Цвет изоляции" in rows[0]
    assert "Категория" in rows[0]  # every column of БД.ПРОВ, not just the visible ones
    assert sorted(row[0] for row in rows[1:]) == sorted(record["id"] for record in records)
    assert all(len(row[1]) == 12 for row in rows[1:])
    service = read_xlsx(_download(references_api))[1]
    assert service.name == "Поля" and service.hidden


def test_an_unchanged_file_loads_back_with_no_change(references_api):
    _seed(references_api)
    before = _prov(references_api)
    content = _download(references_api)

    preview = _upload(references_api, content, dry_run=1).json()
    applied = _upload(references_api, content, dry_run=0).json()

    assert preview["unchanged"] == 2 and not preview["updated"] and not preview["created"]
    assert not (preview["errors"] or preview["conflicts"] or preview["not_found"])
    assert applied["applied"] is False and applied["unchanged"] == 2
    assert _prov(references_api) == before
    assert _audit("references.tech_record_edited") == []


def test_a_changed_cell_is_previewed_without_a_write_then_applied_and_audited(references_api):
    records = _seed(references_api)
    target = next(record for record in records if record["fields"]["Марка"] == "МГТФ 0,2")
    content = _download(references_api)

    def edit(rows):
        colors = rows[0].index("Цвета")
        row = next(row for row in rows if row[0] == target["id"])
        row[colors] = "синий"

    edited = _edited(content, edit)
    preview = _upload(references_api, edited, dry_run=1)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True and body["applied"] is False
    assert body["updated"] == [{"row": body["updated"][0]["row"], "id": target["id"], "title": "МГТФ 0,2",
                                "changes": [{"field": "Цвета", "title": "Цвета", "old": "белый", "new": "синий"}]}]
    assert body["unchanged"] == 1
    assert _prov(references_api)[target["id"]]["Цвета"] == "белый"  # the dry run wrote nothing
    assert _audit("references.tech_record_edited") == []

    applied = _upload(references_api, edited, dry_run=0).json()

    assert applied["applied"] is True and len(applied["updated"]) == 1
    assert _prov(references_api)[target["id"]]["Цвета"] == "синий"
    [event] = _audit("references.tech_record_edited")
    assert event.details["record_id"] == target["id"] and event.details["fields"] == ["Цвета"]
    assert event.details["via"] == "file"


def test_a_row_without_id_is_created_and_a_missing_row_is_left_alone(references_api):
    records = _seed(references_api)
    content = _download(references_api)

    def edit(rows):
        width = len(rows[0])
        new = [""] * width
        new[rows[0].index("Марка")] = "ПВАМ 0,75"
        new[rows[0].index("Цвета")] = "чёрный"
        rows.append(new)
        del rows[1]  # a row taken out of the file: «не трогать», never a delete

    applied = _upload(references_api, _edited(content, edit), dry_run=0).json()
    after = _prov(references_api)

    assert [item["title"] for item in applied["created"]] == ["ПВАМ 0,75"]
    assert len(after) == 3 and all(record["id"] in after for record in records)
    created = next(fields for fields in after.values() if fields.get("Марка") == "ПВАМ 0,75")
    assert created["Цвета"] == "чёрный"
    [event] = _audit("references.tech_record_created")
    assert event.details["via"] == "file"


def test_unknown_id_conflict_and_a_refused_row_are_reported_while_the_rest_applies(references_api):
    records = _seed(references_api)
    first = next(record for record in records if record["fields"]["Марка"] == "МГТФ 0,2")
    second = next(record for record in records if record["fields"]["Марка"] == "МГШВ 0,5")
    content = _download(references_api)
    # someone edits the first record after the download
    assert references_api.patch(f"/api/references/tech-operations/{first['id']}", json={
        "edits": [{"field": "Тип провода", "old": "монтажный", "value": "силовой"}]}).status_code == 200

    def edit(rows):
        colors = rows[0].index("Цвета")
        kind = rows[0].index("Тип провода")
        for row in rows[1:]:
            if row[0] == first["id"]:
                row[colors] = "зелёный"  # a conflict: skipped, the newer edit stays
            if row[0] == second["id"]:
                row[colors] = "жёлтый"  # applies
        bad = list(rows[1])
        bad[0] = str(UUID(int=7))
        rows.append(bad)  # an ID the base never had
        formula = [""] * len(rows[0])
        formula[rows[0].index("Марка")] = "=A1"  # the validators refuse it, only this row
        formula[kind] = "монтажный"
        rows.append(formula)

    body = _upload(references_api, _edited(content, edit), dry_run=0).json()
    after = _prov(references_api)

    assert [item["id"] for item in body["conflicts"]] == [first["id"]]
    assert body["conflicts"][0]["reason"] == CONFLICT_REASON
    assert [item["id"] for item in body["not_found"]] == [str(UUID(int=7))]
    assert body["not_found"][0]["reason"] == NOT_FOUND_REASON
    assert len(body["errors"]) == 1 and "Формулы" in body["errors"][0]["reason"]
    assert [item["id"] for item in body["updated"]] == [second["id"]]
    assert after[first["id"]]["Цвета"] == "белый" and after[first["id"]]["Тип провода"] == "силовой"
    assert after[second["id"]]["Цвета"] == "жёлтый"
    assert len(after) == 2


def test_a_row_refused_after_its_write_leaves_nothing_behind(references_api, monkeypatch):
    """The savepoint: a validator that fails after the row was saved undoes that row only."""
    from app.modules.references import roundtrip

    records = _seed(references_api)
    content = _download(references_api)
    original = roundtrip._TechTable.update

    def failing(self, uow, item, changes, actor, now):
        original(self, uow, item, changes, actor, now)
        if item.values.get("Марка") == "МГТФ 0,2":
            raise roundtrip.ReferenceValidationError("проверка после записи")

    monkeypatch.setattr(roundtrip._TechTable, "update", failing)

    def edit(rows):
        colors = rows[0].index("Цвета")
        for row in rows[1:]:
            row[colors] = "оранжевый"

    body = _upload(references_api, _edited(content, edit), dry_run=0).json()
    after = _prov(references_api)

    assert len(body["errors"]) == 1 and len(body["updated"]) == 1
    by_mark = {fields["Марка"]: fields for fields in after.values()}
    assert by_mark["МГТФ 0,2"]["Цвета"] == "белый" and by_mark["МГШВ 0,5"]["Цвета"] == "оранжевый"
    assert len(_audit("references.tech_record_edited")) == 1
    assert {record["id"] for record in records} == set(after)


def test_a_number_excel_stored_as_a_number_keeps_the_comma_of_the_base(references_api):
    records = _seed(references_api)
    target = next(record for record in records if record["fields"]["Марка"] == "МГТФ 0,2")
    content = _download(references_api)
    main, service = read_xlsx(content)
    rows = [["" if cell is None else str(cell) for cell in row] for row in main.rows]
    section = rows[0].index("Сечение, мм2")
    number_row = next(index for index, row in enumerate(rows) if row[0] == target["id"])
    # Excel: the cell retyped as a number → <c><v>0.35</v></c>
    edited = _with_number(write_xlsx([Sheet(main.name, rows, hidden_columns={1}),
                                      Sheet(service.name, [[str(c or "") for c in r] for r in service.rows],
                                            hidden=True)]), number_row, section, "0.35")

    body = _upload(references_api, edited, dry_run=1).json()

    assert body["updated"][0]["changes"] == [{"field": "Сечение, мм2", "title": "Сечение, мм2", "old": "0,2",
                                              "new": "0,35"}]


def _with_number(content: bytes, row_index: int, column: int, value: str) -> bytes:
    from app.modules.references.xlsx_file import column_letter
    ref = f"{column_letter(column)}{row_index + 1}"
    source = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode()
                start = text.index(f'<c r="{ref}"')
                end = text.index("</c>", start) + len("</c>")
                text = text[:start] + f'<c r="{ref}"><v>{value}</v></c>' + text[end:]
                data = text.encode()
            target.writestr(item, data)
    return out.getvalue()


def test_a_file_resaved_with_shared_strings_reads_back():
    shared = ('<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<si><t>ID записи</t></si><si><r><t>Мар</t></r><r><t>ка</t></r></si></sst>')
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
             '<row r="3"><c r="C3"><v>12</v></c><c r="D3" t="b"><v>1</v></c></row></sheetData></worksheet>')
    workbook = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                '<sheet name="Лист" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="/xl/worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="sharedStrings" Target="sharedStrings.xml"/></Relationships>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    [sheet_read] = read_xlsx(buffer.getvalue())

    assert sheet_read.rows == [["ID записи", None, "Марка"], [], [None, None, 12.0, True]]


def test_not_an_xlsx_or_a_file_of_another_table_is_refused_whole(references_api):
    _seed(references_api)
    garbage = _upload(references_api, b"not a zip", dry_run=1)
    other = _upload(references_api, _download(references_api, "wire-gauges"), dry_run=1)

    assert garbage.status_code == 422 and "xlsx" in garbage.json()["detail"]
    assert other.status_code == 422 and "другой таблицы" in other.json()["detail"]


def test_size_rows_and_maps_go_through_their_own_saves(references_api):
    assert references_api.post("/api/references/wire-gauges", json={"awg": "20", "section_mm2": "0,5"}).status_code == 201
    content = _download(references_api, "wire-gauges")
    rows = _rows(content)
    assert rows[0] == ["ID записи", "Версия", "AWG", "Сечение, мм²", "Ø жилы, мм"]

    def edit(rows):
        rows[1][rows[0].index("Ø жилы, мм")] = "0,81"
        rows.append(["", "", "22", "0,35", ""])

    body = _upload(references_api, _edited(content, edit), dry_run=0, table="wire-gauges").json()
    gauges = references_api.get("/api/references/cables").json()["gauges"]

    assert len(body["updated"]) == 1 and len(body["created"]) == 1 and not body["errors"]
    assert sorted((gauge["awg"], gauge["conductor_d_mm"]) for gauge in gauges) == [("20", 0.81), ("22", None)]
    assert all(event.details.get("via") == "file" for event in _audit("references.wire_gauge_saved")[1:])

    countries = _download(references_api, "countries")

    def edit_map(rows):
        rows.append(["", "", "Акме Коннект", "Россия"])

    applied = _upload(references_api, _edited(countries, edit_map), dry_run=0, table="countries").json()
    shown = references_api.get("/api/references/maps").json()["countries"]

    assert [item["title"] for item in applied["created"]] == ["Акме Коннект"]
    assert {"key": "Акме Коннект", "value": "Россия"} in shown


def test_terminal_columns_under_the_screen_titles_read_back_even_when_moved(references_api):
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [{
        "tab": "ter", "display_text": "JST | ZH", "source_sheet": "БД.ТЕР",
        "fields": {"terManufacturer": "JST", "terArticle": "ZHR-5", "terStep": "1,5"}}]}).status_code == 200
    content = _download(references_api, "ter", titles={"terArticle": "Артикул контакта", "terStep": "Шаг",
                                                       "terManufacturer": "Производитель"})
    main, service = read_xlsx(content)
    rows = [["" if cell is None else str(cell) for cell in row] for row in main.rows]
    assert rows[0][2:] == ["Производитель", "Артикул контакта", "Шаг"]
    rows = [[row[0], row[1], row[4], row[3], row[2]] for row in rows]  # the user moved the columns
    rows[1][2] = "1,6"
    edited = write_xlsx([Sheet(main.name, rows), Sheet(service.name, [[str(c or "") for c in r] for r in service.rows],
                                                       hidden=True)])

    body = _upload(references_api, edited, dry_run=0, table="ter").json()
    [record] = references_api.get("/api/references/tech-operations", params={"tab": "ter"}).json()["records"]

    assert body["updated"][0]["changes"] == [{"field": "terStep", "title": "Шаг", "old": "1,5", "new": "1,6"}]
    assert record["fields"]["terStep"] == "1,6"


def test_a_viewer_downloads_but_does_not_load_and_maps_stay_the_buyers(references_api):
    _seed(references_api)
    content = _download(references_api)
    viewer = Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Мастер", department="Цех")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=viewer, email="master@example.test", roles=frozenset({"foreman"}), permissions={"erpref": "view"})
    assert references_api.post("/api/references/import/prov/file/export", json={}).status_code == 200
    assert _upload(references_api, content, dry_run=1).status_code == 403

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=viewer, email="master@example.test", roles=frozenset({"tech_lead"}), permissions={"erpref": "edit"})
    countries = _download(references_api, "countries")
    assert _upload(references_api, countries, dry_run=1, table="countries").status_code == 403
    assert _upload(references_api, content, dry_run=1).status_code == 200
