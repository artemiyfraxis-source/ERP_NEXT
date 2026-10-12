from dataclasses import replace
import base64
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards import kitsets
from app.modules.warehouse.domain import StockLot
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets


STATE = (
    "production_uow_factory", "techcards_uow_factory", "fulfillment_uow_factory",
    "shipping_uow_factory", "uow_factory", "procurement_uow_factory",
)


def _principal(permissions):
    return lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions=permissions,
    )


@pytest.fixture()
def kitset_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 29, 10, 0)
    app.dependency_overrides[moscow_today] = lambda: datetime(2026, 9, 29).date()
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="Корпус Molex 43025-0400",
        item_type="Комплектующие", purpose="Склад", unit="шт", quantity=Decimal("15"),
        storage_location="1F4", source_receiving_ids=("receiving-one",), article="43025-0400",
    )
    with app.state.fulfillment_uow_factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    app.state.kitset_sheets_reader = FakeSheets()
    yield TestClient(app), lot
    del app.state.kitset_sheets_reader
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    app.dependency_overrides.pop(moscow_today, None)
    for name in STATE:
        delattr(app.state, name)
    engine.dispose()


CALC = "https://docs.google.com/spreadsheets/d/1AbC-calc_id/edit#gid=0"


@pytest.fixture()
def scrap_allowance_on(monkeypatch):
    """Tests written with the «% брака» allowance on (switched off for now — «выдаём вровень», owner 10.10.2026)."""
    monkeypatch.setattr(kitsets, "SCRAP_APPLIED", True)


def test_the_kit_goes_out_exactly_to_the_need_without_the_scrap_allowance(kitset_api):
    """«Выдаём вровень» (owner 10.10.2026): «% брака» 5 of the wire stays on the card, «На партию» and the issued need
    are расход × тираж; a shortage goes out later as «Довыдача»."""
    client, _lot = kitset_api
    _load(client)
    wire = client.get("/api/techcards/kitsets/708К.4").json()["assemblies"][0]["components"][0]
    assert (wire["unit"], wire["scrap"], wire["batch"]) == ("м", "5", "15")

    issued = client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"})
    assert issued.status_code == 200, issued.text
    with app.state.fulfillment_uow_factory() as uow:
        kb1 = next(request for request in uow.fulfillment.list(limit=10) if "КБ1" in request.purpose)
    assert sum(line.requirement for line in kb1.lines if line.name == "UL1007 22AWG") == Decimal("15")


def test_a_second_send_creates_nothing_and_the_state_lists_the_sent_kb(kitset_api):
    client, _lot = kitset_api
    _load(client)
    assert client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"}).status_code == 200

    again = client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"})

    assert again.status_code == 409
    assert again.json()["detail"] == (
        "Все КБ проекта 708К.4 уже отправлены: КБ1 (REQ.26.0000001), КБ2 (REQ.26.0000002)."
    )
    state = client.get("/api/techcards/kitsets/708К.4/issue").json()
    assert [(row["index"], row["request"]["no"], row["request"]["status"]) for row in state["assemblies"]] == [
        ("КБ1", "REQ.26.0000001", "Новая"), ("КБ2", "REQ.26.0000002", "Новая"),
    ]
    with app.state.fulfillment_uow_factory() as uow:
        assert len(uow.fulfillment.list(limit=10)) == 2


def _load(client, **extra):
    return client.post("/api/techcards/kitsets/load", json={"url": CALC, **extra})


def test_project_from_calculation_registers_plan_queue_and_addressee(kitset_api):
    client, _lot = kitset_api
    created = _load(client, deadline="2026-10-20", ship={"shipping_point": "Москва, склад 3", "contact": "Иван"})
    assert created.status_code == 200, created.text
    res = created.json()["res"]
    assert (res["project"], res["assemblies"], res["lines"]) == ("708К.4", 2, 3)
    assert res["erp"] == {"project": True, "positions": 2}
    assert res["ship"]["added"] == 2
    assert not (res["erp_error"] or res["ship_error"] or res["ship_to_error"] or res["deadline_error"])

    listing = client.get("/api/techcards/kitsets").json()
    assert listing["statuses"][:2] == ["Новый", "ТК готова"]
    tile = listing["items"][0]
    assert (tile["project"], tile["status"], tile["kbs"]) == ("708К.4", "Новый", ["КБ1", "КБ2"])

    with app.state.shipping_uow_factory() as uow:
        queued = list(uow.goods.by_project("708К.4"))
        addressee = uow.addressees.get("708К.4")
    assert sorted(good.article for good in queued) == ["708К.4КБ1", "708К.4КБ2"]
    assert addressee.shipping_point == "Москва, склад 3"
    with app.state.production_uow_factory() as uow:
        assert [item.project for item in uow.plan_projects.list()] == ["708К.4"]
        assert [event.kind for event in uow.plan_events.list()] == ["Дедлайн"]


def test_spec_edits_survive_refresh_and_issue_reserves_picked_lot(kitset_api):
    client, lot = kitset_api
    _load(client)
    lots = client.get("/api/techcards/kitsets/lots", params={"q": "43025"}).json()["lots"]
    assert [item["grn"] for item in lots] == ["FRX.26.0000001"]
    saved = client.put("/api/techcards/kitsets/708К.4/lines", json={"edits": [{
        "index": "КБ1", "art": "43025-0400", "per": "2", "scrap": "0", "name": "Корпус Molex",
        "lot": {"lot_id": lots[0]["lot_id"], "warehouse": "ЦАР", "grn": "FRX.26.0000001", "package": ""},
    }]})
    assert saved.json()["res"]["updated"] == 1
    refreshed = _load(client, number="708К.4")
    assert refreshed.status_code == 200
    card = client.get("/api/techcards/kitsets/708К.4").json()
    housing = card["assemblies"][0]["components"][1]
    assert (housing["per"], housing["scrap"], housing["batch"], housing["lot"]["grn"]) == ("2", "0", "20", "FRX.26.0000001")

    missing = client.post("/api/techcards/kitsets/708К.4/issue", json={})
    assert missing.status_code == 422
    issued = client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"})
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["message"].startswith("Заявок создано: 2, из них ждут поставки: 2")
    with app.state.fulfillment_uow_factory() as uow:
        requests = {request.purpose: request for request in uow.fulfillment.list(limit=10)}
        stock = uow.warehouse.get(lot.id)
    kb1 = requests["708К.4 · КБ1 · Жгут питания"]
    bound = [line for line in kb1.lines if line.lot is not None]
    assert [(line.name, line.reserved) for line in bound] == [("Корпус Molex", Decimal("15"))]
    assert stock.available == 0
    # The lot held 15 of 20: the rest waits as its own line, as SUPPLYMAP splits the need.
    assert sum(line.requirement for line in kb1.lines if line.name == "Корпус Molex") == Decimal("20")


def test_piece_lines_default_to_no_scrap_and_batch_in_whole_pieces_up_to_the_issue(kitset_api, scrap_allowance_on):
    """Василий, 04.10: «округлять шт, половинки быть не может. Пока даём без брака»."""
    client, _lot = kitset_api
    _load(client)
    card = client.get("/api/techcards/kitsets/708К.4").json()
    wire, housing = card["assemblies"][0]["components"]
    assert (wire["unit"], wire["scrap"], wire["batch"]) == ("м", "5", "15.75")
    assert (housing["unit"], housing["scrap"], housing["batch"]) == ("шт", "0", "20")

    client.put("/api/techcards/kitsets/708К.4/lines", json={"edits": [
        {"index": "КБ1", "art": "43025-0400", "per": "1.5", "scrap": "5"},
    ]})
    housing = client.get("/api/techcards/kitsets/708К.4").json()["assemblies"][0]["components"][1]
    assert housing["batch"] == "16"  # 1,5 × 10 × 1,05 = 15,75 → 16 шт

    issued = client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"})
    assert issued.status_code == 200, issued.text
    with app.state.fulfillment_uow_factory() as uow:
        kb1 = next(request for request in uow.fulfillment.list(limit=10) if "КБ1" in request.purpose)
    need = {}
    for line in kb1.lines:
        need[line.name] = need.get(line.name, Decimal("0")) + line.requirement
    assert need == {"UL1007 22AWG": Decimal("15.75"), "Корпус Molex": Decimal("16")}


def test_supply_marks_documents_bom_techcard_and_cascade_delete(kitset_api):
    client, _lot = kitset_api
    _load(client)
    supply = client.get("/api/techcards/kitsets/708К.4/supply").json()
    assert {line["art"]: line["stage"] for line in supply["lines"]} == {
        "UL1007 22AWG": "не заведено", "43025-0400": "не заведено", "43030-0007": "не заведено",
    }
    # Unit by the spec line's type, as the calc import (unitForType_): wire in metres, the rest in pieces.
    assert {line["art"]: line["unit"] for line in supply["lines"]} == {
        "UL1007 22AWG": "м", "43025-0400": "шт", "43030-0007": "шт",
    }
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert card["assemblies"][0]["components"][0]["unit"] == "м"

    marked = client.post("/api/techcards/kitsets/708К.4/marks", json={"kb": "КБ1", "kind": "stage", "value": "В работе"})
    assert marked.status_code == 200, marked.text
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert card["assemblies"][0]["stage"]["status"] == "В работе"
    assert card["assemblies"][0]["stage"]["manual"] is True

    attached = client.post("/api/techcards/kitsets/708К.4/documents", json={
        "index": "кб1", "file_name": "Чертёж.pdf", "content_type": "application/pdf",
        "content_b64": base64.b64encode(b"%PDF-1.4 test").decode(),
    })
    assert attached.status_code == 201, attached.text
    document = attached.json()["res"]
    assert document["name"] == "708К.4КБ1 — Чертёж.pdf"
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert card["assemblies"][0]["kd"] == [{
        "id": document["id"], "index": "КБ1", "name": "708К.4КБ1 — Чертёж.pdf", "size": 13, "stored": True,
        "url": None, "uploaded_at": "2026-09-29T10:00:00", "who": "Технолог", "kind": "file",
    }]
    # Файл — в базе ERP: карточка ведёт на выдачу байтов, открывается во вкладке под своим именем.
    downloaded = client.get(f"/api/techcards/kitsets/708К.4/documents/{document['id']}")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == b"%PDF-1.4 test"
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["content-disposition"] == (
        "inline; filename*=UTF-8''708%D0%9A.4%D0%9A%D0%911%20%E2%80%94%20%D0%A7%D0%B5%D1%80%D1%82%D1%91%D0%B6.pdf"
    )

    bom = client.get("/api/techcards/kitsets/708К.4/bom/КБ1").json()
    assert [(c["art"], c["qty"], c["scrap"]) for c in bom["components"]] == [
        ("UL1007 22AWG", "1.5", "0.05"), ("43025-0400", "2", "0"),
    ]
    opened = client.post("/api/techcards/kitsets/708К.4/techcards/КБ1", json={})
    again = client.post("/api/techcards/kitsets/708К.4/techcards/КБ1", json={})
    assert opened.json()["card_id"] == again.json()["card_id"]
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert card["assemblies"][0]["techcard"]["status"] == "Черновик"

    deleted = client.delete("/api/techcards/kitsets/708К.4")
    assert deleted.status_code == 200, deleted.text
    res = deleted.json()["res"]
    assert (res["deleted"], res["cards"]["cards"], res["gp"]["deleted"], res["erp"]) == (3, 1, 2, {"Проекты": 1})
    assert res["journal"] == {"deleted": 1}  # the manual stage mark «В работе» goes with the project
    gone = client.get("/api/techcards/kitsets/708К.4")
    assert gone.status_code == 404
    assert gone.json()["detail"]["gone"] is True
    # tcKitsetDelete не трогает лист «КД»: вложение возвращается с проектом.
    _load(client)
    back = client.get("/api/techcards/kitsets/708К.4").json()
    assert [item["id"] for item in back["assemblies"][0]["kd"]] == [document["id"]]
    # …а журнал начинается с чистого листа: старая отметка этапа не возвращается.
    assert back["journal"] == [] and back["assemblies"][0]["stage"]["manual"] is False
    assert client.get(f"/api/techcards/kitsets/708К.4/documents/{document['id']}").content == b"%PDF-1.4 test"


def _attach(client, name, content=b"x", index="КБ2"):
    return client.post("/api/techcards/kitsets/708К.4/documents", json={
        "index": index, "file_name": name, "content_b64": base64.b64encode(content).decode(),
    })


def test_kd_detach_removes_row_and_bytes(kitset_api):
    """`tcKdDelete`: строка реестра и файл уходят из ERP; повтор — ничего не удаляет."""
    client, _lot = kitset_api
    _load(client)
    first, second = _attach(client, "Схема.png").json()["res"], _attach(client, "ТЗ.docx").json()["res"]
    downloaded = client.get(f"/api/techcards/kitsets/708К.4/documents/{first['id']}")
    assert downloaded.headers["content-type"] == "application/octet-stream"
    removed = client.delete(f"/api/techcards/kitsets/708К.4/documents/{first['id']}")
    assert removed.json()["res"] == {"deleted": 1}
    assert client.get(f"/api/techcards/kitsets/708К.4/documents/{first['id']}").status_code == 404
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert [item["id"] for item in card["assemblies"][1]["kd"]] == [second["id"]]
    again = client.delete(f"/api/techcards/kitsets/708К.4/documents/{first['id']}")
    assert again.json()["res"] == {"deleted": 0}
    # Чужой проект не отдаёт и не удаляет файл по одному id.
    assert client.get(f"/api/techcards/kitsets/708К.5/documents/{second['id']}").status_code == 404


def test_kd_limits_and_checks_come_before_storing(kitset_api):
    """20 МБ на файл, известная сборка, непустой файл — без Google, без 503."""
    client, _lot = kitset_api
    _load(client)
    too_big = _attach(client, "big.pdf", b"x" * (20 * 1024 * 1024 + 1))
    assert too_big.status_code == 422
    assert "20 МБ" in too_big.json()["detail"]
    assert _attach(client, "x.pdf", index="КБ9").status_code == 422
    assert _attach(client, "max.pdf", b"x" * (20 * 1024 * 1024)).status_code == 201
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert [item["name"] for item in card["assemblies"][1]["kd"]] == ["708К.4КБ2 — max.pdf"]


def _drive_only_row(project="708К.4", index="КБ1"):
    """A register row attached while КД lived on Drive (migration 0068): only a file id and a link, no bytes."""
    from app.modules.techcards.kitset_infrastructure import KitDocumentRow

    row_id = UUID("70000000-0000-0000-0000-000000000001")
    with app.state.techcards_uow_factory() as uow:
        uow.session.add(KitDocumentRow(
            id=row_id, project=project, item_index=index, file_name=f"{project}{index} — Старый.pdf",
            file_id="drive-old", url="https://drive.google.com/file/d/drive-old/view?usp=drivesdk",
            uploaded_at=datetime(2026, 9, 29, 9, 0), uploaded_by="Технолог",
        ))
        uow.commit()
    return row_id


def test_kd_historical_drive_row_is_a_read_only_link(kitset_api):
    """Строка из времён Диска: байтов в ERP нет — выдача 404, карточка показывает архивную ссылку; отвязать можно."""
    client, _lot = kitset_api
    _load(client)
    row_id = _drive_only_row()
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert card["assemblies"][0]["kd"] == [{
        "id": str(row_id), "index": "КБ1", "name": "708К.4КБ1 — Старый.pdf", "size": None, "stored": False,
        "url": "https://drive.google.com/file/d/drive-old/view?usp=drivesdk",
        "uploaded_at": "2026-09-29T09:00:00", "who": "Технолог", "kind": "file",
    }]
    assert client.get(f"/api/techcards/kitsets/708К.4/documents/{row_id}").status_code == 404
    removed = client.delete(f"/api/techcards/kitsets/708К.4/documents/{row_id}")
    assert removed.json()["res"] == {"deleted": 1}
    assert client.get("/api/techcards/kitsets/708К.4").json()["assemblies"][0]["kd"] == []


def test_view_only_reads_but_cannot_write(kitset_api):
    client, _lot = kitset_api
    _load(client)
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.get("/api/techcards/kitsets").status_code == 200
    assert client.get("/api/techcards/kitsets/708К.4").status_code == 200
    assert _load(client).status_code == 403
    assert client.delete("/api/techcards/kitsets/708К.4").status_code == 403
    app.dependency_overrides[authenticated_principal] = _principal({"techcards": "edit"})
    assert client.get("/api/techcards/kitsets").status_code == 403
    # Экран мастера показывает плитки проектов: «Ресурсное планирование» читает список, но не карточку.
    app.dependency_overrides[authenticated_principal] = _principal({"production": "edit"})
    assert client.get("/api/techcards/kitsets").status_code == 200
    assert client.get("/api/techcards/kitsets/708К.4").status_code == 403


LINK = "https://docs.google.com/spreadsheets/d/1AbC-calc_id/edit#gid=0"
SHEETS = {
    "ЗКЗ": [
        ["Название проекта", ""], ["Номер", "708К.4"], ["Заказчик", "ООО Заказчик"], [],
        ["№", "Индекс сборки", "Наименование сборки по чертежу", "Кол-во 1", "Вкл. в расчет?"],
        ["1", "кб1", "Жгут питания", "10", "1"],
    ],
    "СПЯ": [
        ["Сборка", "Артикул КД", "Тип", "Наименование", "Кол-во на комплект", "Ссылка"],
        ["КБ1", "UL1007 22AWG", "Провод", "", "1,5", ""],
    ],
    "КБ1": [[], [], [], [], ["", "1", "UL1007", "2", "0,15", "", "", "лудить"]],
}


def test_project_from_calculation_link_reads_google_sheet(kitset_api):
    """`tcReadCalcKitset_(calcUrl)`: ЗКЗ + СПЯ by link; the file title refines the number; link kept as РАСЧЁТ."""
    client, _lot = kitset_api
    reader = FakeSheets(SHEETS, title="708К.4.1 Расчет")
    app.state.kitset_sheets_reader = reader
    response = client.post("/api/techcards/kitsets/load", json={"url": LINK})
    assert response.status_code == 200, response.text
    assert response.json()["res"]["project"] == "708К.4.1"
    # Листы КБn (длины резки для генератора) читаются тем же заведением и хранятся в карточке.
    assert reader.calls == [("1AbC-calc_id", ("ЗКЗ", "СПЯ")), ("1AbC-calc_id", ("КБ1",))]
    card = client.get("/api/techcards/kitsets/708К.4.1").json()
    assert card["calc_source"] == LINK
    assert [item["art"] for item in card["assemblies"][0]["components"]] == ["UL1007 22AWG"]
    bom = client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()
    assert bom["calc_ops"] == [{"code": "1", "comp": "UL1007", "n": "2", "l": "0,15", "req": "лудить"}]
    assert len(reader.calls) == 2  # генератор открывается из базы, Google не ждёт


def test_old_card_reads_the_kb_sheet_once_and_keeps_it(kitset_api):
    client, _lot = kitset_api
    reader = FakeSheets({name: rows for name, rows in SHEETS.items() if name != "КБ1"}, title="708К.4.1 Расчет")
    app.state.kitset_sheets_reader = reader
    assert client.post("/api/techcards/kitsets/load", json={"url": LINK}).status_code == 200
    with app.state.techcards_uow_factory() as uow:  # карточка из времени, когда листы КБn не хранились
        kitset = uow.kitsets.get("708К.4.1")
        uow.kitsets.save(replace(kitset, version=kitset.version + 1, assemblies=tuple(
            replace(item, calc_ops=None) for item in kitset.assemblies)))
        uow.commit()
    reader.sheets = SHEETS
    first = client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()
    assert first["calc_ops"][0]["l"] == "0,15"
    calls = len(reader.calls)
    assert client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()["calc_ops"] == first["calc_ops"]
    assert len(reader.calls) == calls


def test_calculation_link_refusals(kitset_api):
    from app.modules.requests.application import CalcSheetError

    client, _lot = kitset_api
    assert client.post("/api/techcards/kitsets/load", json={"url": "https://example.test/x"}).status_code == 422
    assert client.post("/api/techcards/kitsets/load", json={}).status_code == 422
    app.state.kitset_sheets_reader = FakeSheets({}, error=CalcSheetError("Нет доступа к таблице расчёта", "access"))
    refused = client.post("/api/techcards/kitsets/load", json={"url": LINK})
    assert refused.status_code == 422
    assert "Нет доступа" in refused.json()["detail"]


def test_generator_bom_reads_kb_sheet_of_the_linked_calculation(kitset_api):
    """`tcLoadBomFromKit.calcOps` = `tcCalcOps_(card.calcUrl, index)`: the КБn sheet from row 5, B/C/D/E/H;
    a read failure gives an empty list, never an error."""
    from app.modules.requests.application import CalcSheetError

    client, _lot = kitset_api
    kb = [
        ["Операции КБ1"], [], [], ["№", "Код", "Комплектующее", "n", "L", "", "", "Требования к задаче"],
        ["1", "CUT_WIRE", "UL1007  22AWG", "4", "0,16", "", "", "L=150мм +\nSHF-001T"],
        ["2", "", "итого", "", "", "", "", ""],
        ["3", "SDR_tin"],
    ]
    reader = FakeSheets({**SHEETS, "КБ1": kb}, title="708К.4.1 Расчет")
    app.state.kitset_sheets_reader = reader
    assert client.post("/api/techcards/kitsets/load", json={"url": LINK}).status_code == 200
    bom = client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()
    assert reader.calls[-1] == ("1AbC-calc_id", ("КБ1",))
    with app.state.techcards_uow_factory() as uow:  # листы КБn ещё не читались — генератор прочитает сам
        kitset = uow.kitsets.get("708К.4.1")
        uow.kitsets.save(replace(kitset, version=kitset.version + 1, assemblies=tuple(
            replace(item, calc_ops=None) for item in kitset.assemblies)))
        uow.commit()
    app.state.kitset_sheets_reader = FakeSheets({}, error=CalcSheetError("Нет доступа к таблице расчёта", "access"))
    failed = client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()
    assert bom["calc_ops"] == [
        {"code": "CUT_WIRE", "comp": "UL1007 22AWG", "n": "4", "l": "0,16", "req": "L=150мм + SHF-001T"},
        {"code": "SDR_tin", "comp": "", "n": "", "l": "", "req": ""},
    ]
    assert failed["calc_ops"] == []
    assert bom["run"] == "10"


def test_project_status_in_one_click(kitset_api):
    client, _lot = kitset_api
    _load(client)
    kbs = [item["index"] for item in client.get("/api/techcards/kitsets/708К.4").json()["assemblies"]]
    assert len(kbs) >= 2

    staged = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "status", "value": "В работе"})
    assert staged.status_code == 200, staged.text
    card = staged.json()["card"]
    assert card["status"] == "В работе"
    assert {item["stage"]["status"] for item in card["assemblies"]} == {"В работе"}
    assert card["terminals"] == ["Отказ", "Слился", "Отменён"] and "ХОЛД" in card["blockers"]

    no_reason = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "block", "value": "ХОЛД"})
    assert no_reason.status_code == 422
    held = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "block", "value": "ХОЛД", "text": "ждём оплату"})
    card = held.json()["card"]
    assert card["blocker"] == "ХОЛД" and card["blocker_info"]["text"] == "ждём оплату"
    tile = next(item for item in client.get("/api/techcards/kitsets").json()["items"] if item["project"] == "708К.4")
    assert (tile["status"], tile["blocker"]) == ("В работе", "ХОЛД")
    card = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "unblock"}).json()["card"]
    assert card["blocker"] == "" and card["blocker_info"] is None

    closed = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "status", "value": "Отказ"}).json()["card"]
    assert closed["status"] == "Отказ"
    # Этап у закрытого проекта сначала возвращает его в работу.
    reopened = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "status", "value": "авто"}).json()["card"]
    assert reopened["status"] == "Новый"
    assert all(item["stage"]["manual"] is False for item in reopened["assemblies"])

    bad = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "status", "value": "Готово"})
    assert bad.status_code == 422

    noted = client.post("/api/techcards/kitsets/708К.4/journal", json={"kind": "deadline", "deadline": "2026-10-20"})
    assert noted.status_code == 200, noted.text
    card = noted.json()["card"]
    assert card["deadline"] == "2026-10-20"
    assert [event["kind"] for event in card["journal"][:2]] == ["Дедлайн", "Этап КБ"]


def _side_effects(lot):
    """What a stage change must never touch: kit requests, the lot's stock and reserve, shop tasks, goods."""
    with app.state.fulfillment_uow_factory() as uow:
        requests = sorted((str(item.id), item.status.value, item.version) for item in uow.fulfillment.list(limit=100))
        stock = uow.warehouse.get(lot.id)
    with app.state.production_uow_factory() as uow:
        tasks = len(uow.production.list(limit=100))
    with app.state.shipping_uow_factory() as uow:
        goods = len(uow.goods.list(limit=100))
    return requests, (stock.balance, stock.reserved, stock.version), tasks, goods


def test_manual_stage_change_previews_skips_needs_a_reason_back_and_closes_the_skipped_kits(kitset_api):
    client, lot = kitset_api
    _load(client)
    assert client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"}).status_code == 200
    before = _side_effects(lot)

    preview = client.get("/api/techcards/kitsets/708К.4/status/preview", params={"value": "Отгружено"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["direction"], body["reason_required"], body["error"]) == ("forward", False, "")
    assert [(kb["kb"], kb["from"], kb["to"], kb["skipped"]) for kb in body["kbs"]] == [
        ("КБ1", "Новый", "Отгружено", ["ТК готова", "КБ набрана", "В работе", "КБ на складе"]),
        ("КБ2", "Новый", "Отгружено", ["ТК готова", "КБ набрана", "В работе", "КБ на складе"]),
    ]
    assert body["kbs"][0]["warnings"] == [
        "Закроется (пропущено вручную: Новый → Отгружено): заявок на комплектацию — 1.",
    ]
    assert [item["kind"] for item in body["kbs"][0]["close"]] == ["kit_request"]
    assert body["totals"]["kit_requests"] == 2 and body["preview_token"]
    # Предпросмотр ничего не пишет.
    assert client.get("/api/techcards/kitsets/708К.4").json()["status"] == "Новый"

    # КБ2 руками вперёд, затем проект на «КБ на складе»: КБ1 поднимается, КБ2 (уже выше) не понижается.
    kb2 = client.post("/api/techcards/kitsets/708К.4/marks", json={"kb": "КБ2", "kind": "stage", "value": "Отгружено"})
    assert kb2.status_code == 200, kb2.text
    staged = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "status", "value": "КБ на складе"})
    assert staged.status_code == 200, staged.text
    assert staged.json()["change"]["kbs"][1]["note"] == "Выше выбранного этапа — не понижаем."
    card = staged.json()["card"]
    stages = {item["index"]: item["stage"] for item in card["assemblies"]}
    assert (stages["КБ1"]["status"], stages["КБ2"]["status"]) == ("КБ на складе", "Отгружено")
    assert stages["КБ1"]["skipped"] == ["ТК готова", "КБ набрана", "В работе"]
    assert card["journal"][0]["from_value"] == "Новый" and card["journal"][0]["value"] == "КБ на складе"

    # Назад — только с причиной (и для проекта, и для КБ).
    refused = client.post("/api/techcards/kitsets/708К.4/status", json={"action": "status", "value": "В работе"})
    assert refused.status_code == 422 and refused.json()["detail"] == "Этап назад — напишите причину."
    refused = client.post("/api/techcards/kitsets/708К.4/marks", json={"kb": "КБ2", "kind": "stage", "value": "Новый"})
    assert refused.status_code == 422 and refused.json()["detail"] == "Этап назад — напишите причину."
    back = client.post("/api/techcards/kitsets/708К.4/status", json={
        "action": "status", "value": "В работе", "reason": "вернули на доработку",
    })
    assert back.status_code == 200, back.text
    assert back.json()["change"]["direction"] == "back" and back.json()["events"] == 2
    assert back.json()["card"]["journal"][0]["reason"] == "вернули на доработку"

    # Вперёд пропущенное закрыто (владелец 12.10): заявки «Новая» отменены, РЕЗЕРВ партии вернулся, остаток тот же;
    # назад ничего не открылось.
    requests, (balance, reserved, _version), tasks, goods = _side_effects(lot)
    assert {status for _id, status, _v in requests} == {"Отменена"}
    assert (balance, reserved, tasks, goods) == (before[1][0], 0, before[2], before[3])


def test_stage_apply_with_a_stale_preview_token_is_409_and_writes_nothing(kitset_api):
    client, lot = kitset_api
    _load(client)
    assert client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"}).status_code == 200
    preview = client.get("/api/techcards/kitsets/708К.4/status/preview", params={"value": "В работе"}).json()
    before = _side_effects(lot)

    stale = client.post("/api/techcards/kitsets/708К.4/status", json={
        "action": "status", "value": "В работе", "preview_token": "0" * 32,
    })
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Этапы проекта или связанные задания изменились — обновите и повторите"
    assert _side_effects(lot) == before

    applied = client.post("/api/techcards/kitsets/708К.4/marks", json={
        "kb": "", "kind": "stage", "value": "В работе", "preview_token": preview["preview_token"],
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["change"]["totals"]["kit_requests"] == 2
    # «Производственный план» — тот же ответ.
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "production": "edit"})
    plan_preview = client.get("/api/production/plan/stage-preview", params={"project": "708К.4", "value": "КБ на складе"})
    assert plan_preview.status_code == 200, plan_preview.text
    assert plan_preview.json()["preview_token"] and plan_preview.json()["kbs"][0]["close"] == []


def test_plan_page_stage_goes_through_the_same_service(kitset_api):
    client, _lot = kitset_api
    _load(client)
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "production": "edit"})

    preview = client.get("/api/production/plan/stage-preview", params={"project": "708К.4", "value": "В работе", "kb": "КБ1"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["kbs"][0]["skipped"] == ["ТК готова", "КБ набрана"]

    saved = client.post("/api/production/plan/events", json={
        "project": "708К.4", "kind": "stage", "item_index": "КБ1", "value": "В работе",
    })
    assert saved.status_code == 201, saved.text
    assert (saved.json()["event"]["from_value"], saved.json()["event"]["value"]) == ("Новый", "В работе")
    back = client.post("/api/production/plan/events", json={
        "project": "708К.4", "kind": "stage", "item_index": "КБ1", "value": "Новый",
    })
    assert back.status_code == 422 and back.json()["detail"] == "Этап назад — напишите причину."
    unknown = client.get("/api/production/plan/stage-preview", params={"project": "999К", "value": "В работе"})
    assert unknown.status_code == 422 and unknown.json()["detail"] == "Проект не найден в реестре."


class FakeZpk:
    """«Из расчёта» reader fake: the ЗКП sheet of the same calculation."""

    def __init__(self, grid):
        self.grid, self.calls = grid, 0

    def read_calc(self, spreadsheet_id):
        from app.modules.requests.application import CalcSpreadsheet

        self.calls += 1
        return CalcSpreadsheet(title="708К.4 Расчет", zpk_grid=self.grid, project_cell="708К.4")


ZPK = [
    ["№", "Тип", "Обозначение", "На проект", "Цена  без НДС", "Цена с НДС", "Ссылка", "Место поставки"],
    ["1", "Провод", "UL1007 22AWG", "15", "10", "12", "https://snab.example/ul1007", ""],
    ["2", "Разъём", "43025-0400", "20", "", "", "", ""],
    ["3", "Разъём", "БЕЗ КОЛ-ВА", "", "", "", "", ""],
]
REQUESTS = {"purpose": "Обеспечение производства", "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
            "procurement_deadline": "2026-10-06"}


def test_project_with_requests_files_them_once(kitset_api):
    client, _lot = kitset_api
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    app.state.calc_spreadsheet_reader = reader = FakeZpk(ZPK)
    try:
        plain = _load(client).json()["res"]
        assert (plain["requests"], plain["requests_error"]) == (None, "")
        assert reader.calls == 0

        res = _load(client, requests=REQUESTS).json()["res"]
        assert res["requests_error"] == ""
        assert res["requests"] == {"created": 2, "existing": 0, "skipped": ["БЕЗ КОЛ-ВА"]}
        with app.state.uow_factory() as uow:
            rows = sorted(uow.requests.recent(100), key=lambda row: row.name)
        assert [(row.name, row.quantity, row.unit, row.project_reference, row.quality_document,
                 str(row.planned_date), row.delivery_point) for row in rows] == [
            ("43025-0400", Decimal("20"), "шт", "708К.4", "Нет", "2026-10-06", REQUESTS["delivery_point"]),
            ("UL1007 22AWG", Decimal("15"), "м", "708К.4", "Нет", "2026-10-06", REQUESTS["delivery_point"]),
        ]

        again = _load(client, requests=REQUESTS).json()["res"]
        assert again["requests"] == {"created": 0, "existing": 2, "skipped": []}
        with app.state.uow_factory() as uow:
            assert len(uow.requests.recent(100)) == 2
    finally:
        del app.state.calc_spreadsheet_reader


def test_project_requests_need_newrequest_right_and_never_fail_the_project(kitset_api):
    client, _lot = kitset_api
    app.state.calc_spreadsheet_reader = FakeZpk(ZPK)
    try:
        refused = _load(client, requests=REQUESTS)
        assert refused.status_code == 200
        res = refused.json()["res"]
        assert (res["project"], res["requests"]) == ("708К.4", None)
        assert "Оформить заявку" in res["requests_error"]

        app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
        app.state.calc_spreadsheet_reader = FakeZpk([ZPK[0]])
        empty = _load(client, requests=REQUESTS).json()["res"]
        assert empty["requests"] is None and empty["requests_error"]
    finally:
        del app.state.calc_spreadsheet_reader


# ── Заказать недостающее ─────────────────────────────────────────────


RAMENSKOE = "г. Раменское, ул. 100-й Свирской Дивизии, д. 52"


def _file_request(name, quantity, *, article=None, point=RAMENSKOE, key="manual"):
    from app.modules.requests.application import CreateRequestsCommand, create_requests
    from app.modules.requests.domain import RequestDraft

    draft = RequestDraft(
        purpose="Обеспечение производства", delivery_point=point, name=name, item_type="Комплектующие",
        quantity=Decimal(quantity), unit="шт", quality_document="Нет", procurement_deadline=datetime(2026, 10, 9).date(),
        planned_date=datetime(2026, 10, 9).date(), project_reference="708К.4", design_article=article,
    )
    actor = Actor(id=UUID("40000000-0000-0000-0000-000000000009"), name="Снабженец", department="Снабжение")
    return create_requests(CreateRequestsCommand(
        creation_key=key, drafts=(draft,), actor=actor, now=datetime(2026, 9, 28, 9, 0),
    ), app.state.uow_factory())[0]


def _hand_over(request, quantity):
    """The request is in procurement: a live line «В работе» of the project."""
    from app.modules.procurement.domain import ProcurementStatus, create_line_from_requested
    from app.shared.events import OutboxMessage

    line = create_line_from_requested(OutboxMessage(
        id=UUID("50000000-0000-0000-0000-000000000001"), topic="procurement.requested",
        idempotency_key=f"request:{request.id}:procurement:v2", created_at=datetime(2026, 9, 28, 10, 0),
        payload={
            "source_request_id": str(request.id), "legacy_id": request.legacy_id, "request_date": "2026-09-28",
            "initiator": "Снабженец", "department": "Снабжение", "task_type": "Закупка",
            "designation": request.designation, "project_reference": "708К.4", "priority": "Срочно",
            "delivery_point": request.delivery_point, "planned_date": "2026-10-09", "item_type": "Комплектующие",
            "design_article": request.design_article, "category": "Общепром", "calculated_name": request.name,
            "calculated_quantity": quantity, "calculated_unit": "шт",
        },
    ))
    with app.state.procurement_uow_factory() as uow:
        uow.procurement.add(replace(line, status=ProcurementStatus.IN_WORK))
        uow.commit()


def _supply(client):
    lines = client.get("/api/techcards/kitsets/708К.4/supply").json()["lines"]
    return {line["art"]: line for line in lines}


def test_order_missing_orders_only_what_stock_and_existing_purchases_do_not_cover(kitset_api, scrap_allowance_on):
    client, _lot = kitset_api  # 15 шт of 43025-0400 lie free on the shelf
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    assert _load(client, deadline="2026-10-20").status_code == 200
    _file_request("Корпус Molex", "3")  # manual, no АРТИКУЛ КД: found by the spec name
    _hand_over(_file_request("Контакт", "30", article="43030-0007", key="contact"), "30")

    supply = _supply(client)
    picked = {art: (line["need"], line["free"], line["ordered"], line["short"], line["stage"]) for art, line in supply.items()}
    assert picked == {
        "UL1007 22AWG": ("15.75", "0", "0", "15.75", "не заведено"),
        "43025-0400": ("20", "15", "3", "2", "Запрос · Новый"),
        "43030-0007": ("32", "0", "30", "2", "Закупка · В работе"),
    }

    ordered = client.post("/api/techcards/kitsets/708К.4/order-missing", json={})
    assert ordered.status_code == 200, ordered.text
    body = ordered.json()
    assert body["message"] == "Заявки созданы: 3."
    assert sorted((item["art"], item["quantity"], item["unit"]) for item in body["created"]) == [
        ("43025-0400", "2", "шт"), ("43030-0007", "2", "шт"), ("UL1007 22AWG", "15.75", "м"),
    ]
    with app.state.uow_factory() as uow:
        filed = [row for row in uow.requests.recent(100) if row.creation_key.startswith("kitset-missing:")]
    assert {(row.design_article, row.name, row.project_reference, row.purpose, row.delivery_point,
             row.quality_document, str(row.planned_date), str(row.procurement_deadline), row.item_type)
            for row in filed} == {
        ("UL1007 22AWG", "UL1007 22AWG", "708К.4", "Обеспечение производства", RAMENSKOE, "Нет",
         "2026-10-20", "2026-10-20", "Провод"),
        ("43025-0400", "Корпус Molex", "708К.4", "Обеспечение производства", RAMENSKOE, "Нет",
         "2026-10-20", "2026-10-20", "Разъём"),
        ("43030-0007", "43030-0007", "708К.4", "Обеспечение производства", RAMENSKOE, "Нет",
         "2026-10-20", "2026-10-20", "Контакт"),
    }
    assert {row.designation for row in filed} == {"708К.4.001"}
    assert {row.comment for row in filed} == {"708К.4 · КБ1", "708К.4 · КБ2"}

    again = client.post("/api/techcards/kitsets/708К.4/order-missing", json={}).json()
    assert again["created"] == []
    assert again["message"] == "Заявки созданы: 0, пропущено 3 (уже в закупке)."
    with app.state.uow_factory() as uow:
        assert len(uow.requests.recent(100)) == 5
    assert {art: line["short"] for art, line in _supply(client).items()} == {
        "UL1007 22AWG": "0", "43025-0400": "0", "43030-0007": "0",
    }


def test_order_missing_picked_lines_deadline_and_rights(kitset_api, scrap_allowance_on):
    client, _lot = kitset_api
    assert _load(client).status_code == 200  # no deadline on the project
    refused = client.post("/api/techcards/kitsets/708К.4/order-missing", json={})
    assert refused.status_code == 403
    assert "Оформить заявку" in refused.json()["detail"]

    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    no_deadline = client.post("/api/techcards/kitsets/708К.4/order-missing", json={"articles": ["UL1007 22AWG"]})
    assert no_deadline.status_code == 422
    assert "дедлайна" in no_deadline.json()["detail"]

    client.put("/api/techcards/kitsets/708К.4/lines", json={"edits": [
        {"index": "КБ1", "art": "43025-0400", "per": "1", "scrap": "0"},
    ]})  # 10 needed, 15 free on the shelf
    covered = client.post("/api/techcards/kitsets/708К.4/order-missing", json={
        "articles": ["43025-0400"], "deadline": "2026-10-15",
    }).json()
    assert covered["created"] == []
    assert covered["skipped"] == [{"art": "43025-0400", "kind": "хватает на складе", "reason": "свободно на складе: 15"}]
    assert covered["message"] == "Заявки созданы: 0, пропущено 1 (хватает на складе)."

    picked = client.post("/api/techcards/kitsets/708К.4/order-missing", json={
        "articles": ["ul1007  22awg", "НЕТ-ТАКОГО"], "deadline": "2026-10-15",
    }).json()
    assert [(item["art"], item["quantity"]) for item in picked["created"]] == [("UL1007 22AWG", "15.75")]
    assert [item["kind"] for item in picked["skipped"]] == ["нет в спецификации"]
    with app.state.uow_factory() as uow:
        (filed,) = uow.requests.recent(100)
    # No earlier request of the project: the first point of the directory, the deadline given.
    assert (filed.delivery_point, str(filed.planned_date)) == ("г. Москва, ул. 6-я Радиальная, д. 17", "2026-10-15")
    assert _supply(client)["43030-0007"]["short"] == "32"  # not picked — not ordered
