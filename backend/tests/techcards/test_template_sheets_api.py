"""Шаблоны-листы БД.ТК: каталог, черновик, правка, сохранение, отмена, удаление
(SUPPLYMAP ErpTechmap.gs portalTcTplList/Draft/Save/DropDraft/Delete, TcTemplates.gs)."""

from uuid import UUID

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.modules.techcards.template_sheets import captured_block, make_template_id, slugify

from .library import load_library
from .test_operation_card_api import _template, operation_library_api  # noqa: F401


def _import(client, *templates):
    load_library(app.state.techcards_uow_factory, templates)


def _grid(code: str, step: str = "Выполнить операцию") -> list[list[str]]:
    rows = _template("x", code)["rows"]
    rows[10] = ["Операция", "1", step, "", "", "", "КД"]
    return rows


def test_slug_and_id_follow_supplymap():
    assert slugify("CUT_WIRE_auto | Резка (A)") == "cut-wire-auto-резка-a"
    assert make_template_id("CUT | Резка", ["cut-резка", "cut-резка-2"]) == "cut-резка-3"
    assert make_template_id("!!!", []) == "template"
    assert captured_block([["a", ""], ["", ""], []]) == (("a",),)


def test_catalog_lists_templates_and_open_drafts(operation_library_api):
    _import(operation_library_api, _template("cut-wire", "CUT_WIRE"))
    catalog = operation_library_api.get("/api/techcards/template-sheets").json()

    assert catalog["list"] == [{
        "id": "cut-wire", "title": "CUT_WIRE | Операция", "category": "Сборка", "description": "",
        "size": "12×7", "updatedAt": "2026-09-23T12:00:00", "savedBy": "",
    }]
    assert catalog["drafts"] == []


def test_new_draft_edit_and_save_creates_a_template_the_library_reads(operation_library_api):
    _import(operation_library_api, _template("cut-wire", "CUT_WIRE"))
    opened = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"title": "PRS_NEW | Опрессовка"})
    assert opened.status_code == 201, opened.text
    draft = opened.json()
    assert draft["draftSheet"] == "Черновик — PRS_NEW | Опрессовка"
    assert draft["existingTemplateId"] == "" and draft["rows"] == []

    again = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"title": "PRS_NEW | Опрессовка"})
    assert again.status_code == 409
    assert "уже открыт" in again.json()["detail"]

    empty = operation_library_api.post(f"/api/techcards/template-sheets/drafts/{draft['id']}/save", json={"title": "PRS_NEW | Опрессовка"})
    assert empty.status_code == 422
    assert "Черновик пуст" in empty.json()["detail"]

    grid = _grid("PRS_NEW") + [["", "", ""]]
    edited = operation_library_api.put(f"/api/techcards/template-sheets/drafts/{draft['id']}", json={"rows": grid})
    assert edited.status_code == 200
    assert edited.json()["rows"][10][2] == "Выполнить операцию"

    saved = operation_library_api.post(f"/api/techcards/template-sheets/drafts/{draft['id']}/save", json={
        "title": "PRS_NEW | Опрессовка", "category": "Опрессовка", "description": "Новый",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"action": "created", "id": "prs-new-опрессовка", "title": "PRS_NEW | Опрессовка", "sizeLabel": "11 x 7"}

    catalog = operation_library_api.get("/api/techcards/template-sheets").json()
    assert catalog["drafts"] == []
    created = next(item for item in catalog["list"] if item["id"] == "prs-new-опрессовка")
    assert created["description"] == "Новый" and created["savedBy"] == "Администратор"
    detail = operation_library_api.get("/api/techcards/templates/prs-new-опрессовка").json()
    assert detail["code"] == "PRS_NEW" and detail["category"] == "Опрессовка"
    assert detail["document"]["steps"][0]["text"] == "Выполнить операцию"


def test_edit_through_draft_updates_the_same_template(operation_library_api):
    _import(operation_library_api, _template("cut-wire", "CUT_WIRE"))
    first = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"template_id": "cut-wire"}).json()
    # Повторное «Редактировать» — свежий черновик вместо прежнего.
    draft = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"template_id": "cut-wire"}).json()
    assert draft["id"] != first["id"]
    assert draft["existingTemplateId"] == "cut-wire" and draft["category"] == "Сборка"
    assert draft["rows"][10][2] == "Выполнить операцию"
    assert operation_library_api.get(f"/api/techcards/template-sheets/drafts/{first['id']}").status_code == 404

    operation_library_api.put(f"/api/techcards/template-sheets/drafts/{draft['id']}", json={"rows": _grid("CUT_WIRE", "Отрезать провод")})
    saved = operation_library_api.post(f"/api/techcards/template-sheets/drafts/{draft['id']}/save", json={
        "title": "CUT_WIRE | Операция", "category": "Резка", "description": "",
    }).json()

    assert saved["action"] == "updated" and saved["id"] == "cut-wire"
    detail = operation_library_api.get("/api/techcards/templates/cut-wire").json()
    assert detail["document"]["steps"][0]["text"] == "Отрезать провод"
    assert detail["category"] == "Резка"


def test_save_rejects_a_block_without_steps_and_keeps_the_draft(operation_library_api):
    draft = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"title": "X | Пусто"}).json()
    operation_library_api.put(f"/api/techcards/template-sheets/drafts/{draft['id']}", json={"rows": [["X | Пусто"], ["текст"]]})
    response = operation_library_api.post(f"/api/techcards/template-sheets/drafts/{draft['id']}/save", json={"title": "X | Пусто"})

    assert response.status_code == 422
    assert "нет шагов" in response.json()["detail"]
    assert len(operation_library_api.get("/api/techcards/template-sheets").json()["drafts"]) == 1
    no_title = operation_library_api.post(f"/api/techcards/template-sheets/drafts/{draft['id']}/save", json={"title": " "})
    assert no_title.json()["detail"] == "Укажите название шаблона."


def test_drop_draft_and_delete_template(operation_library_api):
    _import(operation_library_api, _template("cut-wire", "CUT_WIRE"), _template("strip", "STRIP_WIRE"))
    draft = operation_library_api.post("/api/techcards/template-sheets/drafts", json={"title": ""}).json()
    assert draft["title"] == "Новый шаблон 23.09 12:00"

    assert operation_library_api.delete(f"/api/techcards/template-sheets/drafts/{draft['id']}").json() == {"dropped": True}
    assert operation_library_api.delete(f"/api/techcards/template-sheets/drafts/{draft['id']}").json() == {"dropped": False}

    deleted = operation_library_api.delete("/api/techcards/template-sheets/strip")
    assert deleted.json() == {"deleted": True, "id": "strip", "title": "STRIP_WIRE | Операция"}
    assert operation_library_api.get("/api/techcards/templates/strip").status_code == 404
    assert operation_library_api.delete("/api/techcards/template-sheets/strip").status_code == 404


def test_template_sheet_writes_need_templates_edit(operation_library_api):
    _import(operation_library_api, _template("cut-wire", "CUT_WIRE"))
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("30000000-0000-0000-0000-000000000002"), name="Читатель", department="ERP"),
        email="viewer@example.test",
        roles=frozenset({"engineer"}),
        permissions={"techcards": "view", "erptctpl": "view"},
    )

    assert operation_library_api.get("/api/techcards/template-sheets").status_code == 200
    assert operation_library_api.post("/api/techcards/template-sheets/drafts", json={"title": "A | B"}).status_code == 403
    assert operation_library_api.delete("/api/techcards/template-sheets/cut-wire").status_code == 403
