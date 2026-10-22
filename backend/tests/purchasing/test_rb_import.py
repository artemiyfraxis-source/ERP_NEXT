"""Roller_Buyer → ERP importer (app/modules/purchasing/rb_import.py) over a small Roller_Buyer-shaped snapshot."""

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.modules.purchasing.infrastructure import (
    DocumentFileRow,
    InvoiceFileRow,
    InvoiceRow,
    ItemAliasRow,
    ItemRow,
    PriceRefRow,
    SupplierRow,
    UpdDocumentRow,
    UpdInvoiceLinkRow,
    UpdLineRow,
)
from app.modules.purchasing.items import part_key
from app.modules.purchasing.rb_import import Importer, open_snapshot
from app.shared.persistence import create_schema

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

SCHEMA = """
create table suppliers (id integer primary key, name text, inn text, categories text, is_key integer, contacts text,
  site text, notes text, created_at text);
create table items (id integer primary key, name text, part_number text, manufacturer text, category text, unit text,
  type text, mass_ug integer, created_at text);
create table item_aliases (id integer primary key, supplier_id integer, key text, item_id integer);
create table price_refs (id integer primary key, item_id integer, supplier_id integer, supplier_name text,
  offered_part text, offered_manufacturer text, currency text, unit_price4 integer, vat_included integer, qty real,
  lead_time text, source text, source_row integer, imported_at text);
create table upd_documents (file_id text primary key, file_name text, path text, md5 text, google_status text,
  kind text, tag text, issues text, model text, number text, date text, seller_name text, seller_inn text,
  seller_kpp text, buyer_inn text, basis text, payment_doc text, total_net integer, total_vat integer,
  total_gross integer, file_amount integer, line_count integer, dup_of text, drive_url text, google_updated text,
  lines_synced_for text, pdf_file text, synced_at text, manual_header text, manual_saved_at text,
  manual_based_on text, verified_at text, verified_google_updated text, supplier_id integer, project_id integer,
  origin text, twin_of text);
create table upd_lines (id integer primary key, file_id text, source text, seq integer, name text, article text,
  product_type_code text, unit_code text, unit text, qty real, price real, net integer, excise text, vat_rate text,
  vat integer, gross integer, country_code text, country text, declaration text, is_service integer, issues text,
  fixes text);
create table invoices (id integer primary key, source text, external_id text, title text, board_title text,
  column_title text, status text, card_created text, card_updated text, number text, date text, seller_name text,
  seller_inn text, supplier_id integer, total integer, vat integer, vat_free integer, payment_numbers text,
  payment_date text, payment_sum integer, files text, parsed_file_id text, parse_issues text, synced_at text,
  text text, lines text);
create table upd_invoice_links (id integer primary key, upd_file_id text, invoice_id integer, decision text,
  decided_at text);
"""


def _upd(file_id: str, **values) -> dict:
    row = {
        "file_id": file_id, "file_name": f"{file_id}.pdf", "path": "/Входящие", "md5": "", "google_status": "Разобран",
        "kind": "УПД", "tag": "", "issues": "", "model": "", "number": "17", "date": "2026-09-01",
        "seller_name": 'ООО "Крокс Плюс"', "seller_inn": "3662192701", "seller_kpp": "", "buyer_inn": "5040191181",
        "basis": "", "payment_doc": "", "total_net": 10000, "total_vat": 2200, "total_gross": 12200, "file_amount": None,
        "line_count": 2, "dup_of": None, "drive_url": "", "google_updated": "2026-09-02T10:00:00", "lines_synced_for": None,
        "pdf_file": None, "synced_at": "2026-09-24T10:10:00", "manual_header": None, "manual_saved_at": None,
        "manual_based_on": None, "verified_at": None, "verified_google_updated": None, "supplier_id": 1,
        "project_id": None, "origin": "google", "twin_of": None,
    }
    row.update(values)
    return row


def _insert(db: sqlite3.Connection, table: str, row: dict) -> None:
    db.execute(f"insert into {table} ({','.join(row)}) values ({','.join('?' * len(row))})", list(row.values()))


@pytest.fixture()
def snapshot(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    (data / "upd").mkdir(parents=True)
    (data / "edo").mkdir()
    (data / "kaiten" / "55").mkdir(parents=True)
    (data / "upd" / "g1.pdf").write_bytes(b"%PDF-1.4 upd one")
    (data / "upd" / "g2.pdf").write_bytes(b"%PDF-1.4 upd one")  # the same bytes: one stored file
    (data / "edo" / "edo-1.xml").write_bytes("<Файл ИдФайл='1'/>".encode("utf-8"))
    (data / "kaiten" / "55" / "7.pdf").write_bytes(b"%PDF-1.4 invoice")
    (tmp_path / "secret.pdf").write_bytes(b"outside")

    path = tmp_path / "copy.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("insert into suppliers values (1, 'ООО \"КРОКС ПЛЮС\"', '3662192701', '[\"connector\"]', 1, 'Иван, +7 900', '', '', '')")
    db.execute("insert into suppliers values (54, 'ООО \"ЭЗП \"ФРАКСИС\"', '5040191181', '[]', 0, '', '', '', '')")
    db.execute("insert into items values (1, 'Разъём SHR-02V-S-B', 'SHR-02V-S-B', 'JST', 'connector', 'pcs', 'Разъёмы', null, '2026-09-25 06:20:35')")
    db.execute("insert into items values (2, 'Провод МГТФ 0,35', '', '', 'cable', 'm', '', null, '2026-09-25 06:20:35')")
    db.execute("insert into item_aliases values (1, 1, 'корпус shr 2', 1)")
    db.execute("insert into price_refs values (1, 1, 1, 'Крокс', 'SHR-02V-S-B', 'JST', 'CNY', 177, 0, 1000, '3 недели', 'Прайс.xlsx', 2, '2026-09-25T08:19:49.597Z')")
    db.execute("insert into price_refs values (2, 2, null, 'Склад', '', '', 'RUB', 45000, 1, null, '', 'Прайс.xlsx', 3, '2026-09-25T08:19:49.597Z')")
    _insert(db, "upd_documents", _upd("g1", pdf_file="g1.pdf"))
    _insert(db, "upd_documents", _upd("g2", pdf_file="g2.pdf", number="18", twin_of=None))
    _insert(db, "upd_documents", _upd("edo-1", origin="edo", path="edo/edo-1.xml", file_name="ON_NSCHFDOPPR.xml", supplier_id=54))
    _insert(db, "upd_documents", _upd("bad", pdf_file="../secret.pdf", supplier_id=None))
    db.execute("update upd_documents set twin_of = 'edo-1' where file_id = 'g1'")
    for line_id, seq, name in ((1, 1, "Разъём SHR-02V-S-B"), (2, 1, "Доставка"), (3, 2, "Провод")):
        db.execute(
            "insert into upd_lines values (?, 'g1', 'google', ?, ?, 'SHR-02V-S-B', '', '796', 'шт', 10, 10.0, 10000, '', '22', 2200, 12200, '', '', '', 0, '', '')",
            (line_id, seq, name),
        )
    files = [
        {"id": "7", "name": "Счёт № 1 от 01.09.2026.pdf", "kind": "invoice", "size": 16, "path": "kaiten\\55\\7.pdf"},
        {"id": "8", "name": "Платёжное поручение № 885.pdf", "kind": "payment", "size": 5, "path": "kaiten\\55\\8.pdf"},
    ]
    lines = [{"seq": 1, "name": "Разъём SHR-02V-S-B", "article": "", "qty": 10, "unit": "шт", "price": 1220, "sum": 12200}]
    db.execute(
        "insert into invoices values (1, 'kaiten', '55', 'Счёт 1 Крокс', 'Закупки', 'Оплачено', 'paid', "
        "'2025-09-17T11:55:33.558Z', '2025-09-23T11:29:37.584Z', '1', '2026-09-01', 'ООО \"Крокс Плюс\"', '3662192701', 1, "
        "12200, 2200, 0, '885', '2026-09-02', 12200, ?, '7#3', '', '2026-10-09T13:35:31.052Z', 'Счёт № 1', ?)",
        (json.dumps(files), json.dumps(lines)),
    )
    db.execute("insert into upd_invoice_links values (1, 'edo-1', 1, 'confirmed', '2026-10-09T13:35:31.052Z')")
    db.commit()
    db.close()
    return path, data


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _run(sessions, snapshot):
    path, data = snapshot
    with sessions() as session:
        report = Importer(session, open_snapshot(path), data, NOW).run()
        session.commit()
    return report


def test_imports_directories_documents_and_invoices(sessions, snapshot):
    with sessions() as session:  # an item already in ERP keeps its row: the Roller_Buyer item joins it
        session.add(ItemRow(id=uuid4(), name="Корпус SHR", part_number="SHR02VSB",
                            part_key=part_key("SHR02VSB"), category="connector", unit="шт", version=1, created_at=NOW))
        session.commit()
    report = _run(sessions, snapshot)
    with sessions() as session:
        suppliers = session.scalars(select(SupplierRow)).all()
        assert [(s.inn, s.categories, s.is_key) for s in suppliers] == [("3662192701", ["connector"], True)], "our INN never"
        assert "Иван, +7 900" in suppliers[0].notes
        items = {i.name: i for i in session.scalars(select(ItemRow))}
        assert set(items) == {"Корпус SHR", "Провод МГТФ 0,35"} and items["Провод МГТФ 0,35"].unit == "м"
        alias = session.scalars(select(ItemAliasRow)).one()
        assert alias.item_id == items["Корпус SHR"].id and alias.supplier_id == suppliers[0].id
        prices = session.scalars(select(PriceRefRow).order_by(PriceRefRow.source_row)).all()
        assert [(p.currency, p.unit_price4, p.supplier_id) for p in prices] == [("CNY", 177, suppliers[0].id), ("RUB", 45000, None)]

        docs = {d.id: d for d in session.scalars(select(UpdDocumentRow))}
        assert docs["g1"].twin_of == "edo-1" and docs["g1"].doc_date == date(2026, 9, 1)
        # Roller_Buyer linked it to the «supplier» with our INN: that one is not imported, the relink takes the seller's.
        assert docs["edo-1"].supplier_id == suppliers[0].id
        assert docs["edo-1"].folder == "edo" and docs["g2"].supplier_id == suppliers[0].id
        assert docs["bad"].file_id is None, "a path out of DATA_DIR is not read"
        assert docs["g1"].file_id == docs["g2"].file_id, "the same content is stored once"
        assert session.scalar(select(func.count()).select_from(DocumentFileRow)) == 3

        lines = session.scalars(select(UpdLineRow).order_by(UpdLineRow.seq)).all()
        assert [(l.seq, l.name) for l in lines] == [(1, "Разъём SHR-02V-S-B"), (2, "Доставка"), (3, "Провод")]
        assert lines[0].article_key == "SHR02VSB" and lines[0].gross == 12200

        invoice = session.scalars(select(InvoiceRow)).one()
        assert invoice.status == "paid" and invoice.lines[0]["sum"] == 12200 and invoice.text == "Счёт № 1"
        assert invoice.payment_date == date(2026, 9, 2) and invoice.supplier_id == suppliers[0].id
        files = session.scalars(select(InvoiceFileRow).order_by(InvoiceFileRow.seq)).all()
        assert [(f.kind, f.file_id is not None) for f in files] == [("invoice", True), ("payment", False)]
        link = session.scalars(select(UpdInvoiceLinkRow)).one()
        assert (link.upd_id, link.invoice_id, link.decision) == ("edo-1", invoice.id, "confirmed")
    assert report.counts["УПД: документы с перенумерованными строками"] == 1
    assert report.counts["поставщики: пропущены (наш ИНН)"] == 1


def test_a_second_run_changes_nothing_and_keeps_the_buyers_word_given_in_erp(sessions, snapshot):
    _run(sessions, snapshot)
    with sessions() as session:
        doc = session.get(UpdDocumentRow, "g2")
        doc.manual_header = {"number": "18/1"}
        doc.verified_at = NOW
        session.commit()
    before = {}
    with sessions() as session:
        for table in (SupplierRow, ItemRow, ItemAliasRow, PriceRefRow, UpdDocumentRow, UpdLineRow, InvoiceRow,
                      InvoiceFileRow, DocumentFileRow, UpdInvoiceLinkRow):
            before[table.__tablename__] = session.scalar(select(func.count()).select_from(table))
    _run(sessions, snapshot)
    with sessions() as session:
        after = {table: session.scalar(select(func.count()).select_from(UpdDocumentRow.metadata.tables[table]))
                 for table in before}
        assert after == before
        doc = session.get(UpdDocumentRow, "g2")
        assert doc.manual_header == {"number": "18/1"} and doc.verified_at is not None


def test_the_live_database_is_refused(tmp_path):
    live = tmp_path / "Roller_Buyer-data" / "buyer.db"
    live.parent.mkdir()
    sqlite3.connect(live).close()
    with pytest.raises(SystemExit, match="живая база"):
        open_snapshot(live)


def test_nul_bytes_in_the_snapshot_never_reach_the_database(sessions, snapshot):
    """PostgreSQL refuses NUL in text; SQLite (Roller_Buyer's own) keeps it — every snapshot text is cleaned at the read."""
    path, _ = snapshot
    nul = chr(0)
    lines = json.dumps([{"seq": 1, "name": "Разъём@", "qty": 1}], ensure_ascii=False).replace("@", "\\u0000")
    db = sqlite3.connect(path)
    db.execute("update invoices set text = ?, parse_issues = ?, lines = ?",
               (f"Счёт{nul} № 1{nul}", f"нет{nul}ИНН", lines))
    db.execute("update upd_documents set issues = ? where file_id = 'g2'", (f"ошибка{nul}разбора",))
    db.execute("update items set name = ? where id = 2", (f"Провод{nul} МГТФ 0,35",))
    db.commit()
    db.close()
    _run(sessions, snapshot)
    with sessions() as session:
        invoice = session.scalars(select(InvoiceRow)).one()
        assert invoice.text == "Счёт № 1" and invoice.parse_issues == "нетИНН"
        assert nul not in json.dumps(invoice.lines, ensure_ascii=False) and "u0000" not in json.dumps(invoice.lines)
        assert session.get(UpdDocumentRow, "g2").issues == "ошибкаразбора"
        assert "Провод МГТФ 0,35" in {item.name for item in session.scalars(select(ItemRow))}
