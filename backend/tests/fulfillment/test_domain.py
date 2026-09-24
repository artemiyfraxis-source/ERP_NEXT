from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.fulfillment.domain import (
    FulfillmentLine,
    FulfillmentRequest,
    FulfillmentStatus,
    FulfillmentValidationError,
    LotSnapshot,
)


def line(requirement: str = "10") -> FulfillmentLine:
    return FulfillmentLine.create(
        article="КД-001",
        name="Разъём",
        unit="шт",
        requirement=Decimal(requirement),
    )


def lot(suffix: int = 1) -> LotSnapshot:
    return LotSnapshot(
        lot_id=UUID(f"40000000-0000-0000-0000-{suffix:012d}"),
        warehouse="ЦАР",
        grn=f"FRX.26.{suffix:07d}",
        package="",
        storage_location="1F4",
    )


def request(*lines: FulfillmentLine) -> FulfillmentRequest:
    return FulfillmentRequest.create(
        number="REQ-26-000001",
        source_idempotency_key="kitset:591K.3:KB-1:v1",
        source_fingerprint="fingerprint",
        requester="Технолог",
        purpose="591К.3 / КБ-1",
        deadline=None,
        lines=tuple(lines),
        created_at=datetime(2026, 9, 22, 8, 0),
    )


def test_request_exists_without_stock_but_empty_bom_is_skipped():
    created = request(line())

    assert created.status is FulfillmentStatus.NEW
    assert created.lines[0].lot is None
    assert created.lines[0].reserved == 0
    with pytest.raises(FulfillmentValidationError, match="без состава"):
        request()


def test_new_request_rejects_forged_reservations_and_duplicate_ids():
    fresh = line()
    forged = replace(fresh, reserved=Decimal("1"), lot=lot())
    duplicate = replace(fresh, name="Другая строка")

    with pytest.raises(FulfillmentValidationError, match="незарезервированные"):
        request(forged)
    with pytest.raises(FulfillmentValidationError, match="уникальны"):
        request(fresh, duplicate)


def test_human_binding_partially_reserves_and_splits_uncovered_need():
    original = request(line("10"))
    bound = original.bind_line(original.lines[0].id, lot(), Decimal("6"))

    assert bound.status is FulfillmentStatus.UNDERFILLED
    assert len(bound.lines) == 2
    assert bound.lines[0].reserved == Decimal("6")
    assert bound.lines[0].requirement == Decimal("6")
    assert bound.lines[1].requirement == Decimal("4")
    assert bound.lines[1].lot is None
    assert bound.lines[0].requirement_id == bound.lines[1].requirement_id


def test_unbinding_merges_rows_created_by_partial_lot():
    original = request(line("10"))
    partial = original.bind_line(original.lines[0].id, lot(), Decimal("6"))
    merged = partial.unbind_line(partial.lines[0].id)

    assert len(merged.lines) == 1
    assert merged.lines[0].requirement == Decimal("10")
    assert merged.lines[0].reserved == 0


def test_complete_kit_requires_handout_place_and_is_issued_whole():
    original = request(line("3"), line("2"))
    first = original.bind_line(original.lines[0].id, lot(1), Decimal("3"))
    complete = first.bind_line(first.lines[1].id, lot(2), Decimal("2"))

    assert complete.status is FulfillmentStatus.COMPLETE
    with pytest.raises(FulfillmentValidationError, match="место выдачи"):
        complete.mark_ready("")
    ready = complete.mark_ready("Полка выдачи 2")
    issued = ready.mark_issued()

    assert issued.status is FulfillmentStatus.ISSUED
    assert all(item.reserved == 0 for item in issued.lines)
    assert [item.issued for item in issued.lines] == [Decimal("3"), Decimal("2")]


def test_incomplete_or_issued_request_cannot_bypass_gates():
    original = request(line("3"))
    with pytest.raises(FulfillmentValidationError, match="не укомплектован"):
        original.mark_ready("Полка 1")
    complete = original.bind_line(original.lines[0].id, lot(), Decimal("3"))
    issued = complete.mark_ready("Полка 1").mark_issued()
    with pytest.raises(FulfillmentValidationError, match="возврат"):
        issued.cancel()


def test_return_records_good_defects_and_consumed_without_exceeding_issued():
    original = request(line("5"))
    issued = original.bind_line(original.lines[0].id, lot(), Decimal("5")).mark_ready("Полка 1").mark_issued()
    with pytest.raises(FulfillmentValidationError, match="место возврата"):
        issued.mark_returned("")
    returned = issued.mark_returned("Стеллаж возврата")
    completed = returned.complete_return({
        returned.lines[0].id: (Decimal("2"), Decimal("1"), Decimal("0.5")),
    })

    assert completed.status is FulfillmentStatus.DONE
    assert completed.return_location == "Стеллаж возврата"
    assert completed.lines[0].returned_good == Decimal("2")
    assert completed.lines[0].defective_returned == Decimal("1")
    assert completed.lines[0].consumed == Decimal("1.5")
    with pytest.raises(FulfillmentValidationError, match="превышать"):
        returned.complete_return({
            returned.lines[0].id: (Decimal("4"), Decimal("2"), Decimal("0")),
        })
