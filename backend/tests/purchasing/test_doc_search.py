"""«Поиск по документам»: the matcher (Roller_Buyer domain/doc-search.test.ts) and the endpoint (test/doc-search.test.ts),
plus ERP's header and amount hits."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from app.modules.purchasing.doc_search import matches_all, query_amount, query_words, snippet, unit_gross
from app.modules.purchasing.infrastructure import InvoiceFileRow, InvoiceRow, UpdLineRow
from tests.purchasing.test_doc_chain_api import env, upd  # noqa: F401  (env is a fixture)

NOW = datetime(2026, 10, 13, 12, 0)


def test_an_article_is_found_however_it_is_written_case_separators_latin_look_alikes():
    assert matches_all("Кабель РК 50-3-11 (Россия)", query_words("pk50-3"))
    assert matches_all("Электроэлемент; PY04-7T@ACIT", query_words("py047t"))
    assert matches_all("Трубка ТУТнг-LS-6/3 черная", query_words("тут 6/3 черная"))
    assert matches_all("Провод МС 16-13 0,12 (чёрный)", query_words("мс16-13 черный"))
    # Every word must be there.
    assert not matches_all("Трубка ТУТнг-LS-6/3 белая", query_words("тут 6/3 черная"))
    # One-letter words say nothing; an empty query finds nothing.
    assert query_words("а  в") == []
    assert not matches_all("всё что угодно", query_words(""))


def test_the_snippet_shows_the_original_text_around_the_hit():
    text = "Счёт № 5 от 01.10.2026. 1 Разъём SMA-J RG-316 пайка 10 шт 120,00. 2 Доставка 1 500,00"
    assert snippet(text, query_words("smaj"), 10) == "…1 Разъём SMA-J RG-316 па…"
    assert snippet(text, query_words("нет такого")) is None


def test_an_amount_query_to_the_kopeck_or_to_the_rouble():
    assert query_amount("4 815,00").fits(481500) and not query_amount("4 815,00").fits(481550)
    assert query_amount("4815").fits(481550)
    assert query_amount("12 000 ₽").kop == 1200000
    assert query_amount("RG-316") is None


def test_unit_price_with_vat_of_a_upd_line():
    assert unit_gross(Decimal(10), 120000, Decimal(100), "20%") == 12000
    assert unit_gross(None, None, Decimal("100.5"), "22") == 12261
    assert unit_gross(None, None, Decimal(100), "без НДС") == 10000
    assert unit_gross(None, None, None, "20%") is None


def _line(sessions, doc_id, seq, name, source="google", **over):
    with sessions() as s:
        s.add(UpdLineRow(
            id=uuid4(), document_id=doc_id, source=source, seq=seq, name=name, quantity=Decimal(10), unit="шт",
            price=Decimal(100), gross=120000, vat_rate="20%", **over,
        ))
        s.commit()


def _invoice(sessions, **over):
    iid = uuid4()
    values = dict(
        id=iid, source="kaiten", external_id=str(iid), title="Счёт", status="paid", card_created=NOW, card_updated=NOW,
        synced_at=NOW, lines=[],
    )
    values.update(over)
    with sessions() as s:
        s.add(InvoiceRow(**values))
        s.commit()
    return iid


def search(client, q, kind="all"):
    res = client.get("/api/purchasing/doc-search", params={"q": q, "kind": kind})
    assert res.status_code == 200, res.text
    return res.json()


def test_a_position_is_found_in_upds_and_invoices_however_its_article_is_typed(env):  # noqa: F811
    client, sessions = env
    upd(sessions, "g-1", number="g-1")
    _line(sessions, "g-1", 1, "Разъём SMA-J RG-316 пайка")
    _line(sessions, "g-1", 2, "Доставка", is_service=True)
    # The buyer's saved lines are the document in force; the parser's old reading is not searched.
    upd(sessions, "g-2", number="g-2", doc_date=date(2026, 9, 20), manual_saved_at=NOW)
    _line(sessions, "g-2", 1, "Разъем SMA-J старое чтение")
    _line(sessions, "g-2", 1, "Разъём SMA J (RG-316)", source="manual")
    # A duplicate of g-1 stays out.
    upd(sessions, "g-3", number="g-3", twin_of="g-1")
    _line(sessions, "g-3", 1, "Разъём SMA-J RG-316 пайка")
    iid = _invoice(
        sessions, title="SMA-J на RG-316", number="43851", doc_date=date(2026, 9, 1), total=481500,
        lines=[{"seq": 1, "name": "Разъем SMA-J RG-316", "article": "SMA-J-316", "qty": 15, "unit": "шт", "price": 32100, "sum": 481500}],
    )
    pdf = uuid4()
    with sessions() as s:
        from app.modules.purchasing.infrastructure import DocumentFileRow

        s.add(DocumentFileRow(id=pdf, sha256="b" * 64, file_name="Счет 43851.pdf", size=4, content=b"%PDF", created_at=NOW))
        file_row = uuid4()
        s.add(InvoiceFileRow(id=file_row, invoice_id=iid, external_id="11", name="Счет 43851.pdf", kind="invoice", size=1, seq=0, file_id=pdf))
        s.commit()

    found = search(client, "sma-j rg316")
    assert [(h["kind"], h["id"], [line["name"] for line in h["lines"]]) for h in found["hits"]] == [
        ("upd", "g-2", ["Разъём SMA J (RG-316)"]),
        ("upd", "g-1", ["Разъём SMA-J RG-316 пайка"]),
        ("invoice", str(iid), ["Разъем SMA-J RG-316"]),
    ]
    assert found["hits"][1]["lines"][0]["price"] == 12000, "per unit with VAT"
    assert found["hits"][2]["file_id"] == str(file_row)
    assert found["found"] == 3

    assert [h["kind"] for h in search(client, "sma", "invoice")["hits"]] == ["invoice"]
    assert len(search(client, "доставка")["hits"]) == 1, "services are positions too"
    assert search(client, "х")["hits"] == [], "one letter finds nothing"
    assert client.get("/api/purchasing/doc-search", params={"kind": "fax"}).status_code == 422


def test_invoices_whose_positions_were_not_read_are_found_by_their_text_then_the_card_title(env):  # noqa: F811
    client, sessions = env
    _invoice(
        sessions, title="Счёт Мицар", status="to_pay",
        text="Счет на оплату № 5 от 02.09.2026 1 095-329-110 Устр-во для зачистки коаксиал. кабеля 2 шт 773,00 1 546,00 Итого",
    )
    _invoice(sessions, title="SMA-RG178 R/A", status="to_pay")
    hits = search(client, "095329110")["hits"]
    assert len(hits) == 1 and hits[0]["lines"] == [] and hits[0]["matched"] == "text"
    assert "095-329-110 Устр-во" in hits[0]["snippet"]
    by_title = search(client, "rg178")["hits"]
    assert [(h["matched"], h["snippet"]) for h in by_title] == [("title", "SMA-RG178 R/A")]


def test_documents_are_found_by_their_number_seller_and_total(env):  # noqa: F811
    client, sessions = env
    upd(sessions, "g-head-0001", number="УТ-1714", total_gross=1546000)
    upd(sessions, "g-head-0002", number="88", seller_name='ООО "Лютик"', seller_inn="7707083893", total_gross=5000)
    _invoice(sessions, number="АНС/2337", seller_name='ООО "Лютик"', total=1546000, doc_date=date(2026, 9, 2))
    assert [h["id"] for h in search(client, "ут1714")["hits"]] == ["g-head-0001"]
    assert [h["matched"] for h in search(client, "АНС 2337")["hits"]] == ["header"]
    assert sorted(h["kind"] for h in search(client, "лютик")["hits"]) == ["invoice", "upd"]
    by_sum = search(client, "15 460,00")["hits"]
    assert sorted((h["kind"], h["matched"]) for h in by_sum) == [("invoice", "amount"), ("upd", "amount")]
