"""Загрузка станков через HTTP: сетка по дням из плана и выданной ЭТК, единицы из БД.ОБ, предупреждение о занятом
станке при ручной постановке."""

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import ProjectTechcard
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor

MASTER = UUID("31000000-0000-0000-0000-000000000001")
ANNA = UUID("31000000-0000-0000-0000-000000000002")
BORIS = UUID("31000000-0000-0000-0000-000000000003")
VERA = UUID("31000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 9, 22, 8, 0)  # вторник, до смены


@pytest.fixture()
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        session.add_all([
            IdentityUserRow(id=MASTER, login="master", email="master@example.test", name="Мастер", department="Производство", role="master", site="ЦАР", active=True, site_master=True),
            *(IdentityUserRow(id=person, login=login, email=f"{login}@example.test", name=name, department="Монтаж", role="staff", site="ЦАР", active=True, shop_worker=True)
              for person, login, name in ((ANNA, "anna", "Анна"), (BORIS, "boris", "Борис"), (VERA, "vera", "Вера"))),
        ])
        session.commit()
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    with app.state.references_uow_factory() as references:
        for number in ("ОБ.027.1", "ОБ.027.2"):
            references.tech_operations.save(TechOperationRecord.create(
                tab=TechOperationTab.EQUIPMENT, display_text=f"Пресс {number}", search_text=number,
                source_sheet="БД.ОБ", sort_key=number, fields={"Тип": "Оборудование", "Инв. номер": number},
            ))
        references.commit()
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER, name="Мастер", department="Производство"),
        email="master@example.test", roles=frozenset({"foreman"}), permissions={"production": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    app.dependency_overrides[moscow_today] = lambda: NOW.date()
    yield TestClient(app)
    for dependency in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(dependency, None)
    for name in ("production_uow_factory", "techcards_uow_factory", "references_uow_factory"):
        delattr(app.state, name)
    engine.dispose()


def launch(http: TestClient, index: str) -> dict:
    """Выданная техкарта «опрессовка на прессе ОБ.027 → сборка без станка» и задание по ней на 50 шт."""
    card = ProjectTechcard.create(
        project="708К", item_index=index, product_name="Жгут", source_calculation="", deadline=None, now=NOW,
    ).rebuild({
        "operations": [
            {"code": "PRS", "type": "prsTerm",
             "card": {"tooling": [{"section": "Оборудование", "inventory_number": "ОБ.027.X", "name": "Пресс"}]}},
            {"code": "ASM", "type": "insHousing"},
        ],
        "build_config": {"ops": [
            {"type": "prsTerm", "stage": "Опрессовка терминалов", "opNumber": "PRS", "tOp": 30, "tPrep": 1800, "tMachine": 12},
            {"type": "insHousing", "stage": "Монтаж в корпус", "opNumber": "ASM", "tOp": 20, "tPrep": 300},
        ]},
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    response = http.post(
        "/api/production/tasks", headers={"Idempotency-Key": f"machine-{index.encode().hex()}"},
        json={"techcard_id": str(card.id), "quantity": 50, "deadline": "2026-10-02"},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def put(http: TestClient, task: dict, person: UUID, start: str) -> dict:
    operation = next(item for item in task["operations"] if item["code"] == "PRS")
    response = http.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/share", json={
        "version": task["version"], "operator_id": str(person), "quantity": 50, "requested_start": start,
    })
    assert response.status_code == 200, response.text
    return response.json()["task"]


def check(http: TestClient, task: dict, person: UUID, start: str, finish: str) -> list[dict]:
    operation = next(item for item in task["operations"] if item["code"] == "PRS")
    response = http.post("/api/production/machine-load/check", json={
        "task_id": task["id"], "operation_id": operation["id"], "operator_id": str(person), "quantity": 50,
        "start": start, "finish": finish,
    })
    assert response.status_code == 200, response.text
    return response.json()["conflicts"]


def test_grid_shows_press_load_by_day_with_units_from_reference(client):
    first = put(client, launch(client, "КБ1"), ANNA, "2026-09-22T09:00")

    response = client.get("/api/production/machine-load", params={"date_from": "2026-09-21", "date_to": "2026-09-27"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warnings"] == []
    [press] = body["machines"]
    assert (press["machine"], press["label"], press["units"], press["known"]) == ("ОБ.027", "ОБ.027 Пресс", 2, True)
    tuesday = press["days"][1]
    # 1800 с подготовки + 12 с машинного × 50 шт = 2400 с из 2 × 7 ч смены.
    assert tuesday["day"] == "2026-09-22" and tuesday["capacity_seconds"] == 2 * 7 * 3600
    assert tuesday["busy_seconds"] == pytest.approx(2400, abs=60) and tuesday["percent"] == 5 and not tuesday["over"]
    assert press["days"][5]["percent"] is None  # суббота
    [item] = press["items"]
    assert (item["task_number"], item["stage"], item["operator_names"]) == (first["number"], "Опрессовка терминалов", ["Анна"])
    assert item["start"].startswith("2026-09-22T09:00")


def test_check_warns_only_when_every_unit_of_the_press_is_taken(client):
    put(client, launch(client, "КБ1"), ANNA, "2026-09-22T09:00")
    second = launch(client, "КБ2")
    third = launch(client, "КБ3")

    assert check(client, second, BORIS, "2026-09-22T09:00", "2026-09-22T10:00") == []  # вторая единица свободна
    second = put(client, second, BORIS, "2026-09-22T09:00")
    conflicts = check(client, third, VERA, "2026-09-22T09:10", "2026-09-22T10:00")

    assert [item["machine"] for item in conflicts] == ["ОБ.027"]
    assert conflicts[0]["text"].startswith("ОБ.027 занят (заняты все 2 ед.) 22.09 09:10–")
    assert {item["operator_names"][0] for item in conflicts[0]["busy"]} == {"Анна", "Борис"}
    # Переставить свою же долю — не конфликт с самой собой; после окна — свободно.
    assert check(client, second, BORIS, "2026-09-22T09:00", "2026-09-22T10:00") == []
    assert check(client, third, VERA, "2026-09-22T14:00", "2026-09-22T15:00") == []


def test_period_is_validated(client):
    assert client.get("/api/production/machine-load", params={"date_from": "2026-09-27", "date_to": "2026-09-21"}).status_code == 422
    assert client.get("/api/production/machine-load", params={"date_from": "2026-09-01", "date_to": "2026-12-01"}).status_code == 422
    assert client.get("/api/production/machine-load", params={"date_from": "2026-09-21", "date_to": "2026-09-27"}).json()["machines"] == []
