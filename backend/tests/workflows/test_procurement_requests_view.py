"""«Заявки» закупщика ↔ other modules (app/workflows/procurement_requests_view.py): free stock of a line's item, the
last purchase merged from paid lines and UPDs, the request-for-quotes states."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.procurement.requests_view import LastPurchase, ViewLine
from app.modules.purchasing import line_purchases
from app.modules.purchasing.infrastructure import RfqLineRow, RfqRow
from app.modules.warehouse.infrastructure import StockLotRow
from app.shared.persistence import create_schema
from app.workflows import procurement_requests_view as workflow


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def view(**changes) -> ViewLine:
    base = ViewLine(
        id=uuid4(), legacy_id="SM-20261019-01", version=1, status="В работе", buyer_id=None, buyer_name=None,
        source_request_id=UUID(int=1), created_at=datetime(2026, 10, 19), request_date=date(2026, 10, 19),
        planned_date=date(2026, 10, 30), initiator="Иванов", designation="RND.001", project_reference=None,
        item_type="Разъём", category="Общепром", name="Разъём SMA", article="SMA-001", quantity=Decimal("10"), unit="шт",
    )
    return replace(base, **changes)


def lot(grn: str, *, article="", name="", unit="шт", inbound="0", written_off="0", reserved="0") -> StockLotRow:
    return StockLotRow(
        id=uuid4(), version=1, warehouse="ЦАР", grn=grn, package="", article=article, series="", name=name,
        item_type="", purpose="Склад", unit=unit, storage_location="", inbound=Decimal(inbound),
        written_off=Decimal(written_off), reserved=Decimal(reserved), source_receiving_ids=[],
    )


def test_free_stock_sums_what_is_available_of_the_same_item_and_unit(sessions):
    with sessions() as session:
        session.add_all([
            lot("ГРН-1", article="SMA 001", name="Разъём SMA старый", inbound="10", written_off="2", reserved="3"),  # 5
            lot("ГРН-2", article="sma-001", unit="шт.", inbound="4"),  # 4
            lot("ГРН-3", article="SMA-001", unit="м", inbound="100"),  # another unit
            lot("ГРН-4", article="SMA-001", inbound="2", written_off="2"),  # nothing left
            lot("ГРН-5", name="Провод МГТФ 0,12", unit="м", inbound="50", reserved="20"),  # by name: 30
        ])
        session.commit()
        by_article = view()
        by_name = view(article=None, name="провод  МГТФ 0,12", unit="м")
        nothing = view(article="XYZ-9")
        found = workflow.free_stock(session, [by_article, by_name, nothing])
    assert found == {by_article.id: Decimal("9"), by_name.id: Decimal("30")}


def test_last_purchase_is_the_newest_of_paid_lines_and_upds(sessions, monkeypatch):
    older, newer = view(), view(name="Разъём BNC")
    paid = {
        older.id: LastPurchase(Decimal("90"), date(2026, 9, 1), "ООО Альфа", "шт", "procurement"),
        newer.id: LastPurchase(Decimal("50"), date(2026, 10, 1), "ООО Бета", "шт", "procurement"),
    }
    item = uuid4()

    class Nomenclature:
        def __init__(self, session):
            pass

        def match(self, line_id, article, name):
            return item, "part_key"

    monkeypatch.setattr(workflow, "_Nomenclature", Nomenclature)
    monkeypatch.setattr(line_purchases, "last_purchases", lambda session, wanted: {
        wanted[0].key: line_purchases.UpdPurchase(Decimal("80.5"), "2026-09-20", "ООО УПД", "17"),
        wanted[1].key: line_purchases.UpdPurchase(Decimal("40"), "2026-09-20", "ООО УПД", "18"),
    })
    with sessions() as session:
        merged = workflow.last_purchases(session, [older, newer], paid)
    assert merged[older.id] == LastPurchase(Decimal("80.5"), date(2026, 9, 20), "ООО УПД", "шт", "upd")
    assert merged[newer.id] == paid[newer.id]


def test_rfq_states_requote_unless_the_line_was_sent_back_to_new(sessions, monkeypatch):
    first, second, third = uuid4(), uuid4(), uuid4()
    rfq = uuid4()
    now = datetime(2026, 10, 19, 10, 0)
    with sessions() as session:
        session.add(RfqRow(id=rfq, number="КП-2610-001", created_at=now, version=1))
        session.flush()
        for line_id, removed in ((first, now), (second, None), (third, now)):
            session.add(RfqLineRow(id=uuid4(), rfq_id=rfq, procurement_line_id=line_id, name="x", quantity=Decimal("1"),
                                   unit="шт", created_at=now, removed_at=removed))
        session.commit()
        # The rfq slice stores why a line left a request; «В новые» is not a requote.
        monkeypatch.setattr(RfqLineRow, "removed_reason", None, raising=False)
        row = session.query(RfqLineRow).filter_by(procurement_line_id=third).one()
        row.removed_reason = "new"
        states = workflow.rfq_states(session, [first, second, third, uuid4()])
    assert states[first].requote and states[first].requote_from == "КП-2610-001" and not states[first].live
    assert states[second].live and states[second].rfq_number == "КП-2610-001" and not states[second].requote
    assert not states[third].requote and not states[third].live
    assert len(states) == 3
