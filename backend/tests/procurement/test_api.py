from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested, dispatch_next_backorder_requested
from app.modules.procurement.infrastructure import (
    ProcurementLineRow,
    SqlAlchemyProcurementUnitOfWork,
)
from app.modules.requests.api import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from tests.factories import Actor


def message(suffix: int = 1) -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
        topic="procurement.requested",
        idempotency_key=f"request:{suffix}:procurement:v2",
        created_at=datetime(2026, 9, 15, 11, 0),
        payload={
            "source_request_id": f"10000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20260915-{suffix:02d}",
            "request_date": "2026-09-15",
            "initiator": "Иванов",
            "department": "РНД",
            "task_type": "Закупка",
            "designation": f"RND.{suffix:03d}",
            "project_reference": None,
            "priority": "Очень срочно",
            "delivery_point": "Москва",
            "planned_date": "2026-09-25",
            "link": None,
            "item_type": "Комплектующие",
            "design_article": None,
            "category": "Общепром",
            "calculated_name": "Разъём",
            "calculated_quantity": "2",
            "calculated_unit": "шт",
            "calculated_price": None,
            "calculated_price_with_vat": None,
            "calculated_sum": None,
        },
    )


@pytest.fixture()
def procurement_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 15, 11, 30)
    line = consume_procurement_requested(message(), factory())
    yield TestClient(app), line, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.procurement_uow_factory
    engine.dispose()


def test_procurement_routes_require_authenticated_buyer(procurement_api):
    client, _, _ = procurement_api
    app.dependency_overrides.pop(authenticated_principal)
    assert client.get("/api/procurement/available").status_code == 401

    # Access levels: «Лист закупки» closed in the matrix — refused, whatever else is open.
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"),
        email="ivanov@example.test",
        roles=frozenset({"operator"}),
        permissions={"requests": "edit", "newrequest": "edit"},
    )
    assert client.get("/api/procurement/available").status_code == 403


def test_available_queue_uses_bounded_cursor_pages(procurement_api):
    client, first_line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    second_line = consume_procurement_requested(message(2), factory())

    first_page = client.get("/api/procurement/available?limit=1")
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(f"/api/procurement/available?limit=1&after={cursor}")

    assert first_page.status_code == 200
    assert cursor is not None
    assert second_page.status_code == 200
    assert second_page.json()["next_cursor"] is None
    ids = {first_page.json()["lines"][0]["id"], second_page.json()["lines"][0]["id"]}
    assert ids == {str(first_line.id), str(second_line.id)}
    assert client.get("/api/procurement/available?limit=0").status_code == 422

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={},
    )
    assert client.get("/api/procurement/available").status_code == 403


def test_supplier_requests_are_formed_only_for_the_ticked_ready_lines(procurement_api):
    client, first, sessions = procurement_api
    second = consume_procurement_requested(message(2), SqlAlchemyProcurementUnitOfWork(sessions))
    taken = client.post("/api/procurement/take", json={"line_ids": [str(first.id), str(second.id)]}).json()["lines"]
    for item, supplier in zip(taken, ("ООО А", "ООО Б")):
        ready = client.patch(
            f"/api/procurement/{item['id']}/facts",
            json={"version": item["version"], "supplier": supplier, "actual_quantity": "2", "actual_unit": "шт"},
        )
        assert ready.json()["line"]["status"] == "Готов к запросу"

    picked = client.post("/api/procurement/supplier-requests", json={"line_ids": [str(second.id)]})
    nothing = client.post("/api/procurement/supplier-requests", json={"line_ids": [str(second.id)]})
    empty = client.post("/api/procurement/supplier-requests", json={"line_ids": []})

    assert picked.status_code == 200, picked.text
    assert [order["supplier"] for order in picked.json()["orders"]] == ["ООО Б"]
    assert [line["id"] for line in picked.json()["lines"]] == [str(second.id)]
    statuses = {line["id"]: line["status"] for line in client.get("/api/procurement/mine").json()["lines"]}
    assert statuses == {str(first.id): "Готов к запросу", str(second.id): "Жду счет"}
    # Owner, 05.10: a ticked line that cannot go is reported with the reason, the call does not fail.
    assert nothing.status_code == 200, nothing.text
    assert nothing.json()["orders"] == []
    assert nothing.json()["skipped"] == [{
        "line_id": str(second.id),
        "legacy_id": second.legacy_id,
        "reason": "запрос уже сформирован (PO-001), статус «Жду счет»",
    }]
    assert empty.status_code == 422


def test_buyer_can_take_and_complete_own_line_idempotently(procurement_api):
    client, line, sessions = procurement_api

    available = client.get("/api/procurement/available")
    first_take = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    retry_take = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    mine = client.get("/api/procurement/mine")
    facts = {
        "version": first_take.json()["lines"][0]["version"],
        "supplier": "ООО Поставка",
        "actual_quantity": "2",
        "actual_unit": "шт",
    }
    first_save = client.patch(f"/api/procurement/{line.id}/facts", json=facts)
    retry_save = client.patch(f"/api/procurement/{line.id}/facts", json=facts)
    stale_overwrite = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={**facts, "supplier": "Устаревшее значение"},
    )

    assert available.status_code == 200
    assert available.json()["lines"][0]["status"] == "Новый"
    assert first_take.status_code == 200, first_take.text
    assert retry_take.json() == first_take.json()
    assert mine.json()["lines"][0]["buyer"] == "Петров"
    assert first_save.status_code == 200, first_save.text
    assert first_save.json()["line"]["status"] == "Готов к запросу"
    assert retry_save.json() == first_save.json()
    assert stale_overwrite.status_code == 409
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 3


def test_link_accepts_free_text_from_old_data(procurement_api):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]

    saved = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": taken["version"], "supplier": "ООО Поставка", "actual_quantity": "2",
              "actual_unit": "шт", "link": "Склад"},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["line"]["link"] == "Склад"


def test_other_buyer_cannot_edit_claimed_line(procurement_api):
    client, line, _ = procurement_api
    client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сидоров", department="Закупки"),
        email="sidorov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )

    response = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": 2, "supplier": "Чужой поставщик"},
    )

    assert response.status_code == 422
    assert "только свои" in response.json()["detail"]


def test_explicit_empty_supplier_clears_it_and_oversized_is_rejected(procurement_api):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    version = taken.json()["lines"][0]["version"]
    ready = client.patch(f"/api/procurement/{line.id}/facts", json={"version": version, "supplier": "ООО А"})
    assert ready.json()["line"]["status"] == "Готов к запросу"  # КОЛ-ВО / ЕД.ИЗМ (факт) were prefilled by the take

    oversized = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": ready.json()["line"]["version"], "supplier": "x" * 513},
    )
    empty = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": ready.json()["line"]["version"], "supplier": "   "},
    )

    assert oversized.status_code == 422
    assert empty.status_code == 200, empty.text
    assert (empty.json()["line"]["supplier"], empty.json()["line"]["status"]) == (None, "В работе")


@pytest.mark.parametrize(
    "invoice_url",
    ["x", "javascript:alert(1)", "https://user:password@files.test/invoice.pdf"],
)
def test_invoice_requires_safe_absolute_http_url(procurement_api, invoice_url):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    ready = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": "ООО Поставка",
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    )
    assert ready.json()["line"]["status"] == "Готов к запросу"
    requested = client.post("/api/procurement/supplier-requests")

    response = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={
            "version": requested.json()["lines"][0]["version"],
            "invoice_url": invoice_url,
            "expected_arrival": "2026-09-25",
        },
    )

    assert response.status_code == 422


class StubRates:
    def __init__(self, rates=None):
        self.rates = dict(rates or {})

    def rate_to_rub(self, currency, day):
        return self.rates.get((currency, day.isoformat()))


def awaiting_invoice(client, line) -> int:
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    ready = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": "ООО Поставка",
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    )
    assert ready.json()["line"]["status"] == "Готов к запросу"
    requested = client.post("/api/procurement/supplier-requests")
    return requested.json()["lines"][0]["version"]


@pytest.fixture()
def rates():
    stub = StubRates()
    app.state.procurement_exchange_rates = stub
    yield stub
    del app.state.procurement_exchange_rates


def test_invoice_waits_for_fact_money_typed_as_in_invoices(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)
    invoice = {"invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"}

    refused = client.post(f"/api/procurement/{line.id}/invoice", json={"version": version, **invoice})
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Нельзя загрузить счёт — сначала заполните: ВАЛЮТА")

    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={
            "version": version, "currency": "rub", "price": "1 234,50", "vat_rate": "22%",
            "invoice_item_name": "Разъём SMA", "invoice_number": "17", "invoice_date": "2026-09-14",
        },
    )
    assert saved.status_code == 200
    body = saved.json()["line"]
    assert (body["currency"], body["actual_price"], body["actual_price_with_vat"], body["actual_sum"]) == (
        "RUB", "1234.5", "1506.09", "3012.18",
    )
    assert body["pay_missing"] == ["ОЖИД. ДАТА"]

    invoiced = client.post(f"/api/procurement/{line.id}/invoice", json={"version": body["version"], **invoice})
    assert invoiced.status_code == 200
    assert invoiced.json()["line"]["status"] == "В оплату"


@pytest.mark.parametrize("field,value", [("price", "abc"), ("price", "-1"), ("sum", "1.005")])
def test_money_rejects_garbage_negative_and_sub_kopeck_sum(procurement_api, rates, field, value):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)

    response = client.patch(
        f"/api/procurement/{line.id}/money", json={"version": version, "currency": "RUB", field: value},
    )

    assert response.status_code == 422


def test_cbr_outage_returns_manual_rate_prompt_then_marks_manual_rate(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)
    usd = {"currency": "USD", "price": "10", "vat_rate": "22", "invoice_item_name": "SMA"}

    prompt = client.patch(f"/api/procurement/{line.id}/money", json={"version": version, **usd})
    assert prompt.status_code == 409
    assert prompt.json()["detail"] == {
        "code": "manual_rate_required",
        "currency": "USD",
        "date": "2026-09-15",
        "message": "ЦБ не отдал курс USD на 15.09.2026 — введите курс вручную",
    }

    manual = client.patch(
        f"/api/procurement/{line.id}/money",
        json={"version": version, **usd, "manual_exchange_rate": "80,5"},
    )
    body = manual.json()["line"]
    assert (body["exchange_rate"], body["exchange_rate_manual"], body["actual_price"]) == (
        "80.5", True, "857.33",
    )

    stale = client.post(f"/api/procurement/{line.id}/exchange-rate/reconcile", json={"version": body["version"]})
    assert stale.status_code == 503
    rates.rates[("USD", "2026-09-15")] = Decimal("90")
    reconciled = client.post(
        f"/api/procurement/{line.id}/exchange-rate/reconcile", json={"version": body["version"]},
    )
    assert reconciled.status_code == 200
    assert (reconciled.json()["line"]["exchange_rate_manual"], reconciled.json()["line"]["actual_price"]) == (
        False, "958.5",
    )


def paid_with_backorder(client, line, sessions):
    """A paid line whose first inspection came short: receiving parks the remainder as a `-Д` line."""
    with sessions() as session:  # payment needs a delivery point with a receiving warehouse
        session.get(ProcurementLineRow, line.id).delivery_point = "г. Москва, ул. 6-я Радиальная, д. 17"
        session.commit()
    version = awaiting_invoice(client, line)
    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={"version": version, "currency": "RUB", "price": "100", "vat_rate": "22", "invoice_item_name": "SMA"},
    ).json()["line"]
    invoiced = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={"version": saved["version"], "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"},
    ).json()["line"]
    client.post(f"/api/procurement/{line.id}/paid", json={"version": invoiced["version"]})
    uow = SqlAlchemyProcurementUnitOfWork(sessions)
    with uow:
        uow.outbox.add(
            OutboxMessage(
                id=UUID("00000000-0000-0000-0000-00000000b001"),
                topic="procurement.backorder_requested",
                idempotency_key="receiving:api:backorder",
                created_at=datetime(2026, 9, 20, 10, 0),
                payload={"procurement_line_id": str(line.id), "backorder_quantity": "1"},
            )
        )
        uow.commit()
    return dispatch_next_backorder_requested(datetime(2026, 9, 20, 10, 1), SqlAlchemyProcurementUnitOfWork(sessions))


def test_backorder_stays_in_approval_with_its_own_cancel_while_manual_approval_is_off(procurement_api, rates):
    client, line, sessions = procurement_api
    backorder = paid_with_backorder(client, line, sessions)
    assert backorder.status.value == "Согласование"

    generic = client.post(f"/api/procurement/{backorder.id}/approval", json={"version": 1})
    cancelled = client.post(f"/api/procurement/{backorder.id}/backorder/cancel", json={"version": 1})

    assert generic.status_code == 422
    assert generic.json()["detail"] == "У дозавоза свои действия: подтвердить досыл или отменить дозавоз"
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["line"]["status"] == "Отменено"
    with sessions() as session:
        reorders = session.scalars(
            select(OutboxMessageRow).where(OutboxMessageRow.topic == "requests.reorder_requested")
        ).all()
    assert [row.payload["legacy_id"] for row in reorders] == ["SM-20260915-01-Д"]


def test_backorder_is_confirmed_through_api_with_version_check(procurement_api, rates):
    client, line, sessions = procurement_api
    with sessions() as session:  # payment needs a delivery point with a receiving warehouse
        session.get(ProcurementLineRow, line.id).delivery_point = "г. Москва, ул. 6-я Радиальная, д. 17"
        session.commit()
    version = awaiting_invoice(client, line)
    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={"version": version, "currency": "RUB", "price": "100", "vat_rate": "22", "invoice_item_name": "SMA"},
    ).json()["line"]
    invoiced = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={"version": saved["version"], "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"},
    ).json()["line"]
    paid = client.post(f"/api/procurement/{line.id}/paid", json={"version": invoiced["version"]}).json()["line"]
    assert paid["is_backorder"] is False
    refused = client.post(
        f"/api/procurement/{line.id}/backorder/confirm", json={"version": paid["version"], "expected_arrival": "2026-10-01"},
    )
    assert refused.status_code == 422

    uow = SqlAlchemyProcurementUnitOfWork(sessions)
    with uow:
        uow.outbox.add(
            OutboxMessage(
                id=UUID("00000000-0000-0000-0000-00000000b001"),
                topic="procurement.backorder_requested",
                idempotency_key="receiving:api:backorder",
                created_at=datetime(2026, 9, 20, 10, 0),
                payload={"procurement_line_id": str(line.id), "backorder_quantity": "1"},
            )
        )
        uow.commit()
    backorder = dispatch_next_backorder_requested(datetime(2026, 9, 20, 10, 1), SqlAlchemyProcurementUnitOfWork(sessions))
    mine = client.get("/api/procurement/mine").json()["lines"]
    listed = next(item for item in mine if item["id"] == str(backorder.id))
    assert (listed["legacy_id"], listed["status"], listed["is_backorder"], listed["actual_sum"]) == (
        "SM-20260915-01-Д", "Согласование", True, "122",
    )

    stale = client.post(
        f"/api/procurement/{backorder.id}/backorder/confirm", json={"version": 9, "expected_arrival": "2026-10-01"},
    )
    confirmed = client.post(
        f"/api/procurement/{backorder.id}/backorder/confirm", json={"version": 1, "expected_arrival": "2026-10-01"},
    )
    assert stale.status_code == 409
    assert confirmed.status_code == 200
    assert (confirmed.json()["line"]["status"], confirmed.json()["line"]["expected_arrival"]) == ("В пути", "2026-10-01")


def test_open_replacement_blocks_supplier_request_until_allowed(procurement_api):
    client, line, sessions = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    ready = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": "ООО Поставка",
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    ).json()["line"]
    assert ready["replacement"] is None
    assert ready["replacement_open"] is False

    opened = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": ready["version"], "replacement": True},
    )
    assert opened.status_code == 200, opened.text
    opened_line = opened.json()["line"]
    assert opened_line["replacement"] is True
    assert opened_line["replacement_decision"] is None
    assert opened_line["replacement_open"] is True

    blocked = client.post("/api/procurement/supplier-requests")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["orders"] == []
    assert blocked.json()["skipped"] == [{
        "line_id": str(line.id),
        "legacy_id": line.legacy_id,
        "reason": "ООО Поставка (Закупка): есть незакрытая замена (нет решения «Разрешено») — не сформирован",
    }]

    garbage = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": opened_line["version"], "replacement": True, "decision": "Может быть"},
    )
    assert garbage.status_code == 422
    stale = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": ready["version"], "replacement": True, "decision": "Разрешено"},
    )
    assert stale.status_code == 409

    allowed = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": opened_line["version"], "replacement": True, "decision": "Разрешено"},
    ).json()["line"]
    assert allowed["replacement_decision"] == "Разрешено"
    assert allowed["replacement_open"] is False
    assert allowed["permission_number"] == "РЗ-20260915-01"
    assert allowed["permission_date"] == "2026-09-15"

    requested = client.post("/api/procurement/supplier-requests")
    assert requested.status_code == 200, requested.text
    assert [order["order_number"] for order in requested.json()["orders"]] == ["PO-001"]
    assert requested.json()["lines"][0]["status"] == "Жду счет"
    with sessions() as session:
        replacement_events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.replacement_set")
        ).all()
        assert [event.details["decision"] for event in replacement_events] == ["", "Разрешено"]


def test_replacement_permission_document_prints_once_allowed_and_voids_when_forbidden(procurement_api):
    """DOC-1: «РАЗРЕШЕНИЕ НА ЗАМЕНУ» exists only while РЕШ. ЗАМЕНЫ is «Разрешено» (`applyReplacementDecision_`,
    Guards.gs:632-645)."""
    client, line, _sessions = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    ready = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": "ООО Поставка",
            "design_article": "КД-АРТ-001",
            "site_name": "Разъём-аналог XYZ",
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    ).json()["line"]

    not_ready = client.get(f"/api/procurement/{line.id}/replacement-permission")
    assert not_ready.status_code == 409

    opened = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": ready["version"], "replacement": True},
    ).json()["line"]
    allowed = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": opened["version"], "replacement": True, "decision": "Разрешено"},
    ).json()["line"]

    printed = client.get(f"/api/procurement/{line.id}/replacement-permission")
    assert printed.status_code == 200
    sheet = printed.json()["sheet"]
    assert sheet["title"] == "РАЗРЕШЕНИЕ НА ЗАМЕНУ РЗ-20260915-01"
    assert "RND.001" in sheet["subtitle"]
    assert ("Заменяется (артикул КД)", "КД-АРТ-001") in [tuple(row) for row in sheet["rows"]]
    assert ("Применяется", "Разъём-аналог XYZ") in [tuple(row) for row in sheet["rows"]]
    assert ("Поставщик", "ООО Поставка") in [tuple(row) for row in sheet["rows"]]
    assert ("Категория", "Общепром") in [tuple(row) for row in sheet["rows"]]
    assert ("Кол-во", "2 шт") in [tuple(row) for row in sheet["rows"]]
    assert "годен к применению" in sheet["note"]
    assert "ИСПОЛНИТЕЛЬ" in sheet["sign"] and "ЗАКАЗЧИК" in sheet["sign"]

    forbidden = client.patch(
        f"/api/procurement/{line.id}/replacement",
        json={"version": allowed["version"], "replacement": True, "decision": "Запрещено"},
    ).json()["line"]
    assert forbidden["permission_number"] is None
    assert forbidden["permission_date"] is None

    voided = client.get(f"/api/procurement/{line.id}/replacement-permission")
    assert voided.status_code == 409

    missing = client.get("/api/procurement/00000000-0000-0000-0000-000000000000/replacement-permission")
    assert missing.status_code == 404


def test_manual_approval_is_refused_and_a_line_parked_before_the_switch_resumes_through_api(procurement_api):
    client, line, sessions = procurement_api
    version = awaiting_invoice(client, line)

    refused = client.post(f"/api/procurement/{line.id}/approval", json={"version": version})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Этап согласования отключён: позиция идёт сразу к запросу поставщику"
    assert client.get("/api/procurement/mine").json()["lines"][0]["status"] == "Жду счет"

    with sessions() as session:  # parked before the owner switched the stage off (04.10.2026)
        row = session.get(ProcurementLineRow, line.id)
        row.status, row.version = "Согласование", version + 1
        session.commit()
    stale = client.post(f"/api/procurement/{line.id}/approval/resume", json={"version": version})
    resumed = client.post(f"/api/procurement/{line.id}/approval/resume", json={"version": version + 1})
    cancelled = client.post(f"/api/procurement/{line.id}/cancel", json={"version": resumed.json()["line"]["version"]})

    assert stale.status_code == 409
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["line"]["status"] == "Жду счет"
    assert cancelled.json()["line"]["status"] == "Отменено"
    assert all(item["id"] != str(line.id) for item in client.get("/api/procurement/mine").json()["lines"])
    with sessions() as session:
        events = session.scalars(
            select(AuditEventRow.event_type).where(AuditEventRow.aggregate_id == line.id)
        ).all()
    assert "procurement.approval_requested" not in events
    assert events[-2:] == ["procurement.approved", "procurement.cancelled"]


def test_foreign_buyer_cannot_cancel_claimed_line(procurement_api):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сидоров", department="Закупки"),
        email="sidorov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )

    refused = client.post(f"/api/procurement/{line.id}/cancel", json={"version": taken["version"]})

    assert refused.status_code == 422
    assert "только свои строки" in refused.json()["detail"]


def test_invoice_replacement_keeps_status_and_audits_the_old_invoice(procurement_api, rates):
    client, line, sessions = procurement_api
    version = awaiting_invoice(client, line)
    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={
            "version": version, "currency": "RUB", "price": "100", "vat_rate": "22",
            "invoice_item_name": "Разъём SMA", "invoice_number": "17", "invoice_date": "2026-09-14",
        },
    ).json()["line"]
    invoiced = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={"version": saved["version"], "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"},
    ).json()["line"]

    replaced = client.post(
        f"/api/procurement/{line.id}/invoice/replace",
        json={"version": invoiced["version"], "invoice_url": "https://files.test/invoice-v2.pdf", "invoice_number": "18"},
    )

    assert replaced.status_code == 200
    body = replaced.json()["line"]
    assert (body["status"], body["invoice_url"], body["invoice_number"], body["invoice_date"]) == (
        "В оплату", "https://files.test/invoice-v2.pdf", "18", "2026-09-14",
    )
    with sessions() as session:
        event = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.invoice_replaced")
        ).one()
    assert (event.details["previous_invoice_url"], event.details["previous_invoice_number"]) == (
        "https://files.test/invoice.pdf", "17",
    )

    stale = client.post(
        f"/api/procurement/{line.id}/invoice/replace",
        json={"version": invoiced["version"], "invoice_url": "https://files.test/invoice-v3.pdf"},
    )
    assert stale.status_code == 409


def test_invoice_replacement_needs_an_existing_invoice(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)

    refused = client.post(
        f"/api/procurement/{line.id}/invoice/replace",
        json={"version": version, "invoice_url": "https://files.test/invoice-v2.pdf"},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Счёта ещё нет")
def kp_row(line, version, **fields) -> dict:
    return {"line_id": str(line.id), "version": version, **fields}


KP_ROW = {
    "actual_quantity": "3", "actual_unit": "шт", "currency": "RUB", "price": "100", "vat_rate": "22",
    "invoice_item_name": "Разъём SMA", "invoice_number": "17", "invoice_date": "2026-09-15",
    "expected_arrival": "2026-09-25",
}


def second_line(client, sessions, suffix: int = 2):
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    return consume_procurement_requested(message(suffix), factory())


def order_lines(client, *lines, supplier: str = "ООО Поставка") -> tuple[str, list[int]]:
    """Takes the lines to «Готов к запросу» with one supplier and builds one supplier order (PR-3).

    Returns the order number main gave it (PO-NNN) and each line's version, in the order given.
    """
    for line in lines:
        taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
        ready = client.patch(
            f"/api/procurement/{line.id}/facts",
            json={
                "version": taken.json()["lines"][0]["version"],
                "supplier": supplier,
                "actual_quantity": "2",
                "actual_unit": "шт",
            },
        )
        assert ready.json()["line"]["status"] == "Готов к запросу"
    requested = client.post("/api/procurement/supplier-requests")
    assert requested.status_code == 200, requested.text
    by_id = {item["id"]: item for item in requested.json()["lines"]}
    (number,) = {by_id[str(line.id)]["order_number"] for line in lines}
    return number, [by_id[str(line.id)]["version"] for line in lines]


def test_kp_form_opens_only_on_order_lines_waiting_for_invoice(procurement_api, rates):
    client, line, sessions = procurement_api
    other = second_line(client, sessions)

    assert client.get("/api/procurement/kp", params={"reference": "PO-001"}).status_code == 422
    number, _ = order_lines(client, line, other)

    opened = client.get("/api/procurement/kp", params={"reference": f" {number} "})
    assert opened.status_code == 200
    assert {item["legacy_id"] for item in opened.json()["lines"]} == {line.legacy_id, other.legacy_id}
    missing = client.get("/api/procurement/kp", params={"reference": "REQ-OTHER"})
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Нет строк заказа REQ-OTHER в статусе «Жду счет»"


def test_kp_saves_whole_order_without_moving_status(procurement_api, rates):
    client, line, sessions = procurement_api
    other = second_line(client, sessions)
    number, (first_version, second_version) = order_lines(client, line, other)

    saved = client.post(
        "/api/procurement/kp",
        json={
            "order_reference": number,
            "rows": [
                kp_row(line, first_version, **KP_ROW),
                kp_row(other, second_version, currency="RUB", sum="1 220,00", vat_rate="22", actual_quantity="2"),
            ],
        },
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["invoice_error"] == ""
    first, second = body["lines"]
    assert first["status"] == second["status"] == "Жду счет"
    assert (first["actual_quantity"], first["actual_price_with_vat"], first["actual_sum"]) == ("3", "122", "366")
    assert (first["expected_arrival"], first["invoice_number"]) == ("2026-09-25", "17")
    assert first["pay_missing"] == []
    assert (second["actual_price_with_vat"], second["actual_price"]) == ("610", "500")
    # The form sends every field: an omitted one is a cleared cell, as `submitKp_` writes all KP_EDIT_FIELDS.
    assert second["actual_unit"] is None
    assert second["pay_missing"] == ["ЕД.ИЗМ (факт)", "НАИМЕНОВАНИЕ (Счет)", "ОЖИД. ДАТА"]
    with sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.kp_saved")
        ).all()
    assert len(events) == 2
    assert events[0].details["order_reference"] == number


def test_kp_invoice_moves_every_order_line_to_payment(procurement_api, rates):
    client, line, sessions = procurement_api
    other = second_line(client, sessions)
    number, (first_version, second_version) = order_lines(client, line, other)
    rows = [kp_row(line, first_version, **KP_ROW), kp_row(other, second_version, **KP_ROW)]

    saved = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": rows, "invoice_url": "https://files.test/inv.pdf"},
    )

    assert saved.status_code == 200
    assert saved.json()["invoice_error"] == ""
    assert [item["status"] for item in saved.json()["lines"]] == ["В оплату", "В оплату"]
    assert {item["invoice_url"] for item in saved.json()["lines"]} == {"https://files.test/inv.pdf"}


def test_kp_invoice_refused_by_gate_keeps_offer_data(procurement_api, rates):
    client, line, sessions = procurement_api
    other = second_line(client, sessions)
    incomplete = {key: value for key, value in KP_ROW.items() if key != "expected_arrival"}
    number, (first_version, second_version) = order_lines(client, line, other)
    rows = [kp_row(line, first_version, **KP_ROW), kp_row(other, second_version, **incomplete)]

    saved = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": rows, "invoice_url": "https://files.test/inv.pdf"},
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["invoice_error"] == f"{other.legacy_id}: Нельзя загрузить счёт — сначала заполните: ОЖИД. ДАТА"
    assert [item["status"] for item in body["lines"]] == ["Жду счет", "Жду счет"]
    assert body["lines"][0]["actual_sum"] == "366"
    mine = client.get("/api/procurement/mine").json()["lines"]
    assert {item["status"] for item in mine} == {"Жду счет"}
    assert {item["actual_sum"] for item in mine} == {"366"}


def test_kp_rejects_line_of_another_order_and_rolls_back(procurement_api, rates):
    client, line, sessions = procurement_api
    other = second_line(client, sessions)
    number, (first_version,) = order_lines(client, line)
    other_number, (other_version,) = order_lines(client, other, supplier="ООО")
    assert other_number != number

    refused = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": [kp_row(line, first_version, **KP_ROW), kp_row(other, other_version, **KP_ROW)]},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"] == f"{other.legacy_id} не входит в заказ {number}"
    mine = {item["id"]: item for item in client.get("/api/procurement/mine").json()["lines"]}
    assert mine[str(line.id)]["actual_sum"] is None


def test_kp_is_closed_after_invoice_and_checks_quantity(procurement_api, rates):
    client, line, _ = procurement_api
    number, (version,) = order_lines(client, line)

    zero = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": [kp_row(line, version, **{**KP_ROW, "actual_quantity": "0"})]},
    )
    assert zero.status_code == 422
    invoiced = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": [kp_row(line, version, **KP_ROW)], "invoice_url": "https://files.test/i.pdf"},
    )
    paid_version = invoiced.json()["lines"][0]["version"]

    closed = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": [kp_row(line, paid_version, **KP_ROW)]},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"].startswith("«Внести КП» доступно только в статусе «Жду счет»")


def test_kp_foreign_currency_converts_at_invoice_date_rate(procurement_api, rates):
    client, line, _ = procurement_api
    number, (version,) = order_lines(client, line)
    rates.rates[("USD", "2026-09-15")] = Decimal("80")

    saved = client.post(
        "/api/procurement/kp",
        json={"order_reference": number, "rows": [kp_row(line, version, **{**KP_ROW, "currency": "usd", "price": "10"})]},
    )

    assert saved.status_code == 200
    body = saved.json()["lines"][0]
    assert (body["currency"], body["source_currency"], body["actual_price"]) == ("RUB", "USD", "852")
    assert body["exchange_rate_date"] == "2026-09-15"


def test_kp_parse_matches_offer_rows_to_order_lines(procurement_api, rates):
    import base64

    client, line, _ = procurement_api
    number, _ = order_lines(client, line)
    csv_text = "КП № 5 от ООО Поставка\n\nНаименование;Кол-во;Цена, руб;НДС %\nРАЗЪЁМ!;3;100,50;22\nКабель;1;5;22\n"

    parsed = client.post(
        "/api/procurement/kp/parse",
        json={
            "order_reference": number,
            "file_name": "kp.csv",
            "content_base64": base64.b64encode(csv_text.encode("cp1251")).decode(),
        },
    )

    assert parsed.status_code == 200
    body = parsed.json()
    assert body["matches"] == [{"line_id": str(line.id), "name": "РАЗЪЁМ!", "qty": "3", "price": "100,50", "vat": "22"}]
    assert body["not_found"] == ["Кабель"]
    broken = client.post(
        "/api/procurement/kp/parse",
        json={"order_reference": number, "file_name": "kp.xls", "content_base64": "AAAA"},
    )
    assert broken.status_code == 422
    assert broken.json()["detail"] == "Поддерживаются файлы КП .xlsx и .csv"


class _Directory:
    def supplier_map(self):
        return {"chipdip.ru": "ЧипДип"}

    def learn(self, link, supplier, actor, now):
        return False

    def country_for_manufacturer(self, manufacturer):
        return ""


def test_take_fills_the_supplier_from_the_link_domain_and_queues_the_page_fields(procurement_api):
    client, _, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    linked = consume_procurement_requested(
        OutboxMessage(
            id=UUID(int=2), topic="procurement.requested", idempotency_key="request:2:procurement:v2",
            created_at=datetime(2026, 9, 15, 11, 0),
            payload={**message(2).payload, "link": "https://www.chipdip.ru/product/1"},
        ),
        factory(),
    )
    app.state.procurement_supplier_directory = _Directory()
    try:
        response = client.post("/api/procurement/take", json={"line_ids": [str(linked.id)]})
    finally:
        del app.state.procurement_supplier_directory

    assert response.status_code == 200, response.text
    assert response.json()["lines"][0]["supplier"] == "ЧипДип"
    with sessions() as session:
        queued = session.scalars(
            select(OutboxMessageRow).where(OutboxMessageRow.topic == "procurement.autofill_requested")
        ).all()
        autofilled = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.autofilled")
        ).all()
    assert [row.payload["line_id"] for row in queued] == [str(linked.id)]
    assert [row.details["fields"] for row in autofilled] == [["supplier"]]


# --- Заказ целиком (owner, 05.10): «Цена факт» по строкам, счёт и оплата один раз на заказ ------------------------

MOSCOW = "г. Москва, ул. 6-я Радиальная, д. 17"


def formed_order(client, sessions, count: int = 2) -> tuple[str, dict[str, dict]]:
    """`count` lines of one supplier order «Жду счет», formed through the API (the Moscow point: Receiving takes it)."""
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    created = [
        consume_procurement_requested(
            OutboxMessage(**{**message(n).__dict__, "payload": {**message(n).payload, "delivery_point": MOSCOW}}),
            factory(),
        )
        for n in range(2, 2 + count)
    ]
    ids = [str(line.id) for line in created]
    taken = client.post("/api/procurement/take", json={"line_ids": ids}).json()["lines"]
    saved = client.post("/api/procurement/facts", json={"rows": [
        {"line_id": item["id"], "version": item["version"], "supplier": "ООО Поставка"} for item in taken
    ]}).json()
    assert saved["ready"] == count, saved
    formed = client.post("/api/procurement/supplier-requests", json={"line_ids": ids}).json()
    (order,) = formed["orders"]
    return order["order_number"], {line["id"]: line for line in formed["lines"]}


def price_rows(client, lines: dict[str, dict], price: str = "1220") -> dict[str, dict]:
    body = client.post("/api/procurement/fact-prices", json={"rows": [
        {"line_id": line["id"], "version": line["version"], "legacy_id": line["legacy_id"], "price_with_vat": price}
        for line in lines.values()
    ]}).json()
    assert body["saved"] == len(lines), body
    return {line["id"]: line for line in body["lines"]}


def refs(lines: dict[str, dict]) -> list[dict]:
    return [{"line_id": line["id"], "version": line["version"]} for line in lines.values()]


def audit_types(sessions) -> list[str]:
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow.event_type)))


def test_fact_prices_are_saved_row_by_row_and_a_stale_row_is_skipped(procurement_api):
    client, _, sessions = procurement_api
    _, lines = formed_order(client, sessions)
    first, second = lines.values()

    response = client.post("/api/procurement/fact-prices", json={"rows": [
        {"line_id": first["id"], "version": first["version"], "legacy_id": first["legacy_id"], "price_with_vat": "1 220,00"},
        {"line_id": second["id"], "version": 99, "legacy_id": second["legacy_id"], "price_with_vat": "500"},
    ]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["saved"], body["skipped"], body["failed"]) == (1, [second["legacy_id"]], [])
    priced = body["lines"][0]
    assert (priced["status"], priced["currency"], priced["actual_vat_rate"]) == ("Жду счет", "RUB", "22")
    assert (priced["actual_price"], priced["actual_price_with_vat"], priced["actual_sum"]) == ("1000", "1220", "2440")
    with sessions() as session:
        (event,) = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.fact_price_saved")
        ).all()
    assert (event.details["previous_price_with_vat"], event.details["price_with_vat"]) == ("", "1220.00")
    assert event.details["order_reference"] == priced["order_number"]

    # The same price on the version the form was loaded with is a read-only retry, not «пропущено».
    retry = client.post("/api/procurement/fact-prices", json={"rows": [
        {"line_id": first["id"], "version": first["version"], "price_with_vat": "1220"},
    ]}).json()
    assert (retry["saved"], retry["skipped"], retry["lines"][0]["version"]) == (1, [], priced["version"])
    cleared = client.post("/api/procurement/fact-prices", json={"rows": [
        {"line_id": first["id"], "version": priced["version"], "price_with_vat": None},
    ]}).json()["lines"][0]
    assert (cleared["actual_price"], cleared["actual_price_with_vat"], cleared["actual_sum"]) == (None, None, None)
    assert client.post("/api/procurement/fact-prices", json={"rows": []}).status_code == 422


def test_order_invoice_moves_every_awaiting_row_to_payment_and_then_replaces_it(procurement_api):
    client, _, sessions = procurement_api
    number, lines = formed_order(client, sessions)
    lines = price_rows(client, lines)
    first_id = next(iter(lines))
    invoice = {"invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"}

    partial = client.post(f"/api/procurement/orders/{number}/invoice", json={**invoice, "lines": refs(lines)[:1]})
    stale_refs = [{**ref, "version": ref["version"] - 1} if ref["line_id"] == first_id else ref for ref in refs(lines)]
    stale = client.post(f"/api/procurement/orders/{number}/invoice", json={**invoice, "lines": stale_refs})
    unknown = client.post("/api/procurement/orders/PO-404/invoice", json={**invoice, "lines": refs(lines)})
    twice = client.post(f"/api/procurement/orders/{number}/invoice", json={**invoice, "lines": refs(lines) * 2})

    assert partial.status_code == 409 and "«Жду счет» (2)" in partial.json()["detail"]
    assert stale.status_code == 409 and "изменилась" in stale.json()["detail"]
    assert unknown.status_code == 404
    assert twice.status_code == 422
    assert {line["status"] for line in client.get("/api/procurement/mine").json()["lines"] if line["id"] in lines} == {
        "Жду счет"
    }

    recorded = client.post(f"/api/procurement/orders/{number}/invoice", json={
        **invoice, "invoice_number": "СЧ-5", "invoice_date": "2026-09-16", "lines": refs(lines),
    })

    assert recorded.status_code == 200, recorded.text
    invoiced = {line["id"]: line for line in recorded.json()["lines"]}
    assert set(invoiced) == set(lines)
    for line in invoiced.values():
        assert (line["status"], line["invoice_url"], line["invoice_number"], line["invoice_date"]) == (
            "В оплату", "https://files.test/invoice.pdf", "СЧ-5", "2026-09-16",
        )
        # НАИМЕНОВАНИЕ (Счет) took the calculated name (no site name on these rows); ОЖИД. ДАТА is the form's.
        assert (line["invoice_item_name"], line["expected_arrival"], line["pay_missing"]) == ("Разъём", "2026-09-25", [])
        assert line["version"] == lines[line["id"]]["version"] + 1

    replaced = client.post(f"/api/procurement/orders/{number}/invoice", json={
        "invoice_url": "https://files.test/invoice-2.pdf", "expected_arrival": "2026-09-28", "lines": refs(invoiced),
    })

    assert replaced.status_code == 200, replaced.text
    for line in replaced.json()["lines"]:
        assert (line["status"], line["invoice_url"], line["invoice_number"], line["expected_arrival"]) == (
            "В оплату", "https://files.test/invoice-2.pdf", "СЧ-5", "2026-09-28",
        )
    events = audit_types(sessions)
    assert [events.count(kind) for kind in (
        "procurement.invoice_recorded", "procurement.invoice_replaced", "procurement.expected_arrival_set",
    )] == [2, 2, 2]


def test_order_invoice_is_all_or_none_and_names_the_row_the_pay_gate_refuses(procurement_api):
    client, _, sessions = procurement_api
    number, lines = formed_order(client, sessions)
    first, second = lines.values()
    priced = price_rows(client, {first["id"]: first})

    response = client.post(f"/api/procurement/orders/{number}/invoice", json={
        "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25",
        "lines": refs({**priced, second["id"]: second}),
    })

    assert response.status_code == 422
    assert response.json()["detail"].startswith(f"{second['legacy_id']}: Нельзя загрузить счёт — сначала заполните: ")
    assert "ЦЕНА (факт)" in response.json()["detail"]
    mine = {line["id"]: line for line in client.get("/api/procurement/mine").json()["lines"]}
    assert (mine[first["id"]]["status"], mine[first["id"]]["invoice_url"]) == ("Жду счет", None)
    assert mine[first["id"]]["invoice_item_name"] is None  # nothing written, the default name included
    assert "procurement.invoice_recorded" not in audit_types(sessions)


def test_order_is_paid_once_for_every_row_in_payment(procurement_api):
    client, _, sessions = procurement_api
    number, lines = formed_order(client, sessions)
    lines = price_rows(client, lines)
    invoiced = client.post(f"/api/procurement/orders/{number}/invoice", json={
        "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25", "lines": refs(lines),
    }).json()["lines"]
    invoiced = {line["id"]: line for line in invoiced}
    first_id = next(iter(invoiced))

    partial = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(invoiced)[:1]})
    stale = client.post(f"/api/procurement/orders/{number}/paid", json={
        "lines": [{**ref, "version": 1} if ref["line_id"] == first_id else ref for ref in refs(invoiced)],
    })
    assert partial.status_code == 409 and "«В оплату» (2)" in partial.json()["detail"]
    assert stale.status_code == 409

    paid = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(invoiced)})

    assert paid.status_code == 200, paid.text
    assert {(line["status"], line["paid_at"] is not None) for line in paid.json()["lines"]} == {("В пути", True)}
    with sessions() as session:
        expected = session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == "receiving.expected")).all()
        paid_events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.paid")
        ).all()
    assert sorted(row.payload["procurement_line_id"] for row in expected) == sorted(invoiced)
    assert {row.payload["order_number"] for row in expected} == {number}
    assert [event.details["order_reference"] for event in paid_events] == [number, number]
    again = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(invoiced)})
    assert again.status_code == 409  # nothing «В оплату» any more


def test_order_payment_asks_for_a_manual_rate_like_the_single_row_payment(procurement_api, rates):
    client, _, sessions = procurement_api
    number, lines = formed_order(client, sessions, count=1)
    (line,) = lines.values()
    rates.rates[("USD", "2026-09-16")] = Decimal("90")
    usd = client.patch(f"/api/procurement/{line['id']}/money", json={
        "version": line["version"], "currency": "USD", "price": "10", "vat_rate": "22",
        "invoice_item_name": "Разъём", "invoice_date": "2026-09-16",
    }).json()["line"]

    refused = client.post("/api/procurement/fact-prices", json={"rows": [
        {"line_id": usd["id"], "version": usd["version"], "legacy_id": usd["legacy_id"], "price_with_vat": "100"},
    ]}).json()
    assert refused["saved"] == 0
    assert refused["failed"] == [{"legacy_id": usd["legacy_id"], "error": "Счёт в USD: цену меняйте через «Внести КП»"}]

    (invoiced,) = client.post(f"/api/procurement/orders/{number}/invoice", json={
        "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25",
        "lines": [{"line_id": usd["id"], "version": usd["version"]}],
    }).json()["lines"]
    ref = [{"line_id": invoiced["id"], "version": invoiced["version"]}]

    asked = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": ref})
    assert asked.status_code == 409
    assert asked.json()["detail"] == {
        "code": "manual_rate_required", "currency": "USD", "date": "2026-09-15",
        "message": "ЦБ не отдал курс USD на 15.09.2026 — введите курс вручную",
    }

    paid = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": ref, "manual_exchange_rate": "95"})
    assert paid.status_code == 200, paid.text
    (done,) = paid.json()["lines"]
    assert (done["status"], done["exchange_rate"], done["exchange_rate_manual"]) == ("В пути", "95", True)


def test_mine_returns_lines_accepted_in_the_last_30_days_but_never_cancelled(procurement_api):
    client, line, sessions = procurement_api  # «сегодня» в тесте — 15.09.2026
    lines = [line, *(second_line(client, sessions, suffix) for suffix in (2, 3, 4, 5))]
    taken = client.post("/api/procurement/take", json={"line_ids": [str(item.id) for item in lines]})
    assert taken.status_code == 200, taken.text
    recent, old, cancelled, no_date, active = lines
    with sessions() as session:
        for item, status, arrival in (
            (recent, "Готово", date(2026, 9, 5)),  # 10 дней назад
            (old, "Готово", date(2026, 8, 6)),  # 40 дней назад
            (cancelled, "Отменено", date(2026, 9, 10)),
            (no_date, "Готово", None),  # без ФАКТ. ДАТЫ — по дню создания строки (15.09)
            (active, "В пути", None),
        ):
            row = session.get(ProcurementLineRow, item.id)
            row.status, row.actual_arrival = status, arrival
        session.commit()

    returned = {item["id"] for item in client.get("/api/procurement/mine?limit=500").json()["lines"]}
    assert returned == {str(recent.id), str(no_date.id), str(active.id)}

    # Курсорные страницы по 2 строки отдают те же строки без пропусков и повторов.
    seen, after = [], None
    while True:
        page = client.get("/api/procurement/mine", params={"limit": 2, **({"after": after} if after else {})}).json()
        seen += [item["id"] for item in page["lines"]]
        after = page["next_cursor"]
        if not after:
            break
    assert sorted(seen) == sorted(returned)