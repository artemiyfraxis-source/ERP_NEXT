"""«Принтеры склада» in «Оборудование» (owner 20.11.2026): the МФУ card scans plainly (never read as a УПД, never in the
УПД lists) and prints PDFs through the agent of its PC; the label printer card lists the TSC queue. Migration 0214."""

import base64
import hashlib
import io
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.main import app
from app.modules.warehouse import receipt_ocr, scan_jobs_api
from app.modules.warehouse.devices import DeviceRow, PrinterRow
from app.modules.warehouse.document_print import LEASE, NO_ANSWER, DocumentPrintJobRow
from app.modules.warehouse.scan_jobs import ScanJobRow
from app.workflows.warehouse_stations import model_of, stations
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401  (fixture)

PC = "mfp-pc"
SCANNER = "{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}\\0001"
MFP = "Pantum BM2300 Series"
HEARTBEAT = "/api/stock/scan/agent/heartbeat"
SCAN_CLAIM = "/api/stock/scan/agent/claim"
DOC_CLAIM = "/api/stock/documents/agent/claim"
CARD = f"/api/stock/devices/mfp/{PC}"


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(595, 842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture()
def stand(tokens_api, monkeypatch):  # noqa: F811
    sessions, clock = tokens_api
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "")
    read: list = []
    monkeypatch.setattr(scan_jobs_api, "recognise_job", lambda *args, **kwargs: read.append(args))
    admin = _login("admin")
    agent = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="Агент склада")["token"])
    agent["X-Agent-Machine-Id"] = PC
    beat(agent)
    return admin, agent, clock, sessions, read


def beat(agent: dict, printers: list[dict] | None = None, version: str = "2.12.0") -> None:
    response = TestClient(app).post(HEARTBEAT, headers=agent, json={
        "machine_id": PC, "pc_name": "DESKTOP-F40DLD9", "version": version,
        "scanners": [{"id": SCANNER, "name": "Pantum BM2300 Series Scanner"}],
        "printers": printers if printers is not None else [
            {"name": "TSC MB341", "kind": "tspl", "driver": "TSC MB341", "port": "USB001"},
            {"name": MFP, "kind": "document", "driver": "Pantum BM2300 Series", "port": "USB002"},
        ],
    })
    assert response.status_code == 200, response.text


def _scan(admin: TestClient, **fields) -> dict:
    response = admin.post(f"{CARD}/scan", headers=_csrf(admin), json={"scanner": SCANNER, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def _claim_scan(agent: dict) -> dict | None:
    response = TestClient(app).post(SCAN_CLAIM, headers=agent, json={"machine_id": PC, "kinds": ["scan", "vna"]})
    assert response.status_code == 200, response.text
    return response.json()["job"]


def _send_scan(agent: dict, job_id: str, content: bytes):
    return TestClient(app).post(f"/api/stock/scan/agent/jobs/{job_id}/result", headers=agent,
                                files={"file": ("scan.pdf", content, "application/pdf")})


def _print(admin: TestClient, content: bytes, copies: int = 1, printer: str = MFP, name: str = "Паспорт.pdf"):
    return admin.post(f"{CARD}/print", headers=_csrf(admin), params={"printer": printer, "copies": copies},
                      files={"file": (name, content, "application/pdf")})


def _claim_doc(agent: dict, printers=(MFP,)) -> dict | None:
    response = TestClient(app).post(DOC_CLAIM, headers=agent, json={"machine_id": PC, "printers": list(printers)})
    assert response.status_code == 200, response.text
    return response.json()["job"]


def _report(agent: dict, job: dict, outcome: str = "spooled", message: str = ""):
    return TestClient(app).post(f"/api/stock/documents/agent/jobs/{job['id']}/result", headers=agent,
                                json={"machine_id": PC, "attempt": job["attempt"], "outcome": outcome, "message": message})


# --- plain scans ------------------------------------------------------------------------------------------------


def test_a_plain_scan_carries_its_options_is_never_read_and_never_reaches_the_upd_lists(stand):
    admin, agent, clock, sessions, read = stand
    job = _scan(admin, color="color", dpi=600)
    assert (job["status"], job["color"], job["dpi"], job["file"]) == ("pending", "color", 600, "")
    claimed = _claim_scan(agent)
    assert claimed["id"] == job["id"] and claimed["target_scanner"] == SCANNER
    assert claimed["settings"] == {"color": "color", "dpi": 600}, "the agent scans with what the person chose"

    clock.now += timedelta(seconds=5)
    sent = _send_scan(agent, job["id"], _pdf())
    assert sent.status_code == 200, sent.text
    done = admin.get(f"/api/stock/devices/scans/{job['id']}").json()
    assert (done["status"], done["pages"], done["by"]) == ("done", 1, "Admin")
    served = admin.get(done["file"])
    assert served.status_code == 200 and served.content.startswith(b"%PDF-")
    assert read == [], "a plain scan is never sent to the model"
    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job["id"]))
        assert (row.purpose, row.result) == ("plain", "")
    assert admin.get(f"/api/stock/receipts/scan-jobs/{job['id']}").status_code == 404, "not a УПД job"
    assert admin.delete(f"/api/stock/receipts/scan-jobs/{job['id']}", headers=_csrf(admin)).status_code == 404
    card = admin.get(CARD).json()
    assert [scan["id"] for scan in card["scans"]] == [job["id"]]
    assert card["online"] and card["printers"] == [{"name": MFP, "driver": "Pantum BM2300 Series", "present": True}]


def test_the_next_sheet_joins_the_scan_it_continues_into_one_pdf(stand):
    admin, agent, _clock, _sessions, _read = stand
    first = _scan(admin)
    assert _claim_scan(agent)["settings"] == {"color": "gray", "dpi": 300}
    assert _send_scan(agent, first["id"], _pdf(2)).status_code == 200
    second = _scan(admin, append_to=first["id"])
    _claim_scan(agent)
    assert _send_scan(agent, second["id"], _pdf()).status_code == 200
    joined = admin.get(f"/api/stock/devices/scans/{second['id']}").json()
    assert joined["pages"] == 3 and joined["append_to"] == first["id"]
    assert len(PdfReader(io.BytesIO(admin.get(joined["file"]).content)).pages) == 3
    assert [scan["id"] for scan in admin.get(CARD).json()["scans"]] == [second["id"]], "the shorter PDF is folded in"


def test_a_scan_needs_a_live_scanner_of_that_pc_and_the_scan_right(stand):
    admin, agent, clock, _sessions, _read = stand
    refused = admin.post(f"{CARD}/scan", headers=_csrf(admin), json={"scanner": "другой", "color": "gray", "dpi": 300})
    assert refused.status_code == 422 and "не на связи" in refused.json()["detail"]
    assert admin.post(f"{CARD}/scan", headers=_csrf(admin), json={"scanner": SCANNER, "dpi": 1200}).status_code == 422
    stranger = admin.post(f"{CARD}/scan", headers=_csrf(admin), json={"scanner": SCANNER, "append_to": str(uuid4())})
    assert stranger.status_code == 422 and "не найден" in stranger.json()["detail"]
    operator = _login("operator")
    assert operator.get(CARD).status_code == 403
    assert operator.post(f"{CARD}/scan", headers=_csrf(operator), json={"scanner": SCANNER}).status_code == 403
    clock.now += timedelta(minutes=30)
    late = admin.post(f"{CARD}/scan", headers=_csrf(admin), json={"scanner": SCANNER})
    assert late.status_code == 422, "the PC has not reported for half an hour"


def test_a_plain_scan_over_the_limit_fails_at_once(stand, monkeypatch):
    admin, agent, _clock, _sessions, _read = stand
    from app.modules.warehouse import plain_scans

    monkeypatch.setattr(plain_scans, "SCAN_FILE_LIMIT", 300)
    job = _scan(admin)
    _claim_scan(agent)
    assert _send_scan(agent, job["id"], _pdf(3)).status_code == 422
    failed = admin.get(f"/api/stock/devices/scans/{job['id']}").json()
    assert failed["status"] == "failed" and "20 МБ" in failed["error"]


# --- document printing ------------------------------------------------------------------------------------------


def test_a_pdf_goes_to_the_mfp_once_and_the_agent_reports_it(stand):
    admin, agent, _clock, sessions, _read = stand
    content = _pdf(2)
    queued = _print(admin, content, copies=3)
    assert queued.status_code == 200, queued.text
    job = queued.json()
    assert (job["status"], job["pages"], job["copies"], job["title"], job["printer"]) == ("pending", 2, 3, "Паспорт.pdf", MFP)
    assert _claim_doc(agent, printers=("Другой принтер",)) is None, "only the job's own queue takes it"
    taken = _claim_doc(agent)
    assert taken["id"] == job["id"] and taken["copies"] == 3 and taken["pages"] == 2
    assert base64.b64decode(taken["pdf_b64"]) == content and taken["sha256"] == hashlib.sha256(content).hexdigest()
    assert _claim_doc(agent) is None, "a taken job is never handed out again"
    assert admin.get(f"/api/stock/devices/prints/{job['id']}").json()["status"] == "taken"

    stale = _report(agent, {**taken, "attempt": str(uuid4())})
    assert stale.status_code == 409
    assert _report(agent, taken).json() == {"ok": True, "status": "spooled"}
    assert _report(agent, taken).json()["status"] == "spooled", "a repeated receipt is idempotent"
    assert _report(agent, taken, "failed", "Бумага").status_code == 409
    assert admin.get(CARD).json()["prints"][0]["status"] == "spooled"
    with sessions() as session:
        assert session.scalar(select(DocumentPrintJobRow.finished_at).where(DocumentPrintJobRow.id == UUID(job["id"])))


def test_another_agent_and_a_person_cannot_take_or_answer_print_jobs(stand):
    admin, agent, _clock, _sessions, _read = stand
    _print(admin, _pdf())
    assert admin.post(DOC_CLAIM, headers=_csrf(admin), json={"machine_id": PC, "printers": [MFP]}).status_code == 403
    other = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="Другой ПК")["token"])
    other["X-Agent-Machine-Id"] = "other-pc"
    assert TestClient(app).post(DOC_CLAIM, headers=other, json={"machine_id": PC, "printers": [MFP]}).status_code == 403
    taken = _claim_doc(agent)
    answer = TestClient(app).post(f"/api/stock/documents/agent/jobs/{taken['id']}/result", headers=other,
                                  json={"machine_id": PC, "attempt": taken["attempt"], "outcome": "spooled"})
    assert answer.status_code == 403


def test_what_the_printer_cannot_take_is_refused_before_the_queue(stand):
    admin, _agent, _clock, sessions, _read = stand
    assert _print(admin, _pdf(), copies=51).status_code == 422
    assert _print(admin, _pdf(), copies=0).status_code == 422
    sheets = _print(admin, _pdf(11), copies=50)
    assert sheets.status_code == 422 and "500 листов" in sheets.json()["detail"]
    text = admin.post(f"{CARD}/print", headers=_csrf(admin), params={"printer": MFP},
                      files={"file": ("a.txt", b"hello", "text/plain")})
    assert text.status_code == 422 and "PDF, JPG или PNG" in text.json()["detail"]
    unknown = _print(admin, _pdf(), printer="Нет такого")
    assert unknown.status_code == 422 and "не на связи" in unknown.json()["detail"]
    assert _login("operator").post(f"{CARD}/print-test", json={"printer": MFP}).status_code in (403, 401)
    with sessions() as session:
        assert session.scalars(select(DocumentPrintJobRow)).all() == []


def test_an_unanswered_job_becomes_unknown_and_a_late_receipt_still_counts(stand):
    admin, agent, clock, _sessions, _read = stand
    job = _print(admin, _pdf()).json()
    taken = _claim_doc(agent)
    clock.now += LEASE + timedelta(seconds=1)
    unknown = admin.get(f"/api/stock/devices/prints/{job['id']}").json()
    assert (unknown["status"], unknown["error"]) == ("unknown", NO_ANSWER)
    assert _claim_doc(agent) is None, "never printed twice"
    assert _report(agent, taken, "failed", "Нет бумаги").json()["status"] == "failed"
    assert admin.get(f"/api/stock/devices/prints/{job['id']}").json()["error"] == "Нет бумаги"


def test_a_pending_job_can_be_cancelled_and_then_is_never_taken(stand):
    admin, agent, _clock, _sessions, _read = stand
    job = _print(admin, _pdf()).json()
    cancelled = admin.delete(f"/api/stock/devices/prints/{job['id']}", headers=_csrf(admin)).json()
    assert cancelled["status"] == "cancelled"
    assert _claim_doc(agent) is None
    assert admin.delete(f"/api/stock/devices/prints/{uuid4()}", headers=_csrf(admin)).status_code == 404


def test_test_page_and_a_copy_of_a_scan_go_to_the_same_queue(stand):
    admin, agent, clock, _sessions, _read = stand
    test = admin.post(f"{CARD}/print-test", headers=_csrf(admin), json={"printer": MFP})
    assert test.status_code == 200, test.text
    assert (test.json()["title"], test.json()["source"], test.json()["pages"]) == ("Пробная страница", "test", 1)
    clock.now += timedelta(seconds=1)
    scan = _scan(admin)
    _claim_scan(agent)
    _send_scan(agent, scan["id"], _pdf(2))
    copy = admin.post(f"/api/stock/devices/scans/{scan['id']}/print", headers=_csrf(admin), json={"printer": MFP, "copies": 2})
    assert copy.status_code == 200, copy.text
    assert (copy.json()["source"], copy.json()["pages"], copy.json()["copies"]) == ("scan", 2, 2)
    assert [item["source"] for item in admin.get(CARD).json()["prints"]] == ["scan", "test"]


def test_the_printer_queue_must_be_reported_now(stand):
    admin, agent, _clock, _sessions, _read = stand
    beat(agent, printers=[{"name": "TSC MB341", "kind": "tspl"}])  # agent before 2.12.0: no document printer
    assert _print(admin, _pdf()).status_code == 422
    assert admin.get(CARD).json()["printers"] == [{"name": MFP, "driver": "Pantum BM2300 Series", "present": False}]


# --- label printer card ----------------------------------------------------------------------------------------


def test_the_label_printer_card_lists_recent_tsc_jobs(stand):
    admin, _agent, _clock, _sessions, _read = stand
    assert admin.post("/api/stock/labels/test", headers=_csrf(admin)).status_code == 200
    card = admin.get("/api/stock/devices/labels").json()
    assert card["agent"]["queue"] == 1
    job = card["jobs"][0]
    assert (job["what"], job["copies"], job["by"], job["status"], job["lot_id"]) == ("Пробная этикетка", 1, "Admin", "pending", None)


# --- tiles ------------------------------------------------------------------------------------------------------


def test_model_names_drop_the_noise_words():
    assert model_of("Pantum BM2300 Series Scanner") == "Pantum BM2300"
    assert model_of("TSC MB341") == "TSC MB341"
    assert model_of("Scanner") == "Scanner"


def test_the_mfp_is_one_tile_named_by_its_model_and_the_tsc_names_itself(stand):
    _admin, _agent, _clock, sessions, _read = stand
    rows = [
        {"key": f"device:{PC}:scan:{SCANNER}", "kind": "device", "inventory": "", "name": "Сканер",
         "device": "Pantum BM2300 Series Scanner"},
        {"key": f"printer:{PC}:{MFP}", "kind": "printer", "inventory": "", "name": "Принтер", "device": MFP},
        {"key": f"printer:{PC}:TSC MB341", "kind": "printer", "inventory": "", "name": "Принтер этикеток",
         "device": "TSC MB341"},
        {"key": "machine:x", "kind": "machine", "inventory": "ОБ.019", "name": "Станок", "device": ""},
    ]
    with sessions() as session:
        assert {row.kind for row in session.scalars(select(PrinterRow))} == {"tspl", "document"}
        assert session.scalar(select(DeviceRow.device_id)) == SCANNER
        shown = stations(session, rows)
    assert [row["name"] for row in shown] == ["МФУ Pantum BM2300", "Принтер этикеток TSC MB341", "Станок"]
    assert shown[0]["station"] == {"kind": "mfp", "machine_id": PC, "scanners": [SCANNER], "printers": [MFP]}
    assert shown[1]["station"] == {"kind": "labels"} and "station" not in shown[2]
