"""«Новая операция» (owner 14.10.2026): POST /api/techcards/operations writes the БД.ОП row and the card template in
one transaction — blank or a copy — and the code is unique across both."""

from uuid import UUID

from app.main import app
from app.modules.techcards.application import BLANK_STEP
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor

from .test_operation_card_api import EDIT, SOURCE_COMMIT, _sided, _template, operation_library_api  # noqa: F401

CUT_ROW = {
    "Номер": "CUT_WIRE", "Название": "Резка", "Машина": "EW-05F", "Время операции": "3,50",
    "Время подготовки, сек": "180", "Время машины, сек/оп; сек/м": "2,00",
    "opNumber": "CUT_WIRE", "opName": "Резка", "tOp": "3,50", "tPrep": "180", "tMachine": "2,00",
}


def _seed(client, templates, rows=()) -> None:
    assert client.put("/api/techcards/templates/supplymap-library", json={
        "sourceCommit": SOURCE_COMMIT, "templates": templates,
    }).status_code == 200
    if rows:
        assert client.put("/api/references/tech-operations/snapshot", json={
            "schema_version": 14,
            "records": [{"tab": "op", "display_text": f"{row['opName']} | {row['opNumber']}", "source_sheet": "БД.ОП",
                         "fields": row} for row in rows],
        }).status_code == 200


def _op_rows(client) -> list[dict]:
    return client.get("/api/references/tech-operations", params={"tab": "op"}).json()["records"]


def _codes(client) -> list[str]:
    return sorted(item["code"] for item in client.get("/api/techcards/templates").json()["items"])


def test_blank_operation_gets_a_row_and_a_card_with_one_hint_step(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")], [CUT_ROW])

    response = operation_library_api.post("/api/techcards/operations", json={"code": " NEW_OP ", "name": " Пайка  гильзы "})

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["code"], body["name"]) == ("NEW_OP", "Пайка гильзы")
    [card] = body["templates"]
    assert (card["code"], card["name"], card["side"]) == ("NEW_OP", "Пайка гильзы", "")
    assert [step["text"] for step in card["document"]["steps"]] == [BLANK_STEP]
    assert card["document"]["tooling"] == [] and card["document"]["safety"] == ""
    assert card["norms"] == {"operation_seconds": "", "setup_seconds": ""}
    # the row takes the sheet columns of БД.ОП, empty but for «Номер» / «Название»
    fields = body["record"]["fields"]
    assert fields["Номер"] == fields["opNumber"] == "NEW_OP"
    assert fields["Название"] == fields["opName"] == "Пайка гильзы"
    assert fields["Машина"] == fields["tOp"] == fields["Время операции"] == ""
    assert body["record"]["display_text"] == "Пайка гильзы | NEW_OP"
    assert _codes(operation_library_api) == ["CUT_WIRE", "NEW_OP"]
    assert sorted(row["fields"]["opNumber"] for row in _op_rows(operation_library_api)) == ["CUT_WIRE", "NEW_OP"]
    detail = operation_library_api.get(f"/api/techcards/templates/{card['id']}").json()
    assert detail["raw_rows"][0] == ["NEW_OP | Пайка гильзы"]


def test_copy_takes_steps_tooling_defects_safety_and_norms_with_the_record_edit(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")], [CUT_ROW])
    assert operation_library_api.put("/api/techcards/templates/cut-wire", json=EDIT).status_code == 200
    source = operation_library_api.get("/api/techcards/templates/cut-wire").json()["document"]

    response = operation_library_api.post("/api/techcards/operations", json={
        "code": "CUT_WIRE_long", "name": "Резка длинная", "copy_of": "CUT_WIRE",
    })

    assert response.status_code == 201, response.text
    [card] = response.json()["templates"]
    document = card["document"]
    for key in ("steps", "tooling", "defects", "safety", "revision", "designation"):
        assert document[key] == source[key], key
    assert (document["code"], document["name"], card["category"]) == ("CUT_WIRE_long", "Резка длинная", "Сборка")
    assert card["norms"] == {"operation_seconds": "3,50", "setup_seconds": "180"}
    fields = response.json()["record"]["fields"]
    assert (fields["Номер"], fields["opNumber"], fields["Название"]) == ("CUT_WIRE_long", "CUT_WIRE_long", "Резка длинная")
    assert (fields["Машина"], fields["tOp"], fields["tPrep"], fields["tMachine"]) == ("EW-05F", "3,50", "180", "2,00")
    # the source stays as it was
    again = operation_library_api.get("/api/techcards/templates/cut-wire").json()
    assert again["document"] == source and again["code"] == "CUT_WIRE"


def test_copy_of_a_two_sided_operation_copies_both_blocks(operation_library_api):
    _seed(operation_library_api, [_sided("sdr-a", "SDR_X", "A"), _sided("sdr-b", "SDR_X", "B")])

    response = operation_library_api.post("/api/techcards/operations", json={
        "code": "SDR_Y", "name": "Пайка Y", "copy_of": "SDR_X",
    })

    assert response.status_code == 201, response.text
    assert [(card["code"], card["side"], card["title"]) for card in response.json()["templates"]] == [
        ("SDR_Y", "A", "SDR_Y | Пайка Y (A)"), ("SDR_Y", "B", "SDR_Y | Пайка Y (B)"),
    ]
    # no БД.ОП row to copy from: the row is still made, with the number and the name
    assert response.json()["record"]["fields"] == {"opNumber": "SDR_Y", "opName": "Пайка Y"}


def test_duplicate_code_is_refused_and_nothing_is_written(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")],
          [CUT_ROW, {"opNumber": "ROW_ONLY", "opName": "Строка без карточки"}])

    by_template = operation_library_api.post("/api/techcards/operations", json={"code": "CUT_WIRE", "name": "x"})
    other_case = operation_library_api.post("/api/techcards/operations", json={"code": "cut_wire", "name": "x"})
    by_row = operation_library_api.post("/api/techcards/operations", json={"code": "ROW_ONLY", "name": "x"})

    assert by_template.status_code == 422
    assert by_template.json()["detail"] == "Операция с номером «CUT_WIRE» уже есть — номер операции должен быть уникальным"
    assert other_case.status_code == 422 and "уже есть" in other_case.json()["detail"]
    # the template was written first in the transaction; the row check refused — the template rolled back with it
    assert by_row.status_code == 422
    assert by_row.json()["detail"] == "Операция с номером «ROW_ONLY» уже есть — номер операции должен быть уникальным"
    assert _codes(operation_library_api) == ["CUT_WIRE"]
    assert len(_op_rows(operation_library_api)) == 2


def test_bad_requests_get_russian_422(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")])
    post = lambda body: operation_library_api.post("/api/techcards/operations", json=body)  # noqa: E731

    cases = {
        "Укажите номер операции": {"code": " ", "name": "x"},
        "Укажите название операции": {"code": "NEW", "name": "  "},
        "Номер операции — до 64 знаков без пробелов и «|», например CUT_WIRE_auto": {"code": "NEW OP", "name": "x"},
        "Операции «NOPE» для копии нет среди карточек операций": {"code": "NEW", "name": "x", "copy_of": "NOPE"},
        "Номер «PRS_CAP» занят служебной карточкой — выберите другой": {"code": "PRS_CAP", "name": "Опрессовка крышки коакс"},
    }
    for detail, body in cases.items():
        response = post(body)
        assert (response.status_code, response.json()["detail"]) == (422, detail), body
    assert post({"code": "A|B", "name": "x"}).status_code == 422
    assert _codes(operation_library_api) == ["CUT_WIRE"] and _op_rows(operation_library_api) == []


def test_new_operation_needs_both_template_and_reference_edit(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")])
    for permissions in ({"erptctpl": "edit", "erpref": "view"}, {"erptctpl": "view", "erpref": "edit"}):
        app.dependency_overrides[authenticated_principal] = lambda permissions=permissions: Principal(
            actor=Actor(id=UUID("30000000-0000-0000-0000-000000000012"), name="Технолог", department="Производство"),
            email="tech@example.test", roles=frozenset({"engineer"}), permissions=permissions,
        )
        response = operation_library_api.post("/api/techcards/operations", json={"code": "NEW_OP", "name": "x"})
        assert response.status_code == 403, permissions
    assert _codes(operation_library_api) == ["CUT_WIRE"]


def test_erp_created_operation_survives_a_supplymap_library_reload(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")], [CUT_ROW])
    blank = operation_library_api.post("/api/techcards/operations", json={"code": "NEW_OP", "name": "Новая"}).json()

    reload = operation_library_api.put("/api/techcards/templates/supplymap-library", json={
        "sourceCommit": "c" * 40, "templates": [_template("strip", "STRIP")],
    })

    assert reload.status_code == 200, reload.text
    assert _codes(operation_library_api) == ["NEW_OP", "STRIP"]
    kept = operation_library_api.get(f"/api/techcards/templates/{blank['templates'][0]['id']}").json()
    assert kept["document"]["steps"][0]["text"] == BLANK_STEP


def test_copy_of_an_edited_record_survives_a_forced_reload(operation_library_api):
    """The copy carries the source's ERP edit as its own record edit, so a plain reload lists it like any edited
    record; a forced reload drops only SUPPLYMAP templates — the ERP-saved copy keeps its content."""
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")], [CUT_ROW])
    operation_library_api.put("/api/techcards/templates/cut-wire", json=EDIT)
    copy = operation_library_api.post("/api/techcards/operations", json={
        "code": "CUT_COPY", "name": "Копия", "copy_of": "CUT_WIRE",
    }).json()["templates"][0]
    library = {"sourceCommit": "d" * 40, "templates": [_template("cut-wire", "CUT_WIRE")]}

    refused = operation_library_api.put("/api/techcards/templates/supplymap-library", json=library)
    forced = operation_library_api.put("/api/techcards/templates/supplymap-library", json={**library, "force": True})

    assert refused.status_code == 409 and "CUT_COPY (" in refused.json()["detail"]
    assert forced.status_code == 200
    kept = operation_library_api.get(f"/api/techcards/templates/{copy['id']}").json()
    assert kept["document"]["steps"] == copy["document"]["steps"]
    assert kept["document"]["steps"][0]["text"] == "Новый шаг"


def test_issued_etk_keeps_its_document_when_the_card_template_is_edited(operation_library_api):
    _seed(operation_library_api, [_template("cut-wire", "CUT_WIRE")])
    card = operation_library_api.post("/api/techcards/operations", json={
        "code": "NEW_OP", "name": "Новая", "copy_of": "CUT_WIRE",
    }).json()["templates"][0]
    created = operation_library_api.post(
        "/api/techcards/projects", json={"project": "P-300", "item_index": "КБ1", "product_name": "Жгут"},
    ).json()
    built = operation_library_api.post(f"/api/techcards/projects/{created['id']}/build", json={
        "version": created["version"],
        "config": {"projectNo": "P-300", "assemblyIndex": "КБ1", "assemblyName": "Жгут", "partQty": 10, "ops": [{
            "type": "heatTut", "templateId": card["id"], "opNum": "NEW_OP",
            "tPrep": "60", "tOp": "15", "tMachine": "10", "tutCount": 1,
        }]},
    })
    assert built.status_code == 200, built.text
    issued = operation_library_api.post(f"/api/techcards/projects/{created['id']}/issue",
                                        json={"version": built.json()["version"]})
    assert issued.status_code == 200, issued.text
    assert issued.json()["document"]["operations"][0]["card"]["steps"][0]["text"] == "Выполнить операцию"

    edited = operation_library_api.put(f"/api/techcards/templates/{card['id']}", json=EDIT)
    after = operation_library_api.get(f"/api/techcards/projects/{created['id']}").json()

    assert edited.status_code == 200 and edited.json()["document"]["steps"][0]["text"] == "Новый шаг"
    assert after["document"] == issued.json()["document"]
    assert (after["version"], after["revision"]) == (issued.json()["version"], issued.json()["revision"])
