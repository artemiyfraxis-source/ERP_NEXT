"""pure metric logic and the endpoint behind the option."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from app.modules.procurement.domain import create_line_from_requested
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.procurement.kpi import build_kpi
from app.modules.procurement.money import FactMoney
from tests.factories import Actor
from tests.procurement.test_api import message, procurement_api  # noqa: F401 — fixture

START, END = date(2026, 10, 1), date(2026, 10, 31)


def paid(buyer="Петров", *, paid_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc), **fields):
    base = create_line_from_requested(message())
    return replace(base, buyer=Actor(name=buyer, department="Закупки"), paid_at=paid_at, **fields)


def test_metrics_per_buyer_and_total():
    a = paid(
        invoice_date=date(2026, 9, 30), expected_arrival=date(2026, 10, 10), actual_arrival=date(2026, 10, 8),
        calculated_price_with_vat=Decimal("120"), calculated_unit="шт", actual_unit="шт",
        actual_quantity=Decimal("2"), money=FactMoney(price_with_vat=Decimal("100"), sum=Decimal("200")),
    )
    b = paid(
        invoice_date=date(2026, 10, 1), expected_arrival=date(2026, 10, 5), actual_arrival=date(2026, 10, 9),
        actual_quantity=Decimal("1"), actual_unit="кг", calculated_price_with_vat=Decimal("50"),
        money=FactMoney(price_with_vat=Decimal("60"), sum=Decimal("60")),
    )
    other = paid("Сидоров", money=FactMoney(sum=Decimal("10.005")))
    outside = paid(paid_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc))

    result = build_kpi([a, b, other, outside], START, END)

    assert [r["buyer"] for r in result["rows"]] == ["Петров", "Сидоров"]
    petrov = result["rows"][0]
    assert petrov["lines"] == 2 and petrov["paid_sum"] == "260.00"
    assert petrov["on_time"] == {"n": 2, "ok": 1, "percent": "50.0"}
    assert petrov["invoice_to_paid"] == {"days": "1.5", "n": 2}  # 2 and 1 days
    assert petrov["paid_to_received"] == {"days": "6.5", "n": 2}  # 6 and 7 days
    # saving: only line a is comparable (b has different units): (120-100)*2
    assert petrov["savings"] == {"sum": "40.00", "n": 1, "excluded": 1}
    assert result["total"]["lines"] == 3 and result["total"]["paid_sum"] == "270.01"
    assert build_kpi([a, b, other], START, END, buyer="Сидоров")["rows"][0]["buyer"] == "Сидоров"


def test_no_data_gives_none_instead_of_zero():
    row = build_kpi([paid()], START, END)["total"]
    assert row["on_time"]["percent"] is None and row["invoice_to_paid"] == {"days": None, "n": 0}


def test_kpi_endpoint_answers(procurement_api):  # noqa: F811
    client, free_line, sessions = procurement_api
    assert client.post("/api/procurement/take", json={"line_ids": [str(free_line.id)]}).status_code == 200
    with sessions.begin() as session:
        row = session.get(ProcurementLineRow, free_line.id)
        row.paid_at = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
        row.actual_sum = Decimal("500")

    response = client.get("/api/procurement/kpi?from=2026-09-01&to=2026-09-30")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"]["lines"] == 1 and body["total"]["paid_sum"] == "500.00"
    assert body["rows"][0]["buyer"] == "Петров" and body["buyers"] == ["Петров"]
    assert client.get("/api/procurement/kpi?from=2026-09-30&to=2026-09-01").status_code == 422
