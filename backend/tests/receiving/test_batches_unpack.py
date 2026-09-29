"""RC-13 (партии изготовителя в одной поставке), RC-8 (разукомплектовка по пакетам), реквизиты УПД, RC-1."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.receiving.application import (
    InspectBatchesCommand,
    PlaceReceivingCommand,
    UnpackCommand,
    consume_receiving_expected,
    inspect_receiving_batches,
    place_receiving,
    unpack_receiving,
)
from app.modules.receiving.domain import ReceivingStatus, ReceivingValidationError
from app.modules.warehouse.application import dispatch_next_warehouse_received
from tests.receiving.test_supplier_return import (  # noqa: F401 — fixtures
    NOW,
    RECEIVER,
    accept,
    api,
    erp,
    expected_message,
    inspection,
)


def batch(quantity: str, series: str):
    return replace(inspection(quantity), document_quantity=Decimal(quantity), series=series)


def test_batches_split_the_line_and_each_gets_its_own_grn(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())

    parts = inspect_receiving_batches(
        InspectBatchesCommand(
            line_id=line.id, expected_version=line.version,
            batches=(batch("6", "A1"), batch("4", "B2")), actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )

    assert [part.legacy_id for part in parts] == ["SM-20260921-01", "SM-20260921-01-П2"]
    assert [part.ordered_quantity for part in parts] == [Decimal("6"), Decimal("4")]
    assert [part.series for part in parts] == ["A1", "B2"]
    assert all(part.status is ReceivingStatus.ACCEPTED for part in parts)
    assert parts[0].grn and parts[1].grn and parts[0].grn != parts[1].grn
    assert parts[1].split_of_id == line.id
    with receiving() as uow:
        assert uow.receiving.get(parts[1].id).procurement_line_id == line.procurement_line_id


def test_batches_below_the_order_send_the_remainder_to_backorder(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())

    inspect_receiving_batches(
        InspectBatchesCommand(
            line_id=line.id, expected_version=line.version,
            batches=(batch("3", "A1"), batch("4", "B2")), actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )

    with receiving() as uow:
        backorder = uow.outbox.next_unpublished("procurement.backorder_requested", NOW.replace(year=2030))
    assert backorder is not None
    assert Decimal(backorder.payload["backorder_quantity"]) == Decimal("3")


def test_single_batch_is_rejected(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    with pytest.raises(ReceivingValidationError):
        inspect_receiving_batches(
            InspectBatchesCommand(
                line_id=line.id, expected_version=line.version,
                batches=(batch("10", "A1"),), actor=RECEIVER, now=NOW,
            ),
            receiving(),
        )


def test_unpack_splits_an_accepted_line_into_packages_with_the_same_grn(erp):
    receiving, warehouse, _ = erp
    accepted = accept(receiving)

    packages = unpack_receiving(
        UnpackCommand(
            line_id=accepted.id, expected_version=accepted.version,
            quantities=(Decimal("7"), Decimal("3")), actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )

    assert [p.package for p in packages] == ["1", "2"]
    assert [p.fact_quantity for p in packages] == [Decimal("7"), Decimal("3")]
    assert {p.grn for p in packages} == {accepted.grn}
    assert packages[1].legacy_id == "SM-20260921-01-П2"
    for package in packages:
        with receiving() as uow:
            current = uow.receiving.get(package.id)
        place_receiving(
            PlaceReceivingCommand(
                line_id=current.id, expected_version=current.version, storage_location="1F4",
                actor=RECEIVER, now=NOW,
            ),
            receiving(),
        )
    lots = [dispatch_next_warehouse_received(NOW, warehouse()) for _ in packages]
    assert sorted((lot.package, lot.balance) for lot in lots) == [("1", Decimal("7")), ("2", Decimal("3"))]


def test_unpack_sum_must_match_the_fact(erp):
    receiving, _, _ = erp
    accepted = accept(receiving)
    with pytest.raises(ReceivingValidationError, match="Сумма частей"):
        unpack_receiving(
            UnpackCommand(
                line_id=accepted.id, expected_version=accepted.version,
                quantities=(Decimal("7"), Decimal("2")), actor=RECEIVER, now=NOW,
            ),
            receiving(),
        )


def test_api_stores_document_details_and_takes_one_quantity_for_both(api):
    client, receiving, _ = api
    line = consume_receiving_expected(expected_message(), receiving())

    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": line.version,
            "fact_quantity": "10",
            "shipping_document_url": "https://files.test/upd.pdf",
            "expiry_date": "2027-01-01",
            "document_number": "УПД-512",
            "document_date": "2026-09-20",
            "document_name": "Разъём CON-001",
            "note": "коробка мятая",
            "manufacture_date": "2026-08-01",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()["line"]
    assert body["document_quantity"] == "10"
    assert body["document_number"] == "УПД-512"
    assert body["document_date"] == "2026-09-20"
    assert body["document_name"] == "Разъём CON-001"
    assert body["inspection_note"] == "коробка мятая"
    assert body["manufacture_date"] == "2026-08-01"


def test_api_without_quantities_is_rejected(api):
    client, receiving, _ = api
    line = consume_receiving_expected(expected_message(), receiving())
    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={"version": line.version, "shipping_document_url": "https://files.test/upd.pdf"},
    )
    assert response.status_code == 422


def test_api_batches_and_unpack(api):
    client, receiving, _ = api
    line = consume_receiving_expected(expected_message(), receiving())

    split = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": line.version,
            "shipping_document_url": "https://files.test/upd.pdf",
            "batches": [
                {"document_quantity": "6", "series": "A1", "expiry_date": "2027-01-01"},
                {"document_quantity": "4", "series": "B2", "expiry_date": "2027-01-01"},
            ],
        },
    )
    assert split.status_code == 200, split.text
    lines = split.json()["lines"]
    assert [item["series"] for item in lines] == ["A1", "B2"]
    assert lines[1]["split_of_id"] == str(line.id)

    first = lines[0]
    unpacked = client.post(
        f"/api/receiving/{first['id']}/unpack",
        json={"version": first["version"], "quantities": ["5", "1"]},
    )
    assert unpacked.status_code == 200, unpacked.text
    assert [item["package"] for item in unpacked.json()["lines"]] == ["1", "2"]

    bad = client.post(
        f"/api/receiving/{first['id']}/unpack",
        json={"version": unpacked.json()["lines"][0]["version"], "quantities": ["5", "5"]},
    )
    assert bad.status_code == 422
