"""«Приход на склад» (owner 23.10.2026): a delivery header, its lines accepted one by one («Принять» → one lot + a
label job with one copy per bag), the double-count guard against «Приёмка», the autofill of a typed article, the УПД
scan, and the delivery shown on the lot and in «Журнал склада»."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import (
    ItemRow, SqlAlchemyPurchasingUnitOfWork, UpdDocumentRow, UpdLineRow,
)
from app.modules.receiving.infrastructure import ReceivingLineRow, SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse import receipt_ocr, receipts_application
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, StockLotRow
from app.modules.warehouse.label_jobs import LabelJobRow
from app.modules.warehouse.receipt_scan import NO_TEXT, parse_upd_text
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.pdf_samples import identity_pdf

NOW = datetime(2026, 10, 23, 12, 0)
HEADER = {
    "warehouse": "ЦАР", "supplier": "ООО «Ромашка Электро»", "invoice_number": "СЧ-45", "invoice_date": "2026-10-01",
    "upd_number": "123", "upd_date": "2026-10-20", "purpose": "591К.3",
}
LINE = {"article": "CON-001", "quantity": "10", "storage_location": "2A1", "labels": 3}
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f80f00000101000518d84e0000000049454e44ae426082"
)
_STATE = ("warehouse_uow_factory", "receiving_uow_factory", "procurement_uow_factory", "purchasing_uow_factory")


@pytest.fixture()
def stand():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    saved = {name: getattr(app.state, name) for name in _STATE if hasattr(app.state, name)}
    app.state.warehouse_uow_factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.receiving_uow_factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"), email="warehouse@example.test",
        roles=frozenset({"receiver"}), permissions={"warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    for name in _STATE:
        if name in saved:
            setattr(app.state, name, saved[name])
        elif hasattr(app.state, name):
            delattr(app.state, name)
    engine.dispose()


def _receipt(client, **fields) -> dict:
    response = client.post("/api/stock/receipts", json={**HEADER, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def _accept(client, receipt_id, **fields):
    return client.post(f"/api/stock/receipts/{receipt_id}/accept", json={**LINE, **fields})


# --- schema -----------------------------------------------------------------------------------------------------


def test_migration_adds_receipts_and_keeps_old_lots_unlinked(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "0142_production_vswr_measurements")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose,"
            " unit, storage_location, inbound, written_off, reserved, source_receiving_ids, manufacturer, description,"
            " storage_conditions, datasheet, certificate) VALUES (?, 1, 'ЦАР', 'OLD.26.0000001', '', 'A', '', '', '',"
            " 'Склад', 'шт', '1F4', 1, 0, 0, '[]', '', '', '', '', '')",
            ("4" * 32,),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT receipt_id FROM warehouse_lots").fetchall() == [(None,)]
        insert = ("INSERT INTO warehouse_receipts (id, version, warehouse, created_at, created_by, updated_at)"
                  " VALUES (?, 1, ?, '2026-10-23', ?, '2026-10-23')")
        connection.execute(insert, ("1" * 32, "ЖУК", "Кладовщик"))
        row = connection.execute(
            "SELECT supplier, invoice_number, upd_number, purpose, scan_file FROM warehouse_receipts").fetchone()
        assert row == ("", "", "", "", "")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(insert, ("2" * 32, "СКЛ", "Кладовщик"))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute(insert, ("3" * 32, "ЦАР", None))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute(
                "INSERT INTO warehouse_receipts (id, version, warehouse, supplier, created_at, created_by, updated_at)"
                " VALUES (?, 1, 'ЦАР', NULL, '2026-10-23', 'x', '2026-10-23')", ("5" * 32,))
    command.downgrade(config, "0142_production_vswr_measurements")
    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(warehouse_lots)")]
        assert "receipt_id" not in columns
        assert connection.execute("SELECT count(*) FROM warehouse_lots").fetchone() == (1,)


def test_migration_is_followed_only_by_the_scan_queue():
    from alembic.script import ScriptDirectory

    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_revision("0145_warehouse_scan_jobs").down_revision == "0144_warehouse_receipts"


# --- header -----------------------------------------------------------------------------------------------------


def test_header_is_created_edited_under_its_version_and_listed_newest_first(stand):
    client, _ = stand
    first = _receipt(client)
    assert {key: first[key] for key in ("version", "warehouse", "supplier", "invoice_date", "upd_date", "lots_count",
                                        "created_by")} == {
        "version": 1, "warehouse": "ЦАР", "supplier": "ООО «Ромашка Электро»", "invoice_date": "2026-10-01",
        "upd_date": "2026-10-20", "lots_count": 0, "created_by": "Кладовщик",
    }
    edited = client.put(f"/api/stock/receipts/{first['id']}",
                        json={**HEADER, "upd_number": "124", "upd_date": None, "version": 1})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["version"], edited.json()["upd_number"], edited.json()["upd_date"]) == (2, "124", None)

    stale = client.put(f"/api/stock/receipts/{first['id']}", json={**HEADER, "version": 1})
    assert stale.status_code == 409
    assert "изменён другим" in stale.json()["detail"]

    second = _receipt(client, supplier="Второй")
    listed = client.get("/api/stock/receipts?limit=20").json()["receipts"]
    assert {row["id"] for row in listed} == {first["id"], second["id"]}
    assert client.get(f"/api/stock/receipts/{uuid4()}").status_code == 404


@pytest.mark.parametrize("fields,detail", [
    ({"warehouse": "СКЛ"}, "Неизвестный склад «СКЛ»"),
    ({"warehouse": ""}, "Выберите склад прихода"),
    ({"upd_date": "20.10.2026"}, "Дата УПД — дата ГГГГ-ММ-ДД, а не «20.10.2026»"),
    ({"supplier": "х" * 256}, "Поле «Поставщик» длиннее 255 знаков"),
    ({"scan_file": "https://evil.example/x.pdf"}, "Скан УПД — ссылка на файл, загруженный в приход"),
])
def test_bad_header_is_refused_with_a_russian_detail(stand, fields, detail):
    client, _ = stand
    response = client.post("/api/stock/receipts", json={**HEADER, **fields})
    assert response.status_code == 422
    assert response.json()["detail"] == detail


def test_warehouse_of_a_delivery_with_accepted_lines_stays(stand):
    client, _ = stand
    receipt = _receipt(client)
    assert _accept(client, receipt["id"]).status_code == 200
    response = client.put(f"/api/stock/receipts/{receipt['id']}", json={**HEADER, "warehouse": "ЖУК", "version": 1})
    assert response.status_code == 422
    assert response.json()["detail"] == "Склад прихода не меняется: позиции уже приняты на склад ЦАР"


# --- «Принять» --------------------------------------------------------------------------------------------------


def test_accept_puts_one_lot_of_the_delivery_and_one_label_job_with_a_copy_per_bag(stand):
    client, sessions = stand
    receipt = _receipt(client)

    response = _accept(client, receipt["id"], warehouse="ЖУК")

    assert response.status_code == 200, response.text
    body = response.json()
    lot = body["lot"]
    assert (lot["grn"], lot["warehouse"], lot["balance"], lot["purpose"], lot["storage_location"]) == (
        "OLD.26.0000001", "ЦАР", "10", "591К.3", "2A1")
    assert lot["source_receiving_ids"] == [f"manual:{receipt['id']}"]
    assert lot["receipt"] == {"id": receipt["id"], "supplier": "ООО «Ромашка Электро»", "invoice_number": "СЧ-45",
                              "invoice_date": "2026-10-01", "upd_number": "123", "upd_date": "2026-10-20",
                              "prd_number": "", "prd_date": None, "scan_file": ""}
    assert body["label_error"] == ""
    with sessions() as session:
        [job] = session.scalars(select(LabelJobRow)).all()
        stored = session.get(StockLotRow, UUID(lot["id"]))
    assert (str(job.id), job.copies, job.kind, str(job.lot_id), job.status) == (
        body["label_job_id"], 3, "lot", lot["id"], "pending")
    assert str(stored.receipt_id) == receipt["id"]

    detail = client.get(f"/api/stock/receipts/{receipt['id']}").json()
    assert detail["receipt"]["lots_count"] == 1
    assert [row["grn"] for row in detail["lots"]] == ["OLD.26.0000001"]


def test_a_label_that_cannot_be_made_never_costs_the_lot(stand, monkeypatch):
    client, sessions = stand
    receipt = _receipt(client)

    def broken(*_args, **_kwargs):
        raise ValueError("код не помещается на этикетку")

    monkeypatch.setattr(receipts_application, "tspl_job", broken)
    response = _accept(client, receipt["id"])

    assert response.status_code == 200, response.text
    assert response.json()["label_job_id"] is None
    assert response.json()["label_error"] == (
        "Партия принята, этикетка не поставлена в очередь: код не помещается на этикетку")
    with sessions() as session:
        assert session.scalars(select(LabelJobRow)).all() == []
        assert len(session.scalars(select(StockLotRow)).all()) == 1


@pytest.mark.parametrize("fields,detail", [
    ({"storage_location": ""}, "укажи стеллаж, полку и № на полке"),
    ({"quantity": "0"}, "кол-во должно быть больше нуля"),
    ({"article": "", "name": ""}, "пустой артикул"),
    ({"labels": 0}, "Этикеток — от 1 до 50"),
    ({"labels": 51}, "Этикеток — от 1 до 50"),
])
def test_a_bad_line_is_422_and_nothing_is_saved(stand, fields, detail):
    client, sessions = stand
    receipt = _receipt(client)
    response = _accept(client, receipt["id"], **fields)
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    with sessions() as session:
        assert session.scalars(select(StockLotRow)).all() == []
        assert session.scalars(select(LabelJobRow)).all() == []


def test_line_purpose_falls_back_to_the_delivery_then_to_sklad(stand):
    client, _ = stand
    with_purpose = _receipt(client)
    without = _receipt(client, purpose="")
    assert _accept(client, with_purpose["id"], purpose="ВП-001").json()["lot"]["purpose"] == "ВП-001"
    assert _accept(client, with_purpose["id"]).json()["lot"]["purpose"] == "591К.3"
    assert _accept(client, without["id"]).json()["lot"]["purpose"] == "Склад"


def test_accept_into_an_unknown_delivery_is_404(stand):
    client, _ = stand
    assert _accept(client, uuid4()).status_code == 404


def _receiving_line(sessions, *, article="CON-001", status="В пути", order_number="СЧ-45", supplier="Другой"):
    with sessions() as session:
        session.add(ReceivingLineRow(
            id=uuid4(), source_event_id=uuid4(), source_idempotency_key=str(uuid4()), procurement_line_id=uuid4(),
            legacy_id="SM-17", status=status, version=1, created_at=NOW, warehouse="ЦАР", article=article, series="",
            name="Разъём", item_type="", purpose="", goal="", source_link="", supplier=supplier,
            ordered_quantity=Decimal("10"), unit="шт", quality_document_required=False, nonconformities=[],
            backorder_quantity=Decimal("0"), package="", warehouse_event_emitted=False, order_number=order_number,
        ))
        session.commit()


def test_a_line_awaited_in_receiving_asks_first_and_is_accepted_on_confirm(stand):
    client, sessions = stand
    _receiving_line(sessions, article="con 001")
    receipt = _receipt(client)

    asked = _accept(client, receipt["id"])
    assert asked.status_code == 409
    assert asked.json()["detail"] == "Позиция ожидается в Приёмке: заказ СЧ-45 (Другой). Принять здесь всё равно?"

    confirmed = _accept(client, receipt["id"], confirm_expected=True)
    assert confirmed.status_code == 200, confirmed.text


def test_guard_matches_the_supplier_too_and_skips_closed_lines(stand):
    client, sessions = stand
    _receiving_line(sessions, order_number="", supplier="ООО Ромашка Электро")
    _receiving_line(sessions, article="CON-002", status="Принято", order_number="СЧ-45")
    receipt = _receipt(client, invoice_number="")
    assert _accept(client, receipt["id"]).status_code == 409
    assert _accept(client, receipt["id"], article="CON-002").status_code == 200


# --- article autofill -------------------------------------------------------------------------------------------


def test_article_info_fills_from_the_last_lot_then_the_reference_db_then_purchasing(stand):
    client, sessions = stand
    with sessions() as session:
        session.add(ItemRow(id=uuid4(), name="Разъём N из номенклатуры", part_number="N-C58P", part_key="NC58P",
                            manufacturer="Амфенол", category="connector", item_type="", unit="шт", created_at=NOW))
        session.commit()

    only_sources = client.get("/api/stock/article-info?q=N-C58P").json()
    assert only_sources["info"]["series"] == "N"  # the code rule, before the nomenclature
    assert only_sources["info"]["name"] == "N-male RG-58"
    assert only_sources["info"]["manufacturer"] == "Амфенол"  # still empty after the rule
    assert only_sources["info"]["source"] == "разбор кода, номенклатура закупок"
    assert only_sources["places"] == [] and only_sources["expected"] is None

    receipt = _receipt(client)
    _accept(client, receipt["id"], article="N-C58P", name="Разъём N на кабель", manufacturer="", series="",
            datasheet="https://ds.example/n.pdf", storage_location="1F4")
    _accept(client, receipt["id"], article="N-C58P", name="Разъём N на кабель", storage_location="2A1")

    after = client.get("/api/stock/article-info?q=n-c58p&warehouse=ЦАР").json()
    assert after["info"]["name"] == "Разъём N на кабель"  # the last lot wins
    assert after["info"]["datasheet"] == ""  # the LAST lot had none, the first one is not read
    assert after["info"]["series"] == "N" and after["info"]["manufacturer"] == "Амфенол"
    assert after["info"]["expiry_date"] is None
    assert after["info"]["source"].startswith("партия OLD.26.0000002")
    assert after["places"] == ["2A1", "1F4"]
    assert client.get("/api/stock/article-info?q=N-C58P&warehouse=ЖУК").json()["places"] == []


def test_article_info_reads_the_procurement_line_of_the_invoice_and_the_awaited_line(stand):
    client, sessions = stand
    with sessions() as session:
        session.add(ProcurementLineRow(
            id=uuid4(), source_event_id=uuid4(), source_idempotency_key=str(uuid4()), source_request_id=uuid4(),
            legacy_id="SM-20261001-01", revision="1", status="Заказано", version=1, created_at=NOW,
            request_date=date(2026, 10, 1), initiator="", department="", task_type="", designation="591К.3",
            priority="", delivery_point="", planned_date=date(2026, 10, 30), item_type="Кабель", category="cable",
            calculated_name="Кабель RG-58", calculated_quantity=Decimal("5"), calculated_unit="м",
            design_article="RG58-X", invoice_number="СЧ-45", supplier="ООО «Ромашка Электро»",
        ))
        session.commit()
    _receiving_line(sessions, article="RG58-X", order_number="", supplier="Кто-то")

    found = client.get("/api/stock/article-info", params={
        "q": "RG58-X", "supplier": "ООО Ромашка Электро", "invoice_number": "сч 45"}).json()
    assert {key: found["info"][key] for key in ("name", "item_type", "unit", "source")} == {
        "name": "Кабель RG-58", "item_type": "Кабель", "unit": "м", "source": "строка закупки SM-20261001-01"}
    assert found["expected"] is None  # another supplier, no number of this invoice

    _receiving_line(sessions, article="RG58-X", order_number="СЧ-45", supplier="Кто-то")
    found = client.get("/api/stock/article-info", params={"q": "RG58-X", "invoice_number": "СЧ-45"}).json()
    assert found["expected"] == {"order_number": "СЧ-45", "supplier": "Кто-то"}


# --- УПД scan ---------------------------------------------------------------------------------------------------

UPD_TEXT = [
    "Счет-фактура № 123 от 20.10.2026 (1)",
    "Продавец: ООО «Ромашка Электро» (2)",
    "ИНН/КПП продавца: 7701234560/770101001 (2б)",
    "Основание передачи: Счёт № СЧ-45 от 01.10.2026",
    "К платёжно-расчётному документу № 916 от 19.10.2026, № 917 от 20.10.2026 (5)",
]


def _scan(client, name: str, content: bytes, kind: str) -> dict:
    """«Файл…»: the scan is stored with a done job, read after the answer (26.10); the poll gives the result."""
    job = client.post("/api/stock/receipts/scan", files={"file": (name, content, kind)})
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "done" and job.json()["scan_file"].startswith("/api/stock/receipts/files/")
    polled = client.get(f"/api/stock/receipts/scan-jobs/{job.json()['id']}").json()
    assert polled["recognising"] is False
    return polled["result"]


def test_scan_of_a_pdf_with_text_is_stored_and_recognised(stand):
    client, _ = stand
    body = _scan(client, "упд.pdf", identity_pdf(UPD_TEXT), "application/pdf")
    assert body["recognised"] == {
        "supplier": "ООО «Ромашка Электро»", "inn": "7701234560", "upd_number": "123", "upd_date": "2026-10-20",
        "invoice_number": "СЧ-45", "invoice_date": "2026-10-01",
        "prd_number": "916, 917", "prd_date": "2026-10-19",  # the payment document, not the invoice
    }
    assert body["lines"] == [] and body["note"] == ""
    assert body["scan_file"].startswith("/api/stock/receipts/files/")
    served = client.get(body["scan_file"])
    assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
    assert _receipt(client, scan_file=body["scan_file"])["scan_file"] == body["scan_file"]


def test_scan_of_a_picture_is_stored_and_asks_to_type_the_header(stand, monkeypatch):
    client, _ = stand
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "")  # OCR read nothing on the picture
    body = _scan(client, "photo.png", PNG, "image/png")
    assert body["note"] == NO_TEXT == "Текст не распознан — заполните вручную"
    assert body["recognised"]["upd_number"] == "" and body["recognised"]["upd_date"] is None


def test_scan_refuses_what_is_not_a_pdf_or_a_picture(stand):
    client, _ = stand
    response = client.post("/api/stock/receipts/scan", files={"file": ("x.html", b"<html></html>", "text/html")})
    assert response.status_code == 422
    assert response.json()["detail"] == "Скан УПД — файл PDF, JPG или PNG"
    assert client.post("/api/stock/receipts/scan", json={}).status_code == 422


def test_scan_takes_header_and_lines_of_the_upd_purchasing_already_holds(stand):
    client, sessions = stand
    with sessions() as session:
        session.add(UpdDocumentRow(
            id="edo-1", origin="edo", file_name="x.xml", status="Разобран", number="123", doc_date=date(2026, 10, 21),
            seller_name="ООО \"Ромашка Электро\"", seller_inn="7701234560", synced_at=NOW, created_at=NOW,
            basis="Счёт № СЧ-45 от 01.10.2026",
        ))
        session.flush()
        for seq, (article, quantity) in enumerate((("CON-001", Decimal("10")), ("CON-002", Decimal("2.5")))):
            session.add(UpdLineRow(id=uuid4(), document_id="edo-1", source="google", seq=seq, name=f"Разъём {seq}",
                                   article=article, unit="шт", quantity=quantity))
        session.commit()

    body = _scan(client, "упд.pdf", identity_pdf(UPD_TEXT[:1]), "application/pdf")

    assert body["recognised"]["supplier"] == "ООО \"Ромашка Электро\""
    assert body["recognised"]["upd_date"] == "2026-10-21"
    assert body["recognised"]["invoice_number"] == "СЧ-45"
    assert body["lines"] == [
        {"article": "CON-001", "name": "Разъём 0", "quantity": "10", "unit": "шт", "upd_name": "Разъём 0"},
        {"article": "CON-002", "name": "Разъём 1", "quantity": "2.5", "unit": "шт", "upd_name": "Разъём 1"},
    ]


OCR_ROWS = (Path(__file__).parent / "fixtures" / "upd-1788-ocr-rows.txt").read_text(encoding="utf-8")


def test_scan_without_a_registry_upd_reads_the_rows_off_the_scan_and_snaps_known_articles(stand, monkeypatch):
    """Owner 23.10, the first real scan: the article from the name, the roll length × the rolls (26.10: a tape's
    «9мм*16м» is its size — it stays 2 шт)."""
    client, sessions = stand
    monkeypatch.setattr(app.state, "references_uow_factory", None, raising=False)
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "\n".join([*UPD_TEXT, OCR_ROWS]))
    with sessions() as session:
        session.add(ItemRow(id=uuid4(), name="Лента самоклеющаяся 12 мм", part_number="TP-I120EW", part_key="TPI120EW",
                            manufacturer="", category="other", item_type="", unit="м", created_at=NOW))
        session.commit()

    body = _scan(client, "скан.png", PNG, "image/png")

    assert body["recognised"]["upd_number"] == "123"
    assert body["note"] == "Строки прочитаны со скана (6) — сверьте артикулы и количество"
    assert [(line["article"], line["quantity"], line["unit"], line["packs"], line["pack"]) for line in body["lines"]] == [
        ("МТ-2К 6,4/3,2 белая", "300", "м", "3", "100 м"),
        ("МТ-2К 4,8/2,4 белая", "300", "м", "3", "100 м"),
        ("МТ-2К 3,2/1,6 белая", "600", "м", "3", "200 м"),
        ("МТ-2К 2,4/1,2 белая", "600", "м", "3", "200 м"),
        ("Тр-I090EW", "2", "шт", "", ""),
        ("TP-I120EW", "2", "шт", "", ""),
    ]
    assert body["lines"][5]["source"] == "скан УПД, артикул из номенклатуры закупок"
    assert body["lines"][0]["supplier_code"] == "00-00042353" and body["lines"][0]["pack_kind"] == "рул."


def test_scan_rows_come_even_when_the_header_is_not_read(stand, monkeypatch):
    client, _ = stand
    monkeypatch.setattr(app.state, "references_uow_factory", None, raising=False)
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: OCR_ROWS)
    body = _scan(client, "скан.png", PNG, "image/png")
    assert len(body["lines"]) == 6
    assert body["note"] == "Шапка не распознана — заполните вручную. Строки прочитаны со скана (6) — сверьте артикулы и количество"


def test_upd_text_reads_word_dates_and_a_seller_without_marks():
    found = parse_upd_text("Универсальный передаточный документ № А-77 от 5 октября 2026 г.\nПродавец ООО Альфа")
    assert (found.upd_number, found.upd_date, found.supplier) == ("А-77", date(2026, 10, 5), "ООО Альфа")


# --- read model -------------------------------------------------------------------------------------------------


def test_lot_list_scan_and_journal_show_the_delivery(stand):
    client, _ = stand
    receipt = _receipt(client, invoice_number="", invoice_date=None)
    _accept(client, receipt["id"])
    client.post("/api/stock/batches", json={"items": [{"article": "OLD-1", "quantity": "1", "storage_location": "1F4"}]})

    lots = {lot["article"]: lot for lot in client.get("/api/stock").json()["lots"]}
    assert lots["CON-001"]["receipt"]["upd_number"] == "123"
    assert lots["OLD-1"]["receipt"] is None
    scanned = client.get("/api/stock/scan?code=OLD.26.0000001").json()["lots"]
    assert scanned[0]["receipt"]["id"] == receipt["id"]

    events = [row["event"] for row in client.get("/api/stock/journal").json()["rows"]]
    assert "Внесено вручную: +10 · место 2A1 · Приход: ООО «Ромашка Электро» · УПД 123 от 20.10.2026" in events
    assert "Внесено вручную: +1 · место 1F4" in events
    assert "Поставка заведена: склад ЦАР · Приход: ООО «Ромашка Электро» · УПД 123 от 20.10.2026" in events


def test_journal_text_names_invoice_and_leaves_out_empty_parts():
    from app.modules.warehouse.journal import event_text

    details = {"quantity": "2", "storage_location": "1F4", "receipt_id": "x", "supplier": "", "upd_number": "",
               "upd_date": "", "invoice_number": "С-1", "invoice_date": "2026-10-01"}
    assert event_text("warehouse.manually_received", details) == "Внесено вручную: +2 · место 1F4 · счёт С-1 от 01.10.2026"


def _jpeg(fmt: str = "JPEG") -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (40, 60), "white").save(buffer, fmt)
    return buffer.getvalue()


def test_scan_merge_glues_stored_sheets_into_one_pdf(stand, monkeypatch):
    from io import BytesIO

    from pypdf import PdfReader

    client, _ = stand
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "")
    first = _scan(client, "упд.pdf", _jpeg("PDF"), "application/pdf")
    second = _scan(client, "лист2.jpg", _jpeg(), "image/jpeg")
    response = client.post("/api/stock/receipts/scan-merge", json={"files": [first["scan_file"], second["scan_file"]]})
    assert response.status_code == 200, response.text
    job = response.json()
    assert (job["status"], job["recognising"]) == ("done", False), "the sheets' readings combined at once"
    body = job["result"]
    assert set(body) == {"scan_file", "recognised", "lines", "note"} and body["scan_file"] == job["scan_file"]
    assert body["scan_file"] not in (first["scan_file"], second["scan_file"])
    served = client.get(body["scan_file"])
    assert served.headers["content-type"] == "application/pdf"
    assert len(PdfReader(BytesIO(served.content)).pages) == 2


def test_scan_merge_refuses_a_foreign_link_and_a_wrong_count(stand):
    client, _ = stand
    one = _scan(client, "упд.pdf", _jpeg("PDF"), "application/pdf")["scan_file"]
    ghost = f"/api/stock/receipts/files/{uuid4()}"
    for links in ([one, ghost], [one, "http://evil/x"], [one], [one] * 21):
        response = client.post("/api/stock/receipts/scan-merge", json={"files": links})
        assert response.status_code == 422, links
        assert any("а" <= ch.lower() <= "я" for ch in response.json()["detail"])