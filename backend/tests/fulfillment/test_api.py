from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


@pytest.fixture()
def fulfillment_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"),
        email="receiver@example.test",
        roles=frozenset({"receiver"}),
        permissions={"fulfillment": "edit", "handout": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 12, 0)
    lot = StockLot.create(
        warehouse="ЦАР",
        grn="FRX.26.0000001",
        package="",
        name="Разъём",
        item_type="Комплектующие",
        purpose="Склад",
        unit="шт",
        quantity=Decimal("6"),
        storage_location="1F4",
        source_receiving_ids=("receiving-one",),
    )
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    yield TestClient(app), lot, factory
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.fulfillment_uow_factory
    engine.dispose()


def test_receiver_binds_a_human_selected_lot_and_issues_only_the_complete_kit(fulfillment_api):
    client, lot, factory = fulfillment_api
    created = client.post(
        "/api/issues",
        headers={"Idempotency-Key": "kitset-35k-s-13-kb-1"},
        json={
            "purpose": "35К.С.13 / КБ-1",
            "deadline": "2026-10-01",
            "lines": [{"article": "КД-1", "name": "Разъём", "unit": "шт", "requirement": "6"}],
        },
    )
    body = created.json()["request"]
    bound = client.post(
        f"/api/issues/{body['id']}/lines/{body['lines'][0]['id']}/bind",
        json={"version": body["version"], "lot_id": str(lot.id)},
    )
    bound_body = bound.json()["request"]
    completed_kit = client.post(
        f"/api/issues/{body['id']}/complete",
        json={"version": bound_body["version"]},
    )
    ready = client.post(
        f"/api/issues/{body['id']}/ready",
        json={"version": completed_kit.json()["request"]["version"], "handout_location": "Полка выдачи 2"},
    )
    ready_body = ready.json()["request"]
    issued = client.post(
        f"/api/issues/{body['id']}/issue",
        json={"version": ready_body["version"]},
    )
    issued_body = issued.json()["request"]
    returned = client.post(
        f"/api/issues/{body['id']}/returned",
        json={"version": issued_body["version"], "return_location": "Стеллаж возврата"},
    )
    returned_body = returned.json()["request"]
    completed = client.post(
        f"/api/issues/{body['id']}/complete-return",
        json={
            "version": returned_body["version"],
            "lines": [{
                "line_id": returned_body["lines"][0]["id"],
                "returned_good": "2",
                "defective_returned": "1",
                "defective_missing": "0.5",
                "storage_location": "2A1",
            }],
        },
    )

    assert created.status_code == 200, created.text
    assert bound.status_code == 200, bound.text
    assert bound_body["status"] == "Недоукомплектован"
    assert bound_body["at_work"] == "2026-09-22T12:00:00"
    assert completed_kit.json()["request"]["status"] == "Укомплектован"
    assert ready.status_code == 200, ready.text
    assert issued.status_code == 200, issued.text
    assert issued_body["status"] == "КБ Выдана"
    assert returned.status_code == 200, returned.text
    assert returned_body["status"] == "КБ Возвращена"
    assert completed.status_code == 200, completed.text
    assert completed.json()["request"]["status"] == "Готово"
    assert completed.json()["request"]["lines"][0]["consumed"] == "2.5"
    with factory() as uow:
        assert uow.warehouse.get(lot.id).balance == Decimal("2")
        assert uow.warehouse.get(lot.id).reserved == Decimal("0")
        assert uow.warehouse.get(lot.id).storage_location == "2A1"


def test_print_and_return_sheet_endpoints(fulfillment_api):
    client, lot, _ = fulfillment_api
    created = client.post(
        "/api/issues",
        headers={"Idempotency-Key": "kitset-print-endpoint"},
        json={
            "purpose": "316К.14 / КБ-1",
            "deadline": "2026-10-01",
            "lines": [{"article": "КД-1", "name": "Разъём", "unit": "шт", "requirement": "6"}],
        },
    )
    body = created.json()["request"]

    not_issued_yet = client.get(f"/api/issues/{body['id']}/return-sheet")
    assert not_issued_yet.status_code == 409

    printed = client.get(f"/api/issues/{body['id']}/print")
    assert printed.status_code == 200, printed.text
    sheet = printed.json()["sheet"]
    assert sheet["title"] == f"ЗАЯВКА НА ВЫДАЧУ {body['number']}"
    assert sheet["rows"][0][3] == "— ждём поставки —"
    assert "Разъём" in sheet["note"]

    bound = client.post(
        f"/api/issues/{body['id']}/lines/{body['lines'][0]['id']}/bind",
        json={"version": body["version"], "lot_id": str(lot.id)},
    )
    bound_body = bound.json()["request"]
    completed_kit = client.post(
        f"/api/issues/{body['id']}/complete",
        json={"version": bound_body["version"]},
    )
    ready = client.post(
        f"/api/issues/{body['id']}/ready",
        json={"version": completed_kit.json()["request"]["version"], "handout_location": "Полка выдачи 2"},
    )
    ready_body = ready.json()["request"]
    issued = client.post(
        f"/api/issues/{body['id']}/issue",
        json={"version": ready_body["version"]},
    )
    issued_body = issued.json()["request"]

    printed_after_issue = client.get(f"/api/issues/{body['id']}/print")
    assert printed_after_issue.json()["sheet"]["rows"][0][6] == "6"  # «Набрать» = выдано после проводки
    assert printed_after_issue.json()["sheet"]["note"] == ""

    return_sheet = client.get(f"/api/issues/{body['id']}/return-sheet")
    assert return_sheet.status_code == 200, return_sheet.text
    return_body = return_sheet.json()["sheet"]
    assert return_body["title"] == f"ЛИСТ ВОЗВРАТА ПО ЗАЯВКЕ {body['number']}"
    assert return_body["rows"][0][4] == "6"
    assert issued_body["status"] == "КБ Выдана"

    missing = client.get("/api/issues/10000000-0000-0000-0000-000000000001/print")
    assert missing.status_code == 404


def test_handout_requires_receiver_role(fulfillment_api):
    client, _, _ = fulfillment_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Технолог", department="КБ"),
        email="engineer@example.test",
        roles=frozenset({"engineer"}),
        permissions={"fulfillment": "edit", "handout": "edit"},
    )

    response = client.post(
        "/api/issues/10000000-0000-0000-0000-000000000001/issue",
        json={"version": 1},
    )

    assert response.status_code == 403


def _create(client, key: str, purpose: str = "728К.2 · КБ1 · Кабельная сборка") -> dict:
    response = client.post(
        "/api/issues",
        headers={"Idempotency-Key": key},
        json={
            "purpose": purpose,
            "deadline": "2026-10-01",
            "lines": [{"article": "КД-1", "name": "Разъём", "unit": "шт", "requirement": "6"}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["request"]


def _as(roles: set[str], permissions: dict[str, str]) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кто-то", department="Склад"),
        email="someone@example.test",
        roles=frozenset(roles),
        permissions=permissions,
    )


def test_deadline_is_required_by_the_api(fulfillment_api):
    client, _, _ = fulfillment_api
    response = client.post(
        "/api/issues",
        headers={"Idempotency-Key": "no-deadline"},
        json={"purpose": "Ручная", "lines": [{"name": "Разъём", "unit": "шт", "requirement": "1"}]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Укажи дедлайн."


def test_list_carries_project_and_rack_layout(fulfillment_api):
    client, _, _ = fulfillment_api
    _create(client, "project-card")

    listed = client.get("/api/issues").json()

    assert listed["requests"][0]["project"] == "728К.2"
    assert listed["places"]["racks"] >= 1 and listed["places"]["slots"].startswith("ABC")


def test_complete_is_refused_while_a_line_waits_for_a_lot(fulfillment_api):
    client, _, _ = fulfillment_api
    body = _create(client, "complete-gate")

    refused = client.post(f"/api/issues/{body['id']}/complete", json={"version": body["version"]})

    assert refused.status_code == 422
    assert "не закрыто позиций — 1 (Разъём)" in refused.json()["detail"]


def test_picker_sends_to_handout_with_a_rack_address(fulfillment_api):
    client, lot, _ = fulfillment_api
    body = _create(client, "ready-by-picker")
    bound = client.post(
        f"/api/issues/{body['id']}/lines/{body['lines'][0]['id']}/bind",
        json={"version": body["version"], "lot_id": str(lot.id)},
    ).json()["request"]
    complete = client.post(f"/api/issues/{body['id']}/complete", json={"version": bound["version"]}).json()["request"]
    _as({"engineer"}, {"fulfillment": "edit"})

    partial = client.post(f"/api/issues/{body['id']}/ready", json={
        "version": complete["version"], "place": {"rack": "12", "shelf": "с", "num": "", "slot": ""},
    })
    ready = client.post(f"/api/issues/{body['id']}/ready", json={
        "version": complete["version"], "place": {"rack": "12", "shelf": "с", "num": "7", "slot": "b"},
    })

    assert partial.status_code == 422
    assert partial.json()["detail"] == "Адрес неполный: нужны стеллаж, полка и № на полке."
    assert ready.status_code == 200, ready.text
    assert ready.json()["request"]["status"] == "Готов к выдаче"
    assert ready.json()["request"]["handout_location"] == "12C7B"
    assert ready.json()["request"]["at_picked"] == "2026-09-22T12:00:00"


def test_handout_closes_an_issued_request_without_return(fulfillment_api):
    client, lot, _ = fulfillment_api
    body = _create(client, "close-issued")
    request = client.post(
        f"/api/issues/{body['id']}/lines/{body['lines'][0]['id']}/bind",
        json={"version": body["version"], "lot_id": str(lot.id)},
    ).json()["request"]
    for path, extra in (("complete", {}), ("ready", {"handout_location": "1A1"}), ("issue", {})):
        request = client.post(f"/api/issues/{body['id']}/{path}", json={"version": request["version"], **extra}).json()["request"]

    closed = client.post(f"/api/issues/{body['id']}/close", json={"version": request["version"]})

    assert closed.status_code == 200, closed.text
    assert closed.json()["request"]["status"] == "Готово"
    assert closed.json()["request"]["at_issued"] == closed.json()["request"]["at_closed"] == "2026-09-22T12:00:00"


def test_only_admin_deletes_and_the_reserve_goes_back(fulfillment_api):
    client, lot, factory = fulfillment_api
    body = _create(client, "delete-me")
    client.post(
        f"/api/issues/{body['id']}/lines/{body['lines'][0]['id']}/bind",
        json={"version": body["version"], "lot_id": str(lot.id)},
    )
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == Decimal("6")

    refused = client.delete(f"/api/issues/{body['id']}")
    _as({"admin"}, {"fulfillment": "edit"})
    deleted = client.delete(f"/api/issues/{body['id']}")

    assert refused.status_code == 403
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["message"] == "Заявка удалена: позиций 1, снят резерв с 1 партий."
    with factory() as uow:
        assert uow.fulfillment.get(UUID(body["id"])) is None
        assert uow.warehouse.get(lot.id).reserved == Decimal("0")
    assert client.delete(f"/api/issues/{body['id']}").status_code == 404


def test_issued_request_cannot_be_deleted(fulfillment_api):
    client, lot, _ = fulfillment_api
    body = _create(client, "delete-issued")
    request = client.post(
        f"/api/issues/{body['id']}/lines/{body['lines'][0]['id']}/bind",
        json={"version": body["version"], "lot_id": str(lot.id)},
    ).json()["request"]
    for path, extra in (("complete", {}), ("ready", {"handout_location": "1A1"}), ("issue", {})):
        request = client.post(f"/api/issues/{body['id']}/{path}", json={"version": request["version"], **extra}).json()["request"]
    _as({"admin"}, {"fulfillment": "edit"})

    refused = client.delete(f"/api/issues/{body['id']}")

    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        f"Заявка {body['number']} уже выдавалась (позиций с выдачей: 1). "
        "Удалять её нельзя — это документ проведённого списания. Оформи возврат."
    )


def test_manual_request_reserves_picked_lots_at_creation_and_keeps_it_new(fulfillment_api):
    client, lot, factory = fulfillment_api  # lot: Разъём, 6 шт available

    def create():
        return client.post(
            "/api/issues",
            headers={"Idempotency-Key": "issue-form-fifo"},
            json={
                "purpose": "48К.13",
                "deadline": "2026-10-01",
                "lines": [
                    {"article": "", "name": "Разъём", "unit": "шт", "requirement": "4", "lot_id": str(lot.id)},
                    {"article": "", "name": "Разъём", "unit": "шт", "requirement": "5", "lot_id": str(lot.id)},
                ],
            },
        )

    created = create()
    retry = create()

    assert created.status_code == 200, created.text
    body = created.json()["request"]
    assert retry.json()["request"]["id"] == body["id"]
    # issueCreateTasks_: 4 from the lot, then the 2 left on it; the missing 3 wait as a row without a lot.
    assert body["status"] == "Новая"
    assert [(line["requirement"], line["reserved"], line["lot"] is not None) for line in body["lines"]] == [
        ("4", "4", True), ("2", "2", True), ("3", "0", False),
    ]
    assert body["shortfall"] == "Разъём (ГРН не выбран): −3"
    assert body["at_work"] is None
    with factory() as uow:
        assert uow.warehouse.get(lot.id).reserved == Decimal("6")
