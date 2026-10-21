"""«Заявки» закупщика (option procurement.requests_rb): RB statuses on ERP facts, the project chain, request batches,
«Разделить» by quantity and the cancel reason — pure rules."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.procurement.domain import (
    NO_PROJECT_KEY,
    LineProject,
    ProcurementAccessDenied,
    ProcurementStatus,
    ProcurementValidationError,
    batch_key,
    cancel_reason,
    create_line_from_requested,
    line_project,
    split_by_quantity,
)
from app.modules.procurement.requests_view import (
    RB_STATUSES,
    OrderState,
    RfqState,
    ViewLine,
    build_summary,
    line_results,
    plan_label,
    plan_unit_gross,
    rb_status,
)
from tests.factories import Actor
from tests.procurement.test_domain import requested_message

NOW = datetime(2026, 10, 19, 10, 0)
DAY = date(2026, 10, 19)
BUYER = Actor(name="Петров", department="Закупки")
OTHER = Actor(name="Сидоров", department="Закупки")


def view(**changes) -> ViewLine:
    base = ViewLine(
        id=uuid4(), legacy_id="SM-20261019-01", version=1, status="В работе", buyer_id=BUYER.id, buyer_name="Петров",
        source_request_id=UUID(int=1), created_at=NOW, request_date=DAY, planned_date=date(2026, 10, 30),
        initiator="Иванов", designation="708К.4.012", project_reference=None, item_type="Разъём", category="Общепром",
        name="Разъём SMA", article="SMA-001", quantity=Decimal("10"), unit="шт",
    )
    return replace(base, **changes)


# --- RB status on ERP facts: every status ----------------------------------------------------------------------

@pytest.mark.parametrize(("changes", "rfq", "order", "expected"), [
    ({"status": "Новый", "buyer_id": None}, None, None, "new"),
    ({"status": "В работе"}, None, None, "new"),
    ({"status": "Готов к запросу"}, RfqState(requote=True, requote_from="КП-2610-001"), None, "requote"),
    ({"status": "Готово", "stock_covered": Decimal("10")}, None, None, "from_stock"),
    ({"status": "В работе"}, RfqState(live=True, rfq_number="КП-2610-002"), None, "in_rfq"),
    ({"status": "Готов к запросу"}, RfqState(live=True, quoted=True), None, "quoted"),
    ({"status": "Готов к запросу"}, RfqState(live=True, quoted=True, decided=True), None, "decided"),
    ({"status": "Жду счет", "requested": True}, None, None, "decided"),
    ({"status": "В оплату", "invoiced": True}, None, None, "ordered"),
    ({"status": "В оплату", "invoiced": True, "order_number": "PO-1"}, None, OrderState(sent_to_pay=True), "to_pay"),
    ({"status": "В пути", "paid": True}, None, None, "paid"),
    ({"status": "В пути", "paid": True}, None, OrderState(shipped=True), "shipped"),
    ({"status": "В пути", "paid": True, "legacy_id": "SM-20261019-01-Д"}, None, None, "shipped"),
    ({"status": "Карантин", "paid": True}, None, None, "partial"),
    ({"status": "Согласование", "paid": True, "legacy_id": "SM-20261019-01-Д"}, None, None, "partial"),
    ({"status": "Готово", "paid": True}, None, None, "delivered"),
    ({"status": "Отменено"}, RfqState(live=True), None, "cancelled"),
])
def test_rb_status_follows_the_furthest_fact(changes, rfq, order, expected):
    assert rb_status(view(**changes), rfq, order) == expected


def test_every_rb_status_is_reachable_and_ordered_like_roller_buyer():
    assert RB_STATUSES == (
        "new", "requote", "from_stock", "in_rfq", "quoted", "decided", "ordered", "to_pay", "paid", "shipped",
        "partial", "delivered", "cancelled",
    )


def test_a_line_parked_in_approval_before_the_switch_keeps_its_data_stage():
    assert rb_status(view(status="Согласование", invoiced=True)) == "ordered"
    assert rb_status(view(status="Согласование", requested=True)) == "decided"
    assert rb_status(view(status="Согласование"), RfqState(live=True)) == "in_rfq"


def test_a_requote_left_by_a_live_request_is_in_the_request_again():
    assert rb_status(view(), RfqState(live=True, requote=True)) == "in_rfq"


# --- the project chain -----------------------------------------------------------------------------------------

def test_project_reference_wins_and_is_normalized():
    assert line_project("708k.4", "Обеспечение производства.002", "Обеспечение производства") == LineProject(
        "708К.4", "project", "708К.4")
    assert line_project(" вп-001 ", "Склад.003").key == "ВП-001"


def test_project_from_the_designation_when_the_reference_is_empty():
    assert line_project(None, "708К.4.012", "Обеспечение производства") == LineProject("708К.4", "project", "708К.4")
    assert line_project("", "591К.3 · КБ1 · Жгут.001").key == "591К.3"
    assert line_project(None, "ВП-002.004").key == "ВП-002"


def test_purpose_label_when_no_project_is_known():
    assert line_project(None, "Обеспечение производства.002", "Обеспечение производства") == LineProject(
        "Обеспечение производства", "purpose", "Обеспечение производства")
    # No purpose looked up: НАЗНАЧЕНИЕ without its counter.
    assert line_project(None, "RND.014").value == "RND"


def test_nothing_known_is_without_project():
    assert line_project(None, "", None) == LineProject(NO_PROJECT_KEY, "none", "")


def test_batch_is_the_designation_under_one_project():
    project = line_project(None, "Обеспечение производства.002", "Обеспечение производства")
    assert batch_key(project, "обеспечение  производства.002") == batch_key(project, "Обеспечение производства.002")
    assert batch_key(project, "Обеспечение производства.002") != batch_key(project, "Обеспечение производства.003")
    other = line_project("614К.1", "Обеспечение производства.002")
    assert batch_key(other, "Обеспечение производства.002") != batch_key(project, "Обеспечение производства.002")


# --- the summary and the rows ----------------------------------------------------------------------------------

def test_summary_groups_projects_and_batches_with_status_counts():
    a1 = view(designation="708К.4.012")
    a2 = view(designation="708К.4.012", status="Отменено")
    b1 = view(designation="708К.4.013", created_at=datetime(2026, 10, 19, 12, 0))
    c1 = view(designation="Обеспечение производства.002", buyer_id=None, status="Новый",
              created_at=datetime(2026, 10, 19, 9, 0))
    states = {a1.id: RfqState(live=True, rfq_number="КП-2610-001")}
    purposes = {UUID(int=1): "Обеспечение производства"}
    summary = build_summary([a1, a2, b1, c1], purposes, states, BUYER.id, focus=c1.id)
    assert [(p["key"], p["lines"], p["counts"]) for p in summary["projects"]] == [
        ("708К.4", 3, {"new": 1, "in_rfq": 1, "cancelled": 1}),
        ("Обеспечение производства", 1, {"new": 1}),
    ]
    assert [(b["designation"], b["lines"]) for b in summary["batches"]] == [
        ("708К.4.013", 1), ("708К.4.012", 2), ("Обеспечение производства.002", 1),
    ]
    first = summary["batches"][1]
    assert first["counts"] == {"in_rfq": 1, "cancelled": 1} and first["mine"] == 2 and first["selectable"] == 0
    assert summary["batches"][2]["to_take"] == 1
    assert summary["focus"] == {"project": "Обеспечение производства",
                                "batch": "Обеспечение производства|обеспечение производства.002"}
    only = build_summary([a1, a2, b1, c1], purposes, states, BUYER.id, project="708К.4")
    assert {b["designation"] for b in only["batches"]} == {"708К.4.012", "708К.4.013"}
    assert len(only["projects"]) == 2  # the filter list always shows every project of the scope


def test_rows_say_what_the_viewer_may_do():
    mine = view()
    foreign = view(buyer_id=OTHER.id, buyer_name="Сидоров")
    free = view(status="Новый", buyer_id=None, buyer_name=None)
    gone = view(status="Отменено")
    rows = {row["id"]: row for row in line_results(
        [mine, foreign, free, gone], {}, BUYER.id, reasons={gone.id: "не закупаем"},
        free_stock={mine.id: Decimal("4")},
    )}
    assert rows[str(mine.id)]["selectable"] and rows[str(mine.id)]["can_split"] and rows[str(mine.id)]["can_cancel"]
    assert rows[str(mine.id)]["free_stock"] == "4"
    assert not rows[str(foreign.id)]["selectable"] and not rows[str(foreign.id)]["can_cancel"]
    assert rows[str(free.id)]["can_take"] and not rows[str(free.id)]["selectable"]
    assert rows[str(gone.id)]["cancel_reason"] == "не закупаем" and rows[str(gone.id)]["can_resend"]
    # «Все» (no actor): nothing is selectable or actionable.
    for row in line_results([mine, gone], {}, None):
        assert not any(row[key] for key in ("selectable", "can_split", "can_cover", "can_cancel", "can_resend"))


def test_plan_is_the_calc_price_with_vat_and_where():
    assert plan_unit_gross(view(price_with_vat=Decimal("122.00"))) == Decimal("122.00")
    assert plan_unit_gross(view(price=Decimal("100"))) == Decimal("122.00")
    assert plan_unit_gross(view()) is None
    assert plan_label("https://www.elitan.ru/item/1", {}) == "elitan.ru"
    assert plan_label("https://www.elitan.ru/item/1", {"elitan.ru": "Элитан"}) == "Элитан"
    assert plan_label("Фраксис", {}) == "Фраксис"
    assert plan_label(None, {}) == ""


# --- «Разделить» by quantity -----------------------------------------------------------------------------------

def taken_line(**changes):
    line = create_line_from_requested(requested_message()).take_in_work(BUYER)
    return replace(line, **changes)


def test_split_shares_the_quantity_and_keeps_the_buyer_facts():
    source = taken_line(
        calculated_quantity=Decimal("10"), calculated_sum=Decimal("10000.00"), actual_quantity=Decimal("10"),
        supplier="ООО Поставка", actual_unit="шт",
    )
    original, created = split_by_quantity(
        source, Decimal("3"), ["SM-20261019-01", "SM-20261019-02-Д"], DAY, NOW, BUYER,
    )
    assert (original.calculated_quantity, original.actual_quantity, original.calculated_sum) == (
        Decimal("7"), Decimal("7"), Decimal("7000.00"))
    assert original.version == source.version + 1 and original.id == source.id
    assert created.legacy_id == "SM-20261019-03" and created.id != source.id
    assert (created.calculated_quantity, created.actual_quantity, created.calculated_sum) == (
        Decimal("3"), Decimal("3"), Decimal("3000.00"))
    assert created.buyer == BUYER and created.supplier == "ООО Поставка" and created.status is ProcurementStatus.READY
    assert (created.designation, created.source_request_id, created.revision, created.version) == (
        source.designation, source.source_request_id, source.revision, 1)
    assert created.calculated_price_with_vat == source.calculated_price_with_vat
    assert created.stock_covered == 0 and created.note is None and created.money == source.money


def test_split_in_a_request_for_quotes_leaves_the_supplier_to_its_own_choice():
    source = taken_line(calculated_quantity=Decimal("10"), supplier="ООО Поставка", actual_quantity=Decimal("10"))
    original, created = split_by_quantity(source, Decimal("4"), [], DAY, NOW, BUYER, keep_supplier=False)
    assert original.supplier == "ООО Поставка" and created.supplier is None
    assert created.status is ProcurementStatus.IN_WORK


def test_split_takes_only_from_what_is_still_to_buy():
    source = taken_line(calculated_quantity=Decimal("10"), stock_covered=Decimal("4"), actual_quantity=Decimal("6"))
    with pytest.raises(ProcurementValidationError, match="больше нуля и меньше 6 шт"):
        split_by_quantity(source, Decimal("6"), [], DAY, NOW, BUYER)
    for wrong in (Decimal("0"), Decimal("-1"), Decimal("0.0000001")):
        with pytest.raises(ProcurementValidationError):
            split_by_quantity(source, wrong, [], DAY, NOW, BUYER)
    original, created = split_by_quantity(source, Decimal("2.5"), [], DAY, NOW, BUYER)
    assert original.to_buy == Decimal("3.5") and original.actual_quantity == Decimal("3.5")
    assert created.to_buy == Decimal("2.5")


def test_split_is_the_owner_s_and_only_before_the_supplier_request():
    with pytest.raises(ProcurementAccessDenied):
        split_by_quantity(taken_line(), Decimal("1"), [], DAY, NOW, OTHER)
    with pytest.raises(ProcurementValidationError, match="возьмите строку в работу"):
        split_by_quantity(create_line_from_requested(requested_message()), Decimal("1"), [], DAY, NOW, BUYER)
    with pytest.raises(ProcurementValidationError, match="до запроса поставщику"):
        split_by_quantity(taken_line(status=ProcurementStatus.AWAITING_INVOICE), Decimal("1"), [], DAY, NOW, BUYER)


def test_cancel_reason_is_required_and_bounded():
    assert cancel_reason("  не   закупаем ") == "не закупаем"
    with pytest.raises(ProcurementValidationError, match="Укажите причину"):
        cancel_reason("   ")
    with pytest.raises(ProcurementValidationError, match="длиннее"):
        cancel_reason("я" * 501)
