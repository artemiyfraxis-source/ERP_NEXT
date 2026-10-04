"""Лист закупки как в SUPPLYMAP: КОММЕНТАРИЙ закупщика, ОЖИД. ДАТА руками (`gateAwaitInvoice_`, Guards.gs:523-560)
и ФАКТ. ДАТА из приёмки (`syncActualArrival_`, OwnerTick.gs:949-971) — правила строки без базы."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.procurement.api import _line_result
from app.modules.procurement.application import (
    SetExpectedArrivalCommand,
    SetNoteCommand,
    set_procurement_expected_arrival,
    set_procurement_note,
)
from app.modules.procurement.domain import (
    EXPECTED_ARRIVAL_EDITABLE,
    NOTE_EDITABLE,
    NOTE_MAX_LENGTH,
    ProcurementStatus,
    ProcurementValidationError,
    create_backorder_line,
    create_line_from_requested,
)
from app.shared.events import OutboxMessage
from tests.factories import Actor
from tests.procurement.test_application import FakeUnitOfWork, line_awaiting_invoice, requested_message

BUYER = Actor(name="Петров", department="Закупки")
OTHER = Actor(name="Сидоров", department="Закупки")
NOW = datetime(2026, 9, 16, 9, 0)


def line_in(status: ProcurementStatus, **fields):
    line = create_line_from_requested(requested_message())
    return replace(line, status=status, buyer=None if status is ProcurementStatus.NEW else BUYER, **fields)


# --- КОММЕНТАРИЙ ---------------------------------------------------------------------------------------------


def test_note_is_closed_on_new_and_cancelled_and_open_everywhere_else():
    assert NOTE_EDITABLE == set(ProcurementStatus) - {ProcurementStatus.NEW, ProcurementStatus.CANCELLED}
    with pytest.raises(ProcurementValidationError, match="возьмите строку в работу"):
        line_in(ProcurementStatus.NEW).set_note("позвонить поставщику", BUYER)
    with pytest.raises(ProcurementValidationError, match="отменена"):
        line_in(ProcurementStatus.CANCELLED).set_note("позвонить поставщику", BUYER)
    for status in NOTE_EDITABLE:
        line = line_in(status)
        noted = line.set_note("  позвонить поставщику  ", BUYER)
        assert (noted.note, noted.version, noted.status) == ("позвонить поставщику", line.version + 1, status)


def test_note_belongs_to_the_line_buyer_trims_clears_and_is_bounded():
    line = line_in(ProcurementStatus.IN_WORK)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        line.set_note("чужая заметка", OTHER)
    noted = line.set_note("счёт обещали в пятницу", BUYER)
    assert noted.set_note("счёт обещали в пятницу", BUYER) is noted
    cleared = noted.set_note("   ", BUYER)
    assert (cleared.note, cleared.version) == (None, noted.version + 1)
    assert line.set_note("я" * NOTE_MAX_LENGTH, BUYER).note == "я" * NOTE_MAX_LENGTH
    with pytest.raises(ProcurementValidationError, match="длиннее"):
        line.set_note("я" * (NOTE_MAX_LENGTH + 1), BUYER)


def test_note_use_case_checks_the_version_and_audits():
    uow = FakeUnitOfWork()
    line = line_awaiting_invoice(uow, BUYER)
    noted = set_procurement_note(SetNoteCommand(line.id, line.version, "ждём КП", BUYER, NOW), uow)
    assert (noted.note, noted.status) == ("ждём КП", ProcurementStatus.AWAITING_INVOICE)
    assert uow.audit.items[-1].event_type == "procurement.note_saved"
    assert uow.audit.items[-1].details["note"] == "ждём КП"
    with pytest.raises(Exception, match="изменилась"):
        set_procurement_note(SetNoteCommand(line.id, line.version, "другое", BUYER, NOW), uow)


# --- ОЖИД. ДАТА -------------------------------------------------------------------------------------------------


def test_expected_date_opens_at_awaiting_invoice_and_closes_after_quarantine():
    assert EXPECTED_ARRIVAL_EDITABLE == {
        ProcurementStatus.AWAITING_INVOICE,
        ProcurementStatus.TO_PAY,
        ProcurementStatus.IN_TRANSIT,
        ProcurementStatus.QUARANTINE,
    }
    for status in set(ProcurementStatus) - EXPECTED_ARRIVAL_EDITABLE:
        with pytest.raises(ProcurementValidationError):
            line_in(status).set_expected_arrival(date(2026, 10, 1), BUYER)
    for status in EXPECTED_ARRIVAL_EDITABLE:
        line = line_in(status, expected_arrival=date(2026, 9, 25))
        moved = line.set_expected_arrival(date(2026, 10, 1), BUYER)
        assert (moved.expected_arrival, moved.version, moved.status) == (date(2026, 10, 1), line.version + 1, status)


def test_expected_date_message_names_the_window_and_checks_owner_and_order_date():
    with pytest.raises(ProcurementValidationError, match="от «Жду счет» до «Карантин»"):
        line_in(ProcurementStatus.IN_WORK).set_expected_arrival(date(2026, 10, 1), BUYER)
    line = line_in(ProcurementStatus.IN_TRANSIT, expected_arrival=date(2026, 9, 25))
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        line.set_expected_arrival(date(2026, 10, 1), OTHER)
    with pytest.raises(ProcurementValidationError, match="раньше заказа"):
        line.set_expected_arrival(date(2026, 9, 1), BUYER)
    assert line.set_expected_arrival(date(2026, 9, 25), BUYER) is line


class RecordingCopier:
    def __init__(self):
        self.calls = []

    def copy_expected_arrivals(self, dates, actor, now):
        self.calls.append((dict(dates), actor.name))
        return len(dates)


def test_expected_date_use_case_copies_into_receiving_only_once_the_line_is_there():
    uow = FakeUnitOfWork()
    copier = RecordingCopier()
    line = line_awaiting_invoice(uow, BUYER)
    moved = set_procurement_expected_arrival(
        SetExpectedArrivalCommand(line.id, line.version, date(2026, 9, 30), BUYER, NOW), uow, copier,
    )
    assert moved.expected_arrival == date(2026, 9, 30)
    assert copier.calls == []  # «Жду счет»: no delivery yet, the payment snapshot carries the date
    assert uow.audit.items[-1].event_type == "procurement.expected_arrival_set"
    assert uow.audit.items[-1].details["expected_arrival"] == "2026-09-30"

    in_transit = replace(moved, status=ProcurementStatus.IN_TRANSIT, version=moved.version + 1)
    uow.procurement.add(in_transit)
    moved = set_procurement_expected_arrival(
        SetExpectedArrivalCommand(in_transit.id, in_transit.version, date(2026, 10, 3), BUYER, NOW), uow, copier,
    )
    assert copier.calls == [({moved.id: date(2026, 10, 3)}, "Петров")]


# --- ФАКТ. ДАТА -------------------------------------------------------------------------------------------------


def test_actual_arrival_follows_receipts_in_one_version_step_and_is_never_cleared():
    line = line_in(ProcurementStatus.IN_TRANSIT)
    dated = line.follow_receipts(ProcurementStatus.IN_TRANSIT, date(2026, 9, 24))
    assert (dated.actual_arrival, dated.status, dated.version) == (
        date(2026, 9, 24), ProcurementStatus.IN_TRANSIT, line.version + 1,
    )
    done = dated.follow_receipts(ProcurementStatus.DONE, date(2026, 9, 26))
    assert (done.actual_arrival, done.status, done.version) == (
        date(2026, 9, 26), ProcurementStatus.DONE, dated.version + 1,
    )
    assert dated.follow_receipts(None, None) is dated
    assert dated.follow_receipts(ProcurementStatus.IN_TRANSIT, date(2026, 9, 24)) is dated
    approval = line_in(ProcurementStatus.APPROVAL)
    assert approval.follow_receipts(ProcurementStatus.DONE, date(2026, 9, 24)) is approval


def test_line_json_carries_the_calc_prices_note_and_actual_arrival():
    line = line_in(
        ProcurementStatus.DONE,
        calculated_price=Decimal("800.00"),
        calculated_price_with_vat=Decimal("976.00"),
        calculated_sum=Decimal("1952.00"),
        note="ок",
        actual_arrival=date(2026, 9, 26),
    )
    body = _line_result(line)
    assert {key: body[key] for key in (
        "calculated_price", "calculated_price_with_vat", "calculated_sum", "note", "actual_arrival",
    )} == {
        "calculated_price": "800", "calculated_price_with_vat": "976", "calculated_sum": "1952",
        "note": "ок", "actual_arrival": "2026-09-26",
    }


def test_backorder_line_starts_without_dates_and_keeps_the_note():
    original = line_in(
        ProcurementStatus.DONE,
        actual_quantity=Decimal("10"),
        paid_at=datetime(2026, 9, 20, 10, 0),
        expected_arrival=date(2026, 9, 25),
        actual_arrival=date(2026, 9, 24),
        note="партия пришла не вся",
    )
    message = OutboxMessage(
        id=requested_message(2).id,
        topic="procurement.backorder_requested",
        idempotency_key="receiving:x:backorder",
        created_at=datetime(2026, 9, 24, 12, 0),
        payload={"procurement_line_id": str(original.id), "backorder_quantity": "3"},
    )
    backorder = create_backorder_line(original, message, original.legacy_id + "-Д1")
    assert (backorder.expected_arrival, backorder.actual_arrival, backorder.note) == (
        None, None, "партия пришла не вся",
    )
