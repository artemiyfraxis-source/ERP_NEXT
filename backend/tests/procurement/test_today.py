"""Stage option `procurement.today`: Roller_Buyer's «Сегодня» — pure section rules (ported from RB today.test.ts,
orders.test.ts follow-ups, domain/board.test.ts) and the endpoint behind the option."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.procurement.domain import ProcurementStatus
from app.modules.procurement.requests_view import RfqState, ViewLine
from app.modules.procurement.today import (
    ITEMS_SENT,
    LINES_SENT,
    Decision,
    EtaInput,
    KaitenInvoice,
    OrderFacts,
    SilentSupplier,
    TodayFacts,
    TodaySettings,
    build_today,
    line_eta,
    median,
    order_followups,
    parse_lead_text,
    pricebook_wanted,
    rub,
)
from tests.factories import Actor
from tests.procurement.test_api import procurement_api  # noqa: F401 — fixture

TODAY = date(2026, 9, 20)
BUYER = Actor(name="Петров", department="Закупки")
SETTINGS = TodaySettings()


def view(**changes) -> ViewLine:
    base = ViewLine(
        id=uuid4(), legacy_id="SM-20260910-01", version=1, status="В работе", buyer_id=BUYER.id, buyer_name="Петров",
        source_request_id=UUID(int=1), created_at=datetime(2026, 9, 10, 9, 0), request_date=date(2026, 9, 10),
        planned_date=date(2026, 12, 30), initiator="Иванов", designation="П-100.001", project_reference="П-100",
        item_type="Разъём", category="Общепром", name="Разъём SMA", article="SMA-001", quantity=Decimal("100"),
        unit="шт",
    )
    return replace(base, **changes)


def page(*lines: ViewLine, today: date = TODAY, settings: TodaySettings = SETTINGS, **facts) -> dict:
    return build_today(TodayFacts(lines=lines, **facts), today, settings)


def keys(result: dict) -> list[str]:
    return [section["key"] for section in result["sections"]]


def section(result: dict, key: str) -> dict:
    return next(item for item in result["sections"] if item["key"] == key)


# ---- RB today.test.ts ---------------------------------------------------------------------------------------------

def test_an_empty_system_is_calm():
    result = page()
    assert result == {"today": "2026-09-20", "sections": [], "calm": True}


def test_each_stage_of_a_line_lands_in_its_section_in_the_order_of_doing():
    fresh = view(legacy_id="SM-20260910-02", name="Клемма")
    rfq_id = uuid4()
    decided = view(status="Готов к запросу", supplier="ООО А")
    states = {decided.id: RfqState(live=True, rfq_id=rfq_id, rfq_number="КП-2609-001", quoted=True, decided=True)}
    result = page(fresh, decided, rfq=states)
    assert keys(result) == ["rfq", "invoice"]  # a fresh line to ask quotes for, a chosen supplier waiting for the invoice
    assert section(result, "invoice")["items"][0]["link"] == {"tab": "rfq", "rfq": str(rfq_id), "invoices": "1"}

    ordered = replace(decided, status="В оплату", invoiced=True, order_number="PO-1", invoice_date=date(2026, 9, 5),
                      expected_arrival=date(2026, 9, 18))
    result = page(fresh, ordered, rfq=states)
    assert keys(result) == ["late", "rfq", "pay"]
    late = section(result, "late")
    assert late["items"][0]["days"] == 2  # two days past the promised date
    assert late["tone"] == "danger" and late["title"] == "Просрочены поставки"
    assert late["items"][0]["detail"] == "ООО А · заказ PO-1 · обещали 18.09"
    assert late["items"][0]["link"] == {"tab": "orders", "order": "PO-1"}
    pay = section(result, "pay")["items"][0]
    assert (pay["title"], pay["detail"], pay["days"]) == ("ООО А · заказ PO-1", "не передан в оплату", 15)
    sent = page(fresh, ordered, rfq=states, orders={"PO-1": OrderFacts(sent_to_pay_on=date(2026, 9, 6))})
    assert section(sent, "pay")["items"][0]["detail"] == "в оплате"


def test_lines_to_quote_are_one_card_per_project_with_positions_inside():
    a1 = view(project_reference="П-100", designation="П-100.001", name="Разъём 1", request_date=date(2026, 9, 10))
    a2 = view(project_reference="П-100", designation="П-100.001", name="Разъём 2", request_date=date(2026, 9, 12),
              legacy_id="SM-20260912-01")
    b = view(project_reference=None, designation="Склад.004", name="Провод", request_date=date(2026, 9, 15),
             legacy_id="SM-20260915-01")
    requote = view(project_reference="П-100", name="Разъём 3", status="Готов к запросу", legacy_id="SM-20260910-03")
    in_rfq = view(project_reference="П-100", name="Уже в запросе")
    states = {requote.id: RfqState(requote=True, requote_from="КП-2609-001"), in_rfq.id: RfqState(live=True)}
    rfq = section(page(a1, a2, b, requote, in_rfq, rfq=states), "rfq")
    assert [(item["title"], item["detail"], item["days"], item["lines_count"]) for item in rfq["items"]] == [
        ("П-100", "3 позиции · перезапрос: 1", 10, 3),
        ("Склад", "1 позиция", 5, 1),  # no project: the purpose, not one «Без проекта» heap
    ]
    card = rfq["items"][0]
    assert card["link"] == {"tab": "requests", "project": "П-100"}
    assert card["project"] == {"key": "П-100", "kind": "project", "value": "П-100"}
    assert [line["title"] for line in card["lines"]] == ["Разъём 1", "Разъём 3", "Разъём 2"]  # the oldest first
    assert card["lines"][0]["link"] == {"tab": "requests", "project": "П-100", "line": str(a1.id)}
    assert card["lines"][1]["detail"] == "SM-20260910-03 · 100 шт · перезапрос"
    assert rfq["positions"] == 4


def test_a_line_covered_from_stock_or_parked_is_not_to_quote():
    covered = view(stock_covered=Decimal("100"))
    parked = view(status="Согласование")
    assert keys(page(covered, parked)) == []


# ---- RB followups: deliveries and payments -------------------------------------------------------------------------

def test_deliveries_due_within_the_warning_window_arrive_late_ones_are_overdue():
    paid = dict(status="В пути", paid=True, invoiced=True, supplier="ООО Б")
    today_line = view(**paid, name="Сегодня", expected_arrival=date(2026, 9, 20), order_number="PO-2")
    soon = view(**paid, name="Через три", expected_arrival=date(2026, 9, 23), order_number="PO-3")
    later = view(**paid, name="Через четыре", expected_arrival=date(2026, 9, 24), order_number="PO-4")
    result = page(today_line, soon, later)
    arrive = section(result, "arrive")
    assert arrive["title"] == "Приедет сегодня–завтра" and arrive["tone"] == "accent"
    assert [(item["title"], item["detail"], item["days"]) for item in arrive["items"]] == [
        ("Сегодня", "ООО Б · заказ PO-2 · сегодня", None),
        ("Через три", "ООО Б · заказ PO-3 · через 3 дня", None),
    ]
    wider = page(today_line, soon, later, settings=TodaySettings(delivery_warn_days=4))
    assert section(wider, "arrive")["count"] == 3


def test_an_order_with_several_late_lines_is_one_card_with_its_positions():
    common = dict(status="В пути", paid=True, supplier="ООО Б", order_number="PO-7")
    first = view(**common, name="Кабель", expected_arrival=date(2026, 9, 10))
    second = view(**common, name="Клемма", expected_arrival=date(2026, 9, 15))
    no_date = view(status="В пути", paid=True, supplier="ООО Б", order_number="PO-8")
    promised = view(status="В пути", paid=True, supplier="ООО Б", order_number="PO-9", name="Обещано заказом")
    result = page(first, second, no_date, promised, orders={"PO-9": OrderFacts(promised=date(2026, 9, 19))})
    late = section(result, "late")
    card = late["items"][0]
    assert (card["title"], card["detail"], card["days"], card["lines_count"]) == (
        "ООО Б · заказ PO-7", "обещали 10.09 · 2 позиции", 10, 2)
    assert [line["detail"] for line in card["lines"]] == ["100 шт · обещали 10.09", "100 шт · обещали 15.09"]
    # A line without its own date waits for the order's promise (order facts of «Счета и поставки»).
    assert late["items"][1]["title"] == "Обещано заказом" and late["items"][1]["days"] == 1
    assert late["count"] == 2 and late["positions"] == 3


def test_late_lines_are_not_also_at_risk_and_quarantine_is_kept_apart():
    late = view(status="В пути", paid=True, order_number="PO-1", expected_arrival=date(2026, 9, 1),
                planned_date=date(2026, 8, 30))
    inspected = view(status="Карантин", paid=True, order_number="PO-2", supplier="ООО В",
                     actual_arrival=date(2026, 9, 18), planned_date=date(2026, 9, 1), expected_arrival=date(2026, 9, 17))
    result = page(late, inspected)
    assert keys(result) == ["late", "quarantine"]
    item = section(result, "quarantine")["items"][0]
    assert (item["detail"], item["days"], item["link"]) == (
        "ООО В · заказ PO-2 · на приёмке с 18.09", 2, {"tab": "orders", "order": "PO-2"})


def test_unpaid_invoices_are_one_item_per_order_the_oldest_first():
    common = dict(status="В оплату", invoiced=True, supplier="ООО Г")
    a = view(**common, order_number="PO-1", invoice_date=date(2026, 9, 15))
    b = view(**common, order_number="PO-1", invoice_date=date(2026, 9, 16))
    c = view(**common, order_number="PO-2", invoice_date=date(2026, 9, 1))
    pay = section(page(a, b, c, received_orders=frozenset({"PO-2"})), "pay")
    assert [(item["title"], item["detail"], item["days"]) for item in pay["items"]] == [
        ("ООО Г · заказ PO-2", "не передан в оплату · часть товара уже на складе", 19),
        ("ООО Г · заказ PO-1", "не передан в оплату · 2 позиции", 5),
    ]


# ---- requests for quotes -------------------------------------------------------------------------------------------

def test_silent_suppliers_wait_quote_wait_days_and_need_the_rfq_option():
    rfq_id = uuid4()
    silent = SilentSupplier(rfq_id=rfq_id, rfq_number="КП-2609-001", supplier="ООО Молчит", sent_on=date(2026, 9, 17), lines=2)
    assert keys(page(silent=[silent], rfq_on=False)) == []
    quotes = section(page(silent=[silent], rfq_on=True), "quotes")
    item = quotes["items"][0]
    assert (item["title"], item["detail"], item["days"], item["link"]) == (
        "ООО Молчит", "запрос КП-2609-001 от 17.09 · 2 позиции", 3, {"tab": "rfq", "rfq": str(rfq_id)})
    # RB: «days >= quoteWaitDays» — on the third day, not after it.
    assert keys(page(silent=[silent], rfq_on=True, settings=TodaySettings(quote_wait_days=4))) == []


def test_choose_supplier_is_a_card_per_project_waiting_since_the_request_was_sent():
    rfq_id = uuid4()
    quoted = [view(status="Готов к запросу", name=f"Позиция {n}") for n in (1, 2)]
    states = {line.id: RfqState(live=True, rfq_id=rfq_id, rfq_number="КП-2609-004", quoted=True) for line in quoted}
    result = page(*quoted, rfq=states, rfq_sent={rfq_id: date(2026, 9, 14)}, rfq_on=True)
    card = section(result, "choose")["items"][0]
    assert (card["title"], card["detail"], card["days"], card["link"]) == (
        "П-100", "2 позиции · запрос КП-2609-004", 6, {"tab": "rfq", "rfq": str(rfq_id)})
    assert card["lines"][0]["detail"] == "SM-20260910-01 · 100 шт · запрос КП-2609-004"


def test_waiting_for_the_invoice_without_a_request_opens_the_order():
    line = view(status="Жду счет", requested=True, order_number="PO-5", supplier="ООО Д",
                order_date=date(2026, 9, 12))
    item = section(page(line), "invoice")["items"][0]
    assert (item["detail"], item["days"], item["link"]) == ("ООО Д · П-100", 8, {"tab": "orders", "order": "PO-5"})


# ---- documents and ERP extras --------------------------------------------------------------------------------------

def test_kaiten_invoices_to_pay_and_upds_to_check():
    invoices = [KaitenInvoice(title="Счёт 12", seller=None, total=123456, created_on=date(2026, 9, 18))]
    result = page(kaiten=invoices, upd_to_check=3, available=4)
    assert keys(result) == ["kaiten", "upd", "available"]
    kaiten = section(result, "kaiten")["items"][0]
    assert (kaiten["detail"], kaiten["days"], kaiten["link"]) == (
        "поставщик не прочитан · 1 234,56 ₽", 2, {"tab": "invoices", "status": "to_pay"})
    upd = section(result, "upd")["items"][0]
    assert (upd["title"], upd["link"]) == ("3 документа", {"tab": "upd", "status": "needs_review"})
    assert section(result, "available")["items"][0]["link"] == {"tab": "queues"}
    assert keys(page(kaiten=[], upd_to_check=0)) == []  # the options are on, nothing to do


def test_a_section_sends_eight_items_and_counts_the_rest_a_card_thirty_positions():
    lines = [view(project_reference=f"П-{n}", designation=f"П-{n}.001") for n in range(ITEMS_SENT + 3)]
    lines += [view(project_reference="П-0", designation="П-0.001") for _ in range(LINES_SENT + 5)]
    rfq = section(page(*lines), "rfq")
    assert rfq["count"] == ITEMS_SENT + 3 and len(rfq["items"]) == ITEMS_SENT
    big = next(item for item in rfq["items"] if item["title"] == "П-0")
    assert big["lines_count"] == LINES_SENT + 6 and len(big["lines"]) == LINES_SENT


def test_rub_formats_like_ru_locale():
    assert rub(100000) == "1 000" and rub(150) == "1,5" and rub(123456789) == "1 234 567,89"


# ---- «Не успевает к сроку» and lineEta (RB domain/board.test.ts) --------------------------------------------------

def test_lead_texts_from_price_books_become_days():
    assert parse_lead_text("3-5 недель") == 28
    assert parse_lead_text("2 р.д.") == 3
    assert parse_lead_text("33р.д.") == 46
    assert parse_lead_text("4 месяца") == 120
    assert parse_lead_text("сегодня рд") == 0
    assert parse_lead_text("3-4 weeks") == 25  # JS Math.round: halves go up
    assert parse_lead_text("под Заказ") is None
    assert parse_lead_text("") is None
    assert median([28, 3, 46]) == 28 and median([]) is None


def _eta(**changes) -> EtaInput:
    base = EtaInput(status="new", requested=date(2026, 9, 1), today=date(2026, 9, 10), default_order_days=5,
                    default_lead_days=14)
    return replace(base, **changes)


def test_the_date_chosen_at_the_order_wins_a_reschedule_replaces_it():
    assert line_eta(_eta(status="paid", on_order=True, expected=date(2026, 9, 20))) == (date(2026, 9, 20), "order")
    # A stale date on a line not ordered yet is not a promise.
    assert line_eta(_eta(expected=date(2026, 9, 5)))[1] == "default"


def test_before_an_order_quote_lead_then_supplier_history_then_price_books_then_the_default():
    assert line_eta(_eta(status="decided", decided=date(2026, 9, 8), quote_lead_days=7)) == (date(2026, 9, 17), "quote")
    # Request of 01.09 + 5 days to order = 06.09, already past → ordering from today (10.09).
    assert line_eta(_eta(supplier_lead_days=10)) == (date(2026, 9, 20), "history")
    assert line_eta(_eta(pricebook_lead_days=28)) == (date(2026, 10, 8), "pricebook")
    assert line_eta(_eta()) == (date(2026, 9, 24), "default")
    assert line_eta(_eta(today=date(2026, 9, 2))) == (date(2026, 9, 20), "default")  # order expected 06.09 + 14
    assert line_eta(_eta(order_days=1, today=date(2026, 9, 1))) == (date(2026, 9, 16), "default")  # own history
    assert line_eta(_eta(started=date(2026, 9, 9), supplier_lead_days=3)) == (date(2026, 9, 12), "history")  # paid 09.09


def test_delivered_lines_show_the_day_they_arrived_from_stock_has_no_supplier_date():
    assert line_eta(_eta(status="delivered", delivered=date(2026, 9, 9))) == (date(2026, 9, 9), "delivered")
    assert line_eta(_eta(status="from_stock")) == (None, None)


def test_a_line_that_will_not_make_its_date_is_at_risk_grouped_by_project():
    early = view(planned_date=date(2026, 9, 25), name="Нужна рано")  # default: 20.09 + 14 = 04.10
    fine = view(planned_date=date(2026, 12, 30), name="Успевает")
    quoted = view(status="Готов к запросу", planned_date=date(2026, 9, 25), name="По КП", supplier="ООО Е")
    decision = Decision(rfq_id=uuid4(), rfq_number="КП-1", decided_on=date(2026, 9, 19), lead_days=3, supplier="ООО Е")
    states = {quoted.id: RfqState(live=True, rfq_id=decision.rfq_id, quoted=True, decided=True)}
    result = page(early, fine, quoted, decisions={quoted.id: decision}, rfq=states)
    risk = section(result, "risk")
    assert risk["tone"] == "warning"
    card = risk["items"][0]
    assert (card["title"], card["detail"], card["days"]) == ("П-100", "1 позиция · нужно к 25.09 · ожидаем 04.10", 9)
    assert card["lines"][0]["detail"] == "SM-20260910-01 · нужно к 25.09 · ожидаем 04.10 (обычный срок)"
    # The supplier's history moves the date: paid → arrival in 2 days on average.
    with_history = build_today(TodayFacts(lines=(replace(early, supplier="ООО Е"),), supplier_leads={"ооо е": 2}),
                               TODAY, SETTINGS)
    assert keys(with_history) == ["rfq"]  # 20.09 + 2 = 22.09, before 25.09


def test_only_lines_with_nothing_nearer_are_matched_to_price_books():
    bare = view(planned_date=date(2026, 9, 25))
    known = view(supplier="ООО Е")
    dated = view(status="В пути", paid=True, order_number="PO-1", expected_arrival=date(2026, 9, 30))
    facts = TodayFacts(lines=(bare, known, dated), supplier_leads={"ооо е": 5})
    assert [line.id for line in pricebook_wanted(facts, TODAY, SETTINGS)] == [bare.id]
    priced = build_today(replace(facts, pricebook_leads={bare.id: 2}), TODAY, SETTINGS)
    assert "risk" not in keys(priced)


# ---- «Дожать» on the orders board reads the same rules -------------------------------------------------------------

def test_order_followups_group_the_same_facts_per_order_and_per_request():
    late = view(status="В пути", paid=True, supplier="ООО Б", order_number="PO-1", expected_arrival=date(2026, 9, 18))
    soon = view(status="В пути", paid=True, supplier="ООО Б", order_number="PO-2", expected_arrival=date(2026, 9, 21))
    unpaid = view(status="В оплату", invoiced=True, supplier="ООО Г", order_number="PO-3", invoice_date=date(2026, 9, 10))
    rfq_id = uuid4()
    silent = [SilentSupplier(rfq_id, "КП-1", name, date(2026, 9, 15), 2) for name in ("ООО Я", "ООО Ж")]
    result = order_followups(TodayFacts(lines=(late, soon, unpaid), silent=silent, rfq_on=True), TODAY, SETTINGS)
    assert [(item["order_number"], item["days"]) for item in result["late"]] == [("PO-1", 2)]
    assert [(item["order_number"], item["days"]) for item in result["arriving"]] == [("PO-2", 1)]  # days left
    assert [(item["order_number"], item["days"], item["count"]) for item in result["unpaid"]] == [("PO-3", 10, 1)]
    assert result["awaiting_quotes"] == [{
        "rfq_id": str(rfq_id), "rfq_number": "КП-1", "hint": "КП-1: нет ответа — ООО Ж, ООО Я", "date": "2026-09-15",
        "days": 5, "count": 2,
    }]


# ---- the endpoint --------------------------------------------------------------------------------------------------

def test_today_endpoint_lists_the_buyers_lines(procurement_api):  # noqa: F811
    client, free_line, sessions = procurement_api
    assert client.get("/api/procurement/today").json()["sections"][0]["key"] == "available"
    assert client.post("/api/procurement/take", json={"line_ids": [str(free_line.id)]}).status_code == 200
    with sessions.begin() as session:
        row = session.get(ProcurementLineRow, free_line.id)
        row.status = ProcurementStatus.IN_TRANSIT.value
        row.paid_at = datetime(2026, 9, 1, 10, 0)
        row.order_number = "PO-77"
        row.expected_arrival = date(2026, 9, 10)

    response = client.get("/api/procurement/today")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["today"] == "2026-09-15" and body["calm"] is False
    assert [s["key"] for s in body["sections"]] == ["late"]
    item = body["sections"][0]["items"][0]
    assert item["days"] == 5 and item["link"] == {"tab": "orders", "order": "PO-77"}  # the stubbed clock is 2026-09-15


@pytest.mark.parametrize(("key", "bad", "good"), [
    ("delivery_warn_days", 61, 0), ("board_order_days", 121, 120), ("board_lead_days", 366, 365),
    ("quote_wait_days", 0, 1),
])
def test_today_settings_are_validated_like_roller_buyer(key, bad, good):
    from app.modules.purchasing import settings as purchasing_settings

    with pytest.raises(purchasing_settings.InvalidSetting):
        purchasing_settings._coerce(purchasing_settings.BY_KEY[key], bad)
    with pytest.raises(purchasing_settings.InvalidSetting):
        purchasing_settings._coerce(purchasing_settings.BY_KEY[key], 1.5)
    assert purchasing_settings._coerce(purchasing_settings.BY_KEY[key], good) == good
    assert {setting.key: setting.default for setting in purchasing_settings.SETTINGS}[key] in (3, 5, 14)
