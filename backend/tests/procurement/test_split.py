"""PR-9: «Раздробить по поставщикам» (`Pipeline.gs:127-220`, `Portal.gs:1514-1531`)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.procurement.domain import (
    ProcurementStatus,
    ProcurementValidationError,
    create_line_from_requested,
    next_system_id,
    split_line,
)
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.procurement.test_api import procurement_api  # noqa: F401 — shared fixture
from tests.procurement.test_domain import requested_message

NOW = datetime(2026, 9, 29, 10, 0)
DAY = date(2026, 9, 29)


def worked_line():
    """A line the buyer has already filled: supplier, facts, order, invoice, money must not leak."""
    line = create_line_from_requested(requested_message())
    buyer = Actor(name="Петров", department="Закупки")
    return replace(
        line.take_in_work(buyer),
        link="https://example.test/refined",
        design_article="SMA-002",
        calculated_name="Разъём SMA уточн.",
        supplier="ООО Поставка",
        site_name="Площадка",
        actual_quantity=Decimal("2"),
        actual_unit="шт",
        supplier_request_reference="PO-001",
        invoice_url="https://files.test/invoice.pdf",
        invoice_number="17",
        status=ProcurementStatus.TO_PAY,
    )


def test_copies_keep_request_data_and_drop_everything_the_buyer_filled():
    source = worked_line()

    copies = split_line(source, 2, ["SM-20260929-01", "SM-20260929-03-Д", "SM-20260928-09"], DAY, NOW)

    assert [copy.legacy_id for copy in copies] == ["SM-20260929-04", "SM-20260929-05"]
    for copy in copies:
        assert (copy.status, copy.revision, copy.version, copy.created_at) == (
            ProcurementStatus.NEW, "A", 1, NOW,
        )
        assert copy.id != source.id and copy.source_event_id != source.source_event_id
        # REQUEST_TO_PURCHASE_MAP columns, as they stand on the source row now
        assert (copy.source_request_id, copy.designation, copy.priority, copy.delivery_point) == (
            source.source_request_id, source.designation, source.priority, source.delivery_point,
        )
        assert (copy.link, copy.design_article, copy.calculated_name) == (
            "https://example.test/refined", "SMA-002", "Разъём SMA уточн.",
        )
        assert (copy.calculated_quantity, copy.calculated_sum, copy.quality_document) == (
            source.calculated_quantity, source.calculated_sum, source.quality_document,
        )
        # supplier / order / invoice / money are cleared
        assert copy.buyer is None and copy.supplier is None and copy.site_name is None
        assert copy.actual_quantity is None and copy.actual_unit is None
        assert copy.supplier_request_reference is None and copy.invoice_url is None
        assert copy.invoice_number is None and copy.paid_at is None and copy.money.currency is None
    assert len({copy.source_idempotency_key for copy in copies}) == 2


@pytest.mark.parametrize("copies", [0, 31, -1, True, 2.5, "3"])
def test_copy_count_is_one_to_thirty(copies):
    with pytest.raises(ProcurementValidationError, match="1–30"):
        split_line(worked_line(), copies, [], DAY, NOW)


def test_thirty_copies_get_consecutive_ids():
    copies = split_line(worked_line(), 30, [], DAY, NOW)

    assert copies[0].legacy_id == "SM-20260929-01"
    assert copies[-1].legacy_id == "SM-20260929-30"
    assert len({copy.legacy_id for copy in copies}) == 30


def test_system_id_ignores_other_days_and_garbage_tails():
    assert next_system_id([], DAY) == "SM-20260929-01"
    assert next_system_id(["SM-20260928-50", "SM-20260929-x", "SM-20260929-07-Д2"], DAY) == "SM-20260929-08"


def test_copies_land_on_the_splitters_desk_with_an_empty_supplier():
    """Owner, 05.10: the copies are the splitter's «В работе», facts prefilled, ПОСТАВЩИК empty."""
    splitter = Actor(name="Сидоров", department="Закупки")

    copies = split_line(worked_line(), 2, [], DAY, NOW, buyer=splitter)

    for copy in copies:
        assert (copy.status, copy.buyer, copy.supplier) == (ProcurementStatus.IN_WORK, splitter, None)
        assert (copy.actual_quantity, copy.actual_unit) == (copy.calculated_quantity, copy.calculated_unit)
        assert copy.invoice_url is None and copy.money.currency is None
    with pytest.raises(ProcurementValidationError, match="закупщик"):
        split_line(worked_line(), 1, [], DAY, NOW, buyer=Actor(name=" ", department="Закупки"))


def test_split_through_api_puts_copies_on_the_splitters_desk(procurement_api):  # noqa: F811
    client, line, sessions = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]

    response = client.post(f"/api/procurement/{line.id}/split", json={"version": taken["version"], "copies": 2})
    stale = client.post(f"/api/procurement/{line.id}/split", json={"version": taken["version"], "copies": 2})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["line"]["status"], body["line"]["version"]) == ("В работе", taken["version"] + 1)
    # the fixture's line is SM-20260915-01; the split happens on 15.09 (Moscow clock override)
    assert [copy["legacy_id"] for copy in body["copies"]] == ["SM-20260915-02", "SM-20260915-03"]
    assert all(
        (copy["status"], copy["buyer"], copy["supplier"], copy["actual_quantity"]) == ("В работе", "Петров", None, "2")
        for copy in body["copies"]
    )
    assert stale.status_code == 409
    assert client.get("/api/procurement/available").json()["lines"] == []
    mine = {item["legacy_id"] for item in client.get("/api/procurement/mine").json()["lines"]}
    assert mine == {line.legacy_id, "SM-20260915-02", "SM-20260915-03"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 3
        events = list(session.scalars(select(AuditEventRow.event_type)))
    assert events.count("procurement.split") == 1
    assert events.count("procurement.created") == 3
    assert events.count("procurement.taken_in_work") == 3  # the source line's take + the two copies


def test_split_rejects_bad_count_and_is_open_to_any_buyer(procurement_api):  # noqa: F811
    client, line, sessions = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]

    assert client.post(f"/api/procurement/{line.id}/split", json={"version": taken["version"], "copies": 31}).status_code == 422
    assert client.post(f"/api/procurement/{line.id}/split", json={"version": taken["version"], "copies": 0}).status_code == 422

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сидоров", department="Закупки"),
        email="sidorov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    other = client.post(f"/api/procurement/{line.id}/split", json={"version": taken["version"], "copies": 1})
    assert other.status_code == 200, other.text
    assert other.json()["line"]["buyer"] == "Петров"

    app.dependency_overrides.pop(authenticated_principal)
    assert client.post(f"/api/procurement/{line.id}/split", json={"version": 9, "copies": 1}).status_code == 401
