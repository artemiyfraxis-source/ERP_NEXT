"""«Показатели закупщика»: the facts of other modules (Roller_Buyer `kpi.test.ts` replayed on ERP tables) — the КП
baseline, the previous purchase, defects and returns, decisions, answers of suppliers, delivery of orders."""

from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, Uuid, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import (
    ProcurementLineRow, SqlAlchemyProcurementUnitOfWork, _line_domain,
)
from app.modules.procurement.kpi import build_kpi
from app.modules.purchasing.infrastructure import (
    DecisionRow, OrderFactRow, QuoteLineRow, QuoteRow, RfqLineRow, RfqRow, RfqSupplierRow, SupplierNameRow, SupplierRow,
)
from app.modules.purchasing.suppliers import supplier_key
from app.modules.receiving.domain import MECHANICAL_DAMAGE
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.purchasing_kpi import purchasing_kpi_facts
from tests.procurement.test_api import message

START, END, TODAY = date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 20)
PAID = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
DECIDED = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
_suffix = count(1)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def bought(sessions, supplier="Альфа", price="44", quantity="100", need="100", unit="шт", paid=PAID, buyer="Петров",
           article=None, name="Разъём", **row):
    """A paid line (Roller's `line()` helper: 100 × 44, need 100)."""
    n = next(_suffix)
    msg = message(n)
    msg.payload.update(calculated_name=name, design_article=article, calculated_unit=unit, calculated_quantity=need)
    line = consume_procurement_requested(msg, SqlAlchemyProcurementUnitOfWork(sessions))
    values = dict(
        supplier=supplier, actual_unit=unit, actual_quantity=Decimal(quantity), actual_price_with_vat=Decimal(price),
        actual_sum=Decimal(price) * Decimal(quantity), paid_at=paid, buyer_name=buyer, buyer_id=uuid4(),
    )
    values.update(row)
    with sessions() as s:
        stored = s.get(ProcurementLineRow, line.id)
        for key, value in values.items():
            setattr(stored, key, value)
        s.commit()
        return _line_domain(stored)


def supplier(sessions, name, key=False):
    with sessions() as s:
        row = SupplierRow(id=uuid4(), name=name, name_key=name.casefold(), categories=[], is_key=key, domains=[],
                          created_at=DECIDED, version=1)
        s.add(row)
        s.commit()
        return row.id


def request(sessions, lines, buyer_id=None, number=None):
    """A request for quotes over procurement lines → (rfq id, {procurement line id: rfq line id})."""
    with sessions() as s:
        rfq = RfqRow(id=uuid4(), number=number or f"КП-{next(_suffix)}", buyer_id=buyer_id, created_at=DECIDED, version=1)
        s.add(rfq)
        s.flush()
        ids = {}
        for line in lines:
            row = RfqLineRow(id=uuid4(), rfq_id=rfq.id, procurement_line_id=line.id, name=line.calculated_name,
                             quantity=line.calculated_quantity, unit=line.calculated_unit, created_at=DECIDED)
            s.add(row)
            ids[line.id] = row.id
        s.commit()
        return rfq.id, ids


def quote(sessions, rfq_id, supplier_id, rfq_line_id, price, key=False, on=date(2026, 10, 3), declined=False):
    with sessions() as s:
        head = QuoteRow(id=uuid4(), rfq_id=rfq_id, supplier_id=supplier_id, supplier_is_key=key, received_on=on,
                        currency="RUB", source="manual", created_at=DECIDED)
        s.add(head)
        s.flush()
        row = QuoteLineRow(id=uuid4(), quote_id=head.id, rfq_line_id=rfq_line_id, unit_price=None if declined else Decimal(price),
                           vat_included=True, vat_rate=Decimal("22"), tiers=[], declined=declined)
        s.add(row)
        s.commit()
        return row.id


def decide(sessions, rfq_line_id, quote_line_id, baseline="50", priced=3, override=None, at=DECIDED):
    with sessions() as s:
        s.add(DecisionRow(
            id=uuid4(), rfq_line_id=rfq_line_id, quote_line_id=quote_line_id, cover_quantity=Decimal("100"),
            unit_gross=Decimal("44"), total_gross=Decimal("4400"), baseline_unit_gross=Decimal(baseline) if baseline else None,
            priced_quotes=priced, gate_override_code=override, gate_override_reason="срочно" if override else None,
            decided_at=at,
        ))
        s.commit()


def required(model) -> dict:
    """Every NOT NULL column of a table filled by its type."""
    values = {}
    for column in model.__table__.columns:
        if column.nullable or column.default is not None or column.server_default is not None:
            continue
        kind = column.type
        values[column.name] = (
            uuid4() if isinstance(kind, Uuid) else 1 if isinstance(kind, Integer) else False if isinstance(kind, Boolean)
            else Decimal(0) if isinstance(kind, Numeric) else [] if isinstance(kind, JSON)
            else DECIDED if isinstance(kind, DateTime) else date(2026, 10, 1) if isinstance(kind, Date) else "x"
        )
    return values


def receive(sessions, line, nonconformities=(), status="Принято", **row):
    values = required(ReceivingLineRow)
    rid = uuid4()
    values.update(id=rid, procurement_line_id=line.id, source_idempotency_key=f"k{rid}", legacy_id="SM-1", status=status,
                  nonconformities=list(nonconformities))
    values.update(row)
    with sessions() as s:
        s.add(ReceivingLineRow(**values))
        s.commit()
    return rid


def facts(sessions, lines, buyer=None, start=START, end=END):
    with sessions() as s:
        return purchasing_kpi_facts(s, lines, start, end, buyer, TODAY)


def sheet(sessions, lines, buyer=None):
    return build_kpi(lines, START, END, buyer, TODAY, facts(sessions, lines, buyer))


# ---- «Экономия к КП» ------------------------------------------------------------------------------------------------

def test_savings_against_the_kp_baseline_of_the_decision(sessions):
    saved, dearer = bought(sessions), bought(sessions, price="52")
    seller = supplier(sessions, "Альфа", key=True)
    rfq, rfq_lines = request(sessions, [saved, dearer])
    for line in (saved, dearer):
        decide(sessions, rfq_lines[line.id], quote(sessions, rfq, seller, rfq_lines[line.id], "44"))
    money = sheet(sessions, [saved, dearer])["kpi"]["money"]
    assert money["kp_baseline_spend"] == "10000.00"
    assert money["kp_savings"] == "400.00"  # +600 and −200
    assert money["kp_savings_pct"] == "4.0" and money["kp_compared_lines"] == 2


def test_a_line_without_a_decision_or_in_another_unit_is_not_compared_to_the_kp(sessions):
    plain, other_unit = bought(sessions), bought(sessions, unit="м", need="100")
    seller = supplier(sessions, "Альфа")
    rfq, rfq_lines = request(sessions, [other_unit])
    decide(sessions, rfq_lines[other_unit.id], quote(sessions, rfq, seller, rfq_lines[other_unit.id], "44"))
    with sessions() as s:
        s.query(RfqLineRow).update({RfqLineRow.unit: "шт"})  # the request line was in pieces, the line is bought in metres
        s.commit()
    money = sheet(sessions, [plain, other_unit])["kpi"]["money"]
    assert money["kp_savings"] is None and money["kp_savings_pct"] is None and money["kp_compared_lines"] == 0


def test_buying_above_the_need_counts_only_the_need_against_the_kp(sessions):
    line = bought(sessions, quantity="1000", need="87")
    rfq, rfq_lines = request(sessions, [line])
    decide(sessions, rfq_lines[line.id], quote(sessions, rfq, supplier(sessions, "Альфа"), rfq_lines[line.id], "44"))
    assert sheet(sessions, [line])["kpi"]["money"]["kp_savings"] == "522.00"  # 87 × 6


# ---- «К прошлой закупке», новые поставщики ----------------------------------------------------------------------------

def test_vs_last_purchase_compares_with_the_previous_price_of_the_same_item(sessions):
    bought(sessions, price="46", article="SHR-02V", paid=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc), supplier="Бета")
    bought(sessions, price="30", article="SHR-02V", paid=datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc), unit="м",
           supplier="Бета")  # another unit: not the same price
    current = bought(sessions, price="44", article="SHR-02V")
    other = bought(sessions, price="10", article="XH-2P")  # nothing bought before
    money = sheet(sessions, [current, other])["kpi"]["money"]
    assert money["vs_last_purchase"] == "200.00" and money["compared_with_last"] == 1  # (46 − 44) × 100


def test_no_previous_purchase_is_no_data_not_zero(sessions):
    money = sheet(sessions, [bought(sessions, article="ONLY-ONE")])["kpi"]["money"]
    assert money["vs_last_purchase"] is None and money["compared_with_last"] == 0


def test_new_suppliers_are_those_first_paid_in_the_period(sessions):
    bought(sessions, supplier="ООО Старая", paid=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc))
    lines = [bought(sessions, supplier="Старая"), bought(sessions, supplier="Новая"), bought(sessions, supplier="Склад")]
    assert sheet(sessions, lines)["kpi"]["competition"]["new_suppliers"] == 1  # «Склад» is our stock, not a supplier


# ---- брак и возвраты ---------------------------------------------------------------------------------------------------

def test_defects_and_returns_come_from_receiving(sessions):
    bad, good, waiting = bought(sessions), bought(sessions, price="10"), bought(sessions)
    receive(sessions, bad, [MECHANICAL_DAMAGE], status="Возврат")
    receive(sessions, good)
    receive(sessions, waiting, status="В пути")  # not inspected yet: outside the denominator
    with sessions() as s:
        returned = s.query(ReceivingLineRow).filter(ReceivingLineRow.procurement_line_id == bad.id).one()
        s.add(AuditEventRow(id=uuid4(), aggregate_id=returned.id, event_type="receiving.returned_to_supplier",
                            actor_id=uuid4(), actor_name="Склад", actor_department="Склад", occurred_at=DECIDED,
                            details={"quantity": "5"}))
        s.commit()
    quality = sheet(sessions, [bad, good, waiting])["kpi"]["quality"]
    assert quality["received_lines"] == 2 and quality["lines_with_defect"] == 1 and quality["defect_pct"] == "50.0"
    assert quality["returned_amount"] == "220.00"  # 5 × 44
    # brak inspected before 24.10 carries no quantity: «нет данных», counted as «без количества», never a zero
    assert quality["defect_amount"] is None and quality["defect_without_quantity"] == 1


def test_defect_money_is_the_defect_quantity_times_the_paid_price(sessions):
    counted, older, good = bought(sessions), bought(sessions, price="10", supplier="Бета"), bought(sessions)
    receive(sessions, counted, [MECHANICAL_DAMAGE], status="Ожидание", fact_quantity=Decimal("100"),
            defect_quantity=Decimal("3"))
    receive(sessions, older, [MECHANICAL_DAMAGE], status="Ожидание", fact_quantity=Decimal("100"))  # no quantity
    receive(sessions, good)
    result = sheet(sessions, [counted, older, good])
    quality = result["kpi"]["quality"]
    assert quality["defect_amount"] == "132.00"  # 3 × 44; the line without a quantity is not a zero …
    assert quality["defect_without_quantity"] == 1  # … it is named
    assert quality["lines_with_defect"] == 2 and quality["defect_pct"] == "66.7"
    rating = {row["supplier"]: row for row in result["suppliers"]}
    assert rating["Альфа"]["defect_amount"] == "132.00" and rating["Альфа"]["defect_pct"] == "50.0"
    assert rating["Бета"]["defect_amount"] is None and rating["Бета"]["defect_pct"] == "100.0"


def test_a_delivery_and_its_vk_revisions_count_their_defect_once_batches_each(sessions):
    line = bought(sessions)
    root = receive(sessions, line, [MECHANICAL_DAMAGE], status="Архив ВК", fact_quantity=Decimal("60"),
                   defect_quantity=Decimal("5"), created_at=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc))
    receive(sessions, line, [MECHANICAL_DAMAGE], status="Ожидание", fact_quantity=Decimal("60"),
            defect_quantity=Decimal("2"), revision_of_id=root, revision_root_id=root, legacy_id="SM-1-В2",
            created_at=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc))  # the re-inspection's count replaces the first
    receive(sessions, line, [MECHANICAL_DAMAGE], status="Ожидание", fact_quantity=Decimal("40"),
            defect_quantity=Decimal("1"), split_of_id=root, legacy_id="SM-1-П2")  # another batch: its own pieces
    quality = sheet(sessions, [line])["kpi"]["quality"]
    assert quality["defect_amount"] == "132.00"  # (2 + 1) × 44
    assert quality["defect_without_quantity"] == 0 and quality["lines_with_defect"] == 1


def test_nothing_received_is_no_data(sessions):
    quality = sheet(sessions, [bought(sessions)])["kpi"]["quality"]
    assert quality["defect_pct"] is None and quality["returned_amount"] is None and quality["received_lines"] is None


# ---- решения ---------------------------------------------------------------------------------------------------------------

def test_decision_figures_mirror_roller(sessions):
    one, two = bought(sessions), bought(sessions)
    key, other = supplier(sessions, "Ключ", key=True), supplier(sessions, "Вольный")
    rfq, rfq_lines = request(sessions, [one, two])
    first = rfq_lines[one.id]
    q1 = quote(sessions, rfq, key, first, "44", key=True)
    quote(sessions, rfq, other, first, "46")
    decide(sessions, first, q1, priced=3)
    second = rfq_lines[two.id]
    decide(sessions, second, quote(sessions, rfq, key, second, "44", key=True), priced=1, override="single_source")
    outside = bought(sessions)
    rfq2, lines2 = request(sessions, [outside])
    decide(sessions, lines2[outside.id], quote(sessions, rfq2, key, lines2[outside.id], "44", key=True),
           at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc))
    competition = sheet(sessions, [one, two])["kpi"]["competition"]
    assert competition["decisions"] == 2 and competition["avg_offers"] == "2.0"
    assert competition["with_non_key_pct"] == "50.0" and competition["override_pct"] == "50.0"


def test_decisions_follow_the_buyer_filter(sessions):
    mine, theirs = uuid4(), uuid4()
    with sessions() as s:
        for uid, name in ((mine, "Петров"), (theirs, "Сидоров")):
            s.add(IdentityUserRow(**{**required(IdentityUserRow), "id": uid, "login": name, "email": f"{name}@x.test",
                                     "name": name, "department": "Закупки", "role": "buyer"}))
        s.commit()
    line_a, line_b = bought(sessions), bought(sessions, buyer="Сидоров")
    seller = supplier(sessions, "Альфа")
    for line, owner in ((line_a, mine), (line_b, theirs)):
        rfq, ids = request(sessions, [line], buyer_id=owner)
        decide(sessions, ids[line.id], quote(sessions, rfq, seller, ids[line.id], "44"))
    assert sheet(sessions, [line_a, line_b])["kpi"]["competition"]["decisions"] == 2
    assert sheet(sessions, [line_a], "Петров")["kpi"]["competition"]["decisions"] == 1
    assert sheet(sessions, [line_b], "Сидоров")["kpi"]["competition"]["decisions"] == 1
    assert sheet(sessions, [line_a], "Без закупщика")["kpi"]["competition"]["decisions"] == 0


# ---- рейтинг поставщиков --------------------------------------------------------------------------------------------------

def test_supplier_rating_response_and_price_against_the_best(sessions):
    line = bought(sessions, supplier="Альфа")
    alpha, beta, silent = supplier(sessions, "Альфа"), supplier(sessions, "Бета", key=True), supplier(sessions, "Молчун")
    rfq, ids = request(sessions, [line])
    with sessions() as s:
        for seller in (alpha, beta, silent):
            s.add(RfqSupplierRow(id=uuid4(), rfq_id=rfq, supplier_id=seller, reason="manual", sent_at=DECIDED, created_at=DECIDED))
        s.commit()
    quote(sessions, rfq, alpha, ids[line.id], "110")
    quote(sessions, rfq, beta, ids[line.id], "100", key=True)
    rows = {row["supplier"]: row for row in sheet(sessions, [line])["suppliers"]}
    assert set(rows) == {"Альфа", "Бета", "Молчун"}  # asked suppliers show up even without a paid line
    assert rows["Альфа"]["asked"] == 1 and rows["Альфа"]["answered"] == 1 and rows["Альфа"]["response_pct"] == "100.0"
    assert rows["Альфа"]["price_vs_best_pct"] == "10.0" and rows["Альфа"]["is_key"] is False
    assert rows["Бета"]["price_vs_best_pct"] == "0.0" and rows["Бета"]["is_key"] is True and rows["Бета"]["order_lines"] == 0
    assert rows["Молчун"]["response_pct"] == "0.0" and rows["Молчун"]["price_vs_best_pct"] is None


def test_a_supplier_text_matches_its_card_by_an_alias(sessions):
    # Prod 23.10: aliases are read as rows of the alias table, not as tuples — the sheet answered 500 on them.
    line = bought(sessions, supplier="Альфа-Снаб")
    alpha = supplier(sessions, "Альфа", key=True)
    with sessions() as s:
        s.add(SupplierNameRow(name_key=supplier_key("Альфа-Снаб"), supplier_id=alpha, created_at=DECIDED))
        s.commit()
    row = sheet(sessions, [line])["suppliers"][0]
    assert row["is_key"] is True and row["asked"] == 0


def test_a_supplier_text_without_a_card_has_no_request_data(sessions):
    row = sheet(sessions, [bought(sessions, supplier="Без карточки")])["suppliers"][0]
    assert row["asked"] is None and row["response_pct"] is None and row["is_key"] is None


# ---- доставка ----------------------------------------------------------------------------------------------------------------

def test_delivery_comes_from_the_order_facts_of_the_period_lines(sessions):
    one, two = bought(sessions, order_number="ЗК-1"), bought(sessions, order_number="ЗК-1")
    three = bought(sessions, order_number="ЗК-2")
    with sessions() as s:
        for number, cost in (("ЗК-1", "1500"), ("ЗК-2", None), ("ЗК-9", "999")):
            s.add(OrderFactRow(order_number=number, delivery_cost=Decimal(cost) if cost else None, shipment_note="", note="",
                               created_at=DECIDED, updated_at=DECIDED, updated_by_name="", version=1))
        s.commit()
    assert sheet(sessions, [one, two, three])["kpi"]["money"]["delivery_spend"] == "1500.00"  # once per order, ЗК-9 is not ours
    assert sheet(sessions, [three])["kpi"]["money"]["delivery_spend"] is None


# ---- запросы к базе не растут с данными --------------------------------------------------------------------------------------------

def test_the_number_of_queries_does_not_grow_with_the_lines(sessions):
    def queries(lines):
        seen = []
        engine = sessions.kw["bind"]
        listener = lambda *args: seen.append(args[2])  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            facts(sessions, lines)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        return len(seen)

    few = [bought(sessions, article=f"A-{i:03d}") for i in range(2)]
    many = few + [bought(sessions, article=f"B-{i:03d}") for i in range(30)]
    seller = supplier(sessions, "Альфа")
    rfq, ids = request(sessions, many)
    for line in many:
        decide(sessions, ids[line.id], quote(sessions, rfq, seller, ids[line.id], "44"))
        receive(sessions, line, [MECHANICAL_DAMAGE], status="Ожидание", fact_quantity=Decimal("100"),
                defect_quantity=Decimal("1"))
    assert queries(many) == queries(few)
