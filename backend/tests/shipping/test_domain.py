from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.shipping.domain import DeliveryTerms, FinishedGood, Shipment, ShipmentLine, ShipmentStatus, ShippingValidationError, UpdType


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


def test_unpick_releases_whole_line_and_returns_to_new_when_nothing_left():
    item = good().accept(Decimal("6"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item).pick({item.id: item.remaining}, {item.id: item.grn or ""})

    result, good_id, quantity = picked.unpick(picked.lines[0].id)

    assert (good_id, quantity) == (item.id, Decimal("6"))
    assert result.lines[0].picked == Decimal("0")
    assert result.lines[0].grn is None
    assert result.status == ShipmentStatus.NEW
    assert result.version == picked.version + 1


def test_unpick_one_line_keeps_shipment_assembled_while_another_line_is_picked():
    first = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    second = FinishedGood.create(project="591К.3", item_index="КБ2", customer="Заказчик", article="A-2", name="Жгут 2", ordered=Decimal("5"), unit="шт").accept(Decimal("5"), date(2026, 9, 22), "ГП.26.0000002")
    draft = replace(shipment(first), lines=(ShipmentLine.create(first, Decimal("10")), ShipmentLine.create(second, Decimal("5"))))
    picked, _ = draft.pick({first.id: first.remaining, second.id: second.remaining}, {first.id: first.grn or "", second.id: second.grn or ""})

    result, good_id, quantity = picked.unpick(picked.lines[1].id)

    assert (good_id, quantity) == (second.id, Decimal("5"))
    assert result.lines[0].picked == Decimal("10")
    assert result.status == ShipmentStatus.ASSEMBLED


def test_unpick_rejects_empty_line_unknown_line_and_packed_shipment():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    fresh = shipment(item)
    with pytest.raises(ShippingValidationError, match="ничего не набрано"):
        fresh.unpick(fresh.lines[0].id)

    picked, _ = fresh.pick({item.id: item.remaining}, {item.id: item.grn or ""})
    with pytest.raises(ShippingValidationError, match="не найдена"):
        picked.unpick(uuid4())

    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))
    with pytest.raises(ShippingValidationError, match="возврат отбора недоступен"):
        packed.unpick(packed.lines[0].id)


def test_cancel_releases_every_picked_line_summed_per_good():
    item = good().accept(Decimal("7"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))

    cancelled, released = packed.cancel()

    assert released == {item.id: Decimal("7")}
    assert cancelled.status == ShipmentStatus.CANCELLED
    assert all(line.picked == 0 and line.grn is None for line in cancelled.lines)


def test_cancel_of_unpicked_shipment_releases_nothing():
    cancelled, released = shipment(good()).cancel()

    assert released == {}
    assert cancelled.status == ShipmentStatus.CANCELLED


def test_shipped_closed_or_cancelled_shipment_cannot_be_cancelled_again():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    shipped = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22)).mark_shipped(date(2026, 9, 22))
    cancelled, _ = shipment(item).cancel()

    for blocked in (shipped, cancelled):
        with pytest.raises(ShippingValidationError, match="нельзя отменить"):
            blocked.cancel()


def test_put_back_is_symmetric_to_take_and_never_exceeds_taken():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    taken = item.take(Decimal("4"))
    returned = taken.put_back(Decimal("4"))

    assert taken.remaining == Decimal("6")
    assert returned.shipped == Decimal("0")
    assert returned.remaining == Decimal("10")
    assert returned.version == item.version + 2
    with pytest.raises(ShippingValidationError, match="не может превышать"):
        taken.put_back(Decimal("4.01"))
    with pytest.raises(ShippingValidationError):
        taken.put_back(Decimal("0"))
