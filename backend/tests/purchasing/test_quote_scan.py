"""«КП со скана»: a quote scan / photo read by the vision model (port of Roller_Buyer importPdf over ERP's УПД client).

The HTTP calls go to a fake `urlopen`, the page pictures to a fake port; the model's answers are made up (no real ИНН,
no prices). The text layer keeps working without the model."""

import base64
import io
import json
import re
import urllib.error
import urllib.request
from decimal import Decimal
from uuid import uuid4

import pytest
from PIL import Image

from app.main import app
from app.modules.purchasing import quote_import as qi
from app.modules.purchasing import quote_scan
from app.modules.purchasing.api_rfq import quote_scan_pages
from app.modules.warehouse import receipt_ocr
from app.shared import vision_llm
from app.workflows.purchasing_quote_scan import WarehouseScanPages
from tests.pdf_samples import identity_pdf
from tests.purchasing.test_rfq_api import BASE, _rfq, _supplier, rfq_api  # noqa: F401 — the fixture

KEY = "sk-test-0123456789-secret-AbCd"
READY = vision_llm.LlmConfig(base_url="https://llm.example.test/v1", api_key=KEY, model="main", fallback_model="spare")
SCAN = identity_pdf(["  "])  # a PDF with no text layer: a scan


class FakePages:
    def __init__(self, config=READY, pages=1, error: Exception | None = None):
        self._config, self.pages, self.error, self.asked = config, pages, error, []

    def config(self):
        return self._config

    def page_jpegs(self, content, kind, limit):
        self.asked.append(kind)
        if self.error:
            raise self.error
        return [b"jpeg-page"] * self.pages


class FakeApi:
    """Answers in turn: a dict — the model's JSON; an int — that HTTP status; an exception — raised."""

    def __init__(self, *plan):
        self.plan, self.prompts, self.models = list(plan), [], []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        self.models.append(body["model"])
        self.prompts.append(body["messages"][0]["content"][0]["text"])
        step = self.plan.pop(0) if len(self.plan) > 1 else self.plan[0]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            raise urllib.error.HTTPError(request.full_url, step, "error", {}, io.BytesIO(f"failed Bearer {KEY}".encode()))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(step, ensure_ascii=False)}}],
                                      "usage": {"total_tokens": 10}}).encode("utf-8"))


@pytest.fixture()
def scan_api(rfq_api, monkeypatch):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    state = {"pages": FakePages()}
    app.dependency_overrides[quote_scan_pages] = lambda: state["pages"]

    def install(*plan) -> FakeApi:
        fake = FakeApi(*plan)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return fake

    yield client, rfq, state, install
    app.dependency_overrides.pop(quote_scan_pages, None)


def _send(client, rfq, content: bytes, supplier_id: str | None = None, name="kp.pdf"):
    body = {"file_name": name, "content_base64": base64.b64encode(content).decode()}
    return client.post(f"{BASE}/{rfq['id']}/quotes/import", json={**body, **({"supplier_id": supplier_id} if supplier_id else {})})


def _by_article(prompt: str) -> tuple[int, int]:
    numbers = re.findall(r"• (\d+): Разъём(?: \[(SHR-02V-S-B)\])? —", prompt)
    article = next(int(n) for n, a in numbers if a)
    plain = next(int(n) for n, a in numbers if not a)
    return article, plain


def _answer(article_id, plain_id, **header):
    return {
        "supplier_name": "ООО «Пример Снаб»", "supplier_inn": "7701234567", "number": "КП-77", "date": "2026-10-20",
        "valid_until": "2026-11-20", "prices_include_vat": False, "vat_rate": 22,
        "lines": [
            {"requested_id": article_id, "name": "Розетка 2 конт.", "article": "SHR-02V-S-B", "qty": 2, "unit": "шт",
             "price": "1 200,50", "lead_days": 10, "in_stock": False, "declined": False, "analog": False},
            {"requested_id": plain_id, "name": "Вилка 2 конт.", "qty": 2, "unit": "шт", "price": 15,
             "in_stock": True, "declined": False, "analog": True, "analog_note": "JST вместо Molex"},
            {"requested_id": 99, "name": "Доставка курьером", "qty": 1, "unit": "усл", "price": 500,
             "in_stock": False, "declined": False, "analog": False},
        ],
        **header,
    }


def test_a_scan_is_read_by_the_model_and_its_rows_land_on_the_request_lines(scan_api):
    client, rfq, state, install = scan_api
    # The request's line numbers are only known from the prompt: the first call tells them, the second answers.
    probe = install({"lines": []})
    assert _send(client, rfq, SCAN).status_code == 422  # no rows: «Модель не нашла в скане строк КП»
    article, plain = _by_article(probe.prompts[0])
    assert "Наш запрос:" in probe.prompts[0] and "5040191181" in probe.prompts[0]  # our INN: never the supplier
    supplier = _supplier(client, rfq["id"], "ООО Пример Снаб")
    install(_answer(article, plain))

    answer = _send(client, rfq, SCAN, supplier)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["reader"] == "model" and body["model"] == "main" and body["source"] == "file"
    assert body["vat_included"] is False and state["pages"].asked[-1] == "application/pdf"
    header = body["header"]
    assert header["document_name"] == "ООО «Пример Снаб»" and header["inn"] == "7701234567"
    assert (header["number"], header["date"], header["valid_until"]) == ("КП-77", "2026-10-20", "2026-11-20")
    assert header["supplier_matches"] is True and header["supplier_id"] == supplier  # by name among the request's
    rows = {row["source_name"]: row for row in body["lines"]}
    assert rows["Розетка 2 конт."]["matched_by"] == "model" and rows["Розетка 2 конт."]["price"] == "1200.5"
    assert rows["Розетка 2 конт."]["lead_days"] == 10 and rows["Розетка 2 конт."]["vat_rate"] == "22"
    assert rows["Вилка 2 конт."]["in_stock"] is True and rows["Вилка 2 конт."]["lead_days"] == 0
    assert rows["Вилка 2 конт."]["analog"] is True and rows["Вилка 2 конт."]["analog_note"] == "JST вместо Molex"
    assert rows["Розетка 2 конт."]["rfq_line_id"] != rows["Вилка 2 конт."]["rfq_line_id"]
    # Roller: «a model answer pointing outside the RFQ is ignored» — the delivery stays unmatched.
    assert [row["source_name"] for row in body["unmatched"]] == ["Доставка курьером"]
    assert body["unmatched"][0]["matched_by"] is None


def test_answers_outside_the_request_or_twice_are_dropped_and_the_rest_match_the_usual_way():
    first, second = uuid4(), uuid4()
    targets = [qi.Target(first, "Разъём", "SHR-02V-S-B", Decimal(2), "шт"), qi.Target(second, "Кабель РК 50", None, Decimal(10), "м")]
    found = quote_scan.quote_from_answer({
        "supplier_inn": "5040191181", "supplier_name": "ООО ЭЗП ФРАКСИС", "date": "20.10.2026", "lines": [
            {"requested_id": 2, "name": "Кабель РК 50-2-11", "qty": "10", "price": 100},
            {"requested_id": 2, "name": "Кабель РК 50-2-11 второй раз", "price": 90},
            {"requested_id": 0, "name": "SHR-02V-S-B розетка", "price": 5},
            {"requested_id": "7", "name": "Что-то лишнее", "price": 1},
            {"requested_id": True, "name": "Ещё лишнее", "price": "нет", "declined": True},
            {"name": "", "article": ""},
        ]}, targets)
    assert found.header["inn"] == "" and found.header["document_name"] == "" and found.header["date"] is None
    assert [line.rfq_line_id for line in found.lines] == [second, None, None, None, None]
    assert found.lines[4].price is None and found.lines[4].declined
    matched = quote_scan.match_scan(found.lines, targets, {})
    assert matched[2].rfq_line_id == first and matched[2].matched_by == "article"  # model → … → part number
    assert matched[1].rfq_line_id is None  # the line the model already gave is not taken twice


def test_a_photo_is_read_by_the_model_and_kept_with_the_quote(scan_api):
    client, rfq, state, install = scan_api
    picture = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(picture, "JPEG")
    probe = install({"lines": []})
    _send(client, rfq, picture.getvalue(), name="kp.jpg")
    article, plain = _by_article(probe.prompts[0])
    install(_answer(article, plain))
    body = _send(client, rfq, picture.getvalue(), name="kp.jpg").json()
    assert body["reader"] == "model" and state["pages"].asked[-1] == "image/jpeg"
    line_id = body["lines"][0]["rfq_line_id"]
    supplier = _supplier(client, rfq["id"], "ООО Фото")
    saved = client.post(f"{BASE}/{rfq['id']}/quotes", json={
        "supplier_id": supplier, "source": "file", "file_name": "kp.jpg",
        "file_base64": base64.b64encode(picture.getvalue()).decode(), "lines": [{"rfq_line_id": line_id, "unit_price": "1"}]})
    assert saved.status_code == 201, saved.text
    served = client.get(f"{BASE}/quotes/{saved.json()['id']}/file")
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert _send(client, rfq, b"GIF89a....", name="kp.gif").status_code == 422


@pytest.mark.parametrize(("plan", "pages", "words"), [
    ((429,), FakePages(), "квота"),
    ((TimeoutError("timed out"),), FakePages(), "не ответила"),
    ((401,), FakePages(), "не принял ключ"),
    (({"lines": []},), FakePages(config=vision_llm.LlmConfig()), "не настроена"),
    (({"lines": []},), FakePages(error=vision_llm.PagesUnavailable("pdftoppm")), "pdftoppm"),
])
def test_an_unavailable_model_is_explained_and_nothing_crashes(scan_api, plan, pages, words):
    client, rfq, state, install = scan_api
    state["pages"] = pages
    fake = install(*plan)
    refused = _send(client, rfq, SCAN)
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert words in detail and "текстового слоя" in detail and "вручную" in detail
    assert KEY not in detail
    if words == "квота":  # retried, then the fallback model — then given up
        assert fake.models == ["main", "main", "spare"]


def test_the_text_layer_is_read_without_the_model_and_gives_the_supplier_and_date(scan_api):
    client, rfq, state, install = scan_api
    state["pages"] = FakePages(config=vision_llm.LlmConfig())  # no key on this server
    fake = install(500)
    supplier = _supplier(client, rfq["id"], "ООО Другой")
    pdf = identity_pdf(["Поставщик: ООО Альфа Кабель, ИНН 7701234567", "Коммерческое предложение № 12 от 14.10.2026",
                        "Разъём SHR-02V-S-B 2 шт 1200,50 6 дн"])
    answer = _send(client, rfq, pdf, supplier)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["reader"] == "text" and fake.models == [] and state["pages"].asked == []
    assert body["header"]["document_name"] == "ООО Альфа Кабель" and body["header"]["date"] == "2026-10-14"
    assert body["header"]["supplier_matches"] is False  # the document names another supplier than the chosen one
    # A text layer with no row in it asks the model; unavailable — both reasons are told.
    words = _send(client, rfq, identity_pdf(["Добрый день, КП во вложении"]))
    assert words.status_code == 422 and "Не нашёл в тексте строки КП" in words.json()["detail"]
    assert "не настроена" in words.json()["detail"]


def test_a_one_off_failure_is_retried_not_reported(scan_api):
    client, rfq, state, install = scan_api
    probe = install({"lines": []})
    _send(client, rfq, SCAN)
    article, plain = _by_article(probe.prompts[0])
    fake = install(503, _answer(article, plain))
    answer = _send(client, rfq, SCAN)
    assert answer.status_code == 200, answer.text
    assert fake.models == ["main", "main"] and answer.json()["reader"] == "model"


def test_the_bridge_gives_upright_jpegs_of_a_photo_and_the_servers_settings(monkeypatch):
    monkeypatch.setattr(receipt_ocr, "page_turn", lambda image: 90)
    picture = io.BytesIO()
    Image.new("RGB", (60, 20), "white").save(picture, "PNG")
    bridge = WarehouseScanPages(None)
    [page] = bridge.page_jpegs(picture.getvalue(), "image/png", 6)
    with Image.open(io.BytesIO(page)) as seen:
        assert seen.format == "JPEG" and seen.size == (20, 60)  # turned upright
    monkeypatch.setenv("ERP_UPD_LLM_API_KEY", KEY)
    assert bridge.config().ready and bridge.config().model == vision_llm.DEFAULT_MODEL
    monkeypatch.setattr(receipt_ocr.shutil, "which", lambda name: None)
    with pytest.raises(vision_llm.PagesUnavailable):
        bridge.page_jpegs(SCAN, "application/pdf", 6)
