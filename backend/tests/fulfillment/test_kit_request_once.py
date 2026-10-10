"""Одна живая основная заявка на КБ проекта, отказ привязки при задвоенной КБ, номера без повторов, «Вернуть в
комплектацию» и «Довыдача» (владелец, 10.10.2026; прод 180К.11 и 719К.2)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.application import (
    BindArrivedLotCommand,
    DeleteFulfillmentCommand,
    bind_arrived_lot,
    delete_fulfillment,
)
from app.modules.fulfillment.domain import DuplicateKitRequest, FulfillmentLine, FulfillmentRequest, FulfillmentStatus
from app.modules.fulfillment.infrastructure import FulfillmentRequestCounterRow, SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 10, 12, 0)
ACTOR = Actor(name="Кладовщик", department="Склад")


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
        actor=ACTOR, email="receiver@example.test", roles=frozenset({"receiver"}),
        permissions={"fulfillment": "edit", "handout": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), factory, engine
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.fulfillment_uow_factory
    engine.dispose()


def stock(grn: str, quantity: str) -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name="Разъём", item_type="Комплектующие", purpose="Склад",
        unit="шт", quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receiving-{grn}",),
    )


def create(client, purpose: str, need: str = "4"):
    return client.post(
        "/api/issues",
        headers={"Idempotency-Key": f"test-{uuid4()}"},
        json={"purpose": purpose, "deadline": "2026-10-20",
              "lines": [{"article": "КД-1", "name": "Разъём", "unit": "шт", "requirement": need}]},
    )


def created(client, purpose: str, need: str = "4") -> dict:
    response = create(client, purpose, need)
    assert response.status_code == 200, response.text
    return response.json()["request"]


def fetch(client, request_id: str) -> dict:
    return next(item for item in client.get("/api/issues").json()["requests"] if item["id"] == request_id)


def put_lot(factory, lot: StockLot) -> None:
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()


def set_status(factory, request_id: str, status: FulfillmentStatus, **changes) -> None:
    with factory() as uow:
        request = uow.fulfillment.get(UUID(request_id))
        uow.fulfillment.add(replace(request, status=status, **changes))
        uow.commit()


def doubled(client, engine, purpose: str) -> dict:
    """The state of prod before migration 0089 (REQ.26.0000018 / REQ.26.0000029 of 180К.11): a second request for the
    same КБ, made under another КБ and moved over in the database with the index out of the way."""
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_fulfillment_requests_live_kit"))
    request = created(client, purpose.replace("КБ1", "КБ99"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE fulfillment_requests SET purpose = :purpose, item_index = 'КБ1' WHERE number = :number"),
                           {"purpose": purpose, "number": request["number"]})
    return fetch(client, request["id"])


# ── 1–2: one live main request per project + КБ ──────────────────────────────


def test_a_second_request_for_the_same_kb_is_refused_with_its_number(api):
    client, _factory, _engine = api
    first = created(client, "180К.11 · КБ1 · Жгут")

    again = create(client, "180K.11 · кб-1 · Жгут питания")  # Latin K, another spelling of the КБ

    assert again.status_code == 409
    assert again.json()["detail"] == f"Заявка на КБ1 проекта 180K.11 уже есть: {first['number']} (Новая)"
    assert created(client, "180К.11 · КБ2 · Жгут")["number"] == "REQ.26.0000002"
    created(client, "Ремонт стенда")  # a manual request without a project — any number of them
    created(client, "Ремонт стенда")


def test_a_cancelled_kb_can_be_sent_again(api):
    client, _factory, _engine = api
    first = created(client, "180К.11 · КБ1 · Жгут")
    assert client.post(f"/api/issues/{first['id']}/cancel", json={"version": first["version"]}).status_code == 200

    again = created(client, "180К.11 · КБ1 · Жгут")

    assert again["number"] == "REQ.26.0000002"


def test_the_database_refuses_a_second_live_main_request_whatever_the_code_does(api):
    _client, factory, _engine = api
    line = lambda: FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal("1"))
    make = lambda number: FulfillmentRequest.create(
        number=number, source_idempotency_key=number, source_fingerprint="f", requester="Мастер",
        purpose="180К.11 · КБ1 · Жгут", deadline=date(2026, 10, 20), lines=(line(),), created_at=NOW,
    )
    with factory() as uow:
        uow.fulfillment.add(make("REQ.26.0000001"))
        uow.commit()
    with pytest.raises(IntegrityError), factory() as uow:
        uow.fulfillment.add(make("REQ.26.0000002"))


def test_the_kit_columns_are_filled_from_the_purpose(api):
    client, factory, engine = api
    created(client, "728K · КБ 3 · Жгут")
    created(client, "Ремонт стенда")
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT purpose, project_key, item_index, kind FROM fulfillment_requests ORDER BY number"
        )).fetchall()
    assert [tuple(row) for row in rows] == [
        ("728K · КБ 3 · Жгут", "728К.1", "КБ3", "main"), ("Ремонт стенда", None, None, "main"),
    ]


# ── 3: a doubled КБ refuses the binding, nothing written ─────────────────────


def test_bind_lots_refuses_when_two_live_requests_target_the_same_kb(api):
    client, factory, engine = api
    lot = stock("FRX.26.0000001", "10")
    put_lot(factory, lot)
    first = created(client, "180К.11 · КБ1 · Жгут")
    second = doubled(client, engine, "180К.11 · КБ1 · Жгут")
    kb2 = created(client, "180К.11 · КБ2 · Жгут")
    versions = {item["id"]: item["version"] for item in (first, second, kb2)}

    response = client.post("/api/issues/projects/180К.11/bind-lots", json={
        "article": "КД-1", "name": "Разъём", "versions": versions, "lot_ids": [str(lot.id)],
    })

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"у КБ1 две заявки {first['number']}/{second['number']} — отмените лишнюю, ничего не привязано"
    )
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == 0
    assert all(fetch(client, item["id"])["version"] == item["version"] for item in (first, second, kb2))

    # The storekeeper cancels the extra one — then the binding goes through, the reserve once per КБ.
    client.post(f"/api/issues/{second['id']}/cancel", json={"version": second["version"]})
    versions.pop(second["id"])
    bound = client.post("/api/issues/projects/180К.11/bind-lots", json={
        "article": "КД-1", "name": "Разъём", "versions": versions, "lot_ids": [str(lot.id)],
    })
    assert bound.status_code == 200, bound.text
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == Decimal("8")


def test_an_arrival_refuses_to_bind_to_a_doubled_kb(api):
    client, factory, engine = api
    lot = stock("FRX.26.0000002", "10")
    put_lot(factory, lot)
    created(client, "180К.11 · КБ1 · Жгут")
    doubled(client, engine, "180К.11 · КБ1 · Жгут")

    with pytest.raises(DuplicateKitRequest, match="у КБ1 две заявки"), factory() as uow:
        bind_arrived_lot(BindArrivedLotCommand(
            project="180К.11", lot_id=lot.id, quantity=Decimal("10"), articles=("КД-1",), names=(),
            actor=ACTOR, now=NOW,
        ), uow)
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == 0


# ── 4: numbers are never given out twice ─────────────────────────────────────


def test_a_deleted_requests_number_is_not_given_out_again(api):
    client, factory, _engine = api
    created(client, "Ремонт 1")
    last = created(client, "Ремонт 2")
    delete_fulfillment(DeleteFulfillmentCommand(request_id=UUID(last["id"]), actor=ACTOR, now=NOW), factory())

    after = created(client, "Ремонт 3")

    assert last["number"] == "REQ.26.0000002"
    assert after["number"] == "REQ.26.0000003"
    with factory() as uow:
        assert uow.session.get(FulfillmentRequestCounterRow, 2026).last_number == 3


# ── 5: «Вернуть в комплектацию» ──────────────────────────────────────────────


def test_a_kit_at_the_handout_point_goes_back_to_kitting_and_only_then_can_be_cancelled(api):
    client, factory, _engine = api
    lot = stock("FRX.26.0000003", "10")
    put_lot(factory, lot)
    kit = created(client, "719К.2 · КБ1 · Жгут")
    line = kit["lines"][0]
    kit = client.post(f"/api/issues/{kit['id']}/lines/{line['id']}/bind", json={
        "version": kit["version"], "lot_id": str(lot.id),
    }).json()["request"]
    assert kit["status"] == "Укомплектован"
    kit = client.post(f"/api/issues/{kit['id']}/ready", json={
        "version": kit["version"], "handout_location": "5F1A",
    }).json()["request"]
    assert kit["status"] == "Готов к выдаче"

    refused = client.post(f"/api/issues/{kit['id']}/cancel", json={"version": kit["version"]})
    assert refused.status_code == 422
    assert "Вернуть в комплектацию" in refused.json()["detail"]
    stale = client.post(f"/api/issues/{kit['id']}/return-to-kitting", json={"version": kit["version"] - 1})
    assert stale.status_code == 409

    back = client.post(f"/api/issues/{kit['id']}/return-to-kitting", json={"version": kit["version"]})

    assert back.status_code == 200, back.text
    body = back.json()["request"]
    assert (body["status"], body["handout_location"], body["at_picked"]) == ("Укомплектован", None, None)
    assert body["version"] == kit["version"] + 1
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == Decimal("4")  # the reserve stays: no goods moved
        events = [row.event_type for row in uow.session.scalars(select(AuditEventRow))]
    assert "fulfillment.returned_to_kitting" in events
    again = client.post(f"/api/issues/{kit['id']}/return-to-kitting", json={"version": body["version"]})
    assert again.status_code == 422  # only from «Готов к выдаче»
    cancelled = client.post(f"/api/issues/{kit['id']}/cancel", json={"version": body["version"]})
    assert cancelled.status_code == 200
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == 0


# ── C: «Довыдача» is outside the one-per-КБ rule ─────────────────────────────


def extra(client, request_id: str, quantity: str = "2", reason: str = "брак при опрессовке", **line):
    return client.post(
        f"/api/issues/{request_id}/extra",
        headers={"Idempotency-Key": f"extra-{uuid4()}"},
        json={"reason": reason, "lines": [{"article": "КД-1", "name": "Разъём", "unit": "шт", "quantity": quantity,
                                           **line}]},
    )


def test_an_issued_kit_gets_extra_requests_beside_its_main_one(api):
    client, factory, _engine = api
    kit = created(client, "180К.11 · КБ1 · Жгут")
    early = extra(client, kit["id"])
    assert early.status_code == 422 and "после выдачи" in early.json()["detail"]
    set_status(factory, kit["id"], FulfillmentStatus.ISSUED)

    first, second = extra(client, kit["id"]), extra(client, kit["id"], "1", "не хватило")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = first.json()["request"]
    assert body["kind"] == "extra"
    assert body["purpose"] == "180К.11 · КБ1 · Жгут · Довыдача: брак при опрессовке"
    assert [(line["name"], line["requirement"], line["lot"]) for line in body["lines"]] == [("Разъём", "2", None)]
    assert create(client, "180К.11 · КБ1 · Жгут").status_code == 409  # the main rule still holds
    assert extra(client, kit["id"], reason=" ").status_code == 422
    assert extra(client, kit["id"], name="Провод", article="КД-9").status_code == 422  # not in the kit
    assert extra(client, body["id"]).status_code == 422  # an extra is not a base for another extra
