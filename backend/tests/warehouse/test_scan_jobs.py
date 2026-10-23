"""«Отсканировать УПД» со сканера склада (owner 23.10.2026): the button queues a scan job, the agent of the warehouse PC
(API token `scan:upload`) claims it, scans on the Pantum and uploads the file; the screen polls the job and gets the
same answer as a hand-uploaded scan. A job nobody answers fails on its own, the storekeeper can cancel it, only the
agent takes jobs. A scan without a text layer is read by OCR (tesseract), and a server without it says so."""

import io
import json
import shutil
import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app.main import app
from app.modules.warehouse import receipt_ocr
from app.modules.warehouse.infrastructure import WarehouseReceiptFileRow
from app.modules.warehouse.receipt_scan import NO_TEXT, OCR_MISSING, PARTLY, parse_upd_text, recognise_upd
from app.modules.warehouse.scan_jobs import (
    CANCELLED, CLAIMED_TIMEOUT, NO_ANSWER, NO_REASON, PENDING_TIMEOUT, ScanJobRow,
)
from app.modules.warehouse.scan_jobs_api import SCAN_AGENT_ONLY
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401  (fixture)
from tests.migrations.seed_any_revision import seed
from tests.pdf_samples import identity_pdf

JOBS = "/api/stock/receipts/scan-jobs"
CLAIM = "/api/stock/scan/agent/claim"
UPD_TEXT = [
    "Счет-фактура № 123 от 20.10.2026 (1)",
    "Продавец: ООО «Ромашка Электро» (2)",
    "ИНН/КПП продавца: 7701234567/770101001 (2б)",
    "Основание передачи: Счёт № СЧ-45 от 01.10.2026",
]
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f80f00000101000518d84e0000000049454e44ae426082"
)
# What tesseract makes of a paper УПД: «№» as «N°» / «Ne», a long dash, Latin «C» and «o», a comma in a date, table
# rules and a marker «(2]».
OCR_UPD = """Статус: 1 | Cчет—фактура N° 992177 oт 25.09,2026 (1)
Исправление Ne -- от -- (1а)
| Продавец: ООО "Элитан Трейд" (2] |
Адрес: 125124, г. Москва, ул. Правды, д. 26 (2а)
ИНН/КПП продавца: 7728168971 / 772801001 (2б)
Документ об отгрузке Ne п/п 1-3 № 992177 от 25.09.2026 (5а)
Основание передачи (сдачи) / получения (приемки): Счет на оплату N 45811 от 22 сентября 2026 г. [8]
"""


def _result(job_id: str) -> str:
    return f"/api/stock/scan/agent/jobs/{job_id}/result"


@pytest.fixture()
def queue(tokens_api, monkeypatch):  # noqa: F811
    sessions, clock = tokens_api
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "")  # no real OCR unless a test asks for it
    admin = _login("admin")
    agent = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="Агент склада")["token"])
    return admin, agent, clock, sessions


def _new_job(admin: TestClient) -> str:
    response = admin.post(JOBS, headers=_csrf(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    return body["id"]


def _job(admin: TestClient, job_id: str) -> dict:
    response = admin.get(f"{JOBS}/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _claim(agent: dict) -> str | None:
    response = TestClient(app).post(CLAIM, headers=agent)
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    return job["id"] if job else None


def _upload(agent: dict, job_id: str, name: str, content: bytes, kind: str):
    return TestClient(app).post(_result(job_id), files={"file": (name, content, kind)}, headers=agent)


# --- schema -----------------------------------------------------------------------------------------------------


def test_scan_jobs_migration_is_followed_only_by_the_article_split():
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_revision("0146_split_article_maker").down_revision == "0145_warehouse_scan_jobs"


def test_database_holds_done_with_a_file_and_only_done(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "head")
    file_id = "f" * 32
    insert = ("INSERT INTO warehouse_scan_jobs (id, status, requested_by, created_at, claimed_at, finished_at, file_id,"
              " error) VALUES (?, ?, 'Кладовщик', '2026-10-23 12:00', ?, ?, ?, ?)")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO warehouse_receipt_files (id, file_name, content_type, size, content, uploaded_by, uploaded_at)"
            " VALUES (?, 'УПД.pdf', 'application/pdf', 3, x'255044', 'Кладовщик', '2026-10-23 12:00')", (file_id,))
        connection.execute(insert, ("1" * 32, "pending", None, None, None, ""))
        connection.execute(insert, ("2" * 32, "done", "2026-10-23 12:00", "2026-10-23 12:01", file_id, ""))
        connection.execute(insert, ("3" * 32, "failed", None, "2026-10-23 12:01", None, "Отменено"))
        refused = [
            ("4" * 32, "pending", None, None, file_id, ""),  # a file on an open job
            ("5" * 32, "done", "2026-10-23 12:00", "2026-10-23 12:01", None, ""),  # done without a file
            ("6" * 32, "failed", None, "2026-10-23 12:01", None, ""),  # failed without a reason
            ("7" * 32, "claimed", None, None, None, ""),  # claimed without a moment
            ("8" * 32, "printed", None, None, None, ""),
        ]
        for row in refused:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                connection.execute(insert, row)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM warehouse_receipt_files")
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute(
                "INSERT INTO warehouse_scan_jobs (id, status, requested_by, created_at) VALUES (?, 'pending', NULL, 'x')",
                ("9" * 32,))
    command.downgrade(config, "0144_warehouse_receipts")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "warehouse_scan_jobs" not in tables and "warehouse_receipt_files" in tables


def test_the_ci_seeder_fills_the_scan_queue_once_it_stands_one_revision_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        assert seed(engine).get("warehouse_scan_jobs")
    finally:
        engine.dispose()


# --- the queue --------------------------------------------------------------------------------------------------


def test_button_agent_file_and_the_answer_of_a_hand_uploaded_scan(queue):
    admin, agent, clock, sessions = queue
    job_id = _new_job(admin)
    assert _job(admin, job_id) == {"id": job_id, "status": "pending", "error": "", "result": None}

    assert _claim(agent) == job_id
    assert _claim(agent) is None, "a claimed job is not handed out twice"
    assert _job(admin, job_id)["status"] == "claimed"

    clock.now += timedelta(seconds=20)
    uploaded = _upload(agent, job_id, "scan.pdf", identity_pdf(UPD_TEXT), "application/pdf")
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json() == {"ok": True, "id": job_id, "status": "done"}

    done = _job(admin, job_id)
    assert (done["status"], done["error"]) == ("done", "")
    result = done["result"]
    assert result["recognised"] == {
        "supplier": "ООО «Ромашка Электро»", "inn": "7701234567", "upd_number": "123", "upd_date": "2026-10-20",
        "invoice_number": "СЧ-45", "invoice_date": "2026-10-01",
    }
    assert result["lines"] == [] and result["note"] == ""
    assert result["scan_file"].startswith("/api/stock/receipts/files/")
    served = admin.get(result["scan_file"])
    assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
    by_hand = admin.post("/api/stock/receipts/scan", headers=_csrf(admin),
                         files={"file": ("упд.pdf", identity_pdf(UPD_TEXT), "application/pdf")}).json()
    assert set(result) == set(by_hand) and result["recognised"] == by_hand["recognised"]

    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job_id))
        assert json.loads(row.result) == result, "the answer is made once and stored"
        stored = session.get(WarehouseReceiptFileRow, row.file_id)
        assert (stored.uploaded_by, stored.file_name) == ("Admin", "scan.pdf"), "the scan is the storekeeper's"
    assert _job(admin, job_id)["result"] == result

    again = _upload(agent, job_id, "scan.pdf", identity_pdf(UPD_TEXT), "application/pdf")
    assert again.status_code == 409 and again.json()["detail"] == "Задание сканирования уже закрыто"
    with sessions() as session:
        assert len(session.scalars(select(WarehouseReceiptFileRow)).all()) == 2, "the retry stored nothing"


def test_a_flatbed_jpeg_of_nine_megabytes_reaches_the_queue(queue):
    """The Pantum on USB gives ~9 MB JPEGs (23.10): the agent's upload is not cut by the 1 MB default body limit."""
    admin, agent, _clock, _sessions = queue
    job_id = _new_job(admin)
    assert _claim(agent) == job_id
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * (9 * 1_048_576) + b"\xff\xd9"
    uploaded = _upload(agent, job_id, "scan.jpg", jpeg, "image/jpeg")
    assert uploaded.status_code == 200, uploaded.text
    assert _job(admin, job_id)["status"] == "done"
    assert _upload(agent, str(uuid4()), "scan.pdf", identity_pdf(UPD_TEXT), "application/pdf").status_code == 404
    assert admin.get(f"{JOBS}/{uuid4()}").status_code == 404


def test_jobs_are_handed_out_oldest_first(queue):
    admin, agent, clock, _sessions = queue
    first = _new_job(admin)
    clock.now += timedelta(seconds=1)
    second = _new_job(admin)
    assert [_claim(agent), _claim(agent), _claim(agent)] == [first, second, None]


def test_the_agent_reports_why_it_could_not_scan(queue):
    admin, agent, _clock, _sessions = queue
    job_id = _new_job(admin)
    service = TestClient(app)
    never_claimed = service.post(_result(job_id), json={"error": "Нет бумаги"}, headers=agent)
    assert never_claimed.status_code == 409 and never_claimed.json()["detail"] == "Задание сканирования не выдано агенту"

    _claim(agent)
    assert service.post(_result(job_id), content=b"nothing", headers=agent).status_code == 422
    assert _job(admin, job_id)["status"] == "claimed", "a request that says nothing changes nothing"
    reported = service.post(_result(job_id), json={"error": "  Сканер \n занят "}, headers=agent)
    assert reported.status_code == 200 and reported.json()["status"] == "failed"
    assert _job(admin, job_id) == {"id": job_id, "status": "failed", "error": "Сканер занят", "result": None}
    assert service.post(_result(job_id), json={"error": "x"}, headers=agent).status_code == 409

    silent = _new_job(admin)
    _claim(agent)
    assert service.post(_result(silent), json={"error": ""}, headers=agent).status_code == 200
    assert _job(admin, silent)["error"] == NO_REASON


def test_a_file_erp_does_not_keep_fails_the_job(queue):
    admin, agent, _clock, _sessions = queue
    job_id = _new_job(admin)
    _claim(agent)
    response = _upload(agent, job_id, "x.html", b"<html></html>", "text/html")
    assert response.status_code == 422 and response.json()["detail"] == "Скан УПД — файл PDF, JPG или PNG"
    assert _job(admin, job_id)["error"] == "Скан УПД — файл PDF, JPG или PNG"


def test_a_job_nobody_answers_fails_on_its_own(queue):
    admin, agent, clock, sessions = queue
    waiting = _new_job(admin)
    clock.now += PENDING_TIMEOUT - timedelta(seconds=1)
    assert _job(admin, waiting)["status"] == "pending"
    clock.now += timedelta(seconds=2)
    assert _job(admin, waiting) == {"id": waiting, "status": "failed", "error": NO_ANSWER, "result": None}
    assert NO_ANSWER == "Сканер не ответил — проверьте агент на ПК склада"
    with sessions() as session:
        assert session.get(ScanJobRow, UUID(waiting)).status == "failed", "persisted"

    late = _new_job(admin)
    clock.now += PENDING_TIMEOUT + timedelta(seconds=1)
    assert _claim(agent) is None, "the agent never scans for a storekeeper who gave up"
    assert _job(admin, late)["error"] == NO_ANSWER

    taken = _new_job(admin)
    assert _claim(agent) == taken
    clock.now += CLAIMED_TIMEOUT + timedelta(seconds=1)
    assert _job(admin, taken)["error"] == NO_ANSWER
    late_file = _upload(agent, taken, "scan.pdf", identity_pdf(UPD_TEXT), "application/pdf")
    assert late_file.status_code == 409


def test_the_storekeeper_cancels_a_job(queue):
    admin, agent, _clock, _sessions = queue
    job_id = _new_job(admin)
    cancelled = admin.delete(f"{JOBS}/{job_id}", headers=_csrf(admin))
    assert cancelled.status_code == 200 and cancelled.json() == {"id": job_id, "status": "failed"}
    assert _job(admin, job_id)["error"] == CANCELLED == "Отменено"
    assert _claim(agent) is None
    assert admin.delete(f"{JOBS}/{job_id}", headers=_csrf(admin)).json()["status"] == "failed"

    taken = _new_job(admin)
    _claim(agent)
    admin.delete(f"{JOBS}/{taken}", headers=_csrf(admin))
    assert _upload(agent, taken, "scan.pdf", identity_pdf(UPD_TEXT), "application/pdf").status_code == 409
    assert admin.delete(f"{JOBS}/{uuid4()}", headers=_csrf(admin)).status_code == 404


def test_only_the_agent_takes_jobs_and_only_a_person_queues_them(queue):
    admin, agent, _clock, _sessions = queue
    job_id = _new_job(admin)
    refused = admin.post(CLAIM, headers=_csrf(admin))
    assert refused.status_code == 403 and refused.json()["detail"] == SCAN_AGENT_ONLY
    labels_only = _bearer(_issue(admin, scopes=("labels:print",), name="Только печать")["token"])
    service = TestClient(app)
    assert service.post(CLAIM, headers=labels_only).status_code == 403
    assert service.post(_result(job_id), json={"error": "x"}, headers=labels_only).status_code == 403
    scan_only = _bearer(_issue(admin, scopes=("scan:upload",), name="Только скан")["token"])
    assert service.post("/api/stock/labels/agent/claim", json={}, headers=scan_only).status_code == 403
    assert service.post(CLAIM, headers=scan_only).json()["job"]["id"] == job_id

    assert service.post(JOBS, headers=agent).status_code == 403
    assert service.get(f"{JOBS}/{job_id}", headers=agent).status_code == 403
    assert service.delete(f"{JOBS}/{job_id}", headers=agent).status_code == 403
    assert service.post("/api/stock/receipts/scan", files={"file": ("x.png", PNG, "image/png")},
                        headers=agent).status_code == 403


def test_a_picture_on_a_server_without_tesseract_asks_to_type_the_header(queue, monkeypatch):
    admin, agent, _clock, _sessions = queue
    monkeypatch.setattr(receipt_ocr, "ocr_text", _original_ocr_text)
    monkeypatch.setattr(receipt_ocr.shutil, "which", lambda name: None)
    job_id = _new_job(admin)
    _claim(agent)
    assert _upload(agent, job_id, "scan.png", PNG, "image/png").status_code == 200
    result = _job(admin, job_id)["result"]
    assert result["note"] == OCR_MISSING == "Распознавание недоступно на сервере — заполните вручную"
    assert result["recognised"]["upd_number"] == "" and result["scan_file"].startswith("/api/stock/receipts/files/")


# --- OCR --------------------------------------------------------------------------------------------------------

_original_ocr_text = receipt_ocr.ocr_text


class FakePrograms:
    """`subprocess.run` of tesseract / pdftoppm: pdftoppm writes two page pictures, tesseract prints `text`."""

    def __init__(self, text: str):
        self.text = text
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, arguments, **options):
        self.calls.append((list(arguments), options))
        if arguments[0].endswith("pdftoppm"):
            prefix = Path(arguments[-1])
            for page in (1, 2):
                prefix.with_name(f"{prefix.name}-{page}.png").write_bytes(PNG)
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        assert Path(arguments[1]).read_bytes()[:4] == PNG[:4]
        return subprocess.CompletedProcess(arguments, 0, self.text.encode(), b"")


@pytest.fixture()
def programs(monkeypatch):
    monkeypatch.setattr(receipt_ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakePrograms(OCR_UPD)
    monkeypatch.setattr(receipt_ocr.subprocess, "run", fake)
    return fake


def test_a_picture_is_read_by_tesseract_in_russian_and_english(programs):
    found, note = recognise_upd(PNG, "image/png")
    assert (found.upd_number, found.upd_date, found.supplier) == ("992177", date(2026, 9, 25), 'ООО "Элитан Трейд"')
    assert (found.inn, found.invoice_number, found.invoice_date) == ("7728168971", "45811", date(2026, 9, 22))
    assert note == ""
    [(arguments, options)] = programs.calls
    assert arguments[0] == "/usr/bin/tesseract" and arguments[2:] == ["stdout", "-l", "rus+eng", "--psm", "6"]
    assert options["timeout"] == 60


def test_a_pdf_without_text_is_rasterised_two_pages_at_300_dpi(programs):
    found, note = recognise_upd(identity_pdf([]), "application/pdf")
    assert found.upd_number == "992177" and note == ""
    pdftoppm, *tesseract = [arguments for arguments, _ in programs.calls]
    assert pdftoppm[:8] == ["/usr/bin/pdftoppm", "-r", "300", "-f", "1", "-l", "2", "-png"]
    assert len(tesseract) == 2


def test_a_pdf_with_a_text_layer_is_not_sent_to_ocr(programs):
    found, note = recognise_upd(identity_pdf(UPD_TEXT), "application/pdf")
    assert found.upd_number == "123" and note == "" and programs.calls == []


def test_ocr_that_fails_or_hangs_gives_no_text(monkeypatch):
    monkeypatch.setattr(receipt_ocr.shutil, "which", lambda name: f"/usr/bin/{name}")

    def hang(arguments, **options):
        raise subprocess.TimeoutExpired(arguments, options["timeout"])

    monkeypatch.setattr(receipt_ocr.subprocess, "run", hang)
    assert recognise_upd(PNG, "image/png") == (recognise_upd(b"", "text/html")[0], NO_TEXT)
    monkeypatch.setattr(receipt_ocr.subprocess, "run",
                        lambda arguments, **options: subprocess.CompletedProcess(arguments, 1, b"junk", b"error"))
    assert recognise_upd(PNG, "image/png")[1] == NO_TEXT


def test_ocr_text_that_gives_only_part_of_the_header_says_so(monkeypatch):
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "Продавец: ООО Альфа")
    found, note = recognise_upd(PNG, "image/jpeg")
    assert found.supplier == "ООО Альфа" and note == PARTLY


def test_upd_patterns_take_ocr_noise():
    found = parse_upd_text(OCR_UPD)
    assert found.supplier == 'ООО "Элитан Трейд"'
    assert (found.upd_number, found.upd_date) == ("992177", date(2026, 9, 25))
    assert (found.invoice_number, found.invoice_date) == ("45811", date(2026, 9, 22))
    # The first line lost — «Документ об отгрузке» still gives the УПД; a 12-digit ИНН of an ИП is kept whole.
    shipment = parse_upd_text("Продавец: ИП Иванов И.И. (2)\nИНН/КПП продавца: 772801234567\n"
                              "Документ об отгрузке № п/п 1-3, № А-77 от 5 октября 2026 г. (5а)")
    assert (shipment.upd_number, shipment.upd_date) == ("А-77", date(2026, 10, 5))
    assert (shipment.supplier, shipment.inn) == ("ИП Иванов И.И.", "772801234567")
    # «Счет —фактура» with a dash of any length is not taken for the invoice.
    assert parse_upd_text("Счет —фактура № 5 от 01.10.2026").invoice_number == ""


# --- real tesseract (the server image has it; a workstation usually does not) ------------------------------------

FONT = Path(__file__).parents[2] / "app" / "assets" / "fonts" / "DejaVuSans.ttf"


def _page_picture(lines: list[str]):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(FONT), 40)
    image = Image.new("L", (2000, 120 + 80 * len(lines)), 255)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((60, 60 + 80 * index), line, font=font, fill=0)
    return image


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed here")
def test_real_tesseract_reads_a_printed_upd_header(monkeypatch):
    buffer = io.BytesIO()
    _page_picture(UPD_TEXT).save(buffer, format="PNG")
    found, _note = recognise_upd(buffer.getvalue(), "image/png")
    assert (found.upd_number, found.upd_date) == ("123", date(2026, 10, 20))
    assert found.inn == "7701234567"


@pytest.mark.skipif(shutil.which("tesseract") is None or shutil.which("pdftoppm") is None,
                    reason="tesseract / pdftoppm are not installed here")
def test_real_ocr_reads_a_scanned_pdf():
    buffer = io.BytesIO()
    _page_picture(UPD_TEXT).save(buffer, format="PDF", resolution=150)
    found, _note = recognise_upd(buffer.getvalue(), "application/pdf")
    assert (found.upd_number, found.upd_date) == ("123", date(2026, 10, 20))
