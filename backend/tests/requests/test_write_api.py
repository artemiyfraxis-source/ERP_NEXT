from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.requests.api import Principal, authenticated_principal, moscow_now
from app.modules.requests.application import ProcurementDetails
from app.modules.requests.infrastructure import (
    OutboxMessageRow,
    RequestRow,
    SqlAlchemyUnitOfWork,
    create_schema,
)
from tests.factories import Actor


def request_payload(name: str = "Разъём") -> dict:
    return {
        "purpose": "RND",
        "project_reference": "591К.3",
        "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
        "name": name,
        "item_type": "Комплектующие",
        "quantity": "2",
        "unit": "шт",
        "quality_document": "Нет",
        "procurement_deadline": "2026-09-18",
        "planned_date": "2026-09-25",
    }


@pytest.fixture()
def write_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"),
        email="ivanov@example.test",
        roles=frozenset({"employee"}),
        permissions={"newrequest": "edit", "myrequests": "edit", "requests": "view"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 15, 10, 30)
    yield TestClient(app), engine, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.uow_factory
    engine.dispose()


def test_create_requires_authenticated_server_session(write_api):
    client, _, _ = write_api
    app.dependency_overrides.pop(authenticated_principal)

    response = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:unauthenticated"},
        json={"requests": [request_payload()]},
    )

    assert response.status_code == 401


def test_create_uses_server_actor_and_is_idempotent(write_api):
    client, engine, sessions = write_api
    headers = {"Idempotency-Key": "create:browser:42"}
    payload = {"requests": [request_payload(), request_payload("Кабель")]}

    first = client.post("/api/requests", headers=headers, json=payload)
    retry = client.post("/api/requests", headers=headers, json=payload)

    assert first.status_code == 201, first.text
    assert retry.status_code == 201, retry.text
    assert retry.json() == first.json()
    listed = client.get("/api/requests")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["requests"]] == ["Разъём", "Кабель"]
    with sessions() as session:
        rows = list(session.scalars(select(RequestRow).order_by(RequestRow.creation_index)))
        assert len(rows) == 2
        assert all(row.actor_name == "Иванов" for row in rows)


def test_create_requires_idempotency_key(write_api):
    client, _, _ = write_api

    response = client.post("/api/requests", json={"requests": [request_payload()]})

    assert response.status_code == 422


def test_request_body_limit_rejects_oversized_payload(write_api):
    client, _, _ = write_api
    response = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:oversized"},
        content="x" * 1_048_577,
    )

    assert response.status_code == 413


def test_anyone_hands_their_own_request_over_with_nobody_as_its_buyer(write_api):
    # Owner 23.10: «Передавать заявки в закупку всем». The initiator is not a buyer — the line goes to Procurement
    # with nobody's name on it («Неразобранные»), not onto the initiator's desk; «Повторно» stays the buyer's.
    client, _, sessions = write_api
    created = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:handoff:own"},
        json={"requests": [request_payload()]},
    ).json()
    request_id = created["requests"][0]["id"]

    handed = client.post("/api/requests/handoff", json={"request_ids": [request_id]})

    assert handed.status_code == 202, handed.text
    with sessions() as session:
        row = session.get(RequestRow, UUID(request_id))
        assert row.status == "В работе" and row.procurement_actor_name is None
        message = session.scalars(select(OutboxMessageRow)).one()
        assert message.payload["buyer_id"] is None
    again = client.post("/api/requests/handoff", json={"request_ids": [request_id], "retransfer": True})
    assert again.status_code == 422


def test_without_requests_on_edit_only_ones_own_request_is_handed_over(write_api):
    client, _, sessions = write_api
    created = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:handoff:other"},
        json={"requests": [request_payload()]},
    ).json()
    request_id = created["requests"][0]["id"]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=uuid4(), name="Сидоров", department="РНД"),
        email="sidorov@example.test",
        roles=frozenset({"employee"}),
        permissions={"newrequest": "edit", "myrequests": "view"},
    )

    foreign = client.post("/api/requests/handoff", json={"request_ids": [request_id]})

    assert foreign.status_code == 403
    assert foreign.json()["detail"] == "Передать в закупку можно только свои заявки"
    with sessions() as session:
        assert session.get(RequestRow, UUID(request_id)).status == "Новый"
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 0
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=uuid4(), name="Козлова", department="Коммерция"),
        email="kozlova@example.test",
        roles=frozenset({"commercial"}),
        permissions={"requests": "edit", "procurement": "view"},
    )
    assert client.post("/api/requests/handoff", json={"request_ids": [request_id]}).status_code == 202
    with sessions() as session:
        assert session.get(RequestRow, UUID(request_id)).procurement_actor_name is None


def test_a_buyer_takes_the_handed_request_and_a_retry_does_not_duplicate_outbox(write_api):
    client, engine, sessions = write_api
    created = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:handoff:42"},
        json={"requests": [request_payload()]},
    ).json()
    request_id = created["requests"][0]["id"]

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    first = client.post("/api/requests/handoff", json={"request_ids": [request_id]})
    retry = client.post("/api/requests/handoff", json={"request_ids": [request_id]})

    assert first.status_code == 202, first.text
    assert retry.status_code == 202, retry.text
    assert retry.json() == first.json()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 1
        assert session.get(RequestRow, UUID(request_id)).procurement_actor_name == "Петров"


def test_mine_lists_only_own_requests_newest_first_with_default_stage(write_api):
    """RQ-MINE: server-side filter by the authenticated actor id, not the client-side name match."""
    client, _, _ = write_api
    client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:mine:ivanov"},
        json={"requests": [request_payload("Разъём"), request_payload("Кабель")]},
    )
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"employee"}),
        permissions={"newrequest": "edit"},
    )
    client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:mine:petrov"},
        json={"requests": [request_payload("Труба")]},
    )
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"),
        email="ivanov@example.test",
        roles=frozenset({"employee"}),
        permissions={"myrequests": "view"},
    )

    first_page = client.get("/api/requests/mine", params={"limit": 1, "offset": 0})
    second_page = client.get("/api/requests/mine", params={"limit": 1, "offset": 1})

    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["total"] == 2
    assert [item["name"] for item in first_page.json()["requests"]] == ["Кабель"]
    assert first_page.json()["requests"][0]["stage"] == "Новый"
    assert [item["name"] for item in second_page.json()["requests"]] == ["Разъём"]


def test_mine_rejects_limit_outside_bounds(write_api):
    client, _, _ = write_api

    response = client.get("/api/requests/mine", params={"limit": 0})

    assert response.status_code == 422


def test_mine_reports_procurement_stage_once_handed_off(write_api):
    """A stage beyond Requests' own status (buyer pipeline) comes from the Procurement port."""
    client, _, _ = write_api

    class StubStageLookup:
        def stages_for(self, request_ids):
            return {request_id: "Жду счет" for request_id in request_ids}

        def details_for(self, request_ids):
            return {request_id: ProcurementDetails(stage="Жду счет") for request_id in request_ids}

    app.state.procurement_stage_lookup = StubStageLookup()
    try:
        client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:mine:stage"},
            json={"requests": [request_payload()]},
        )

        response = client.get("/api/requests/mine")

        assert response.status_code == 200, response.text
        assert response.json()["requests"][0]["stage"] == "Жду счет"
        assert response.json()["requests"][0]["status"] == "Новый"
    finally:
        del app.state.procurement_stage_lookup


def test_retransfer_is_an_explicit_flag_and_refused_for_a_new_request(write_api):
    client, engine, sessions = write_api
    created = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:retransfer:1"},
        json={"requests": [request_payload()]},
    ).json()
    request_id = created["requests"][0]["id"]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )

    too_early = client.post("/api/requests/handoff", json={"request_ids": [request_id], "retransfer": True})
    first = client.post("/api/requests/handoff", json={"request_ids": [request_id]})
    again = client.post("/api/requests/handoff", json={"request_ids": [request_id], "retransfer": True})

    assert too_early.status_code == 422, too_early.text
    assert first.status_code == 202, first.text
    assert again.status_code == 202, again.text
    assert again.json() != first.json()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 2


def test_create_accepts_project_outside_open_registry_and_stores_canonical_number(write_api):
    client, _, _ = write_api

    class Registry:
        def open_projects(self):
            return ["591К.3", "2000К.1"]

    app.state.project_registry = Registry()
    try:
        unregistered = client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:project:closed"},
            json={"requests": [{**request_payload(), "project_reference": "48К.13"}]},
        )
        latin = client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:project:latin"},
            json={"requests": [{**request_payload(), "project_reference": "591K.3"}]},
        )
        accepted = client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:project:open"},
            json={"requests": [{**request_payload(), "project_reference": "2000к"}]},
        )
    finally:
        del app.state.project_registry

    assert unregistered.status_code == 201
    assert unregistered.json()["requests"][0]["project_reference"] == "48К.13"
    assert latin.status_code == 422
    assert "№ ПРОЕКТА" in latin.json()["detail"]
    assert accepted.status_code == 201
    assert accepted.json()["requests"][0]["project_reference"] == "2000К.1"


def test_mine_shows_purchase_columns_to_the_initiator(write_api):
    """openMyRequests: закупщик, поставщик, № заказа, ожид. дата, сумма come through the port."""
    client, _, _ = write_api

    class StubStageLookup:
        def details_for(self, request_ids):
            return {
                request_id: ProcurementDetails(
                    stage="В пути",
                    buyer="Петров",
                    supplier="ООО Ромашка",
                    order_number="З-17",
                    expected_arrival=date(2026, 10, 3),
                    sum=Decimal("1500.00"),
                )
                for request_id in request_ids
            }

    app.state.procurement_stage_lookup = StubStageLookup()
    try:
        client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:mine:purchase"},
            json={"requests": [request_payload()]},
        )
        row = client.get("/api/requests/mine").json()["requests"][0]
    finally:
        del app.state.procurement_stage_lookup

    assert row["stage"] == "В пути"
    assert row["buyer"] == "Петров"
    assert row["supplier"] == "ООО Ромашка"
    assert row["order_number"] == "З-17"
    assert row["expected_arrival"] == "2026-10-03"
    assert row["procurement_sum"] == "1500.00"


def test_mine_without_procurement_has_empty_purchase_columns(write_api):
    client, _, _ = write_api
    client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:mine:empty"},
        json={"requests": [request_payload()]},
    )

    row = client.get("/api/requests/mine").json()["requests"][0]

    assert row["stage"] == "Новый"
    assert row["buyer"] is None and row["order_number"] is None and row["procurement_sum"] is None


def test_mine_and_list_search_by_substring(write_api):
    client, _, _ = write_api
    client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:search"},
        json={"requests": [request_payload("Разъём SMA"), request_payload("Кабель RG58")]},
    )

    mine = client.get("/api/requests/mine", params={"q": "rg58"}).json()
    listed = client.get("/api/requests", params={"q": "SMA"}).json()
    by_id = client.get("/api/requests", params={"q": "SM-20260915-02"}).json()

    assert mine["total"] == 1
    assert [item["name"] for item in mine["requests"]] == ["Кабель RG58"]
    assert [item["name"] for item in listed["requests"]] == ["Разъём SMA"]
    assert [item["name"] for item in by_id["requests"]] == ["Кабель RG58"]


def test_batch_shares_one_designation_and_names_the_failing_position(write_api):
    client, _, _ = write_api
    ok = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:batch:ok"},
        json={"requests": [request_payload("A"), request_payload("B"), request_payload("C")]},
    )
    bad = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:batch:bad"},
        json={"requests": [request_payload("A"), {**request_payload(" "), "name": " "}]},
    )

    assert ok.status_code == 201, ok.text
    assert {item["designation"] for item in ok.json()["requests"]} == {"RND.001"}
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("Позиция 2: ")
    assert "НАИМЕНОВАНИЕ" in bad.json()["detail"]


def test_general_category_defaults_quality_document_to_no_on_the_server(write_api):
    client, _, _ = write_api

    response = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:quality:default"},
        json={"requests": [{**request_payload(), "quality_document": "", "category": "Общепром"}]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["requests"][0]["quality_document"] == "Нет"


def test_schema_lists_open_registry_projects_for_the_goal_dropdown(write_api):
    client, _, _ = write_api

    class Registry:
        def open_projects(self):
            return ["591К.3", "2000К.1"]

    assert client.get("/api/requests/schema").json()["projects"] == []
    app.state.project_registry = Registry()
    try:
        response = client.get("/api/requests/schema")
    finally:
        del app.state.project_registry

    assert response.json()["projects"] == ["591К.3", "2000К.1"]
