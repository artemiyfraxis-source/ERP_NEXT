from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.shipping.domain import DeliveryTerms, FinishedGood, Shipment, ShipmentLine, ShippingValidationError, UpdType


def good() -> FinishedGood:
    return FinishedGood.create(project="591К.3", item_index="КБ1", customer="Заказчик", article="A-1", name="Жгут", ordered=Decimal("40"), unit="шт")


def shipment(item: FinishedGood, *, terms=DeliveryTerms.PICKUP, upd_type=UpdType.EDO) -> Shipment:
    return Shipment.create(
        number="ОТГ-20260922-01", created_at=datetime(2026, 9, 22, 9), customer=item.customer,
        receiver="Получатель", shipping_point="Москва", contact="", phone="", terms=terms,
        carrier="", deadline=None, upd_type=upd_type, upd_number="", upd_date=None,
        upd_url="", manager="Менеджер", note="", lines=(ShipmentLine.create(item, Decimal("10")),),
    )


def test_partial_pick_keeps_requested_and_picked_separate():
    item = good().accept(Decimal("6"), date(2026, 9, 22), "ГП.26.0000001")
    result, taken = shipment(item).pick({item.id: item.remaining}, {item.id: item.grn or ""})

    assert result.lines[0].requested == Decimal("10")
    assert result.lines[0].picked == Decimal("6")
    assert result.lines[0].shortage == Decimal("4")
    assert taken == {item.id: Decimal("6")}


def test_carrier_close_requires_upd_file_number_date_and_track():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item, terms=DeliveryTerms.CARRIER).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))
    shipped = packed.mark_shipped(date(2026, 9, 22))

    with pytest.raises(ShippingValidationError, match="№ УПД, файл УПД, трек-номер"):
        shipped.close()


def test_paper_shipment_requires_upd_file_before_dispatch():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item, upd_type=UpdType.PAPER).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))

    with pytest.raises(ShippingValidationError, match="файл УПД"):
        packed.mark_shipped(date(2026, 9, 22))


def test_packed_shipment_cannot_be_repacked_with_new_document_numbers():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))

    with pytest.raises(ShippingValidationError, match="упаковка недоступна"):
        packed.pack("PL591К.3-2", "DN591К.3-2", date(2026, 9, 22))
