"""Три артикула у партии склада (owner 28.10.2026): «Артикул производителя» — с которым позиция вышла с завода,
«Артикул поставщика» — под которым она в продаже у поставщика, «Артикул номенклатуры» — наш (`article`, как был).
The delivery line and quick entry fill them, the row edit changes them (version-checked, journaled), the stock list
sorts and searches by them; migration 0158 adds the columns and fills only what is certain."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.warehouse.application import STOCK_SORT_KEYS, sort_stock_lots
from app.modules.warehouse.domain import RECEIPT_SCAN_PATH, StockLot
from app.modules.warehouse.word_search import matches_words
from tests.warehouse.test_receipts import _accept, _receipt, stand  # noqa: F401 — fixture

UPD_NAME = "Vell Наконечник штыревой втулочный, НШВ 6,0-12, 100 шт (VLnhv-6.0-12(100)) 1396474"
NSHV = {
    "article": "НШВ 6,0-12", "maker_article": "VLnhv-6.0-12(100)", "supplier_article": "1396474",
    "manufacturer": "Vell", "name": "Наконечник штыревой втулочный, НШВ 6,0-12, 100 шт",
}


def test_a_delivery_line_keeps_the_maker_and_the_supplier_article_beside_ours(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client)
    accepted = _accept(client, receipt["id"], **NSHV, upd_name=UPD_NAME)
    assert accepted.status_code == 200, accepted.text
    lot = accepted.json()["lot"]
    assert (lot["article"], lot["maker_article"], lot["supplier_article"]) == (
        "НШВ 6,0-12", "VLnhv-6.0-12(100)", "1396474")
    [listed] = client.get("/api/stock?limit=10").json()["lots"]
    assert (listed["maker_article"], listed["supplier_article"]) == ("VLnhv-6.0-12(100)", "1396474")


def test_quick_entry_takes_both_articles_and_leaves_them_empty_when_not_typed(stand):  # noqa: F811
    client, _ = stand
    response = client.post("/api/stock/batches", json={"items": [
        {**NSHV, "quantity": "100", "storage_location": "2A1"},
        {"article": "CON-009", "quantity": "1", "storage_location": "2A1", "name": "Разъём"},
    ]})
    assert response.status_code == 200, response.text
    lots = {lot["article"]: lot for lot in client.get("/api/stock?limit=10").json()["lots"]}
    assert (lots["НШВ 6,0-12"]["maker_article"], lots["НШВ 6,0-12"]["supplier_article"]) == (
        "VLnhv-6.0-12(100)", "1396474")
    assert (lots["CON-009"]["maker_article"], lots["CON-009"]["supplier_article"]) == ("", "")


def test_the_articles_are_trimmed_and_too_long_ones_refused(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client)
    lot = _accept(client, receipt["id"], maker_article="  ABC-1  ", supplier_article=" 00-42 ").json()["lot"]
    assert (lot["maker_article"], lot["supplier_article"]) == ("ABC-1", "00-42")
    assert _accept(client, receipt["id"], maker_article="x" * 256).status_code == 422


def test_the_row_edit_changes_both_articles_with_a_version_check_and_a_journal(stand):  # noqa: F811
    client, _ = stand
    lot = _accept(client, _receipt(client)["id"], **NSHV).json()["lot"]
    edited = client.put(f"/api/stock/batches/{lot['id']}/row", json={
        "version": lot["version"], "maker_article": "VLnhv-6.0-12", "supplier_article": "",
    })
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["lot"]["article"], body["lot"]["maker_article"], body["lot"]["supplier_article"]) == (
        "НШВ 6,0-12", "VLnhv-6.0-12", "")
    assert [change["column"] for change in body["changes"]] == ["АРТИКУЛ ПРОИЗВОДИТЕЛЯ", "АРТИКУЛ ПОСТАВЩИКА"]
    stale = client.put(f"/api/stock/batches/{lot['id']}/row",
                       json={"version": lot["version"], "maker_article": "другой"})
    assert stale.status_code == 409
    too_long = client.put(f"/api/stock/batches/{lot['id']}/row",
                          json={"version": body["lot"]["version"], "supplier_article": "1" * 256})
    assert too_long.status_code == 422


def _lot(number: int, maker: str = "", supplier: str = "") -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=f"OLD.26.{number:07d}", package="", article=f"ART-{number}", name=f"Позиция {number}",
        item_type="Комплектующие", purpose="Склад", unit="шт", quantity=Decimal("1"), storage_location="1F4",
        source_receiving_ids=("r1",), maker_article=maker, supplier_article=supplier,
    )


@pytest.mark.parametrize("key", ["maker_article", "supplier_article"])
def test_the_stock_sorts_by_both_articles_naturally_and_empty_ones_first(key):
    assert key in STOCK_SORT_KEYS
    lots = [_lot(1, "B-10", "200"), _lot(2, "B-9", "30"), _lot(3), _lot(4, "a-1", "1000")]
    ordered = [getattr(lot, key) for lot in sort_stock_lots(lots, key, desc=False)]
    assert ordered == {"maker_article": ["", "a-1", "B-9", "B-10"], "supplier_article": ["", "30", "200", "1000"]}[key]
    assert [getattr(lot, key) for lot in sort_stock_lots(lots, key, desc=True)] == ordered[::-1]


def test_the_stock_list_api_sorts_by_the_new_columns(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client)
    for maker, supplier in (("B-10", "2"), ("B-9", "10")):
        assert _accept(client, receipt["id"], article=f"A{maker}", maker_article=maker,
                       supplier_article=supplier).status_code == 200
    for key, expected in (("maker_article", ["B-9", "B-10"]), ("supplier_article", ["2", "10"])):
        response = client.get("/api/stock", params={"sort": key, "limit": 10})
        assert response.status_code == 200, response.text
        assert [lot[key] for lot in response.json()["lots"]] == expected


@pytest.mark.parametrize("query", ["1396474", "VLnhv-6.0-12", "vlnhv6012", "наконечник 1396474"])
def test_a_search_word_finds_the_maker_or_the_supplier_article(query):
    lot = _lot(7, "VLnhv-6.0-12(100)", "1396474")
    lot = StockLot(**{**lot.__dict__, "name": "Наконечник штыревой втулочный"})
    assert matches_words(lot, query)
    assert not matches_words(_lot(8), query)


def test_the_stock_list_api_searches_by_the_supplier_article(stand):  # noqa: F811
    client, _ = stand
    receipt = _receipt(client)
    _accept(client, receipt["id"], **NSHV)
    _accept(client, receipt["id"], article="CON-001")
    found = client.get("/api/stock", params={"q": "1396474"}).json()["lots"]
    assert [lot["article"] for lot in found] == ["НШВ 6,0-12"]


# --- migration 0158 ------------------------------------------------------------------------------------------------

BEFORE = "0157_warehouse_scan_agents"
AFTER = "0158_lot_maker_supplier_articles"


def _insert(connection, table: str, **values) -> None:
    """A row with the given values; every other NOT NULL column without a default gets a dummy of its type."""
    for _, name, kind, notnull, default, primary in connection.execute(f"PRAGMA table_info({table})"):
        if name in values or not notnull or default is not None:
            continue
        kind = kind.upper()
        values[name] = (
            0 if any(word in kind for word in ("INT", "NUMERIC", "BOOL", "DECIMAL", "FLOAT")) else
            "2026-10-01" if "DATE" in kind else
            "[]" if "JSON" in kind else uuid4().hex  # unique, and a UUID where the column is one
        )
    columns = ", ".join(values)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))


def _stock_lot(connection, lot_id: str, number: int, article: str, sources: list[str], *, receipt_id=None,
               upd_name: str = "") -> None:
    _insert(connection, "warehouse_lots", id=lot_id, version=1, warehouse="ЦАР", grn=f"OLD.26.{number:07d}",
            package="", article=article, series="", name=f"Позиция {number}", item_type="", purpose="Склад",
            unit="шт", storage_location="1F4", inbound=1, written_off=0, reserved=0,
            source_receiving_ids=json.dumps(sources), receipt_id=receipt_id, upd_name=upd_name)


def _bound_line(connection, part_number: str) -> str:
    """A receiving line whose procurement line is bound to a purchasing item with this part number; its id as text."""
    line_id, procurement_line, item_id = uuid4(), uuid4(), uuid4()
    key = part_number.replace("-", "").replace(" ", "").upper()
    _insert(connection, "purchasing_items", id=item_id.hex, name="Позиция", part_number=part_number,
            part_key=key, category="other", unit="шт", version=1)
    _insert(connection, "purchasing_line_items", procurement_line_id=procurement_line.hex, item_id=item_id.hex)
    _insert(connection, "receiving_lines", id=line_id.hex, procurement_line_id=procurement_line.hex,
            article=part_number, nonconformities="[]")
    return str(line_id)


def test_migration_fills_only_the_certain_articles_and_changes_nothing_else(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, BEFORE)
    file_id, receipt_id = uuid4(), uuid4().hex
    reading = {"found": {}, "lines": [
        {"name": UPD_NAME, "supplier_code": "1396474", "article": "VLnhv-6.0-12(100)"},
        {"name": "Разъём XHP-4 JST", "supplier_code": "00-111"},
        {"name": "Разъём XHP-4  jst", "supplier_code": "00-222"},  # the same printed row twice with two codes
        {"name": "Провод МГТФ 0,12", "supplier_code": ""},
    ]}
    with sqlite3.connect(path) as connection:
        matching = _bound_line(connection, "XHP-4")
        other_item = _bound_line(connection, "SXH-001T")
        _insert(connection, "warehouse_receipt_files", id=file_id.hex, file_name="упд.pdf", content_type="application/pdf",
                size=1, content=b"%", uploaded_by="Кладовщик", uploaded_at="2026-10-27", recognition=json.dumps(reading))
        _insert(connection, "warehouse_receipts", id=receipt_id, version=1, warehouse="ЦАР",
                scan_file=f"{RECEIPT_SCAN_PATH}{file_id}", created_at="2026-10-27", created_by="Кладовщик",
                updated_at="2026-10-27")
        _stock_lot(connection, "1" * 32, 1, "xhp 4", [f"{matching}:r1"])  # the part number, written another way
        _stock_lot(connection, "2" * 32, 2, "XHP-4", [other_item])  # bound to another item
        _stock_lot(connection, "3" * 32, 3, "XHP-4", ["manual:abc", "legacy:ЦАР:OLD.1"])  # not from receiving
        _stock_lot(connection, "4" * 32, 4, "НШВ 6,0-12", [f"manual:{receipt_id}"], receipt_id=receipt_id,
                   upd_name=f"  {UPD_NAME.upper()} ")
        _stock_lot(connection, "5" * 32, 5, "XHP-4", [f"manual:{receipt_id}"], receipt_id=receipt_id,
                   upd_name="Разъём XHP-4 JST")  # two codes — not certain
        _stock_lot(connection, "6" * 32, 6, "МГТФ", [f"manual:{receipt_id}"], receipt_id=receipt_id,
                   upd_name="Провод МГТФ 0,12")  # no code on the row
        _stock_lot(connection, "7" * 32, 7, "Без УПД", [f"manual:{receipt_id}"], receipt_id=receipt_id)
        before = connection.execute("SELECT * FROM warehouse_lots ORDER BY id").fetchall()

    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT id, article, maker_article, supplier_article FROM warehouse_lots ORDER BY id").fetchall()
        assert [row[2:] for row in rows] == [
            ("xhp 4", ""), ("", ""), ("", ""), ("", "1396474"), ("", ""), ("", ""), ("", ""),
        ]
        width = len(before[0])
        assert [row[:width] for row in connection.execute("SELECT * FROM warehouse_lots ORDER BY id")] == before

    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(warehouse_lots)")}
        assert "maker_article" not in columns and "supplier_article" not in columns
        assert connection.execute("SELECT * FROM warehouse_lots ORDER BY id").fetchall() == before
