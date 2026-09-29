"""Прослеживаемость строки закупки (TRACE_COLUMNS.PURCHASE, Config.gs:269): закупщик заносит изготовителя,
страну, тип/№/дату документа качества и стандарт; СТРАНА подставляется по изготовителю
(countryForManufacturer_), всё уходит в приёмку со снимком поставки."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.procurement.application import (
    MarkProcurementPaidCommand,
    RecordInvoiceCommand,
    SaveProcurementMoneyCommand,
    SetTraceabilityCommand,
    mark_procurement_paid,
    record_invoice,
    save_procurement_money,
    set_procurement_traceability,
)
from app.modules.procurement.domain import (
    MoneyEntry,
    ProcurementTraceability,
    ProcurementValidationError,
)
from app.modules.procurement.money import MoneyInput
from app.modules.receiving.domain import create_line_from_expected
from tests.factories import Actor
from tests.procurement.test_application import FakeUnitOfWork, line_awaiting_invoice


class FakeDirectory:
    def __init__(self, countries):
        self.countries = countries

    def supplier_map(self):
        return {}

    def learn(self, link, supplier, actor, now):
        return False

    def country_for_manufacturer(self, manufacturer):
        return self.countries.get(manufacturer.casefold(), "")


def _trace(uow, line, buyer, directory=None, **fields):
    return set_procurement_traceability(
        SetTraceabilityCommand(
            line_id=line.id,
            expected_version=line.version,
            trace=ProcurementTraceability(**fields),
            actor=buyer,
            now=datetime(2026, 9, 15, 12, 0),
        ),
        uow,
        directory,
    )


def test_traceability_travels_to_receiving_with_the_delivery():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    line = line_awaiting_invoice(uow, buyer)
    line = _trace(
        uow, line, buyer, FakeDirectory({"molex": "США"}),
        manufacturer=" Molex ", quality_document_type="Паспорт", quality_document_number="П-17",
        quality_document_date=date(2026, 9, 1), standard="ГОСТ 23587-96",
    )
    assert line.trace.country == "США"
    assert line.trace.manufacturer == "Molex"
    line = save_procurement_money(
        SaveProcurementMoneyCommand(
            line_id=line.id, expected_version=line.version,
            entry=MoneyEntry(amounts=MoneyInput(currency="RUB", price=Decimal("100"), vat_rate=Decimal("22")),
                             invoice_item_name="Разъём"),
            actor=buyer, now=datetime(2026, 9, 15, 12, 5),
        ),
        uow,
    )
    line = record_invoice(
        RecordInvoiceCommand(
            line_id=line.id, expected_version=line.version, invoice_url="https://files.test/i.pdf",
            expected_arrival=date(2026, 9, 25), actor=buyer, now=datetime(2026, 9, 15, 12, 10),
        ),
        uow,
    )
    mark_procurement_paid(
        MarkProcurementPaidCommand(line_id=line.id, expected_version=line.version, actor=buyer,
                                   now=datetime(2026, 9, 15, 12, 20)),
        uow,
    )

    message = uow.outbox.items[-1]
    assert message.topic == "receiving.expected"
    assert {key: message.payload[key] for key in (
        "manufacturer", "country", "quality_document_type", "quality_document_number",
        "quality_document_date", "standard",
    )} == {
        "manufacturer": "Molex", "country": "США", "quality_document_type": "Паспорт",
        "quality_document_number": "П-17", "quality_document_date": "2026-09-01", "standard": "ГОСТ 23587-96",
    }
    received = create_line_from_expected(message)
    assert (received.manufacturer, received.country, received.quality_document_date) == (
        "Molex", "США", date(2026, 9, 1),
    )


def test_typed_country_wins_and_unchanged_maker_keeps_country():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    directory = FakeDirectory({"molex": "США"})
    line = line_awaiting_invoice(uow, buyer)
    line = _trace(uow, line, buyer, directory, manufacturer="Molex", country="Китай")
    assert line.trace.country == "Китай"
    line = _trace(uow, line, buyer, directory, manufacturer="Molex", country="")
    assert line.trace.country is None
    line = _trace(uow, line, buyer, directory, manufacturer="Unknown")
    assert line.trace.country is None


def test_only_the_owner_edits_and_an_empty_form_is_a_no_op():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    line = line_awaiting_invoice(uow, buyer)
    with pytest.raises(ProcurementValidationError):
        _trace(uow, line, Actor(name="Сидоров", department="Закупки"), manufacturer="Molex")
    unchanged = _trace(uow, line, buyer)
    assert unchanged.version == line.version
