"""Requests filed from the techcard/spec go to Procurement at once (owner, 06.10).

«Заказать недостающее» and the calc requests made with a new project are handed off in the same transaction as
they are created: «В работе» in «Заявки», one `procurement.requested` without a buyer, and the consumer makes a
«Новый» line in «Неразобранные». Nobody presses «В закупку»; the one who filed them is not the buyer.
"""

from datetime import date, datetime

from sqlalchemy import select

from app.main import app
from app.modules.procurement.application import dispatch_next_procurement_requested
from app.modules.procurement.domain import ProcurementStatus
from app.modules.requests.application import CalcSpreadsheet
from app.modules.requests.domain import RequestStatus
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import OutboxMessageRow
from app.workflows.project_kitset import KitsetRequestsCommand, create_kitset_requests
from tests.factories import Actor
from tests.procurement.test_backorder_flow import db  # noqa: F401 — fixture
from tests.techcards.test_kitset_api import _load, _principal, kitset_api  # noqa: F401 — fixture

NOW = datetime(2026, 9, 29, 10, 0)
LATER = datetime(2026, 9, 29, 10, 5)
ENGINEER = Actor(name="Технолог", department="Производство")
POINT = "г. Москва, ул. 6-я Радиальная, д. 17"
ZPK = [
    ["№", "Тип", "Обозначение", "На проект", "Цена  без НДС", "Цена с НДС", "Ссылка", "Место поставки"],
    ["1", "Кабель", "МГТФ 0,35", "12,5", "1 000,50", "1 200,60", "https://x.test/a", "Россия"],
    ["2", "Разъём", "DB-9M", "4", "", "", "", "Китай"],
    ["3", "Разъём", "Без количества", "", "", "", "", ""],
]


class FakeCalc:
    def read_calc(self, spreadsheet_id):
        return CalcSpreadsheet(title="708К.4 Расчёт", zpk_grid=ZPK, project_cell="708К.4")


def kitset_requests(db):  # noqa: F811
    return create_kitset_requests(
        KitsetRequestsCommand(
            project="708К.4", url="https://docs.google.com/spreadsheets/d/1calc/edit", purpose="Обеспечение производства",
            delivery_point=POINT, procurement_deadline=date(2026, 10, 20), actor=ENGINEER, now=NOW,
        ),
        db.requests,
        FakeCalc,
    )


def outbox(sessions):
    with sessions() as session:
        return session.scalars(
            select(OutboxMessageRow).where(OutboxMessageRow.topic == "procurement.requested")
        ).all()


def drain(procurement_uow):
    lines = []
    while (line := dispatch_next_procurement_requested(LATER, procurement_uow())) is not None:
        lines.append(line)
    return lines


def test_calc_requests_of_a_new_project_land_unassigned_in_procurement(db):  # noqa: F811
    result = kitset_requests(db)

    assert (result.created, result.skipped) == (2, ("Без количества",))
    with db.requests() as uow:
        filed = list(uow.requests.recent(100))
    assert {(row.name, row.status, row.procurement_actor) for row in filed} == {
        ("МГТФ 0,35", RequestStatus.IN_WORK, None), ("DB-9M", RequestStatus.IN_WORK, None),
    }
    messages = outbox(db.sessions)
    assert {message.idempotency_key for message in messages} == {
        f"request:{row.id}:procurement:v2" for row in filed
    }
    assert {message.payload["buyer_id"] for message in messages} == {None}

    lines = drain(db.procurement)

    assert sorted((line.calculated_name, line.status, line.buyer) for line in lines) == [
        ("DB-9M", ProcurementStatus.NEW, None), ("МГТФ 0,35", ProcurementStatus.NEW, None),
    ]
    assert {line.source_request_id for line in lines} == {row.id for row in filed}

    again = kitset_requests(db)

    assert (again.created, again.existing) == (0, 2)
    assert len(outbox(db.sessions)) == 2
    assert drain(db.procurement) == []


def test_order_missing_lands_unassigned_in_procurement_and_never_twice(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    assert _load(client, deadline="2026-10-20").status_code == 200

    ordered = client.post("/api/techcards/kitsets/708К.4/order-missing", json={})

    assert ordered.status_code == 200, ordered.text
    assert len(ordered.json()["created"]) == 3
    with app.state.uow_factory() as uow:
        filed = [row for row in uow.requests.recent(100) if row.creation_key.startswith("kitset-missing:")]
        keys = {row.id: uow.outbox.by_idempotency_key(f"request:{row.id}:procurement:v2") for row in filed}
    assert {(row.status, row.procurement_actor) for row in filed} == {(RequestStatus.IN_WORK, None)}
    assert all(message is not None for message in keys.values())
    assert {message.payload["buyer_id"] for message in keys.values()} == {None}

    repeat = client.post("/api/techcards/kitsets/708К.4/order-missing", json={}).json()
    assert repeat["created"] == []

    lines = drain(app.state.procurement_uow_factory)

    assert {(line.source_request_id, line.status, line.buyer) for line in lines} == {
        (row.id, ProcurementStatus.NEW, None) for row in filed
    }
    after = client.post("/api/techcards/kitsets/708К.4/order-missing", json={}).json()
    assert after["created"] == []
    assert drain(app.state.procurement_uow_factory) == []
