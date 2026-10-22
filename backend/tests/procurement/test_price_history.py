from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.price_history import (
    PriceCandidate,
    PriceItem,
    delta_pct,
    past_purchases,
    unit_price_with_vat,
)

from .test_api import message, procurement_api  # noqa: F401


def item(article=None, name="Разъём", line_id=None):
    return PriceItem(line_id or uuid4(), article, (name,))


def cand(article=None, name="Разъём", day=1, price="10", unit="шт", paid=True, line_id=None):
    return PriceCandidate(
        item=item(article, name, line_id),
        paid_at=datetime(2026, 9, day) if paid else None,
        supplier="ООО Х",
        unit_price_with_vat=Decimal(price),
        unit=unit,
        quantity=Decimal("2"),
        order_number="ЗК-1",
    )


def test_article_match_ignores_case_and_separators_and_names_do_not_matter():
    target = item("SHR 02V-s-b", "Разъём A")
    found = past_purchases(target, [cand("shr02vsb", "Другое"), cand("OTHER-1", "Разъём A")])
    assert len(found) == 1


def test_name_match_when_target_has_no_article_is_exact_after_normalization():
    target = item(None, "Разъём  Тип 1")
    found = past_purchases(target, [cand(name="разъём тип 1"), cand(name="Разъём тип 12")])
    assert len(found) == 1


def test_unpaid_and_self_are_excluded_newest_first_limit_five():
    target = item(None, "Разъём")
    candidates = [cand(day=d) for d in range(1, 9)] + [cand(paid=False), cand(line_id=target.line_id, day=9)]
    found = past_purchases(target, candidates)
    assert [p.paid_at.day for p in found] == [8, 7, 6, 5, 4]


def test_delta_needs_equal_units():
    target = item(None, "Разъём")
    history = past_purchases(target, [cand(price="10", unit="шт")])
    assert delta_pct(Decimal("10.8"), "шт", history) == Decimal("8.0")
    assert delta_pct(Decimal("10.8"), "м", history) is None
    assert delta_pct(None, "шт", history) is None


def test_gross_from_net_and_vat_and_foreign_conversion():
    assert unit_price_with_vat(currency="RUB", price=Decimal("10"), vat_rate=Decimal("20"), price_with_vat=None) == Decimal("12.00")
    assert unit_price_with_vat(currency="RUB", price=None, vat_rate=None, price_with_vat=None) is None
    foreign = unit_price_with_vat(
        currency="USD", price=None, vat_rate=Decimal("0"), price_with_vat=None,
        source_price=Decimal("1"), exchange_rate=Decimal("100"),
    )
    assert foreign == Decimal("106.50")




def test_api_answers_price_history(procurement_api):
    client, line, sessions = procurement_api
    second = consume_procurement_requested(message(2), SqlAlchemyProcurementUnitOfWork(sessions))
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id), str(second.id)]})
    assert taken.status_code == 200, taken.text
    with sessions() as s:
        old = s.get(ProcurementLineRow, line.id)
        old.design_article = "ART-123"
        old.paid_at = datetime(2026, 9, 3, 12, 0)
        old.actual_price_with_vat = Decimal("10")
        old.actual_unit = "шт"
        old.supplier = "ООО Х"
        cur = s.get(ProcurementLineRow, second.id)
        cur.design_article = "art 123"
        cur.actual_price_with_vat = Decimal("10.8")
        cur.actual_unit = "шт"
        s.commit()

    one = client.get(f"/api/procurement/{second.id}/price-history")
    assert one.status_code == 200, one.text
    assert one.json()["delta_pct"] == "8.0"
    assert one.json()["purchases"][0]["supplier"] == "ООО Х"
    batch = client.post("/api/procurement/price-history", json={"line_ids": [str(second.id), str(uuid4())]})
    assert list(batch.json()) == [str(second.id)]
    assert batch.json()[str(second.id)] == one.json()  # the indexed batch answers exactly like the single query
    too_many = client.post("/api/procurement/price-history", json={"line_ids": [str(uuid4()) for _ in range(201)]})
    assert too_many.status_code == 422
    assert client.get(f"/api/procurement/{uuid4()}/price-history").status_code == 404
