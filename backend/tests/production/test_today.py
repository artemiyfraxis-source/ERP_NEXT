"""«Сегодня» мастера участка: участок экрана, кто что делает, план на сегодня, просроченное, что стоит и почему.
Ничего не пишет."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import Absence
from app.modules.production.domain import OperatorSkill, WorkShift
from app.modules.production.plan import PlanEvent, PlanProjectSnapshot
from app.modules.production.today import (
    STATE_NEW,
    STATE_RUNNING,
    kit_text,
    planned_today,
    resolve_site,
)
from app.modules.production.traceability import WorkSession
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from app.shared.persistence import AuditEventRow

from tests.production.test_api import issue_card, production_api  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 22, 9, 0)  # вторник
OPS = [("PRS_TERMINAL_hand", "Опрессовка терминалов", 30, 0), ("SDR_DB", "Пайка разъёма", 30, 0)]
SECOND = UUID("10000000-0000-0000-0000-000000000003")
AWAY = UUID("10000000-0000-0000-0000-000000000004")
ZHUK = UUID("10000000-0000-0000-0000-000000000005")


# ── Правила ─────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("viewer", "full", "wanted", "expected"), [
    ("ЦАР", False, "ЖУК", ("ЦАР", False)),  # мастер ЦАР видит свой участок, выбор не работает
    ("ЖУК", False, "", ("ЖУК", False)),
    ("ЦАР", True, "ЖУК", ("ЖУК", True)),  # полный доступ выбирает
    ("", False, "ЖУК", ("ЖУК", True)),  # сотрудник без площадки — выбирает
    ("", False, "", ("ЦАР", True)),  # по умолчанию ЦАР
    ("", True, "Склад", ("ЦАР", True)),  # чужое слово — ЦАР
])
def test_site_of_the_screen(viewer, full, wanted, expected):
    assert resolve_site(viewer, full, wanted) == expected


def test_kit_reason_names_the_request_and_its_status():
    assert kit_text(None) == "Комплект КБ не набран: запроса на комплектацию нет"
    assert kit_text("Недоукомплектован", "ВЫД-26-0007") == "Комплект КБ не набран: запрос ВЫД-26-0007 — «Недоукомплектован»"


def test_plan_of_today_takes_windows_touching_the_day_and_started_leftovers():
    class Line:
        def __init__(self, name, start, finish, started=False, finished=False):
            self.name, self.planned_start, self.planned_finish = name, start, finish
            self.started, self.finished = started, finished
            self.task = type("T", (), {"number": name})()
            self.operation = type("O", (), {"position": 1})()

    day = datetime(2026, 9, 22)
    end = day + timedelta(days=1)
    lines = [
        Line("сегодня", day.replace(hour=10), day.replace(hour=11)),
        Line("через полночь", day - timedelta(hours=2), day + timedelta(hours=10)),
        Line("вчера, начата", day - timedelta(hours=8), day - timedelta(hours=6), started=True),
        Line("вчера, не начата", day - timedelta(hours=8), day - timedelta(hours=6)),  # только в «Просрочено»
        Line("вчера, сдана", day - timedelta(hours=8), day - timedelta(hours=6), started=True, finished=True),
        Line("завтра", end + timedelta(hours=9), end + timedelta(hours=10)),
    ]

    assert [line.name for line in planned_today(lines, day, end)] == ["вчера, начата", "через полночь", "сегодня"]


# ── Через HTTP ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _staff():
    with app.state.production_uow_factory() as uow:
        uow.session.add_all([
            IdentityUserRow(id=SECOND, login="second", email="second@example.test", name="Байков", department="Производство",
                            role="staff", site="ЦАР", active=True, shop_worker=True),
            IdentityUserRow(id=AWAY, login="away", email="away@example.test", name="Яковлева", department="Производство",
                            role="staff", site="", active=True, shop_worker=True),  # без площадки — ЦАР
            IdentityUserRow(id=ZHUK, login="zhuk", email="zhuk@example.test", name="Зингер", department="Производство",
                            role="staff", site="ЖУК", active=True, shop_worker=True),
        ])
        uow.calendar.replace(frozenset(), (Absence(uuid4(), AWAY, date(2026, 9, 21), date(2026, 9, 25), "Отпуск"),), WorkShift())
        uow.commit()


def _launch(client, key, project, index, quantity, **extra):
    card = issue_card(project, index, OPS)
    response = client.post("/api/production/tasks", headers={"Idempotency-Key": key},
                           json={"techcard_id": str(card.id), "quantity": quantity, **extra})
    assert response.status_code == 200, response.text
    return response.json()["task"]


def _assign(client, task, position, operator_id, start=NOW):
    operation = task["operations"][position - 1]
    response = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)], "requested_start": start.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def test_today_shows_people_plan_and_what_cannot_start(production_api):  # noqa: F811
    client, operator_id = production_api
    _staff()
    task = _launch(client, "today-1", "801К.1", "КБ1", 10)
    task = _assign(client, task, 1, operator_id)
    task = _assign(client, task, 2, SECOND)
    with app.state.production_uow_factory() as uow:
        # Допуск к опрессовке есть только у Байкова: у Оператора по этому коду допуска нет.
        uow.skills.replace_for_operator(SECOND, (OperatorSkill(SECOND, "PRS_TERMINAL_hand", True, 3, Decimal(1)),))
        uow.plan_events.add(PlanEvent(project="801К.1", occurred_at=NOW, who="Мастер", kind="Блокер",
                                      blocker="ОЖИД ИНФ", text="ждём чертёж"))
        audit_before = uow.session.scalar(select(func.count()).select_from(AuditEventRow))
        uow.commit()
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(
            project="801К.1", customer="Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
            assemblies=(KitAssembly("КБ1", "Жгут", run=Decimal(10)), KitAssembly("КБ2", "Кабель", run=Decimal(5))),
        ))
        uow.commit()

    response = client.get("/api/production/today?site=ЖУК")

    assert response.status_code == 200, response.text
    body = response.json()
    # Мастер ЦАР: свой участок, выбор участка не действует.
    assert (body["site"], body["can_switch"], body["today"]) == ("ЦАР", False, "2026-09-22")
    assert body["working_day"] is True and body["shift"] == {"start": "09:00", "finish": "17:30"}
    people = {item["name"]: item for item in body["people"]}
    assert list(people) == ["Байков", "Оператор", "Яковлева"]  # Зингер — ЖУК, мастер — не монтажник
    assert people["Оператор"]["state"] == "свободен" and (people["Оператор"]["planned"], people["Оператор"]["done"]) == (1, 0)
    assert people["Оператор"]["next"]["stage"] == "Опрессовка терминалов"
    assert people["Яковлева"]["state"] == "отсутствует" and people["Яковлева"]["absence"] == {
        "kind": "Отпуск", "from": "2026-09-21", "to": "2026-09-25"}
    plan = body["plan"]
    assert [(line["operator_name"], line["position"], line["state"]) for line in plan] == [
        ("Оператор", 1, STATE_NEW), ("Байков", 2, STATE_NEW)]
    assert plan[0]["quantity"] == 10 and plan[0]["done"] == 0 and plan[0]["remaining"] == 10 and plan[0]["late"] is False
    assert body["handed"] == [] and body["overdue"]["operations"] == [] and body["overdue"]["tasks"] == []
    stuck = [(item["kind"], item["item"], item["position"], item["fix"]) for item in body["stuck"]]
    assert stuck == [
        ("blocker", "", None, "prodplan"),
        ("skill", "КБ1", 1, "skills"),
        ("input", "КБ1", 2, "task"),
        ("kit", "КБ1", None, "issues"),
        ("etk", "КБ2", None, "techcards"),
    ]
    reasons = {item["kind"]: item for item in body["stuck"]}
    assert reasons["blocker"]["reason"] == "Проект на ОЖИД ИНФ: ждём чертёж"
    assert reasons["skill"]["reason"] == "Нет допуска у Оператор к «Опрессовка терминалов»"
    assert reasons["skill"]["fix_label"] == "Компетенции" and reasons["skill"]["operator_name"] == "Оператор"
    assert reasons["input"]["reason"] == "Ждёт этап 1 «Опрессовка терминалов»: от него ещё ничего не сдано"
    assert reasons["kit"]["reason"] == "Комплект КБ не набран: запроса на комплектацию нет"
    assert reasons["etk"]["reason"] == "ЭТК не выдана — задание не запустить (Кабель)"
    with app.state.production_uow_factory() as uow:
        assert uow.session.scalar(select(func.count()).select_from(AuditEventRow)) == audit_before


def test_today_shows_running_work_overdue_and_late_projects(production_api):  # noqa: F811
    client, operator_id = production_api
    _staff()
    task = _launch(client, "today-2", "801К.1", "КБ1", 10, deadline="2026-09-20")
    task = _assign(client, task, 1, operator_id)
    with app.state.production_uow_factory() as uow:
        uow.plan_projects.replace((PlanProjectSnapshot("801К.1", customer="Заказчик", deadline=date(2026, 9, 21), imported_at=NOW),))
        operation = UUID(task["operations"][0]["id"])
        uow.work.save(WorkSession.start(UUID(task["id"]), operation, operator_id, datetime(2026, 9, 22, 9, 30)))
        uow.commit()
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 15, 0)

    body = client.get("/api/production/today").json()

    people = {item["name"]: item for item in body["people"]}
    work = people["Оператор"]["work"]
    assert people["Оператор"]["state"] == "работает"
    assert (work["stage"], work["task_number"], work["position"]) == ("Опрессовка терминалов", task["number"], 1)
    assert work["since"].startswith("2026-09-22T09:30")
    assert people["Байков"]["state"] == "свободен" and people["Байков"]["next"] is None
    # Плановый конец опрессовки прошёл, сдачи нет — просрочено; в плане дня строка помечена.
    late = body["overdue"]["operations"]
    assert [(line["operator_name"], line["position"], line["state"]) for line in late] == [("Оператор", 1, STATE_RUNNING)]
    assert body["plan"][0]["late"] is True
    assert body["overdue"]["tasks"] == [{
        "task_id": task["id"], "task_number": task["number"], "project": "801К.1", "item": "КБ1", "name": "Жгут",
        "deadline": "2026-09-20", "days": 2, "site": "ЦАР",
    }]
    projects = body["overdue"]["projects"]
    assert [item["project"] for item in projects] == ["801К.1"] and projects[0]["deadline"] == "2026-09-21"
    # По заданию уже работают — комплект и ЭТК не спрашиваются; вход этапа 1 — из комплекта.
    assert [item["kind"] for item in body["stuck"]] == []


def test_full_access_switches_to_zhuk_with_its_people_plan_and_handed_tasks(production_api):  # noqa: F811
    # Владелец 29.10: «Сегодня» ЖУК — люди, план и «стоит» как у ЦАР; задания, отданные целиком, видны отдельно.
    client, _operator_id = production_api
    _staff()
    task = _launch(client, "today-3", "802К.1", "КБ1", 20)
    handed = client.post(f"/api/production/tasks/{task['id']}/handover", json={"version": task["version"], "site": "ЖУК"})
    assert handed.status_code == 200, handed.text
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=uuid4(), name="Директор", department="Дирекция"), email="boss@example.test",
        roles=frozenset({"superadmin"}), permissions={"production": "edit"},
    )
    planned = _assign(client, _launch(client, "today-4", "803К.1", "КБ1", 4), 1, ZHUK)

    body = client.get("/api/production/today?site=ЖУК").json()

    assert (body["site"], body["can_switch"]) == ("ЖУК", True) and "handover" not in body
    assert [item["name"] for item in body["people"]] == ["Зингер"]
    assert body["people"][0]["planned"] == 1
    assert [(line["operator_name"], line["task_number"], line["position"]) for line in body["plan"]] == [
        ("Зингер", planned["number"], 1)]
    assert [(item["task_number"], item["quantity"], item["delivered"], item["remaining"]) for item in body["handed"]] == [
        (task["number"], 20, 0, 20)]
    # Без комплекта стоят и отданное ЖУК задание, и задание людей ЖУК.
    assert sorted((item["kind"], item["project"], item["item"]) for item in body["stuck"]) == [
        ("kit", "802К.1", "КБ1"), ("kit", "803К.1", "КБ1")]


def test_today_on_an_empty_shop(production_api):  # noqa: F811
    client, _operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 26, 10, 0)  # суббота
    app.dependency_overrides[moscow_today] = lambda: date(2026, 9, 26)

    body = client.get("/api/production/today").json()

    assert body["working_day"] is False and body["plan"] == [] and body["stuck"] == []
    assert [item["state"] for item in body["people"]] == ["свободен"]
    assert body["overdue"] == {"operations": [], "tasks": [], "projects": []}
