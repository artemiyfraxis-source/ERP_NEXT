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
    assert body["kpi"]["money"]["spend"] == "500.00" and body["suppliers"] == [] and body["highlights"] == []
    assert body["kpi"]["quality"]["defect_pct"] is None  # nothing received: no data
    assert body["kpi"]["competition"]["decisions"] == 0 and body["kpi"]["money"]["delivery_spend"] is None  # facts are wired
    assert client.get("/api/procurement/kpi?from=2026-09-30&to=2026-09-01").status_code == 422


# ---- Roller_Buyer `domain/kpi.test.ts`, replayed on ERP procurement lines --------------------------------------------
TODAY = date(2026, 10, 20)


def bought(price="44", quantity="100", need="100", baseline="50", **fields):
    """A paid line: calculated at `baseline`, bought at `price` (Roller's `line()` helper: 100 × 44 against 50)."""
    base = dict(
        supplier="Альфа", calculated_quantity=Decimal(need), calculated_unit="шт", actual_unit="шт",
        actual_quantity=Decimal(quantity), calculated_price_with_vat=Decimal(baseline) if baseline else None,
        money=FactMoney(price_with_vat=Decimal(price), sum=Decimal(price) * Decimal(quantity)),
        request_date=date(2026, 9, 15), order_date=date(2026, 9, 19),
        expected_arrival=date(2026, 10, 12), actual_arrival=date(2026, 10, 11),
    )
    base.update(fields)
    return paid(**base)


def sheet(lines, **kwargs):
    return build_kpi(lines, START, END, today=TODAY, **kwargs)


def test_savings_against_the_baseline_including_a_negative_line():
    k = sheet([bought(), bought(price="52")])["kpi"]["money"]
    assert k["baseline_spend"] == "10000.00"
    assert k["savings"] == "400.00"  # +600 and −200
    assert k["savings_pct"] == "4.0"
    assert k["spend"] == "9600.00"


def test_lines_without_a_baseline_count_in_spend_but_not_in_savings():
    k = sheet([bought(baseline=None)])["kpi"]["money"]
    assert k["compared_lines"] == 0 and k["savings_pct"] is None and k["spend"] == "4400.00"


def test_lines_outside_the_period_are_ignored():
    outside = bought(paid_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc), expected_arrival=date(2026, 9, 30))
    k = sheet([outside])["kpi"]
    assert k["money"]["spend"] == "0.00" and k["delivery"]["due"] == 0


def test_on_time_share_skips_lines_still_open_and_counts_the_overdue_ones():
    late = bought(expected_arrival=date(2026, 10, 7), actual_arrival=date(2026, 10, 11))  # 4 days late
    open_line = bought(expected_arrival=date(2026, 10, 25), actual_arrival=None)  # expected after today: still open
    overdue = bought(expected_arrival=date(2026, 10, 18), actual_arrival=None)  # 2 days past, goods not in
    delivery = sheet([bought(), late, open_line, overdue])["kpi"]["delivery"]
    assert delivery["due"] == 3 and delivery["on_time"] == 1 and delivery["on_time_pct"] == "33.3"
    assert delivery["avg_delay_days"] == "3.0"  # (4 + 2) / 2
    assert delivery["avg_request_to_order_days"] == "4.0"  # 15.09 → 19.09
    assert delivery["avg_request_to_delivery_days"] == "26.0"  # 15.09 → 11.10, only the received lines


def test_quality_and_competition_figures_without_a_source_are_no_data():
    k = sheet([bought(), bought(supplier="Бета")])["kpi"]
    assert k["quality"]["lines"] == 2
    assert k["quality"]["defect_pct"] is None and k["quality"]["defect_amount"] is None
    assert k["competition"]["avg_offers"] is None and k["competition"]["override_pct"] is None
    assert k["competition"]["new_suppliers"] is None and k["money"]["vs_last_purchase"] is None
    assert k["competition"]["top1_share"] == "50.0" and k["competition"]["top3_share"] == "100.0"


def test_supplier_rating_sorted_by_spend():
    ratings = sheet([bought(), bought(supplier="Бета", price="10"), bought(supplier="альфа ")])["suppliers"]
    assert [r["supplier"] for r in ratings] == ["Альфа", "Бета"]
    alpha = ratings[0]
    assert alpha["order_lines"] == 2 and alpha["spend"] == "8800.00" and alpha["on_time_pct"] == "100.0"
    assert alpha["asked"] is None and alpha["response_pct"] is None and alpha["price_vs_best_pct"] is None


def test_top_savings_biggest_first_and_only_positive():
    small, big, loss = bought(price="49"), bought(price="40"), bought(price="55")
    top = sheet([small, big, loss])["highlights"]
    assert [h["line_id"] for h in top] == [str(big.id), str(small.id)]
    assert top[0]["savings"] == "1000.00" and top[0]["baseline_unit_price"] == "50.00" and top[0]["unit_price"] == "40.00"
    assert len(sheet([bought(price=str(40 - i)) for i in range(7)])["highlights"]) == 5


def test_buying_above_the_need_is_stock_not_savings():
    # 1000 bought for an 87 need at 44 against a 50 baseline.
    k = sheet([bought(quantity="1000", need="87")])
    assert k["kpi"]["money"]["savings"] == "522.00"  # 87 × 6
    assert k["kpi"]["money"]["surplus_spend"] == "40172.00"  # 913 × 44
    assert k["kpi"]["money"]["spend"] == "44000.00"  # everything paid
    assert k["highlights"][0]["savings"] == "522.00"
    assert k["rows"][0]["savings"]["sum"] == "522.00"


def test_payload_keeps_the_per_buyer_table():
    result = sheet([bought(), bought(buyer="Сидоров")])
    assert [r["buyer"] for r in result["rows"]] == ["Петров", "Сидоров"] and result["total"]["lines"] == 2
