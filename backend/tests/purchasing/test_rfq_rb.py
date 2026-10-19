"""«Запросы КП» brought to Roller_Buyer's parity (stand of Витя, 16.10.2026): letters, matrix details, withdraw,
«Подбор», invoice-request letters, quote import header, decision reason codes."""

from uuid import UUID, uuid4

from app.modules.purchasing.infrastructure import SupplierRow
from tests.purchasing.test_rfq_api import BASE, NOW, _quote, _rfq, _supplier, rfq_api  # noqa: F401 — the fixture


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
