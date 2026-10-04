"""«Личная карточка» (Phase 1, T2): сведения о человеке из всех модулей — только чтение через порты модулей."""

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
from app.modules.identity.domain import ROLE_SEED, AccessMode, IdentityUser, hash_password
from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork, provision_user, seed_role_matrix
from app.modules.operations.domain import ProjectTask
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.modules.production.application import Absence, LaunchProductionCommand, launch_production
from app.modules.production.domain import OperatorSkill, ProductionOperation, ProductionStatus
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.plan import PlanProjectSnapshot
from app.modules.production.traceability import ProductionOutput, WorkSession, WorkStatus
from app.modules.requests.application import CreateRequestsCommand, create_requests
from app.modules.requests.domain import RequestDraft, RequestStatus
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork as SqlAlchemyRequestsUnitOfWork
from app.shared.clock import moscow_now
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.employee_card import LIST_LIMIT, employee_card
from tests.factories import Actor

NOW = datetime(2026, 10, 14, 12, 0)  # по Москве: срез месяца — октябрь 2026
PASSWORD = "card-password-1"

ADMIN = UUID("60000000-0000-0000-0000-000000000001")
MASTER = UUID("60000000-0000-0000-0000-000000000002")
FITTER = UUID("60000000-0000-0000-0000-000000000003")
FITTER_2 = UUID("60000000-0000-0000-0000-000000000004")
FITTER_ZHUK = UUID("60000000-0000-0000-0000-000000000005")
FITTER_GONE = UUID("60000000-0000-0000-0000-000000000006")
VIEWER = UUID("60000000-0000-0000-0000-000000000007")
MASTER_NO_SITE = UUID("60000000-0000-0000-0000-000000000008")
UNKNOWN = UUID("60000000-0000-0000-0000-0000000000ff")

PROJECT = "708К.4"
NEIGHBOUR = "708К.41"


def _user(user_id, login, name, role, department="", site=None, active=True):
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=name, department=department,
        role=role, site=site, active=active,
    )


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    password = hash_password(PASSWORD)
    people = (
        (_user(ADMIN, "admin", "Технолог Иван", "admin", "ИТ"), {}),
        (_user(MASTER, "master", "Мастер Пётр", "master", "Цех", "ЦАР"), {}),
        (_user(MASTER_NO_SITE, "master2", "Мастер без площадки", "master", "Цех"), {}),
        (_user(FITTER, "fitter", "Монтажник Анна", "staff", "Монтаж", "ЦАР"), {}),
        (_user(FITTER_2, "fitter2", "Монтажник Борис", "staff", "Монтаж", "ЦАР"), {}),
        (_user(FITTER_ZHUK, "fitter3", "Монтажник Жук", "staff", "Монтаж", "ЖУК"), {}),
        (_user(FITTER_GONE, "fitter4", "Монтажник Уволенный", "staff", "Монтаж", "ЦАР", active=False), {}),
        (_user(VIEWER, "viewer", "Кадровик", "staff", "Офис"), {"staffdir": AccessMode.VIEW}),
    )
    with sessions() as session:
        for user, personal in people:
            provision_user(session, user, password, role_access=ROLE_SEED.get(user.role, {}), user_access=personal)
        seed_role_matrix(session)
        session.commit()
    yield SimpleNamespace(
        sessions=sessions,
        identity=lambda: SqlAlchemyIdentityUnitOfWork(sessions),
        production=lambda: SqlAlchemyProductionUnitOfWork(sessions),
        requests=lambda: SqlAlchemyRequestsUnitOfWork(sessions),
        operations=lambda: SqlAlchemyOperationsUnitOfWork(sessions),
    )
    engine.dispose()


def card(erp, user_id, **omit):
    factories = {name: getattr(erp, name) for name in ("production", "requests", "operations") if name not in omit}
    return employee_card(user_id, NOW, identity=erp.identity, **factories)


# ── Что лежит в модулях ──────────────────────────────────────────────────


def utc(month: int, day: int, hour: int, minute: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def launch(erp, project, operators, *, number=1, deadline=None, cancelled=False):
    operation = ProductionOperation.create(
        position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
        seconds_per_unit=Decimal("60"), setup_seconds=Decimal("0"), quantity=2,
    )
    task = launch_production(LaunchProductionCommand(
        idempotency_key=f"card:{project}:{number}", project=project, item="КБ1", name="Жгут", quantity=2,
        fulfillment_request_id=None, deadline=deadline, actor=Actor(name="Мастер", department="Цех"), now=NOW,
        operations=(replace(operation, operator_ids=tuple(operators)),),
    ), erp.production())
    if cancelled:
        with erp.production() as uow:
            uow.production.add(replace(task, status=ProductionStatus.CANCELLED))
            uow.commit()
    return task


def worked(erp, task, operator_id, started, finished, pause=0, *, session=True):
    """Выработка по закрытой сессии (`session=False` — исправление выработки: сессии нет, время нулевое)."""
    session_id = uuid4()
    with erp.production() as uow:
        if session:
            uow.work.save(WorkSession(
                id=session_id, task_id=task.id, operation_id=task.operations[0].id, operator_id=operator_id,
                started_at=started, status=WorkStatus.COMPLETED, pause_seconds=pause,
            ))
        uow.outputs.add(ProductionOutput(
            id=uuid4(), session_id=session_id if session else None, task_id=task.id,
            operation_id=task.operations[0].id, operator_id=operator_id, started_at=started, finished_at=finished,
            pause_seconds=pause, good_quantity=Decimal("1"), defect_quantity=Decimal("0"), output_box_id=uuid4(),
        ))
        uow.commit()


def plan(erp, *snapshots):
    with erp.production() as uow:
        uow.plan_projects.replace(tuple(replace(item, imported_at=NOW) for item in snapshots))
        uow.commit()


def file_requests(erp, actor_id, count=1, *, project=PROJECT, purpose="RND", key="card", name="Разъём"):
    drafts = tuple(RequestDraft(
        purpose=purpose, project_reference=project, delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
        name=f"{name} {index}", item_type="Комплектующие", quantity=Decimal("2"), unit="шт", quality_document="Нет",
        procurement_deadline=date(2026, 10, 20), planned_date=date(2026, 10, 25),
    ) for index in range(count))
    return create_requests(CreateRequestsCommand(
        creation_key=f"{key}:{uuid4()}", drafts=drafts, actor=Actor(name="Инициатор", department="Цех", id=actor_id),
        now=datetime(2026, 10, 10, 9, 0),
    ), erp.requests())


def set_status(erp, request, status):
    with erp.requests() as uow:
        uow.requests.add(replace(request, status=status))
        uow.commit()


def project_task(erp, module, *, title="Поставить в план", status_open=True):
    task = ProjectTask.create(
        project=PROJECT, item_index="КБ1", module=module, kind="plan_item", title=title, source_event_id=uuid4(),
        source_type="test.event", subject_id=None, created_at=datetime(2026, 10, 9, 9, 0),
    )
    with erp.operations() as uow:
        uow.project_tasks.add(task if status_open else task.close(NOW, "Арбитр ERP"))
        uow.commit()
    return task


# ── Шапка ────────────────────────────────────────────────────────────────


def test_header_shows_person_roles_site_and_activity(erp):
    body = card(erp, MASTER)

    assert body["header"] == {
        "id": str(MASTER), "name": "Мастер Пётр", "department": "Цех",
        "roles": [{"key": "master", "label": "Мастер"}], "site": "ЦАР", "site_label": "Москва", "active": True,
    }
    assert body["as_of"] == "2026-10-14"


def test_header_without_site_and_department_is_null_not_blank(erp):
    bare = uuid4()
    with erp.sessions() as session:
        provision_user(session, _user(bare, "bare", "Без отдела", "estimator"), hash_password(PASSWORD))
        session.commit()

    header = employee_card(bare, NOW, identity=erp.identity)["header"]

    assert header["department"] is None
    assert header["site"] is None and header["site_label"] is None
    assert header["roles"] == [{"key": "estimator", "label": "Сметчик"}]


def test_unknown_person_has_no_card(erp):
    assert card(erp, UNKNOWN) is None


def test_inactive_person_still_has_a_card(erp):
    assert card(erp, FITTER_GONE)["header"]["active"] is False


# ── Подчинённые ──────────────────────────────────────────────────────────


def test_master_sees_the_fitters_of_his_site_only(erp):
    body = card(erp, MASTER)

    assert [item["name"] for item in body["subordinates"]] == ["Монтажник Анна", "Монтажник Борис"]
    assert body["subordinates"][0] == {
        "id": str(FITTER), "name": "Монтажник Анна", "site": "ЦАР", "department": "Монтаж",
    }


def test_master_without_a_site_sees_every_fitter_but_not_the_dismissed_or_himself(erp):
    names = [item["name"] for item in card(erp, MASTER_NO_SITE)["subordinates"]]

    assert names == ["Монтажник Анна", "Монтажник Борис", "Монтажник Жук"]


def test_other_roles_have_no_subordinates(erp):
    assert card(erp, FITTER)["subordinates"] == []
    assert card(erp, ADMIN)["subordinates"] == []


# ── Проекты ──────────────────────────────────────────────────────────────


def test_projects_come_from_operations_and_requests_with_the_plan_deadline(erp):
    launch(erp, PROJECT, [FITTER], number=1)
    launch(erp, "500К.1", [FITTER, FITTER_2], number=2)
    launch(erp, "600К.1", [FITTER_2], number=3)  # не его
    file_requests(erp, FITTER, project="700К.2")
    plan(
        erp, PlanProjectSnapshot(project=PROJECT, deadline=date(2026, 10, 20), status="В работе"),
        PlanProjectSnapshot(project="500К.1", status="В работе"),
    )

    projects = card(erp, FITTER)["projects"]

    assert projects == [
        {"project": PROJECT, "roles": ["Исполнитель"], "deadline": "2026-10-20"},
        {"project": "500К.1", "roles": ["Исполнитель"], "deadline": None},
        {"project": "700К.2", "roles": ["Инициатор заявок"], "deadline": None},
    ]


def test_a_project_with_both_roles_is_one_row(erp):
    launch(erp, PROJECT, [FITTER])
    file_requests(erp, FITTER, 3)

    assert card(erp, FITTER)["projects"] == [
        {"project": PROJECT, "roles": ["Исполнитель", "Инициатор заявок"], "deadline": None},
    ]


def test_latin_k_and_a_neighbour_project_do_not_mix(erp):
    launch(erp, "708K.4", [FITTER])  # латинская K — тот же проект
    launch(erp, NEIGHBOUR, [FITTER_2], number=2)
    plan(erp, PlanProjectSnapshot(project=PROJECT, deadline=date(2026, 10, 20)))

    assert card(erp, FITTER)["projects"] == [{"project": PROJECT, "roles": ["Исполнитель"], "deadline": "2026-10-20"}]
    assert card(erp, FITTER_2)["projects"] == [{"project": NEIGHBOUR, "roles": ["Исполнитель"], "deadline": None}]


def test_request_project_is_taken_from_the_purpose_when_the_number_is_not_filled(erp):
    # Назначение вида «проект · КБ · имя» приходит с импортом из расчёта; форма принимает только справочник.
    for request in file_requests(erp, FITTER, project=None):
        with erp.requests() as uow:
            uow.requests.add(replace(request, purpose=f"{PROJECT} · КБ1 · Жгут"))
            uow.commit()
    file_requests(erp, FITTER, project=None, key="rnd")  # «RND» — не проект

    assert card(erp, FITTER)["projects"] == [{"project": PROJECT, "roles": ["Инициатор заявок"], "deadline": None}]


def test_cancelled_task_and_cancelled_request_give_no_project(erp):
    launch(erp, PROJECT, [FITTER], cancelled=True)
    for request in file_requests(erp, FITTER, project="700К.2"):
        set_status(erp, request, RequestStatus.CANCELLED)

    assert card(erp, FITTER)["projects"] == []


def test_a_project_closed_in_the_registry_is_not_shown_an_unknown_one_stays(erp):
    launch(erp, PROJECT, [FITTER], number=1)
    launch(erp, "500К.1", [FITTER], number=2)
    launch(erp, "900К.1", [FITTER], number=3)  # в снимке плана его нет — закрыт или нет, неизвестно
    plan(
        erp, PlanProjectSnapshot(project=PROJECT, status="В работе"),
        PlanProjectSnapshot(project="500К.1", status="Закрыт"),
    )

    assert [item["project"] for item in card(erp, FITTER)["projects"]] == [PROJECT, "900К.1"]


def test_projects_are_sorted_by_deadline_undated_last(erp):
    for number, project in enumerate(("300К.1", "100К.1", "200К.1"), start=1):
        launch(erp, project, [FITTER], number=number)
    plan(
        erp, PlanProjectSnapshot(project="300К.1", deadline=date(2026, 10, 15)),
        PlanProjectSnapshot(project="200К.1", deadline=date(2026, 10, 30)),
    )

    assert [item["project"] for item in card(erp, FITTER)["projects"]] == ["300К.1", "200К.1", "100К.1"]


# ── Компетенции и отсутствия ─────────────────────────────────────────────


def test_skills_are_the_persons_own_rows(erp):
    with erp.production() as uow:
        uow.skills.replace_for_operator(FITTER, (
            OperatorSkill(FITTER, "OP-02", False, 0, Decimal("1")),
            OperatorSkill(FITTER, "OP-01", True, 4, Decimal("1.250000")),
        ))
        uow.skills.replace_for_operator(FITTER_2, (OperatorSkill(FITTER_2, "OP-01", True, 5, Decimal("0.8")),))
        uow.commit()

    assert card(erp, FITTER)["skills"] == [
        {"operation": "OP-01", "allowed": True, "stars": 4, "coefficient": "1.25"},
        {"operation": "OP-02", "allowed": False, "stars": 0, "coefficient": "1"},
    ]
    assert card(erp, ADMIN)["skills"] == []


def test_upcoming_absences_and_the_next_vacation(erp):
    with erp.production() as uow:
        uow.calendar.replace(uow.calendar.holidays(), (
            Absence(uuid4(), FITTER, date(2026, 9, 1), date(2026, 9, 14), "Отпуск"),  # уже прошёл
            Absence(uuid4(), FITTER, date(2026, 10, 12), date(2026, 10, 14), "Больничный"),  # идёт сегодня
            Absence(uuid4(), FITTER, date(2026, 12, 1), date(2026, 12, 14), "Отпуск"),
            Absence(uuid4(), FITTER, date(2026, 11, 2), date(2026, 11, 8), "Отпуск"),
            Absence(uuid4(), FITTER_2, date(2026, 10, 20), date(2026, 10, 21), "Отгул"),
        ), uow.calendar.shift())
        uow.commit()

    absences = card(erp, FITTER)["absences"]

    assert absences["upcoming"] == [
        {"kind": "Больничный", "from_date": "2026-10-12", "to_date": "2026-10-14"},
        {"kind": "Отпуск", "from_date": "2026-11-02", "to_date": "2026-11-08"},
        {"kind": "Отпуск", "from_date": "2026-12-01", "to_date": "2026-12-14"},
    ]
    assert absences["next_vacation"] == {"kind": "Отпуск", "from_date": "2026-11-02", "to_date": "2026-11-08"}


def test_no_absences_means_no_next_vacation(erp):
    assert card(erp, FITTER)["absences"] == {"upcoming": [], "next_vacation": None}


# ── Часы за месяц ────────────────────────────────────────────────────────


def test_hours_are_the_finished_sessions_of_this_month_minus_pauses(erp):
    task = launch(erp, PROJECT, [FITTER, FITTER_2])
    worked(erp, task, FITTER, utc(10, 5, 5), utc(10, 5, 9), pause=1800)  # 4 ч − 30 мин
    worked(erp, task, FITTER, utc(10, 6, 5), utc(10, 6, 7))  # 2 ч
    worked(erp, task, FITTER_2, utc(10, 6, 5), utc(10, 6, 12))  # чужая

    hours = card(erp, FITTER)["hours_this_month"]

    assert hours == {"month": "2026-10", "label": "октябрь 2026", "hours": 5.5, "sessions": 2}


def test_the_month_is_counted_in_moscow_time(erp):
    task = launch(erp, PROJECT, [FITTER])
    worked(erp, task, FITTER, utc(9, 30, 18), utc(9, 30, 20, 30))  # 30.09 23:30 по Москве — сентябрь
    worked(erp, task, FITTER, utc(9, 30, 20), utc(9, 30, 21, 30))  # 01.10 00:30 по Москве — октябрь, 1.5 ч
    worked(erp, task, FITTER, utc(10, 31, 20), utc(10, 31, 21, 30))  # 01.11 00:30 по Москве — ноябрь

    hours = card(erp, FITTER)["hours_this_month"]

    assert hours["hours"] == 1.5 and hours["sessions"] == 1


def test_a_correction_is_not_a_session_and_a_cancelled_task_keeps_its_hours(erp):
    task = launch(erp, PROJECT, [FITTER], cancelled=True)
    worked(erp, task, FITTER, utc(10, 5, 5), utc(10, 5, 8))  # 3 ч
    worked(erp, task, FITTER, utc(10, 7, 9), utc(10, 7, 9), session=False)  # исправление: время нулевое

    hours = card(erp, FITTER)["hours_this_month"]

    assert hours["hours"] == 3.0 and hours["sessions"] == 1


def test_no_sessions_this_month_is_no_figure_not_zero_hours(erp):
    task = launch(erp, PROJECT, [FITTER])
    worked(erp, task, FITTER, utc(9, 5, 5), utc(9, 5, 9))  # прошлый месяц

    assert card(erp, FITTER)["hours_this_month"] == {
        "month": "2026-10", "label": "октябрь 2026", "hours": None, "sessions": 0,
    }


# ── Заявки ───────────────────────────────────────────────────────────────


def test_requests_are_the_persons_open_ones_newest_first_ten_of_all(erp):
    mine = file_requests(erp, FITTER, LIST_LIMIT + 2)
    set_status(erp, mine[0], RequestStatus.CLOSED)
    set_status(erp, mine[1], RequestStatus.CANCELLED)
    set_status(erp, mine[2], RequestStatus.IN_WORK)
    file_requests(erp, FITTER_2, 4, key="other")

    requests = card(erp, FITTER)["requests"]

    assert requests["count"] == LIST_LIMIT  # 12 − закрытая − отменённая
    assert len(requests["items"]) == LIST_LIMIT
    assert {item["status"] for item in requests["items"]} == {"Новый", "В работе"}
    assert all(item["name"].startswith("Разъём") and item["number"] for item in requests["items"])
    assert set(requests["items"][0]) == {"id", "number", "name", "status"}
    assert {item["id"] for item in requests["items"]} <= {str(item.id) for item in mine}


def test_a_person_without_requests_has_a_real_zero(erp):
    assert card(erp, ADMIN)["requests"] == {"count": 0, "items": []}


# ── Задачи ───────────────────────────────────────────────────────────────


def test_tasks_are_the_open_ones_in_sections_the_person_edits(erp):
    project_task(erp, "production", title="Поставить в план")
    project_task(erp, "shipping", title="Оформить отгрузку")
    project_task(erp, "production", title="Уже сделана", status_open=False)

    # Мастер правит «production» (ROLE_SEED), отгрузку только смотрит; технолог правит всё.
    master = card(erp, MASTER)["tasks"]
    assert master["count"] == 1
    assert master["items"][0] | {"id": "-"} == {
        "id": "-", "project": PROJECT, "item_index": "КБ1", "module": "production", "module_label": "Производство",
        "kind": "plan_item", "title": "Поставить в план", "created_at": "2026-10-09T09:00:00",
    }
    assert card(erp, ADMIN)["tasks"]["count"] == 2
    assert card(erp, FITTER)["tasks"] == {"count": 0, "items": []}


def test_a_personal_exception_changes_the_sections_of_the_card(erp):
    project_task(erp, "shipping")
    with erp.identity() as uow:
        uow.identity.replace_user_access(FITTER, {"shipping": AccessMode.EDIT})
        uow.commit()

    assert card(erp, FITTER)["tasks"]["count"] == 1


# ── Модуль не подключён ──────────────────────────────────────────────────


def test_missing_modules_leave_their_sections_empty_without_fake_zeros(erp):
    body = card(erp, MASTER, production=True, requests=True, operations=True)

    assert body["header"]["name"] == "Мастер Пётр"
    assert body["subordinates"] == [] and body["projects"] == [] and body["skills"] == []
    assert body["absences"] == {"upcoming": [], "next_vacation": None}
    assert body["hours_this_month"] == {"month": "2026-10", "label": "октябрь 2026", "hours": None, "sessions": None}
    assert body["requests"] == {"count": None, "items": []}
    assert body["tasks"] == {"count": None, "items": []}


def test_each_module_is_optional_on_its_own(erp):
    launch(erp, PROJECT, [FITTER])
    file_requests(erp, FITTER, 2)

    no_production = card(erp, FITTER, production=True)
    assert no_production["projects"] == [{"project": PROJECT, "roles": ["Инициатор заявок"], "deadline": None}]
    assert no_production["requests"]["count"] == 2

    no_requests = card(erp, FITTER, requests=True)
    assert no_requests["projects"] == [{"project": PROJECT, "roles": ["Исполнитель"], "deadline": None}]
    assert no_requests["requests"] == {"count": None, "items": []}


def test_reading_a_card_writes_nothing(erp):
    launch(erp, PROJECT, [FITTER])
    file_requests(erp, FITTER)
    with erp.sessions() as session:
        before = session.scalar(select(func.count()).select_from(AuditEventRow))

    card(erp, FITTER)
    card(erp, MASTER)

    with erp.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == before


# ── HTTP ─────────────────────────────────────────────────────────────────

STATE = {
    "identity_uow_factory": "identity", "production_uow_factory": "production", "uow_factory": "requests",
    "operations_uow_factory": "operations",
}


@pytest.fixture()
def api(erp):
    for name, factory in STATE.items():
        setattr(app.state, name, getattr(erp, factory))
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW

    def login(name: str) -> TestClient:
        client = TestClient(app)
        response = client.post("/api/auth/login", json={"login": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = client.cookies.get("erp_csrf")
        return client

    yield login
    app.dependency_overrides.pop(moscow_now, None)
    for name in (*STATE, "cookie_secure", "identity_clock"):
        if hasattr(app.state, name):
            delattr(app.state, name)


def test_own_card_is_always_open(erp, api):
    launch(erp, PROJECT, [FITTER])
    me = api("fitter")  # рядовой «Сотрудник»: раздела «Сотрудники» у него нет

    response = me.get("/api/employee-card/me")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["header"]["id"] == str(FITTER) and body["header"]["name"] == "Монтажник Анна"
    assert body["projects"] == [{"project": PROJECT, "roles": ["Исполнитель"], "deadline": None}]
    assert body == card(erp, FITTER)


def test_own_card_needs_a_login(api):
    assert TestClient(app).get("/api/employee-card/me").status_code == 401
    assert TestClient(app).get(f"/api/employee-card/{FITTER}").status_code == 401


def test_anothers_card_is_refused_without_the_staffdir_section(erp, api):
    me = api("fitter")

    assert me.get(f"/api/employee-card/{MASTER}").status_code == 403
    # Чужой id не выдаёт, есть такой сотрудник или нет: 403, а не 404.
    assert me.get(f"/api/employee-card/{UNKNOWN}").status_code == 403
    # Свой id по пути тоже «чужая карточка»: свою читают через /me.
    assert me.get(f"/api/employee-card/{FITTER}").status_code == 403


def test_anothers_card_opens_with_view_on_staffdir_and_for_full_access_roles(erp, api):
    launch(erp, PROJECT, [FITTER])

    for login in ("viewer", "admin"):
        response = api(login).get(f"/api/employee-card/{FITTER}")
        assert response.status_code == 200, (login, response.text)
        assert response.json() == card(erp, FITTER)


def test_unknown_person_is_404_for_those_who_may_look(erp, api):
    response = api("admin").get(f"/api/employee-card/{UNKNOWN}")

    assert response.status_code == 404 and response.json()["detail"] == "Сотрудник не найден."


def test_card_path_takes_only_an_id(erp, api):
    assert api("admin").get("/api/employee-card/not-an-id").status_code == 422


def test_api_skips_a_module_that_is_not_configured(erp, api):
    launch(erp, PROJECT, [FITTER])
    delattr(app.state, "production_uow_factory")

    body = api("fitter").get("/api/employee-card/me").json()

    assert body["projects"] == [] and body["hours_this_month"]["hours"] is None
    assert body["requests"]["count"] == 0
