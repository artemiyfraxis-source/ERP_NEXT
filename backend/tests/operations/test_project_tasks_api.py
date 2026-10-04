"""GET /api/project-tasks и POST /api/project-tasks/{id}/close: задачи арбитра (ARCH-TRACK-3, C2) и права на них."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import SECTION_LABELS
from app.modules.operations.domain import ProjectTask, ProjectTaskError
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 4, 12, 0)


@pytest.fixture()
def factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield lambda: SqlAlchemyOperationsUnitOfWork(sessions)
    engine.dispose()


def add_task(factory, *, project="708К.4", item="КБ1", module="production", kind="launch_task",
             title="Задание можно запускать: 708К.4 КБ1", created_at=NOW) -> ProjectTask:
    task = ProjectTask.create(
        project=project, item_index=item, module=module, kind=kind, title=title, source_event_id=uuid4(),
        source_type="fulfillment.issued", subject_id=uuid4(), created_at=created_at,
    )
    with factory() as uow:
        uow.project_tasks.add(task)
        uow.commit()
    return task


def principal(roles, permissions, name="Сотрудник"):
    return lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000002"), name=name, department="Цех"),
        email="worker@example.test", roles=frozenset(roles), permissions=permissions,
    )


MASTER = principal({"master"}, {"production": "edit"}, "Мастер")
STAFF = principal({"staff"}, {"production": "view"})
PM = principal({"pm"}, {"shipping": "edit", "projects": "view"}, "Менеджер")
PRODHEAD = principal({"prodhead"}, {section: "edit" for section in SECTION_LABELS}, "Начальник производства")


@pytest.fixture()
def client(factory):
    app.state.operations_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = MASTER
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    del app.state.operations_uow_factory


def as_(who):
    app.dependency_overrides[authenticated_principal] = who


def titles(response):
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["tasks"]]


def test_list_filters_by_project_module_and_status(client, factory):
    launch = add_task(factory, created_at=NOW.replace(hour=9))
    add_task(factory, project="708К.41", title="Соседний проект", created_at=NOW.replace(hour=10))
    ship = add_task(factory, module="shipping", kind="ship_goods", title="Оформить отгрузку: 708К.4 КБ1",
                    created_at=NOW.replace(hour=11))
    done = add_task(factory, item="КБ2", title="Поставить в план: 708К.4 КБ2", kind="plan_item")
    with factory() as uow:
        uow.project_tasks.save(done.close(NOW, "Мастер"))
        uow.commit()
    as_(PRODHEAD)

    assert titles(client.get("/api/project-tasks", params={"project": "708K.4"})) == [
        launch.title, ship.title, done.title,
    ]
    assert titles(client.get("/api/project-tasks", params={"project": "708К.4", "status": "open"})) == [
        launch.title, ship.title,
    ]
    assert titles(client.get("/api/project-tasks", params={"module": "shipping"})) == [ship.title]
    assert client.get("/api/project-tasks", params={"status": "maybe"}).status_code == 422

    body = client.get("/api/project-tasks", params={"project": "708К.4", "status": "done"}).json()["tasks"][0]
    assert body == {
        "id": str(done.id), "project": "708К.4", "item_index": "КБ2", "module": "production",
        "module_label": "Производство", "kind": "plan_item", "title": done.title, "status": "done",
        "status_label": "Закрыта", "created_at": NOW.isoformat(), "closed_at": NOW.isoformat(), "closed_by": "Мастер",
        "source_type": "fulfillment.issued", "can_close": False,
    }


def test_a_task_is_seen_from_its_section_or_from_projects(client, factory):
    add_task(factory, created_at=NOW.replace(hour=9))
    add_task(factory, module="shipping", kind="ship_goods", title="Оформить отгрузку: 708К.4 КБ1")

    as_(MASTER)
    assert titles(client.get("/api/project-tasks")) == ["Задание можно запускать: 708К.4 КБ1"]
    as_(PM)  # «Проекты» на просмотр: карточка проекта видит всё по проекту
    assert titles(client.get("/api/project-tasks")) == [
        "Задание можно запускать: 708К.4 КБ1", "Оформить отгрузку: 708К.4 КБ1",
    ]
    assert [item["can_close"] for item in client.get("/api/project-tasks").json()["tasks"]] == [False, True]
    as_(principal({"staff"}, {}))
    assert titles(client.get("/api/project-tasks")) == []


def test_close_needs_the_edit_role_of_the_tasks_section(client, factory):
    task = add_task(factory)

    for who in (STAFF, PM, principal({"staff"}, {"production": "edit"})):
        as_(who)
        assert client.post(f"/api/project-tasks/{task.id}/close").status_code == 403
    with factory() as uow:
        assert uow.project_tasks.get(task.id).is_open

    as_(MASTER)
    response = client.post(f"/api/project-tasks/{task.id}/close")

    assert response.status_code == 200, response.text
    body = response.json()["task"]
    assert (body["status"], body["status_label"], body["closed_by"], body["can_close"]) == (
        "done", "Закрыта", "Мастер", False,
    )
    with factory() as uow:
        assert uow.project_tasks.get(task.id).status.value == "done"
        event = uow.session.execute(
            select(AuditEventRow.event_type, AuditEventRow.actor_name, AuditEventRow.details)
            .where(AuditEventRow.aggregate_id == task.id)
        ).one()
    assert tuple(event) == (
        "operations.project_task_closed", "Мастер", {"kind": "launch_task", "project": "708К.4", "item_index": "КБ1"},
    )


def test_full_access_closes_any_section_and_a_closed_task_stays_closed(client, factory):
    task = add_task(factory, module="shipping", kind="ship_goods", title="Оформить отгрузку: 708К.4 КБ1")
    as_(PRODHEAD)

    assert client.post(f"/api/project-tasks/{task.id}/close").status_code == 200
    again = client.post(f"/api/project-tasks/{task.id}/close")

    assert again.status_code == 409 and again.json()["detail"] == "Задача уже закрыта"
    assert client.post(f"/api/project-tasks/{uuid4()}/close").status_code == 404


def test_a_task_closes_once():
    task = ProjectTask.create(
        project="708К.4", item_index=" кб1 ", module="production", kind="plan_item", title="Поставить в план",
        source_event_id=uuid4(), source_type="techcards.project_issued", subject_id=None, created_at=NOW,
    )
    assert (task.item_index, task.status_label) == ("КБ1", "Открыта")

    closed = task.close(NOW, " Мастер ")

    assert (closed.closed_by, closed.closed_at, closed.status_label) == ("Мастер", NOW, "Закрыта")
    with pytest.raises(ProjectTaskError):
        closed.close(NOW, "Мастер")
    with pytest.raises(ProjectTaskError):
        ProjectTask.create(
            project=" ", item_index="", module="production", kind="plan_item", title="x", source_event_id=uuid4(),
            source_type="x", subject_id=None, created_at=NOW,
        )
