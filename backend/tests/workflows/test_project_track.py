"""«Трек проекта» (ARCH-TRACK-3, C1): всё, что по проекту создано в модулях, по времени — только чтение через порты."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.application import CreateFulfillmentCommand, create_fulfillment
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.production.application import LaunchProductionCommand, launch_production
from app.modules.production.domain import ProductionOperation
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.traceability import ProductionOutput
from app.modules.receiving.application import dispatch_next_receiving_expected
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork as SqlAlchemyRequestsUnitOfWork
from app.modules.shipping.application import (
    AcceptGoodCommand,
    CreateShipmentCommand,
    RegisterGoodCommand,
    RequestedGood,
    accept_good,
    create_shipment,
    register_good,
)
from app.modules.shipping.domain import ShipmentStatus
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.modules.techcards.project_cards import ProjectTechcard
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from app.workflows.project_track import TrackEntry, _order, project_track
from tests.factories import Actor
from tests.procurement.test_backorder_flow import NOW as PURCHASE_AT
from tests.procurement.test_backorder_flow import inspect, paid_procurement_line

PROJECT = "708К.4"
NEIGHBOUR = "708К.41"
ACTOR = Actor(name="Технолог", department="Производство")
FACTORIES = (
    "techcards", "requests", "procurement", "receiving", "fulfillment", "production", "shipping",
)


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, 0)


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield SimpleNamespace(
        sessions=sessions,
        techcards=lambda: SqlAlchemyTechcardsUnitOfWork(sessions),
        requests=lambda: SqlAlchemyRequestsUnitOfWork(sessions),
        procurement=lambda: SqlAlchemyProcurementUnitOfWork(sessions),
        receiving=lambda: SqlAlchemyReceivingUnitOfWork(sessions),
        fulfillment=lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
        production=lambda: SqlAlchemyProductionUnitOfWork(sessions),
        shipping=lambda: SqlAlchemyShippingUnitOfWork(sessions),
    )
    engine.dispose()


def track(erp, project=PROJECT, **omit):
    factories = {name: getattr(erp, name) for name in FACTORIES if name not in omit}
    return project_track(project, **factories)


def rows(entries):
    return [(item.module, item.kind, item.ref, item.status, item.item_index) for item in entries]


# ── Что лежит в модулях ──────────────────────────────────────────────────


def seed_project(erp, project=PROJECT):
    """Расчёт → техкарта → заявка → закупка → приёмка → комплект → задание → ГП → отправка, по одной КБ1."""
    with erp.techcards() as uow:
        uow.kitsets.save(Kitset(
            project=project, customer="ООО Заказчик", calc_source="https://docs.google.com/spreadsheets/d/1abc",
            assemblies=(KitAssembly(index="КБ1", name="Жгут питания", run=Decimal("2")),),
            updated_at=at(19, 15), updated_by="Технолог", created_at=at(19, 9),
        ))
        card = ProjectTechcard.create(
            project=project, item_index="КБ1", product_name="Жгут питания", source_calculation="",
            deadline=None, now=at(19, 11),
        )
        issued = card.rebuild({"operations": [{"code": "10", "connector": "A", "name": "Резка"}]}, at(20, 9)).issue(at(20, 10))
        uow.techcards.save(issued)
        uow.commit()


def seed_purchase(erp, project=PROJECT):
    """Заявка проекта → строка закупки (оплачена) → поставка, принятая с ГРН."""
    request, line = paid_procurement_line(erp)
    with erp.requests() as uow:
        stored = uow.requests.get(request.id)  # already handed to procurement: «В работе»
        request = replace(stored, project_reference=project, purpose=f"{project} · КБ1 · Жгут питания")
        uow.requests.add(request)
        uow.commit()
    expected = dispatch_next_receiving_expected(PURCHASE_AT, erp.receiving())
    accepted = inspect(erp, expected, "10")
    return request, line, accepted


def seed_workshop(erp, project=PROJECT, kb="КБ1"):
    line = FulfillmentLine.create(article="43025-0400", name="Корпус Molex", unit="шт", requirement=Decimal("10"))
    kit = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"track:{uuid4()}", purpose=f"{project} · {kb} · Жгут питания", deadline=date(2026, 10, 20),
        lines=(line,), actor=ACTOR, now=at(22, 9),
    ), erp.fulfillment())
    with erp.fulfillment() as uow:
        uow.fulfillment.add(replace(kit, status=FulfillmentStatus.ISSUED, at_issued=at(22, 15)))
        uow.commit()
    operation = ProductionOperation.create(
        position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
        seconds_per_unit=Decimal("60"), setup_seconds=Decimal("0"), quantity=2,
    )
    task = launch_production(LaunchProductionCommand(
        idempotency_key=f"track:{uuid4()}", project=project, item=kb, name="Жгут", quantity=2,
        fulfillment_request_id=kit.id, deadline=date(2026, 10, 20), operations=(operation,), actor=ACTOR, now=at(23, 8),
    ), erp.production())
    return kit, task


def seed_output(erp, task, good: str, defect: str = "0", finished=at(23, 12)):
    with erp.production() as uow:
        uow.outputs.add(ProductionOutput(
            id=uuid4(), session_id=None, task_id=task.id, operation_id=task.operations[0].id, operator_id=uuid4(),
            started_at=at(23, 9), finished_at=finished, pause_seconds=0, good_quantity=Decimal(good),
            defect_quantity=Decimal(defect), output_box_id=uuid4(),
        ))
        uow.commit()


def seed_shipping(erp, project=PROJECT):
    goods = []
    for index, ordered in (("КБ1", "2"), ("КБ2", "4")):
        goods.append(register_good(RegisterGoodCommand(
            project=project, item_index=index, customer="ООО Заказчик", article=f"{project}{index}", name="Жгут",
            ordered=Decimal(ordered), unit="шт", note="", actor=ACTOR, now=at(19, 9),
        ), erp.shipping()))
    accepted = accept_good(AcceptGoodCommand(
        good_id=goods[0].id, expected_version=goods[0].version, quantity=Decimal("2"),
        received_at=date(2026, 9, 25), actor=ACTOR, now=at(25, 9),
    ), erp.shipping())
    shipment = create_shipment(CreateShipmentCommand(
        customer="ООО Заказчик", receiver="ООО Заказчик", shipping_point="Москва, склад 3", contact="", phone="",
        terms=None, carrier="", deadline=None, upd_type=None, upd_number="", upd_date=None, upd_url="", note="",
        lines=(RequestedGood(accepted.id, Decimal("2")),), actor=ACTOR, now=at(26, 10),
    ), erp.shipping())
    with erp.shipping() as uow:
        uow.shipments.add(replace(shipment, status=ShipmentStatus.SHIPPED, shipped_at=date(2026, 9, 27)))
        uow.commit()
    return accepted, shipment


@pytest.fixture()
def seeded(erp):
    seed_project(erp)
    request, line, accepted = seed_purchase(erp)
    kit, task = seed_workshop(erp)
    seed_output(erp, task, "2")
    good, shipment = seed_shipping(erp)
    return SimpleNamespace(request=request, line=line, receipt=accepted, kit=kit, task=task, good=good, shipment=shipment)


# ── Трек по модулям ──────────────────────────────────────────────────────


def test_track_follows_the_project_through_every_module_in_time_order(erp, seeded):
    entries = track(erp)

    assert rows(entries) == [
        ("Проекты", "Карточка проекта из расчёта", PROJECT, "Создана", ""),
        ("Техкарты", "Техкарта изделия", f"{PROJECT}КБ1", "Черновик", "КБ1"),
        ("Проекты", "Карточка проекта", PROJECT, "Обновлена", ""),
        ("Техкарты", "Техкарта выдана", f"{PROJECT}КБ1", "Выдана", "КБ1"),
        ("Заявки", "Заявка на закупку", seeded.request.legacy_id, "В работе", "КБ1"),
        ("Закупка", "Позиция закупки", seeded.line.legacy_id, "В пути", "КБ1"),
        ("Закупка", "Оплата поставщику", seeded.line.legacy_id, "Оплачено", "КБ1"),
        ("Приёмка", "Поставка ожидается", seeded.receipt.legacy_id, "Принято", "КБ1"),
        ("Комплектация", "Запрос на комплектацию", seeded.kit.number, "КБ Выдана", "КБ1"),
        ("Комплектация", "Комплект выдан в цех", seeded.kit.number, "КБ Выдана", "КБ1"),
        ("Производство", "Задание запущено", seeded.task.number, "Запущено", "КБ1"),
        ("Производство", "Операция выполнена", f"{seeded.task.number} · Разделка", "Выполнена", "КБ1"),
        ("Приёмка", "Входной контроль", seeded.receipt.grn, "Принято", "КБ1"),
        ("Отгрузка", "Готовая продукция принята", seeded.good.grn or f"{PROJECT}КБ1", "Принято 2 из 2 шт", "КБ1"),
        ("Отгрузка", "Отправка оформлена", seeded.shipment.number, "Отгружена", "КБ1"),
        ("Отгрузка", "Отправка уехала", seeded.shipment.number, "Отгружена", "КБ1"),
        ("Отгрузка", "Готовая продукция ожидается", f"{PROJECT}КБ2", "В очереди: 4 шт", "КБ2"),
    ]
    dated = [item.at for item in entries if item.at is not None]
    assert dated == sorted(dated)
    assert entries[-1].at is None and all(item.at is not None for item in entries[:-1])


def test_every_entry_has_the_documented_shape(erp, seeded):
    first, *_rest, last = (item.payload() for item in track(erp))

    assert first == {
        "at": "2026-09-19T09:00:00", "module": "Проекты", "kind": "Карточка проекта из расчёта",
        "ref": PROJECT, "status": "Создана", "item_index": "",
    }
    assert last["at"] is None and set(last) == {"at", "module", "kind", "ref", "status", "item_index"}


def test_receipt_shows_its_grn_and_the_line_it_came_from(erp, seeded):
    entries = [item for item in track(erp) if item.module == "Приёмка"]

    assert seeded.receipt.grn and entries[1].ref == seeded.receipt.grn
    assert entries[0].ref == seeded.receipt.legacy_id and entries[0].item_index == "КБ1"


def test_an_operation_is_done_only_when_the_task_quantity_is_handed_in(erp, seeded):
    other = seed_workshop(erp, PROJECT, kb="КБ2")[1]  # a second task of the same project (one kit per КБ)
    seed_output(erp, other, "1")  # 1 of 2 — not done

    kinds = [(item.kind, item.ref) for item in track(erp) if item.module == "Производство"]

    assert ("Операция выполнена", f"{seeded.task.number} · Разделка") in kinds
    assert not any(kind == "Операция выполнена" and ref.startswith(other.number) for kind, ref in kinds)
    assert ("Задание запущено", other.number) in kinds
    seed_output(erp, other, "0", defect="1")  # rejected pieces count as processed, as prodOpState_ does
    assert any(item.ref.startswith(other.number) and item.kind == "Операция выполнена" for item in track(erp))


def test_only_the_project_itself_never_its_neighbour(erp, seeded):
    before = rows(track(erp))
    seed_project(erp, NEIGHBOUR)
    seed_workshop(erp, NEIGHBOUR)
    seed_shipping(erp, NEIGHBOUR)

    assert rows(track(erp)) == before  # «708К.4» does not take «708К.41» in any module
    neighbour = track(erp, NEIGHBOUR)
    assert {item.module for item in neighbour} == {"Проекты", "Техкарты", "Комплектация", "Производство", "Отгрузка"}
    assert not {item.ref for item in neighbour} & {item.ref for item in track(erp)}


def test_latin_k_and_missing_dot_are_the_same_project(erp):
    seed_workshop(erp, "719К.1")

    assert len(track(erp, "719К")) == len(track(erp, "719K.1")) == 3  # «719К» ≡ «719К.1», Latin K ≡ Cyrillic К
    assert track(erp, "719К.11") == []


def test_missing_modules_are_skipped(erp, seeded):
    only_workshop = track(erp, techcards=None, requests=None, procurement=None, receiving=None, shipping=None)

    assert {item.module for item in only_workshop} == {"Комплектация", "Производство"}
    without_receiving = track(erp, receiving=None)
    assert "Приёмка" not in {item.module for item in without_receiving}
    assert {"Проекты", "Заявки", "Закупка", "Производство", "Отгрузка"} <= {item.module for item in without_receiving}
    assert project_track(PROJECT) == []
    assert project_track("") == []


def test_receipt_is_found_by_its_purpose_when_requests_and_procurement_are_not_read(erp, seeded):
    with erp.receiving() as uow:
        uow.receiving.add(replace(seeded.receipt, purpose=f"{PROJECT} · КБ1 · Жгут питания"))
        uow.commit()

    entries = track(erp, requests=None, procurement=None)

    assert [(item.kind, item.item_index) for item in entries if item.module == "Приёмка"] == [
        ("Поставка ожидается", "КБ1"), ("Входной контроль", "КБ1"),
    ]


def test_a_project_without_records_has_an_empty_track(erp, seeded):
    assert track(erp, "999К.9") == []


def test_reading_the_track_writes_nothing(erp, seeded):
    def counts():
        with erp.sessions() as session:
            return session.scalar(select(func.count()).select_from(AuditEventRow)), \
                session.scalar(select(func.count()).select_from(OutboxMessageRow))

    before = counts()
    track(erp)

    assert counts() == before


# ── Порядок: время, без даты — в конце ───────────────────────────────────


def test_undated_entries_go_last_and_equal_times_keep_the_section_order():
    def entry(when, kind):
        return TrackEntry(when, "Модуль", kind, "N", "Статус")

    moment = datetime(2026, 9, 21, 10, 0)
    ordered = _order([
        entry(None, "без даты"), entry(datetime(2026, 9, 22, 9, 0), "позже"), entry(moment, "раньше 1"),
        entry(moment, "раньше 2"), entry(None, "без даты 2"),
    ])

    assert [item.kind for item in ordered] == ["раньше 1", "раньше 2", "позже", "без даты", "без даты 2"]


def test_naive_and_aware_times_sort_together():
    naive = datetime(2026, 9, 21, 10, 0)
    aware = datetime(2026, 9, 21, 10, 30, tzinfo=timezone.utc)
    earlier_aware = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)

    ordered = _order([TrackEntry(value, "М", "К", "N", "С") for value in (aware, naive, earlier_aware)])

    assert [item.at for item in ordered] == [earlier_aware, naive, aware]


# ── API ──────────────────────────────────────────────────────────────────

STATE = {
    "techcards_uow_factory": "techcards", "uow_factory": "requests", "procurement_uow_factory": "procurement",
    "receiving_uow_factory": "receiving", "fulfillment_uow_factory": "fulfillment",
    "production_uow_factory": "production", "shipping_uow_factory": "shipping",
}


def _principal(permissions):
    return lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Менеджер", department="Проекты"),
        email="pm@example.test", roles=frozenset({"pm"}), permissions=permissions,
    )


@pytest.fixture()
def client(erp):
    for name, factory in STATE.items():
        setattr(app.state, name, getattr(erp, factory))
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    for name in STATE:
        if hasattr(app.state, name):
            delattr(app.state, name)


def test_api_returns_the_track_of_a_project(client, erp, seeded):
    response = client.get(f"/api/techcards/kitsets/{PROJECT}/track")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"] == PROJECT
    assert [item["module"] for item in body["entries"]][:3] == ["Проекты", "Техкарты", "Проекты"]
    assert body["entries"][-1] == {
        "at": None, "module": "Отгрузка", "kind": "Готовая продукция ожидается",
        "ref": f"{PROJECT}КБ2", "status": "В очереди: 4 шт", "item_index": "КБ2",
    }
    assert body["entries"] == [item.payload() for item in track(erp)]


def test_api_matches_the_project_written_with_latin_k(client, erp, seeded):
    response = client.get("/api/techcards/kitsets/708K.4/track")

    assert response.status_code == 200 and response.json()["project"] == PROJECT


def test_api_skips_a_module_that_is_not_configured(client, erp, seeded):
    delattr(app.state, "receiving_uow_factory")

    response = client.get(f"/api/techcards/kitsets/{PROJECT}/track")

    assert response.status_code == 200
    assert "Приёмка" not in {item["module"] for item in response.json()["entries"]}


def test_api_unknown_project_is_404_and_gone(client):
    response = client.get("/api/techcards/kitsets/999К.9/track")

    assert response.status_code == 404
    assert response.json()["detail"]["gone"] is True


def test_api_needs_the_projects_view_permission(client, erp, seeded):
    for permissions in ({"techcards": "edit"}, {"production": "edit"}, {"projects": "none"}, {}):
        app.dependency_overrides[authenticated_principal] = _principal(permissions)
        assert client.get(f"/api/techcards/kitsets/{PROJECT}/track").status_code == 403, permissions
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    assert client.get(f"/api/techcards/kitsets/{PROJECT}/track").status_code == 200
