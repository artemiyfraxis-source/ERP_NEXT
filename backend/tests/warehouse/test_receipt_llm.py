"""A paper УПД read by a vision model over an OpenAI-compatible API (owner 26.10.2026: «давай сразу апи»).

The HTTP calls go to a fake `urlopen`; the scans are blank pictures, the model's answers made up (no real ИНН, no
prices). Tesseract stays the fallback, the key is set by the administrator and never leaves the server."""

import io
import json
import logging
import subprocess
import threading
import urllib.error
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.warehouse import receipt_llm, receipt_ocr
from app.modules.warehouse.receipt_scan import NO_TEXT, PARTLY, RecognisedUpd
from app.modules.warehouse.scan_reading import (
    MODEL_FAILED, ScanReading, merge_readings, read_scan, reading_from_json, reading_json,
)
from app.modules.warehouse.upd_lines import item_line
from app.shared.http_auth import Principal, authenticated_principal
from tests.access_levels import principal_as
from tests.identity.test_api_tokens import _csrf, _login, tokens_api  # noqa: F401  (fixture)

KEY = "sk-test-0123456789-secret-AbCd"
SETTINGS = "/api/stock/receipts/llm-settings"
CONFIG = receipt_llm.LlmConfig(base_url="https://llm.example.test/v1", api_key=KEY)
PAGE_1 = {
    "supplier": "ООО «Пример Кабель»", "inn": "7701234567", "upd_number": "А-17", "upd_date": "2026-10-20",
    "invoice_number": "45", "invoice_date": "2026-10-01",
    "lines": [
        {"n": "1", "supplier_code": "00-0001", "name": "Маркировочная трубка МТ-2К 6,4/3,2мм белая (в рулоне 100 м)",
         "unit": "шт", "quantity": "3.000"},
        {"n": "2", "supplier_code": "00-0002", "name": "Тр-1090EW Самоклеющаяся лента, 9мм*16м, белая",
         "unit": "шт", "quantity": "2"},
    ],
}
PAGE_2 = {
    "supplier": "", "inn": "", "upd_number": "", "upd_date": "", "invoice_number": "", "invoice_date": "",
    "lines": [{"n": "3", "supplier_code": "00-0003", "name": "Кабель UTP 4x2x0,5 в бухте 305 м", "unit": "м",
               "quantity": "305"}],
}


def _reply(data: dict, fenced: bool = False) -> bytes:
    text = json.dumps(data, ensure_ascii=False)
    if fenced:
        text = f"```json\n{text}\n```"
    return json.dumps({
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 5800, "completion_tokens": 400, "total_tokens": 6200},
    }).encode()


class FakeApi:
    """`urlopen` of the relay: each call takes the next planned answer (bytes — 200, int — that HTTP status,
    Exception — raised); the requests are kept."""

    def __init__(self, *plan):
        self.plan = list(plan)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, request, timeout):
        with self.lock:
            body = json.loads(request.data.decode())
            self.requests.append({"url": request.full_url, "body": body, "timeout": timeout,
                                  "auth": request.get_header("Authorization")})
            step = self.plan.pop(0) if self.plan else _reply(PAGE_1)
        if callable(step) and not isinstance(step, type):
            step = step(body)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            raise urllib.error.HTTPError(request.full_url, step, "error", {},
                                         io.BytesIO(f"upstream failed for Bearer {KEY}".encode()))
        return io.BytesIO(step)


@pytest.fixture()
def api(monkeypatch):
    def install(*plan) -> FakeApi:
        fake = FakeApi(*plan)
        monkeypatch.setattr(receipt_llm.urllib.request, "urlopen", fake)
        return fake

    return install


def _deadline() -> float:
    return 10_000.0


def _clock() -> float:
    return 0.0


# --- the client ---------------------------------------------------------------------------------------------------


def test_a_page_goes_with_the_prompt_the_key_and_the_measured_parameters(api):
    fake = api(_reply(PAGE_1))
    answer = receipt_llm.read_page(CONFIG, b"jpeg-bytes", 1, _deadline(), _clock)
    assert answer.data == PAGE_1 and answer.model == "gpt-5.6-luna"
    [sent] = fake.requests
    assert sent["url"] == "https://llm.example.test/v1/chat/completions" and sent["auth"] == f"Bearer {KEY}"
    body = sent["body"]
    assert (body["model"], body["temperature"], body["reasoning_effort"]) == ("gpt-5.6-luna", 0, "medium")
    assert body["response_format"] == {"type": "json_object"} and sent["timeout"] == 120
    text, picture = body["messages"][0]["content"]
    assert text["text"] == receipt_llm.PROMPT and "Цифры переписывай посимвольно" in text["text"]
    assert picture["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("content", [
    "```json\n{\"upd_number\": \"5\", \"lines\": []}\n```",
    "Вот JSON: {\"upd_number\": \"5\", \"lines\": []} — готово",
    [{"type": "text", "text": "{\"upd_number\": \"5\", \"lines\": []}"}],
])
def test_the_answer_is_read_in_fences_with_chatter_and_in_parts(content):
    answer = {"choices": [{"message": {"content": content}}]}
    assert receipt_llm.parse_answer(receipt_llm.message_text(answer)) == {"upd_number": "5", "lines": []}
    with pytest.raises(receipt_llm.LlmError):
        receipt_llm.parse_answer("не JSON")


def test_5xx_is_retried_once_then_the_fallback_model_reads_the_page(api, monkeypatch):
    fake = api(502, TimeoutError("timed out"), _reply(PAGE_1, fenced=True))
    logger, records = logging.getLogger(receipt_llm.__name__), []
    handler = logging.Handler(logging.INFO)
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
    monkeypatch.setattr(logger, "disabled", False)  # another test's logging config may have switched it off
    level = logger.level
    logger.setLevel(logging.INFO)  # setLevel, not the attribute: it clears the logger's enabled-for cache
    logger.addHandler(handler)
    try:
        answer = receipt_llm.read_page(CONFIG, b"jpeg", 2, _deadline(), _clock)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    log = "\n".join(records)
    assert [request["body"]["model"] for request in fake.requests] == ["gpt-5.6-luna", "gpt-5.6-luna",
                                                                       "gemini-3.8-flash"]
    assert answer.data == PAGE_1 and answer.model == "gemini-3.8-flash"
    assert "gemini-3.8-flash, стр. 2" in log and "токенов 6200" in log and "HTTP 502" in log
    assert KEY not in log, "the key never reaches the log, not even from an echoing error body"


def test_a_4xx_goes_straight_to_the_fallback_and_a_dropped_parameter_is_asked_again(api):
    rejects_format = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": "response_format unsupported"}'))
    fake = api(rejects_format, _reply(PAGE_2))
    answer = receipt_llm.read_page(CONFIG, b"jpeg", 1, _deadline(), _clock)
    assert answer.data == PAGE_2 and len(fake.requests) == 2
    assert "response_format" not in fake.requests[1]["body"] and fake.requests[1]["body"]["model"] == "gpt-5.6-luna"

    fake = api(401, _reply(PAGE_2))
    assert receipt_llm.read_page(CONFIG, b"jpeg", 1, _deadline(), _clock).model == "gemini-3.8-flash"
    assert len(fake.requests) == 2, "a refused key is not retried on the same model"


def test_no_model_read_the_page_says_why_without_the_key(api):
    api(500, 500, 500)
    answer = receipt_llm.read_page(CONFIG, b"jpeg", 1, _deadline(), _clock)
    assert answer.data is None and "HTTP 500" in answer.error and KEY not in answer.error and "***" in answer.error


def test_the_document_budget_stops_calls_that_cannot_finish(api):
    fake = api()
    answer = receipt_llm.read_page(CONFIG, b"jpeg", 1, deadline=3.0, clock=_clock)
    assert answer.data is None and fake.requests == []


def test_pages_go_in_parallel_and_come_back_in_page_order(api):
    started = threading.Barrier(3, timeout=5)

    def page(body):
        started.wait()  # three calls at once — or the barrier times out
        return _reply(PAGE_2 if body["messages"][0]["content"][1]["image_url"]["url"].endswith("Mg==") else PAGE_1)

    api(page, page, page)
    answers = receipt_llm.read_pages(CONFIG, [b"1", b"2", b"1"])
    assert [answer.page for answer in answers] == [1, 2, 3]
    assert [answer.data["lines"][0]["n"] for answer in answers] == ["1", "3", "1"]


def test_config_env_wins_field_by_field_and_the_key_has_no_default():
    stored = {"base_url": "https://saved.example.test/v1", "api_key": "sk-saved-0123456789", "model": "",
              "effort": "low"}
    config = receipt_llm.llm_config(stored, environ={"ERP_UPD_LLM_MODEL": "env-model"})
    assert (config.base_url, config.api_key, config.model, config.fallback_model, config.effort) == (
        "https://saved.example.test/v1", "sk-saved-0123456789", "env-model", "gemini-3.8-flash", "low")
    assert receipt_llm.llm_config({}, environ={}).ready is False
    assert receipt_llm.llm_config({}, environ={"ERP_UPD_LLM_API_KEY": KEY}).base_url == "https://api.relaymodels.com/v1"
    assert receipt_llm.env_fields({"ERP_UPD_LLM_API_KEY": KEY, "ERP_UPD_LLM_EFFORT": " "}) == ["api_key"]
    assert KEY not in repr(receipt_llm.llm_config({"api_key": KEY}, environ={}))
    assert receipt_llm.key_hint(KEY) == "sk-te…AbCd" and receipt_llm.key_hint("short") == "…"


# --- lines and pages ----------------------------------------------------------------------------------------------


def test_model_rows_become_warehouse_lines_pack_only_when_said_in_words():
    lines = receipt_llm.page_lines(PAGE_1) + receipt_llm.page_lines(PAGE_2)
    assert [(line.article, line.quantity, line.unit, line.packs, line.supplier_code, line.number) for line in lines] == [
        ("МТ-2К 6,4/3,2 белая", Decimal(300), "м", Decimal(3), "00-0001", "1"),  # 3 рулона × 100 м
        ("Тр-I090EW", Decimal(2), "шт", None, "00-0002", "2"),  # «9мм*16м» is the tape's size: 2 шт stay 2 шт
        ("", Decimal(305), "м", Decimal(1), "00-0003", "3"),
    ]
    assert lines[0].name == "Маркировочная трубка МТ-2К 6,4/3,2мм белая (в рулоне 100 м)", "the name as printed"
    assert lines[1].name.startswith("Тр-I090EW "), "the OCR I↔1 fix of the code stays"
    odd = receipt_llm.page_lines({"lines": [{"name": "", "supplier_code": ""}, "x", {"name": "Хомут", "quantity": ""}]})
    assert [(line.name, line.quantity, line.unit) for line in odd] == [("Хомут", None, "шт")]


def test_the_header_of_a_page():
    found = receipt_llm.page_header({**PAGE_1, "inn": "77 0123 4567", "upd_date": "20.10.2026", "invoice_date": ""})
    assert found == RecognisedUpd(supplier="ООО «Пример Кабель»", inn="7701234567", upd_number="А-17",
                                  upd_date=date(2026, 10, 20), invoice_number="45", invoice_date=None)
    assert receipt_llm.page_header({"inn": "123", "upd_date": "вчера"}) == RecognisedUpd()


def test_pages_merge_header_first_lines_in_order_a_repeated_sheet_once():
    one = ScanReading(receipt_llm.page_header(PAGE_1), tuple(receipt_llm.page_lines(PAGE_1)), engine="gpt-5.6-luna")
    two = ScanReading(receipt_llm.page_header(PAGE_2), tuple(receipt_llm.page_lines(PAGE_2)), engine="gpt-5.6-luna")
    merged = merge_readings([one, two, two])
    assert merged.found.upd_number == "А-17" and merged.note == ""
    assert [line.number for line in merged.lines] == ["1", "2", "3"]
    late = merge_readings([two, one])
    assert late.found.supplier == "ООО «Пример Кабель»", "the header from the first page that has it"
    same_name = item_line("Хомут", Decimal(1), "шт", number="7")
    other_row = item_line("Хомут", Decimal(1), "шт", number="8")
    assert len(merge_readings([ScanReading(RecognisedUpd(), (same_name, other_row))]).lines) == 2
    assert merge_readings([]).note == NO_TEXT
    assert merge_readings([ScanReading(RecognisedUpd(supplier="ООО Альфа"))]).note == PARTLY


def test_a_reading_is_kept_as_json_and_read_back():
    reading = ScanReading(receipt_llm.page_header(PAGE_1), tuple(receipt_llm.page_lines(PAGE_1)), "", "w", "m")
    assert reading_from_json(reading_json(reading)) == reading
    assert reading_from_json("") is None and reading_from_json("{broken") is None
    assert reading_from_json(json.dumps({"found": {}, "lines": [{"unknown": 1}]})) is None


# --- the whole file: upright pages, model, fallback ---------------------------------------------------------------

def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (300, 200), 255).save(buffer, "PNG")
    return buffer.getvalue()


def test_a_picture_is_turned_upright_and_read_by_the_model(api, monkeypatch):
    turns = []
    monkeypatch.setattr(receipt_ocr, "page_turn", lambda image: turns.append(image.name) or 90)
    fake = api(_reply(PAGE_1))
    reading = read_scan(_png(), "image/png", CONFIG)
    assert turns == ["scan.png"] and len(fake.requests) == 1
    import base64

    from PIL import Image

    sent = fake.requests[0]["body"]["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(sent))) as picture:
        assert (picture.format, picture.mode, picture.size) == ("JPEG", "L", (200, 300)), "turned, grayscale"
    assert reading.found.upd_number == "А-17" and len(reading.lines) == 2 and reading.engine == "gpt-5.6-luna"
    assert reading.warning == ""


def test_a_page_no_model_read_goes_to_tesseract(api, monkeypatch):
    monkeypatch.setattr(receipt_ocr, "page_turn", lambda image: 0)
    monkeypatch.setattr(receipt_ocr, "page_text", lambda image: "Счет-фактура № 9 от 01.10.2026 (1)\nПродавец: ООО Бета")
    api(500, 500, 500)
    reading = read_scan(_png(), "image/png", CONFIG)
    assert (reading.found.upd_number, reading.found.supplier) == ("9", "ООО Бета")
    assert reading.warning == MODEL_FAILED and reading.engine == "tesseract"


def test_without_a_key_the_scan_is_read_by_tesseract_as_before(api, monkeypatch):
    fake = api()
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "Продавец: ООО Альфа")
    reading = read_scan(_png(), "image/png", receipt_llm.LlmConfig())
    assert reading.found.supplier == "ООО Альфа" and reading.note == PARTLY and fake.requests == []


def test_orientation_by_tesseract_osd_else_by_the_words_read(monkeypatch, tmp_path):
    from PIL import Image

    image = tmp_path / "page.png"
    Image.new("L", (40, 20), 255).save(image)
    monkeypatch.setattr(receipt_ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    osd = "Page number: 0\nOrientation in degrees: 270\nRotate: 90\nOrientation confidence: 7.31\nScript: Cyrillic\n"
    monkeypatch.setattr(receipt_ocr.subprocess, "run",
                        lambda arguments, **options: subprocess.CompletedProcess(arguments, 0, osd.encode(), b""))
    assert receipt_ocr.page_turn(image) == 270, "«Rotate: 90» clockwise = Pillow's 270"

    words = " ".join(["Продавец Покупатель Количество"] * 20)

    def unsure(arguments, **options):
        if "0" in arguments and "--psm" in arguments and arguments[arguments.index("--psm") + 1] == "0":
            return subprocess.CompletedProcess(arguments, 0, b"Rotate: 180\nOrientation confidence: 0.4\n", b"")
        text = words if Path(arguments[1]).name.endswith("-t180.png") else "ii ~~"
        return subprocess.CompletedProcess(arguments, 0, text.encode(), b"")

    monkeypatch.setattr(receipt_ocr.subprocess, "run", unsure)
    assert receipt_ocr.page_turn(image) == 180, "an upside-down page found by the words it reads"
    monkeypatch.setattr(receipt_ocr.shutil, "which", lambda name: None)
    assert receipt_ocr.page_turn(image) == 0


# --- settings and the async screen --------------------------------------------------------------------------------


@pytest.fixture()
def stand(tokens_api, monkeypatch):  # noqa: F811
    for variable in receipt_llm.ENV.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(receipt_ocr, "page_turn", lambda image: 0)
    monkeypatch.setattr(receipt_ocr, "ocr_text", lambda content, kind: "")
    monkeypatch.setattr(receipt_ocr, "page_text", lambda image: "")
    monkeypatch.setattr(receipt_ocr, "_pdf_pages", _one_page)  # pdftoppm (the server image has it)
    return _login("admin"), tokens_api[0]


def _one_page(content: bytes, folder: Path) -> list[Path]:
    page = folder / "page-1.png"
    page.write_bytes(_png())
    return [page]


def test_the_key_is_write_only_and_the_journal_does_not_hold_it(stand):
    admin, _sessions = stand
    empty = admin.get(SETTINGS).json()
    assert (empty["key_set"], empty["key_hint"], empty["model"], empty["env"]) == (False, "", "", [])
    assert empty["defaults"] == {"base_url": "https://api.relaymodels.com/v1", "model": "gpt-5.6-luna",
                                 "fallback_model": "gemini-3.8-flash", "effort": "medium"}
    saved = admin.put(SETTINGS, headers=_csrf(admin), json={
        "base_url": "https://llm.example.test/v1", "model": "", "fallback_model": "", "effort": "", "api_key": KEY})
    assert saved.status_code == 200, saved.text
    assert KEY not in saved.text and saved.json()["key_hint"] == "sk-te…AbCd" and saved.json()["key_set"] is True
    assert saved.json()["effective"]["model"] == "gpt-5.6-luna"
    kept = admin.put(SETTINGS, headers=_csrf(admin), json={"base_url": "https://llm.example.test/v1", "effort": "high"})
    assert kept.json()["key_set"] is True and kept.json()["effort"] == "high", "no key sent — the key is kept"
    assert KEY not in admin.get(SETTINGS).text
    journal = admin.get("/api/stock/journal").text
    assert "Распознавание УПД" in journal and "ключ API заменён" in journal and KEY not in journal
    for bad in ({"base_url": "ftp://x"}, {"effort": "max"}, {"api_key": "sk two words"}):
        assert admin.put(SETTINGS, headers=_csrf(admin), json=bad).status_code == 422, bad
    cleared = admin.put(SETTINGS, headers=_csrf(admin), json={"clear_key": True}).json()
    assert cleared["key_set"] is False and cleared["base_url"] == ""


def test_the_server_env_wins_and_is_shown(stand, monkeypatch):
    admin, _sessions = stand
    monkeypatch.setenv("ERP_UPD_LLM_API_KEY", KEY)
    view = admin.get(SETTINGS).json()
    assert view["env"] == ["api_key"] and view["key_set"] is True and KEY not in json.dumps(view)


def test_only_the_administrator_sees_and_sets_the_model(stand):
    storekeeper = Principal(actor=principal_as("storekeeper").actor, email="store@example.test",
                            roles=frozenset({"storekeeper"}), permissions={"warehouse": "edit"})
    app.dependency_overrides[authenticated_principal] = lambda: storekeeper
    try:
        client = TestClient(app)
        assert client.get(SETTINGS).status_code == 403
        assert client.put(SETTINGS, json={"api_key": KEY}).status_code == 403
        assert client.post(f"{SETTINGS}/check").status_code == 403
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
    assert _login("operator").get(SETTINGS).status_code == 403


def test_check_calls_each_model_with_one_token(stand, api):
    admin, _sessions = stand
    assert admin.post(f"{SETTINGS}/check", headers=_csrf(admin)).json() == {"ok": False, "models": [],
                                                                             "error": "Ключ API не задан"}
    admin.put(SETTINGS, headers=_csrf(admin), json={"base_url": "https://llm.example.test/v1", "api_key": KEY})
    fake = api(_reply({}), 401)
    checked = admin.post(f"{SETTINGS}/check", headers=_csrf(admin)).json()
    assert [(model["model"], model["ok"]) for model in checked["models"]] == [("gpt-5.6-luna", True),
                                                                               ("gemini-3.8-flash", False)]
    assert checked["ok"] is False and KEY not in json.dumps(checked)
    assert fake.requests[0]["body"]["max_tokens"] == 1


def test_file_scan_is_read_by_the_model_in_the_background_and_merged_from_kept_readings(stand, api):
    admin, _sessions = stand
    admin.put(SETTINGS, headers=_csrf(admin), json={"base_url": "https://llm.example.test/v1", "api_key": KEY})
    fake = api(_reply(PAGE_1), _reply(PAGE_2))
    first = admin.post("/api/stock/receipts/scan", headers=_csrf(admin), files={"file": ("1.png", _png(), "image/png")})
    assert first.status_code == 200 and first.json()["result"] is None and first.json()["recognising"] is True
    page_1 = admin.get(f"/api/stock/receipts/scan-jobs/{first.json()['id']}").json()["result"]
    assert page_1["recognised"]["upd_number"] == "А-17" and page_1["recognised"]["inn"] == "7701234567"
    assert [(line["quantity"], line["unit"], line["supplier_code"]) for line in page_1["lines"]] == [
        ("300", "м", "00-0001"), ("2", "шт", "00-0002")]
    assert page_1["note"] == "Строки прочитаны со скана (2) — сверьте артикулы и количество"
    second = admin.post("/api/stock/receipts/scan", headers=_csrf(admin), files={"file": ("2.png", _png(), "image/png")})
    page_2 = admin.get(f"/api/stock/receipts/scan-jobs/{second.json()['id']}").json()["result"]
    assert page_2["recognised"]["upd_number"] == "" and len(page_2["lines"]) == 1
    calls = len(fake.requests)

    merged = admin.post("/api/stock/receipts/scan-merge", headers=_csrf(admin),
                        json={"files": [page_1["scan_file"], page_2["scan_file"]]}).json()
    assert len(fake.requests) == calls, "the sheets are not read again"
    result = merged["result"]
    assert result["recognised"]["supplier"] == "ООО «Пример Кабель»"
    assert [line["supplier_code"] for line in result["lines"]] == ["00-0001", "00-0002", "00-0003"]
    assert KEY not in json.dumps(merged)


def test_the_poll_never_reads_and_restarts_a_lost_reading(stand, api, monkeypatch):
    from app.modules.warehouse import scan_jobs_api

    admin, _sessions = stand
    admin.put(SETTINGS, headers=_csrf(admin), json={"base_url": "https://llm.example.test/v1", "api_key": KEY})
    fake = api(_reply(PAGE_1))
    lost, original = [], scan_jobs_api.recognise_job
    monkeypatch.setattr(scan_jobs_api, "recognise_job", lambda *args: lost.append(args))  # the process died
    job = admin.post("/api/stock/receipts/scan", headers=_csrf(admin), files={"file": ("1.png", _png(), "image/png")})
    job_id = job.json()["id"]
    waiting = admin.get(f"/api/stock/receipts/scan-jobs/{job_id}").json()
    assert (waiting["status"], waiting["recognising"], waiting["result"]) == ("done", True, None)
    assert waiting["scan_file"].startswith("/api/stock/receipts/files/") and fake.requests == []
    assert len(lost) == 2, "the poll restarts the reading as a background task, never inside itself"
    monkeypatch.setattr(scan_jobs_api, "recognise_job", original)
    first_poll = admin.get(f"/api/stock/receipts/scan-jobs/{job_id}").json()
    assert first_poll["result"] is None, "this poll answered before the reading ran"
    done = admin.get(f"/api/stock/receipts/scan-jobs/{job_id}").json()
    assert done["recognising"] is False and done["result"]["recognised"]["upd_number"] == "А-17"


def test_a_reading_that_crashes_stores_an_answer_that_asks_to_fill_by_hand(stand, monkeypatch):
    from app.modules.warehouse import scan_recognition

    admin, _sessions = stand

    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(scan_recognition, "read_scan", broken)
    job = admin.post("/api/stock/receipts/scan", headers=_csrf(admin), files={"file": ("1.png", _png(), "image/png")})
    result = admin.get(f"/api/stock/receipts/scan-jobs/{job.json()['id']}").json()["result"]
    assert result["note"] == scan_recognition.FAILED and result["lines"] == []
