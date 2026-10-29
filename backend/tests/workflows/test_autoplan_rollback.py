"""Откат «Автоплана» за окно времени (app/workflows/autoplan_rollback.py, владелец 14.10.2026)."""

from datetime import date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import (
    ProductionOperationOperatorRow,
    ProductionOperationRow,
    ProductionPlanEventRow,
    ProductionSerialRow,
    ProductionTaskRow,
    SqlAlchemyProductionUnitOfWork,
)
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import ProjectTechcard
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.clock import MOSCOW, moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.autoplan_rollback import EVENT_TYPE, run
from tests.factories import Actor

MASTER = UUID("31000000-0000-0000-0000-000000000001")
ANNA = UUID("31000000-0000-0000-0000-000000000002")
BORIS = UUID("31000000-0000-0000-0000-000000000003")
DAY = date(2026, 9, 22)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 22, hour, minute)


SINCE = at(8, 30).replace(tzinfo=MOSCOW)
UNTIL = at(12).replace(tzinfo=MOSCOW)


@pytest.fixture()
def shop():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        session.add_all([
            IdentityUserRow(id=MASTER, login="master", email="master@example.test", name="Мастер", department="Производство", role="master", site="ЦАР", active=True, site_master=True),
            IdentityUserRow(id=ANNA, login="anna", email="anna@example.test", name="Анна", department="Монтаж", role="staff", site="ЦАР", active=True, shop_worker=True),
            IdentityUserRow(id=BORIS, login="boris", email="boris@example.test", name="Борис", department="Монтаж", role="staff", site="ЦАР", active=True, shop_worker=True),
        ])
        session.commit()
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER, name="Мастер", department="Производство"),
        email="master@example.test", roles=frozenset({"foreman"}), permissions={"production": "edit"},
    )
    shop = Shop(TestClient(app), sessions)
    shop.clock(at(8))
    yield shop
    for dependency in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(dependency, None)
    for name in ("production_uow_factory", "techcards_uow_factory", "references_uow_factory"):
        delattr(app.state, name)
    engine.dispose()


class Shop:
    def __init__(self, http: TestClient, sessions):
        self.http, self.sessions = http, sessions

    def clock(self, moment: datetime) -> None:
        app.dependency_overrides[moscow_now] = lambda: moment
        app.dependency_overrides[moscow_today] = lambda: moment.date()

    def issue(self, index: str, *, project: str = "708К.1") -> ProjectTechcard:
        config = {"ops": [
            {"type": "prsTerm", "stage": "Опрессовка терминалов", "opNumber": "PRS", "tOp": 30, "tPrep": 1800,
             "ep": {"termArt": "T-1"}},
            {"type": "insHousing", "stage": "Монтаж в корпус", "opNumber": "ASM", "tOp": 20, "tPrep": 300},
        ]}
        card = ProjectTechcard.create(
            project=project, item_index=index, product_name="Жгут", source_calculation="", deadline=date(2026, 9, 30), now=at(7),
        ).rebuild({
            "operations": [
                {"code": "PRS", "connector": "1", "side": "", "type": "prsTerm", "fields": {"applicator": "A19"}},
                {"code": "ASM", "connector": "2", "side": "", "type": "insHousing", "connector_article": "XP-6"},
            ],
            "build_config": config, "kit": {"part_quantity": "10"},
        }, at(7)).issue(at(7))
        with app.state.techcards_uow_factory() as uow:
            uow.techcards.save(card)
            uow.commit()
        return card

    def launch(self, index: str) -> dict:
        card = self.issue(index)
        response = self.http.post(
            "/api/production/tasks", headers={"Idempotency-Key": f"launch-{index.encode().hex()}"},
            json={"techcard_id": str(card.id), "quantity": 20, "deadline": "2026-10-02"},
        )
        assert response.status_code == 200, response.text
        return response.json()["task"]

    def autoplan(self) -> dict:
        planned = self.http.post("/api/production/autoplan/preview", json={}).json()
        response = self.http.post("/api/production/autoplan/apply", json={
            "fingerprint": planned["fingerprint"],
            "items": [{key: item[key] for key in ("task_id", "operation_id", "operator_id")} for item in planned["proposals"]],
        })
        assert response.status_code == 200, response.text
        return response.json()

    def plan_status(self, project: str, value: str) -> None:
        response = self.http.post("/api/production/plan/events", json={"project": project, "kind": "status", "value": value})
        assert response.status_code == 201, response.text

    def rollback(self, *, apply: bool) -> str:
        text, code = run(self.sessions, since=SINCE, until=UNTIL, apply=apply, now=at(13))
        assert code == 0, text
        return text

    def state(self) -> dict:
        """Всё, что откат должен вернуть: задания, операции (плановые окна), доли, «Запущено» по КБ, журнал плана.
        «Распланировано» больше не этап (владелец 29.10) — окна операций проверяются напрямую."""
        launched = {item["index"]: item["launched"] for item in self.http.get("/api/production/launchable").json()["items"]}
        with self.sessions() as session:
            tasks = sorted(
                (row.number, row.project, row.item, row.quantity, row.status, row.launch_seq, row.handed_to)
                for row in session.scalars(select(ProductionTaskRow))
            )
            operations = sorted(
                (str(row.task_id), row.position, row.stage, row.planned_start, row.planned_finish, row.assigned_by)
                for row in session.scalars(select(ProductionOperationRow))
            )
            shares = sorted(
                (str(row.operation_id), str(row.operator_id), row.planned_start, row.planned_finish)
                for row in session.scalars(select(ProductionOperationOperatorRow))
            )
            serials = len(list(session.scalars(select(ProductionSerialRow.id))))
            plan_events = sorted((row.project, row.kind, row.value) for row in session.scalars(select(ProductionPlanEventRow)))
        return {
            "tasks": tasks, "operations": operations, "shares": shares, "serials": serials, "launched": launched,
            "planned": sorted({row[0] for row in operations if row[3] is not None}), "plan_events": plan_events,
        }

    def rollback_events(self) -> list[dict]:
        with self.sessions() as session:
            return [row.details for row in session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == EVENT_TYPE))]


def test_rollback_returns_tasks_kit_launch_ladder_and_plan_to_before_the_autoplan(shop):
    shop.launch("КБ1")  # ПЗ-0001, запущено до окна без плана (как ПЗ-0007 12.10)
    shop.issue("КБ2")  # техкарта выдана, задания нет — автоплан его запустит
    before = shop.state()
    assert before["launched"] == {"КБ1": 20, "КБ2": 0} and before["planned"] == []

    shop.clock(at(9))
    applied = shop.autoplan()
    shop.clock(at(10))
    shop.plan_status("708К.1", "Отменён")
    during = shop.state()
    assert len(applied["created"]) == 1 and during["launched"]["КБ2"] == 10
    assert len(during["planned"]) == 2  # оба задания КБ1 и КБ2 стоят в плане
    assert all(operation[5] == "Автоплан · Мастер" for operation in during["operations"])

    dry = shop.rollback(apply=False)
    assert f"удалить задание {applied['created'][0]['number']}" in dry
    assert "→ не назначено (до окна у задания плана не было)" in dry
    assert "удалить запись журнала плана 708К.1: «Статус проекта» = «Отменён»" in dry
    assert "Проверка без записи" in dry
    assert shop.state() == during and shop.rollback_events() == []

    done = shop.rollback(apply=True)

    assert done.startswith("Откат автоплана") and "Откачено:" in done
    assert shop.state() == before
    events = shop.rollback_events()
    assert len(events) == 1 and events[0]["deleted_tasks"] == [applied["created"][0]["number"]]


def test_rollback_is_idempotent(shop):
    shop.launch("КБ1")
    shop.issue("КБ2")
    before = shop.state()
    shop.clock(at(9))
    shop.autoplan()
    shop.rollback(apply=True)

    again = shop.rollback(apply=True)

    assert "Откатывать нечего" in again
    assert shop.state() == before and len(shop.rollback_events()) == 1


def test_task_with_work_is_refused_and_the_rest_is_rolled_back(shop):
    shop.launch("КБ1")
    shop.issue("КБ2")
    shop.clock(at(9))
    created = shop.autoplan()["created"][0]
    with shop.sessions() as session:  # монтажник «взял» долю созданного задания — след работы
        operation = session.scalar(select(ProductionOperationRow).where(
            ProductionOperationRow.task_id == UUID(created["id"]), ProductionOperationRow.position == 1))
        share = session.scalar(select(ProductionOperationOperatorRow).where(
            ProductionOperationOperatorRow.operation_id == operation.id))
        share.started_at = at(9, 30)
        session.commit()

    text = shop.rollback(apply=True)

    assert f"{created['number']} (708К.1 · КБ2): не удаляется — по нему отмечена работа" in text
    state = shop.state()
    assert [task[0] for task in state["tasks"]] == ["ПЗ-0001", created["number"]]
    with shop.sessions() as session:
        kb1 = session.scalar(select(ProductionTaskRow).where(ProductionTaskRow.number == "ПЗ-0001"))
        kb1_operations = list(session.scalars(select(ProductionOperationRow).where(ProductionOperationRow.task_id == kb1.id)))
    assert all(row.assigned_by is None and row.planned_start is None for row in kb1_operations)
    # Повтор: отказ остаётся отказом, второй записи отката нет.
    assert "не удаляется" in shop.rollback(apply=True) and len(shop.rollback_events()) == 1


def test_existing_task_planned_before_the_window_loses_only_the_autoplan_assignment(shop):
    task = shop.launch("КБ1")
    operation = task["operations"][0]
    response = shop.http.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(BORIS)], "requested_start": "2026-09-23T09:00:00"},
    )
    assert response.status_code == 200, response.text
    shop.clock(at(9))
    shop.autoplan()  # ставит второй этап, ручное назначение первого не трогает

    text = shop.rollback(apply=True)

    assert "«до» журнал не хранит (план до окна был), снимаю назначение автоплана" in text
    with shop.sessions() as session:
        rows = {row.position: row for row in session.scalars(
            select(ProductionOperationRow).where(ProductionOperationRow.task_id == UUID(task["id"])))}
    assert rows[1].assigned_by == "Мастер" and rows[1].planned_start is not None
    assert rows[2].assigned_by is None and rows[2].planned_start is None
