"""«Запросы КП» brought to Roller_Buyer's parity (stand of Витя, 16.10.2026): letters, matrix details, withdraw,
«Подбор», invoice-request letters, quote import header, decision reason codes."""

import base64
import hashlib
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing import rfq as rfq_service
from app.modules.purchasing import rfq_batch
from app.modules.purchasing.infrastructure import (
    DecisionRow, DocumentFileRow, InvoiceFileRow, InvoiceRow, ItemAliasRow, ItemRow, QuoteRow, RfqLineRow, RfqSupplierRow,
    SupplierRow,
)
from app.modules.purchasing.items import alias_key
from app.modules.purchasing.quote_import import read_header
from app.modules.purchasing.rfq_kaiten import invoice_vat, pack_in_name, quantity_in
from tests.pdf_samples import identity_pdf
from app.workflows.procurement_history import line_history
from app.workflows.procurement_rfq_requote import requote_lines
from tests.purchasing.test_rfq_api import BASE, NOW, _as, _quote, _rfq, _supplier, rfq_api  # noqa: F401 — the fixture


def _line_of(matrix: dict, procurement_line_id) -> dict:
    return next(line for line in matrix["lines"] if line["procurement_line_id"] == str(procurement_line_id))


def test_the_matrix_carries_what_the_letters_need(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    alfa = _supplier(client, rfq["id"], "ООО Альфа")
    with sessions() as session:
        session.get(SupplierRow, UUID(alfa)).email = "Иван Петров <sales@alfa.ru>, buh@alfa.ru"
        session.commit()
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    head = matrix["suppliers"][0]
    assert head["email"] == "sales@alfa.ru"  # the first address of the card's field
    assert matrix["settings"]["quote_wait_days"] == 3 and matrix["settings"]["delivery_default"] == "1500"
    line = _line_of(matrix, lines[0].id)
    assert line["article"] == "SHR-02V-S-B" and "project" in line and "plan_supplier" in line


def test_a_cell_shows_the_price_ladder_the_applied_break_the_packs_and_the_plan(rfq_api):
    client, lines, _ = rfq_api
    rfq = _rfq(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    line = _line_of(matrix, lines[0].id)  # 2 pcs, plan 110 with VAT
    tiers = _supplier(client, rfq["id"], "ООО Ступени")
    packs = _supplier(client, rfq["id"], "ООО Бухты")
    _quote(client, rfq["id"], tiers, line["id"], unit_price="100", vat_included=False, vat_rate="22", moq="10",
           tiers=[{"from": "10", "price": "80"}, {"from": "100", "price": "70"}])
    _quote(client, rfq["id"], packs, line["id"], unit_price="50", pack="4")
    cells = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)["cells"]
    ladder = cells[tiers]
    assert ladder["cover_qty"] == "10" and ladder["applied_from"] == "10" and ladder["gross_unit"] == "97.6"
    assert ladder["base_gross"] == "122" and ladder["tiers_gross"] == [{"from": "10", "gross": "97.6"}, {"from": "100", "gross": "85.4"}]
    assert ladder["plan_pct"] == "-11.3" and ladder["packs"] is None
    reel = cells[packs]
    assert reel["cover_qty"] == "4" and reel["packs"] == "1" and reel["applied_from"] is None and reel["plan_pct"] == "-54.5"


def test_a_quote_keeps_its_number_and_note_and_a_supplier_who_answers_unasked_counts_as_asked(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    line = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)
    asked = _supplier(client, rfq["id"], "ООО Спросили")  # in the request, not marked sent
    with sessions() as session:
        stranger = SupplierRow(id=uuid4(), name="ООО Сам пришёл", name_key="сам пришёл", categories=[], is_key=False,
                               domains=[], created_at=NOW, version=1)
        session.add(stranger)
        session.commit()
        stranger_id = str(stranger.id)
    for supplier_id, number in ((asked, " КП-17 "), (stranger_id, None)):
        body = {"supplier_id": supplier_id, "number": number, "note": "цены до конца месяца", "received_on": "2026-09-14",
                "lines": [{"rfq_line_id": line["id"], "unit_price": "100", "tiers": [{"from": "500", "price": "90"}]}]}
        assert client.post(f"{BASE}/{rfq['id']}/quotes", json=body).status_code == 201
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    heads = {head["supplier_id"]: head for head in matrix["suppliers"]}
    assert heads[stranger_id]["sent_at"].startswith("2026-09-14") and heads[asked]["sent_at"].startswith("2026-09-14")
    quote = next(quote for quote in matrix["quotes"] if quote["supplier_id"] == asked)
    assert quote["number"] == "КП-17" and quote["note"] == "цены до конца месяца" and quote["has_file"] is False
    cell = _line_of(matrix, lines[0].id)["cells"][asked]
    assert cell["quote_number"] == "КП-17" and cell["tiers"] == [{"from": "500", "price": "90"}]


# ── withdraw (Roller rfq-withdraw.test.ts) ──────────────────────────────────────────────────────────────────────────


def _withdraw(client, rfq_id, ids, to, **more):
    return client.post(f"{BASE}/{rfq_id}/lines/withdraw", json={"rfq_line_ids": ids, "to": to, **more})


def test_nobody_has_it_the_line_goes_to_requote_with_who_had_nothing_then_into_a_new_request(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    alfa = _supplier(client, rfq["id"], "Альфа")
    _supplier(client, rfq["id"], "Бета")
    line = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)
    _quote(client, rfq["id"], alfa, line["id"], declined=True)
    done = _withdraw(client, rfq["id"], [line["id"]], "requote")
    assert done.status_code == 200, done.text
    assert done.json() == {"withdrawn": 1}
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    assert all(item["id"] != line["id"] for item in matrix["lines"])
    removed = matrix["removed"][0]
    assert removed["reason"] == "requote" and removed["reason_label"] == "перезапрос"
    assert removed["note"] == "Запрос КП КП-2609-001 от 15.09.2026: Альфа — отказ; Бета — не ответил"
    with sessions() as session:
        state = requote_lines(session, [lines[0].id, lines[1].id])
    assert list(state) == [lines[0].id] and state[lines[0].id]["rfq_number"] == "КП-2609-001"
    # «Перезапрос» goes into the next request like a new line; then it is not «перезапрос» any more.
    assert str(lines[0].id) in {item["id"] for item in client.get(f"{BASE}/candidates").json()["lines"]}
    _rfq(client, lines[:1])
    with sessions() as session:
        assert requote_lines(session, [lines[0].id]) == {}
    with sessions() as session:
        events = line_history(session, lines[0].id)["events"]
    out = next(event for event in events if "Выведена из запроса" in event["title"])
    assert out["title"] == "Выведена из запроса КП-2609-001 — перезапрос" and "Альфа — отказ" in out["details"]


def test_back_to_new_a_line_with_prices_goes_only_on_confirmation_a_chosen_one_never(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    alfa = _supplier(client, rfq["id"], "Альфа")
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    first, second = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    _quote(client, rfq["id"], alfa, first["id"], unit_price="22.33")
    refused = _withdraw(client, rfq["id"], [first["id"]], "new")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "has_prices" and refused.json()["detail"]["lines"] == [first["name"]]
    assert "есть цены" in refused.json()["detail"]["message"]
    stale = _withdraw(client, rfq["id"], [first["id"]], "new", drop_prices=True, rfq_version=matrix["rfq"]["version"] + 5)
    assert stale.status_code == 409
    ok = _withdraw(client, rfq["id"], [first["id"]], "new", drop_prices=True, rfq_version=matrix["rfq"]["version"])
    assert ok.status_code == 200, ok.text
    removed = client.get(f"{BASE}/{rfq['id']}").json()["removed"][0]
    assert removed["reason"] == "new" and "Альфа — 22,33 ₽" in removed["note"]  # the price is not lost
    with sessions() as session:
        assert requote_lines(session, [lines[0].id]) == {}  # back to new is not «перезапрос»

    _quote(client, rfq["id"], alfa, second["id"], unit_price="500")
    cell = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[1].id)["cells"][alfa]
    decided = client.post(f"{BASE}/{rfq['id']}/decisions", json={
        "rfq_line_id": second["id"], "quote_line_id": cell["quote_line_id"], "gate_override_reason": "Срочно"})
    assert decided.status_code == 201, decided.text
    blocked = _withdraw(client, rfq["id"], [second["id"]], "new", drop_prices=True)
    assert blocked.status_code == 422 and "выбран поставщик" in blocked.json()["detail"]
    assert _withdraw(client, rfq["id"], [first["id"]], "requote").status_code == 422  # already out


def test_only_the_requests_buyer_takes_lines_out(rfq_api):
    client, lines, _ = rfq_api
    rfq = _rfq(client, lines)
    line = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)
    _as("Иванов")
    assert _withdraw(client, rfq["id"], [line["id"]], "new").status_code == 403
    assert _withdraw(client, rfq["id"], [line["id"]], "elsewhere").status_code == 422


# ── «Подбор» (Roller rfq-basket.test.ts) ────────────────────────────────────────────────────────────────────────────


def _three_quotes(client, rfq_id, line_x, line_y):
    alfa, beta, gamma = (_supplier(client, rfq_id, name) for name in ("Альфа", "Бета", "Гамма"))
    for supplier, (x, y) in ((alfa, ("10", "5.2")), (beta, ("11", "5")), (gamma, ("12", "6"))):
        body = {"supplier_id": supplier, "lines": [{"rfq_line_id": line_x, "unit_price": x}, {"rfq_line_id": line_y, "unit_price": y}]}
        assert client.post(f"{BASE}/{rfq_id}/quotes", json=body).status_code == 201
    return alfa, beta, gamma


def test_basket_chooses_the_whole_set_at_once_one_reason_covers_every_pick_that_needs_it(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    x, y = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    alfa, _, _ = _three_quotes(client, rfq["id"], x["id"], y["id"])
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    x, y = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    # All from Альфа: the second line is not its cheapest — one reason for it.
    picks = [{"rfq_line_id": x["id"], "quote_line_id": x["cells"][alfa]["quote_line_id"], "expected_version": x["version"]},
             {"rfq_line_id": y["id"], "quote_line_id": y["cells"][alfa]["quote_line_id"], "expected_version": y["version"]}]
    url = f"{BASE}/{rfq['id']}/decisions/batch"
    refused = client.post(url, json={"picks": picks})
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["code"] == "batch_refused" and [line["name"] for line in detail["lines"]] == [y["name"]]
    assert "не самое дешёвое" in detail["message"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DecisionRow)) == 0  # nothing written
    other = client.post(url, json={"picks": picks, "not_cheapest_code": "other"})
    assert other.status_code == 422 and "опишите причину" in other.text

    ok = client.post(url, json={"picks": picks, "not_cheapest_code": "other", "not_cheapest_note": "Подбор: дешевле с доставкой"})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"decided": 2, "kept": 0}
    after = client.get(f"{BASE}/{rfq['id']}").json()
    decisions = [(_line_of(after, line.id)["decision"]["not_cheapest_code"], _line_of(after, line.id)["decision"]["not_cheapest_reason"])
                 for line in lines]
    assert decisions == [(None, None), ("other", "Подбор: дешевле с доставкой")]  # the reason stays only where needed
    with sessions() as session:
        assert {session.get(ProcurementLineRow, line.id).supplier for line in lines} == {"Альфа"}
    # Saved again, the same set is kept as is.
    again = client.post(url, json={"picks": [{**pick, "expected_version": None} for pick in picks]})
    assert again.status_code == 200 and again.json() == {"decided": 0, "kept": 2}


def test_basket_stops_on_a_stale_line_before_writing_and_reports_a_failure_midway(rfq_api, monkeypatch):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    x, y = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    _, beta, _ = _three_quotes(client, rfq["id"], x["id"], y["id"])
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    x, y = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    picks = [{"rfq_line_id": x["id"], "quote_line_id": x["cells"][beta]["quote_line_id"], "expected_version": x["version"]},
             {"rfq_line_id": y["id"], "quote_line_id": y["cells"][beta]["quote_line_id"], "expected_version": y["version"] + 1}]
    body = {"picks": picks, "not_cheapest_code": "lead_time"}
    stale = client.post(f"{BASE}/{rfq['id']}/decisions/batch", json=body)
    assert stale.status_code == 422 and "обновите" in stale.json()["detail"]["lines"][0]["message"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DecisionRow)) == 0

    picks[1]["expected_version"] = y["version"]
    real_decide = rfq_batch.decide
    calls = []

    def failing_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise rfq_service.LineChanged("Строку закупки изменили — обновите")
        return real_decide(*args, **kwargs)

    monkeypatch.setattr(rfq_batch, "decide", failing_second)
    partial = client.post(f"{BASE}/{rfq['id']}/decisions/batch", json=body)
    assert partial.status_code == 409
    detail = partial.json()["detail"]
    assert detail["code"] == "batch_partial" and detail["decided"] == [x["name"]] and detail["failed"]["name"] == y["name"]
    assert detail["not_done"] == [] and "Выбор записан" in detail["message"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DecisionRow)) == 1


# ── «Запросить счета» (Roller invoice-request.test.ts) ─────────────────────────────────────────────────────────────


def test_one_invoice_request_per_chosen_supplier_whole_packs_the_break_the_vat_mode_delivery(rfq_api):
    client, lines, sessions = rfq_api
    with sessions() as session:
        for line, quantity in zip(lines, (Decimal("1000"), Decimal("1500"))):
            row = session.get(ProcurementLineRow, line.id)
            row.calculated_quantity, row.calculated_unit, row.design_article = quantity, "м", None
        session.get(ProcurementLineRow, lines[0].id).calculated_name = "Силикон 28AWG белый"
        session.get(ProcurementLineRow, lines[1].id).calculated_name = "ПВАМ 0,35 красный"
        session.commit()
    rfq = _rfq(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    silicone, pvam = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    prodiel = _supplier(client, rfq["id"], 'ООО "Продиэл"')
    delta = _supplier(client, rfq["id"], 'ООО "Дельта-юниверсум"')
    # Продиэл: reels of 100 m at 19,25 ₽, from 500 m — 14,01 ₽ (a 500 m reel), with VAT, from its site.
    assert client.post(f"{BASE}/{rfq['id']}/quotes", json={
        "supplier_id": prodiel, "number": "сайт Продиэлком", "received_on": "2026-09-14",
        "lines": [{"rfq_line_id": silicone["id"], "unit_price": "19.25", "pack": "100", "tiers": [{"from": "500", "price": "14.01"}]}],
    }).status_code == 201
    # Дельта: an invoice-quote without VAT, delivery 1 500 ₽.
    assert client.post(f"{BASE}/{rfq['id']}/quotes", json={
        "supplier_id": delta, "number": "ДЛТ/1990", "received_on": "2026-09-14", "delivery_cost": "1500",
        "lines": [{"rfq_line_id": pvam["id"], "unit_price": "7.54", "vat_included": False, "vat_rate": "22"}],
    }).status_code == 201
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    for line, supplier in ((lines[0], prodiel), (lines[1], delta)):
        row = _line_of(matrix, line.id)
        decided = client.post(f"{BASE}/{rfq['id']}/decisions", json={
            "rfq_line_id": row["id"], "quote_line_id": row["cells"][supplier]["quote_line_id"], "gate_override_code": "single_source"})
        assert decided.status_code == 201, decided.text
    letters = client.get(f"{BASE}/{rfq['id']}/invoice-requests").json()["requests"]
    by_name = {letter["supplier_name"]: letter for letter in letters}
    first, second = by_name['ООО "Продиэл"'], by_name['ООО "Дельта-юниверсум"']
    assert first["subject"] == "Запрос счёта — предложение от 14.09.2026 (сайт Продиэлком), 1 поз."
    assert "по вашему предложению от 14.09.2026 (сайт Продиэлком)" in first["body"]
    # At a price break the reel is not guessed: 14,01 ₽ is the 500 m reel's price, not ten 100 m ones.
    assert "1. Силикон 28AWG белый — 1 000 м × 14,01 ₽ = 14 010,00 ₽" in first["body"]
    assert "Итого по ценам предложения: 14 010,00 ₽ с НДС." in first["body"]
    assert "просим включить её стоимость в счёт" in first["body"]
    assert "ИНН 5040191181, КПП 504001001" in first["body"]
    assert first["cart"] == "Силикон 28AWG белый\t1 000 м"
    assert second["subject"] == "Запрос счёта — КП № ДЛТ/1990 от 14.09.2026, 1 поз."
    assert "ПВАМ 0,35 красный — 1 500 м × 7,54 ₽ = 11 310,00 ₽" in second["body"]
    assert "Итого по ценам предложения: 11 310,00 ₽ без НДС." in second["body"]
    assert "Доставка — 1 500,00 ₽, как в предложении" in second["body"]
    assert letters[0]["supplier_name"] == 'ООО "Продиэл"'  # dearest first


def test_whole_reels_at_the_base_price_and_the_requisites_from_the_settings(rfq_api):
    client, lines, sessions = rfq_api
    with sessions() as session:
        row = session.get(ProcurementLineRow, lines[0].id)
        row.calculated_quantity, row.calculated_unit = Decimal("150"), "м"
        session.commit()
    rfq = _rfq(client, lines[:1])
    line = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)
    alfa = _supplier(client, rfq["id"], "Альфа")
    _quote(client, rfq["id"], alfa, line["id"], unit_price="10", pack="100")
    cell = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)["cells"][alfa]
    assert client.post(f"{BASE}/{rfq['id']}/decisions", json={
        "rfq_line_id": line["id"], "quote_line_id": cell["quote_line_id"], "gate_override_code": "urgent"}).status_code == 201
    with sessions() as session:
        from app.modules.purchasing import settings as purchasing_settings
        purchasing_settings.save(session, {"letter_requisites": "ООО Тест, ИНН 7700000000\r\n  Москва  "}, None, NOW)
        session.commit()
    letter = client.get(f"{BASE}/{rfq['id']}/invoice-requests").json()["requests"][0]
    assert "— 200 м (2 × 100 м) × 10,00 ₽ = 2 000,00 ₽" in letter["body"]
    assert "Покупатель:\nООО Тест, ИНН 7700000000\nМосква" in letter["body"]
    assert letter["cart"].endswith("\t2 × 100 м")


# ── quote import: the header, Excel columns, the PDF kept with the quote ────────────────────────────────────────────


def test_the_header_is_read_by_regex_our_own_inn_skipped():
    head = read_header("ООО Альфа ИНН 7701234567 КПП 770101001\nПокупатель: ЗЭП ФРАКСИС ИНН 5040191181\n"
                       "Коммерческое предложение № КП-123/45 от 14.10.2026\nПредложение действительно до 30.10.2026",
                       date(2026, 10, 14))
    assert head == {"number": "КП-123/45", "valid_until": "2026-10-30", "inn": "7701234567"}
    bill = read_header("Счет на оплату № 1990 от 14.10.26\nИНН/КПП: 5040191181/504001001\nПоставщик ИНН 772800000000\n"
                       "Цены действительны в течение 10 рабочих дней", date(2026, 10, 14))
    assert bill == {"number": "1990", "valid_until": "2026-10-28", "inn": "772800000000"}
    assert read_header("просто текст", date(2026, 10, 14)) == {"number": "", "valid_until": None, "inn": ""}


def test_excel_paste_takes_the_buyers_columns_and_vat_and_remembered_wordings(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    supplier = _supplier(client, rfq["id"], "ООО Таблица")
    url = f"{BASE}/{rfq['id']}/quotes/paste"
    text = "Поз\tТовар\tКод\tШт\tЦена\n1\tКлеммник хитрый\tXX-1\t2\t100\n"
    guessed = client.post(url, json={"text": text, "supplier_id": supplier}).json()
    assert guessed["width"] == 5 and guessed["column_labels"]["price"] == "Цена" and guessed["vat_included"] is True
    remapped = client.post(url, json={"text": text, "supplier_id": supplier, "vat_included": False,
                                      "columns": {"name": 1, "article": 2, "qty": 3, "price": 4}}).json()
    assert remapped["columns"]["name"] == 1 and remapped["vat_included"] is False
    row = remapped["rows"][0]
    assert (row["source_name"], row["source_article"], row["qty"], row["price"]) == ("Клеммник хитрый", "XX-1", "2", "100")
    assert row["rfq_line_id"] is None  # nothing in common with the request's lines yet
    # Remembered: this supplier calls our line «Клеммник хитрый XX-1».
    line_id = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)["id"]
    with sessions() as session:
        item = ItemRow(id=uuid4(), name="Разъём", category="connector", unit="шт", created_at=NOW, version=1)
        session.add(item)
        session.flush()
        session.get(RfqLineRow, UUID(line_id)).item_id = item.id
        session.add(ItemAliasRow(id=uuid4(), item_id=item.id, supplier_id=UUID(supplier), alias_text="XX-1 Клеммник хитрый",
                                 alias_key=alias_key("XX-1 Клеммник хитрый"), created_at=NOW))
        session.commit()
    again = client.post(url, json={"text": text, "supplier_id": supplier, "columns": {"name": 1, "article": 2, "qty": 3, "price": 4}}).json()
    assert (again["rows"][0]["rfq_line_id"], again["rows"][0]["matched_by"]) == (line_id, "alias")


def test_the_pdf_a_quote_came_in_is_kept_with_it(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    supplier = _supplier(client, rfq["id"], "ООО Файл")
    line_id = _line_of(client.get(f"{BASE}/{rfq['id']}").json(), lines[0].id)["id"]
    pdf = identity_pdf(["Разъём SHR-02V-S-B 2 шт 100,00"])
    body = {"supplier_id": supplier, "source": "file", "number": "17", "file_name": "КП Файл.pdf",
            "file_base64": base64.b64encode(pdf).decode(), "lines": [{"rfq_line_id": line_id, "unit_price": "100"}]}
    saved = client.post(f"{BASE}/{rfq['id']}/quotes", json=body)
    assert saved.status_code == 201, saved.text
    quote = next(item for item in client.get(f"{BASE}/{rfq['id']}").json()["quotes"] if item["id"] == saved.json()["id"])
    assert quote["has_file"] is True
    served = client.get(f"{BASE}/quotes/{quote['id']}/file")
    assert served.status_code == 200 and served.content == pdf and served.headers["content-type"] == "application/pdf"
    second = client.post(f"{BASE}/{rfq['id']}/quotes", json={**body, "number": "18"})
    assert second.status_code == 201
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DocumentFileRow)) == 1  # the same file is kept once
    not_pdf = client.post(f"{BASE}/{rfq['id']}/quotes", json={**body, "file_base64": base64.b64encode(b"<html>").decode()})
    assert not_pdf.status_code == 422 and "только PDF" in not_pdf.json()["detail"]
    plain = client.post(f"{BASE}/{rfq['id']}/quotes", json={**body, "file_base64": None, "file_name": None})
    assert client.get(f"{BASE}/quotes/{plain.json()['id']}/file").status_code == 404


def test_a_decision_takes_rollers_reason_codes_and_keeps_the_label_or_the_words(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    x, y = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    alfa = _supplier(client, rfq["id"], "Альфа")
    _quote(client, rfq["id"], alfa, x["id"], unit_price="10")
    _quote(client, rfq["id"], alfa, y["id"], unit_price="10")
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    url = f"{BASE}/{rfq['id']}/decisions"
    pick = lambda line: {"rfq_line_id": line["id"], "quote_line_id": _line_of(matrix, line["procurement_line_id"])["cells"][alfa]["quote_line_id"]}  # noqa: E731
    words = client.post(url, json={**pick(x), "gate_override_code": "other"})
    assert words.status_code == 422 and "опишите причину" in words.json()["detail"]
    bad = client.post(url, json={**pick(x), "gate_override_code": "cheaper"})
    assert bad.status_code == 422
    assert client.post(url, json={**pick(x), "gate_override_code": "single_source"}).status_code == 201
    assert client.post(url, json={**pick(y), "gate_override_code": "other", "gate_override_note": "  только у них  "}).status_code == 201
    after = client.get(f"{BASE}/{rfq['id']}").json()
    assert [(_line_of(after, line.id)["decision"]["gate_override_code"], _line_of(after, line.id)["decision"]["gate_override_reason"])
            for line in lines] == [("single_source", "Единственный производитель или поставщик"), ("other", "только у них")]


# ── Kaiten invoices in a request (Roller rfq-kaiten.test.ts) ────────────────────────────────────────────────────────


def test_invoice_vat_mode_units_and_packs():
    lines = [{"seq": 1, "name": "Провод", "qty": 10, "unit": "м", "price": 1000, "sum": 10_000}]
    assert invoice_vat(12_200, 2200, False, lines, Decimal(22)) == (False, Decimal(22))  # positions without VAT
    assert invoice_vat(10_000, 1667, False, lines, Decimal(22)) == (True, Decimal(20))
    assert invoice_vat(10_000, None, True, lines, Decimal(22)) == (True, Decimal(0))
    # Two rates (goods 22 %, delivery 5 %) and the 5 % figure read: not trusted.
    assert invoice_vat(10_000, 46, False, lines, Decimal(22)) == (True, Decimal(22))
    assert quantity_in(Decimal("0.5"), "км", "м", None) == Decimal("500.000")
    assert quantity_in(2, "бухта", "м", Decimal(305)) == Decimal("610.000")
    assert quantity_in(2, "бухта", "м", None) is None
    assert pack_in_name("SHF-001T##5000@JST упаковка 5000 шт.", "") == Decimal(5000)
    assert pack_in_name("Терминал (упаковка 5000 шт)", "шт") is None  # counted in pieces already


def _kaiten_setup(client, lines, sessions):
    with sessions() as session:
        specs = (("Кабель коаксиальный RG-316", "RG-316", "100", "м"), ("Разъём SMA-J угловой", "SMA-J-01", "20", "шт"))
        for line, (name, article, quantity, unit) in zip(lines, specs):
            row = session.get(ProcurementLineRow, line.id)
            row.calculated_name, row.design_article = name, article
            row.calculated_quantity, row.calculated_unit = Decimal(quantity), unit
            row.actual_quantity, row.actual_unit = Decimal(quantity), unit
        session.commit()
    rfq = _rfq(client, lines)
    alfa = _supplier(client, rfq["id"], "Альфа")
    beta = _supplier(client, rfq["id"], "Бета")
    _supplier(client, rfq["id"], "Гамма")
    with sessions() as session:
        session.get(SupplierRow, UUID(alfa)).inn = "7701000019"
        session.commit()
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    cable, sma = _line_of(matrix, lines[0].id), _line_of(matrix, lines[1].id)
    _quote(client, rfq["id"], beta, cable["id"], unit_price="45")  # B quoted the cable cheaper than the invoice will
    return rfq, alfa, beta, cable, sma


def _invoice(sessions, supplier_id, number, day, lines, file=False):
    with sessions() as session:
        total = sum(line["sum"] for line in lines)
        row = InvoiceRow(id=uuid4(), source="kaiten", external_id=number, title=f"Счет № {number}", status="paid",
                         card_created=datetime(2026, 9, 16, 9), card_updated=datetime(2026, 9, 17, 9), number=number,
                         doc_date=day, supplier_id=UUID(supplier_id) if supplier_id else None, total=total,
                         vat=round(total * 22 / 122), lines=lines, synced_at=datetime(2026, 9, 17))
        session.add(row)
        if file:
            content = identity_pdf([f"Счет № {number}"])
            stored = DocumentFileRow(id=uuid4(), sha256=hashlib.sha256(content).hexdigest(), file_name=f"Счет {number}.pdf",
                                     size=len(content), content=content, created_at=NOW)
            session.add(stored)
            session.flush()
            session.add(InvoiceFileRow(id=uuid4(), invoice_id=row.id, external_id=f"f{number}", name=f"Счет {number}.pdf",
                                       kind="invoice", size=len(content), seq=0, file_id=stored.id))
        session.commit()
        return str(row.id)


def _goods():
    return [
        {"seq": 1, "name": "Кабель коаксиальный", "article": "RG-316", "qty": 0.1, "unit": "км", "price": 4_800_000, "sum": 480_000},
        {"seq": 2, "name": "Разъём SMA-J-01 угловой", "article": "", "qty": 20, "unit": "шт", "price": 15_000, "sum": 300_000},
        {"seq": 3, "name": "Доставка до терминала", "article": "", "qty": 1, "unit": "усл", "price": 50_000, "sum": 50_000},
        {"seq": 4, "name": "Паяльник", "article": "", "qty": 1, "unit": "шт", "price": 100_000, "sum": 100_000},
    ]


def test_an_invoice_dated_after_the_request_is_offered_with_its_positions_matched(rfq_api):
    client, lines, sessions = rfq_api
    rfq, alfa, _, cable, sma = _kaiten_setup(client, lines, sessions)
    invoice = _invoice(sessions, alfa, "СЧ-77", date(2026, 9, 16), _goods())
    _invoice(sessions, alfa, "СЧ-1", date(2026, 9, 1), _goods())  # predates the request
    _invoice(sessions, None, "Х-5", date(2026, 9, 16), [])  # a scan: no positions, nobody's
    answer = client.get(f"{BASE}/{rfq['id']}/kaiten").json()
    assert answer["rfq_date"] == "2026-09-15" and answer["unread"] == 1
    assert [item["invoice_id"] for item in answer["invoices"]] == [invoice]
    got = answer["invoices"][0]
    assert (got["supplier_id"], got["matched"], got["vat_included"], got["vat_rate"], got["delivery_gross"]) == (alfa, 2, True, "22", "500")
    assert [(line["targets"], line["delivery"]) for line in got["lines"]] == [
        ([{"rfq_line_id": cable["id"], "quantity": "100"}], False),  # 0,1 км is 100 м
        ([{"rfq_line_id": sma["id"], "quantity": "20"}], False),
        ([], True),
        ([], False),
    ]


def test_oformit_makes_the_quote_from_the_invoice_the_choice_and_the_order_through_procurement(rfq_api):
    client, lines, sessions = rfq_api
    rfq, alfa, _, cable, sma = _kaiten_setup(client, lines, sessions)
    invoice = _invoice(sessions, alfa, "СЧ-77", date(2026, 9, 16), _goods())
    url = f"{BASE}/{rfq['id']}/kaiten/{invoice}"
    picks = [{"seq": 1, "rfq_line_id": cable["id"], "quantity": "100"}, {"seq": 2, "rfq_line_id": sma["id"], "quantity": "20"}]

    dry = client.post(url, json={"picks": picks, "dry_run": True})
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert body["needs"] == {"gate": 2, "not_cheapest": 1, "analog": 0}, body  # two answers of three; B is cheaper on the cable
    assert [(line["offer"], line["decision"], line["unit_price"]) for line in body["lines"]] == [
        ("invoice", "new", "48"), ("invoice", "new", "150")]
    assert body["problems"] == [] and body["has_file"] is False
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(QuoteRow).where(QuoteRow.source == "invoice")) == 0

    no_reason = client.post(url, json={"picks": picks, "expected_arrival": "2026-09-25"})
    assert no_reason.status_code == 422 and "причин" in no_reason.text
    reasons = {"gate_override_code": "urgent", "not_cheapest_code": "lead_time"}
    no_file = client.post(url, json={"picks": picks, "expected_arrival": "2026-09-25", **reasons})
    assert no_file.status_code == 422 and "Файл счёта" in no_file.json()["detail"]

    invoice = _invoice(sessions, alfa, "СЧ-78", date(2026, 9, 16), _goods(), file=True)
    url = f"{BASE}/{rfq['id']}/kaiten/{invoice}"
    no_date = client.post(url, json={"picks": picks, **reasons})
    assert no_date.status_code == 422 and "ожидаемую дату" in no_date.json()["detail"]
    done = client.post(url, json={"picks": picks, "expected_arrival": "2026-09-25", **reasons})
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["done"][0] == "КП из счёта" and len(result["orders"]) == 1
    with sessions() as session:
        rows = [session.get(ProcurementLineRow, line.id) for line in lines]
        assert [(row.status, row.supplier, row.invoice_number, row.invoice_date, row.expected_arrival) for row in rows] == [
            ("В оплату", "Альфа", "СЧ-78", date(2026, 9, 16), date(2026, 9, 25))] * 2
        assert rows[0].order_number == rows[1].order_number == result["orders"][0]
        assert rows[0].invoice_url.startswith("/api/procurement/files/")
        quote = session.scalar(select(QuoteRow).where(QuoteRow.source == "invoice"))
        assert (quote.number, quote.received_on, str(quote.invoice_id), quote.file_id is not None) == ("СЧ-78", date(2026, 9, 16), invoice, True)
    after = client.get(f"{BASE}/{rfq['id']}").json()
    assert [(_line_of(after, line.id)["decision"]["gate_override_code"], _line_of(after, line.id)["editable"]) for line in lines] == [
        ("urgent", False), ("urgent", False)]
    again = client.get(f"{BASE}/{rfq['id']}/kaiten").json()["invoices"]
    mine = next(item for item in again if item["invoice_id"] == invoice)
    assert mine["matched"] == 0 and len(mine["ordered_lines"]) == 2
    twice = client.post(url, json={"picks": picks, "expected_arrival": "2026-09-25", **reasons})
    assert twice.status_code == 422  # the lines are ordered already


def test_an_invoice_older_than_the_request_cannot_be_its_order(rfq_api):
    client, lines, sessions = rfq_api
    rfq, alfa, _, cable, _ = _kaiten_setup(client, lines, sessions)
    invoice = _invoice(sessions, alfa, "СЧ-1", date(2026, 9, 1), _goods())
    answer = client.post(f"{BASE}/{rfq['id']}/kaiten/{invoice}", json={
        "picks": [{"seq": 1, "rfq_line_id": cable["id"], "quantity": "100"}], "dry_run": True})
    assert answer.status_code == 422 and "раньше запроса КП" in answer.json()["detail"]


# ── creating a request: the send date, a declined rotation suggestion ───────────────────────────────────────────────


def test_a_request_created_as_sent_and_with_a_declined_rotation_suggestion(rfq_api):
    client, lines, sessions = rfq_api
    with sessions() as session:
        rotation = SupplierRow(id=uuid4(), name="ООО Давно", name_key="давно", categories=[], is_key=False, domains=[],
                               created_at=NOW, version=1)
        session.add(rotation)
        session.commit()
        rotation_id = str(rotation.id)
    body = {"line_ids": [str(lines[0].id)], "suppliers": [{"name": "ООО Ключ"}], "sent_on": "2026-09-14",
            "declined_suggestions": [rotation_id]}
    both = client.post(BASE, json={**body, "suppliers": [{"supplier_id": rotation_id}]})
    assert both.status_code == 422 and "отказе от подсказки" in both.json()["detail"]
    future = client.post(BASE, json={**body, "sent_on": "2026-12-01"})
    assert future.status_code == 422
    created = client.post(BASE, json=body)
    assert created.status_code == 201, created.text
    matrix = client.get(f"{BASE}/{created.json()['id']}").json()
    assert matrix["suppliers"][0]["sent_at"].startswith("2026-09-14")
    assert matrix["declined_suggestions"] == [{"supplier_id": rotation_id, "name": "ООО Давно"}]
    # The declined one is not asked: rotation still sees him as never asked.
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RfqSupplierRow).where(RfqSupplierRow.supplier_id == UUID(rotation_id))) == 0

