from datetime import datetime
from decimal import Decimal

import pytest

from app.modules.fulfillment.application import (
    BindLotCommand,
    CompleteReturnCommand,
    CreateFulfillmentCommand,
    MarkReadyCommand,
    MarkReturnedCommand,
    ReturnDisposition,
    VersionedFulfillmentCommand,
    bind_lot,
    cancel_fulfillment,
    complete_return,
    create_fulfillment,
    issue_fulfillment,
    mark_ready,
    mark_returned,
)
from app.modules.fulfillment.domain import (
    FulfillmentLine,
    FulfillmentStatus,
    FulfillmentValidationError,
)
from app.modules.warehouse.domain import StockLot
from tests.factories import Actor


class Fulfillments:
    def __init__(self):
        self.items = {}

    def get(self, request_id):
        return self.items.get(request_id)

    def by_idempotency_key(self, key):
        return next(
            (item for item in self.items.values() if item.source_idempotency_key == key),
            None,
        )

    def numbers(self, year):
        prefix = f"REQ.{year % 100:02d}."
        return [item.number for item in self.items.values() if item.number.startswith(prefix)]

    def list(self, limit=200, after_id=None):
        items = sorted(self.items.values(), key=lambda item: item.id)
        if after_id is not None:
            items = [item for item in items if item.id > after_id]
        return items[:limit]

    def add(self, request):
        self.items[request.id] = request


class Lots:
    def __init__(self, *lots):
        self.items = {lot.id: lot for lot in lots}

    def get(self, lot_id):
        return self.items.get(lot_id)

    def add(self, lot):
        self.items[lot.id] = lot


class Audit:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class Uow:
    def __init__(self, *lots):
        self.fulfillment = Fulfillments()
        self.warehouse = Lots(*lots)
        self.writeoffs = Audit()
        self.audit = Audit()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1


def stock(quantity: str, suffix: str) -> StockLot:
    return StockLot.create(
        warehouse="ЦАР",
        grn=f"FRX.26.{suffix}",
        package="",
        name="Разъём",
        item_type="Комплектующие",
        purpose="RND",
        unit="шт",
        quantity=Decimal(quantity),
        storage_location="1F4",
        source_receiving_ids=(f"receiving-{suffix}",),
    )


def test_human_lot_binding_and_whole_kit_issue_are_one_stock_transaction():
    first_lot = stock("6", "0000001")
    second_lot = stock("4", "0000002")
    uow = Uow(first_lot, second_lot)
    actor = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 9, 22, 8, 0)
    created = create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key="kitset:591K.3:KB-1:v1",
            purpose="591К.3 / КБ-1",
            deadline=None,
            lines=(
                FulfillmentLine.create(
                    article="КД-001",
                    name="Разъём",
                    unit="шт",
                    requirement=Decimal("10"),
                ),
            ),
            actor=actor,
            now=now,
        ),
        uow,
    )
    retry = create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key="kitset:591K.3:KB-1:v1",
            purpose="591К.3 / КБ-1",
            deadline=None,
            lines=(FulfillmentLine.create(article="КД-001", name="Разъём", unit="шт", requirement=Decimal("10")),),
            actor=actor,
            now=now,
        ),
        uow,
    )
    partial = bind_lot(
        BindLotCommand(
            request_id=created.id,
            expected_version=created.version,
            line_id=created.lines[0].id,
            lot_id=first_lot.id,
            actor=actor,
            now=now,
        ),
        uow,
    )
    complete = bind_lot(
        BindLotCommand(
            request_id=partial.id,
            expected_version=partial.version,
            line_id=partial.lines[1].id,
            lot_id=second_lot.id,
            actor=actor,
            now=now,
        ),
        uow,
    )
    ready = mark_ready(
        MarkReadyCommand(
            request_id=complete.id,
            expected_version=complete.version,
            handout_location="Полка выдачи 2",
            actor=actor,
            now=now,
        ),
        uow,
    )
    issued = issue_fulfillment(
        VersionedFulfillmentCommand(
            request_id=ready.id,
            expected_version=ready.version,
            actor=actor,
            now=now,
        ),
        uow,
    )
    returned = mark_returned(
        MarkReturnedCommand(
            request_id=issued.id,
            expected_version=issued.version,
            actor=actor,
            now=now,
            return_location="Стеллаж возврата",
        ),
        uow,
    )
    completed = complete_return(
        CompleteReturnCommand(
            request_id=returned.id,
            expected_version=returned.version,
            actor=actor,
            now=now,
            lines=(
                ReturnDisposition(
                    line_id=returned.lines[0].id,
                    returned_good=Decimal("2"),
                    defective_returned=Decimal("1"),
                    defective_missing=Decimal("0.5"),
                    storage_location="2A1",
                ),
                ReturnDisposition(
                    line_id=returned.lines[1].id,
                    returned_good=Decimal("1"),
                    defective_returned=Decimal("0"),
                    defective_missing=Decimal("0"),
                ),
            ),
        ),
        uow,
    )

    assert retry == created
    assert partial.status is FulfillmentStatus.UNDERFILLED
    assert complete.status is FulfillmentStatus.COMPLETE
    assert issued.status is FulfillmentStatus.ISSUED
    assert completed.status is FulfillmentStatus.DONE
    assert uow.warehouse.get(first_lot.id).balance == Decimal("2")
    assert uow.warehouse.get(first_lot.id).storage_location == "2A1"
    assert uow.warehouse.get(second_lot.id).balance == Decimal("1")
    assert uow.warehouse.get(first_lot.id).reserved == Decimal("0")
    assert [event.event_type for event in uow.audit.items] == [
        "fulfillment.created",
        "fulfillment.lot_bound",
        "fulfillment.lot_bound",
        "fulfillment.ready",
        "fulfillment.issued",
        "fulfillment.returned",
        "fulfillment.return_completed",
    ]


def test_cancel_retry_is_read_only():
    uow = Uow()
    actor = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 9, 22, 8, 0)
    created = create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key="kitset:retry",
            purpose="КБ-2",
            deadline=None,
            lines=(FulfillmentLine.create(article="", name="Кабель", unit="м", requirement=Decimal("2")),),
            actor=actor,
            now=now,
        ),
        uow,
    )
    with pytest.raises(FulfillmentValidationError, match="другой заявки"):
        create_fulfillment(
            CreateFulfillmentCommand(
                idempotency_key="  kitset:retry  ",
                purpose="Другая КБ",
                deadline=None,
                lines=(FulfillmentLine.create(article="", name="Кабель", unit="м", requirement=Decimal("2")),),
                actor=actor,
                now=now,
            ),
            uow,
        )
    cancelled = cancel_fulfillment(
        VersionedFulfillmentCommand(created.id, created.version, actor, now),
        uow,
    )
    retry = cancel_fulfillment(
        VersionedFulfillmentCommand(cancelled.id, cancelled.version, actor, now),
        uow,
    )

    assert retry == cancelled
    assert [event.event_type for event in uow.audit.items].count("fulfillment.cancelled") == 1


def test_lot_unit_must_match_requirement_unit():
    warehouse_lot = stock("5", "0000003")
    uow = Uow(warehouse_lot)
    actor = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 9, 22, 8, 0)
    created = create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key="kitset:unit-mismatch",
            purpose="КБ-3",
            deadline=None,
            lines=(FulfillmentLine.create(article="", name="Смола", unit="кг", requirement=Decimal("5")),),
            actor=actor,
            now=now,
        ),
        uow,
    )

    with pytest.raises(FulfillmentValidationError, match="Единица партии"):
        bind_lot(
            BindLotCommand(
                request_id=created.id,
                expected_version=created.version,
                line_id=created.lines[0].id,
                lot_id=warehouse_lot.id,
                actor=actor,
                now=now,
            ),
            uow,
        )

    assert uow.warehouse.get(warehouse_lot.id).reserved == Decimal("0")
