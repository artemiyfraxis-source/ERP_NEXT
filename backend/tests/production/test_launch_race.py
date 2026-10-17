"""Одна КБ, два запуска в один момент (автоплан и «Создать задание»): создаётся одно задание.

Отдельные сессии над файловой SQLite (у каждой своё соединение). Чередование задаётся крючком: второй запуск
проходит целиком в тот момент, когда первый уже прочитал номер запуска КБ, но ещё не записал задание."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import DuplicateLaunch, LaunchProductionCommand, launch_production
from app.modules.production.domain import ProductionOperation
from app.modules.production.infrastructure import (
    ProductionTaskRow,
    SqlAlchemyProductionRepository,
    SqlAlchemyProductionUnitOfWork,
)
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.production.test_autoplan_api import ANNA, MASTER, NOW, apply, issue, preview, tasks_of

ACTOR = Actor(id=MASTER, name="Мастер", department="Производство")


@pytest.fixture()
def race(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'race.db').as_posix()}", connect_args={"check_same_thread": False})
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        session.add_all([
            IdentityUserRow(id=MASTER, login="master", email="master@example.test", name="Мастер", department="Производство", role="master", site="ЦАР", active=True, site_master=True),
            IdentityUserRow(id=ANNA, login="anna", email="anna@example.test", name="Анна", department="Монтаж", role="staff", site="ЦАР", active=True, shop_worker=True),
        ])
        session.commit()
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.production_uow_factory = factory
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="master@example.test", roles=frozenset({"foreman"}), permissions={"production": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    app.dependency_overrides[moscow_today] = lambda: NOW.date()
    yield TestClient(app), sessions, factory
    for dependency in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(dependency, None)
    for name in ("production_uow_factory", "techcards_uow_factory", "references_uow_factory"):
        delattr(app.state, name)
    engine.dispose()


def by_hand(techcard_id: UUID, key: str, quantity: int = 10) -> LaunchProductionCommand:
    """«Создать задание» по КБ — как его собирает api.launch."""
    return LaunchProductionCommand(
        idempotency_key=key, project="708К", item="КБ2", name="Жгут", quantity=quantity,
        fulfillment_request_id=None, deadline=date(2026, 10, 2),
        operations=(ProductionOperation.create(
            position=1, code="ASM", stage="Монтаж в корпус", description="Собрать",
            seconds_per_unit=Decimal("20"), setup_seconds=Decimal("300"), quantity=quantity,
        ),), actor=ACTOR, now=NOW, techcard_id=techcard_id,
    )


def interleave(monkeypatch, method: str, rival) -> None:
    """Первый вызов метода репозитория пропускает соперника целиком: после себя (чтение номера запуска) или до себя
    (номер ПЗ — последний шаг перед записью задания). Вызовы самого соперника идут как есть."""
    original = getattr(SqlAlchemyProductionRepository, method)
    state = {"done": False, "inside": False}

    def run_rival() -> None:
        state["inside"] = True
        try:
            rival()
        finally:
            state["inside"], state["done"] = False, True

    def hooked(self, *args, **kwargs):
        if state["done"] or state["inside"]:
            return original(self, *args, **kwargs)
        if method == "reserve_number":
            run_rival()
            return original(self, *args, **kwargs)
        result = original(self, *args, **kwargs)
        run_rival()
        return result

    monkeypatch.setattr(SqlAlchemyProductionRepository, method, hooked)


def stored(sessions) -> list[tuple[str, str, UUID | None, int | None]]:
    with sessions() as session:
        return [
            (row.number, row.source_idempotency_key, row.techcard_id, row.launch_seq)
            for row in session.scalars(select(ProductionTaskRow).order_by(ProductionTaskRow.number))
        ]


def test_two_manual_launches_of_one_card_create_one_task_and_a_later_launch_is_second(race, monkeypatch):
    http, sessions, factory = race
    card = issue("КБ2")
    interleave(monkeypatch, "last_launch_seqs", lambda: launch_production(by_hand(card.id, "rival"), factory()))

    with pytest.raises(DuplicateLaunch, match="Задание по этой КБ уже создано — обновите"):
        launch_production(by_hand(card.id, "loser"), factory())

    assert [(key, seq) for _, key, _, seq in stored(sessions)] == [("rival", 1)]
    monkeypatch.undo()
    later = launch_production(by_hand(card.id, "later", quantity=5), factory())
    assert later.quantity == 5
    assert [(key, seq) for _, key, _, seq in stored(sessions)] == [("rival", 1), ("later", 2)]


def test_losing_manual_launch_over_http_gets_409(race, monkeypatch):
    http, sessions, factory = race
    card = issue("КБ2")
    interleave(monkeypatch, "last_launch_seqs", lambda: launch_production(by_hand(card.id, "rival"), factory()))

    response = http.post(
        "/api/production/tasks", headers={"Idempotency-Key": "by-hand"},
        json={"techcard_id": str(card.id), "quantity": 10},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Задание по этой КБ уже создано — обновите"
    assert [(key, seq) for _, key, _, seq in stored(sessions)] == [("rival", 1)]
    monkeypatch.undo()
    again = http.post(
        "/api/production/tasks", headers={"Idempotency-Key": "by-hand-2"},
        json={"techcard_id": str(card.id), "quantity": 4},
    )
    assert again.status_code == 200, again.text
    assert [(key, seq) for _, key, _, seq in stored(sessions)] == [("rival", 1), ("by-hand-2", 2)]


def test_autoplan_losing_to_a_manual_launch_after_its_calculation_is_stale(race, monkeypatch):
    http, sessions, factory = race
    card = issue("КБ2")
    planned = preview(http)
    assert [item["index"] for item in planned["new_tasks"]] == ["КБ2"]
    # «Создать задание» проходит, когда автоплан уже посчитал и прочитал номер запуска, — перед записью задания.
    interleave(monkeypatch, "reserve_number", lambda: launch_production(by_hand(card.id, "by-hand"), factory()))

    response = apply(http, planned)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Задание по этой КБ уже создано — пересчитайте автоплан"
    assert [(key, seq) for _, key, _, seq in stored(sessions)] == [("by-hand", 1)]
    assert len(tasks_of(http)) == 1


def test_manual_launch_losing_to_autoplan_is_refused(race, monkeypatch):
    http, sessions, factory = race
    card = issue("КБ2")
    planned = preview(http)
    applied = []
    interleave(monkeypatch, "last_launch_seqs", lambda: applied.append(apply(http, planned)))

    with pytest.raises(DuplicateLaunch):
        launch_production(by_hand(card.id, "by-hand"), factory())

    assert applied[0].status_code == 200, applied[0].text
    rows = stored(sessions)
    assert len(rows) == 1 and rows[0][1].startswith("autoplan:") and rows[0][2:] == (card.id, 1)
    monkeypatch.undo()
    launch_production(by_hand(card.id, "by-hand-later"), factory())
    assert [seq for *_, seq in stored(sessions)] == [1, 2]


def test_task_without_techcard_has_no_launch_number(race):
    _, sessions, factory = race
    for key in ("free-1", "free-2"):
        launch_production(replace(by_hand(UUID(int=0), key), techcard_id=None), factory())
    assert [(key, card, seq) for _, key, card, seq in stored(sessions)] == [("free-1", None, None), ("free-2", None, None)]
