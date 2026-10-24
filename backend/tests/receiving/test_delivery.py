"""«Провести поставку»: one УПД, many positions, one transaction (owner, 05.10.2026)."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.modules.receiving.application import (
    AcceptDeliveryCommand,
    ConcurrentReceivingUpdate,
    DeliveryLineInput,
    accept_delivery,
    consume_receiving_expected,
)
from app.modules.receiving.domain import ReceivingStatus, ReceivingValidationError
from app.modules.warehouse.application import dispatch_next_warehouse_received
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.receiving.test_supplier_return import (  # noqa: F401 — fixtures
    NOW,
    RECEIVER,
    api,
    erp,
    expected_message,
)


def message(suffix: int, **payload):
    base = expected_message(suffix)
    return replace(
        base,
        created_at=base.created_at + timedelta(minutes=suffix),
        payload={**base.payload, "order_number": f"З-{suffix}", **payload},
    )


def expect(receiving, *suffixes, **payload):
    return [consume_receiving_expected(message(suffix, **payload), receiving()) for suffix in suffixes]


def item(line, **values):
    return DeliveryLineInput(line_id=line.id, expected_version=line.version, **values)


def deliver(receiving, items, **values):
    return accept_delivery(
        AcceptDeliveryCommand(
            lines=tuple(items),
            document_number=values.pop("document_number", "УПД-77"),
            document_date=values.pop("document_date", NOW.date()),
            actor=RECEIVER,
            now=NOW,
            **values,
        ),
        receiving(),
    )


def outbox(receiving, topic):
    with receiving() as uow:
        rows = uow.session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == topic))
        return [SimpleNamespace(idempotency_key=row.idempotency_key, payload=dict(row.payload)) for row in rows]


def audit_count(receiving, event_type):
    with receiving() as uow:
        return uow.session.scalar(
            select(func.count()).select_from(AuditEventRow).where(AuditEventRow.event_type == event_type)
        )


def lines_now(receiving, lines):
    with receiving() as uow:
        return [uow.receiving.get(line.id) for line in lines]


def test_ten_lines_are_accepted_placed_and_handed_over_once_each(erp):
    receiving, warehouse, _ = erp
    lines = expect(receiving, *range(1, 11))

    result = deliver(
        receiving,
        [item(line, storage_location="1F4") for line in lines],
        shipping_document_url="https://files.test/upd-77.pdf",
    )

    assert [line.status for line in result.lines] == [ReceivingStatus.ACCEPTED] * 10
    assert all(line.warehouse_event_emitted for line in result.lines)
    assert len({line.grn for line in result.lines}) == 10
    assert len({line.vk_act_number for line in result.lines}) == 10
    assert {(line.document_number, line.document_date) for line in result.lines} == {("УПД-77", NOW.date())}
    assert {line.shipping_document_url for line in result.lines} == {"https://files.test/upd-77.pdf"}
    handed = outbox(receiving, "warehouse.received")
    assert sorted(message.idempotency_key for message in handed) == sorted(
        f"receiving:{line.id}:warehouse" for line in lines
    )
    assert audit_count(receiving, "receiving.delivery_accepted") == 1
    with receiving() as uow:
        details = uow.session.scalar(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "receiving.delivery_accepted")
        )
    assert details["counts"] == {"lines": 10, "statuses": {"Принято": 10}, "placed": 10, "sent_to_warehouse": 10}
    assert details["document_number"] == "УПД-77" and details["supplier"] == "ООО Поставка"

    lots = [dispatch_next_warehouse_received(NOW, warehouse()) for _ in range(10)]
    assert all(lot is not None and lot.balance == Decimal("10") for lot in lots)
    assert dispatch_next_warehouse_received(NOW, warehouse()) is None

    # The same request again: stale versions → refused, nothing moves twice.
    with pytest.raises(ConcurrentReceivingUpdate):
        deliver(receiving, [item(line, storage_location="1F4") for line in lines])
    assert len(outbox(receiving, "warehouse.received")) == 10
    assert audit_count(receiving, "receiving.delivery_accepted") == 1
    assert lines_now(receiving, lines) == list(result.lines)


def test_only_upd_requisites_accept_every_line_at_its_ordered_quantity(erp):
    receiving, _, _ = erp
    # Two orders of one supplier on one УПД.
    first, second = expect(receiving, 1), expect(receiving, 2, ordered_quantity="4.5")
    lines = [*first, *second]

    result = deliver(receiving, [item(line) for line in lines])

    assert [(line.status, line.accepted_quantity) for line in result.lines] == [
        (ReceivingStatus.ACCEPTED, Decimal("10")),
        (ReceivingStatus.ACCEPTED, Decimal("4.5")),
    ]
    assert [line.nonconformities for line in result.lines] == [(), ()]
    # No place yet: accepted, waiting for the shelf, not on stock.
    assert not any(line.warehouse_event_emitted for line in result.lines)
    assert outbox(receiving, "warehouse.received") == []


def test_shortfall_goes_to_stock_with_backorder_complaint_and_alert(erp):
    receiving, _, _ = erp
    short_order, short_fact = expect(receiving, 1, 2)

    result = deliver(
        receiving,
        [
            item(short_order, document_quantity=Decimal("6"), storage_location="1F4"),
            item(short_fact, fact_quantity=Decimal("8"), storage_location="1F4"),
        ],
    )

    order_line, fact_line = result.lines
    assert (order_line.status, order_line.accepted_quantity, order_line.backorder_quantity) == (
        ReceivingStatus.ACCEPTED, Decimal("6"), Decimal("4"),
    )
    assert (fact_line.status, fact_line.accepted_quantity, fact_line.backorder_quantity) == (
        ReceivingStatus.ACCEPTED, Decimal("8"), Decimal("0"),
    )
    assert order_line.warehouse_event_emitted and fact_line.warehouse_event_emitted
    assert fact_line.complaint_act_number and fact_line.nc_act_number
    (backorder,) = outbox(receiving, "procurement.backorder_requested")
    assert Decimal(backorder.payload["backorder_quantity"]) == 4
    assert backorder.payload["receiving_line_id"] == str(short_order.id)
    notes = outbox(receiving, "notification.requested")
    assert sorted(note.payload["reference"] for note in notes) == ["SM-20260921-01", "SM-20260921-02"]
    assert all("статус «Принято»" in note.payload["body"] for note in notes)


def test_backordered_remainder_cannot_be_accepted_again_on_the_line(api):
    client, receiving, _ = api
    (line,) = expect(receiving, 1)
    (accepted,) = deliver(receiving, [item(line, document_quantity=Decimal("6"))]).lines

    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": accepted.version,
            "document_quantity": "10",
            "shipping_document_url": "https://files.test/upd.pdf",
        },
    )

    assert response.status_code == 422
    assert "SM-20260921-01-Д" in response.json()["detail"]
    assert "дозавозом" in response.json()["detail"]
    # An accepted line is not part of a second delivery either.
    again = client.post(
        "/api/receiving/deliveries",
        json={
            "document_number": "УПД-78",
            "document_date": "2026-09-21",
            "lines": [{"id": str(line.id), "version": accepted.version, "document_quantity": "4"}],
        },
    )
    assert again.status_code == 422
    assert "«Принято»" in again.json()["detail"]


def test_delivery_without_upd_is_accepted_with_a_note_and_no_nonconformity_act(erp):
    from app.modules.receiving.application import build_act_print_sheet
    from app.modules.receiving.domain import SHIPPING_DOCUMENT_PENDING_NOTE

    receiving, _, _ = erp
    lines = expect(receiving, 1, 2)

    result = accept_delivery(
        AcceptDeliveryCommand(
            lines=tuple(item(line, storage_location="1F4") for line in lines),
            document_number="",
            document_date=None,
            actor=RECEIVER,
            now=NOW,
        ),
        receiving(),
    )

    for line in result.lines:
        assert line.status is ReceivingStatus.ACCEPTED
        assert line.grn and line.warehouse_event_emitted and line.vk_act_number
        # One representation with a single-line ВК without УПД (hotfix #213): a computed note, no nonconformity.
        assert line.nonconformities == ()
        assert line.shipping_document_pending is True
        assert not line.nc_act_number
        assert not line.document_number and line.document_date is None
    assert SHIPPING_DOCUMENT_PENDING_NOTE in str(build_act_print_sheet(result.lines[0], "incoming").to_dict())


def test_delivery_without_upd_matches_a_single_vk_without_upd(erp):
    """One way to say «УПД позже»: the delivery path and a single-line ВК give the same line state, note and act."""
    from app.modules.receiving.api import _result
    from app.modules.receiving.application import (
        InspectReceivingCommand,
        _incoming_results,
        inspect_receiving,
    )
    from app.modules.receiving.domain import InspectionInput

    receiving, _, _ = erp
    by_delivery, by_vk = expect(receiving, 1, 2)

    (delivered,) = deliver(receiving, [item(by_delivery)], document_number="", document_date=None).lines
    inspected = inspect_receiving(
        InspectReceivingCommand(
            line_id=by_vk.id,
            expected_version=by_vk.version,
            inspection=InspectionInput(
                fact_quantity=by_vk.ordered_quantity,
                document_quantity=by_vk.ordered_quantity,
                ordered_quantity=by_vk.ordered_quantity,
                has_shipping_document=False,
                quality_document_required=by_vk.quality_document_required,
                quality_document_present=True,
                expiry_date=None,
                inspection_date=NOW.date(),
            ),
            actor=RECEIVER,
            now=NOW,
        ),
        receiving(),
    )

    def state(line):
        shown = _result(line)
        return (
            line.status,
            line.verdict,
            line.nonconformities,
            line.shipping_document_pending,
            bool(line.nc_act_number),
            shown["shipping_document_note"],
            shown["nonconformities"],
            _incoming_results(line),
        )

    assert state(delivered) == state(inspected)
    assert state(delivered)[0] is ReceivingStatus.ACCEPTED
    assert state(delivered)[5] == "УПД не приложен — придёт по ЭДО"


def test_delivery_without_upd_over_http(api):
    client, receiving, _ = api
    (line,) = expect(receiving, 1)

    response = client.post(
        "/api/receiving/deliveries",
        json={"lines": [{"id": str(line.id), "version": line.version}]},
    )

    assert response.status_code == 200, response.text
    (accepted,) = response.json()["lines"]
    assert accepted["status"] == "Принято" and accepted["grn"]
    assert accepted["nonconformities"] == []
    assert accepted["shipping_document_note"] == "УПД не приложен — придёт по ЭДО"


def test_one_bad_line_rolls_back_the_whole_delivery(erp):
    receiving, _, _ = erp
    lines = expect(receiving, 1, 2, 3)

    with pytest.raises(ReceivingValidationError, match="SM-20260921-03"):
        deliver(
            receiving,
            [
                item(lines[0], storage_location="1F4"),
                item(lines[1], storage_location="1F4"),
                item(lines[2], storage_location="33A1"),  # no such shelf — found after two lines went through
            ],
        )

    assert lines_now(receiving, lines) == lines
    assert outbox(receiving, "warehouse.received") == []
    assert audit_count(receiving, "receiving.inspected") == 0
    with receiving() as uow:
        assert uow.receiving.all_grns() == []


@pytest.mark.parametrize(
    ("payload", "values", "detail"),
    [
        ({"supplier": "ООО Другой"}, {}, "разных поставщиков"),
        ({"warehouse": "ЖУК"}, {}, "разных складов"),
        ({}, {"warehouse": "ЖУК"}, "склад «ЦАР», а поставка — на «ЖУК»"),
    ],
)
def test_delivery_is_one_supplier_and_one_warehouse(erp, payload, values, detail):
    receiving, _, _ = erp
    first = expect(receiving, 1)[0]
    other = expect(receiving, 2, **payload)[0]

    with pytest.raises(ReceivingValidationError, match=detail):
        deliver(receiving, [item(first), item(other)], **values)
    assert lines_now(receiving, [first, other]) == [first, other]


def test_deviations_keep_their_rules_and_a_missing_certificate_is_only_a_warning(erp):
    receiving, _, _ = erp
    defect, wrong, certificate, clean = expect(receiving, 1, 2, 3, 4, quality_document_required=True)

    result = deliver(
        receiving,
        [
            item(defect, deviation="brak", deviation_quantity=Decimal("3"), storage_location="1F4"),
            item(wrong, deviation="peresort", storage_location="1F4"),
            item(certificate, deviation="no_quality_doc", storage_location="1F4"),
            item(clean, storage_location="1F4"),
        ],
    )

    by_id = {line.id: line for line in result.lines}
    assert by_id[defect.id].status is ReceivingStatus.WAITING
    assert by_id[defect.id].storage_location is None and not by_id[defect.id].warehouse_event_emitted
    assert by_id[defect.id].nonconformity_description == "Механическое повреждение / брак: 3 шт"
    assert by_id[wrong.id].status is ReceivingStatus.RETURN
    assert by_id[certificate.id].status is ReceivingStatus.ACCEPTED
    assert by_id[certificate.id].warehouse_event_emitted
    assert by_id[certificate.id].nonconformities == ("Отсутствие / несоответствие документа по качеству",)
    assert by_id[clean.id].status is ReceivingStatus.ACCEPTED and by_id[clean.id].nonconformities == ()
    # The certificate gap alerts nobody; брак and пересорт do.
    references = sorted(note.payload["reference"] for note in outbox(receiving, "notification.requested"))
    assert references == [defect.legacy_id, wrong.legacy_id]


def test_expired_deviation_needs_an_expired_date(erp):
    receiving, _, _ = erp
    (line,) = expect(receiving, 1)

    with pytest.raises(ReceivingValidationError, match="истёкший срок"):
        deliver(receiving, [item(line, deviation="expired")])
    (returned,) = deliver(
        receiving, [item(line, deviation="expired", expiry_date=NOW.date() - timedelta(days=1))]
    ).lines
    assert returned.status is ReceivingStatus.RETURN


def test_waiting_line_is_released_by_a_delivery(erp):
    receiving, _, _ = erp
    (line,) = expect(receiving, 1)
    (waiting,) = deliver(receiving, [item(line, deviation="brak", deviation_quantity=Decimal("2"))]).lines
    assert waiting.status is ReceivingStatus.WAITING

    (released,) = deliver(receiving, [item(waiting, storage_location="1F4")], document_number="УПД-77").lines

    assert released.status is ReceivingStatus.ACCEPTED
    assert released.use_permit_number and released.warehouse_event_emitted
    assert released.grn == waiting.grn and released.vk_act_number == waiting.vk_act_number


def test_api_delivery_returns_the_lines(api):
    client, receiving, _ = api
    (line,) = expect(receiving, 1)

    # The УПД is optional (it comes by ЭДО later); a delivery without lines is not.
    assert client.post("/api/receiving/deliveries", json={"lines": []}).status_code == 422

    response = client.post(
        "/api/receiving/deliveries",
        json={
            "document_number": "УПД-77",
            "document_date": "2026-09-20",
            "warehouse": "ЦАР",
            "lines": [{"id": str(line.id), "version": line.version, "storage_location": "1f4", "series": "L-1"}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivery_id"]
    (row,) = body["lines"]
    assert (row["status"], row["accepted_quantity"], row["storage_location"], row["series"]) == (
        "Принято", "10", "1F4", "L-1",
    )
    assert row["warehouse_event_emitted"] is True
    assert row["document_date"] == "2026-09-20"


def test_queue_puts_open_lines_first_and_pages_without_gaps(api):
    client, receiving, _ = api
    lines = expect(receiving, *range(1, 121))
    # The first 100 go to stock, the next 19 are accepted without a place; only the last created is open.
    deliver(receiving, [item(line, storage_location="1F4") for line in lines[:100]])
    deliver(receiving, [item(line) for line in lines[100:119]])

    first = client.get("/api/receiving").json()
    assert first["lines"][0]["legacy_id"] == lines[-1].legacy_id
    assert first["lines"][0]["status"] == "В пути"
    assert [row["legacy_id"] for row in first["lines"][1:20]] == [line.legacy_id for line in lines[100:119]]

    seen: list[str] = []
    cursor = None
    while True:
        page = client.get("/api/receiving", params={"limit": 50, **({"after": cursor} if cursor else {})}).json()
        seen.extend(row["legacy_id"] for row in page["lines"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    expected_order = [lines[-1], *lines[100:119], *lines[:100]]
    assert seen == [line.legacy_id for line in expected_order]

    assert [row["legacy_id"] for row in client.get("/api/receiving?preset=open").json()["lines"]] == [
        lines[-1].legacy_id
    ]
    assert len(client.get("/api/receiving?preset=to_stock").json()["lines"]) == 19
    assert client.get("/api/receiving?status=Нет такого").status_code == 422
