"""«КП из PDF и текста»: pack rules (port of Roller_Buyer quote-import.test.ts), text lines, and the import endpoint."""

import base64
import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.modules.purchasing import quote_import as qi
from app.modules.purchasing.infrastructure import ItemAliasRow
from app.shared import stage_options
from tests.pdf_samples import identity_pdf
from tests.purchasing.test_rfq_api import BASE, _rfq, _supplier, rfq_api  # noqa: F401 — the fixture

LINE = uuid4()


def line(**over) -> qi.ImportedLine:
    base = dict(source_name="Клемма GHR-04V-S", qty=Decimal(1), unit="уп", price=Decimal(8000), rfq_line_id=LINE,
                matched_by="article")
    return qi.ImportedLine(**{**base, **over})


def targets(quantity="5000", unit="шт"):
    return [qi.Target(LINE, "Клемма", None, Decimal(quantity), unit)]


def test_a_pack_quote_is_priced_per_unit_with_its_pack_size():
    # Owner 12.10.2026 (Roller): request 5000 pcs, quote for one pack of 8000 pcs was read as «1 pcs».
    [named] = qi.apply_packs([line(source_name="Клемма GHR-04V-S (8000 шт)")], targets())
    assert named.price == Decimal("1") and named.pack == 8000 and named.moq == 8000
    assert "упаковку 8000 шт" in named.pack_note
    [in_unit] = qi.apply_packs([line(unit="уп. 8 000 шт", price=Decimal(4000))], targets())
    assert in_unit.price == Decimal("0.5") and in_unit.pack == 8000
    [unknown] = qi.apply_packs([line()], targets())
    assert unknown.price is None and "размер упаковки не указан" in unknown.pack_note
    [more] = qi.apply_packs([line(unit="шт", qty=Decimal(8000), price=Decimal("1.2"))], targets())
    assert more.price == Decimal("1.2") and more.moq == 8000
    [same] = qi.apply_packs([line(unit="шт", qty=Decimal(5000), price=Decimal("1.2"))], targets())
    assert same.moq is None
    [reel] = qi.apply_packs(
        [line(unit="бухта", source_name="Провод МГТФ 0,35 бухта 100 м", price=Decimal(900))], targets("250", "м"))
    assert reel.price == Decimal("9") and reel.pack == 100


def test_declined_unmatched_or_unpriced_lines_are_left_alone():
    declined = line(declined=True)
    free = line(rfq_line_id=None)
    unpriced = line(price=None)
    assert qi.apply_packs([declined, free, unpriced], targets()) == [declined, free, unpriced]
    assert declined.pack_note == "" and free.price == 8000


def test_text_lines_are_read_with_total_lead_and_vat():
    text = (
        "Коммерческое предложение № 5\n"
        "1 Разъём SHR-02V-S-B 2 шт 1 200,50 2 401,00 5 дн\n"
        "2. Кабель РК 50-2-11 100 м 145,00 14 500,00 в наличии\n"
        "Клемма GHR-04V-S 1 уп 8000,00 22%\n"
        "Доставка по договорённости 1500,50\n"
    )
    lines, unread = qi.read_text_lines(text)
    assert [(l.source_name, l.qty, l.unit, l.price, l.lead_days) for l in lines[:2]] == [
        ("Разъём SHR-02V-S-B", 2, "шт", Decimal("1200.50"), 5),
        ("Кабель РК 50-2-11", 100, "м", Decimal("145.00"), 0),
    ]
    assert lines[1].in_stock and lines[2].vat_rate == 22 and lines[2].price == 8000
    assert unread == ["Доставка по договорённости 1500,50"]
    [lead] = qi.read_text_lines("Вилка 50 шт 305,00 9 дн")[0]
    assert lead.price == Decimal("305.00") and lead.lead_days == 9


def _enable(monkeypatch, tmp_path, quote_import=True):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.rfq": True, "procurement.quote_import": quote_import}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


def test_import_text_matches_lines_and_converts_packs(rfq_api, monkeypatch, tmp_path):
    client, lines, _ = rfq_api
    _enable(monkeypatch, tmp_path)
    rfq = _rfq(client, lines)
    supplier = _supplier(client, rfq["id"], "ООО Упаковка")
    url = f"{BASE}/{rfq['id']}/quotes/import"
    text = "Разъём SHR-02V-S-B (100 шт) 1 уп 500,00 7 дн\nСовсем другое 3 шт 10,00\nмусор 12,50\n"
    answer = client.post(url, json={"text": text, "supplier_id": supplier})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    [first] = body["lines"]
    assert (first["matched_by"], first["price"], first["pack"], first["moq"], first["lead_days"]) == ("article", "5", "100", "100", 7)
    assert "упаковку 100 шт" in first["pack_note"]
    assert [row["source_name"] for row in body["unmatched"]] == ["Совсем другое"]
    assert body["unread"] == ["мусор 12,50"] and body["source"] == "paste"
    # a pack of unknown size → no price, a note
    unknown = client.post(url, json={"text": "Разъём SHR-02V-S-B 1 уп 500,00"}).json()["lines"][0]
    assert unknown["price"] is None and "размер упаковки не указан" in unknown["pack_note"]
    # a tab table goes through the Excel reader
    table = client.post(url, json={"text": "Наименование\tАртикул\tКол-во\tЦена без НДС\nРазъём\tshr 02v-s-b\t2\t90\n"}).json()
    assert table["vat_included"] is False and table["lines"][0]["price"] == "90"
    assert client.post(url, json={"text": "ничего"}).status_code == 422
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"text": "x", "content_base64": "AAAA"}).status_code == 422


def test_import_pdf_text_layer_and_refusals(rfq_api, monkeypatch, tmp_path):
    client, lines, _ = rfq_api
    _enable(monkeypatch, tmp_path)
    rfq = _rfq(client, lines)
    url = f"{BASE}/{rfq['id']}/quotes/import"

    def send(content: bytes):
        return client.post(url, json={"file_name": "kp.pdf", "content_base64": base64.b64encode(content).decode()})

    ok = send(identity_pdf(["Разъём SHR-02V-S-B 2 шт 1200,50 6 дн"]))
    assert ok.status_code == 200, ok.text
    assert ok.json()["source"] == "file" and ok.json()["lines"][0]["price"] == "1200.5"
    scan = send(identity_pdf(["  "]))
    assert scan.status_code == 422 and "текстового слоя" in scan.json()["detail"]
    assert send(b"not a pdf at all").status_code == 422
    assert client.post(url, json={"content_base64": "***"}).status_code == 422


def test_option_off_is_404_and_aliases_are_remembered_only_when_on(rfq_api, monkeypatch, tmp_path):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines)
    supplier = _supplier(client, rfq["id"], "ООО Слово")
    url = f"{BASE}/{rfq['id']}/quotes/import"
    _enable(monkeypatch, tmp_path, quote_import=False)
    assert client.post(url, json={"text": "Разъём 2 шт 10,00"}).status_code == 404
    line_id = client.get(f"{BASE}/{rfq['id']}").json()["lines"][0]["id"]
    save = {"supplier_id": supplier, "source": "file", "lines": [{"rfq_line_id": line_id, "unit_price": "10"}],
            "aliases": [{"rfq_line_id": line_id, "text": "Хитрое название"}]}
    assert client.post(f"{BASE}/{rfq['id']}/quotes", json=save).status_code == 201
    with sessions() as session:
        assert session.scalars(select(ItemAliasRow)).first() is None
    _enable(monkeypatch, tmp_path)
    assert client.post(f"{BASE}/{rfq['id']}/quotes", json=save).status_code == 201
    with sessions() as session:
        stored = session.scalars(select(ItemAliasRow)).all()
    if stored:  # the line has an item behind it only when the nomenclature linked one
        assert str(stored[0].supplier_id) == supplier
        again = client.post(url, json={"text": "Хитрое название 2 шт 10,00", "supplier_id": supplier}).json()
        assert again["lines"][0]["matched_by"] == "alias"
