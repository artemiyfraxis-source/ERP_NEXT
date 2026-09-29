from dataclasses import replace
from datetime import date, datetime
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
    build_issue_print_sheet,
    build_issue_return_sheet,
    cancel_fulfillment,
    complete_kit,
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


class Outbox(Audit):
    def by_idempotency_key(self, key):
        return next((item for item in self.items if item.idempotency_key == key), None)


class Uow:
    def __init__(self, *lots):
        self.fulfillment = Fulfillments()
        self.warehouse = Lots(*lots)
        self.writeoffs = Audit()
        self.audit = Audit()
        self.outbox = Outbox()
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
            deadline=date(2026, 10, 1),
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
            deadline=date(2026, 10, 1),
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
    bound_all = bind_lot(
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
    assert bound_all.status is FulfillmentStatus.UNDERFILLED
    complete = complete_kit(VersionedFulfillmentCommand(bound_all.id, bound_all.version, actor, now), uow)
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
    # Issue.gs:459-474: «готов к выдаче» reaches the requester (by user id) with the pickup place.
    (ready_note,) = uow.outbox.items
    assert ready_note.topic == "notification.requested"
    assert ready_note.payload["recipient_user_ids"] == str(actor.id) == str(ready.requester_id)
    assert ready_note.payload["recipient_roles"] == ""
    assert ready_note.payload["title"] == f"Заявка {ready.number} готова к получению"
    assert "место: Полка выдачи 2" in ready_note.payload["body"]
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
        "fulfillment.completed",
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
            deadline=date(2026, 10, 1),
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
                deadline=date(2026, 10, 1),
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


def test_a_lot_in_other_units_binds_and_gives_the_line_its_unit():
    warehouse_lot = stock("5", "0000003")
    uow = Uow(warehouse_lot)
    actor = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 9, 22, 8, 0)
    created = create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key="kitset:unit-mismatch",
            purpose="КБ-3",
            deadline=date(2026, 10, 1),
            lines=(FulfillmentLine.create(article="", name="Смола", unit="кг", requirement=Decimal("5")),),
            actor=actor,
            now=now,
        ),
        uow,
    )

    bound = bind_lot(
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

    # issueAttachLot_: no unit check; the bound row takes the lot's ЕД.ИЗМ.
    assert uow.warehouse.get(warehouse_lot.id).reserved == Decimal("5")
    assert bound.lines[0].unit == warehouse_lot.unit


def test_print_sheet_marks_shortage_and_return_sheet_lists_only_issued_lines():
    lot = stock("6", "0000004")  # покрывает требование 6 целиком — без остатка-«недобора» на этой строке
    uow = Uow(lot)
    actor = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 9, 29, 9, 0)
    created = create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key="kitset:print-sheet",
            purpose="316К.14 / КБ-1",
            deadline=date(2026, 10, 1),
            lines=(
                FulfillmentLine.create(article="КД-9", name="Разъём", unit="шт", requirement=Decimal("6")),
                FulfillmentLine.create(article="", name="Кабель без партии", unit="м", requirement=Decimal("3")),
            ),
            actor=actor,
            now=now,
        ),
        uow,
    )

    before_binding = build_issue_print_sheet(created, now)
    assert before_binding.title == f"ЗАЯВКА НА ВЫДАЧУ {created.number}"
    assert before_binding.columns[:3] == ("№", "Артикул КД", "Наименование")
    assert before_binding.rows[0] == ("1", "КД-9", "Разъём", "— ждём поставки —", "", "6", "0", "шт", "", "")
    assert "Разъём" in before_binding.note and "Кабель без партии" in before_binding.note

    bound = bind_lot(
        BindLotCommand(
            request_id=created.id,
            expected_version=created.version,
            line_id=created.lines[0].id,
            lot_id=lot.id,
            actor=actor,
            now=now,
        ),
        uow,
    )
    after_binding = build_issue_print_sheet(bound, now)
    bound_row = next(row for row in after_binding.rows if row[1] == "КД-9")
    assert bound_row[3] == lot.grn
    assert bound_row[6] == "6"  # «Набрать» = резерв, ещё не выдано
    assert "Разъём" not in after_binding.note
    assert "Кабель без партии" in after_binding.note

    with pytest.raises(FulfillmentValidationError, match="ещё нет выданных"):
        build_issue_return_sheet(bound, now)

    # Строку без партии полностью укомплектовать нельзя в этом сценарии — вместо полного цикла
    # «Готов к выдаче → выдать» ставим позицию с партией в «выдано» напрямую, как это уже делает
    # `issue_fulfillment` внутри одной транзакции. Лист возврата должен видеть только её.
    issued_line = bound.lines[0].mark_issued()
    manually_issued = replace(bound, lines=(issued_line, bound.lines[1]))
    return_sheet = build_issue_return_sheet(manually_issued, now)
    assert len(return_sheet.rows) == 1
    assert return_sheet.rows[0][1] == "Разъём"
    assert return_sheet.rows[0][4] == "6"
    assert return_sheet.title == f"ЛИСТ ВОЗВРАТА ПО ЗАЯВКЕ {manually_issued.number}"
