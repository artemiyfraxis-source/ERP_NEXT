"""PL (форма 04.2) и DN (форма 04.1) по образцам владельца 15.10.2026: значения полей формы целиком.

Подпись кладовщика — фамилия кладовщика (уровень «Кладовщик», активный), который упаковал («Гусев А.А.»); менеджер,
полный доступ и токен API не подписывают (владелец 22.10.2026); этикетки коробов — по одной на место.
"""

import pytest

from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.shared.persistence import OutboxMessageRow
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.modules.shipping.documents import build_delivery_note, packer_short, person_short, split_carrier, split_hours, storekeeper_signature
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from app.modules.shipping.domain import ShipmentStatus
from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture
from tests.shipping.test_domain import good as good_domain, shipment as domain_shipment
from tests.shipping.test_documents_api import _shipment, _stocked

SHIPPER = 'ООО "ЗЭП ФРАКСИС"'
STOREKEEPER = Actor(id=UUID("20000000-0000-0000-0000-000000000002"), name="Гусев Алексей Андреевич", department="Склад")
OTHER_STOREKEEPER = Actor(id=UUID("30000000-0000-0000-0000-000000000003"), name="Петров Пётр", department="Склад")
BOXES = [
    {"dims": "32x25x21", "net": "11", "gross": "11,6", "seal": "П-17",
     "items": [{"item_index": "КБ1", "quantity": "6"}, {"item_index": "КБ2", "quantity": "5"}]},
    {"items": [{"item_index": "КБ1", "quantity": "4"}]},
]


def _seed_user(actor, level="storekeeper", active=True):
    """Сотрудник в справочнике: упаковал тот, кто сейчас кладовщик, определяет уровень доступа его карточки."""
    uow = app.state.shipping_uow_factory()
    with uow:
        uow.session.add(IdentityUserRow(
            id=actor.id, login=str(actor.id), email=f"{actor.id}@example.test", name=actor.name, department="Склад",
            role="staff", site=None, active=active, access_level=level,
        ))
        uow.session.commit()


@pytest.fixture(autouse=True)
def storekeepers(shipping_api):
    """Гусев и Петров — кладовщики; прочие тесты заводят своих людей сами."""
    _seed_user(STOREKEEPER)
    _seed_user(OTHER_STOREKEEPER)


@contextmanager
def _acting(actor, at=None):
    """Запросы внутри блока идут от `actor` (кладовщик раскладывает по коробам), при `at` — в это время."""
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="store@example.test", roles=frozenset({"admin"}), permissions={"shipping": "edit"},
    )
    if at is not None:
        app.dependency_overrides[moscow_now] = lambda: at
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def _print(client, shipment_id, document):
    response = client.get(f"/api/shipping/shipments/{shipment_id}/print", params={"document": document})
    assert response.status_code == 200, response.text
    return response.json()["forms"]


def _lay_out(client, shipment, boxes=BOXES):
    laid = client.put(f"/api/shipping/shipments/{shipment['id']}/boxes", json={"version": shipment["version"], "boxes": boxes})
    assert laid.status_code == 200, laid.text
    return laid.json()["shipment"]


def _packed_in_two_boxes(client, **extra):
    """Менеджер («Администратор») заводит отправку, кладовщик Гусев раскладывает её по коробам."""
    first = _stocked(client, item_index="КБ1", accepted="10")
    second = _stocked(client, item_index="КБ2", accepted="5")
    shipment = _shipment(client, [{"good_id": first["id"], "quantity": "10"}, {"good_id": second["id"], "quantity": "5"}], **extra)
    with _acting(STOREKEEPER):
        return _lay_out(client, shipment)


def _row(part, name, quantity, batch="", pl="", boxes=""):
    return {"part": part, "name": name, "quantity": quantity, "unit": "шт", "batch": batch, "pl": pl, "boxes": boxes}


def test_packing_list_is_one_sheet_per_box_as_form_04_2(shipping_api):
    client = shipping_api
    shipment = _packed_in_two_boxes(client)
    forms = _print(client, shipment["id"], "pl")
    assert shipment["manager"] == "Администратор"

    common = {"kind": "pl", "title": "PL591К.3-1", "number": ["PL", "591К.3", "-", "1"], "places": "2", "package": "Короб",
              "packed_at": "22.09.26", "shipped_at": "22.09.26", "packer": "Гусев А.А."}
    assert forms == [
        {**common, "place": "1", "dims": "32x25x21", "net": "11", "gross": "11,6", "seal": "П-17", "rows": [
            # «Чертежный номер/Артикул» — № КБ с артикулом, «Номер этикетки» — GRN партии ГП.
            _row("КБ1 A-КБ1", "Жгут КБ1", "6", batch="ГП.26.0000001"), _row("КБ2 A-КБ2", "Жгут КБ2", "5", batch="ГП.26.0000002"),
        ]},
        # Короб без габаритов и веса: клетки остаются пустыми — дописать от руки.
        {**common, "place": "2", "dims": "", "net": "", "gross": "", "seal": "", "rows": [
            _row("КБ1 A-КБ1", "Жгут КБ1", "4", batch="ГП.26.0000001"),
        ]},
    ]


def test_delivery_note_sums_each_kb_over_boxes_with_project_card_requisites(shipping_api):
    client = shipping_api
    client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "po": "PO-7", "contract": "Д-12", "spec": "С-3", "invoice": "591К.3", "pays": "Заказчик",
        "customer_address": "Москва, ул. Заказчика, 1", "hours": "9-18",  # pays и customer_address хранятся, но не печатаются
    })
    shipment = _packed_in_two_boxes(client, contact="Иванов", phone="+7 900 000-00-00", carrier="СДЭК")
    forms = _print(client, shipment["id"], "dn")

    assert forms == [{
        "kind": "dn", "title": "DN591К.3-1", "number": ["DN", "591К.3", "-", "1"],
        # Ещё не отгружена — дата упаковки, как в образце (DN печатают вместе с PL).
        "date": "22.09.26",
        "customer": "Заказчик", "po": "PO-7", "po_from_card": True, "contract": "Д-12", "spec": "С-3", "invoice": "591К.3",
        "terms": "Доставка ТК",
        # Получатель не заказчик — ТК везёт ему: он в адресе доставки.
        "shipping_address": "Получатель, Москва", "hours": "9-18", "contact": "Иванов", "phone": "+7 900 000-00-00",
        "carrier": "СДЭК", "track": "", "tk_note": "",
        "rows": [
            _row("КБ1 A-КБ1", "Жгут КБ1", "10", pl="PL591К.3-1", boxes="BXN 1, 2"),
            _row("КБ2 A-КБ2", "Жгут КБ2", "5", pl="PL591К.3-1", boxes="BXN 1"),
        ],
        "upd_edo": True, "upd_paper": False, "paper_box": "BXN 1", "note": "",
        # Брутто второго короба не взвешено — общий вес пустой, дописать от руки.
        "places": "2", "gross": "", "upd": "УПД-1", "shipped_at": "22.09.26", "packer": "Гусев А.А.",
    }]


def test_delivery_note_takes_upd_number_and_date_and_the_shipping_day(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="10")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "2.5"}], upd_type="БУМАГА", terms="САМОВЫВОЗ", carrier="",
                         upd_number="УПД-17", upd_date="2026-09-21", upd_url="https://files.example/upd.pdf")
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    assert shipped.status_code == 200, shipped.text

    dn = _print(client, shipment["id"], "dn")[0]
    pl = _print(client, shipment["id"], "pl")

    assert (dn["upd"], dn["date"], dn["shipped_at"], dn["places"]) == ("УПД-17 от 21.09.26", "22.09.26", "22.09.26", "1")
    assert (dn["upd_edo"], dn["upd_paper"]) == (False, True)
    assert dn["rows"][0]["boxes"] == "BXN 1"
    assert [row["quantity"] for row in pl[0]["rows"]] == ["2,5"]


def test_upd_without_number_refuses_the_dn_and_no_upd_says_so(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="6")
    paper = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], upd_type="БУМАГА", upd_number="")
    none = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], upd_type="БЕЗ УПД", upd_number="")

    # Владелец 28.10.2026: пока не внесён № УПД, DN не печатается.
    refused = client.get(f"/api/shipping/shipments/{paper['id']}/print", params={"document": "dn"})
    assert refused.status_code == 409 and refused.json()["detail"] == "Нельзя печатать DN: не хватает — № УПД, файл УПД"
    # «Без УПД» — только по разрешению с PIN (владелец 29.10.2026); с ним DN пишет «без УПД» (test_dispatch_plan).
    unapproved = client.get(f"/api/shipping/shipments/{none['id']}/print", params={"document": "dn"})
    assert unapproved.status_code == 409
    assert unapproved.json()["detail"] == "Нельзя печатать DN: не хватает — разрешение на отгрузку без УПД"


def test_part_number_skips_an_article_that_is_only_project_and_kb(shipping_api):
    client = shipping_api
    plain = client.post("/api/shipping/goods", json={
        "project": "591К.3", "item_index": "КБ1", "customer": "Заказчик", "article": "591К.3 КБ1", "name": "Жгут КБ1",
        "ordered": "5", "unit": "шт",
    }).json()["good"]
    plain = client.post(f"/api/shipping/goods/{plain['id']}/accept", json={"version": plain["version"], "quantity": "5"}).json()["good"]
    shipment = _shipment(client, [{"good_id": plain["id"], "quantity": "5"}])

    # «КБ1 591К.3 КБ1» не повторяется — в «Чертежный номер/Артикул» только «КБ1».
    assert [row["part"] for row in _print(client, shipment["id"], "pl")[0]["rows"]] == ["КБ1"]
    assert [row["part"] for row in _print(client, shipment["id"], "dn")[0]["rows"]] == ["КБ1"]


def test_person_short_is_surname_and_initials_from_the_employee_card():
    assert person_short("Гусев Алексей Андреевич") == "Гусев А.А."
    assert person_short("  Безменов   никита ") == "Безменов Н."
    assert person_short("Гусев А.А.") == "Гусев А.А."
    assert person_short("Гусев А. А.") == "Гусев А.А."
    assert person_short("Администратор") == "Администратор"
    # Не человек или имени в карточке нет — пустая строка под подпись.
    assert person_short("") == ""
    assert person_short("store@example.test") == ""
    assert person_short("API: склад") == ""
    assert packer_short(None) == ""
    assert packer_short((UUID(int=0), "ERP Integration")) == ""
    assert packer_short((STOREKEEPER.id, STOREKEEPER.name)) == "Гусев А.А."


def test_signature_follows_whoever_packed_last_not_the_manager(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="5")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "5"}], no_docs=True)
    one_box = [{"items": [{"item_index": "КБ1", "quantity": "5"}]}]
    with _acting(STOREKEEPER, datetime(2026, 9, 22, 10, 0)):
        shipment = _lay_out(client, shipment, one_box)
    # «Перевыпустить» другим кладовщиком позже — подпись его.
    with _acting(OTHER_STOREKEEPER, datetime(2026, 9, 22, 11, 0)):
        _lay_out(client, shipment, one_box)

    forms = _print(client, shipment["id"], "pl") + _print(client, shipment["id"], "dn")

    assert [form["packer"] for form in forms] == ["Петров П.", "Петров П."]


def test_signature_is_never_a_manager_or_full_access_user(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="5")
    # Менеджер («Администратор» в журнале) и не-кладовщик по карточке: подписи нет — клетка для руки.
    manager = Actor(id=UUID("50000000-0000-0000-0000-000000000005"), name="Орлов Олег", department="Офис")
    _seed_user(manager, level="pm")
    with _acting(manager):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "5"}])

    forms = _print(client, shipment["id"], "pl") + _print(client, shipment["id"], "dn")

    assert [form["packer"] for form in forms] == ["", ""]


def test_signature_is_the_storekeeper_even_when_a_manager_laid_out_boxes_later(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="5")
    one_box = [{"items": [{"item_index": "КБ1", "quantity": "5"}]}]
    manager = Actor(id=UUID("50000000-0000-0000-0000-000000000005"), name="Орлов Олег", department="Офис")
    _seed_user(manager, level="superadmin")
    with _acting(STOREKEEPER, datetime(2026, 9, 22, 10, 0)):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "5"}], no_docs=True)
        shipment = _lay_out(client, shipment, one_box)
    with _acting(manager, datetime(2026, 9, 22, 11, 0)):
        _lay_out(client, shipment, one_box)

    assert _print(client, shipment["id"], "dn")[0]["packer"] == "Гусев А.А."


def test_signature_skips_a_storekeeper_who_is_no_longer_active_or_no_longer_a_storekeeper(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="5")
    one_box = [{"items": [{"item_index": "КБ1", "quantity": "5"}]}]
    with _acting(STOREKEEPER, datetime(2026, 9, 22, 10, 0)):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "5"}], no_docs=True)
        shipment = _lay_out(client, shipment, one_box)
    with _acting(OTHER_STOREKEEPER, datetime(2026, 9, 22, 11, 0)):
        _lay_out(client, shipment, one_box)
    uow = app.state.shipping_uow_factory()
    with uow:
        uow.session.get(IdentityUserRow, OTHER_STOREKEEPER.id).active = False
        uow.session.commit()
    assert _print(client, shipment["id"], "dn")[0]["packer"] == "Гусев А.А."

    with uow:
        uow.session.get(IdentityUserRow, STOREKEEPER.id).access_level = "pm"
        uow.session.commit()
    assert _print(client, shipment["id"], "dn")[0]["packer"] == ""


def test_storekeeper_signature_takes_the_newest_actor_who_is_a_storekeeper():
    first, second, third = (UUID(int=1), "Гусев Алексей"), (UUID(int=2), "Орлов Олег"), (UUID(int=3), "Петров Пётр")
    assert storekeeper_signature([second, first, third], {first[0], third[0]}) == "Гусев А."
    assert storekeeper_signature([second], {first[0]}) == ""
    assert storekeeper_signature([], {first[0]}) == ""
    assert storekeeper_signature([(UUID(int=0), "ERP Integration")], {UUID(int=0)}) == ""


def test_signature_is_whoever_formed_documents_when_boxes_were_never_laid_out(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    with _acting(STOREKEEPER):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    assert _print(client, shipment["id"], "dn")[0]["packer"] == "Гусев А.А."


def test_signature_stays_blank_when_packed_by_an_api_token(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    with _acting(Actor(id=UUID("40000000-0000-0000-0000-000000000004"), name="API: склад", department="Токен API")):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    forms = _print(client, shipment["id"], "pl") + _print(client, shipment["id"], "dn")

    assert [form["packer"] for form in forms] == ["", ""]


def test_box_labels_one_per_box_with_contents_weight_and_dims(shipping_api):
    client = shipping_api
    shipment = _packed_in_two_boxes(client)

    response = client.get(f"/api/shipping/shipments/{shipment['id']}/labels")

    assert response.status_code == 200, response.text
    common = {"shipper": SHIPPER, "consignee": "Получатель", "address": "Москва", "project": "591К.3", "pl_number": "PL591К.3-1"}
    assert response.json()["labels"] == [
        {**common, "place": "Упаковка 1/2", "items": [
            {"item_index": "КБ1", "quantity": "6", "unit": "шт"}, {"item_index": "КБ2", "quantity": "5", "unit": "шт"},
        ], "net": "11", "dims": "32x25x21"},
        # Не измеренный короб: вес и габариты пустые — дописать от руки.
        {**common, "place": "Упаковка 2/2", "items": [{"item_index": "КБ1", "quantity": "4", "unit": "шт"}], "net": "", "dims": ""},
    ]


def test_box_labels_without_laid_out_boxes_is_one_label_for_everything_packed(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="10")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "2.5"}])

    labels = client.get(f"/api/shipping/shipments/{shipment['id']}/labels").json()["labels"]

    assert [(label["place"], label["items"]) for label in labels] == [
        ("Упаковка 1/1", [{"item_index": "КБ1", "quantity": "2,5", "unit": "шт"}]),
    ]


def test_box_labels_need_documents_first(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], no_docs=True)

    response = client.get(f"/api/shipping/shipments/{shipment['id']}/labels")
    missing = client.get("/api/shipping/shipments/00000000-0000-0000-0000-0000000000ff/labels")

    assert response.status_code == 409 and "отгрузочные" in response.json()["detail"]
    assert missing.status_code == 404


def test_delivery_note_falls_back_to_the_project_number_and_prints_po_only_for_a_customer_order(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    dn = _print(client, shipment["id"], "dn")[0]

    # Карточки проекта нет: «Заказ №» — номер проекта, без одинокого «PO #»; спецификация необязательна — пусто
    # (владелец 28.10.2026).
    assert (dn["po"], dn["po_from_card"], dn["spec"]) == ("591К.3", False, "")


def test_delivery_note_card_values_win_over_the_project_number(shipping_api):
    client = shipping_api
    client.put("/api/shipping/project-addressee", json={"project": "591К.3", "po": " PO-7 ", "spec": "С-3"})
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    dn = _print(client, shipment["id"], "dn")[0]

    assert (dn["po"], dn["po_from_card"], dn["spec"]) == ("PO-7", True, "С-3")


def test_delivery_note_moves_the_track_out_of_the_carrier_and_prints_the_shipment_track(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    typed = _shipment(client, [{"good_id": good["id"], "quantity": "1"}], carrier="major-express 2071163030")
    tracked = _shipment(client, [{"good_id": good["id"], "quantity": "1"}], carrier="СДЭК")
    tracked = client.put(f"/api/shipping/shipments/{tracked['id']}/documents", json={
        "version": tracked["version"], "upd_type": "ЭДО", "upd_number": "УПД-1", "track_number": "1234567890",
    }).json()["shipment"]

    split = _print(client, typed["id"], "dn")[0]
    own = _print(client, tracked["id"], "dn")[0]

    assert (split["carrier"], split["track"]) == ("major-express", "2071163030")
    assert (own["carrier"], own["track"]) == ("СДЭК", "1234567890")


def test_split_carrier_only_takes_a_long_digit_group_at_the_end():
    assert split_carrier("major-express 2071163030", "") == ("major-express", "2071163030")
    assert split_carrier("СДЭК: 1234567", "") == ("СДЭК", "1234567")
    assert split_carrier("major-express 2071163030", "55") == ("major-express 2071163030", "55")
    assert split_carrier("ПЭК 12345", "") == ("ПЭК 12345", "")
    assert split_carrier("2071163030", "") == ("2071163030", "")
    assert split_carrier("", "") == ("", "")


def test_delivery_terms_are_printed_as_words_and_default_to_the_carrier_when_one_is_set(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="9")
    lines = [{"good_id": good["id"], "quantity": "1"}]
    pickup = _shipment(client, lines, terms="САМОВЫВОЗ")
    carrier = _shipment(client, lines, terms="ДОСТАВКА ТК")
    terms = [_print(client, one["id"], "dn")[0]["terms"] for one in (pickup, carrier)]
    assert terms == ["Самовывоз", "Доставка ТК"]

    # Уехавшая до правила 28.10.2026 без условий печатается как есть: с ТК — «Доставка ТК», без — пусто.
    item = good_domain().accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    for carrier_name, word in (("СДЭК", "Доставка ТК"), ("", "")):
        made = domain_shipment(item, terms=None, carrier=carrier_name)
        picked, _ = made.pick({item.id: item.remaining}, {item.id: item.grn or ""})
        old = replace(picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22)), status=ShipmentStatus.SHIPPED)
        assert build_delivery_note(old, "Заказчик").terms == word


def test_working_hours_left_in_the_note_go_to_the_hours_cell(shipping_api):
    client = shipping_api
    client.put("/api/shipping/project-addressee", json={"project": "591К.3", "note": "(пн-пт 10:00-17:00)"})
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    dn = _print(client, shipment["id"], "dn")[0]

    assert (dn["hours"], dn["note"]) == ("пн-пт 10:00-17:00", "")


def test_split_hours_leaves_real_notes_and_filled_hours_alone():
    assert split_hours("", "(пн-пт 10:00-17:00)") == ("пн-пт 10:00-17:00", "")
    assert split_hours("9-18", "10:00-17:00") == ("9-18", "")
    assert split_hours("", "Звонить за час. Пн-пт 10:00-17:00") == ("", "Звонить за час. Пн-пт 10:00-17:00")
    assert split_hours("", "Не кантовать") == ("", "Не кантовать")
    assert split_hours("", "") == ("", "")


def test_contact_phone_and_carrier_fall_back_to_the_project_card(shipping_api):
    client = shipping_api
    client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "contact": "Андрей", "phone": "+7 900 111-22-33", "carrier": "ПЭК",
    })
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], contact="", phone="", carrier="")

    dn = _print(client, shipment["id"], "dn")[0]

    assert (dn["contact"], dn["phone"], dn["carrier"]) == ("Андрей", "+7 900 111-22-33", "ПЭК")


def test_delivery_note_total_gross_is_the_sum_of_box_weights_once_every_box_has_one(shipping_api):
    client = shipping_api
    boxes = [
        {"gross": "11,6", "items": [{"item_index": "КБ1", "quantity": "6"}, {"item_index": "КБ2", "quantity": "5"}]},
        {"gross": "2,4", "items": [{"item_index": "КБ1", "quantity": "4"}]},
    ]
    first = _stocked(client, item_index="КБ1", accepted="10")
    second = _stocked(client, item_index="КБ2", accepted="5")
    shipment = _shipment(client, [{"good_id": first["id"], "quantity": "10"}, {"good_id": second["id"], "quantity": "5"}])
    with _acting(STOREKEEPER):
        _lay_out(client, shipment, boxes)

    assert _print(client, shipment["id"], "dn")[0]["gross"] == "14"
    assert [form["gross"] for form in _print(client, shipment["id"], "pl")] == ["11,6", "2,4"]


def _storekeeper_only(actor):
    return Principal(actor=actor, email="store@example.test", roles=frozenset({"storekeeper"}), permissions={"shipping_goods": "edit"})


def test_storekeeper_saves_the_track_number_and_the_manager_is_notified(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[authenticated_principal] = lambda: _storekeeper_only(STOREKEEPER)
    try:
        response = client.put(f"/api/shipping/shipments/{shipment['id']}/documents", json={
            "version": shipment["version"], "upd_type": "ЭДО", "upd_number": "12", "track_number": "2071163030",
        })
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)

    assert response.status_code == 200, response.text
    assert response.json()["shipment"]["track_number"] == "2071163030"
    uow = app.state.shipping_uow_factory()
    with uow:
        titles = [row.payload.get("title") for row in uow.session.query(OutboxMessageRow)]
    assert f"Трек по отправке {shipment['number']}" in titles


def test_documents_are_closed_to_a_user_without_shipping_sections(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=STOREKEEPER, email="x@example.test", roles=frozenset({"operator"}), permissions={"production": "edit"},
    )
    try:
        response = client.put(f"/api/shipping/shipments/{shipment['id']}/documents", json={
            "version": shipment["version"], "track_number": "1",
        })
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)

    assert response.status_code == 403
