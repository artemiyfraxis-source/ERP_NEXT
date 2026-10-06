"""«Выдать комплект» per КБ and «Выдать выбранные» for the whole order (owner 06.10.2026): one transaction, all or nothing,
a «Укомплектован» kit sent to the handout point on the way, never written off twice."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import WriteOffEntryRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from tests.factories import Actor

PROJECT = "33МФ.2"


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
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 6, 9, 57)
    lots = {
        name: StockLot.create(
            warehouse="ЦАР", grn=grn, package="", name=name, item_type="Комплектующие", purpose="Склад", unit="шт",
            quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receiving-{grn}",),
        )
        for name, grn, quantity in (("Разъём", "FRX.26.0000001", "10"), ("Клемма", "FRX.26.0000002", "20"))
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


def _kit(client, lots, kb: str, picks: dict[str, str], waiting: int = 0) -> dict:
    """A request of the project's КБ with `picks` bound ({lot name: quantity}) and `waiting` lines without a lot."""
    lines = [{"article": f"КД-{name}", "name": name, "unit": "шт", "requirement": qty} for name, qty in picks.items()]
    lines += [{"article": f"КД-ждём-{index}", "name": f"Ждём {index}", "unit": "шт", "requirement": "1"} for index in range(waiting)]
    created = client.post(
        "/api/issues", headers={"Idempotency-Key": str(uuid4())},
        json={"purpose": f"{PROJECT} · {kb} · Жгут", "deadline": "2026-10-09", "lines": lines},
    )
    assert created.status_code == 200, created.text
    request = created.json()["request"]
    for line in list(request["lines"]):
        if line["name"] not in picks:
            continue
        bound = client.post(
            f"/api/issues/{request['id']}/lines/{line['id']}/bind",
            json={"version": request["version"], "lot_id": str(lots[line["name"]].id)},
        )
        assert bound.status_code == 200, bound.text
        request = bound.json()["request"]
    return request


def _ready(client, request: dict) -> dict:
    sent = client.post(f"/api/issues/{request['id']}/ready", json={"version": request["version"], "handout_location": "1B3"})
    assert sent.status_code == 200, sent.text
    return sent.json()["request"]


def _batch(client, *requests: dict):
    return client.post("/api/issues/issue-batch", json={
        "requests": [{"id": request["id"], "version": request["version"]} for request in requests],
    })


def _state(factory, lots):
    with factory() as uow:
        stock = {name: (uow.warehouse.get(lot.id).reserved, uow.warehouse.get(lot.id).written_off) for name, lot in lots.items()}
        writeoffs = uow.session.scalars(select(WriteOffEntryRow).order_by(WriteOffEntryRow.occurred_at)).all()
        return stock, [(row.reason, row.name, row.quantity, row.remaining_after) for row in writeoffs]


def _statuses(client) -> dict[str, str]:
    return {request["purpose"].split(" · ")[1]: request["status"] for request in client.get("/api/issues").json()["requests"]}


def test_the_whole_order_is_issued_at_once_including_kits_not_sent_to_the_handout_point(shop):
    client, lots, factory = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "2", "Клемма": "3"})
    kb2 = _kit(client, lots, "КБ2", {"Разъём": "4"})
    kb3 = _ready(client, _kit(client, lots, "КБ3", {"Клемма": "5"}))
    assert (kb1["status"], kb2["status"], kb3["status"]) == ("Укомплектован", "Укомплектован", "Готов к выдаче")

    issued = _batch(client, kb3, kb2, kb1)

    assert issued.status_code == 200, issued.text
    body = issued.json()
    # КБ in natural order, whatever order the screen sent them in.
    assert [request["purpose"].split(" · ")[1] for request in body["requests"]] == ["КБ1", "КБ2", "КБ3"]
    assert {request["status"] for request in body["requests"]} == {"КБ Выдана"}
    assert body["message"].startswith("Выдано: КБ1 (")
    assert "Списано со склада позиций: 4." in body["message"]
    by_kb = {request["purpose"].split(" · ")[1]: request for request in body["requests"]}
    # The kit that skipped «Отправить на выдачу» still gets its «Отправлена на выдачу» stamp and the offered place.
    assert by_kb["КБ1"]["at_picked"] == "2026-10-06T09:57:00"
    assert by_kb["КБ1"]["at_issued"] == "2026-10-06T09:57:00"
    assert by_kb["КБ1"]["version"] == kb1["version"] + 2
    assert by_kb["КБ3"]["version"] == kb3["version"] + 1
    stock, writeoffs = _state(factory, lots)
    assert stock == {"Разъём": (Decimal("0"), Decimal("6")), "Клемма": (Decimal("0"), Decimal("8"))}
    # One СПИСАНИЕ row per position; ОСТАТОК ПОСЛЕ follows the kits one after another on the shared lot.
    razem = sorted((row[2], row[3]) for row in writeoffs if row[1] == "Разъём")
    assert razem == [(Decimal("2"), Decimal("8")), (Decimal("4"), Decimal("4"))]
    assert len(writeoffs) == 4
    with factory() as uow:
        on_issue = [event.details for event in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "fulfillment.ready")
        ) if event.aggregate_id == UUID(kb1["id"])]
        keys = [message.idempotency_key for message in uow.session.scalars(select(OutboxMessageRow))]
    # The place «Отправить на выдачу» would offer: the last one used at the kit's site (КБ3 went to 1B3 just before).
    assert len(on_issue) == 1 and on_issue[0]["handout_location"] == "1B3"
    assert on_issue[0]["with_issue"] is True
    # «готова к получению» went out only for КБ3, sent to the shelf by hand; КБ1/КБ2 were handed over on the spot.
    ready_keys = [key for key in keys if ":ready:" in key]
    assert ready_keys == [f"notify:fulfillment:{kb3['id']}:ready:v{kb3['version']}"]


def test_one_kit_not_picked_refuses_the_batch_and_nothing_is_issued(shop):
    client, lots, factory = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "2"})
    kb2 = _ready(client, _kit(client, lots, "КБ2", {"Клемма": "3"}))
    kb4 = _kit(client, lots, "КБ4", {"Разъём": "1"}, waiting=2)
    before = _state(factory, lots)

    refused = _batch(client, kb1, kb2, kb4)

    assert refused.status_code == 422
    assert refused.json()["detail"] == f"КБ4 ({kb4['number']}): комплект не собран, набрано 1 из 3 позиций. Ничего не выдано."
    assert _statuses(client) == {"КБ1": "Укомплектован", "КБ2": "Готов к выдаче", "КБ4": "Недоукомплектован"}
    assert _state(factory, lots) == before
    assert before[1] == []


def test_a_stock_failure_on_a_later_kit_rolls_back_the_kits_already_posted(shop):
    client, lots, factory = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "2"})
    kb2 = _kit(client, lots, "КБ2", {"Клемма": "3"})
    with factory() as uow:  # the shelf lost «Клемма» behind the ERP's back: balance 1 under a reserve of 3
        lot = uow.warehouse.get(lots["Клемма"].id)
        uow.warehouse.add(replace(lot, written_off=Decimal("19"), version=lot.version + 1))
        uow.commit()
    before = _state(factory, lots)

    refused = _batch(client, kb1, kb2)

    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        f"КБ2 ({kb2['number']}): Недостаточно физического остатка для выдачи. Ничего не выдано."
    )
    # КБ1 went first and was posted inside the transaction — it is rolled back with the rest.
    assert _statuses(client) == {"КБ1": "Укомплектован", "КБ2": "Укомплектован"}
    assert _state(factory, lots) == before


def test_a_repeated_call_never_writes_off_twice(shop):
    client, lots, factory = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "2"})
    kb2 = _kit(client, lots, "КБ2", {"Клемма": "3"})
    first = _batch(client, kb1, kb2)
    assert first.status_code == 200, first.text
    after_first = _state(factory, lots)

    # A double click sends the same versions again: the kits moved on, the call is refused, the stock stays.
    again = _batch(client, kb1, kb2)
    assert again.status_code == 409
    assert again.json()["detail"].startswith("КБ1 (")
    assert "Ничего не выдано." in again.json()["detail"]
    # The screen reloaded meanwhile and shows «КБ Выдана»: a call with the fresh versions is refused by the status.
    fresh = {request["id"]: request for request in first.json()["requests"]}
    refused = _batch(client, fresh[kb1["id"]], fresh[kb2["id"]])
    assert refused.status_code == 422
    assert refused.json()["detail"] == f"КБ1 ({kb1['number']}): уже выдана. Ничего не выдано."
    assert _state(factory, lots) == after_first
    assert len(after_first[1]) == 2


def test_a_single_complete_kit_is_issued_in_one_click(shop):
    client, lots, factory = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "3", "Клемма": "1"})
    assert kb1["status"] == "Укомплектован"

    issued = _batch(client, kb1)

    assert issued.status_code == 200, issued.text
    request = issued.json()["requests"][0]
    assert request["status"] == "КБ Выдана"
    assert request["handout_location"] is None
    assert [line["issued"] for line in request["lines"]] == ["3", "1"]
    stock, writeoffs = _state(factory, lots)
    assert stock["Разъём"] == (Decimal("0"), Decimal("3"))
    assert len(writeoffs) == 2
    with factory() as uow:
        places = [event.details["handout_location"] for event in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "fulfillment.ready")
        )]
    assert places == ["Зона выдачи"]  # no place used at the site yet: the default, as «Отправить на выдачу» offers


def test_a_new_kit_with_every_lot_is_not_issued_before_it_is_marked_complete(shop):
    client, lots, _ = shop
    created = client.post("/api/issues", headers={"Idempotency-Key": "manual-with-lot"}, json={
        "purpose": f"{PROJECT} · КБ5 · Жгут", "deadline": "2026-10-09",
        "lines": [{"name": "Разъём", "unit": "шт", "requirement": "1", "lot_id": str(lots["Разъём"].id)}],
    }).json()["request"]
    assert created["status"] == "Новая"

    refused = _batch(client, created)

    assert refused.status_code == 422
    assert refused.json()["detail"] == f"КБ5 ({created['number']}): комплект не отмечен «Укомплектован». Ничего не выдано."


def test_issue_takes_the_handout_right_not_the_picking_one(shop):
    client, lots, factory = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "2"})
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"fulfillment": "edit"})

    refused = _batch(client, kb1)

    assert refused.status_code == 403
    assert refused.json()["detail"] == "Раздел «Выдача со списанием» вам не открыт на правку."
    assert _state(factory, lots)[1] == []


def test_the_same_request_twice_or_a_stale_version_is_refused(shop):
    client, lots, _ = shop
    kb1 = _kit(client, lots, "КБ1", {"Разъём": "2"})

    twice = client.post("/api/issues/issue-batch", json={"requests": [
        {"id": kb1["id"], "version": kb1["version"]}, {"id": kb1["id"], "version": kb1["version"]},
    ]})
    stale = _batch(client, {**kb1, "version": kb1["version"] - 1})
    empty = client.post("/api/issues/issue-batch", json={"requests": []})

    assert twice.status_code == 422 and twice.json()["detail"] == "Заявка указана в выдаче дважды"
    assert stale.status_code == 409
    assert empty.status_code == 422
    assert _statuses(client) == {"КБ1": "Укомплектован"}
