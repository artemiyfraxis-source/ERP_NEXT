"""Автоплан через HTTP: режимы «Весь цех» и «По проекту», КБ без задания, применение одним коммитом, отпечаток,
горизонт без выбора периода, площадка мастера."""

from datetime import date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.domain import ProductionTask, ProductionValidationError
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


def issue(index: str, *, project: str = "708К", terminal: str = "T-1", height: str = "1,14 - 1,24",
          part_quantity: str = "10", deadline: date | None = None) -> ProjectTechcard:
    """Выданная техкарта «опрессовка → сборка» (задания по ней ещё нет)."""
    config = {"ops": [
        {"type": "prsTerm", "stage": "Опрессовка терминалов", "opNumber": "PRS", "tOp": 30, "tPrep": 1800,
         "ep": {"termArt": terminal}},
        {"type": "insHousing", "stage": "Монтаж в корпус", "opNumber": "ASM", "tOp": 20, "tPrep": 300},
    ]}
    card = ProjectTechcard.create(
        project=project, item_index=index, product_name="Жгут", source_calculation="", deadline=deadline, now=NOW,
    ).rebuild({
        "operations": [
            {"code": "PRS", "connector": "1", "side": "", "type": "prsTerm",
             "fields": {"applicator": "A19", "crimpHeight": height},
             "card": {"tooling": [{"section": "Оборудование", "inventory_number": "ОБ.027.X", "name": "Пресс"}]}},
            {"code": "ASM", "connector": "2", "side": "", "type": "insHousing", "connector_article": "XP-6"},
        ],
        "build_config": config, "kit": {"part_quantity": part_quantity},
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def launch(client: TestClient, index: str, *, project: str = "708К", terminal: str = "T-1",
           height: str = "1,14 - 1,24", deadline: str = "2026-10-02", quantity: int = 50) -> dict:
    """Выданная техкарта и запущенное по ней задание."""
    card = issue(index, project=project, terminal=terminal, height=height)
    response = client.post(
        "/api/production/tasks", headers={"Idempotency-Key": f"autoplan-{(project + index).encode().hex()}"},
        json={"techcard_id": str(card.id), "quantity": quantity, "deadline": deadline},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def preview(client: TestClient, **extra) -> dict:
    response = client.post("/api/production/autoplan/preview", json=extra)
    assert response.status_code == 200, response.text
    return response.json()


def apply(client: TestClient, planned: dict, **extra):
    return client.post("/api/production/autoplan/apply", json={
        "fingerprint": planned["fingerprint"],
        "items": [{key: item[key] for key in ("task_id", "operation_id", "operator_id")} for item in planned["proposals"]],
        **extra,
    })


def tasks_of(client: TestClient) -> dict[str, dict]:
    return {task["id"]: task for task in client.get("/api/production/tasks").json()["tasks"]}


def test_preview_plans_every_task_skips_zhuk_and_saves_no_changeover_while_the_plan_has_no_setup(client):
    """Since 18.10 the plan counts no setup (the operator starts a task only once everything is ready), so one
    terminal on one fitter saves nothing: there is no changeover to save, and the load is spread over the fitters."""
    http, _ = client
    tasks = [launch(http, index, height=height) for index, height in (("КБ1", "1,14 - 1,24"), ("КБ2", "1,14 - 1,24"), ("КБ3", "0,80 - 0,90"))]
    handed = launch(http, "КБ4")
    assert http.post(f"/api/production/tasks/{handed['id']}/handover", json={"version": handed["version"], "site": "ЖУК"}).status_code == 200

    planned = preview(http)

    crimps = [item for item in planned["proposals"] if item["code"] == "PRS"]
    assert {item["task_id"] for item in planned["proposals"]} == {task["id"] for task in tasks}
    assert len(planned["proposals"]) == 6 and planned["unplaced"] == []
    assert {item["operator_id"] for item in crimps} <= {str(ANNA), str(BORIS)}
    assert [item["setup_seconds"] for item in planned["proposals"]] == [0] * 6
    assert [item["changeover_seconds"] for item in planned["proposals"]] == [0] * 6
    assert all(item["operator_id"] != str(ZHUK) for item in planned["proposals"])
    assert crimps[0]["machine"] == "ОБ.027 Пресс"
    assert crimps[0]["setup_label"].startswith("Опрессовка терминалов: терминал T-1, аппликатор A19")
    assert planned["stats"] == {"changeovers": 0, "changeovers_naive": 0, "saved_seconds": 0}
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
    # Период не выбирают: от сегодня до срока задания (02.10), режим — весь цех.
    assert (events[0]["date_from"], events[0]["date_to"], events[0]["master"]) == ("2026-09-22", "2026-10-02", "Мастер")
    assert events[0]["mode"] == "shop" and events[0]["created"] == []


def test_whole_shop_replaces_old_autoplan_and_keeps_manual_assignments(client):
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

    again = preview(http)

    # «Весь цех» пересчитывает всё, что ставил автоплан; ручное назначение и его время не трогает.
    autoplanned = {item["operation_id"] for item in planned["proposals"]}
    assert {item["operation_id"] for item in again["proposals"]} == autoplanned
    assert apply(http, again).status_code == 200
    stored = tasks_of(http)[manual["id"]]
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


def _master_site(sessions, site: str | None) -> None:
    with sessions() as session:
        session.get(IdentityUserRow, MASTER).site = site
        session.commit()


def test_zhuk_master_plans_zhuk_people_like_tsar(client):
    # Владелец 29.10: «ЖУК — показать и планировать как ЦАР»; мастер ЖУК видит и ставит только своих.
    http, sessions = client
    task = launch(http, "КБ1")
    _master_site(sessions, "ЖУК")

    context = http.post("/api/production/autoplan/context", json={}).json()
    planned = preview(http)
    other = http.post("/api/production/autoplan/preview", json={"site": "ЦАР"})
    applied = apply(http, planned)

    assert (context["site"], context["sites"]) == ("ЖУК", ["ЖУК"])
    assert [item["name"] for item in context["fitters"]] == ["Антон"]
    assert {item["operator_id"] for item in planned["proposals"]} == {str(ZHUK)} and len(planned["proposals"]) == 2
    assert other.status_code == 403 and "ЖУК" in other.json()["detail"], other.text
    assert applied.status_code == 200, applied.text
    assert [op["operator_ids"] for op in tasks_of(http)[task["id"]]["operations"]] == [[str(ZHUK)], [str(ZHUK)]]


def test_master_of_a_site_outside_the_shop_is_refused(client):
    http, sessions = client
    launch(http, "КБ1")
    _master_site(sessions, "АДМ")

    response = http.post("/api/production/autoplan/preview", json={})

    assert response.status_code == 403, response.text
    assert "ЦАР, ЖУК" in response.json()["detail"]


def test_director_chooses_a_site_or_plans_both_and_picks_zhuk_people_for_a_project(client):
    http, sessions = client
    launch(http, "КБ1")
    _master_site(sessions, None)

    both = http.post("/api/production/autoplan/context", json={}).json()
    zhuk = http.post("/api/production/autoplan/context", json={"site": "ЖУК"}).json()
    unknown = http.post("/api/production/autoplan/preview", json={"site": "МСК"})
    project = preview(http, mode="project", project="708К", people=[str(ZHUK), str(ANNA)])
    zhuk_only = preview(http, mode="project", project="708К", people=[str(ZHUK)], site="ЖУК")
    stranger = http.post("/api/production/autoplan/preview", json={"mode": "project", "project": "708К", "people": [str(ANNA)], "site": "ЖУК"})

    assert (both["site"], both["sites"]) == ("", ["ЦАР", "ЖУК"])
    assert {item["name"]: item["site"] for item in both["fitters"]} == {"Анна": "ЦАР", "Борис": "ЦАР", "Антон": "ЖУК"}
    assert [item["name"] for item in zhuk["fitters"]] == ["Антон"]
    assert unknown.status_code == 422, unknown.text
    assert {item["operator_id"] for item in project["proposals"]} <= {str(ZHUK), str(ANNA)} and project["unplaced"] == []
    assert {item["operator_id"] for item in zhuk_only["proposals"]} == {str(ZHUK)}
    assert stranger.status_code == 422 and "монтажников ЖУК" in stranger.json()["detail"], stranger.text


def test_zhuk_autoplan_does_not_move_work_autoplanned_on_tsar_people(client):
    http, sessions = client
    task = launch(http, "КБ1")
    assert apply(http, preview(http)).status_code == 200  # мастер ЦАР: работа на Анне / Борисе
    _master_site(sessions, "ЖУК")

    planned = preview(http)

    # Автоплан ЦАР не начат и переставляем, но стоит на людях ЦАР — автоплан мастера ЖУК его не забирает.
    assert planned["proposals"] == []
    assert {op["operator_ids"][0] for op in tasks_of(http)[task["id"]]["operations"]} <= {str(ANNA), str(BORIS)}


def test_horizon_needs_no_dates_and_an_explicit_end_is_validated(client):
    http, _ = client
    launch(http, "КБ1", deadline="2026-10-09")

    planned = preview(http)
    short = preview(http, date_to="2026-09-23")
    past = http.post("/api/production/autoplan/preview", json={"date_to": "2026-09-01"})
    far = http.post("/api/production/autoplan/preview", json={"date_to": "2026-12-31"})

    assert planned["period"]["start"].startswith("2026-09-22T09:00")
    assert planned["period"]["last_day"] == "2026-10-09"
    assert short["period"]["last_day"] == "2026-09-23"
    assert past.status_code == 422 and far.status_code == 422


def test_excluded_task_is_not_planned_and_exclusion_is_in_the_fingerprint(client):
    http, _ = client
    first, second = launch(http, "КБ1"), launch(http, "КБ2")

    planned = preview(http, exclude=[second["id"]])

    assert {item["task_id"] for item in planned["proposals"]} == {first["id"]}
    assert len(planned["proposals"]) == 2
    assert planned["scope"] == {"tasks": 1, "new": 0, "fitters": 2}
    assert date.fromisoformat(planned["proposals"][0]["start"][:10]) == NOW.date()
    # Отпечаток помнит исключения: применить без них — данные «другие».
    assert apply(http, planned).status_code == 409
    applied = apply(http, planned, exclude=[second["id"]])
    assert applied.status_code == 200, applied.text
    assert [item["id"] for item in applied.json()["tasks"]] == [first["id"]]
    assert second["id"] in {item["task_id"] for item in preview(http)["proposals"]}


def test_preview_takes_issued_kb_without_task_and_writes_nothing(client):
    http, _ = client
    launched = launch(http, "КБ1")
    card = issue("КБ2", deadline=date(2026, 9, 30))

    planned = preview(http)

    new = [item for item in planned["proposals"] if item["new"]]
    assert len(new) == 2 and {item["task_number"] for item in new} == {"Новое · 708К КБ2"}
    assert planned["new_tasks"][0]["techcard_id"] == str(card.id) and planned["new_tasks"][0]["quantity"] == 10
    assert planned["scope"] == {"tasks": 1, "new": 1, "fitters": 2}
    assert launched["id"] in {item["task_id"] for item in planned["proposals"]}
    # Предпросмотр ничего не пишет: задания нет, КБ по-прежнему к запуску.
    assert list(tasks_of(http)) == [launched["id"]]
    ready = {item["index"]: item for item in http.get("/api/production/launchable").json()["items"]}
    assert ready["КБ2"]["launched"] == 0


def test_apply_creates_the_task_and_its_schedule_in_one_commit(client):
    http, sessions = client
    card = issue("КБ2", deadline=date(2026, 9, 30))
    planned = preview(http)

    response = apply(http, planned)

    assert response.status_code == 200, response.text
    created = response.json()["created"]
    assert len(created) == 1 and created[0]["number"].startswith("ПЗ-")
    task = tasks_of(http)[created[0]["id"]]
    assert (task["quantity"], task["deadline"], task["version"]) == (10, "2026-09-30", 2)
    assert task["techcard_id"] == str(card.id)
    assert all(operation["assigned_by"] == "Автоплан · Мастер" and operation["operator_ids"] for operation in task["operations"])
    ready = {item["index"]: item for item in http.get("/api/production/launchable").json()["items"]}
    assert ready["КБ2"]["launched"] == 10
    assert preview(http)["new_tasks"] == []
    with sessions() as session:
        launched = [row.details for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "production.launched"))]
        applied = [row.details for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "production.autoplan_applied"))]
    assert launched == [{"number": created[0]["number"]}]
    assert applied[0]["created"] == [created[0]["number"]]


def test_failed_schedule_leaves_no_task_behind(client, monkeypatch):
    http, _ = client
    issue("КБ2")
    planned = preview(http)

    def broken(self, assignments, assigned_by):
        raise ProductionValidationError("сбой расписания")

    monkeypatch.setattr(ProductionTask, "apply_schedule", broken)
    response = apply(http, planned)

    assert response.status_code == 422, response.text
    assert tasks_of(http) == {}


def test_kb_launched_after_preview_is_refused(client):
    http, _ = client
    card = issue("КБ2")
    planned = preview(http)
    launched = http.post(
        "/api/production/tasks", headers={"Idempotency-Key": "by-hand"},
        json={"techcard_id": str(card.id), "quantity": 10},
    )
    assert launched.status_code == 200, launched.text

    response = apply(http, planned)

    assert response.status_code == 409, response.text
    assert len(tasks_of(http)) == 1


def test_kb_without_run_goes_to_unplaced(client):
    http, _ = client
    issue("КБ3", part_quantity="")

    planned = preview(http)

    assert planned["proposals"] == [] and planned["new_tasks"] == []
    assert planned["unplaced"] == [{
        "operation_id": None, "task_id": None, "techcard_id": planned["unplaced"][0]["techcard_id"],
        "task_number": "708К КБ3", "position": None,
        "reason": "Нет тиража КБ — задайте тираж в наборе проекта",
    }]


def test_project_mode_plans_only_that_project_on_the_chosen_people(client):
    http, _ = client
    own = launch(http, "КБ1")
    other = launch(http, "КБ1", project="815Р")
    shop = preview(http)
    assert apply(http, shop).status_code == 200
    before = tasks_of(http)[other["id"]]
    issue("КБ2")

    planned = preview(http, mode="project", project="708К", people=[str(BORIS)])

    assert {item["task_id"] for item in planned["proposals"] if not item["new"]} == {own["id"]}
    assert {item["task_number"] for item in planned["proposals"] if item["new"]} == {"Новое · 708К КБ2"}
    assert {item["operator_id"] for item in planned["proposals"]} == {str(BORIS)}
    assert planned["scope"] == {"tasks": 1, "new": 1, "fitters": 1}
    # Люди и проект — в отпечатке: другой набор людей при применении — «данные изменились».
    assert apply(http, planned, mode="project", project="708К", people=[str(ANNA)]).status_code == 409
    response = apply(http, planned, mode="project", project="708К", people=[str(BORIS)])
    assert response.status_code == 200, response.text
    after = tasks_of(http)
    assert after[other["id"]] == before
    assert all(operation["operator_ids"] == [str(BORIS)] for operation in after[own["id"]]["operations"])


def test_project_mode_leaves_out_unticked_kb_and_exclusion_is_in_the_fingerprint(client):
    http, _ = client
    kept, dropped = launch(http, "КБ1"), launch(http, "КБ2")
    card = issue("КБ3")
    other = launch(http, "КБ1", project="815Р")
    scope = {"mode": "project", "project": "708К", "people": [str(BORIS)]}

    whole = preview(http, **scope)
    planned = preview(http, **scope, exclude=[dropped["id"], str(card.id)])

    assert {item["task_id"] for item in whole["proposals"] if not item["new"]} == {kept["id"], dropped["id"]}
    assert {item["task_id"] for item in planned["proposals"]} == {kept["id"]}
    assert planned["new_tasks"] == [] and planned["scope"] == {"tasks": 1, "new": 0, "fitters": 1}
    assert planned["fingerprint"] != whole["fingerprint"]
    # Применение с другим набором снятых КБ (или без него) — «данные изменились».
    assert apply(http, planned, **scope).status_code == 409
    assert apply(http, planned, **scope, exclude=[dropped["id"]]).status_code == 409
    response = apply(http, planned, **scope, exclude=[str(card.id), dropped["id"]])
    assert response.status_code == 200, response.text
    assert response.json()["created"] == []
    after = tasks_of(http)
    assert set(after) == {kept["id"], dropped["id"], other["id"]}
    assert all(operation["operator_ids"] == [str(BORIS)] for operation in after[kept["id"]]["operations"])
    assert all(not operation["operator_ids"] for operation in after[dropped["id"]]["operations"])
    assert all(not operation["operator_ids"] for operation in after[other["id"]]["operations"])


def test_project_mode_needs_project_and_fitters_of_the_site(client):
    http, _ = client
    launch(http, "КБ1")

    no_people = http.post("/api/production/autoplan/preview", json={"mode": "project", "project": "708К"})
    stranger = http.post("/api/production/autoplan/preview", json={"mode": "project", "project": "708К", "people": [str(ZHUK)]})
    no_project = http.post("/api/production/autoplan/preview", json={"mode": "project", "people": [str(ANNA)]})

    assert no_people.status_code == 422 and stranger.status_code == 422 and no_project.status_code == 422


def test_context_lists_work_projects_and_free_hours(client):
    http, _ = client
    launch(http, "КБ1", deadline="2026-09-25")
    issue("КБ2", project="815Р", deadline=date(2026, 9, 24))
    issue("КБ3", part_quantity="")

    response = http.post("/api/production/autoplan/context", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["project"] for row in body["projects"]] == ["815Р", "708К"]
    assert body["projects"][0]["deadline"] == "2026-09-24" and body["projects"][0]["new"] == 1
    assert body["projects"][1]["new"] == 0  # КБ3 без тиража ставить нечего
    assert {item["index"]: item["skipped"] for item in body["new"]} == {
        "КБ2": "", "КБ3": "Нет тиража КБ — задайте тираж в наборе проекта",
    }
    assert body["period"] == {"start": "2026-09-22T09:00:00", "last_day": "2026-09-25", "auto": True}
    fitters = {item["name"]: item for item in body["fitters"]}
    assert set(fitters) == {"Анна", "Борис"} and fitters["Анна"]["free_seconds"] > 0
    project = http.post("/api/production/autoplan/context", json={"mode": "project", "project": "815Р"}).json()
    assert project["tasks"] == [] and [item["index"] for item in project["new"]] == ["КБ2"]
    assert project["period"]["last_day"] == "2026-09-24"
