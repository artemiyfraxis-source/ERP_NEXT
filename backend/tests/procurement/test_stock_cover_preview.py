"""«Со склада» × preview of the kit issue: the project's own reserve is picked first, other projects see it taken."""

from decimal import Decimal

from app.main import app
from app.modules.requests.application import CalcSpreadsheet
from app.modules.warehouse.domain import StockLot
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.fulfillment.test_issue_calc import FakeReader, URL
from tests.procurement.test_stock_cover import add_lot, cover, env, line_in_work  # noqa: F401 — fixture
from tests.requests.test_calc_import import GRID


def wire(grn: str, quantity: str) -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name="МГТФ 0,35", item_type="Провод", purpose="Склад", unit="шт",
        quantity=Decimal(quantity), storage_location="1A1", source_receiving_ids=(f"r-{grn}",),
    )


def as_planner() -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"), email="k@example.test", roles=frozenset({"receiver"}),
        permissions={"fulfillment": "edit", "projects": "edit", "warehouse": "view"},
    )


def preview(client, title: str = "Расчёт 591К.3") -> dict:
    grid = [[title, *GRID[0][1:]], *GRID[1:]]
    app.state.calc_spreadsheet_reader = FakeReader(CalcSpreadsheet(title=title, zpk_grid=grid))
    try:
        response = client.post("/api/issues/calc-preview", json={"url": URL, "warehouse": "ЦАР"})
    finally:
        del app.state.calc_spreadsheet_reader
    assert response.status_code == 200, response.text
    return response.json()["lines"][0]


def reserved_setup(client, sessions):
    add_lot(sessions, wire("OLD.25.0000009", "10"))
    new = add_lot(sessions, wire("FRX.26.0000005", "10"))
    line = line_in_work(client, sessions)  # a procurement line of 591К.3
    assert cover(client, line, new, "8").status_code == 200  # the newer lot: reserve 8, available 2
    as_planner()


def test_preview_of_the_owning_project_picks_the_reserved_lot_first(env):  # noqa: F811
    client, sessions = env
    reserved_setup(client, sessions)

    line = preview(client)  # need 12.5

    assert [(p["grn"], p["qty"], p.get("reserved")) for p in line["picks"]] == [
        ("FRX.26.0000005", "10", "8"),  # 8 of the reserve + 2 free — before the older lot
        ("OLD.25.0000009", "2.5", None),
    ]
    assert line["short"] == "0"


def test_preview_of_another_project_sees_the_reserve_as_taken(env):  # noqa: F811
    client, sessions = env
    reserved_setup(client, sessions)

    line = preview(client, "Расчёт 700К.1")

    assert [(p["grn"], p["qty"], "reserved" in p) for p in line["picks"]] == [
        ("OLD.25.0000009", "10", False), ("FRX.26.0000005", "2", False),
    ]
    assert line["short"] == "0.5"


def test_a_fully_reserved_lot_is_offered_to_its_project_only(env):  # noqa: F811
    client, sessions = env
    lot = add_lot(sessions, wire("FRX.26.0000005", "10"))
    assert cover(client, line_in_work(client, sessions), lot, "10").status_code == 200  # available 0
    as_planner()

    own = preview(client)
    assert [(p["grn"], p["qty"], p["reserved"]) for p in own["picks"]] == [("FRX.26.0000005", "10", "10")]
    assert preview(client, "Расчёт 700К.1")["found"] is False  # nothing free for the other project

    def lots(path: str) -> list[dict]:
        return client.get(path).json()["lots"]

    assert [(l["grn"], l["available"], l.get("reserved")) for l in lots("/api/issues/lots?project=591К.3")] == [
        ("FRX.26.0000005", "10", "10")]
    assert lots("/api/issues/lots") == [] and lots("/api/issues/lots?project=700К.1") == []
    assert [(l["grn"], l["available"], l.get("reserved")) for l in lots("/api/techcards/kitsets/lots?project=591К.3")] == [
        ("FRX.26.0000005", "10", "10")]
    assert lots("/api/techcards/kitsets/lots") == []
