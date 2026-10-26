"""«Поставка» → «Реестр УПД» (owner 26.10.2026, relaying Витя): the УПД of every delivery the warehouse takes in lands in
the purchasing registry once a line is accepted — a paper scan as origin «scan» (header from the delivery, lines from
the model's reading), an ЭДО file as an ordinary «edo» document. Idempotent, the buyer's word stays, a copy of a
document the registry already holds becomes its twin, a failing push never costs the lot, and the backfill brings the
deliveries taken in before."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.modules.purchasing import upd_registry as repo
from app.modules.purchasing import upd_scans
from app.modules.purchasing.edo_xml import edo_document_id, parse_edo_xml
from app.modules.purchasing.infrastructure import (
    DocumentFileRow,
    SqlAlchemyPurchasingUnitOfWork,
    UpdDocumentRow,
    UpdLineRow,
)
from app.modules.warehouse.infrastructure import (
    SqlAlchemyWarehouseUnitOfWork,
    StockLotRow,
    WarehouseReceiptFileRow,
)
from app.modules.warehouse.receipt_scan import RecognisedUpd
from app.modules.warehouse.scan_reading import ScanReading, reading_json
from app.modules.warehouse.upd_lines import UpdLine
from app.shared.persistence import AuditEventRow
from app.workflows import receipt_upd_registry as flow
from app.modules.warehouse import receipts_api
from tests.factories import Actor
from tests.warehouse.test_receipts import HEADER, LINE, NOW, stand  # noqa: F401 — fixture

FIXTURES = Path(__file__).parents[1] / "purchasing" / "fixtures" / "edo"
UPD_XML = (FIXTURES / "upd-503.xml").read_bytes()
EDO_ID = edo_document_id(parse_edo_xml(UPD_XML.decode("cp1251"), "upd.xml").id_file)
PDF = b"%PDF-1.4\n% scan of a paper UPD\n"
READING = ScanReading(
    RecognisedUpd(supplier="ООО «Ромашка Электро»", inn="7701234567", upd_number="123", upd_date=date(2026, 10, 20),
                  invoice_number="СЧ-45", invoice_date=date(2026, 10, 1)),
    (
        UpdLine(article="CON-001", name="Разъём CON-001", quantity=Decimal("10"), unit="шт",
                invoice_quantity=Decimal("10"), invoice_unit="шт", supplier_code="RS-1", number="1"),
        UpdLine(article="", name="Провод 0,5 мм2", quantity=Decimal("300"), unit="м", invoice_quantity=Decimal("3"),
                invoice_unit="рул", supplier_code="W-05", number="2"),
    ),
    engine="gpt-test",
)


def _file(sessions, content: bytes = PDF, kind: str = "application/pdf", name: str = "УПД.pdf",
          reading: ScanReading | None = READING) -> str:
    file_id = uuid4()
    with sessions() as session:
        session.add(WarehouseReceiptFileRow(
            id=file_id, file_name=name, content_type=kind, size=len(content), content=content, uploaded_by="Кладовщик",
            uploaded_at=NOW, recognition=reading_json(reading) if reading is not None else "",
        ))
        session.commit()
    return f"/api/stock/receipts/files/{file_id}"


def _receipt(client, **fields) -> dict:
    response = client.post("/api/stock/receipts", json={**HEADER, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def _accept(client, receipt_id, **fields):
    response = client.post(f"/api/stock/receipts/{receipt_id}/accept", json={**LINE, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def _doc(sessions, document_id: str) -> UpdDocumentRow | None:
    with sessions() as session:
        return session.get(UpdDocumentRow, document_id)


def _lines(sessions, document_id: str, source: str = "google") -> list[UpdLineRow]:
    with sessions() as session:
        return list(session.scalars(select(UpdLineRow).where(
            UpdLineRow.document_id == document_id, UpdLineRow.source == source).order_by(UpdLineRow.seq)))


def _imports(sessions) -> int:
    with sessions() as session:
        return len(list(session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "purchasing.upd_scan_imported"))))


def _scan_id(link: str) -> str:
    return f"wh-{link.rsplit('/', 1)[1]}"


def test_migration_lets_the_scan_origin_in_and_its_downgrade_removes_only_scans(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "0153_upd_scan_origin")
    insert = ("INSERT INTO purchasing_upd_documents (id, origin, file_name, folder, md5, status, kind, tag, issues, model, "
              "number, seller_name, seller_inn, seller_kpp, buyer_inn, basis, payment_doc, line_count, drive_url, "
              "parser_updated, synced_at, created_at) VALUES (?, ?, 'f', '', '', 'Разобран', 'УПД', '', '', '', '1', '', "
              "'', '', '', '', '', 0, '', '', '2026-10-26', '2026-10-26')")
    with sqlite3.connect(path) as connection:
        connection.execute(insert, ("wh-1", "scan"))
        connection.execute(insert, ("edo-1", "edo"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("x-1", "paper"))
    command.downgrade(config, "0151_upd_llm_recognition")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id FROM purchasing_upd_documents").fetchall() == [("edo-1",)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("wh-2", "scan"))


def test_the_delivery_prd_goes_into_the_registry_as_the_payment_document(stand):  # noqa: F811
    client, sessions = stand
    link = _file(sessions)
    receipt = _receipt(client, scan_file=link, prd_number="916", prd_date="2026-10-19")
    _accept(client, receipt["id"])
    assert _doc(sessions, _scan_id(link)).payment_doc == "№ 916 от 19.10.2026"
    plain = _file(sessions)
    _accept(client, _receipt(client, scan_file=plain)["id"])
    assert _doc(sessions, _scan_id(plain)).payment_doc == ""


def test_an_accepted_scan_lands_in_the_registry_as_a_scan_document_needing_a_check(stand):  # noqa: F811
    client, sessions = stand
    link = _file(sessions)
    receipt = _receipt(client, scan_file=link)
    _accept(client, receipt["id"])
    doc = _doc(sessions, _scan_id(link))
    assert doc is not None and doc.origin == "scan" and doc.kind == "УПД"
    assert (doc.number, doc.doc_date, doc.seller_name, doc.seller_inn) == (
        "123", date(2026, 10, 20), "ООО «Ромашка Электро»", "7701234567")
    assert doc.basis == "Счёт № СЧ-45 от 01.10.2026" and doc.folder == "Склад ЦАР" and doc.model == "Склад: gpt-test"
    assert doc.status == "Требует проверки" and doc.line_count == 2
    assert [(line.seq, line.name, line.article, line.quantity, line.unit) for line in _lines(sessions, doc.id)] == [
        (1, "Разъём CON-001", "CON-001", Decimal("10"), "шт"), (2, "Провод 0,5 мм2", "W-05", Decimal("3"), "рул"),
    ]
    with sessions() as session:
        assert session.get(DocumentFileRow, doc.file_id).content == PDF


def test_the_registry_lists_scans_apart_under_needs_review(stand):  # noqa: F811
    client, sessions = stand
    link = _file(sessions)
    _accept(client, _receipt(client, scan_file=link)["id"])
    with sessions() as session:
        listing = repo.list_documents(session, repo.ListFilter(origin="scan"))
    assert listing["origins"]["scan"] == 1 and listing["total"] == 1
    [card] = listing["items"]
    assert card["origin"] == "scan" and card["status"] == "needs_review"
    with sessions() as session:
        assert repo.list_documents(session, repo.ListFilter(review=True))["total"] == 1


def test_a_second_accept_writes_nothing_and_a_changed_header_updates_the_document(stand):  # noqa: F811
    client, sessions = stand
    link = _file(sessions)
    receipt = _receipt(client, scan_file=link)
    _accept(client, receipt["id"])
    before = _doc(sessions, _scan_id(link)).parser_updated
    _accept(client, receipt["id"], article="CON-002")
    assert _doc(sessions, _scan_id(link)).parser_updated == before and _imports(sessions) == 1
    edited = client.put(f"/api/stock/receipts/{receipt['id']}",
                        json={**HEADER, "scan_file": link, "upd_number": "123/1", "version": receipt["version"]})
    assert edited.status_code == 200, edited.text
    _accept(client, receipt["id"], article="CON-003")
    doc = _doc(sessions, _scan_id(link))
    assert doc.number == "123/1" and doc.parser_updated != before and _imports(sessions) == 2
    assert len(_lines(sessions, doc.id)) == 2


def test_the_buyers_saved_version_is_never_overwritten_and_the_change_is_flagged(stand):  # noqa: F811
    client, sessions = stand
    link = _file(sessions)
    receipt = _receipt(client, scan_file=link)
    _accept(client, receipt["id"])
    document_id = _scan_id(link)
    header = {"number": "123", "date": "2026-10-20", "seller_name": "ООО Ромашка", "seller_inn": "7701234567",
              "seller_kpp": "", "buyer_inn": "", "total_net": 100_00, "total_vat": 22_00, "total_gross": 122_00}
    uow = SqlAlchemyPurchasingUnitOfWork(sessions)
    with uow:
        repo.save_manual(uow, document_id, header, [{"seq": 1, "name": "Разъём", "quantity": "10", "price": "10"}],
                         _doc(sessions, document_id).parser_updated, None, Actor(name="Витя", department="Закупки"), NOW)
        uow.commit()
    client.put(f"/api/stock/receipts/{receipt['id']}",
               json={**HEADER, "scan_file": link, "upd_number": "124", "version": receipt["version"]})
    _accept(client, receipt["id"], article="CON-002")
    doc = _doc(sessions, document_id)
    assert doc.manual_header["number"] == "123" and doc.manual_header["total_gross"] == 122_00
    assert [line.name for line in _lines(sessions, document_id, "manual")] == ["Разъём"]
    assert doc.number == "124"  # the parser's side follows the delivery
    with sessions() as session:
        card = repo.document_detail(session, document_id)
    assert card["manual"] is True and card["changed_in_parser"] is True and card["number"] == "123"


def test_an_edo_file_of_a_delivery_lands_as_an_edo_document_once(stand):  # noqa: F811
    client, sessions = stand
    link = _file(sessions, UPD_XML, "application/xml", "upd.xml", reading=None)
    receipt = _receipt(client, scan_file=link, supplier='ООО "Ромашка Электро"', upd_number="РЭ-0915",
                       upd_date="2026-09-15")
    _accept(client, receipt["id"])
    doc = _doc(sessions, EDO_ID)
    assert doc is not None and doc.origin == "edo" and doc.number == "РЭ-0915"
    assert _doc(sessions, _scan_id(link)) is None
    result = flow.push_receipt(lambda: SqlAlchemyWarehouseUnitOfWork(sessions),
                               lambda: SqlAlchemyPurchasingUnitOfWork(sessions), UUID(receipt["id"]),
                               Actor(name="Кладовщик", department="Склад"), NOW)
    assert result["status"] == "unchanged" and result["origin"] == "edo" and result["document_id"] == EDO_ID


def test_a_scan_of_a_upd_the_registry_holds_from_edo_becomes_its_twin(stand):  # noqa: F811
    client, sessions = stand
    uow = SqlAlchemyPurchasingUnitOfWork(sessions)
    with uow:
        repo.import_edo(uow, [("upd.xml", UPD_XML)], Actor(name="Витя", department="Закупки"), NOW)
        uow.commit()
    reading = ScanReading(RecognisedUpd(supplier='ООО "Ромашка Электро"', inn="7701000019", upd_number="РЭ-0915",
                                        upd_date=date(2026, 9, 15)))
    link = _file(sessions, reading=reading)
    receipt = _receipt(client, scan_file=link, supplier='ООО "Ромашка Электро"', upd_number="РЭ-0915",
                       upd_date="2026-09-15")
    _accept(client, receipt["id"])
    scan = _doc(sessions, _scan_id(link))
    assert scan.seller_inn == "7701000019" and scan.twin_of == EDO_ID
    assert _doc(sessions, EDO_ID).twin_of is None


def test_the_inn_is_not_taken_from_a_scan_naming_another_seller():
    receipt = {"supplier": "ООО «Василёк»", "warehouse": "ЖУК", "upd_number": "7", "upd_date": None,
               "invoice_number": "", "invoice_date": None}
    file = flow.ReceiptFile(uuid4(), "УПД.pdf", "application/pdf", "")
    doc = flow.scan_document(receipt, file, READING)
    assert doc.seller_inn == "" and doc.basis == "" and doc.folder == "Склад ЖУК"
    assert flow.scan_document({**receipt, "supplier": "Ромашка Электро"}, file, READING).seller_inn == "7701234567"
    assert flow.scan_document(receipt, file, None).lines == ()


def test_a_failing_push_never_costs_the_accepted_lot(stand, monkeypatch):  # noqa: F811
    client, sessions = stand

    def broken(*args, **kwargs):
        raise RuntimeError("реестр недоступен")

    monkeypatch.setattr(upd_scans, "import_scan", broken)
    link = _file(sessions)
    receipt = _receipt(client, scan_file=link)
    body = _accept(client, receipt["id"])
    assert body["lot"]["article"] == "CON-001"
    with sessions() as session:
        assert session.scalar(select(StockLotRow).where(StockLotRow.receipt_id == UUID(receipt["id"]))) is not None
    assert _doc(sessions, _scan_id(link)) is None
    monkeypatch.undo()
    _accept(client, receipt["id"], article="CON-002")  # the next accept retries
    assert _doc(sessions, _scan_id(link)) is not None


def test_a_delivery_without_a_file_or_without_accepted_lines_is_skipped(stand):  # noqa: F811
    client, sessions = stand
    _accept(client, _receipt(client)["id"])
    with sessions() as session:
        assert session.scalar(select(UpdDocumentRow)) is None
    link = _file(sessions)
    receipt = _receipt(client, scan_file=link)
    result = flow.push_receipt(lambda: SqlAlchemyWarehouseUnitOfWork(sessions),
                               lambda: SqlAlchemyPurchasingUnitOfWork(sessions), UUID(receipt["id"]),
                               Actor(name="Кладовщик", department="Склад"), NOW)
    assert result["status"] == "skipped" and result["reason"] == "ни одна строка поставки не принята"


def test_backfill_dry_run_writes_nothing_apply_pushes_each_delivery_once(stand, monkeypatch):  # noqa: F811
    client, sessions = stand
    monkeypatch.setattr(receipts_api, "push_after_accept", lambda *args, **kwargs: None)  # deliveries taken in before
    scan = _file(sessions)
    edo = _file(sessions, UPD_XML, "application/xml", "upd.xml", reading=None)
    _accept(client, _receipt(client, scan_file=scan)["id"])
    _accept(client, _receipt(client, scan_file=edo)["id"])
    _receipt(client, scan_file=_file(sessions))  # no line accepted
    _accept(client, _receipt(client)["id"])  # no file
    with sessions() as session:
        assert session.scalar(select(UpdDocumentRow)) is None
    warehouse = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)  # noqa: E731
    purchasing = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)  # noqa: E731
    actor = Actor(name="перенос", department="")
    dry = flow.backfill(warehouse, purchasing, actor, NOW, apply=False)
    assert sorted(item["status"] for item in dry) == ["created", "created"]
    with sessions() as session:
        assert session.scalar(select(UpdDocumentRow)) is None
    applied = flow.backfill(warehouse, purchasing, actor, NOW, apply=True)
    assert sorted((item["origin"], item["status"]) for item in applied) == [("edo", "created"), ("scan", "created")]
    assert _doc(sessions, _scan_id(scan)) is not None and _doc(sessions, EDO_ID) is not None
    again = flow.backfill(warehouse, purchasing, actor, NOW, apply=True)
    assert sorted(item["status"] for item in again) == ["unchanged", "unchanged"]
    assert "Итого 2 — unchanged: 2." in flow.format_report(again, applied=True)
