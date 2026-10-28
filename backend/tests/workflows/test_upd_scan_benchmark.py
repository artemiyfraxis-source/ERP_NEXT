"""`python -m app.workflows.upd_pdf_parse --benchmark-scans N` (27.10.2026): the last warehouse scans read anew by the
document reader and compared with their stored readings — field by field, the model's failures printed, nothing
written. The model is a fake `urlopen`; the scans are blank pictures, the answers made up (no real ИНН)."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.warehouse import receipt_ocr, scan_benchmark
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, WarehouseReceiptFileRow
from app.modules.warehouse.receipt_scan import RecognisedUpd
from app.modules.warehouse.scan_reading import ScanReading, reading_json
from app.modules.warehouse.upd_lines import item_line
from app.shared.doc_reader.transport import LlmConfig
from app.shared.persistence import create_schema
from app.workflows import upd_pdf_parse as flow

KEY = "sk-test-0123456789-secret-AbCd"
CONFIG = LlmConfig(base_url="https://llm.example.test/v1", api_key=KEY, model="main", fallback_model="spare")
NOW = datetime(2026, 10, 27, 9, 0)
PAGE = {
    "docKind": "UPD", "number": "А-17", "date": "2026-10-20", "sellerName": "ООО «Пример Кабель»",
    "sellerInn": "7701234567", "basis": "Счет № 45 от 01.10.2026", "paymentDoc": "№ 916 от 19.10.2026",
    "lines": [
        {"seq": 1, "article": "00-0001", "name": "Лента самоклеющаяся 9мм*16м", "unit": "шт", "qty": 2},
        {"seq": 2, "article": "00-0002", "name": "Кабель UTP 4x2x0,5", "unit": "м", "qty": 305},
    ],
}


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (300, 200), 255).save(buffer, "PNG")
    return buffer.getvalue()


class FakeApi:
    """`urlopen` of the relay: bytes — 200 with that body, int — that HTTP status."""

    def __init__(self, *plan):
        self.plan, self.calls, self.lock = list(plan), 0, threading.Lock()

    def __call__(self, request, timeout):
        with self.lock:
            self.calls += 1
            step = self.plan.pop(0) if self.plan else 500
        if isinstance(step, int):
            raise urllib.error.HTTPError(request.full_url, step, "error", {}, io.BytesIO(f"Bearer {KEY}".encode()))
        return io.BytesIO(step)


def _reply(data: dict) -> bytes:
    return json.dumps({"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}).encode()


def _stored() -> str:
    """A reading kept before the document reader: tesseract, the seller misspelt, a line glued, no ПРД."""
    found = RecognisedUpd(supplier="ООО Пример Кабел", inn="7701234567", upd_number="А-17", upd_date=date(2026, 10, 20),
                          invoice_number="45", invoice_date=date(2026, 10, 1))
    lines = (item_line("Лента самоклеющаяся 9мм*16м", Decimal("2"), "шт", supplier_code="00-0001", number="1"),)
    return reading_json(ScanReading(found, lines, "", "", "tesseract"))


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(receipt_ocr, "page_turn", lambda image: 0)
    monkeypatch.setattr(receipt_ocr, "page_text", lambda image: "")
    yield sessions
    engine.dispose()


def _add(sessions, name: str, recognition: str, minutes: int) -> str:
    file_id = uuid4()
    content = _png()
    with sessions() as session:
        session.add(WarehouseReceiptFileRow(
            id=file_id, file_name=name, content_type="image/png", size=len(content), content=content,
            uploaded_by="Кладовщик", uploaded_at=NOW + timedelta(minutes=minutes), recognition=recognition))
        session.commit()
    return str(file_id)


def _recognitions(sessions) -> dict:
    with sessions() as session:
        return {str(row.id): row.recognition for row in session.scalars(select(WarehouseReceiptFileRow))}


def test_the_last_scans_are_read_anew_and_compared_field_by_field_writing_nothing(db, monkeypatch, capsys):
    _add(db, "old.png", _stored(), minutes=0)
    _add(db, "not-read.png", "", minutes=5)  # never read: not a benchmark file
    before = _recognitions(db)
    fake = FakeApi(_reply(PAGE))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    report = flow.benchmark_scans(db, lambda: SqlAlchemyWarehouseUnitOfWork(db), CONFIG, 5)
    out = capsys.readouterr().out
    assert fake.calls == 1, "one page, one call"
    assert report["files"] == {"прочитано": 1, "другое число строк": 1}
    assert report["header"] == {"supplier": 1, "prd_number": 1, "prd_date": 1}
    assert "продавец: «ООО Пример Кабел» → «ООО «Пример Кабель»»" in out and "ПРД №: — → «916»" in out
    assert "строк: 1 → 2" in out and "стр. 2 наименование: — → «Кабель UTP 4x2x0,5»" in out
    assert "было tesseract → стало main" in out
    assert _recognitions(db) == before, "the stored readings stay as they were"


def test_a_model_that_does_not_answer_is_printed_and_the_run_goes_on(db, monkeypatch, capsys):
    _add(db, "a.png", _stored(), minutes=0)
    _add(db, "b.png", "{broken", minutes=1)
    monkeypatch.setattr(urllib.request, "urlopen", FakeApi(500, 500, 401))
    report = flow.benchmark_scans(db, lambda: SqlAlchemyWarehouseUnitOfWork(db), CONFIG, 5)
    out = capsys.readouterr().out
    assert report["files"]["пропущено"] == 1 and report["files"]["прочитано"] == 1
    assert report["files"]["без модели (запасной способ)"] == 1
    assert "! Модель распознавания не ответила" in out and "HTTP 401" in out and KEY not in out


def test_a_reader_that_crashes_and_the_run_budget_stop_nothing_else(db, capsys):
    for minute in range(3):
        _add(db, f"{minute}.png", _stored(), minutes=minute)
    ticks = iter([0.0, 0.0, 10.0, 99.0, 99.0])  # start, a check before each file, the summary

    def crash(content, kind):
        raise ValueError("сломалось")

    with db() as session:
        scans = scan_benchmark.recent_scans(session, 10)
    assert [scan.file_name for scan in scans] == ["2.png", "1.png", "0.png"], "newest first"
    report = scan_benchmark.run(scans, crash, budget=50, clock=lambda: next(ticks))
    assert report["files"] == {"ошибка": 2, "не прочитано: время": 1}
    assert "ValueError: сломалось" in capsys.readouterr().out


def test_the_read_only_session_refuses_any_write(db):
    with pytest.raises(flow.ReadOnly):
        with flow.read_only(db) as session:
            session.add(WarehouseReceiptFileRow(
                id=uuid4(), file_name="x", content_type="image/png", size=1, content=b"x", uploaded_by="x",
                uploaded_at=NOW))
            session.flush()
    assert _recognitions(db) == {}
