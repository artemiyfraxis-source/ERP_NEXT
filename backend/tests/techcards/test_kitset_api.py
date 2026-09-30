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


def test_supply_marks_documents_bom_techcard_and_cascade_delete(kitset_api):
    client, _lot = kitset_api
    _load(client)
    supply = client.get("/api/techcards/kitsets/708К.4/supply").json()
    assert {line["art"]: line["stage"] for line in supply["lines"]} == {
        "UL1007 22AWG": "не заведено", "43025-0400": "не заведено", "43030-0007": "не заведено",
    }

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
        "url": None, "uploaded_at": "2026-09-29T10:00:00", "who": "Технолог",
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
        ("UL1007 22AWG", "1.5", "0.05"), ("43025-0400", "2", "0.05"),
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
    gone = client.get("/api/techcards/kitsets/708К.4")
    assert gone.status_code == 404
    assert gone.json()["detail"]["gone"] is True
    # tcKitsetDelete не трогает лист «КД»: вложение возвращается с проектом.
    _load(client)
    back = client.get("/api/techcards/kitsets/708К.4").json()
    assert [item["id"] for item in back["assemblies"][0]["kd"]] == [document["id"]]
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
        "uploaded_at": "2026-09-29T09:00:00", "who": "Технолог",
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
