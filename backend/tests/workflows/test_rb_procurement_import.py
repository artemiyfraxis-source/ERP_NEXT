"""Roller_Buyer → ERP procurement flow (app/workflows/rb_procurement_import.py) over a tiny Roller_Buyer-shaped snapshot:
one request line per RB status, a request for quotes with quotes and decisions, a paid and an unpaid order, a withdrawn
line — the ERP stages, the links, the re-run that writes nothing, the buyer's ERP change kept, and what the existing
read endpoints («Заявки», «Счета и поставки») show of the result."""

import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.application import SetNoteCommand, set_procurement_note
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    DocumentFileRow,
    LineItemRow,
    OrderFactRow,
    PurchasingSettingRow,
    QuoteLineRow,
    QuoteRow,
    RfqDeclinedSuggestionRow,
    RfqLineRow,
    RfqRow,
    RfqSupplierRow,
    SqlAlchemyPurchasingUnitOfWork,
)
from app.modules.purchasing.rb_import import Importer, open_snapshot
from app.modules.requests.api import moscow_now
from app.modules.requests.infrastructure import RequestRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, Base, OutboxMessageRow, create_schema
from app.workflows.procurement_history import line_history
from app.workflows.purchasing_orders import order_lines
from app.workflows.rb_procurement_import import (
    IMPORTED,
    TOLLING_REASON,
    ProcurementFlowImporter,
    Snapshot,
    _id,
    enable_savepoints,
)
from tests.purchasing.test_rb_import import SCHEMA as REFERENCE_SCHEMA

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
VITYA = UUID("c48f6346-6383-4f4a-98b7-1e95d8578e6d")

FLOW_SCHEMA = """
create table settings (key text primary key, value text);
create table projects (id integer primary key, number text, customer text, name text, kind text, calc_url text,
  calc_id text, closed_at text, created_at text);
create table requests (id integer primary key, number text, source text, received_at text, urgent integer, note text,
  created_at text, project_id integer);
create table request_lines (id integer primary key, request_id integer, item_id integer, quantity real, need_by text,
  supply text, budget_unit_gross integer, cancelled_at text, cancel_reason text, plan_source text, requote_at text,
  requote_note text);
create table rfqs (id integer primary key, created_at text, note text);
create table rfq_lines (id integer primary key, rfq_id integer, request_line_id integer);
create table rfq_suppliers (id integer primary key, rfq_id integer, supplier_id integer, sent_at text,
  declined_suggestion integer);
create table quotes (id integer primary key, rfq_id integer, supplier_id integer, supplier_is_key integer,
  received_at text, number text, valid_until text, source text, file_name text, delivery_cost integer,
  min_order_sum integer);
create table quote_lines (id integer primary key, quote_id integer, rfq_line_id integer, unit_price integer,
  vat_included integer, vat_rate integer, moq real, lead_days integer, in_stock integer, declined integer,
  analog integer, analog_note text, pack real, tiers text);
create table decisions (id integer primary key, rfq_line_id integer, quote_line_id integer, gate_override_reason text,
  gate_override_note text, not_cheapest_reason text, not_cheapest_note text, analog_approved_by text, decided_at text);
create table orders (id integer primary key, supplier_id integer, number text, order_date text, promised_date text,
  expected_date text, paid_at text, note text, file_name text, sent_to_pay_at text, shipped_at text,
  shipment_note text, delivery_cost integer);
create table order_lines (id integer primary key, order_id integer, decision_id integer, quantity real,
  unit_price integer, vat_included integer, vat_rate integer, short_closed_at text, short_reason text);
create table receipts (id integer primary key, order_line_id integer, date text, received real, defect real,
  returned real, reason text, upd_file_id text, upd_seq integer, upd_unit_gross integer);
"""

# RB request line → its RB status, as the snapshot below makes it.
EXPECTED_RB = {
    1: "new", 2: "requote", 3: "in_rfq", 4: "quoted", 5: "decided", 6: "shipped", 7: "ordered", 8: "cancelled",
    9: "from_stock", 10: "tolling", 11: "to_pay",
}
EXPECTED_ERP = {
    1: "В работе", 2: "В работе", 3: "В работе", 4: "В работе", 5: "Готов к запросу", 6: "В пути", 7: "Жду счет",
    8: "Отменено", 9: "В работе", 10: "Отменено", 11: "В оплату",
}
# What «Заявки» (requests-view) computes back from the ERP facts; line 7 has no invoice anywhere: ERP «Жду счет».
EXPECTED_VIEW = {**EXPECTED_RB, 7: "decided", 9: "new", 10: "cancelled"}


def _invoice(db, row_id: int, external: str, number, day, supplier: int, title: str, files: list) -> None:
    db.execute(
        "insert into invoices values (?, 'kaiten', ?, ?, 'Закупки', 'Оплачено', 'paid', '2026-10-13T08:00:00Z', "
        "'2026-10-14T08:00:00Z', ?, ?, '', '', ?, 100000, 0, 0, '', null, null, ?, null, '', '2026-10-20T10:00:00Z', "
        "'', '[]')",
        (row_id, external, title, number, day, supplier, json.dumps(files)),
    )


@pytest.fixture()
def snapshot(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    (data / "files" / "quotes").mkdir(parents=True)
    (data / "kaiten" / "100").mkdir(parents=True)
    (data / "kaiten" / "101").mkdir(parents=True)
    (data / "files" / "quotes" / "quote-1.pdf").write_bytes(b"%PDF-1.4 quote one")
    (data / "kaiten" / "100" / "5.pdf").write_bytes(b"%PDF-1.4 invoice NF")
    (data / "kaiten" / "101" / "6.pdf").write_bytes(b"%PDF-1.4 invoice KROKS")
    path = tmp_path / "copy.db"
    db = sqlite3.connect(path)
    db.executescript(REFERENCE_SCHEMA + FLOW_SCHEMA)
    for row in (
        (1, 'ООО "КРОКС ПЛЮС"', "3662192701", 1), (2, "АО «ЧИП И ДИП»", "7729108750", 0),
        (3, "ООО «ИНТЕЛЛЕКТУАЛЬНАЯ ТЕХНИКА»", "7811174392", 0),
    ):
        db.execute("insert into suppliers values (?, ?, ?, '[]', ?, '', '', '', '')", row)
    for item_id in range(1, 12):
        db.execute("insert into items values (?, ?, ?, '', 'connector', 'pcs', 'Соединитель', null, '2026-09-25 06:20:35')",
                   (item_id, f"Позиция {item_id}", f"PN-{item_id}"))
    db.execute("insert into projects values (1, '713К.4', 'ООО \"ТСР\"', '713К.4 Расчет', 'order', null, null, null, '')")
    db.execute("insert into projects values (2, '776К.', 'ООО \"ЯНДЕКС\"', '776К. Расчет', 'order', null, null, null, '')")
    db.execute("insert into requests values (1, '713К.4/1', 'production', '2026-10-12', 0, 'По расчёту 713К.4', "
               "'2026-10-12 10:00:00', 1)")
    db.execute("insert into requests values (2, '776К./1', 'production', '2026-10-12', 1, '', '2026-10-12 10:05:00', 2)")
    lines = [
        (1, 1, 1, 10, "buy", 12200, None, "", "https://www.chipdip.ru/product/1", None, ""),
        (2, 1, 2, 5, "buy", None, None, "", "", "2026-10-14", "Запрос КП № 1 от 12.10.2026: КРОКС — не ответил"),
        (3, 1, 3, 7, "buy", None, None, "", "Фраксис", None, ""),
        (4, 1, 4, 20, "buy", None, None, "", "", None, ""),
        (5, 1, 5, 100, "buy", 1500, None, "", "", None, ""),
        (6, 1, 6, 200, "buy", None, None, "", "", None, ""),
        (7, 1, 7, 50, "buy", None, None, "", "", None, ""),
        (8, 1, 8, 3, "buy", None, "2026-10-13", "хз", "", None, ""),
        (9, 1, 9, 4, "stock", None, None, "", "Склад", None, ""),
        (10, 1, 10, 6, "tolling", None, None, "", "Давальческое", None, ""),
        (11, 2, 11, 30, "buy", None, None, "", "", None, ""),
    ]
    for line_id, request, item, qty, supply, budget, cancelled, reason, plan, requote, note in lines:
        db.execute("insert into request_lines values (?, ?, ?, ?, null, ?, ?, ?, ?, ?, ?, ?)",
                   (line_id, request, item, qty, supply, budget, cancelled, reason, plan, requote, note))
    db.execute("insert into rfqs values (1, '2026-10-12', ''), (2, '2026-10-13', 'второй')")
    db.execute("insert into rfq_lines values (1, 1, 3), (2, 1, 4), (3, 1, 5), (4, 2, 6), (5, 2, 7), (6, 2, 11)")
    db.execute("insert into rfq_suppliers values (1, 1, 1, '2026-10-12', 0), (2, 1, 2, '2026-10-12', 0), "
               "(3, 1, 3, null, 1), (4, 2, 1, '2026-10-13', 0), (5, 2, 2, '2026-10-13', 0), (6, 2, 3, '2026-10-13', 0)")
    quotes = [
        (1, 1, 1, 1, "2026-10-13", "КП-1", "2026-10-30", "google", "quote-1.pdf", 50000),
        (2, 1, 2, 0, "2026-10-13", "777", None, "manual", None, None),
        (3, 2, 3, 0, "2026-10-14", "НФ-E14422", None, "manual", None, None),
        (4, 2, 2, 0, "2026-10-14", "", None, "manual", None, None),
        (5, 2, 1, 1, "2026-10-13", "0310261655", None, "excel", None, None),
    ]
    for row in quotes:
        db.execute("insert into quotes values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null)", row)
    quote_lines = [
        (1, 1, 2, 2440, 1, 22, None, None, None, "[]"),
        (2, 1, 3, 1000, 1, 22, 100, 50, None, '[{"from":500,"unitPrice":900}]'),
        (3, 2, 3, 1200, 1, 22, None, None, 3, "[]"),
        (4, 3, 4, 13500, 1, 22, None, None, None, "[]"),
        (5, 4, 5, 9300, 1, 22, None, None, None, "[]"),
        (6, 5, 6, 21000, 0, 20, None, None, None, "[]"),
    ]
    for row_id, quote, rfq_line, price, included, vat, moq, pack, lead, tiers in quote_lines:
        db.execute("insert into quote_lines values (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '', ?, ?)",
                   (row_id, quote, rfq_line, price, included, vat, moq, lead, pack, tiers))
    db.execute("insert into decisions values (1, 3, 3, 'urgent', '', 'other', 'Срок поставки', '', '2026-10-14')")
    db.execute("insert into decisions values (2, 4, 4, 'single_source', '', null, '', '', '2026-10-14')")
    # Decided a day after the order was placed (a Kaiten invoice entered late in RB): the order keeps its own day.
    db.execute("insert into decisions values (3, 5, 5, null, '', null, '', '', '2026-10-15')")
    db.execute("insert into decisions values (4, 6, 6, null, '', null, '', '', '2026-10-13')")
    db.execute("insert into orders values (1, 3, 'НФ-E14422', '2026-10-14', '2026-10-28', null, '2026-10-14', "
               "'Из Kaiten: Счет № НФ-E14422', null, '2026-10-14', '2026-10-16', 'СДЭК 123', 300000)")
    db.execute("insert into orders values (2, 2, '18071137', '2026-10-14', '2026-12-07', null, null, '', null, null, "
               "null, '', 44000)")
    db.execute("insert into orders values (3, 1, '0310261655', '2026-10-13', '2026-10-27', '2026-10-30', null, '', null, "
               "'2026-10-13', null, '', null)")
    db.execute("insert into order_lines values (1, 1, 2, 200, 13500, 1, 22, null, ''), (2, 2, 3, 50, 9300, 1, 22, null, ''), "
               "(3, 3, 4, 30, 21000, 0, 20, null, '')")
    _invoice(db, 1, "100", "НФ-E14422", "2026-10-14", 3, "Счёт НФ-E14422",
             [{"id": "5", "name": "Счет № НФ-E14422.pdf", "kind": "invoice", "size": 19, "path": "kaiten\\100\\5.pdf"}])
    _invoice(db, 2, "101", None, None, 1, "0310261655_1.pdf",
             [{"id": "6", "name": "0310261655_1.pdf", "kind": "other", "size": 22, "path": "kaiten\\101\\6.pdf"}])
    db.commit()
    db.close()
    return path, data


@pytest.fixture()
def erp():
    engine = enable_savepoints(create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        session.add(IdentityUserRow(
            id=VITYA, login="vitya", email="vitya@example.test", name="Виктор", department="Руководящий состав",
            role="admin", active=True, access_level="admin",
        ))
        session.commit()
    yield sessions
    engine.dispose()


BUYER = Actor(id=VITYA, name="Виктор", department="Руководящий состав")


def _reference(sessions, snapshot) -> None:
    path, data = snapshot
    with sessions() as session:
        Importer(session, open_snapshot(path), data, NOW).run()
        session.commit()


def _flow(sessions, snapshot, now=NOW, apply=True):
    path, data = snapshot
    with sessions() as session:
        report = ProcurementFlowImporter(session, Snapshot(open_snapshot(path)), data, BUYER, now).run()
        session.commit() if apply else session.rollback()
    return report


def _counts(sessions) -> dict[str, int]:
    with sessions() as session:
        return {table.name: session.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}


def _line(session, rb_id: int) -> ProcurementLineRow:
    return session.get(ProcurementLineRow, _id("line", rb_id))


def test_every_rb_status_lands_on_its_erp_stage_with_links(erp, snapshot):
    _reference(erp, snapshot)
    report = _flow(erp, snapshot)
    assert not report.skipped, report.skipped
    with erp() as session:
        stages = {rb_id: _line(session, rb_id).status for rb_id in EXPECTED_RB}
        assert stages == EXPECTED_ERP
        assert sum(report.statuses.values()) == 11
        assert report.statuses[("ordered", "Жду счет")] == 1 and report.statuses[("shipped", "В пути")] == 1

        # Requests: one per RB line, the lines of one RB request share one НАЗНАЧЕНИЕ, the project number is ERP's.
        requests = {row.id: row for row in session.scalars(select(RequestRow))}
        assert len(requests) == 11
        first = {requests[_id("request", n)].designation for n in range(1, 11)}
        assert first == {"713К.4.001"}
        second = requests[_id("request", 11)]
        assert (second.designation, second.project_reference, second.purpose) == ("776К.1.001", "776К.1", "Коммерческий проект")
        line = _line(session, 1)
        assert (line.buyer_name, line.buyer_id, line.calculated_unit, line.project_reference) == ("Виктор", VITYA, "шт", "713К.4")
        assert line.calculated_price_with_vat == Decimal("122.00") and line.calculated_sum == Decimal("1220.00")
        assert line.source_request_id == _id("request", 1) and line.link == "https://www.chipdip.ru/product/1"
        assert session.get(LineItemRow, line.id) is not None
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow).where(
            OutboxMessageRow.topic == "procurement.requested", OutboxMessageRow.published_at.is_(None))) == 0

        # Decided: the RB choice written into the line as the RFQ bridge writes it.
        decided = _line(session, 5)
        assert (decided.supplier, decided.actual_quantity) == ("АО «ЧИП И ДИП»", Decimal("100"))
        decision = session.get(DecisionRow, _id("decision", 1))
        assert (decision.unit_gross, decision.total_gross, decision.priced_quotes) == (Decimal("12"), Decimal("1200.00"), 2)
        assert decision.cheapest_unit_gross == Decimal("10") and decision.baseline_unit_gross == Decimal("10")
        assert (decision.not_cheapest_code, decision.not_cheapest_reason) == ("other", "Срок поставки")
        assert (decision.gate_override_code, decision.gate_override_reason) == ("urgent", "Срочно")
        assert decision.applied_at is not None

        # The request for quotes: lines, suppliers, a declined suggestion, quotes with the file, tiers in roubles.
        rfq = session.get(RfqRow, _id("rfq", 1))
        assert (rfq.number, rfq.title, rfq.buyer_id) == ("КП-2610-001", "Roller_Buyer № 1", VITYA)
        assert session.scalar(select(func.count()).select_from(RfqSupplierRow).where(RfqSupplierRow.rfq_id == rfq.id)) == 2
        assert session.scalar(select(func.count()).select_from(RfqDeclinedSuggestionRow)) == 1
        quote = session.get(QuoteRow, _id("quote", 1))
        assert (quote.source, quote.number, quote.supplier_is_key, quote.delivery_cost) == ("file", "КП-1", True, Decimal("500.00"))
        assert session.get(DocumentFileRow, quote.file_id).content == b"%PDF-1.4 quote one"
        tiered = session.get(QuoteLineRow, _id("quote-line", 2))
        assert (tiered.unit_price, tiered.moq, tiered.pack, tiered.tiers) == (
            Decimal("10"), Decimal("100"), Decimal("50"), [{"from": "500", "price": "9"}])
        # The withdrawn line: a removed line in the request it left, «Перезапрос».
        removed = session.get(RfqLineRow, _id("rfq-line-removed", f"{rfq.id}:2"))
        assert (removed.removed_reason, removed.procurement_line_id) == ("requote", _line(session, 2).id)

        # Orders: ERP's own numbers, the invoice file, the money with VAT, paid, the facts.
        shipped, waiting, to_pay = _line(session, 6), _line(session, 7), _line(session, 11)
        assert shipped.order_number and waiting.order_number and to_pay.order_number
        assert len({shipped.order_number, waiting.order_number, to_pay.order_number}) == 3
        assert (shipped.order_date, waiting.order_date, to_pay.order_date) == (
            date(2026, 10, 14), date(2026, 10, 14), date(2026, 10, 13))
        assert (shipped.invoice_number, shipped.invoice_date, shipped.paid_at is not None) == ("НФ-E14422", date(2026, 10, 14), True)
        assert shipped.actual_price_with_vat == Decimal("135.00") and shipped.actual_sum == Decimal("27000.00")
        assert shipped.invoice_url and shipped.expected_arrival == date(2026, 10, 28)
        assert to_pay.actual_price == Decimal("210.00") and to_pay.actual_price_with_vat == Decimal("252.00")
        assert to_pay.expected_arrival == date(2026, 10, 30) and to_pay.invoice_url
        assert waiting.invoice_url is None and waiting.actual_price_with_vat == Decimal("93.00")
        facts = session.get(OrderFactRow, shipped.order_number)
        assert (facts.sent_to_pay_on, facts.shipped_on, facts.shipment_note, facts.delivery_cost) == (
            date(2026, 10, 14), date(2026, 10, 16), "СДЭК 123", Decimal("3000.00"))
        assert session.get(OrderFactRow, waiting.order_number).delivery_cost == Decimal("440.00")
        # The promised date ERP derives from the audit is RB's, the expected one moved.
        promised = {row.id: row.promised for row in order_lines(session, order_numbers=[to_pay.order_number])}
        assert promised[to_pay.id] == date(2026, 10, 27)

        # Cancelled with RB's reason; «Давальческое» cancelled with its own; «Со склада» on the desk with a note.
        reasons = {row.aggregate_id: row.details["reason"] for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.cancelled"))}
        assert reasons == {_id("line", 8): "хз", _id("line", 10): TOLLING_REASON}
        assert "со склада" in _line(session, 9).note
        # Every line says where it came from.
        stamped = set(session.scalars(select(AuditEventRow.aggregate_id).where(AuditEventRow.event_type == IMPORTED)))
        assert stamped == {_id("line", n) for n in EXPECTED_RB}
        history = line_history(session, _id("line", 6))
        titles = [event["title"] for event in history["events"]]
        assert titles[0].startswith("Заявка SM-20261012-") and "Перенесено из Roller_Buyer" in titles
        assert {"Взята в работу", "Данные заполнены", "Запрос поставщику отправлен", "Счёт внесён", "Оплачено"} <= set(titles)
        steps = ["Включена в запрос КП", "КП получено", "Выбран поставщик", "Запрос поставщику отправлен", "Счёт внесён",
                 "Оплачено"]
        first = [next(i for i, title in enumerate(titles) if title.startswith(step)) for step in steps]
        assert first == sorted(first), titles  # RB's days become moments in RB's order
        moved = next(event for event in history["events"] if event["title"] == "Перенесено из Roller_Buyer")
        assert moved["details"] == "заявка 713К.4/1; там «В доставке»; здесь «В пути»"
        assert session.get(PurchasingSettingRow, "gate_min_quotes") is None  # RB's values equal ERP's defaults


def test_dry_run_writes_nothing_and_a_rerun_inserts_nothing(erp, snapshot):
    _reference(erp, snapshot)
    before = _counts(erp)
    dry = _flow(erp, snapshot, apply=False)
    assert _counts(erp) == before
    applied = _flow(erp, snapshot)
    assert {key: value for key, value in dry.tables.items()} == applied.tables
    after = _counts(erp)
    assert after["procurement_lines"] == before["procurement_lines"] + 11
    again = _flow(erp, snapshot, now=datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    assert _counts(erp) == after
    assert not again.counts and not again.skipped


def test_erp_change_wins_and_untouched_lines_follow_rb(erp, snapshot, tmp_path):
    _reference(erp, snapshot)
    _flow(erp, snapshot)
    # The buyer writes a note on line 3 in ERP: from now on ERP owns it.
    with erp() as session:
        line = _line(session, 3)
    set_procurement_note(SetNoteCommand(line_id=line.id, expected_version=line.version, note="уточнить у КБ",
                                        actor=BUYER, now=NOW), SqlAlchemyProcurementUnitOfWork(erp))
    # Meanwhile in RB: line 3 and line 4 got a quote and a decision, line 1 was cancelled.
    path, data = snapshot
    newer = tmp_path / "newer.db"
    shutil.copy(path, newer)
    db = sqlite3.connect(newer)
    db.execute("insert into quote_lines values (7, 1, 1, 500, 1, 22, null, null, 0, 0, 0, '', null, '[]')")
    db.execute("insert into decisions values (5, 1, 7, 'urgent', '', null, '', '', '2026-10-15')")
    db.execute("insert into decisions values (6, 2, 1, 'urgent', '', null, '', '', '2026-10-15')")
    db.execute("update request_lines set cancelled_at = '2026-10-16', cancel_reason = 'не нужно' where id = 1")
    db.commit()
    db.close()
    report = _flow(erp, (newer, data), now=datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    with erp() as session:
        assert _line(session, 3).status == "В работе" and _line(session, 3).note == "уточнить у КБ"
        assert session.get(DecisionRow, _id("decision", 5)) is None
        assert _line(session, 4).status == "Готов к запросу" and _line(session, 4).supplier == 'ООО "КРОКС ПЛЮС"'
        assert _line(session, 1).status == "Отменено"
    assert any("решение RB 5" in text for text in report.skipped)
    # And once more: nothing new.
    counts = _counts(erp)
    _flow(erp, (newer, data), now=datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))
    assert _counts(erp) == counts


@pytest.fixture()
def api(erp):
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(erp)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(erp)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=BUYER, email="vitya@example.test", roles=frozenset({"buyer"}), permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 21, 12, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.procurement_uow_factory
    del app.state.purchasing_uow_factory


def test_the_screens_read_the_rb_statuses_back(api, erp, snapshot):
    _reference(erp, snapshot)
    _flow(erp, snapshot)
    summary = api.get("/api/procurement/requests-view").json()
    assert {project["key"] for project in summary["projects"]} == {"713К.4", "776К.1"}
    shown: dict[str, str] = {}
    for batch in summary["batches"]:
        for row in api.get("/api/procurement/requests-view", params={"batch": batch["key"]}).json()["lines"]:
            shown[row["name"]] = row["rb_status"]
    assert shown == {f"Позиция {n}": status for n, status in EXPECTED_VIEW.items()}
    with erp() as session:
        numbers = {rb_id: _line(session, rb_id).order_number for rb_id in (6, 7, 11)}
    board = {order["order_number"]: order["column"] for order in api.get(
        "/api/purchasing/orders", params={"scope": "mine"}).json()["orders"]}
    assert board == {numbers[6]: "transit", numbers[7]: "awaiting", numbers[11]: "to_pay"}
