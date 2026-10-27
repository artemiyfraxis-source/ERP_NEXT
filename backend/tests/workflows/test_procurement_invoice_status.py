"""Счёт закупки в реестре «Счета» живёт строками закупки (решения сессии владельца 27.10.2026 по вопросам Вити): строки
оплачены — счёт оплачен (день и сумма оплаты), строки отменены — счёт отменён, неверный файл заменён новым — старый
счёт «Заменён»; один и тот же счёт (Kaiten или повторная загрузка) — одна строка реестра; перенос счетов, загруженных
до реестра, — отдельной командой с проверкой без записи."""

from __future__ import annotations

import base64
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, update

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.doc_chain import load_world
from app.modules.purchasing.infrastructure import (
    InvoiceFileRow,
    InvoiceRow,
    InvoiceUploadRow,
    SqlAlchemyPurchasingUnitOfWork,
)
from app.modules.purchasing.invoices import invoice_card
from app.modules.requests.api import moscow_now
from app.workflows import procurement_invoice_registry as flow
from tests.factories import Actor
from tests.procurement.test_api import (  # noqa: F401 — fixtures
    awaiting_invoice, procurement_api, rates,
)
from tests.shared.test_invoice_reading import cells_pdf
from tests.workflows.test_procurement_invoice_registry import INVOICE_PDF

NOW = datetime(2026, 10, 27, 12, 0)


def invoice_pdf(number: str, day: str, seller: str = 'ООО "Поставка"', inn: str = "7701234560") -> bytes:
    return cells_pdf([
        (30, 800, seller), (30, 790, "Получатель"),
        (30, 760, f"Счет на оплату № {number} от {day} г."),
        (30, 740, f"Поставщик (Исполнитель): {seller}, ИНН {inn}, КПП 770101001, Москва"),
        (30, 728, 'Покупатель (Заказчик): ООО "ЗЭП "ФРАКСИС", ИНН 5040191181'),
        (30, 700, "№"), (60, 700, "Товары (работы, услуги)"), (300, 700, "Кол-во"), (340, 700, "Ед."),
        (380, 700, "Цена"), (450, 700, "Сумма"),
        (32, 685, "1"), (60, 685, "Разъём SMA"), (300, 685, "2"), (340, 685, "шт"), (380, 685, "122,00"),
        (450, 685, "244,00"),
        (380, 650, "Итого:"), (450, 650, "244,00"),
        (300, 638, "В том числе НДС 22%:"), (450, 638, "44,00"),
    ])


WRONG_PDF = invoice_pdf("777", "1 октября 2026", seller='ООО "Чужой"', inn="7709999990")


@pytest.fixture()
def wired(procurement_api, rates):  # noqa: F811
    client, line, sessions = procurement_api
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    yield client, line, sessions
    del app.state.purchasing_uow_factory


def _later(minutes: int) -> None:
    """The clock moves on: the next upload is a newer file."""
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 15, 11, 30 + minutes)


def _factories(sessions):
    return (lambda: SqlAlchemyProcurementUnitOfWork(sessions)), (lambda: SqlAlchemyPurchasingUnitOfWork(sessions))


def _ready(client, line, sessions) -> int:
    """«Жду счет» with the money typed, delivered to a site with a receiving warehouse; the line's version."""
    with sessions() as session:
        session.get(ProcurementLineRow, line.id).delivery_point = "г. Москва, ул. 6-я Радиальная, д. 17"
        session.commit()
    version = awaiting_invoice(client, line)
    saved = client.patch(f"/api/procurement/{line.id}/money", json={
        "version": version, "currency": "RUB", "price": "100", "vat_rate": "22", "invoice_item_name": "Разъём SMA",
    })
    return saved.json()["line"]["version"]


def _upload(client, line_id, content=INVOICE_PDF, name="счёт.pdf") -> str:
    body = client.post("/api/procurement/invoice-files", json={
        "line_id": str(line_id), "file_name": name, "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
    })
    assert body.status_code == 200, body.text
    return body.json()["url"]


def _record(client, line_id, version, url) -> dict:
    saved = client.post(f"/api/procurement/{line_id}/invoice", json={
        "version": version, "invoice_url": url, "expected_arrival": "2026-09-25",
    })
    assert saved.status_code == 200, saved.text
    return saved.json()["line"]


def _rows(sessions, source=None) -> list[InvoiceRow]:
    with sessions() as session:
        query = select(InvoiceRow).order_by(InvoiceRow.card_created)
        if source:
            query = query.where(InvoiceRow.source == source)
        return list(session.scalars(query))


def _kaiten(sessions, number="12", day=date(2026, 10, 20), inn="7701234560", seller='ООО "Поставка"',
            status="to_pay") -> InvoiceRow:
    row = InvoiceRow(
        id=uuid4(), source="kaiten", external_id=str(uuid4())[:8], title="Карточка Kaiten", status=status,
        card_created=datetime(2026, 10, 20, 9), card_updated=datetime(2026, 10, 20, 9), number=number, doc_date=day,
        seller_name=seller, seller_inn=inn, total=24400, synced_at=datetime(2026, 10, 20, 9), lines=[],
    )
    with sessions() as session:
        session.add(row)
        session.commit()
    return row


def test_paying_the_line_pays_the_uploaded_invoice_with_the_day_and_the_sum(wired):
    client, line, sessions = wired
    version = _ready(client, line, sessions)
    url = _upload(client, line.id)
    (row,) = _rows(sessions)
    assert row.status == "to_pay"

    recorded = _record(client, line.id, version, url)
    assert _rows(sessions)[0].status == "to_pay"
    paid = client.post(f"/api/procurement/{line.id}/paid", json={"version": recorded["version"]})
    assert paid.status_code == 200, paid.text

    (row,) = _rows(sessions)
    assert (row.status, row.payment_date, row.payment_sum) == ("paid", date(2026, 9, 15), 24400)
    with sessions() as session:
        world, _ = load_world(session, date(2026, 10, 27))
        card, _ = invoice_card(session, world, row.id)
    assert (card["status"], card["payment_date"], card["payment_sum"], card["payment_matches"]) == (
        "paid", "2026-09-15", 24400, True)


def test_cancelled_lines_cancel_the_invoice_on_the_workers_pass(wired):
    client, line, sessions = wired
    version = _ready(client, line, sessions)
    _record(client, line.id, version, _upload(client, line.id))
    with sessions() as session:
        session.execute(update(ProcurementLineRow).where(ProcurementLineRow.id == line.id).values(status="Отменено"))
        session.commit()

    assert flow.invoice_status_tick(NOW, sessions) == 1
    assert _rows(sessions)[0].status == "cancelled"
    assert flow.invoice_status_tick(NOW, sessions) == 0  # nothing changes twice


def test_a_wrong_file_replaced_on_the_line_stays_in_the_registry_as_replaced_and_closes_nothing(wired):
    client, line, sessions = wired
    version = _ready(client, line, sessions)
    wrong = _upload(client, line.id, WRONG_PDF, "чужой.pdf")
    recorded = _record(client, line.id, version, wrong)
    _later(5)
    right = _upload(client, line.id)
    assert {row.number: row.status for row in _rows(sessions)} == {"777": "to_pay", "12": "to_pay"}  # not sent yet

    replaced = client.post(f"/api/procurement/{line.id}/invoice/replace", json={
        "version": recorded["version"], "invoice_url": right,
    })
    assert replaced.status_code == 200, replaced.text

    rows = {row.number: row for row in _rows(sessions)}
    assert (rows["777"].status, rows["12"].status) == ("replaced", "to_pay")
    with sessions() as session:
        world, _ = load_world(session, date(2026, 10, 27))
    assert rows["777"].id in world.invoices and rows["777"].id not in world.chain_of_invoice
    assert rows["12"].id in world.chain_of_invoice


def test_a_wrong_file_uploaded_again_before_it_is_sent_is_replaced_at_once(wired):
    client, line, sessions = wired
    _ready(client, line, sessions)
    _upload(client, line.id, WRONG_PDF, "чужой.pdf")
    _later(5)
    _upload(client, line.id)

    assert {row.number: row.status for row in _rows(sessions)} == {"777": "replaced", "12": "to_pay"}


def test_the_same_invoice_uploaded_twice_is_one_row_with_the_newest_file(wired):
    client, line, sessions = wired
    version = _ready(client, line, sessions)
    first = _upload(client, line.id, name="счёт.pdf")
    _later(5)
    second = _upload(client, line.id, invoice_pdf("12", "21 октября 2026"), name="счёт исправленный.pdf")

    (row,) = _rows(sessions)
    assert (row.source, row.doc_date) == ("procurement", date(2026, 10, 21))  # the newest reading is the header
    with sessions() as session:
        uploads = session.scalars(select(InvoiceUploadRow).where(InvoiceUploadRow.invoice_id == row.id)
                                  .order_by(InvoiceUploadRow.uploaded_at)).all()
        files = {f.external_id: f.kind for f in session.scalars(select(InvoiceFileRow).where(InvoiceFileRow.invoice_id == row.id))}
    keys = [first.rsplit("/", 1)[1], second.rsplit("/", 1)[1]]
    assert [(u.file_key, u.created_row) for u in uploads] == [(keys[0], True), (keys[1], False)]
    assert files == {keys[0]: "other", keys[1]: "invoice"}

    # the line carries the second file: paying it pays the one row
    recorded = _record(client, line.id, version, second)
    assert client.post(f"/api/procurement/{line.id}/paid", json={"version": recorded["version"]}).status_code == 200
    assert [(r.status, r.payment_sum) for r in _rows(sessions)] == [("paid", 24400)]


def test_an_invoice_known_from_kaiten_is_not_doubled_and_goes_paid_with_the_lines(wired):
    client, line, sessions = wired
    kaiten = _kaiten(sessions, day=date(2026, 10, 22))  # ± 3 days is the same invoice
    version = _ready(client, line, sessions)
    url = _upload(client, line.id)

    assert _rows(sessions, "procurement") == []
    with sessions() as session:
        (upload,) = session.scalars(select(InvoiceUploadRow)).all()
        (file_row,) = session.scalars(select(InvoiceFileRow).where(InvoiceFileRow.invoice_id == kaiten.id)).all()
    assert (upload.invoice_id, upload.created_row, file_row.kind) == (kaiten.id, False, "invoice")
    (row,) = _rows(sessions, "kaiten")
    assert (row.title, row.doc_date) == ("Карточка Kaiten", date(2026, 10, 22))  # Kaiten's header stays

    recorded = _record(client, line.id, version, url)
    assert client.post(f"/api/procurement/{line.id}/paid", json={"version": recorded["version"]}).status_code == 200
    (row,) = _rows(sessions, "kaiten")
    assert (row.status, row.payment_date, row.payment_sum) == ("paid", date(2026, 9, 15), 24400)


def test_a_kaiten_card_is_never_cancelled_or_replaced_from_procurement(wired):
    client, line, sessions = wired
    kaiten = _kaiten(sessions)
    _ready(client, line, sessions)
    _upload(client, line.id)  # attached to the card, never sent
    _later(5)
    _upload(client, line.id, WRONG_PDF, "чужой.pdf")  # a newer, different invoice for the same line

    assert {row.source: row.status for row in _rows(sessions)} == {"kaiten": "to_pay", "procurement": "to_pay"}
    with sessions() as session, pytest.raises(Exception):
        session.execute(update(InvoiceRow).where(InvoiceRow.id == kaiten.id).values(status="replaced"))
        session.flush()


@pytest.mark.parametrize(("kaiten", "twin"), [
    ({"day": date(2026, 10, 23)}, True),  # 3 days later
    ({"day": date(2026, 10, 24)}, False),  # 4 days — another invoice
    ({"number": "№ 0012"}, False),  # another number (leading zeros count)
    ({"number": "№12"}, True),  # «№», spaces dropped
    ({"inn": "7701234561"}, False),  # both INNs read and they differ: another seller with the same name
    ({"inn": None}, True),  # no INN on the card: the seller's name decides
    ({"inn": None, "seller": 'ООО "Другая Поставка"'}, False),
])
def test_twins_by_seller_number_and_date(wired, kaiten, twin):
    client, line, sessions = wired
    _kaiten(sessions, **kaiten)
    _ready(client, line, sessions)
    _upload(client, line.id)

    assert (len(_rows(sessions, "procurement")) == 0) is twin


def test_a_scan_is_never_a_twin(wired):
    client, line, sessions = wired
    _kaiten(sessions)
    _ready(client, line, sessions)
    _upload(client, line.id, b"\xff\xd8\xff\xe0 scan", "scan.jpg")

    assert len(_rows(sessions, "procurement")) == 1


def test_a_failing_status_sync_never_fails_the_payment(wired, monkeypatch):
    client, line, sessions = wired
    version = _ready(client, line, sessions)
    recorded = _record(client, line.id, version, _upload(client, line.id))

    def broken(*args, **kwargs):
        raise RuntimeError("purchasing is down")

    monkeypatch.setattr(flow, "sync_invoice_status", broken)
    paid = client.post(f"/api/procurement/{line.id}/paid", json={"version": recorded["version"]})

    assert paid.status_code == 200 and paid.json()["line"]["status"] == "В пути"
    assert _rows(sessions)[0].status == "to_pay"
    monkeypatch.undo()
    assert flow.invoice_status_tick(NOW, sessions) == 1 and _rows(sessions)[0].status == "paid"


def test_backfill_takes_the_invoices_lines_carry_dry_run_first(procurement_api, rates):  # noqa: F811
    client, line, sessions = procurement_api
    version = _ready(client, line, sessions)
    _upload(client, line.id, WRONG_PDF, "чужой.pdf")  # uploaded before the registry, never sent: not taken
    url = _upload(client, line.id)  # purchasing not wired: nothing reaches the registry
    recorded = _record(client, line.id, version, url)
    assert client.post(f"/api/procurement/{line.id}/paid", json={"version": recorded["version"]}).status_code == 200
    _kaiten(sessions, number="99")  # an unrelated Kaiten card stays as it is
    procurement, purchasing = _factories(sessions)
    actor = Actor(name="перенос", department="")

    dry = flow.backfill(procurement, purchasing, actor, NOW)
    assert (dry.files, dict(dry.pushed), dict(dry.statuses)) == (1, {"created": 1}, {"paid": 1})
    assert _rows(sessions, "procurement") == []
    report = flow.format_report(dry, applied=False)
    assert "Проверка без записи" in report and "новых счетов: 1" in report

    applied = flow.backfill(procurement, purchasing, actor, NOW, apply=True)
    assert (dict(applied.pushed), dict(applied.statuses)) == ({"created": 1}, {"paid": 1})
    (row,) = _rows(sessions, "procurement")
    assert (row.number, row.status, row.payment_sum, row.external_id) == ("12", "paid", 24400, url.rsplit("/", 1)[1])

    again = flow.backfill(procurement, purchasing, actor, NOW, apply=True)
    assert (dict(again.pushed), dict(again.statuses)) == ({"unchanged": 1}, {"unchanged": 1})
    assert len(_rows(sessions)) == 2


def test_backfill_joins_a_kaiten_twin(procurement_api, rates):  # noqa: F811
    client, line, sessions = procurement_api
    kaiten = _kaiten(sessions)
    version = _ready(client, line, sessions)
    _record(client, line.id, version, _upload(client, line.id))
    procurement, purchasing = _factories(sessions)

    report = flow.backfill(procurement, purchasing, Actor(name="перенос", department=""), NOW, apply=True)

    assert dict(report.pushed) == {"attached": 1} and _rows(sessions, "procurement") == []
    with sessions() as session:
        assert session.scalar(select(InvoiceUploadRow.invoice_id)) == kaiten.id


def test_migration_adds_the_statuses_and_the_upload_links_and_downgrades_cleanly(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "0155_invoice_procurement_source")
    insert = ("INSERT INTO purchasing_invoices (id, source, external_id, title, board_title, column_title, status, "
              "card_created, card_updated, vat_free, payment_numbers, parse_issues, text, lines, synced_at) "
              "VALUES (?, ?, ?, 't', '', '', ?, '2026-10-27', '2026-10-27', 0, '', '', '', '[]', '2026-10-27')")
    link = ("INSERT INTO purchasing_invoice_uploads (id, invoice_id, file_key, order_number, file_name, uploaded_at, "
            "recorded_at, created_row) VALUES (?, ?, ?, 'PO-1', 'f.pdf', '2026-10-27', '2026-10-27', 1)")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(insert, ("00000000000000000000000000000001", "procurement", "f-1", "replaced"))
        connection.execute(insert, ("00000000000000000000000000000002", "kaiten", "card-1", "paid"))
        connection.execute(link, ("00000000000000000000000000000011", "00000000000000000000000000000001", "f-1"))
        connection.execute(link, ("00000000000000000000000000000012", "00000000000000000000000000000002", "f-2"))
        for bad in (
            (insert, ("00000000000000000000000000000003", "kaiten", "card-2", "cancelled")),  # Kaiten keeps Kaiten's
            (insert, ("00000000000000000000000000000004", "procurement", "f-3", "lost")),
            (link, ("00000000000000000000000000000013", "00000000000000000000000000000002", "f-1")),  # one file, one row
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(*bad)
    command.downgrade(config, "0154_user_ui_settings")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT source, status FROM purchasing_invoices").fetchall() == [("kaiten", "paid")]
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "purchasing_invoice_uploads" not in tables
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("00000000000000000000000000000005", "kaiten", "card-3", "replaced"))
    command.upgrade(config, "0155_invoice_procurement_source")  # and up again
