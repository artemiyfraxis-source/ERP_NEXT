"""«Выдать набранное» (owner 17.11.2026, 663К.1: «добрать остатки и выдать набранное нельзя»): a kit still being picked
hands out its covered lines through the normal issue, every line still waiting moves unchanged into a new «Довыдача»."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.domain import (
    FulfillmentKind,
    FulfillmentLine,
    FulfillmentRequest,
    FulfillmentStatus,
    FulfillmentValidationError,
    LotSnapshot,
)
from app.modules.fulfillment.application import ConcurrentFulfillmentUpdate
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import WriteOffEntryRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

PROJECT = "663К.1"
NOW = datetime(2026, 11, 18, 10, 0)


# --- domain ---------------------------------------------------------------------------------------------------------


def _snapshot(grn: str = "FRX.26.0000001") -> LotSnapshot:
    return LotSnapshot(lot_id=uuid4(), warehouse="ЦАР", grn=grn, package="", storage_location="1F4")


def _request(*needs: tuple[str, str], kind: FulfillmentKind = FulfillmentKind.MAIN) -> FulfillmentRequest:
    return FulfillmentRequest.create(
        number="REQ.26.0000100", source_idempotency_key="key", source_fingerprint="print", requester="Мастер",
        purpose=f"{PROJECT} · КБ7 · Жгут", deadline=date(2026, 11, 20),
        lines=tuple(FulfillmentLine.create(article=f"КД-{name}", name=name, unit="шт", requirement=Decimal(qty))
                    for name, qty in needs),
        created_at=NOW, kind=kind,
    )


def _line(request: FulfillmentRequest, name: str) -> FulfillmentLine:
    return next(line for line in request.lines if line.name == name)


def _picking_kit() -> FulfillmentRequest:
    """Разъём bound in full, Клемма «есть, но не заведено», Провод bound 6 of 10 (the rest waits), Втулка «нет на
    складе», Гильза waits."""
    request = _request(("Разъём", "2"), ("Клемма", "4"), ("Провод", "10"), ("Втулка", "3"), ("Гильза", "1"))
    request = request.bind_line(_line(request, "Разъём").id, _snapshot(), Decimal("2"))
    request = request.mark_off_book(_line(request, "Клемма").id)
    request = request.bind_line(_line(request, "Провод").id, _snapshot("FRX.26.0000002"), Decimal("6"))
    return request.mark_absent(_line(request, "Втулка").id, "Кладовщик", NOW)


def test_split_keeps_the_covered_lines_and_returns_every_open_one_unchanged():
    request = _picking_kit()
    assert request.status is FulfillmentStatus.UNDERFILLED

    kept, open_lines = request.split_picked()

    assert kept.status is FulfillmentStatus.COMPLETE and kept.fully_reserved
    assert kept.version == request.version + 1
    assert [(line.name, line.requirement) for line in kept.lines] == [
        ("Разъём", Decimal("2")), ("Клемма", Decimal("4")), ("Провод", Decimal("6")),
    ]
    assert [(line.name, line.requirement) for line in open_lines] == [
        ("Провод", Decimal("4")), ("Втулка", Decimal("3")), ("Гильза", Decimal("1")),
    ]
    # The open lines are the request's own objects: ids, marks and needs untouched.
    assert all(line in request.lines for line in open_lines)
    assert _line(request, "Втулка").absent and open_lines[1].absent_by == "Кладовщик"


def test_a_lot_holding_less_than_the_need_is_open_and_moves_with_its_reserve():
    request = _request(("Разъём", "2"), ("Провод", "10"))
    request = request.bind_line(_line(request, "Разъём").id, _snapshot(), Decimal("2"))
    partial = replace(_line(request, "Провод"), lot=_snapshot("FRX.26.0000003"), reserved=Decimal("4"))
    request = replace(request, lines=(request.lines[0], partial))

    kept, open_lines = request.split_picked()
    extra = FulfillmentRequest.remainder_of(
        request, open_lines, number="REQ.26.0000101", source_idempotency_key="k2", source_fingerprint="f2",
        created_at=NOW,
    )

    assert [line.name for line in kept.lines] == ["Разъём"]
    assert extra.lines == (partial,)
    assert extra.lines[0].reserved == Decimal("4") and extra.lines[0].lot.grn == "FRX.26.0000003"


def test_the_remainder_is_an_extra_of_the_same_kb_that_keeps_the_absent_mark():
    request = _picking_kit()
    _, open_lines = request.split_picked()

    extra = FulfillmentRequest.remainder_of(
        request, open_lines, number="REQ.26.0000101", source_idempotency_key="k2", source_fingerprint="f2",
        created_at=NOW,
    )

    assert extra.kind is FulfillmentKind.EXTRA
    assert extra.status is FulfillmentStatus.UNDERFILLED
    assert extra.purpose == f"{PROJECT} · КБ7 · Жгут · Довыдача: недобор REQ.26.0000100"
    assert (extra.requester, extra.deadline, extra.version) == ("Мастер", date(2026, 11, 20), 1)
    assert [line.id for line in extra.lines] == [line.id for line in open_lines]
    assert _line(extra, "Втулка").absent
    assert "Втулка (нет на складе): −3" in extra.shortfall


def test_the_remainder_refuses_covered_or_foreign_lines():
    request = _picking_kit()
    kept, open_lines = request.split_picked()
    stranger = FulfillmentLine.create(article="", name="Чужая", unit="шт", requirement=Decimal("1"))

    for lines, message in (
        (kept.lines[:1], "В довыдачу переносят только ненабранные позиции"),
        ((stranger,), "Позиция заявки не найдена"),
        ((), "В довыдачу нечего переносить"),
    ):
        with pytest.raises(FulfillmentValidationError, match=message):
            FulfillmentRequest.remainder_of(
                request, lines, number="REQ.26.0000101", source_idempotency_key="k", source_fingerprint="f",
                created_at=NOW,
            )


def test_a_new_kit_with_a_lot_bound_at_creation_can_issue_what_it_has():
    request = _request(("Разъём", "2"), ("Гильза", "1"))
    request = request.bind_line(_line(request, "Разъём").id, _snapshot(), Decimal("2"), touch=False)
    assert request.status is FulfillmentStatus.NEW

    kept, open_lines = request.split_picked()

    assert [line.name for line in kept.lines] == ["Разъём"]
    assert [line.name for line in open_lines] == ["Гильза"]


@pytest.mark.parametrize("status", [
    FulfillmentStatus.COMPLETE, FulfillmentStatus.READY, FulfillmentStatus.ISSUED, FulfillmentStatus.CANCELLED,
    FulfillmentStatus.DONE, FulfillmentStatus.RETURNED,
])
def test_other_statuses_are_refused(status):
    request = replace(_picking_kit(), status=status)
    with pytest.raises(FulfillmentValidationError, match=f"а заявка REQ.26.0000100 — «{status.value}»"):
        request.split_picked()


def test_nothing_picked_nothing_waiting_or_an_extra_is_refused():
    nothing = _request(("Разъём", "2"), ("Гильза", "1"))
    whole = _request(("Разъём", "2"))
    whole = whole.bind_line(whole.lines[0].id, _snapshot(), Decimal("2"))
    extra = replace(_picking_kit(), kind=FulfillmentKind.EXTRA)

    with pytest.raises(FulfillmentValidationError, match="Нечего выдавать: ни одна позиция не набрана"):
        nothing.split_picked()
    with pytest.raises(FulfillmentValidationError, match="Комплект набран целиком"):
        replace(whole, status=FulfillmentStatus.UNDERFILLED).split_picked()
    with pytest.raises(FulfillmentValidationError, match="только для основной заявки КБ"):
        extra.split_picked()


# --- application / API ----------------------------------------------------------------------------------------------


def _principal(permissions: dict[str, str]) -> Principal:
    return Principal(
        actor=Actor(name="Кладовщик", department="Склад"),
        email="receiver@example.test",
        roles=frozenset({"receiver"}),
        permissions=permissions,
    )


@pytest.fixture()
def shop():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"fulfillment": "edit", "handout": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    lots = {
        name: StockLot.create(
            warehouse="ЦАР", grn=grn, package="", name=name, item_type="Комплектующие", purpose="Склад", unit="шт",
            quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receiving-{grn}",),
        )
        for name, grn, quantity in (("Разъём", "FRX.26.0000001", "10"), ("Провод", "FRX.26.0000002", "6"))
    }
    with factory() as uow:
        for lot in lots.values():
            uow.warehouse.add(lot)
        uow.commit()
    yield TestClient(app), lots, factory
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.fulfillment_uow_factory
    engine.dispose()


def _post(client, url: str, request: dict, **extra) -> dict:
    response = client.post(url, json={"version": request["version"], **extra})
    assert response.status_code == 200, response.text
    return response.json()["request"]


def _kit(client, lots) -> dict:
    """КБ7: Разъём 2 bound, Клемма 4 «есть, но не заведено», Провод 10 bound to a lot of 6 (4 wait), Втулка 3 «нет на
    складе», Гильза 1 waits."""
    lines = [{"article": f"КД-{name}", "name": name, "unit": "шт", "requirement": qty}
             for name, qty in (("Разъём", "2"), ("Клемма", "4"), ("Провод", "10"), ("Втулка", "3"), ("Гильза", "1"))]
    created = client.post("/api/issues", headers={"Idempotency-Key": str(uuid4())},
                          json={"purpose": f"{PROJECT} · КБ7 · Жгут", "deadline": "2026-11-20", "lines": lines})
    assert created.status_code == 200, created.text
    request = created.json()["request"]
    ids = {line["name"]: line["id"] for line in request["lines"]}
    request = _post(client, f"/api/issues/{request['id']}/lines/{ids['Разъём']}/bind", request,
                    lot_id=str(lots["Разъём"].id))
    request = _post(client, f"/api/issues/{request['id']}/lines/{ids['Клемма']}/off-book", request)
    request = _post(client, f"/api/issues/{request['id']}/lines/{ids['Провод']}/bind", request,
                    lot_id=str(lots["Провод"].id))
    return _post(client, f"/api/issues/{request['id']}/lines/{ids['Втулка']}/absent", request)


def _stock(factory, lots):
    with factory() as uow:
        stock = {name: (uow.warehouse.get(lot.id).reserved, uow.warehouse.get(lot.id).written_off)
                 for name, lot in lots.items()}
        writeoffs = [(row.name, row.quantity) for row in uow.session.scalars(select(WriteOffEntryRow))]
        return stock, sorted(writeoffs)


def _events(factory, event_type: str) -> list[tuple[UUID, dict]]:
    with factory() as uow:
        rows = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type))
        return [(row.aggregate_id, row.details) for row in rows]


def test_the_picked_lines_are_issued_and_the_rest_moves_to_an_extra_request(shop):
    client, lots, factory = shop
    kit = _kit(client, lots)
    assert kit["status"] == "Недоукомплектован"
    open_ids = [line["id"] for line in kit["lines"] if line["name"] in {"Втулка", "Гильза"} or (
        line["name"] == "Провод" and line["lot"] is None)]

    response = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]})

    assert response.status_code == 200, response.text
    body = response.json()
    issued, extra = body["request"], body["extra"]
    assert issued["status"] == "КБ Выдана" and issued["kind"] == "main"
    assert [(line["name"], line["issued"], line["off_book"]) for line in issued["lines"]] == [
        ("Разъём", "2", False), ("Клемма", "4", True), ("Провод", "6", False),
    ]
    assert extra["kind"] == "extra" and extra["status"] == "Недоукомплектован"
    assert extra["purpose"] == f"{PROJECT} · КБ7 · Жгут · Довыдача: недобор {kit['number']}"
    assert [(line["name"], line["requirement"], line["reserved"], line["lot"]) for line in extra["lines"]] == [
        ("Провод", "4", "0", None), ("Втулка", "3", "0", None), ("Гильза", "1", "0", None),
    ]
    # The moved lines keep their ids and the «нет на складе» mark.
    assert [line["id"] for line in extra["lines"]] == open_ids
    assert extra["lines"][1]["absent_by"] == "Кладовщик" and extra["lines"][1]["absent_at"]
    assert body["message"] == f"Выдано позиций: 3 (КБ7 ({kit['number']})). Остальное (3 поз.) — в довыдаче {extra['number']}."
    # Stock: only the issued lines are written off — reserve → списано, one СПИСАНИЕ row per lot line.
    stock, writeoffs = _stock(factory, lots)
    assert stock == {"Разъём": (Decimal("0"), Decimal("2")), "Провод": (Decimal("0"), Decimal("6"))}
    assert writeoffs == [("Провод", Decimal("6")), ("Разъём", Decimal("2"))]
    # Both requests are listed; the КБ has one live main kit (now issued) and its extra.
    listed = {request["number"]: request for request in client.get("/api/issues").json()["requests"]}
    assert listed[kit["number"]]["status"] == "КБ Выдана" and listed[extra["number"]]["kind"] == "extra"
    assert len(listed[kit["number"]]["lines"]) == 3 and len(listed[extra["number"]]["lines"]) == 3


def test_audit_names_the_issued_lines_and_the_extra_request(shop):
    client, lots, factory = shop
    kit = _kit(client, lots)

    body = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]}).json()

    extra = body["extra"]
    [(aggregate, partial)] = _events(factory, "fulfillment.issued_partial")
    assert aggregate == UUID(kit["id"])
    assert partial["extra_number"] == extra["number"] and partial["extra_id"] == extra["id"]
    assert [(item["name"], item["quantity"], item["grn"]) for item in partial["issued"]] == [
        ("Разъём", "2", "FRX.26.0000001"), ("Клемма", "4", None), ("Провод", "6", "FRX.26.0000002"),
    ]
    assert [(item["name"], item["quantity"]) for item in partial["moved"]] == [
        ("Провод", "4"), ("Втулка", "3"), ("Гильза", "1"),
    ]
    assert partial["moved"][1]["absent"] is True
    created = [details for aggregate, details in _events(factory, "fulfillment.created") if aggregate == UUID(extra["id"])]
    assert len(created) == 1 and created[0]["from_request"] == kit["number"]
    # The normal issue path ran for the main kit: sent to the handout place without a message, then issued.
    assert [aggregate for aggregate, _ in _events(factory, "fulfillment.issued")] == [UUID(kit["id"])]
    [(_, ready)] = _events(factory, "fulfillment.ready")
    assert ready["handout_location"] == "Зона выдачи" and ready["with_issue"] is True


def test_a_partial_reserve_moves_with_its_line_and_its_lot_is_not_touched(shop):
    client, lots, factory = shop
    created = client.post("/api/issues", headers={"Idempotency-Key": "partial"}, json={
        "purpose": f"{PROJECT} · КБ10 · Жгут", "deadline": "2026-11-20",
        "lines": [{"name": "Разъём", "unit": "шт", "requirement": "2"}, {"name": "Провод", "unit": "шт", "requirement": "10"}],
    }).json()["request"]
    kit = _post(client, f"/api/issues/{created['id']}/lines/{created['lines'][0]['id']}/bind", created,
                lot_id=str(lots["Разъём"].id))
    with factory() as uow:  # an old line holding 4 of 10 on the lot (before binds split a short lot)
        request = uow.fulfillment.get(UUID(kit["id"]))
        lot = uow.warehouse.get(lots["Провод"].id)
        snapshot = LotSnapshot(lot_id=lot.id, warehouse=lot.warehouse, grn=lot.grn, package="", storage_location="1F4")
        wire = replace(request.lines[1], lot=snapshot, reserved=Decimal("4"))
        uow.fulfillment.add(replace(request, lines=(request.lines[0], wire), version=request.version + 1))
        uow.warehouse.add(lot.reserve(Decimal("4")))
        uow.commit()

    response = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"] + 1})

    assert response.status_code == 200, response.text
    [line] = response.json()["extra"]["lines"]
    assert (line["id"], line["requirement"], line["reserved"], line["lot"]["grn"]) == (
        str(wire.id), "10", "4", "FRX.26.0000002",
    )
    stock, writeoffs = _stock(factory, lots)
    assert stock == {"Разъём": (Decimal("0"), Decimal("2")), "Провод": (Decimal("4"), Decimal("0"))}
    assert writeoffs == [("Разъём", Decimal("2"))]


def test_a_stale_version_is_refused_and_nothing_moves(shop):
    client, lots, factory = shop
    kit = _kit(client, lots)
    before = _stock(factory, lots)

    stale = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"] - 1})
    first = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]})
    again = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]})

    assert stale.status_code == 409
    assert first.status_code == 200
    assert again.status_code == 409  # a double click: the kit moved on, nothing is written off twice
    assert _stock(factory, lots)[1] == [("Провод", Decimal("6")), ("Разъём", Decimal("2"))]
    assert before[1] == []
    numbers = [request["number"] for request in client.get("/api/issues").json()["requests"]]
    assert len(numbers) == 2


def test_refusals_leave_the_kit_and_the_stock_as_they_were(shop):
    client, lots, factory = shop
    created = client.post("/api/issues", headers={"Idempotency-Key": "whole"}, json={
        "purpose": f"{PROJECT} · КБ11 · Жгут", "deadline": "2026-11-20",
        "lines": [{"name": "Разъём", "unit": "шт", "requirement": "1"}, {"name": "Гильза", "unit": "шт", "requirement": "1"}],
    }).json()["request"]

    nothing = client.post(f"/api/issues/{created['id']}/issue-picked", json={"version": created["version"]})
    kit = _post(client, f"/api/issues/{created['id']}/lines/{created['lines'][0]['id']}/bind", created,
                lot_id=str(lots["Разъём"].id))
    kit = _post(client, f"/api/issues/{kit['id']}/lines/{kit['lines'][1]['id']}/off-book", kit)
    whole = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]})

    assert nothing.status_code == 422 and nothing.json()["detail"].startswith("Нечего выдавать")
    assert kit["status"] == "Укомплектован"
    assert whole.status_code == 422 and whole.json()["detail"].startswith("«Выдать набранное» — у комплекта")
    assert _stock(factory, lots)[1] == []
    assert len(client.get("/api/issues").json()["requests"]) == 1


def test_an_extra_request_is_not_issued_in_part(shop):
    client, lots, _ = shop
    kit = _kit(client, lots)
    extra = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]}).json()["extra"]

    refused = client.post(f"/api/issues/{extra['id']}/issue-picked", json={"version": extra["version"]})

    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("«Выдать набранное» — только для основной заявки КБ")


def test_issue_picked_takes_the_handout_right(shop):
    client, lots, factory = shop
    kit = _kit(client, lots)
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"fulfillment": "edit"})

    refused = client.post(f"/api/issues/{kit['id']}/issue-picked", json={"version": kit["version"]})

    assert refused.status_code == 403
    assert _stock(factory, lots)[1] == []


def test_add_refuses_a_line_of_another_request_and_move_lines_moves_all_or_nothing(shop):
    client, lots, factory = shop
    kit = _kit(client, lots)
    other = client.post("/api/issues", headers={"Idempotency-Key": "other"}, json={
        "purpose": f"{PROJECT} · КБ13 · Жгут", "deadline": "2026-11-20",
        "lines": [{"name": "Гильза", "unit": "шт", "requirement": "1"}],
    }).json()["request"]
    waiting = next(line for line in kit["lines"] if line["name"] == "Гильза")
    with factory() as uow:
        target = uow.fulfillment.get(UUID(other["id"]))
        stolen = next(line for line in uow.fulfillment.get(UUID(kit["id"])).lines if line.name == "Гильза")
        with pytest.raises(FulfillmentValidationError, match="принадлежит другой заявке"):
            uow.fulfillment.add(replace(target, lines=(*target.lines, stolen), version=target.version + 1))
    with factory() as uow:
        with pytest.raises(ConcurrentFulfillmentUpdate):  # one id is not on the source request: nothing moves
            uow.fulfillment.move_lines([UUID(waiting["id"]), UUID(other["lines"][0]["id"])], UUID(kit["id"]), UUID(other["id"]))
    with factory() as uow:
        uow.fulfillment.move_lines([UUID(waiting["id"])], UUID(kit["id"]), UUID(other["id"]))
        uow.commit()
    with factory() as uow:
        assert [line.name for line in uow.fulfillment.get(UUID(other["id"])).lines] == ["Гильза", "Гильза"]
        assert "Гильза" not in [line.name for line in uow.fulfillment.get(UUID(kit["id"])).lines]


def test_the_list_offers_the_handout_place_for_a_kit_issue_picked_takes(shop):
    client, lots, _ = shop
    kit = _kit(client, lots)

    listed = {request["id"]: request for request in client.get("/api/issues").json()["requests"]}

    assert listed[kit["id"]]["handout_place"] == "Зона выдачи"