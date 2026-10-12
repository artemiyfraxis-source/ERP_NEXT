"""«нет на складе» on a kit line (owner 12.10.2026: «нужна кнопка „нет на складе“»): the storekeeper confirms the
part is physically absent — the line stays open as a shortage, never «есть», one of the two marks at a time."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.domain import (
    FulfillmentLine,
    FulfillmentRequest,
    FulfillmentStatus,
    FulfillmentValidationError,
    LotSnapshot,
)
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 12, 9, 30)
LOT = LotSnapshot(lot_id=uuid4(), warehouse="ЦАР", grn="FRX.26.0000001", package="", storage_location="1F4")


def kit(*needs: str) -> FulfillmentRequest:
    return FulfillmentRequest.create(
        number="REQ.26.0000012", source_idempotency_key="key", source_fingerprint="fp", requester="Мастер",
        purpose="719К.2 · КБ12 · Жгут", deadline=date(2026, 10, 20), created_at=NOW,
        lines=tuple(FulfillmentLine.create(article="510471000", name="Molex 510471000", unit="шт",
                                           requirement=Decimal(need)) for need in needs),
    )


# ── pure rules ────────────────────────────────────────────────────────


def test_absent_line_stays_open_as_a_shortage_and_names_it_in_the_shortfall():
    request = kit("100", "4")
    marked = request.mark_absent(request.lines[0].id, "Кладовщик", NOW)

    line = marked.lines[0]
    assert (line.absent, line.absent_at, line.absent_by) == (True, NOW, "Кладовщик")
    assert line.covered is False and line.waiting is True
    assert marked.status is FulfillmentStatus.UNDERFILLED
    assert [item.id for item in marked.open_lines()] == [line.id, marked.lines[1].id]
    assert "Molex 510471000 (нет на складе): −100" in marked.shortfall
    with pytest.raises(FulfillmentValidationError, match="уже отмечена"):
        marked.mark_absent(line.id, "Кладовщик", NOW)
    with pytest.raises(FulfillmentValidationError, match="Не собран|не собран"):
        marked.mark_complete()


def test_the_two_marks_replace_each_other():
    request = kit("100")
    off = request.mark_off_book(request.lines[0].id)
    assert off.status is FulfillmentStatus.COMPLETE

    absent = off.mark_absent(off.lines[0].id, "Кладовщик", NOW)
    assert (absent.lines[0].off_book, absent.lines[0].absent) == (False, True)
    assert absent.status is FulfillmentStatus.UNDERFILLED  # «есть» taken back: the kit is not whole any more

    back = absent.mark_off_book(absent.lines[0].id)
    assert (back.lines[0].off_book, back.lines[0].absent, back.lines[0].absent_by) == (True, False, "")
    assert back.status is FulfillmentStatus.COMPLETE


def test_a_lot_takes_the_mark_off_the_bound_part_and_the_rest_keeps_it():
    request = kit("100")
    absent = request.mark_absent(request.lines[0].id, "Кладовщик", NOW)
    bound = absent.bind_line(absent.lines[0].id, LOT, Decimal("60"))

    assert [(line.requirement, line.lot is not None, line.absent) for line in bound.lines] == [
        (Decimal("60"), True, False), (Decimal("40"), False, True),
    ]
    with pytest.raises(FulfillmentValidationError, match="партия"):
        bound.mark_absent(bound.lines[0].id, "Кладовщик", NOW)


def test_clearing_folds_the_line_back_and_an_absent_line_never_takes_a_free_one():
    request = kit("100", "4")
    absent = request.mark_absent(request.lines[0].id, "Кладовщик", NOW)
    bound = absent.bind_line(absent.lines[1].id, LOT, Decimal("4"))
    released = bound.unbind_line(bound.lines[1].id)
    assert [(line.requirement, line.absent) for line in released.lines] == [
        (Decimal("100"), True), (Decimal("4"), False),
    ]

    cleared = released.clear_absent(released.lines[0].id)
    assert [(line.requirement, line.absent) for line in cleared.lines] == [(Decimal("104"), False)]
    assert cleared.status is FulfillmentStatus.UNDERFILLED
    with pytest.raises(FulfillmentValidationError, match="не отмечена"):
        cleared.clear_absent(cleared.lines[0].id)


def test_an_issued_or_closed_kit_takes_no_mark():
    request = kit("4")
    ready = request.mark_off_book(request.lines[0].id).mark_ready("Зона выдачи")
    with pytest.raises(FulfillmentValidationError, match="закрыта"):
        ready.mark_absent(ready.lines[0].id, "Кладовщик", NOW)
    issued_line = ready.mark_issued().lines[0]
    with pytest.raises(FulfillmentValidationError, match="Выданную"):
        issued_line.mark_absent("Кладовщик", NOW)


# ── API and the database ──────────────────────────────────────────────


def _principal(permissions: dict) -> Principal:
    return Principal(
        actor=Actor(name="Кладовщик", department="Склад"), email="receiver@example.test",
        roles=frozenset({"receiver"}), permissions=permissions,
    )


@pytest.fixture()
def api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"fulfillment": "edit", "handout": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), factory
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.fulfillment_uow_factory
    engine.dispose()


def create(client, *needs: str) -> dict:
    response = client.post("/api/issues", headers={"Idempotency-Key": f"test-{uuid4()}"}, json={
        "purpose": "719К.2 · КБ12 · Жгут", "deadline": "2026-10-20",
        "lines": [{"article": f"КД-{index}", "name": f"Деталь {index}", "unit": "шт", "requirement": need}
                  for index, need in enumerate(needs, start=1)],
    })
    assert response.status_code == 200, response.text
    return response.json()["request"]


def _events(factory, prefix: str) -> list[str]:
    with factory() as uow:
        return sorted(uow.session.scalars(
            select(AuditEventRow.event_type).where(AuditEventRow.event_type.like(f"{prefix}%"))
        ))


def _notices(factory) -> list[dict]:
    with factory() as uow:
        return [row.payload for row in uow.session.scalars(
            select(OutboxMessageRow).where(OutboxMessageRow.topic == "notification.requested")
        ) if row.payload.get("type") == "absent"]


def test_mark_twice_is_one_mark_one_event_one_notice_then_unmark(api):
    client, factory = api
    request = create(client, "100", "4")
    line = request["lines"][0]
    url = f"/api/issues/{request['id']}/lines/{line['id']}/absent"

    marked = client.post(url, json={"version": request["version"]})
    double = client.post(url, json={"version": request["version"]})  # the second click of a double click

    assert marked.status_code == 200, marked.text
    body = marked.json()["request"]
    assert (body["lines"][0]["absent_by"], body["lines"][0]["absent_at"]) == ("Кладовщик", NOW.isoformat())
    assert body["lines"][0]["off_book"] is False
    assert body["status"] == "Недоукомплектован"
    assert "(нет на складе)" in body["shortfall"]
    assert double.status_code == 200
    assert double.json()["request"]["version"] == body["version"]
    assert _events(factory, "fulfillment.absent") == ["fulfillment.absent_marked"]
    notices = _notices(factory)
    assert len(notices) == 1
    assert notices[0]["recipient_audiences"] == "supply"
    assert notices[0]["title"] == "Нет на складе: КД-1"
    assert "Деталь 1 — 100 шт. 719К.2 · КБ12 · Жгут, заявка REQ.26." in notices[0]["body"]

    cleared = client.request("DELETE", url, json={"version": body["version"]})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["request"]["lines"][0]["absent_at"] is None
    assert cleared.json()["request"]["status"] == "Недоукомплектован"
    assert _events(factory, "fulfillment.absent") == ["fulfillment.absent_cleared", "fulfillment.absent_marked"]
    stale = client.post(url, json={"version": body["version"]})
    assert stale.status_code == 409  # a real change from an old screen is still refused


def test_server_keeps_one_mark_at_a_time(api):
    client, factory = api
    request = create(client, "4")
    line = request["lines"][0]
    base = f"/api/issues/{request['id']}/lines/{line['id']}"

    off = client.post(f"{base}/off-book", json={"version": request["version"]}).json()["request"]
    assert off["status"] == "Укомплектован"
    absent = client.post(f"{base}/absent", json={"version": off["version"]})
    assert absent.status_code == 200, absent.text
    body = absent.json()["request"]
    assert (body["lines"][0]["off_book"], body["lines"][0]["absent_by"]) == (False, "Кладовщик")
    assert body["status"] == "Недоукомплектован"
    assert body["fully_reserved"] is False

    back = client.post(f"{base}/off-book", json={"version": body["version"]}).json()["request"]
    assert (back["lines"][0]["off_book"], back["lines"][0]["absent_at"], back["status"]) == (True, None, "Укомплектован")
    with factory() as uow:
        details = [row.details for row in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "fulfillment.absent_marked")
        )]
    assert details[0]["off_book_cleared"] is True


def test_only_who_can_pick_the_kit_marks_it(api):
    client, _factory = api
    request = create(client, "4")
    url = f"/api/issues/{request['id']}/lines/{request['lines'][0]['id']}/absent"
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"fulfillment": "view"})
    assert client.post(url, json={"version": request["version"]}).status_code == 403
    assert client.post(
        f"/api/issues/{request['id']}/lines/{request['lines'][0]['id']}/off-book", json={"version": request["version"]},
    ).status_code == 403


def test_the_database_refuses_a_mark_next_to_another_state(api):
    client, factory = api
    request = create(client, "4")
    line_id = request["lines"][0]["id"].replace("-", "")
    with factory() as uow:
        session = uow.session
        with pytest.raises(IntegrityError):  # who without when
            session.execute(text("UPDATE fulfillment_lines SET absent_by = 'Кладовщик' WHERE id = :id"), {"id": line_id})
        session.rollback()
        with pytest.raises(IntegrityError):  # «нет на складе» and «есть, но не заведено» together
            session.execute(text(
                "UPDATE fulfillment_lines SET absent_at = '2026-10-12 09:30:00', absent_by = 'Кладовщик', off_book = 1"
                " WHERE id = :id"), {"id": line_id})
        session.rollback()
        with pytest.raises(IntegrityError):  # a mark on a reserved line
            session.execute(text(
                "UPDATE fulfillment_lines SET absent_at = '2026-10-12 09:30:00', absent_by = 'Кладовщик', reserved = 1"
                " WHERE id = :id"), {"id": line_id})
        session.rollback()
        session.execute(text(
            "UPDATE fulfillment_lines SET absent_at = '2026-10-12 09:30:00', absent_by = 'Кладовщик' WHERE id = :id"
        ), {"id": line_id})
        session.rollback()
