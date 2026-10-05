"""«Запросы на комплектацию» (owner 05.10.2026): one or several ГРН for one item across every КБ of the project,
«есть, но не заведено», techcard reserves taken over by the kit of the same project + КБ."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.domain import KitReserve
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import WriteOffEntryRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor


NOW = datetime(2026, 10, 5, 12, 0)


def stock(grn: str, quantity: str, name: str = "Разъём") -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name=name, item_type="Комплектующие", purpose="Склад",
        unit="шт", quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receiving-{grn}",),
    )


@pytest.fixture()
def api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"),
        email="receiver@example.test",
        roles=frozenset({"receiver"}),
        permissions={"fulfillment": "edit", "handout": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), factory
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.fulfillment_uow_factory
    engine.dispose()


def put_lots(factory, *lots: StockLot) -> None:
    with factory() as uow:
        for lot in lots:
            uow.warehouse.add(lot)
        uow.commit()


def create(client, purpose: str, *lines: tuple[str, str, str]) -> dict:
    response = client.post(
        "/api/issues",
        headers={"Idempotency-Key": f"test-{uuid4()}"},
        json={
            "purpose": purpose,
            "deadline": "2026-10-20",
            "lines": [{"article": article, "name": name, "unit": "шт", "requirement": need}
                      for article, name, need in lines],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["request"]


def fetch(client, request_id: str) -> dict:
    return next(item for item in client.get("/api/issues").json()["requests"] if item["id"] == request_id)


def bind_lots(client, project: str, versions: dict, **body):
    return client.post(
        f"/api/issues/projects/{project}/bind-lots",
        json={"article": "КД-1", "name": "Разъём", "versions": versions, **body},
    )


def item_lines(request: dict) -> list[dict]:
    return [line for line in request["lines"] if line["article"] == "КД-1"]


def test_two_lots_go_to_three_kb_in_natural_order_fifo_by_grn_with_a_split_and_a_short(api):
    client, factory = api
    newer, older = stock("FRX.26.0000010", "4"), stock("FRX.26.0000009", "5")
    put_lots(factory, newer, older)
    kb10 = create(client, "728К.2 · КБ10 · Жгут", ("КД-1", "Разъём", "4"))
    kb2 = create(client, "728К.2 · КБ2 · Жгут", ("КД-1", "Разъём", "4"), ("КД-2", "Провод", "3"))
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    other = create(client, "729К.1 · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    versions = {item["id"]: item["version"] for item in (kb10, kb2, kb1)}

    response = bind_lots(client, "728К.2", versions, lot_ids=[str(newer.id), str(older.id)])

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(row["kb"], row["grn"], row["quantity"]) for row in body["bound"]] == [
        ("КБ1", "FRX.26.0000009", "4"),
        ("КБ2", "FRX.26.0000009", "1"),
        ("КБ2", "FRX.26.0000010", "3"),
        ("КБ10", "FRX.26.0000010", "1"),
    ]
    assert body["short"] == "3"
    assert "Не хватает 3 шт" in body["message"]
    assert {item["id"]: item["version"] for item in body["requests"]} == {
        kb1["id"]: kb1["version"] + 1, kb2["id"]: kb2["version"] + 1, kb10["id"]: kb10["version"] + 1,
    }

    first, second, tenth = fetch(client, kb1["id"]), fetch(client, kb2["id"]), fetch(client, kb10["id"])
    assert [(line["lot"]["grn"], line["reserved"]) for line in item_lines(first)] == [("FRX.26.0000009", "4")]
    assert [(line["lot"]["grn"], line["requirement"]) for line in item_lines(second)] == [
        ("FRX.26.0000009", "1"), ("FRX.26.0000010", "3"),
    ]
    assert [((line["lot"] or {}).get("grn"), line["requirement"], line["reserved"]) for line in item_lines(tenth)] == [
        ("FRX.26.0000010", "1", "1"), (None, "3", "0"),
    ]
    assert first["status"] == "Недоукомплектован" and tenth["status"] == "Недоукомплектован"
    provod = next(line for line in second["lines"] if line["article"] == "КД-2")
    assert provod["lot"] is None and provod["reserved"] == "0"  # another item of the КБ is not touched
    assert fetch(client, other["id"])["version"] == other["version"]  # another project is not touched
    with factory() as uow:
        assert uow.warehouse.get(older.id).reserved == Decimal("5")
        assert uow.warehouse.get(newer.id).reserved == Decimal("4")


def test_lines_that_already_have_a_lot_stay_and_only_waiting_need_is_filled(api):
    client, factory = api
    picked, fresh = stock("FRX.26.0000001", "2"), stock("FRX.26.0000002", "10")
    put_lots(factory, picked, fresh)
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "5"))
    line = kb1["lines"][0]
    bound = client.post(
        f"/api/issues/{kb1['id']}/lines/{line['id']}/bind", json={"version": kb1["version"], "lot_id": str(picked.id)},
    ).json()["request"]

    response = bind_lots(client, "728К.2", {bound["id"]: bound["version"]}, lot_ids=[str(picked.id), str(fresh.id)])

    assert response.status_code == 200, response.text
    assert [(row["grn"], row["quantity"]) for row in response.json()["bound"]] == [("FRX.26.0000002", "3")]
    assert response.json()["short"] == "0"
    lines = fetch(client, kb1["id"])["lines"]
    assert [(item["lot"]["grn"], item["reserved"]) for item in lines] == [
        ("FRX.26.0000001", "2"), ("FRX.26.0000002", "3"),
    ]


def test_stale_version_is_refused_with_409_and_nothing_moves(api):
    client, factory = api
    lot = stock("FRX.26.0000001", "10")
    put_lots(factory, lot)
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    kb2 = create(client, "728К.2 · КБ2 · Жгут", ("КД-1", "Разъём", "4"))

    stale = bind_lots(client, "728К.2", {kb1["id"]: kb1["version"], kb2["id"]: kb2["version"] + 5},
                      lot_ids=[str(lot.id)])
    unseen = bind_lots(client, "728К.2", {kb1["id"]: kb1["version"]}, lot_ids=[str(lot.id)])

    assert stale.status_code == 409, stale.text
    assert unseen.status_code == 409, unseen.text
    assert kb2["number"] in unseen.json()["detail"]
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == Decimal("0")
    assert fetch(client, kb1["id"])["version"] == kb1["version"]


def test_bulk_bind_refuses_without_lots_without_waiting_lines_and_with_empty_lots(api):
    client, factory = api
    empty = stock("FRX.26.0000001", "3")
    put_lots(factory, empty.reserve(Decimal("3")))
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    versions = {kb1["id"]: kb1["version"]}

    assert bind_lots(client, "728К.2", versions).status_code == 422
    assert bind_lots(client, "728К.2", versions, lot_ids=[str(empty.id)], off_book=True).status_code == 422
    assert bind_lots(client, "728К.2", versions, lot_ids=[str(empty.id)]).status_code == 422  # nothing available
    assert bind_lots(client, "728К.2", versions, lot_ids=[str(uuid4())]).status_code == 404
    assert bind_lots(client, "999К.1", {}, lot_ids=[str(empty.id)]).status_code == 422  # no waiting lines
    assert fetch(client, kb1["id"])["version"] == kb1["version"]


def test_off_book_for_the_whole_project_then_issue_writes_nothing_to_writeoffs(api):
    client, factory = api
    lot = stock("FRX.26.0000001", "10", name="Провод")
    put_lots(factory, lot)
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "4"), ("КД-2", "Провод", "2"))
    kb2 = create(client, "728К.2 · КБ2 · Жгут", ("КД-1", "Разъём", "3"))

    marked = bind_lots(client, "728К.2", {kb1["id"]: kb1["version"], kb2["id"]: kb2["version"]}, off_book=True)

    assert marked.status_code == 200, marked.text
    assert [(row["kb"], row["quantity"], row["off_book"], row["lot_id"]) for row in marked.json()["bound"]] == [
        ("КБ1", "4", True, None), ("КБ2", "3", True, None),
    ]
    request = fetch(client, kb1["id"])
    assert item_lines(request)[0]["off_book"] is True
    assert "Разъём" not in request["shortfall"]
    provod = next(line for line in request["lines"] if line["article"] == "КД-2")
    request = client.post(
        f"/api/issues/{request['id']}/lines/{provod['id']}/bind", json={"version": request["version"], "lot_id": str(lot.id)},
    ).json()["request"]
    assert request["fully_reserved"] is True
    request = client.post(f"/api/issues/{request['id']}/complete", json={"version": request["version"]}).json()["request"]
    request = client.post(
        f"/api/issues/{request['id']}/ready", json={"version": request["version"], "handout_location": "Полка 1"},
    ).json()["request"]
    sheet = client.get(f"/api/issues/{request['id']}/print").json()["sheet"]
    issued = client.post(f"/api/issues/{request['id']}/issue", json={"version": request["version"]})

    assert issued.status_code == 200, issued.text
    off_book_line = item_lines(issued.json()["request"])[0]
    assert off_book_line["issued"] == "4" and off_book_line["lot"] is None
    assert any("есть, не заведено" in row for row in sheet["rows"])
    assert "Разъём" not in sheet["note"]
    with factory() as uow:
        rows = list(uow.session.scalars(select(WriteOffEntryRow)))
        assert [(row.grn, row.quantity) for row in rows] == [("FRX.26.0000001", Decimal("2"))]
        assert uow.warehouse.get(lot.id).balance == Decimal("8")


def test_off_book_line_mark_unmark_and_return_refuses_good_back(api):
    client, factory = api
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    line = kb1["lines"][0]
    url = f"/api/issues/{kb1['id']}/lines/{line['id']}/off-book"

    marked = client.post(url, json={"version": kb1["version"]})
    stale = client.post(url, json={"version": kb1["version"]})
    again = client.post(url, json={"version": marked.json()["request"]["version"]})
    cleared = client.request("DELETE", url, json={"version": marked.json()["request"]["version"]})

    assert marked.status_code == 200, marked.text
    assert marked.json()["request"]["lines"][0]["off_book"] is True
    assert marked.json()["request"]["status"] == "Недоукомплектован"
    assert stale.status_code == 409
    assert again.status_code == 422
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["request"]["lines"][0]["off_book"] is False

    request = client.post(url, json={"version": cleared.json()["request"]["version"]}).json()["request"]
    request = client.post(f"/api/issues/{request['id']}/complete", json={"version": request["version"]}).json()["request"]
    request = client.post(
        f"/api/issues/{request['id']}/ready", json={"version": request["version"], "handout_location": "Полка 1"},
    ).json()["request"]
    request = client.post(f"/api/issues/{request['id']}/issue", json={"version": request["version"]}).json()["request"]
    request = client.post(
        f"/api/issues/{request['id']}/returned", json={"version": request["version"], "return_location": "Возврат"},
    ).json()["request"]
    good_back = client.post(f"/api/issues/{request['id']}/complete-return", json={
        "version": request["version"], "lines": [{"line_id": request["lines"][0]["id"], "returned_good": "1"}],
    })
    defect_only = client.post(f"/api/issues/{request['id']}/complete-return", json={
        "version": request["version"], "lines": [{"line_id": request["lines"][0]["id"], "defective_returned": "1"}],
    })

    assert good_back.status_code == 422
    assert "есть, не заведено" in good_back.json()["detail"]
    assert defect_only.status_code == 200, defect_only.text
    assert defect_only.json()["request"]["lines"][0]["consumed"] == "3"
    with factory() as uow:
        events = list(uow.session.scalars(
            select(AuditEventRow.event_type).where(AuditEventRow.event_type.like("fulfillment.off_book%"))
        ))
    assert sorted(events) == [
        "fulfillment.off_book_cleared", "fulfillment.off_book_marked", "fulfillment.off_book_marked",
    ]


def kit_reserve(lot: StockLot, project: str, item_index: str, quantity: str) -> KitReserve:
    return KitReserve(
        id=uuid4(), project=project, item_index=item_index, article="КД-1", lot_id=lot.id,
        warehouse=lot.warehouse, grn=lot.grn, package=lot.package, unit=lot.unit, quantity=Decimal(quantity),
        created_at=NOW, created_by="Технолог",
    )


def test_kit_of_the_same_project_and_kb_takes_the_techcard_reserve_over_first(api):
    client, factory = api
    lot = stock("FRX.26.0000001", "10")
    reserve = kit_reserve(lot, "728К.2", "КБ1", "6")
    with factory() as uow:
        uow.warehouse.add(lot.reserve(Decimal("6")))
        uow.kit_reserves.add(reserve)
        uow.commit()
        # the warehouse row edit may not go under the techcard reserve either
    with factory() as uow:
        assert uow.fulfillment.reserved_on_lot(lot.id) == Decimal("6")

    picker_all = client.get("/api/issues/lots", params={"project": "728К.2"}).json()["lots"]
    picker_other = client.get("/api/issues/lots", params={"project": "729К.1"}).json()["lots"]
    assert [(row["lot_id"], row["available"], row["reserved"]) for row in picker_all] == [(str(lot.id), "10", "6")]
    assert [(row["available"], "reserved" in row) for row in picker_other] == [("4", False)]

    kb2 = create(client, "728К.2 · КБ2 · Жгут", ("КД-1", "Разъём", "5"))
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "8"))
    response = bind_lots(client, "728К.2", {kb1["id"]: kb1["version"], kb2["id"]: kb2["version"]},
                         lot_ids=[str(lot.id)])

    assert response.status_code == 200, response.text
    # КБ1 gets its 6 reserved + 2 of ДОСТУПНО; КБ2 the last 2 free — never КБ1's reserve.
    assert [(row["kb"], row["quantity"]) for row in response.json()["bound"]] == [("КБ1", "8"), ("КБ2", "2")]
    assert response.json()["short"] == "3"
    with factory() as uow:
        taken = uow.kit_reserves.get(reserve.id)
        assert (taken.quantity, taken.consumed, taken.consumed_by) == (Decimal("0"), Decimal("6"), kb1["number"])
        stored = uow.warehouse.get(lot.id)
        assert (stored.reserved, stored.available) == (Decimal("10"), Decimal("0"))
        assert uow.fulfillment.reserved_on_lot(lot.id) == Decimal("10")


def test_single_bind_takes_only_its_own_kb_reserve(api):
    client, factory = api
    lot = stock("FRX.26.0000001", "5")
    reserve = kit_reserve(lot, "728К.2", "КБ1", "5")
    with factory() as uow:
        uow.warehouse.add(lot.reserve(Decimal("5")))
        uow.kit_reserves.add(reserve)
        uow.commit()
    kb2 = create(client, "728К.2 · КБ2 · Жгут", ("КД-1", "Разъём", "2"))
    refused = client.post(
        f"/api/issues/{kb2['id']}/lines/{kb2['lines'][0]['id']}/bind",
        json={"version": kb2["version"], "lot_id": str(lot.id)},
    )
    kb1 = create(client, "728К.2 · КБ1 · Жгут", ("КД-1", "Разъём", "2"))
    taken = client.post(
        f"/api/issues/{kb1['id']}/lines/{kb1['lines'][0]['id']}/bind",
        json={"version": kb1["version"], "lot_id": str(lot.id)},
    )

    assert refused.status_code == 422
    assert taken.status_code == 200, taken.text
    with factory() as uow:
        assert uow.kit_reserves.get(reserve.id).quantity == Decimal("3")
        assert uow.warehouse.get(lot.id).reserved == Decimal("5")
        assert uow.session.scalar(select(func.count()).select_from(WriteOffEntryRow)) == 0
