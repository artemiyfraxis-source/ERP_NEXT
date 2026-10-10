"""«Отправить на комплектацию» — одна живая заявка на КБ (прод 180К.11 и 719К.2, 10.10.2026)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import VersionedFulfillmentCommand, cancel_fulfillment
from app.modules.fulfillment.domain import DuplicateKitRequest, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.techcards.kitsets import KitAssembly, KitComponent, Kitset
from app.shared.persistence import create_schema
from app.workflows.project_kitset import issue_from_kitset, kitset_issue_state
from tests.factories import Actor

NOW = datetime(2026, 10, 10, 9, 0)
ACTOR = Actor(name="Технолог", department="Производство")
DEADLINE = date(2026, 10, 20)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    engine.dispose()


def kitset(project: str, count: int) -> Kitset:
    return Kitset(
        project=project, customer="Заказчик", calc_source="", updated_at=NOW, updated_by=ACTOR.name, created_at=NOW,
        assemblies=tuple(
            KitAssembly(index=f"КБ{index}", name="Жгут", run=Decimal("2"), components=(
                KitComponent(article="43025-0400", type="Разъём", name="Корпус Molex", per_unit=Decimal("2")),
            ))
            for index in range(1, count + 1)
        ),
    )


def requests(factory) -> list:
    with factory() as uow:
        return list(uow.fulfillment.list(limit=100))


def test_second_press_creates_nothing_and_names_the_request_already_sent(factory):
    card = kitset("180К.11", 1)
    first = issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    assert [item["index"] for item in first.created] == ["КБ1"]

    with pytest.raises(DuplicateKitRequest) as refused:
        issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)

    assert "КБ1 (REQ.26.0000001)" in str(refused.value)
    assert [request.number for request in requests(factory)] == ["REQ.26.0000001"]


def test_a_new_kb_goes_alone_and_the_sent_one_is_listed(factory):
    issue_from_kitset(kitset("180К.11", 1), DEADLINE, ACTOR, NOW, factory)

    result = issue_from_kitset(kitset("180К.11", 2), DEADLINE, ACTOR, NOW, factory)

    assert [item["index"] for item in result.created] == ["КБ2"]
    assert result.already == [{"index": "КБ1", "no": "REQ.26.0000001", "status": "Новая"}]
    assert "уже отправлены: КБ1 (REQ.26.0000001)" in result.message


def test_719k2_only_the_cancelled_kb1_goes_again(factory):
    """КБ2–КБ12 live, КБ1 cancelled at the handout point (prod 05.10): the send creates exactly one request — КБ1."""
    card = kitset("719К.2", 12)
    issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    kb1 = next(request for request in requests(factory) if "· КБ1 ·" in request.purpose)
    cancel_fulfillment(VersionedFulfillmentCommand(kb1.id, kb1.version, ACTOR, NOW), factory())

    state = {row["index"]: row["request"] for row in kitset_issue_state(card, factory)}
    assert state["КБ1"] is None and all(state[f"КБ{index}"] for index in range(2, 13))

    result = issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)

    assert [item["index"] for item in result.created] == ["КБ1"]
    assert [item["index"] for item in result.already] == [f"КБ{index}" for index in range(2, 13)]
    live = [request for request in requests(factory) if request.status is not FulfillmentStatus.CANCELLED]
    assert len(live) == 12
    assert sum("· КБ1 ·" in request.purpose for request in live) == 1
    assert result.created[0]["no"] == "REQ.26.0000013"  # a new number, not the cancelled one's
