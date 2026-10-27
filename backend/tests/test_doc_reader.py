"""The ERP's one document reader (app.shared.doc_reader, Витя 26.10.2026: our parser and Артемий's «становятся одним
целым»): an electronic PDF in one text call, a scan page by page; the pages merged and only then checked. The calls go
to a fake `complete`; the answers are made up — no real model is asked."""

from __future__ import annotations

import json
import threading

import pytest

from app.shared.doc_reader import closing, reader, transport
from app.shared.doc_reader.transport import LlmConfig, LlmError
from tests.pdf_samples import identity_pdf

CONFIG = LlmConfig(base_url="https://llm.example.test/v1", api_key="sk-test-0123456789-AbCd")
HEAD = {
    "docKind": "UPD", "number": "А-17", "date": "2026-10-20", "sellerName": "ООО «Пример Кабель»",
    "sellerInn": "7707083893", "sellerKpp": "770701001", "buyerInn": "5040191181",
    "basis": "Счет № 45 от 01.10.2026", "paymentDoc": "№ 916 от 19.10.2026",
}


def _line(seq: int, name: str, qty: float = 1, price: float = 100) -> dict:
    net = qty * price
    return {"seq": seq, "article": f"00-{seq:04d}", "name": name, "unit": "шт", "qty": qty, "price": price,
            "net": net, "vatRate": "22%", "vat": round(net * 0.22, 2), "gross": round(net * 1.22, 2)}


FIRST = {**HEAD, "lines": [_line(1, "Разъём XLR"), _line(2, "Кабель ВВГ 3х1,5", 10, 50)]}
LAST = {"docKind": "UPD", "number": None, "sellerName": None, "totalNet": 650, "totalVat": 143, "totalGross": 793,
        "lines": [_line(3, "Хомут 2,5х100", 2, 25)]}


def test_pages_merge_header_from_the_first_totals_from_the_last_lines_in_order_a_repeated_sheet_once():
    merged = closing.merge_pages([FIRST, None, LAST, LAST])
    assert (merged["number"], merged["sellerName"], merged["basis"]) == ("А-17", "ООО «Пример Кабель»", HEAD["basis"])
    assert (merged["totalNet"], merged["totalGross"]) == (650, 793)
    assert [line["seq"] for line in merged["lines"]] == [1, 2, 3], "a sheet scanned twice — its rows once"
    assert closing.merge_pages([]) == {} and closing.merge_pages([None]) == {}
    late = closing.merge_pages([{"docKind": "OTHER", "lines": []}, FIRST])
    assert late["docKind"] == "UPD", "a page the model could not place does not decide the kind"


def test_a_torg12_next_to_its_invoice_is_one_document_lines_from_the_invoice():
    torg = {"docKind": "TORG12", "number": "381", "basis": "Договор поставки № 55 от 04.02.2021", "totalGross": 793,
            "lines": [_line(1, "Разъём XLR")]}
    invoice = {**FIRST, "basis": None, "lines": FIRST["lines"] + LAST["lines"]}
    merged = closing.merge_pages([torg, invoice])
    assert merged["docKind"] == "TORG12_INVOICE" and merged["number"] == "А-17", "the header of the invoice"
    assert merged["basis"] == "Договор поставки № 55 от 04.02.2021", "the basis of the ТОРГ-12"
    assert [line["name"] for line in merged["lines"]] == ["Разъём XLR", "Кабель ВВГ 3х1,5", "Хомут 2,5х100"]
    doc = closing.closing_doc([torg, invoice])
    assert doc.kind == "TORG12_INVOICE" and doc.total_gross == 793 and doc.issues == []


def test_the_document_is_checked_after_the_merge_and_a_page_no_model_read_is_named():
    doc = closing.closing_doc([FIRST, LAST])
    assert (doc.status, doc.total_gross, len(doc.lines), doc.issues) == (closing.OK, 793, 3, [])
    short = closing.closing_doc([FIRST, None, {**LAST, "lines": []}])
    assert short.issues == ["модель не прочитала стр. 2 — их строк в разборе нет",
                            "сумма строк 732 ≠ итог документа 793"] and short.status == closing.CHECK
    corrected = closing.closing_doc([FIRST, LAST], header={"number": "А-17/1", "date": None})
    assert corrected.number == "А-17/1" and "нет даты документа" in corrected.issues, "the storekeeper's header wins"


def test_the_page_task_and_the_warehouse_fields():
    page = closing.prompt("", types=["Кабель", "Лента"], page=True)
    assert "ОДНА страница документа" in page and "позиции со всех страниц" not in page
    assert "makerArticle" in page and "ровно одно из: «Кабель»; «Лента»" in page and "shortName" in page
    whole = closing.prompt("")
    assert "позиции со всех страниц" in whole and "makerArticle" not in whole, "the registry asks no warehouse fields"
    assert "ДВА документа" in closing.prompt("TN") and "ДВА документа" not in closing.prompt("TN", page=True)
    text = closing.prompt("", "Счет-фактура № 7   от 20.10.2026\n\n  Продавец:  ООО Альфа")
    assert "<<<ТЕКСТ PDF\nСчет-фактура № 7 от 20.10.2026\nПродавец: ООО Альфа\nТЕКСТ PDF>>>" in text


def _reply(data: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}],
            "usage": {"total_tokens": 6200}}


class Calls:
    def __init__(self, answer):
        self.answer = answer
        self.sent: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, config, model, messages, options, timeout):
        with self.lock:
            self.sent.append({"model": model, "messages": messages, "options": options, "timeout": timeout})
        step = self.answer(messages, model) if callable(self.answer) else self.answer
        if isinstance(step, Exception):
            raise step
        return _reply(step)


def test_an_electronic_document_is_one_text_call_and_its_pictures_are_never_made(monkeypatch):
    calls = Calls({**FIRST, "lines": FIRST["lines"] + LAST["lines"], "totalGross": 793})
    monkeypatch.setattr(transport, "complete", calls)
    reading = reader.read_closing(CONFIG, text="Счет-фактура № А-17 от 20.10.2026", tag="",
                                  pictures=lambda: pytest.fail("a text layer needs no pictures"))
    assert (reading.source, reading.read, reading.model, reading.failed) == ("text", True, "gpt-5.6-luna", [])
    [call] = calls.sent
    assert isinstance(call["messages"][0]["content"], str) and "изображений нет" in call["messages"][0]["content"]
    assert call["options"]["response_format"] == {"type": "json_object"} and call["options"]["temperature"] == 0
    assert reading.doc().status == closing.OK


def test_a_scan_is_read_page_by_page_in_parallel_the_failed_page_stays_none(monkeypatch):
    def answer(messages, model):
        picture = messages[0]["content"][1]["image_url"]["url"]
        if picture.endswith(",Mg=="):  # b"2"
            return LlmError("HTTP 401: invalid key", status=401)
        return FIRST if picture.endswith(",MQ==") else LAST

    calls = Calls(answer)
    monkeypatch.setattr(transport, "complete", calls)
    reading = reader.read_closing(CONFIG, text="", pictures=lambda: [b"1", b"2", b"3"], types=["Кабель"])
    assert reading.source == "pages" and reading.pages[0] == FIRST and reading.pages[2] == LAST
    assert reading.pages[1] is None and reading.failed == [2] and "стр. 2" in reading.errors[0]
    assert all("ОДНА страница" in call["messages"][0]["content"][0]["text"] for call in calls.sent)
    assert sorted(call["model"] for call in calls.sent) == ["gemini-3.8-flash", "gpt-5.6-luna", "gpt-5.6-luna",
                                                           "gpt-5.6-luna"], "the refused page tried the fallback once"
    assert reader.read_closing(CONFIG, text="", pictures=lambda: []) == reader.ClosingReading()


def test_a_text_layer_counts_only_when_it_is_real_text():
    words = ["Счет-фактура № 123 от 20.10.2026", "Продавец: ООО «Ромашка Электро»"] + [
        "Маркировочная трубка термоусадочная белая в рулоне"] * 6
    assert "Ромашка" in reader.text_layer(identity_pdf(words))
    assert reader.text_layer(identity_pdf(["Счет № 1"])) == "", "a scan's stray text layer is not a document"
    assert reader.text_layer(b"%PDF-1.4 broken") == ""


def test_a_row_number_gap_on_seq_rereads_the_page(monkeypatch):
    glued = {"lines": [{"seq": n, "name": f"Провод {n}"} for n in (1, 2, 3, 5)]}
    full = {"lines": [{"seq": n, "name": f"Провод {n}"} for n in range(1, 6)]}
    answers = iter([glued, full])
    monkeypatch.setattr(transport, "complete", Calls(lambda messages, model: next(answers)))
    page = reader.read_page(CONFIG, b"jpeg", 1, deadline=10_000, clock=lambda: 0.0, prompt="p")
    assert page.data == full and reader.numbering_gap(glued) and not reader.numbering_gap({"lines": [{"seq": 8.0}]})
