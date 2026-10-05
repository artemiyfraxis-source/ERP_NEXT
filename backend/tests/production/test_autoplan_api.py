"""Автоплан через HTTP: предпросмотр, применение одним коммитом, отпечаток, площадка мастера."""

from datetime import date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

MASTER = UUID("30000000-0000-0000-0000-000000000001")
ANNA = UUID("30000000-0000-0000-0000-000000000002")
BORIS = UUID("30000000-0000-0000-0000-000000000003")
ZHUK = UUID("30000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 9, 22, 9, 0)
PERIOD = {"date_from": "2026-09-22", "date_to": "2026-09-25"}


@pytest.fixture()
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        session.add_all([
            IdentityUserRow(id=MASTER, login="master", email="master@example.test", name="Мастер", department="Производство", role="master", site="ЦАР", active=True, site_master=True),
            IdentityUserRow(id=ANNA, login="anna", email="anna@example.test", name="Анна", department="Монтаж", role="staff", site="ЦАР", active=True, shop_worker=True),
            IdentityUserRow(id=BORIS, login="boris", email="boris@example.test", name="Борис", department="Монтаж", role="staff", site="ЦАР", active=True, shop_worker=True),
            IdentityUserRow(id=ZHUK, login="zhuk", email="zhuk@example.test", name="Антон", department="Монтаж", role="staff", site="ЖУК", active=True, shop_worker=True),
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
    yield TestClient(app), sessions
    for dependency in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(dependency, None)
    for name in ("production_uow_factory", "techcards_uow_factory", "references_uow_factory"):
        delattr(app.state, name)
    engine.dispose()


def launch(client: TestClient, index: str, *, terminal: str = "T-1", height: str = "1,14 - 1,24",
           deadline: str = "2026-10-02", quantity: int = 50) -> dict:
    """Выданная техкарта «опрессовка → сборка» и запущенное по ней задание."""
    config = {"ops": [
        {"type": "prsTerm", "stage": "Опрессовка терминалов", "opNumber": "PRS", "tOp": 30, "tPrep": 1800,
         "ep": {"termArt": terminal}},
        {"type": "insHousing", "stage": "Монтаж в корпус", "opNumber": "ASM", "tOp": 20, "tPrep": 300},
    ]}
    card = ProjectTechcard.create(
        project="708К", item_index=index, product_name="Жгут", source_calculation="", deadline=None, now=NOW,
    ).rebuild({
        "operations": [
            {"code": "PRS", "connector": "1", "side": "", "type": "prsTerm",
             "fields": {"applicator": "A19", "crimpHeight": height},
             "card": {"tooling": [{"section": "Оборудование", "inventory_number": "ОБ.027.X", "name": "Пресс"}]}},
            {"code": "ASM", "connector": "2", "side": "", "type": "insHousing", "connector_article": "XP-6"},
        ],
        "build_config": config, "kit": {"part_quantity": "10"},
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    response = client.post(
        "/api/production/tasks", headers={"Idempotency-Key": f"autoplan-{index.encode().hex()}"},
        json={"techcard_id": str(card.id), "quantity": quantity, "deadline": deadline},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def preview(client: TestClient, **extra) -> dict:
    response = client.post("/api/production/autoplan/preview", json={**PERIOD, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client: TestClient, planned: dict, **extra):
    return client.post("/api/production/autoplan/apply", json={
        **PERIOD, "fingerprint": planned["fingerprint"],
        "items": [{key: item[key] for key in ("task_id", "operation_id", "operator_id")} for item in planned["proposals"]],
        **extra,
    })


def test_preview_batches_one_terminal_on_one_fitter_and_skips_zhuk(client):
    http, _ = client
    tasks = [launch(http, index, height=height) for index, height in (("КБ1", "1,14 - 1,24"), ("КБ2", "1,14 - 1,24"), ("КБ3", "0,80 - 0,90"))]
    handed = launch(http, "КБ4")
    assert http.post(f"/api/production/tasks/{handed['id']}/handover", json={"version": handed["version"], "site": "ЖУК"}).status_code == 200

    planned = preview(http)

    crimps = [item for item in planned["proposals"] if item["code"] == "PRS"]
    assert {item["task_id"] for item in planned["proposals"]} == {task["id"] for task in tasks}
    assert len(planned["proposals"]) == 6 and planned["unplaced"] == []
    assert {item["operator_id"] for item in crimps} == {str(ANNA)}
    assert sorted(item["changeover_seconds"] for item in crimps) == [0, 600, 1800]
    assert all(item["operator_id"] != str(ZHUK) for item in planned["proposals"])
    assert crimps[0]["machine"] == "ОБ.027 Пресс"
    assert crimps[0]["setup_label"].startswith("Опрессовка терминалов: терминал T-1, аппликатор A19")
    anna = next(item for item in planned["batches"] if item["operator_id"] == str(ANNA))
    assert anna["batches"][0]["operations"] == 3
    assert anna["batches"][0]["tasks"] == [task["number"] for task in tasks]
    assert planned["stats"]["changeovers"] < planned["stats"]["changeovers_naive"]
    assert sum(item["setup_seconds"] - item["changeover_seconds"] for item in crimps) == 1800 + 1200
    assert planned["stats"]["saved_seconds"] == sum(item["setup_seconds"] - item["changeover_seconds"] for item in planned["proposals"])
    assert all(item["task_version"] == tasks[0]["version"] for item in crimps)


def test_apply_writes_the_chosen_assignments_and_one_audit_event(client):
    http, sessions = client
    first, second = launch(http, "КБ1"), launch(http, "КБ2")
    planned = preview(http)
    chosen = {**planned, "proposals": [item for item in planned["proposals"] if item["task_id"] == first["id"]]}

    response = apply(http, chosen)

    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 2
    assert [item["number"] for item in response.json()["tasks"]] == [first["number"]]
    stored = {task["id"]: task for task in http.get("/api/production/tasks").json()["tasks"]}
    for operation, proposal in zip(stored[first["id"]]["operations"], chosen["proposals"]):
        assert operation["operator_ids"] == [proposal["operator_id"]]
        assert operation["assigned_by"] == "Автоплан · Мастер"
    assert stored[first["id"]]["version"] == first["version"] + 1
    assert all(not operation["operator_ids"] for operation in stored[second["id"]]["operations"])
    with sessions() as session:
        events = [row.details for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "production.autoplan_applied"))]
    assert len(events) == 1
    assert events[0]["operations"] == 2 and events[0]["tasks"] == [first["number"]]
    assert (events[0]["date_from"], events[0]["date_to"], events[0]["master"]) == ("2026-09-22", "2026-09-25", "Мастер")


def test_existing_assignments_stay_and_replan_moves_only_autoplanned_work(client):
    http, _ = client
    manual = launch(http, "КБ1")
    crimp = manual["operations"][0]
    assigned = http.post(
        f"/api/production/tasks/{manual['id']}/operations/{crimp['id']}/assign",
        json={"version": manual["version"], "operator_ids": [str(BORIS)]},
    )
    assert assigned.status_code == 200, assigned.text
    launch(http, "КБ2", terminal="T-2")

    planned = preview(http)
    assert crimp["id"] not in {item["operation_id"] for item in planned["proposals"]}
    assembly = next(item for item in planned["proposals"] if item["operation_id"] == manual["operations"][1]["id"])
    assert assembly["start"] >= assigned.json()["plan"]["finish"]
    assert apply(http, planned).status_code == 200

    locked = preview(http)
    moved = preview(http, replan_auto=True)

    assert locked["proposals"] == []
    autoplanned = {item["operation_id"] for item in planned["proposals"]}
    assert {item["operation_id"] for item in moved["proposals"]} == autoplanned
    stored = next(task for task in http.get("/api/production/tasks").json()["tasks"] if task["id"] == manual["id"])
    assert stored["operations"][0]["operator_ids"] == [str(BORIS)] and stored["operations"][0]["assigned_by"] == "Мастер"


def test_stale_fingerprint_is_refused(client):
    http, _ = client
    task = launch(http, "КБ1")
    planned = preview(http)
    operation = task["operations"][1]
    http.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(BORIS)]},
    )

    changed = apply(http, planned)
    forged = apply(http, {**preview(http), "fingerprint": "0" * 64})

    assert changed.status_code == 409, changed.text
    assert "пересчитайте" in changed.json()["detail"]
    assert forged.status_code == 409


def test_master_of_another_site_is_refused(client):
    http, sessions = client
    launch(http, "КБ1")
    with sessions() as session:
        session.get(IdentityUserRow, MASTER).site = "ЖУК"
        session.commit()

    response = http.post("/api/production/autoplan/preview", json=PERIOD)

    assert response.status_code == 403, response.text
    assert "ЦАР" in response.json()["detail"]


def test_period_is_validated(client):
    http, _ = client

    backwards = http.post("/api/production/autoplan/preview", json={"date_from": "2026-09-25", "date_to": "2026-09-22"})
    past = http.post("/api/production/autoplan/preview", json={"date_from": "2026-09-01", "date_to": "2026-09-05"})

    assert backwards.status_code == 422 and past.status_code == 422


def test_task_filter_limits_what_is_planned(client):
    http, _ = client
    first, second = launch(http, "КБ1"), launch(http, "КБ2")

    planned = preview(http, task_ids=[first["id"]])

    assert {item["task_id"] for item in planned["proposals"]} == {first["id"]}
    assert len(planned["proposals"]) == 2
    assert date.fromisoformat(planned["proposals"][0]["start"][:10]) == NOW.date()
    # Отпечаток помнит фильтр: применить без него — данные «другие».
    assert apply(http, planned).status_code == 409
    applied = apply(http, planned, task_ids=[first["id"]])
    assert applied.status_code == 200, applied.text
    assert [item["id"] for item in applied.json()["tasks"]] == [first["id"]]
    assert second["id"] in {item["task_id"] for item in preview(http)["proposals"]}
