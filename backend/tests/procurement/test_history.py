"""«История строки»: the line's life rebuilt from stored facts, from the request to the warehouse."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.receiving.application import consume_receiving_expected
from app.modules.receiving.infrastructure import ReceivingLineRow, SqlAlchemyReceivingUnitOfWork
from app.modules.requests.infrastructure import RequestRow
from app.modules.warehouse.infrastructure import StockLotRow, WriteOffEntryRow
from app.shared.events import OutboxMessage
from app.shared.persistence import AuditEventRow
from tests.purchasing.test_rfq_api import BASE, _quote, _rfq, _supplier, rfq_api  # noqa: F401 — fixture


def _audit(session, aggregate_id, kind, when, details=None, name="Петров", department="Закупки"):
    session.add(AuditEventRow(
        id=uuid4(), aggregate_id=aggregate_id, event_type=kind, actor_id=uuid4(), actor_name=name,
        actor_department=department, occurred_at=when, details=details or {},
    ))


def _seed_request(session, line):
    session.add(RequestRow(
        id=line.source_request_id, legacy_id="SM-20260915-01", creation_key="k", creation_index=0,
        creation_fingerprint="f", designation="RND.001", status="В закупке", version=1,
        created_at=datetime(2026, 9, 14, 9, 0), actor_id=uuid4(), actor_name="Иванов", actor_department="РНД",
        task_type="Закупка", priority="Срочно", purpose="RND.001", delivery_point="Москва", name="Разъём",
        item_type="Комплектующие", category="Общепром", quantity=Decimal("2"), unit="шт", quality_document="Нет",
        procurement_deadline=date(2026, 9, 20), planned_date=date(2026, 9, 25),
    ))


def _seed_tail(sessions, line):
    """Receiving, the lot and its journal for the line: what happens after the invoice."""
    consume_receiving_expected(
        OutboxMessage(
            id=uuid4(), topic="receiving.expected", idempotency_key=f"procurement:{line.id}:receiving:v1",
            created_at=datetime(2026, 9, 16, 12, 0),
            payload={
                "procurement_line_id": str(line.id), "legacy_id": line.legacy_id, "warehouse": "ЦАР", "article": "SHR",
                "name": "Разъём", "item_type": "Комплектующие", "purpose": "RND.001", "goal": "", "source_link": "",
                "supplier": "ООО Дёшево", "ordered_quantity": "3", "unit": "шт", "quality_document_required": False,
            },
        ),
        SqlAlchemyReceivingUnitOfWork(sessions),
    )
    with sessions() as session:
        delivery = session.query(ReceivingLineRow).filter_by(procurement_line_id=line.id).one()
        delivery.grn, delivery.package, delivery.verdict = "ГРН-0001", "", "Годен"
        delivery.accepted_quantity, delivery.status = Decimal("3"), "Принято"
        lot_id = uuid4()
        session.add(StockLotRow(
            id=lot_id, version=1, warehouse="ЦАР", grn="ГРН-0001", package="", article="SHR", series="", name="Разъём",
            item_type="Комплектующие", purpose="RND.001", unit="шт", storage_location="A-1", inbound=Decimal("3"),
            written_off=Decimal("1"), reserved=Decimal("0"), source_receiving_ids=[str(delivery.id)],
        ))
        _audit(session, delivery.id, "receiving.inspected", datetime(2026, 9, 18, 10, 0),
               {"from_status": "В пути", "to_status": "Принято"}, "Кладовщик", "Склад")
        session.add(WriteOffEntryRow(
            id=uuid4(), occurred_at=datetime(2026, 9, 19, 15, 0), kind="issue", lot_id=lot_id, warehouse="ЦАР",
            grn="ГРН-0001", package="", name="Разъём", quantity=Decimal("1"), unit="шт", reason="Комплектация",
            purpose="RND.001", author="Кладовщик", posted=True, remaining_after=Decimal("2"), actor_id=uuid4(),
            actor_name="Кладовщик",
        ))
        session.commit()


def test_history_follows_the_line_from_request_to_issue(rfq_api):  # noqa: F811
    client, lines, sessions = rfq_api
    line = lines[0]
    rfq = _rfq(client, lines)
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    rfq_line = next(item for item in matrix["lines"] if item["procurement_line_id"] == str(line.id))
    key = _supplier(client, rfq["id"], "ООО Ключевой", sessions, key=True)
    cheap = _supplier(client, rfq["id"], "ООО Дёшево")
    assert client.post(f"{BASE}/{rfq['id']}/suppliers/{key}/sent").status_code == 200
    _quote(client, rfq["id"], key, rfq_line["id"], unit_price="120")
    cheap_quote = _quote(client, rfq["id"], cheap, rfq_line["id"], unit_price="90", vat_included=False, vat_rate="22", pack="3")
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    cells = next(item for item in matrix["lines"] if item["id"] == rfq_line["id"])["cells"]
    decided = client.post(f"{BASE}/{rfq['id']}/decisions", json={
        "rfq_line_id": rfq_line["id"], "quote_line_id": cells[cheap]["quote_line_id"],
        "not_cheapest_reason": "Срок", "gate_override_reason": "Два КП достаточно",
    })
    assert decided.status_code == 201, decided.text
    assert cheap_quote

    with sessions() as session:
        _seed_request(session, line)
        _audit(session, line.id, "procurement.supplier_request_sent", datetime(2026, 9, 17, 9, 0), {"order_number": "ЗК-100"})
        _audit(session, line.id, "procurement.expected_arrival_set", datetime(2026, 9, 17, 9, 5),
               {"expected_arrival": "2026-09-30", "previous_expected_arrival": "2026-09-25"})
        _audit(session, line.id, "procurement.paid", datetime(2026, 9, 17, 14, 0),
               {"sum": "329.40", "source_currency": "CNY", "exchange_rate": "12.5", "exchange_rate_date": "2026-09-17"})
        row = session.get(ProcurementLineRow, line.id)
        row.invoice_number, row.invoice_date = "СЧ-7", date(2026, 9, 16)
        session.commit()
    _seed_tail(sessions, line)

    response = client.get(f"/api/procurement/{line.id}/history")

    assert response.status_code == 200, response.text
    events = response.json()["events"]
    titles = [event["title"] for event in events]
    assert titles[0] == "Заявка SM-20260915-01 создана" and events[0]["who"] == "Иванов"
    expected_order = [
        "Заявка SM-20260915-01 создана", "Передана в закупку", "Взята в работу", "Включена в запрос КП КП-2609-001",
        "Запрос КП отправлен: ООО Ключевой", "КП получено: ООО Ключевой", "КП получено: ООО Дёшево",
        "Выбран поставщик: ООО Дёшево", "Счёт внесён",
    ]
    positions = [titles.index(item) for item in expected_order if item in titles]
    assert positions == sorted(positions), titles
    by_title = {event["title"]: event for event in events}
    # Dates come only from stored timestamps; every event of this flow has one.
    assert all(event["at"] for event in events if event["title"] != "Ожидаемая дата поставки")
    chosen = by_title["Выбран поставщик: ООО Дёшево"]
    assert "не самое дешёвое: Срок" in chosen["details"] and "329,40 ₽" in chosen["details"] and "109,80 ₽ за ед. с НДС" in chosen["details"]
    assert by_title["Запрос поставщику отправлен"]["details"] == "заказ № ЗК-100"
    assert by_title["Ожидаемая дата поставки"]["details"] == "25.09.2026 → 30.09.2026"
    assert "курс 12.5" in by_title["Оплачено"]["details"] and by_title["Оплачено"]["who"] == "Петров"
    assert by_title["Приёмка: Годен"]["who"] == "Кладовщик" and "ГРН ГРН-0001" in by_title["Приёмка: Годен"]["details"]
    assert by_title["Выдача: 1 шт"]["details"].startswith("ГРН ГРН-0001; Комплектация")
    # Ordered by time: paid (17.09) before the acceptance (18.09) before the issue (19.09).
    assert titles.index("Оплачено") < titles.index("Приёмка: Годен") < titles.index("Выдача: 1 шт")


def test_unstored_dates_are_said_not_invented(rfq_api):  # noqa: F811
    client, lines, sessions = rfq_api
    with sessions() as session:
        row = session.get(ProcurementLineRow, lines[1].id)
        row.expected_arrival = date(2026, 10, 3)
        session.commit()

    events = client.get(f"/api/procurement/{lines[1].id}/history").json()["events"]

    undated = [event for event in events if event["at"] is None]
    assert [event["title"] for event in undated] == ["Ожидаемая дата поставки"]
    assert "дата не хранится" in undated[0]["details"] and "03.10.2026" in undated[0]["details"]
    assert events[-1] is undated[0]  # undated facts go last


def test_unknown_line_is_404_and_a_known_one_answers(rfq_api):  # noqa: F811
    client, lines, _ = rfq_api
    assert client.get(f"/api/procurement/{UUID(int=5)}/history").status_code == 404
    assert client.get(f"/api/procurement/{lines[0].id}/history").status_code == 200
