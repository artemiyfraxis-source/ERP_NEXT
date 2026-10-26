"""The documents of a lot (owner 26.10.2026: «склад увеличиваем: там должно быть, но скрыто — наименование по счёту,
наименование по УПД, даты счёта, даты УПД, файлы…, номера УПД и счёта»; ПРД — «К платёжно-расчётному документу»):
the lot keeps the УПД's and the invoice's names, the delivery keeps the ПРД, the lot list reads the rest from the
delivery. Migration 0152 adds the columns and copies the printed name of lots accepted from a scanned УПД."""

import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

from app.modules.warehouse.domain import RECEIPT_SCAN_PATH
from tests.warehouse.test_receipts import _accept, _receipt, stand  # noqa: F401 — fixture

BEFORE = "0151_upd_llm_recognition"
AFTER = "0152_lot_document_names"
SCAN = f"{RECEIPT_SCAN_PATH}{uuid4()}"
UPD_NAME = "Контакт обжимной SXH-001T-P0.6 (JST), упаковка 6000 шт, код 00-00042353"


def test_the_delivery_keeps_its_payment_document_and_answers_it_back(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client, prd_number="916, 917", prd_date="2026-10-19")
    assert (receipt["prd_number"], receipt["prd_date"]) == ("916, 917", "2026-10-19")
    again = client.put(f"/api/stock/receipts/{receipt['id']}",
                       json={**receipt, "prd_number": "918", "prd_date": None, "version": receipt["version"]})
    assert again.status_code == 200, again.text
    assert (again.json()["prd_number"], again.json()["prd_date"]) == ("918", None)
    resumed = client.get(f"/api/stock/receipts/{receipt['id']}").json()["receipt"]
    assert resumed["prd_number"] == "918"
    bad = _receipt_response(client, prd_date="19.10.2026")
    assert bad.status_code == 422 and "Дата ПРД" in bad.json()["detail"]


def _receipt_response(client, **fields):
    from tests.warehouse.test_receipts import HEADER

    return client.post("/api/stock/receipts", json={**HEADER, **fields})


def test_accept_keeps_the_upd_name_and_the_lot_list_reads_the_documents_from_the_delivery(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client, scan_file=SCAN, prd_number="916", prd_date="2026-10-19")
    accepted = _accept(client, receipt["id"], name="Контакт обжимной JST SXH, 0,08–0,2 мм²", upd_name=UPD_NAME)
    assert accepted.status_code == 200, accepted.text
    lot = accepted.json()["lot"]
    assert (lot["name"], lot["upd_name"], lot["invoice_name"]) == (
        "Контакт обжимной JST SXH, 0,08–0,2 мм²", UPD_NAME, "")

    [listed] = client.get("/api/stock?limit=10").json()["lots"]
    assert (listed["upd_name"], listed["invoice_name"]) == (UPD_NAME, "")
    assert listed["receipt"] == {
        "id": receipt["id"], "supplier": "ООО «Ромашка Электро»", "invoice_number": "СЧ-45",
        "invoice_date": "2026-10-01", "upd_number": "123", "upd_date": "2026-10-20", "prd_number": "916",
        "prd_date": "2026-10-19", "scan_file": SCAN,
    }


def test_a_lot_edit_keeps_the_document_names(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client)
    lot = _accept(client, receipt["id"], name="Разъём", upd_name=UPD_NAME).json()["lot"]
    edited = client.put(f"/api/stock/batches/{lot['id']}/identity",
                         json={"version": lot["version"], "article": "CON-002", "name": "Разъём 2", "series": ""})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["lot"]["name"], edited.json()["lot"]["upd_name"]) == ("Разъём 2", UPD_NAME)


def test_quick_entry_without_a_delivery_has_no_upd_name(stand):  # noqa: F811
    client, _ = stand
    response = client.post("/api/stock/batches", json={"items": [
        {"article": "CON-009", "quantity": "1", "storage_location": "2A1", "name": "Разъём"}]})
    assert response.status_code == 200, response.text
    [listed] = client.get("/api/stock?limit=10").json()["lots"]
    assert (listed["upd_name"], listed["receipt"]) == ("", None)


# --- migration 0152 ------------------------------------------------------------------------------------------------


def _config(path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _lot(connection, lot_id: str, grn: str, name: str, receipt_id: str | None) -> None:
    connection.execute(
        "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose,"
        " unit, storage_location, inbound, written_off, reserved, source_receiving_ids, manufacturer, description,"
        " storage_conditions, datasheet, certificate, receipt_id) VALUES (?, 1, 'ЦАР', ?, '', 'A', '', ?, '',"
        " 'Склад', 'шт', '1F4', 1, 0, 0, '[]', '', '', '', '', '', ?)",
        (lot_id, grn, name, receipt_id),
    )


def test_migration_copies_the_printed_name_of_lots_from_a_scanned_upd_and_renames_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    scanned, typed = "a" * 32, "b" * 32
    with sqlite3.connect(path) as connection:
        insert = ("INSERT INTO warehouse_receipts (id, version, warehouse, scan_file, created_at, created_by,"
                  " updated_at) VALUES (?, 1, 'ЦАР', ?, '2026-10-23', 'Кладовщик', '2026-10-23')")
        connection.execute(insert, (scanned, SCAN))
        connection.execute(insert, (typed, ""))
        _lot(connection, "1" * 32, "OLD.26.0000001", UPD_NAME, scanned)
        _lot(connection, "2" * 32, "OLD.26.0000002", "Разъём, набран руками", typed)
        _lot(connection, "3" * 32, "OLD.26.0000003", "Старая партия", None)
        _lot(connection, "4" * 32, "OLD.26.0000004", "", scanned)
        before = connection.execute("SELECT id, name FROM warehouse_lots ORDER BY id").fetchall()

    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT id, name, upd_name, invoice_name FROM warehouse_lots ORDER BY id").fetchall()
        assert [(row[0], row[1]) for row in rows] == before, "no lot renamed, none added or removed"
        assert [(row[2], row[3]) for row in rows] == [(UPD_NAME, ""), ("", ""), ("", ""), ("", "")]
        assert connection.execute("SELECT prd_number, prd_date FROM warehouse_receipts").fetchall() == [
            ("", None), ("", None)]
        assert connection.execute("SELECT count(*) FROM warehouse_receipts").fetchone() == (2,)

    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        lots = [row[1] for row in connection.execute("PRAGMA table_info(warehouse_lots)")]
        receipts = [row[1] for row in connection.execute("PRAGMA table_info(warehouse_receipts)")]
        assert "upd_name" not in lots and "invoice_name" not in lots and "prd_number" not in receipts
        assert connection.execute("SELECT id, name FROM warehouse_lots ORDER BY id").fetchall() == before
