"""«Запросы КП» brought to Roller_Buyer's parity (stand of Витя, 16.10.2026): letters, matrix details, withdraw,
«Подбор», invoice-request letters, quote import header, decision reason codes."""

from uuid import UUID, uuid4

from app.modules.purchasing.infrastructure import SupplierRow
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
