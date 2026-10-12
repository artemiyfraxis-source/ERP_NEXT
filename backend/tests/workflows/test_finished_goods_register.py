"""«Из производства» и приёмка в регистр ГП (владелец 12.10.2026): сдано цехом по КБ, пакетная приёмка не больше
сданного (всё или ничего, ключ повтора), сквозной статус проекта по КБ и этапы лестницы по регистру."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.production.domain import ProductionOperation, ProductionStatus, ProductionTask, TaskDelivery
from app.modules.production.infrastructure import (
    SqlAlchemyDeliveryRepository,
    SqlAlchemyOutputRepository,
    SqlAlchemyProductionRepository,
)
from app.modules.production.traceability import ProductionOutput
from app.modules.shipping.domain import FinishedGood
from app.modules.shipping.infrastructure import FinishedGoodMoveRow, SqlAlchemyShippingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.workflows.finished_goods import shop_delivered
from app.workflows.production_plan import kit_stage_facts
from tests.factories import Actor

NOW = datetime(2026, 10, 12, 9, 0)
STOREKEEPER = Actor(id=UUID("10000000-0000-0000-0000-000000000007"), name="Кладовщик", department="Склад")


def _task(item: str, quantity: int, *, number: str, site: str = "", status=ProductionStatus.RUNNING) -> ProductionTask:
    operations = tuple(
        ProductionOperation.create(
            position=position, code=f"OP-0{position}", stage="Монтаж", description="", seconds_per_unit=Decimal("60"),
            setup_seconds=Decimal("0"), quantity=quantity,
        ) for position in (1, 2)
    )
    task = ProductionTask.create(
        number=number, source_idempotency_key=f"key:{number}", source_fingerprint="f", project="708К.4", item=item,
        name=f"Жгут {item}", quantity=quantity, fulfillment_request_id=None, deadline=None, launched_by="Мастер",
        operations=operations, created_at=NOW,
    )
    return replace(task, handed_to=site, status=status)


def _output(task: ProductionTask, position: int, good: int) -> ProductionOutput:
    return ProductionOutput(
        id=uuid4(), session_id=None, task_id=task.id, operation_id=task.operations[position - 1].id, operator_id=uuid4(),
        started_at=NOW, finished_at=NOW, pause_seconds=0, good_quantity=Decimal(good), defect_quantity=Decimal(0),
        output_box_id=uuid4(),
    )


def test_shop_delivered_counts_site_deliveries_final_outputs_and_done_tasks():
    shop = _task("КБ1", 50, number="ПЗ-1")
    site = _task("КБ2", 10, number="ПЗ-2", site="ЖУК")
    done = _task("КБ3", 20, number="ПЗ-3", status=ProductionStatus.DONE)
    gone = _task("КБ4", 5, number="ПЗ-4", status=ProductionStatus.CANCELLED)
    result = shop_delivered((shop, site, done, gone), {site.id: 7}, {shop.id: Decimal(30)})
    assert {key.split("|")[1]: (kb.run, kb.delivered) for key, kb in result.items()} == {
        "КБ1": (50, 30), "КБ2": (10, 7), "КБ3": (20, 20),
    }


@pytest.fixture()
def flow_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=STOREKEEPER, email="sklad@example.test", roles=frozenset({"foreman"}),
        permissions={"shipping_goods": "edit", "warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    shop = _task("КБ1", 50, number="ПЗ-1")
    site = _task("КБ2", 10, number="ПЗ-2", site="ЖУК")
    waiting = _task("КБ3", 20, number="ПЗ-3")
    cancelled = _task("КБ4", 5, number="ПЗ-4", status=ProductionStatus.CANCELLED)
    with sessions() as session:
        tasks = SqlAlchemyProductionRepository(session)
        for task in (shop, site, waiting, cancelled):
            tasks.add(task)
        outputs = SqlAlchemyOutputRepository(session)
        outputs.add(_output(shop, 1, 50))  # первая операция — не изделие
        outputs.add(_output(shop, 2, 30))
        deliveries = SqlAlchemyDeliveryRepository(session)
        deliveries.save(TaskDelivery(uuid4(), site.id, "ЖУК", 10, NOW, "Мастер ЖУК"))
        deliveries.save(TaskDelivery(uuid4(), site.id, "ЖУК", 4, NOW, "Мастер ЖУК", NOW, "Мастер ЖУК"))  # отменена
        session.commit()
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        uow.goods.add(FinishedGood.create(
            project="708К.4", item_index="КБ1", customer="Заказчик", article="", name="Жгут КБ1", ordered=Decimal(50), unit="шт",
        ))
        uow.commit()
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    engine.dispose()


def _accept(client, lines, key="k-1"):
    return client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": key}, json={
        "lines": [{"project": project, "item_index": item, "quantity": quantity} for project, item, quantity in lines],
    })


def _moves(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(FinishedGoodMoveRow))


def test_from_production_lists_what_the_shop_delivered_and_the_warehouse_has_not_accepted(flow_api):
    client, _ = flow_api
    rows = client.get("/api/shipping/finished-goods/from-production").json()["rows"]
    assert [(row["item_index"], row["delivered"], row["accepted"], row["remaining"], row["in_production"]) for row in rows] == [
        ("КБ1", "30", "0", "30", "50"), ("КБ2", "10", "0", "10", "10"), ("КБ3", "0", "0", "0", "20"),
    ]
    assert rows[0]["good_id"] and rows[1]["good_id"] is None and rows[0]["name"] == "Жгут КБ1"
    assert rows[1]["tasks"] == [{"number": "ПЗ-2", "status": "Запущено", "site": "ЖУК", "quantity": 10, "delivered": "10"}]


def test_batch_accept_is_all_or_nothing_and_never_over_what_the_shop_delivered(flow_api):
    client, sessions = flow_api
    over = _accept(client, [("708К.4", "КБ2", "10"), ("708К.4", "КБ1", "31")])
    assert over.status_code == 422
    assert "708К.4 КБ1: сдано цехом 30 шт, уже принято 0 — принять можно ещё 30" in over.json()["detail"]
    assert _moves(sessions) == 0  # КБ2 из того же пакета тоже не принята
    assert _accept(client, [("708К.4", "КБ1", "1"), ("708к.4", "кб1", "1")]).status_code == 422  # КБ дважды
    nothing = _accept(client, [("708К.4", "КБ3", "1")])
    assert nothing.status_code == 422 and "принять можно ещё 0" in nothing.json()["detail"]

    done = _accept(client, [("708К.4", "КБ1", "30"), ("708К.4", "КБ2", "10")])
    assert done.status_code == 200, done.text
    goods = {good["item_index"]: good for good in done.json()["goods"]}
    assert (goods["КБ1"]["accepted"], goods["КБ2"]["accepted"], goods["КБ2"]["ordered"]) == ("30", "10", "10")
    assert goods["КБ1"]["grn"] and goods["КБ2"]["name"] == "Жгут КБ2"
    assert _moves(sessions) == 2

    again = _accept(client, [("708К.4", "КБ1", "30"), ("708К.4", "КБ2", "10")])
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert _moves(sessions) == 2
    other = _accept(client, [("708К.4", "КБ1", "1")])
    assert other.status_code == 409
    rows = client.get("/api/shipping/finished-goods/from-production").json()["rows"]
    assert [(row["item_index"], row["remaining"], row["in_production"]) for row in rows] == [("КБ1", "0", "20"), ("КБ3", "0", "20")]


def test_accept_needs_the_storekeepers_shipping_rights(flow_api):
    client, _ = flow_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Менеджер", department="Продажи"), email="pm@example.test", roles=frozenset({"commercial"}),
        permissions={"shipping": "edit", "warehouse": "edit"},
    )
    assert _accept(client, [("708К.4", "КБ1", "1")]).status_code == 403
    assert client.get("/api/shipping/finished-goods/from-production").status_code == 200
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Оператор", department="Цех"), email="op@example.test", roles=frozenset({"operator"}),
        permissions={"warehouse": "view"},
    )
    assert client.get("/api/shipping/finished-goods/from-production").status_code == 403
    assert client.post("/api/shipping/finished-goods/accept", json={"lines": []}).status_code in (403, 422)


def test_single_accept_keeps_the_same_limit(flow_api):
    client, _ = flow_api
    good = client.get("/api/shipping/goods").json()["goods"][0]
    over = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "31"})
    assert over.status_code == 422 and "сдано цехом 30" in over.json()["detail"]
    ok = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "12"})
    assert ok.status_code == 200 and ok.json()["good"]["accepted"] == "12"
    stale = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "1"})
    assert stale.status_code == 409


def test_project_status_follows_pieces_through_every_state_and_the_ladder_reads_the_register(flow_api):
    client, sessions = flow_api
    assert _accept(client, [("708К.4", "КБ1", "30"), ("708К.4", "КБ2", "10")]).status_code == 200
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Администратор", department="ERP"), email="a@example.test", roles=frozenset({"admin"}), permissions={},
    )
    good = next(item for item in client.get("/api/shipping/goods").json()["goods"] if item["item_index"] == "КБ1")
    shipment = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "upd_type": "ЭДО",
        "lines": [{"good_id": good["id"], "quantity": "20"}],
    }).json()["shipment"]
    client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    second = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "upd_type": "ЭДО",
        "lines": [{"good_id": good["id"], "quantity": "4"}],
    })
    assert second.status_code == 200

    status = client.get("/api/shipping/finished-goods/project", params={"project": "708к.4"}).json()
    assert status["project"] == "708К.4"
    assert [state["label"] for state in status["states"]] == ["В производстве", "Принято складом", "Упаковано", "Отгружено"]
    kbs = {kb["item_index"]: kb for kb in status["kbs"]}
    # Тираж 50: 20 ещё в производстве, 6 на складе, 4 упаковано, 20 уехало.
    assert kbs["КБ1"]["pieces"] == {"production": "20", "accepted": "6", "packed": "4", "shipped": "20"}
    assert (kbs["КБ1"]["delivered"], kbs["КБ1"]["waiting"], kbs["КБ1"]["journal_ok"]) == ("30", "0", True)
    assert [(item["status"], item["picked"]) for item in kbs["КБ1"]["shipments"]] == [("Отгружена", "20"), ("Упакована", "4")]
    assert kbs["КБ2"]["pieces"] == {"production": "0", "accepted": "10", "packed": "0", "shipped": "0"}
    assert kbs["КБ3"]["pieces"]["production"] == "20" and kbs["КБ3"]["good_id"] is None
    kinds = [(move["item_index"], move["kind_label"], move["quantity"]) for move in status["moves"]]
    assert sorted(kinds) == sorted([
        ("КБ1", "Принято складом", "30"), ("КБ2", "Принято складом", "10"), ("КБ1", "Упаковано", "20"),
        ("КБ1", "Отгружено", "20"), ("КБ1", "Упаковано", "4"),
    ])
    shipped = next(move for move in status["moves"] if move["kind"] == "ship")
    assert shipped["shipment_number"] and (shipped["from_label"], shipped["to_label"]) == ("Упаковано", "Отгружено")

    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        goods = uow.goods.list()
    facts, _ = kit_stage_facts(cards=(), tasks=(), fulfillment=(), goods=goods, marks={})
    assert facts["stock"] == {"708К.4|КБ2"}  # КБ1: принято 30 из 50 — ещё не «Принято складом»
    assert facts["packed"] == set() and facts["shipped"] == set()
