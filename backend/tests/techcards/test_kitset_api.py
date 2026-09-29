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
from tests.techcards.xlsx_builder import calc_workbook


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
    yield TestClient(app), lot
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    app.dependency_overrides.pop(moscow_today, None)
    for name in STATE:
        delattr(app.state, name)
    engine.dispose()


def _load(client, **extra):
    return client.post("/api/techcards/kitsets/load", json={
        "file_name": "708К.4 Расчет.xlsx",
        "content_b64": base64.b64encode(calc_workbook()).decode(),
        **extra,
    })


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
    downloaded = client.get(f"/api/techcards/kitsets/708К.4/documents/{document['id']}")
    assert downloaded.content == b"%PDF-1.4 test"

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
    assert (res["deleted"], res["kd"], res["cards"]["cards"], res["gp"]["deleted"], res["erp"]) == (
        3, 1, 1, 2, {"Проекты": 1},
    )
    gone = client.get("/api/techcards/kitsets/708К.4")
    assert gone.status_code == 404
    assert gone.json()["detail"]["gone"] is True


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


class _Sheets:
    """Fake Google Sheets: ЗКЗ and СПЯ as display strings, like `values.batchGet` FORMATTED_VALUE."""

    def __init__(self, sheets, error=None):
        self.sheets, self.error, self.calls = sheets, error, []

    def read_sheets(self, spreadsheet_id, names):
        self.calls.append((spreadsheet_id, names))
        if self.error:
            raise self.error
        return "708К.4.1 Расчет", {name: grid for name, grid in self.sheets.items() if name in names}


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
}


def test_project_from_calculation_link_reads_google_sheet(kitset_api):
    """`tcReadCalcKitset_(calcUrl)`: ЗКЗ + СПЯ by link; the file title refines the number; link kept as РАСЧЁТ."""
    client, _lot = kitset_api
    reader = _Sheets(SHEETS)
    app.state.kitset_sheets_reader = reader
    try:
        response = client.post("/api/techcards/kitsets/load", json={"url": LINK})
    finally:
        del app.state.kitset_sheets_reader
    assert response.status_code == 200, response.text
    assert response.json()["res"]["project"] == "708К.4.1"
    assert reader.calls == [("1AbC-calc_id", ("ЗКЗ", "СПЯ"))]
    card = client.get("/api/techcards/kitsets/708К.4.1").json()
    assert card["calc_source"] == LINK
    assert [item["art"] for item in card["assemblies"][0]["components"]] == ["UL1007 22AWG"]


def test_calculation_link_refusals(kitset_api):
    from app.modules.requests.application import CalcSheetError

    client, _lot = kitset_api
    assert client.post("/api/techcards/kitsets/load", json={"url": "https://example.test/x"}).status_code == 422
    assert client.post("/api/techcards/kitsets/load", json={}).status_code == 422
    app.state.kitset_sheets_reader = _Sheets({}, CalcSheetError("Нет доступа к таблице расчёта", "access"))
    try:
        refused = client.post("/api/techcards/kitsets/load", json={"url": LINK})
    finally:
        del app.state.kitset_sheets_reader
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
    reader = _Sheets({**SHEETS, "КБ1": kb})
    app.state.kitset_sheets_reader = reader
    try:
        assert client.post("/api/techcards/kitsets/load", json={"url": LINK}).status_code == 200
        bom = client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()
        app.state.kitset_sheets_reader = _Sheets({}, CalcSheetError("Нет доступа к таблице расчёта", "access"))
        failed = client.get("/api/techcards/kitsets/708К.4.1/bom/КБ1").json()
    finally:
        del app.state.kitset_sheets_reader
    assert reader.calls[-1] == ("1AbC-calc_id", ("КБ1",))
    assert bom["calc_ops"] == [
        {"code": "CUT_WIRE", "comp": "UL1007 22AWG", "n": "4", "l": "0,16", "req": "L=150мм + SHF-001T"},
        {"code": "SDR_tin", "comp": "", "n": "", "l": "", "req": ""},
    ]
    assert failed["calc_ops"] == []
    assert bom["run"] == "10"
