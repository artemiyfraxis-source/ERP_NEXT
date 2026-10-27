"""Счёт, загруженный к заказу в закупке: полный разбор и проверки в ответе, сам счёт — в реестре «Счета» закупок
(источник procurement), откуда его видит цепочка документов. Реестр не ломает загрузку; миграция 0155 пускает новый
источник и при откате убирает только его."""

from __future__ import annotations

import base64
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.main import app
from app.modules.purchasing import suppliers
from app.modules.purchasing.doc_chain import load_world
from app.modules.purchasing.infrastructure import (
    DocumentFileRow,
    InvoiceFileRow,
    InvoiceRow,
    SqlAlchemyPurchasingUnitOfWork,
)
from app.modules.purchasing.invoices import invoice_title
from app.shared.persistence import AuditEventRow
from app.workflows import procurement_invoice_registry as flow
from tests.factories import Actor
from tests.procurement.test_api import (  # noqa: F401 — fixtures
    awaiting_invoice, procurement_api, rates,
)
from tests.shared.test_invoice_reading import cells_pdf

INVOICE_PDF = cells_pdf([
    (30, 800, 'ООО "Поставка"'), (30, 790, "Получатель"),
    (30, 760, "Счет на оплату № 12 от 20 октября 2026 г."),
    (30, 740, 'Поставщик (Исполнитель): ООО "Поставка", ИНН 7701234560, КПП 770101001, Москва'),
    (30, 728, 'Покупатель (Заказчик): ООО "ЗЭП "ФРАКСИС", ИНН 5040191181'),
    (30, 700, "№"), (60, 700, "Товары (работы, услуги)"), (300, 700, "Кол-во"), (340, 700, "Ед."),
    (380, 700, "Цена"), (450, 700, "Сумма"),
    (32, 685, "1"), (60, 685, "Разъём SMA"), (300, 685, "2"), (340, 685, "шт"), (380, 685, "122,00"),
    (450, 685, "244,00"),
    (380, 650, "Итого:"), (450, 650, "244,00"),
    (300, 638, "В том числе НДС 22%:"), (450, 638, "44,00"),
    (30, 620, "Двести сорок четыре рубля 00 копеек"),
])


@pytest.fixture()
def wired(procurement_api, rates):  # noqa: F811
    client, line, sessions = procurement_api
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    yield client, line, sessions
    del app.state.purchasing_uow_factory


def _invoiced_line(client, line) -> str:
    """The line waits for the invoice with its money typed; returns its supplier order №."""
    version = awaiting_invoice(client, line)
    saved = client.patch(f"/api/procurement/{line.id}/money", json={
        "version": version, "currency": "RUB", "price": "100", "vat_rate": "22", "invoice_item_name": "Разъём SMA",
    })
    return saved.json()["line"]["order_number"]


def _upload(client, line_id, content=INVOICE_PDF, name="счёт.pdf"):
    return client.post("/api/procurement/invoice-files", json={
        "line_id": str(line_id), "file_name": name, "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
    })


def _registry(sessions) -> list[InvoiceRow]:
    with sessions() as session:
        return list(session.scalars(select(InvoiceRow).where(InvoiceRow.source == "procurement")))


def test_upload_answers_with_the_whole_reading_and_checks_against_the_order(procurement_api, rates):  # noqa: F811
    client, line, _ = procurement_api
    _invoiced_line(client, line)

    body = _upload(client, line.id).json()

    assert (body["invoice_number"], body["invoice_date"], body["hint"]) == ("12", "2026-10-20", "")
    reading = body["reading"]
    assert {key: reading[key] for key in ("number", "date", "seller_name", "seller_inn", "total", "vat", "vat_free")} == {
        "number": "12", "date": "2026-10-20", "seller_name": 'ООО "Поставка"', "seller_inn": "7701234560",
        "total": 24400, "vat": 4400, "vat_free": False,
    }
    assert reading["lines"] == [
        {"seq": 1, "name": "Разъём SMA", "article": "", "qty": 2, "unit": "шт", "price": 12200, "sum": 24400},
    ]
    assert (reading["issues"], reading["scan"]) == ([], False)
    assert body["checks"] == [
        {"kind": "seller", "level": "ok",
         "text": 'Продавец ООО "Поставка" — поставщик заказа (ИНН 7701234560 в справочнике не записан)'},
        {"kind": "total", "level": "ok", "text": "Сумма счёта 244,00 ₽ (НДС 44,00 ₽) = сумме строк заказа"},
        {"kind": "lines", "level": "ok", "text": "Позиций в счёте: 1; все строки заказа (1) нашлись в счёте"},
    ]


def test_a_scan_answers_with_an_empty_reading_and_no_checks(procurement_api, rates):  # noqa: F811
    client, line, _ = procurement_api
    awaiting_invoice(client, line)

    body = _upload(client, line.id, content=b"\xff\xd8\xff\xe0 scan", name="scan.jpg").json()

    assert body["reading"]["scan"] is True and body["reading"]["lines"] == [] and body["checks"] == []
    assert "впишите вручную" in body["hint"]


def test_uploaded_invoice_lands_in_the_registry_with_header_lines_text_and_file(wired):
    client, line, sessions = wired
    with sessions() as session:
        supplier = suppliers.create(session, {"name": "ООО Поставка", "inn": "7701234560"}, None, datetime(2026, 10, 1))
        session.commit()
    _invoiced_line(client, line)

    body = _upload(client, line.id).json()

    file_id = body["url"].rsplit("/", 1)[1]
    (row,) = _registry(sessions)
    assert (row.external_id, row.status, row.number, row.doc_date) == (file_id, "to_pay", "12", date(2026, 10, 20))
    assert (row.seller_name, row.seller_inn, row.total, row.vat, row.supplier_id) == (
        'ООО "Поставка"', "7701234560", 24400, 4400, supplier.id)
    assert row.lines == body["reading"]["lines"] and row.parse_issues == ""
    assert row.title.startswith("Заказ PO-") and row.title.endswith(body["file_name"])
    assert invoice_title(row) == "Счёт № 12 от 20.10.2026"
    with sessions() as session:
        assert "Счет на оплату № 12" in session.get(InvoiceRow, row.id).text
        (file_row,) = session.scalars(select(InvoiceFileRow).where(InvoiceFileRow.invoice_id == row.id))
        assert (file_row.kind, file_row.name) == ("invoice", body["file_name"])
        assert session.get(DocumentFileRow, file_row.file_id).content == INVOICE_PDF
        world, _ = load_world(session, date(2026, 10, 27))
        assert world.invoices[row.id].total == 24400 and world.chain_of_invoice[row.id].state == "unpaid"
        events = session.scalars(select(AuditEventRow.event_type).where(AuditEventRow.aggregate_id == row.id)).all()
    assert events == ["purchasing.invoice_upload_created"]
    # the directory knows the order's supplier by INN: the seller check says so
    assert body["checks"][0] == {"kind": "seller", "level": "ok", "text": "ИНН 7701234560 — поставщик заказа «ООО Поставка»"}


def test_the_same_file_again_changes_nothing_and_a_new_upload_of_it_joins_its_row(wired):
    client, line, sessions = wired
    number = _invoiced_line(client, line)
    first = _upload(client, line.id).json()
    (row,) = _registry(sessions)
    actor = Actor(name="Петров", department="Закупки")
    with sessions() as session:
        uploaded_at = session.get(InvoiceRow, row.id).card_created

    again = flow.push_uploaded_invoice(
        app.state.purchasing_uow_factory, UUID(row.external_id), first["file_name"], uploaded_at, INVOICE_PDF, None,
        number, "ООО Поставка", actor, datetime(2026, 10, 27, 10, 0),
    )
    assert again == {"file": row.external_id, "status": "unchanged", "invoice_id": str(row.id), "source": "procurement"}
    _upload(client, line.id)  # the same invoice uploaded again: one invoice, one row (owner's session 27.10)
    assert [item.id for item in _registry(sessions)] == [row.id]


def test_a_failing_registry_never_fails_the_upload(wired, monkeypatch):
    client, line, sessions = wired
    _invoiced_line(client, line)

    def broken(*args, **kwargs):
        raise RuntimeError("purchasing is down")

    monkeypatch.setattr(flow, "record_uploaded_invoice", broken)
    uploaded = _upload(client, line.id)

    assert uploaded.status_code == 200 and uploaded.json()["reading"]["number"] == "12"
    assert _registry(sessions) == []


def test_an_invoice_dropped_on_the_order_lands_in_the_registry_too(wired):
    client, line, sessions = wired
    number = _invoiced_line(client, line)

    recognised = client.post(f"/api/procurement/orders/{number}/recognize", json={
        "kind": "invoice", "file_name": "счёт.pdf", "content_type": "application/pdf",
        "content_base64": base64.b64encode(INVOICE_PDF).decode(),
    })

    assert recognised.status_code == 200, recognised.text
    (row,) = _registry(sessions)
    assert (row.number, row.total, row.title.startswith(f"Заказ {number}")) == ("12", 24400, True)


def test_migration_lets_the_procurement_source_in_and_its_downgrade_removes_only_it(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "0155_invoice_procurement_source")
    insert = ("INSERT INTO purchasing_invoices (id, source, external_id, title, board_title, column_title, status, "
              "card_created, card_updated, vat_free, payment_numbers, parse_issues, text, lines, synced_at) "
              "VALUES (?, ?, ?, 't', '', '', 'to_pay', '2026-10-27', '2026-10-27', 0, '', '', '', '[]', '2026-10-27')")
    with sqlite3.connect(path) as connection:
        connection.execute(insert, ("00000000000000000000000000000001", "procurement", "f-1"))
        connection.execute(insert, ("00000000000000000000000000000002", "kaiten", "card-1"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("00000000000000000000000000000003", "mail", "m-1"))
    command.downgrade(config, "0154_user_ui_settings")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT source FROM purchasing_invoices").fetchall() == [("kaiten",)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("00000000000000000000000000000004", "procurement", "f-2"))
