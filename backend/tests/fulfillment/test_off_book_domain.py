"""«есть, но не заведено» and the КБ key as pure rules."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.fulfillment.domain import (
    FulfillmentLine,
    FulfillmentRequest,
    FulfillmentValidationError,
    KitReserve,
    LotSnapshot,
    item_index_key,
    kb_number,
    kb_of,
)

NOW = datetime(2026, 10, 5, 12, 0)
LOT = LotSnapshot(lot_id=uuid4(), warehouse="ЦАР", grn="FRX.26.0000001", package="", storage_location="1F4")


def request(*needs: str) -> FulfillmentRequest:
    return FulfillmentRequest.create(
        number="REQ.26.0000001", source_idempotency_key="key", source_fingerprint="fp", requester="Мастер",
        purpose="728К.2 · КБ1 · Жгут", deadline=date(2026, 10, 20), created_at=NOW,
        lines=tuple(FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal(need))
                    for need in needs),
    )


def test_kb_is_read_from_the_purpose_in_every_spelling():
    assert kb_number("728К.2 · КБ10 · Жгут") == 10
    assert kb_number("35К.С.13 / КБ-1") == 1
    assert kb_number("Ручная заявка") is None
    assert kb_of("728К.2 · кб 3 · Жгут") == "КБ3"
    assert item_index_key("кб-1") == item_index_key("КБ1") == "КБ1"


def test_off_book_line_counts_as_covered_and_is_issued_whole_without_reserve():
    kit = request("4")
    marked = kit.mark_off_book(kit.lines[0].id)

    assert marked.fully_reserved is True
    assert marked.shortfall == ""
    assert marked.status.value == "Укомплектован"
    line = marked.lines[0].mark_issued()
    assert (line.issued, line.reserved, line.lot) == (Decimal("4"), Decimal("0"), None)


def test_off_book_line_takes_no_lot_and_a_bound_line_takes_no_mark():
    kit = request("4", "2")
    marked = kit.mark_off_book(kit.lines[0].id)
    with pytest.raises(FulfillmentValidationError, match="не заведено"):
        marked.bind_line(marked.lines[0].id, LOT, Decimal("4"))
    bound = marked.bind_line(marked.lines[1].id, LOT, Decimal("2"))
    with pytest.raises(FulfillmentValidationError, match="партия"):
        bound.mark_off_book(bound.lines[1].id)


def test_clearing_the_mark_folds_the_line_back_with_free_rows_but_never_folds_a_marked_line():
    kit = request("4", "3")
    both = kit.mark_off_book(kit.lines[0].id)
    assert len(both.lines) == 2  # a marked row does not fold into the free one

    cleared = both.clear_off_book(both.lines[0].id)
    assert [(line.requirement, line.off_book) for line in cleared.lines] == [(Decimal("7"), False)]
    assert cleared.status.value == "Недоукомплектован"


def test_off_book_return_refuses_good_back_but_takes_defects():
    kit = request("4")
    issued = kit.mark_off_book(kit.lines[0].id).lines[0].mark_issued()
    with pytest.raises(FulfillmentValidationError, match="не заведено"):
        issued.settle_return(Decimal("1"), Decimal("0"), Decimal("0"))
    settled = issued.settle_return(Decimal("0"), Decimal("1"), Decimal("0"))
    assert settled.consumed == Decimal("3")


def test_kit_reserve_consume_keeps_the_trace_and_refuses_more_than_held():
    reserve = KitReserve(
        id=uuid4(), project="728К.2", item_index="КБ1", article="КД-1", lot_id=uuid4(), warehouse="ЦАР",
        grn="FRX.26.0000001", package="", unit="шт", quantity=Decimal("5"), created_at=NOW, created_by="Технолог",
    )
    taken = reserve.consume(Decimal("2"), "REQ.26.0000001").consume(Decimal("1"), "REQ.26.0000002")

    assert (taken.quantity, taken.consumed, taken.consumed_by) == (
        Decimal("2"), Decimal("3"), "REQ.26.0000001, REQ.26.0000002",
    )
    assert taken.label == "728К.2 · КБ1"
    with pytest.raises(FulfillmentValidationError):
        taken.consume(Decimal("3"), "REQ.26.0000003")
    released = taken.release(NOW)
    assert (released.active, released.released_at) == (False, NOW)
