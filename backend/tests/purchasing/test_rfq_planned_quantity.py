"""Planned purchase quantities are versioned buyer intent, not project demand or stock writes."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.infrastructure import RfqLineRow, RfqRow
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.procurement.test_api import message
from tests.purchasing.test_rfq_api import BASE, NOW, _as, _quote, _supplier, rfq_api


def _body(line, quantity="7.25"):
    return {"line_ids": [str(line.id)], "planned_lines": [{
        "id": str(line.id), "expected_version": line.version, "planned_quantity": quantity,
    }]}


@pytest.mark.parametrize("planned", ["1.25", "7.25"])
def test_planned_purchase_flows_into_quote_decision_but_preserves_project_demand(rfq_api, planned):
    client, _, sessions = rfq_api
    line = consume_procurement_requested(message(3), app.state.procurement_uow_factory())
    result = client.post(BASE, json=_body(line, planned))
    assert result.status_code == 201, result.text
    rfq_id = result.json()["id"]
    with sessions() as session:
        purchase = session.get(ProcurementLineRow, line.id)
        assert purchase.calculated_quantity == line.calculated_quantity
        assert purchase.stock_covered == 0
        stored = session.scalar(select(RfqLineRow).where(RfqLineRow.rfq_id == UUID(rfq_id)))
        assert stored.quantity == Decimal(planned)
    supplier = _supplier(client, rfq_id, "Поставщик плана")
    matrix = client.get(f"{BASE}/{rfq_id}").json()
    rfq_line = matrix["lines"][0]
    assert rfq_line["quantity"] == planned
    _quote(client, rfq_id, supplier, rfq_line["id"], unit_price="10")
    matrix = client.get(f"{BASE}/{rfq_id}").json()
    cell = matrix["lines"][0]["cells"][supplier]
    assert cell["cover_qty"] == planned
    decision = client.post(f"{BASE}/{rfq_id}/decisions", json={
        "rfq_line_id": rfq_line["id"], "quote_line_id": cell["quote_line_id"],
        "gate_override_reason": "Согласован единственный поставщик",
    })
    assert decision.status_code == 201, decision.text
    with sessions() as session:
        purchase = session.get(ProcurementLineRow, line.id)
        assert purchase.actual_quantity == Decimal(planned)
        assert purchase.calculated_quantity == line.calculated_quantity and purchase.stock_covered == 0
    letters = client.get(f"{BASE}/{rfq_id}/invoice-requests").json()["requests"]
    assert letters[0]["lines"][0]["qty"] == planned


@pytest.mark.parametrize("failure", ["stale", "duplicate", "missing", "extra", "zero", "negative", "nan", "large", "precision"])
def test_invalid_plan_never_claims_or_writes(rfq_api, failure):
    client, _, sessions = rfq_api
    line = consume_procurement_requested(message(3), app.state.procurement_uow_factory())
    body = _body(line)
    item = body["planned_lines"][0]
    if failure == "stale":
        item["expected_version"] += 1
    elif failure == "duplicate":
        body["planned_lines"].append(dict(item))
    elif failure == "missing":
        body["planned_lines"] = []
    elif failure == "extra":
        body["planned_lines"].append({**item, "id": str(uuid4())})
    else:
        item["planned_quantity"] = {"zero": "0", "negative": "-1", "nan": "NaN",
                                    "large": "1000000000000", "precision": "1.0000001"}[failure]
    with sessions() as session:
        counts = [session.scalar(select(func.count()).select_from(table))
                  for table in (AuditEventRow, OutboxMessageRow)]
    response = client.post(BASE, json=body)
    assert response.status_code == (409 if failure == "stale" else 422), response.text
    with sessions() as session:
        purchase = session.get(ProcurementLineRow, line.id)
        assert purchase.buyer_id is None and purchase.version == line.version
        assert session.scalar(select(func.count()).select_from(RfqRow)) == 0
        assert [session.scalar(select(func.count()).select_from(table))
                for table in (AuditEventRow, OutboxMessageRow)] == counts


@pytest.mark.parametrize("failure", ["paid", "closed", "backorder", "covered", "foreign", "supplier"])
def test_explicit_plan_preserves_guards_and_atomic_rollback(rfq_api, failure):
    client, owned, sessions = rfq_api
    line = consume_procurement_requested(message(3), app.state.procurement_uow_factory())
    with sessions() as session:
        row = session.get(ProcurementLineRow, line.id)
        if failure == "paid":
            row.paid_at = NOW
        elif failure == "closed":
            row.status = "Отменено"
        elif failure == "backorder":
            row.legacy_id += "-Д"
        elif failure == "covered":
            row.stock_covered = row.calculated_quantity
        session.commit()
    body = _body(line)
    if failure == "foreign":
        with sessions() as session:
            other = session.get(ProcurementLineRow, owned[0].id)
            body["line_ids"].append(str(other.id))
            body["planned_lines"].append({"id": str(other.id), "expected_version": other.version,
                                          "planned_quantity": "1"})
        _as("Другой закупщик")
    elif failure == "supplier":
        body["suppliers"] = [{"supplier_id": str(uuid4())}]
    response = client.post(BASE, json=body)
    assert response.status_code in {403, 404, 422}, response.text
    with sessions() as session:
        row = session.get(ProcurementLineRow, line.id)
        assert row.buyer_id is None and row.version == line.version
        assert session.scalar(select(func.count()).select_from(RfqRow)) == 0


def test_duplicate_live_rfq_rejected_with_fresh_plan_version(rfq_api):
    client, _, sessions = rfq_api
    line = consume_procurement_requested(message(3), app.state.procurement_uow_factory())
    assert client.post(BASE, json=_body(line)).status_code == 201
    with sessions() as session:
        row = session.get(ProcurementLineRow, line.id)
        body = _body(row, "8")
    response = client.post(BASE, json=body)
    assert response.status_code == 409 and "уже в запросе" in response.text
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RfqRow)) == 1
        assert session.scalar(select(RfqLineRow.quantity)) == Decimal("7.25")
