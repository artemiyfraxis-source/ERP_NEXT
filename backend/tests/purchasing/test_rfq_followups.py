"""«Сегодня» over HTTP with the requests for quotes and the documents: «Нет ответа на запрос КП» per (request, supplier),
«Выбрать поставщика» per project, «Ждём счёт» after the decision, Kaiten invoices on their Moscow day, UPDs to check,
and «Дожать» of «Счета и поставки» reading the same rules."""

import json
from datetime import date, datetime
from uuid import uuid4

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing import settings as purchasing_settings
from app.modules.purchasing.infrastructure import InvoiceRow, OrderFactRow, UpdDocumentRow
from app.shared import stage_options
from app.shared.clock import moscow_now
from tests.purchasing.test_rfq_api import BASE, _as, _quote, _rfq, _supplier, rfq_api  # noqa: F401 — fixture

TODAY = "/api/procurement/today"


def _options(monkeypatch, tmp_path, **states):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.today": True, **states}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


def _clock(day: int, hour: int = 11) -> None:
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, day, hour, 30)


def _page(client) -> dict:
    response = client.get(TODAY)
    assert response.status_code == 200, response.text
    return response.json()


def _sections(client) -> dict[str, dict]:
    return {section["key"]: section for section in _page(client)["sections"]}


def _flow(client, lines):
    """One request with two lines; two suppliers sent on the 15th, one answered for line 0."""
    rfq = _rfq(client, lines)
    rfq_lines = client.get(f"{BASE}/{rfq['id']}").json()["lines"]
    first = next(item for item in rfq_lines if item["procurement_line_id"] == str(lines[0].id))
    silent = _supplier(client, rfq["id"], "ООО Молчит")
    answered = _supplier(client, rfq["id"], "ООО Ответил")
    for supplier in (silent, answered):
        assert client.post(f"{BASE}/{rfq['id']}/suppliers/{supplier}/sent").status_code == 200
    _quote(client, rfq["id"], answered, first["id"], unit_price="100")
    return rfq, first, silent, answered


def test_rfq_sections_need_the_rfq_option(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq, *_ = _flow(client, lines)
    _clock(20)
    sections = _sections(client)
    quotes = sections["quotes"]["items"]
    assert [(item["title"], item["detail"], item["days"]) for item in quotes] == [
        ("ООО Молчит", f"запрос {rfq['number']} от 15.09 · 2 позиции", 5),
    ]
    assert quotes[0]["link"] == {"tab": "rfq", "rfq": rfq["id"]}
    choose = sections["choose"]["items"][0]
    assert choose["detail"] == f"1 позиция · запрос {rfq['number']}" and choose["days"] == 5
    assert choose["link"] == {"tab": "rfq", "rfq": rfq["id"]}
    assert "rfq" not in sections  # both lines are in a live request: nothing more to ask
    _options(monkeypatch, tmp_path, **{"procurement.rfq": False})
    assert not {"quotes", "choose"} & set(_sections(client))


def test_no_answer_counts_from_quote_wait_days_on_and_skips_a_decline(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq, first, silent, answered = _flow(client, lines)

    _clock(17)  # two days after sending
    assert "quotes" not in _sections(client)
    _clock(18)  # RB: «days >= quoteWaitDays» — the third day counts
    assert _sections(client)["quotes"]["count"] == 1

    with sessions() as session:
        purchasing_settings.save(session, {"quote_wait_days": 10}, None, datetime(2026, 9, 18))
        session.commit()
    assert "quotes" not in _sections(client)
    with sessions() as session:
        purchasing_settings.save(session, {"quote_wait_days": 3}, None, datetime(2026, 9, 18))
        session.commit()

    declined = client.post(f"{BASE}/{rfq['id']}/suppliers/{silent}/declined", json={"note": "нет", "declined": True})
    assert declined.status_code == 200, declined.text
    assert "quotes" not in _sections(client)


def test_no_answer_counts_only_the_lines_asked_of_the_silent_supplier(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq = _rfq(client, lines)
    rfq_lines = client.get(f"{BASE}/{rfq['id']}").json()["lines"]
    first = next(item for item in rfq_lines if item["procurement_line_id"] == str(lines[0].id))
    silent = _supplier(client, rfq["id"], "ООО Молчит")
    asked = client.put(f"{BASE}/{rfq['id']}/suppliers/{silent}/lines", json={"line_ids": [first["id"]]})
    assert asked.status_code == 200, asked.text
    assert client.post(f"{BASE}/{rfq['id']}/suppliers/{silent}/sent").status_code == 200
    _clock(20)
    assert _sections(client)["quotes"]["items"][0]["detail"].endswith("· 1 позиция")


def test_a_decision_moves_the_line_from_choose_to_waiting_for_the_invoice(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq, first, silent, answered = _flow(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    cell = next(item for item in matrix["lines"] if item["id"] == first["id"])["cells"][answered]
    decided = client.post(f"{BASE}/{rfq['id']}/decisions", json={
        "rfq_line_id": first["id"], "quote_line_id": cell["quote_line_id"], "gate_override_reason": "Срочно",
    })
    assert decided.status_code == 201, decided.text
    sections = _sections(client)
    assert "choose" not in sections
    item = sections["invoice"]["items"][0]
    assert item["link"] == {"tab": "rfq", "rfq": rfq["id"], "invoices": "1"}
    assert item["detail"].startswith("ООО Ответил · ")
    # Only the other line of the request is still undecided: the silent supplier waits on it alone.
    _clock(20)
    assert _sections(client)["quotes"]["items"][0]["detail"].endswith("· 1 позиция")


def test_another_buyers_requests_and_lines_are_not_on_my_page(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    _flow(client, lines)
    _clock(20)
    _as("Сидоров")
    assert _page(client)["calm"] is True


def test_kaiten_invoices_count_their_moscow_day_and_twins_need_no_review(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"documents.chain": True, "documents.upd": True})
    now = datetime(2026, 9, 10, 9, 0)
    with sessions() as session:
        # 21:30 UTC on the 13th is 00:30 MSK on the 14th: that is the card's day.
        session.add(InvoiceRow(
            id=uuid4(), source="kaiten", external_id="k1", title="Счёт № 12", status="to_pay",
            card_created=datetime(2026, 9, 13, 21, 30), card_updated=now, seller_name="ООО Касса", total=1234500,
            synced_at=now, lines=[],
        ))
        session.add(InvoiceRow(
            id=uuid4(), source="kaiten", external_id="k2", title="Оплачен", status="paid", card_created=now,
            card_updated=now, synced_at=now, lines=[],
        ))
        for n, (verified, twin) in enumerate([(None, None), (now, None), (None, "edo-x"), (None, None)]):
            session.add(UpdDocumentRow(
                id=f"g-{n}", origin="google", file_name=f"{n}.pdf", status="Требует проверки", synced_at=now,
                created_at=now, verified_at=verified, twin_of=twin,
            ))
        session.add(UpdDocumentRow(id="g-ok", origin="google", file_name="ok.pdf", status="Разобран", synced_at=now,
                                  created_at=now))
        session.commit()
    _clock(15)
    sections = _sections(client)
    kaiten = sections["kaiten"]["items"][0]
    assert (kaiten["title"], kaiten["detail"], kaiten["days"]) == ("Счёт № 12", "ООО Касса · 12 345 ₽", 1)
    assert sections["upd"]["items"][0]["title"] == "2 документа"
    _options(monkeypatch, tmp_path)
    assert not {"kaiten", "upd"} & set(_sections(client))


def test_order_followups_on_the_orders_board_read_the_same_rules(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.orders": True})
    with sessions() as session:
        for line in lines:
            row = session.get(ProcurementLineRow, line.id)
            row.status, row.order_number, row.supplier = "В оплату", "PO-5", "ООО Счёт"
            row.invoice_url, row.invoice_date = "https://files.test/i.pdf", date(2026, 9, 10)
        session.add(OrderFactRow(
            order_number="PO-5", sent_to_pay_on=date(2026, 9, 11), shipment_note="", note="",
            created_at=datetime(2026, 9, 11), updated_at=datetime(2026, 9, 11), version=1,
        ))
        session.commit()
    pay = _sections(client)["pay"]["items"]
    assert [(item["title"], item["detail"]) for item in pay] == [("ООО Счёт · заказ PO-5", "в оплате · 2 позиции")]
    followups = client.get("/api/purchasing/orders/followups").json()
    assert [(item["order_number"], item["count"], item["days"]) for item in followups["unpaid"]] == [("PO-5", 2, 5)]
    # «Заявки» show the same fact: the order was handed over for payment.
    _options(monkeypatch, tmp_path, **{"procurement.requests_rb": True})
    view = client.get("/api/procurement/requests-view").json()
    assert view["projects"][0]["counts"] == {"to_pay": 2}
