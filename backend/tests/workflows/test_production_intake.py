"""«Приёмка с производства» (владелец 14.10.2026): все КБ открытых проектов, что в работе, — от не запущенной до
частично принятой; уехавшее целиком и отменённые / закрытые проекты из списка уходят."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.production.domain import ProductionOperation, ProductionStatus, ProductionTask, TaskDelivery
from app.modules.production.infrastructure import (
    SqlAlchemyDeliveryRepository,
    SqlAlchemyOutputRepository,
    SqlAlchemyPlanEventRepository,
    SqlAlchemyPlanProjectRepository,
    SqlAlchemyProductionRepository,
)
from app.modules.production.plan import PlanEvent, PlanProjectSnapshot
from app.modules.production.traceability import ProductionOutput
from app.modules.shipping.domain import FinishedGood
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.kitset_infrastructure import SqlAlchemyKitsetRepository
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.workflows.finished_goods import KbProduction, TaskShare
from app.workflows.production_intake import IntakeFacts, intake_rows, natural_key
from tests.factories import Actor

NOW = datetime(2026, 10, 14, 9, 0)
STOREKEEPER = Actor(id=UUID("10000000-0000-0000-0000-000000000007"), name="Алексей", department="Склад")


def _task(project: str, item: str, quantity: int, *, number: str, site: str = "", status=ProductionStatus.RUNNING) -> ProductionTask:
    operations = tuple(
        ProductionOperation.create(
            position=position, code=f"OP-0{position}", stage="Монтаж", description="", seconds_per_unit=Decimal("60"),
            setup_seconds=Decimal("0"), quantity=quantity,
        ) for position in (1, 2)
    )
    task = ProductionTask.create(
        number=number, source_idempotency_key=f"key:{number}", source_fingerprint="f", project=project, item=item,
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


def _kitset(project: str, *runs: tuple[str, int]) -> Kitset:
    return Kitset(
        project=project, customer="Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
        assemblies=tuple(KitAssembly(index=index, name=f"Жгут {index}", run=Decimal(run)) for index, run in runs),
    )


@pytest.fixture()
def intake_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=STOREKEEPER, email="sklad@example.test", roles=frozenset({"foreman"}),
        permissions={"shipping_goods": "edit", "warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    shop = _task("708К.4", "КБ1", 50, number="ПЗ-1")
    site = _task("708К.4", "КБ2", 10, number="ПЗ-2", site="ЖУК")
    done = _task("708К.4", "КБ10", 5, number="ПЗ-3", status=ProductionStatus.DONE)
    gone = _task("708К.4", "КБ3", 20, number="ПЗ-4", status=ProductionStatus.CANCELLED)
    cancelled_project = _task("710К.2", "КБ1", 7, number="ПЗ-5", status=ProductionStatus.DONE)
    with sessions() as session:
        SqlAlchemyPlanProjectRepository(session).replace((
            PlanProjectSnapshot(imported_at=NOW, project="708К.4", customer="Заказчик А", deadline=date(2026, 11, 1)),
            PlanProjectSnapshot(imported_at=NOW, project="709К.1", customer="Заказчик Б"),
            PlanProjectSnapshot(imported_at=NOW, project="710К.2", customer="Заказчик В", deadline=date(2026, 10, 1)),
            PlanProjectSnapshot(imported_at=NOW, project="711К.3", customer="Заказчик Г", status="Закрыт"),
            PlanProjectSnapshot(imported_at=NOW, project="712К.1", customer="Заказчик Д", deadline=date(2026, 10, 20)),
        ))
        SqlAlchemyPlanEventRepository(session).add(PlanEvent(
            project="710К.2", occurred_at=NOW, who="Руководитель", kind="Статус проекта", value="Отменён",
        ))
        kitsets = SqlAlchemyKitsetRepository(session)
        kitsets.save(_kitset("708К.4", ("КБ1", 50), ("КБ2", 10), ("КБ3", 20), ("КБ10", 5), ("КБ4", 6)))
        kitsets.save(_kitset("709К.1", ("КБ1", 3)))
        kitsets.save(_kitset("710К.2", ("КБ1", 7)))
        kitsets.save(_kitset("711К.3", ("КБ1", 2)))
        kitsets.save(_kitset("712К.1", ("КБ2", 4)))
        tasks = SqlAlchemyProductionRepository(session)
        for task in (shop, site, done, gone, cancelled_project):
            tasks.add(task)
        outputs = SqlAlchemyOutputRepository(session)
        outputs.add(_output(shop, 1, 50))  # первая операция — не изделие
        outputs.add(_output(shop, 2, 30))
        SqlAlchemyDeliveryRepository(session).save(TaskDelivery(uuid4(), site.id, "ЖУК", 4, NOW, "Мастер ЖУК"))
        session.commit()
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        uow.goods.add(FinishedGood.create(
            project="708К.4", item_index="КБ1", customer="Заказчик", article="", name="Жгут КБ1 изделие",
            ordered=Decimal(50), unit="шт",
        ))
        uow.goods.add(FinishedGood.create(
            project="708К.4", item_index="КБ4", customer="Заказчик", article="", name="Жгут КБ4", ordered=Decimal(6), unit="шт",
        ))
        uow.commit()
    client = TestClient(app)
    # КБ4 сделана до ERP (заданий нет): принята до тиража и уехала целиком.
    assert client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "k-4"}, json={
        "lines": [{"project": "708К.4", "item_index": "КБ4", "quantity": "6"}],
    }).status_code == 200
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Администратор", department="ERP"), email="a@example.test", roles=frozenset({"admin"}), permissions={},
    )
    good = next(item for item in client.get("/api/shipping/goods").json()["goods"] if item["item_index"] == "КБ4")
    shipment = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "upd_type": "ЭДО",
        "lines": [{"good_id": good["id"], "quantity": "6"}],
    }).json()["shipment"]
    assert client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]}).status_code == 200
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=STOREKEEPER, email="sklad@example.test", roles=frozenset({"foreman"}),
        permissions={"shipping_goods": "edit", "warehouse": "edit"},
    )
    yield client, engine
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    engine.dispose()


def _rows(client) -> list[dict]:
    response = client.get("/api/shipping/intake")
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def test_intake_lists_every_kb_of_open_projects_until_everything_is_sent(intake_api):
    client, _ = intake_api
    accepted = client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "k-1"}, json={
        "lines": [{"project": "708К.4", "item_index": "КБ1", "quantity": "12"}],
    })
    assert accepted.status_code == 200, accepted.text
    rows = _rows(client)
    # По сроку (без срока — в конце), КБ по номеру: КБ2 раньше КБ10; отменённый 710К.2 и закрытый 711К.3 не видны,
    # КБ4 уехала целиком.
    assert [(row["project"], row["item_index"]) for row in rows] == [
        ("712К.1", "КБ2"),
        ("708К.4", "КБ1"), ("708К.4", "КБ2"), ("708К.4", "КБ3"), ("708К.4", "КБ10"),
        ("709К.1", "КБ1"),
    ]
    by_kb = {(row["project"], row["item_index"]): row for row in rows}

    partly_accepted = by_kb[("708К.4", "КБ1")]
    assert partly_accepted["good_id"] and partly_accepted["good_version"] == 2 and partly_accepted["grn"]
    assert {key: partly_accepted[key] for key in (
        "customer", "deadline", "name", "unit", "stage", "needed", "launched", "delivered", "accepted", "packed", "sent",
        "remaining", "location",
    )} == {
        "customer": "Заказчик А", "deadline": "2026-11-01", "name": "Жгут КБ1 изделие", "unit": "шт", "stage": "В работе",
        "needed": "50", "launched": "50", "delivered": "30", "accepted": "12", "packed": "0", "sent": "0",
        "remaining": "18", "location": "",
    }
    assert partly_accepted["tasks"] == [{"number": "ПЗ-1", "status": "Запущено", "site": "", "quantity": 50, "delivered": "30"}]

    partly_handed = by_kb[("708К.4", "КБ2")]
    assert (partly_handed["needed"], partly_handed["launched"], partly_handed["delivered"], partly_handed["remaining"]) == (
        "10", "10", "4", "4",
    )
    assert partly_handed["good_id"] is None and partly_handed["good_version"] is None and partly_handed["grn"] is None
    assert partly_handed["tasks"][0]["site"] == "ЖУК"

    not_launched = by_kb[("708К.4", "КБ3")]  # единственное задание отменено — как не запущенная
    assert (not_launched["stage"], not_launched["needed"], not_launched["launched"], not_launched["delivered"],
            not_launched["remaining"], not_launched["tasks"]) == ("Новый", "20", "0", "0", "0", [])
    assert by_kb[("709К.1", "КБ1")]["deadline"] is None and by_kb[("709К.1", "КБ1")]["needed"] == "3"

    done = by_kb[("708К.4", "КБ10")]
    assert (done["stage"], done["delivered"], done["remaining"]) == ("ОТК", "5", "5")

    for row in rows:
        assert Decimal(row["remaining"]) == max(Decimal(row["delivered"]) - Decimal(row["accepted"]), Decimal(0))


def test_intake_reads_in_bulk_not_per_kb(intake_api):
    client, engine = intake_api
    statements: list[str] = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        _rows(client)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    # Ни запроса ГП, набора или карточки ТК на КБ: по одному на всё.
    assert sum("FROM finished_goods" in sql for sql in statements) == 1
    assert sum("FROM techcard_kitsets" in sql for sql in statements) == 1
    assert len(statements) < 60


def test_intake_needs_shipping_view(intake_api):
    client, _ = intake_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Менеджер", department="Продажи"), email="pm@example.test", roles=frozenset({"commercial"}),
        permissions={"shipping": "view"},
    )
    assert client.get("/api/shipping/intake").status_code == 200
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Оператор", department="Цех"), email="op@example.test", roles=frozenset({"operator"}),
        permissions={"warehouse": "edit", "production": "edit"},
    )
    assert client.get("/api/shipping/intake").status_code == 403


def _good(item: str, ordered: int, *, accepted: int = 0, sent: int = 0) -> FinishedGood:
    good = FinishedGood.create(
        project="720К.1", item_index=item, customer="", article="", name=f"Жгут {item}", ordered=Decimal(ordered), unit="шт",
    )
    return replace(good, accepted=Decimal(accepted), shipped=Decimal(sent), sent=Decimal(sent))


def test_remaining_never_exceeds_what_the_shop_delivered_and_the_warehouse_has_not_accepted():
    kb = KbProduction(
        key="720К.1|КБ1", project="720К.1", item_index="КБ1", name="Жгут", run=Decimal(10), delivered=Decimal(4),
        tasks=(TaskShare("ПЗ-1", "Запущено", "", 10, Decimal(4)),),
    )
    facts = IntakeFacts(
        projects=(PlanProjectSnapshot(project="720К.1"),),
        goods=(_good("КБ1", 10, accepted=6), _good("КБ2", 3, sent=2), _good("КБ3", 3, accepted=3, sent=3)),
        production={kb.key: kb},
    )
    rows = {row.item_index: row for row in intake_rows(facts)}
    # Принято до ERP больше сданного по заданиям — принять сейчас нечего, а не минус.
    assert rows["КБ1"].remaining == 0 and rows["КБ1"].launched == 10
    assert rows["КБ2"].sent == 2 and rows["КБ2"].remaining == 0  # уехало 2 из 3 — ещё в списке
    assert "КБ3" not in rows  # уехало всё


def test_kb_without_a_known_run_stays_and_natural_order():
    facts = IntakeFacts(
        projects=(PlanProjectSnapshot(project="721К.1"),),
        kitsets=(_kitset("721К.1", ("КБ10", 0), ("КБ2", 1), ("КБ1", 1)),),
    )
    assert [(row.item_index, row.needed) for row in intake_rows(facts)] == [
        ("КБ1", 1), ("КБ2", 1), ("КБ10", 0),
    ]
    assert natural_key("КБ2") < natural_key("КБ10") < natural_key("КБ10А")


def test_kb_marked_shipped_leaves_the_intake_even_without_erp_shipments():
    """Владелец 14.10: КБ с этапом «Отгружено» (отгружено ещё в Google, отмечено вручную) — принимать нечего."""
    facts = IntakeFacts(
        projects=(PlanProjectSnapshot(project="722К.1"),),
        kitsets=(_kitset("722К.1", ("КБ1", 5), ("КБ2", 5)),),
        events=(PlanEvent(project="722К.1", item_index="КБ2", occurred_at=NOW, who="Мастер", kind="Этап КБ", value="Отгружено"),),
    )
    assert [row.item_index for row in intake_rows(facts)] == ["КБ1"]


def test_kb_in_work_without_erp_tasks_can_be_accepted_up_to_the_order():
    """Владелец 14.10: цех ведёт КБ вне заданий ERP — «В работе» без заданий принимается до заказанного минус принятое,
    «Новый» — нет (как предел `finished_goods._limit` без заданий: заказанное)."""
    facts = IntakeFacts(
        projects=(PlanProjectSnapshot(project="723К.1"),),
        kitsets=(_kitset("723К.1", ("КБ1", 50), ("КБ2", 50)),),
        goods=(replace(_good("КБ1", 50, accepted=20), project="723К.1"), replace(_good("КБ2", 50), project="723К.1")),
        events=(PlanEvent(project="723К.1", item_index="КБ1", occurred_at=NOW, who="Мастер", kind="Этап КБ", value="В работе"),),
    )
    rows = {row.item_index: row for row in intake_rows(facts)}
    assert (rows["КБ1"].stage, rows["КБ1"].remaining) == ("В работе", 30)
    assert rows["КБ2"].remaining == 0
