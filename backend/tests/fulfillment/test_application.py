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
    OffBookCommand,
    ReturnDisposition,
    UnbindLotCommand,
    VersionedFulfillmentCommand,
    bind_lot,
    handout_place,
    set_off_book,
    unbind_lot,
    build_issue_print_sheet,
    build_issue_return_sheet,
    cancel_fulfillment,
    complete_kit,
    complete_return,
    create_fulfillment,
    issue_fulfillment,
    kit_key,
    mark_ready,
    mark_returned,
    next_request_number,
    request_sequence,
    return_to_kitting,
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
        self.counters = {}

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

    def next_number(self, year):
        number = next_request_number(year, self.numbers(year), self.counters.get(year, 0))
        self.counters[year] = request_sequence(number)
        return number

    def live_kit_request(self, project_key, item_index):
        return next((
            item for item in self.items.values()
            if kit_key(item.purpose) == (project_key, item_index) and item.status is not FulfillmentStatus.CANCELLED
        ), None)

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
    assert bound_all.status is FulfillmentStatus.COMPLETE  # the last open line closed: «Укомплектован» by itself
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
    assert ready_note.payload["recipient_audiences"] == ""
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


class Sites:
    """The kit's shop site (G1-T3): which site a request goes to, its masters and the last handout place there."""

    def __init__(self, site, masters, last=None):
        self.site, self.master_ids, self.last = site, masters, last

    def site_of(self, request):
        return self.site

    def masters(self, site):
        return self.master_ids if site == self.site else []

    def last_handout(self, site):
        return self.last if site == self.site else None


def _two_line_kit(uow, actor, now):
    return create_fulfillment(CreateFulfillmentCommand(
        idempotency_key="kitset:728K.2:KB-1:v1", purpose="728К.2 · КБ1 · Жгут", deadline=date(2026, 10, 10),
        lines=(
            FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal("4")),
            FulfillmentLine.create(article="КД-2", name="Провод", unit="м", requirement=Decimal("2")),
        ),
        actor=actor, now=now,
    ), uow)


def _completed(uow, request, actor, now):
    for line in request.lines:
        request = set_off_book(OffBookCommand(request.id, request.version, actor, now, line.id), uow)
    return request


def test_picking_the_last_line_completes_the_kit_by_itself_and_journals_it_once():
    lot = stock("10", "0000001")
    uow = Uow(lot)
    actor = Actor(name="Кладовщик", department="Склад")
    now = datetime(2026, 10, 5, 9, 0)
    created = _two_line_kit(uow, actor, now)

    partial = bind_lot(BindLotCommand(created.id, created.version, actor, now, created.lines[0].id, lot.id), uow)
    assert partial.status is FulfillmentStatus.UNDERFILLED
    complete = set_off_book(OffBookCommand(partial.id, partial.version, actor, now, partial.lines[1].id), uow)
    assert complete.status is FulfillmentStatus.COMPLETE
    completed = [event for event in uow.audit.items if event.event_type == "fulfillment.completed"]
    assert [event.details for event in completed] == [{"auto": True}]

    # A late press of «Укомплектован» writes nothing; removing the lot rolls the kit back.
    assert complete_kit(VersionedFulfillmentCommand(complete.id, complete.version, actor, now), uow) == complete
    assert len([event for event in uow.audit.items if event.event_type == "fulfillment.completed"]) == 1
    rolled = unbind_lot(UnbindLotCommand(complete.id, complete.version, actor, now, complete.lines[0].id), uow)
    assert rolled.status is FulfillmentStatus.UNDERFILLED
    assert uow.warehouse.get(lot.id).reserved == 0


def test_ready_notifies_the_requester_and_the_masters_of_the_kit_site_once_each():
    uow = Uow()
    requester = Actor(name="Технолог", department="КБ")
    storekeeper = Actor(name="Кладовщик", department="Склад")
    master = Actor(name="Степан", department="Монтаж")
    second_master = Actor(name="Мастер 2", department="Монтаж")
    uow.kit_sites = Sites("ЖУК", [master.id, requester.id, second_master.id])
    now = datetime(2026, 10, 5, 9, 0)
    request = _completed(uow, _two_line_kit(uow, requester, now), storekeeper, now)
    assert request.status is FulfillmentStatus.COMPLETE

    ready = mark_ready(MarkReadyCommand(request.id, request.version, storekeeper, now, "3B2"), uow)

    (note,) = uow.outbox.items  # one message; the requester who is also listed as a master is addressed once
    assert note.payload["recipient_user_ids"].split(",") == [str(requester.id), str(master.id), str(second_master.id)]
    assert "место: 3B2" in note.payload["body"]
    (event,) = [item for item in uow.audit.items if item.event_type == "fulfillment.ready"]
    assert event.details == {"handout_location": "3B2", "site": "ЖУК"}
    assert ready.status is FulfillmentStatus.READY


def test_ready_without_a_site_directory_still_reaches_the_requester():
    uow = Uow()
    requester = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 10, 5, 9, 0)
    request = _completed(uow, _two_line_kit(uow, requester, now), requester, now)

    mark_ready(MarkReadyCommand(request.id, request.version, requester, now, "Зона выдачи"), uow)

    (note,) = uow.outbox.items
    assert note.payload["recipient_user_ids"] == str(requester.id)


def test_handout_place_offers_the_last_place_of_the_site_or_the_handout_zone():
    uow = Uow()
    actor = Actor(name="Кладовщик", department="Склад")
    request = _two_line_kit(uow, actor, datetime(2026, 10, 5, 9, 0))

    assert handout_place(request, uow) == "Зона выдачи"  # no site directory
    uow.kit_sites = Sites("ЦАР", [])
    assert handout_place(request, uow) == "Зона выдачи"
    uow.kit_sites = Sites("ЦАР", [], last="12C7")
    assert handout_place(request, uow) == "12C7"
