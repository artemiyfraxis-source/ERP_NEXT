"""«Заявка по расчёту»: SUPPLYMAP IssueCalc.html → issueCalcPreview / issueCalcMatch_ → queueIssueRequest."""

from decimal import Decimal

from app.main import app
from app.modules.requests.application import CalcSheetError, CalcSpreadsheet
from app.modules.warehouse.domain import StockLot
from app.workflows.issue_calc import CalcPosition, calc_match, calc_number, is_same_item
from tests.fulfillment.test_api import fulfillment_api  # noqa: F401 — fixture
from tests.requests.test_calc_import import GRID

URL = "https://docs.google.com/spreadsheets/d/1AbC-d_9/edit#gid=0"


class FakeReader:
    def __init__(self, sheet: CalcSpreadsheet | None = None, error: CalcSheetError | None = None):
        self.sheet = sheet
        self.error = error

    def read_calc(self, spreadsheet_id: str) -> CalcSpreadsheet:
        if self.error:
            raise self.error
        return self.sheet


def lot(grn: str, name: str, quantity: str, unit: str = "м", warehouse: str = "ЦАР", article: str = "") -> StockLot:
    return StockLot.create(
        warehouse=warehouse, grn=grn, package="", name=name, item_type="Провод", purpose="Склад", unit=unit,
        quantity=Decimal(quantity), storage_location="1A1", source_receiving_ids=(f"r-{grn}",), article=article,
    )


def test_is_same_item_follows_schema_gs():
    assert is_same_item("МГТФ 0,35", "Провод МГТФ-0,35 белый")
    assert not is_same_item("МГТФ 0,50", "Провод МГТФ 0,35")
    assert is_same_item("DB-9M", "")  # empty stock name counts as the same item (isSameItem_)
    assert calc_number("12,5") == Decimal("12.5") and calc_number("—") == 0


def test_calc_match_picks_fifo_and_reports_shortfall_and_missing():
    stock = [lot("FRX.26.0000005", "МГТФ 0,35", "4"), lot("OLD.25.0000009", "МГТФ 0,35", "8")]
    lines = calc_match([
        CalcPosition("МГТФ 0,35", Decimal("12.5"), "м"),
        CalcPosition("DB-9M", Decimal("4"), "шт"),
    ], stock)

    assert [(pick["grn"], pick["qty"]) for pick in lines[0]["picks"]] == [("OLD.25.0000009", "8"), ("FRX.26.0000005", "4")]
    assert (lines[0]["found"], lines[0]["short"]) == (True, "0.5")
    assert (lines[1]["found"], lines[1]["short"], lines[1]["picks"]) == (False, "4", [])


def test_preview_then_create_through_the_ordinary_request_path(fulfillment_api):  # noqa: F811
    client, _fixture_lot, factory = fulfillment_api
    wire = lot("OLD.25.0000009", "МГТФ 0,35", "10")
    with factory() as uow:
        uow.warehouse.add(wire)
        uow.warehouse.add(lot("FRX.26.0000020", "МГТФ 0,35", "50", warehouse="ЖУК"))
        uow.commit()
    app.state.calc_spreadsheet_reader = FakeReader(CalcSpreadsheet(title="Расчёт 591К.3", zpk_grid=GRID))
    try:
        unknown = client.post("/api/issues/calc-preview", json={"url": URL, "warehouse": "СКЛАД"})
        preview = client.post("/api/issues/calc-preview", json={"url": URL, "warehouse": "ЦАР"})
    finally:
        del app.state.calc_spreadsheet_reader

    assert unknown.status_code == 422 and unknown.json()["detail"] == "Неизвестный склад: СКЛАД"
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["project"] == "591К.3"
    wire_line, connector = body["lines"]
    assert [(pick["grn"], pick["qty"]) for pick in wire_line["picks"]] == [("OLD.25.0000009", "10")]
    assert wire_line["short"] == "2.5"
    # the fixture lot «Разъём» does not match «DB-9M»: nothing on this warehouse
    assert (connector["found"], connector["short"]) == (False, "4")

    created = client.post("/api/issues", headers={"Idempotency-Key": "calc-591"}, json={
        "purpose": body["project"],
        "deadline": "2026-10-01",
        "lines": [{
            "article": pick["article"], "name": pick["name"], "unit": pick["unit"], "requirement": pick["qty"],
            "lot_id": pick["lot_id"],
        } for line in body["lines"] for pick in line["picks"]],
        "short_note": [f"{line['name']}: нет на складе (−{line['qty']})" for line in body["lines"] if not line["found"]],
    })

    assert created.status_code == 200, created.text
    request = created.json()["request"]
    assert request["status"] == "Новая"
    assert request["shortfall"] == "DB-9M: нет на складе (−4)"
    with factory() as uow:
        assert uow.warehouse.get(wire.id).reserved == Decimal("10")

    # issueHeadSync_: the first lot operation rewrites НЕДОБОР from the lines.
    unbound = client.request(
        "DELETE", f"/api/issues/{request['id']}/lines/{request['lines'][0]['id']}/bind",
        json={"version": request["version"]},
    )
    assert unbound.json()["request"]["shortfall"] == "МГТФ 0,35 (ГРН не выбран): −10"


def test_preview_reports_a_calc_sheet_error(fulfillment_api):  # noqa: F811
    client, _lot, _factory = fulfillment_api
    app.state.calc_spreadsheet_reader = FakeReader(error=CalcSheetError("Нет доступа к таблице", kind="access"))
    try:
        response = client.post("/api/issues/calc-preview", json={"url": URL, "warehouse": "ЦАР"})
    finally:
        del app.state.calc_spreadsheet_reader

    assert response.status_code == 422
    assert response.json()["detail"] == "Нет доступа к таблице"
