from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.shipping.domain import DeliveryTerms, FinishedGood, ProjectAssembly, Shipment, ShipmentLine, ShipmentStatus, ShippingValidationError, UpdType, plan_project_queue, shipping_gaps


def good() -> FinishedGood:
    return FinishedGood.create(project="591К.3", item_index="КБ1", customer="Заказчик", article="A-1", name="Жгут", ordered=Decimal("40"), unit="шт")


def shipment(item: FinishedGood, *, terms=DeliveryTerms.PICKUP, upd_type=UpdType.EDO, **own) -> Shipment:
    """Отправка со всеми данными для отгрузки (кроме трека ТК); `own` переопределяет любое поле создания."""
    fields = dict(
        receiver="Получатель", shipping_point="Москва", contact="", phone="+7 900 000-00-00", terms=terms,
        carrier="СДЭК" if terms == DeliveryTerms.CARRIER else "", upd_number="УПД-1" if upd_type not in (None, UpdType.NONE) else "",
    )
    fields.update(own)
    return Shipment.create(
        number="ОТГ-20260922-01", created_at=datetime(2026, 9, 22, 9), customer=item.customer,
        **{key: fields[key] for key in ("receiver", "shipping_point", "contact", "phone", "terms", "carrier")},
        deadline=None, upd_type=upd_type, upd_number=fields["upd_number"], upd_date=None,
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
    picked, _ = shipment(item, terms=DeliveryTerms.CARRIER, upd_type=UpdType.PAPER, upd_number="").pick({item.id: item.remaining}, {item.id: item.grn or ""})
    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))
    # Отправка, уехавшая до этого правила (без данных): закрытие по-прежнему называет, чего не хватает.
    shipped = replace(packed, status=ShipmentStatus.SHIPPED, shipped_at=date(2026, 9, 22))

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


def test_dispatch_counts_what_left_the_plant_and_blocks_putting_it_back():
    taken = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001").take(Decimal("6"))
    gone = taken.dispatch(Decimal("4"))

    assert (gone.shipped, gone.sent, gone.remaining) == (Decimal("6"), Decimal("4"), Decimal("4"))
    assert gone.put_back(Decimal("2")).shipped == Decimal("4")
    with pytest.raises(ShippingValidationError, match="не может превышать"):
        gone.put_back(Decimal("3"))
    with pytest.raises(ShippingValidationError, match="Уехало больше"):
        gone.dispatch(Decimal("3"))


def test_shipped_before_erp_is_a_total_not_an_increment():
    item = good()
    first = item.ship_before_erp(Decimal("154"))
    again = first.ship_before_erp(Decimal("154"))
    more = again.ship_before_erp(Decimal("200"))

    assert (first.accepted, first.shipped, first.sent, first.before_erp) == (Decimal("154"),) * 4
    assert first.remaining == Decimal("0") and first.grn is None and first.has_movement
    assert again is first
    assert more.ship_before_erp(Decimal("100")) is more
    assert (more.accepted, more.sent, more.before_erp, more.version) == (Decimal("200"), Decimal("200"), Decimal("200"), item.version + 2)
    with pytest.raises(ShippingValidationError):
        item.ship_before_erp(Decimal("0"))


def test_project_queue_adds_one_row_per_new_kb_and_is_idempotent():
    cards = [ProjectAssembly("КБ1", "Жгут", Decimal("40")), ProjectAssembly("КБ2", "Кабель", Decimal("5"), "A-2")]
    first = plan_project_queue(cards, [])
    assert [item.item_index for item in first.add] == ["КБ1", "КБ2"]
    assert first.update == ()

    existing = [good(), FinishedGood.create(project="591К.3", item_index="кб2", customer="", article="A-2", name="Кабель", ordered=Decimal("5"), unit="шт")]
    again = plan_project_queue(cards, existing)
    assert again.add == () and again.update == () and again.skipped == ()


def test_project_queue_follows_run_until_something_ships():
    changed = [ProjectAssembly("КБ1", "Жгут", Decimal("45"))]
    accepted = good().accept(Decimal("6"), date(2026, 9, 22), "ГП.26.0000001")
    plan = plan_project_queue(changed, [accepted])
    assert [(item.item_index, run) for item, run in plan.update] == [("КБ1", Decimal("45"))]

    shipped = accepted.take(Decimal("2"))
    assert plan_project_queue(changed, [shipped]).update == ()
    with pytest.raises(ShippingValidationError, match="уже отгружалась"):
        shipped.reorder(Decimal("45"))


def test_project_queue_ignores_blank_index_and_reports_kb_without_run():
    plan = plan_project_queue([
        ProjectAssembly("  ", "Без КБ", Decimal("3")),
        ProjectAssembly("КБ3", "Без тиража", Decimal("0")),
        ProjectAssembly("КБ4", "Дубль", Decimal("2")),
        ProjectAssembly("кб4", "Дубль", Decimal("9")),
    ], [])
    assert [item.item_index for item in plan.add] == ["КБ4"]
    assert plan.add[0].run == Decimal("2")
    assert plan.skipped == ("КБ3",)


def test_goods_with_movement_outlive_the_project_card():
    assert not good().has_movement
    assert good().accept(Decimal("1"), date(2026, 9, 22), "ГП.26.0000001").has_movement


def test_project_addressee_remember_keeps_stored_value_when_new_one_is_empty():
    from app.modules.shipping.domain import ProjectAddressee

    stored = ProjectAddressee(project="591К.3", version=1, shipping_point="Москва", contact="Иванов", phone="+7 900")
    same = stored.remember(shipping_point="  ", contact="", phone="+7 900")
    moved = stored.remember(shipping_point="Тверь", contact="", phone="")

    assert same is stored
    assert (moved.shipping_point, moved.contact, moved.phone) == ("Тверь", "Иванов", "+7 900")


def test_project_addressee_card_edit_changes_only_sent_fields_and_empty_erases():
    from app.modules.shipping.domain import ProjectAddressee

    stored = ProjectAddressee(project="591К.3", version=1, receiver="АО Получатель", shipping_point="Москва",
                              contact="Иванов", terms=DeliveryTerms.CARRIER, upd_type=UpdType.PAPER)
    edited = stored.edit({"contact": "", "terms": None, "carrier": " ПЭК "})

    assert edited.contact == ""
    assert edited.terms is None
    assert edited.carrier == "ПЭК"
    assert (edited.receiver, edited.shipping_point, edited.upd_type) == ("АО Получатель", "Москва", UpdType.PAPER)
    with pytest.raises(ShippingValidationError):
        stored.edit({"customer": "x"})


def test_effective_address_own_non_empty_wins_and_empty_inherits_project_card():
    from app.modules.shipping.domain import ProjectAddressee, effective_address

    card = ProjectAddressee(project="591К.3", receiver="АО Получатель", shipping_point="Москва", contact="Иванов",
                            phone="+7 900", terms=DeliveryTerms.CARRIER, carrier="ПЭК", upd_type=UpdType.PAPER)
    address = effective_address(receiver="", shipping_point="Тверь", contact=" ", phone="", terms=None,
                                carrier="", upd_type=UpdType.EDO, project=card)
    typed = effective_address(receiver="ООО Другой", shipping_point="", contact="", phone="", terms=None,
                              carrier="", upd_type=None, project=card)

    assert address.shipping_point == "Тверь"
    assert address.upd_type == UpdType.EDO
    assert (address.receiver, address.contact, address.phone, address.terms, address.carrier) == (
        "АО Получатель", "Иванов", "+7 900", DeliveryTerms.CARRIER, "ПЭК")
    assert address.inherited == {"receiver", "contact", "phone", "terms", "carrier"}
    assert typed.receiver == "ООО Другой"


def test_effective_address_without_card_falls_back_to_customer_for_receiver_only():
    from app.modules.shipping.domain import effective_address

    address = effective_address(receiver="", shipping_point="", contact="", phone="", terms=None, carrier="",
                                upd_type=None, project=None, customer_fallback="Заказчик")

    assert address.receiver == "Заказчик"
    assert address.shipping_point == ""
    assert address.terms is None
    assert address.inherited == frozenset()


def test_ship_from_any_active_status_when_something_is_picked():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    new = shipment(item)
    picked, _ = new.pick({item.id: item.remaining}, {item.id: item.grn or ""})

    # shipShipped_: статус не важен, важно, что набрано (shipGateMiss_('Отгружена')).
    assert picked.mark_shipped(date(2026, 9, 22)).status == ShipmentStatus.SHIPPED
    assert replace(picked, status=ShipmentStatus.NEW).mark_shipped(date(2026, 9, 22)).status == ShipmentStatus.SHIPPED
    with pytest.raises(ShippingValidationError, match="ни одна позиция не набрана"):
        new.mark_shipped(date(2026, 9, 22))
    with pytest.raises(ShippingValidationError, match="только активную"):
        picked.mark_shipped(date(2026, 9, 22)).mark_shipped(date(2026, 9, 23))


def test_shipment_without_terms_and_upd_type_needs_upd_file_to_ship_like_paper():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item, terms=None, upd_type=None).pick({item.id: item.remaining}, {item.id: item.grn or ""})

    # Владелец 28.10.2026: без условий доставки и типа УПД отправка не уезжает — оба называются в списке.
    with pytest.raises(ShippingValidationError, match="условия доставки.*тип УПД"):
        picked.mark_shipped(date(2026, 9, 22))
    documented = replace(picked, terms=DeliveryTerms.PICKUP).update_documents(
        upd_type=UpdType.PAPER, upd_number="7", upd_date=None, upd_url="https://f/u.pdf", track_number="")
    # Самовывозу трек для закрытия не нужен (его требует только «ДОСТАВКА ТК»).
    assert documented.mark_shipped(date(2026, 9, 22)).close().status == ShipmentStatus.CLOSED


# ── Что не хватает для отгрузки (владелец 28.10.2026): один список для «Отгрузить», печати DN и экранов ──────────────


def _gaps(**own) -> tuple[str, ...]:
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    terms = own.pop("terms", DeliveryTerms.PICKUP)
    upd_type = own.pop("upd_type", UpdType.EDO)
    track = own.pop("track", "")
    url = own.pop("upd_url", "")
    made = replace(shipment(item, terms=terms, upd_type=upd_type, **own), track_number=track, upd_url=url)
    return shipping_gaps(made)


def test_pickup_needs_terms_receiver_phone_and_upd_but_neither_address_nor_track():
    assert _gaps() == ()
    assert _gaps(shipping_point="") == ()
    assert _gaps(phone="") == ("телефон получателя",)
    assert _gaps(terms=None) == ("условия доставки",)
    item = good()
    assert shipping_gaps(replace(shipment(item), receiver=" ")) == ("получатель",)


def test_carrier_delivery_adds_carrier_address_and_track_and_the_track_is_the_storekeepers_part():
    assert _gaps(terms=DeliveryTerms.CARRIER) == ("трек-номер",)
    assert _gaps(terms=DeliveryTerms.CARRIER, track="T-1") == ()
    assert _gaps(terms=DeliveryTerms.CARRIER, carrier="", shipping_point="", track="T-1") == ("перевозчик", "адрес доставки")
    item = good()
    # Без трека — то, что вносит менеджер: с этим списком печатается DN.
    assert shipping_gaps(shipment(item, terms=DeliveryTerms.CARRIER), with_track=False) == ()


def test_upd_types_decide_the_number_and_file_gaps():
    assert _gaps(upd_type=None, upd_number="") == ("тип УПД",)
    assert _gaps(upd_type=UpdType.EDO, upd_number="") == ("№ УПД",)
    assert _gaps(upd_type=UpdType.PAPER, upd_number="") == ("№ УПД", "файл УПД")
    assert _gaps(upd_type=UpdType.PAPER, upd_number="7") == ("файл УПД",)
    assert _gaps(upd_type=UpdType.PAPER, upd_number="7", upd_url="/api/shipping/files/x") == ()
    # ЭДО файла не требует; «без УПД» не просит ни номера, ни файла.
    assert _gaps(upd_type=UpdType.EDO, upd_number="7") == ()
    assert _gaps(upd_type=UpdType.NONE, upd_number="") == ()


def test_shipping_refuses_with_the_whole_list_in_the_gates_words():
    item = good().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    picked, _ = shipment(item, terms=None, upd_type=None, phone="").pick({item.id: item.remaining}, {item.id: item.grn or ""})

    with pytest.raises(ShippingValidationError) as refused:
        picked.mark_shipped(date(2026, 9, 22))
    assert str(refused.value) == "Нельзя отгрузить: не хватает — условия доставки, телефон получателя, тип УПД"
    # Закрытие уже уехавших не пересматривается: оно проверяет своё (№ и файл УПД, трек).
    gone = replace(picked, status=ShipmentStatus.SHIPPED, shipped_at=date(2026, 9, 22))
    assert gone.status == ShipmentStatus.SHIPPED


def test_a_shipment_may_be_created_without_an_address_for_the_storekeeper_to_pack_first():
    item = good()
    assert shipment(item, shipping_point="").shipping_point == ""
