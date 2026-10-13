"""PL и DN по образцу владельца (лист «ТЕСТ» «учет ТМЦ», PL-DN_№228К.20.2-2.docx): структура листа целиком.

Подпись кладовщика — фамилия того, кто упаковал («Гусев А.А.»), не менеджер; этикетки коробов — по одной на место.
"""

from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from app.main import app
from app.modules.shipping.documents import packer_short, person_short
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture
from tests.shipping.test_documents_api import _shipment, _stocked

SHIPPER = [
    'ООО "ЗЭП ФРАКСИС"',
    "ИНН 5040191181 / КПП 504001001    ОГРН 1245000065100",
    "Адрес: 140188, М.О., г. Жуковский, ул. Молодежная, д. 29",
    "Сайт: https://fraxis.ru    Электронный адрес: order@fraxis.ru",
    "Телефон для связи: +7 (495) 085-14-79 доб. 1    +7 (910) 084-54-25",
]
COLUMNS = ["GRN", "№ КБ", "ОПИСАНИЕ", "кол-во", "Ед. изм."]
STOREKEEPER = Actor(id=UUID("20000000-0000-0000-0000-000000000002"), name="Гусев Алексей Андреевич", department="Склад")
BOXES = [
    {"dims": "32x25x21", "net": "11", "gross": "11,6", "items": [{"item_index": "КБ1", "quantity": "6"}, {"item_index": "КБ2", "quantity": "5"}]},
    {"items": [{"item_index": "КБ1", "quantity": "4"}]},
]


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


def test_packing_list_is_one_sheet_per_box_laid_out_as_the_sample(shipping_api):
    client = shipping_api
    shipment = _packed_in_two_boxes(client)
    forms = _print(client, shipment["id"], "pl")
    storekeeper = "Гусев А.А."
    assert shipment["manager"] == "Администратор"

    assert forms == [
        {
            "kind": "pl", "title": "УПАКОВОЧНАЯ ВЕДОМОСТЬ",
            "head": [
                {"label": "Номер документа", "value": "PL591К.3-1", "side": SHIPPER[0]},
                {"label": "Упаковка №", "value": "BXN 1/2", "side": SHIPPER[1]},
                {"label": "Дата упаковки:", "value": "22.09.2026", "side": SHIPPER[2]},
                {"label": "Габариты упаковки (cm)", "value": "32x25x21", "side": SHIPPER[3]},
                {"label": "Вес (нетто), кг", "value": "11", "side": SHIPPER[4]},
            ],
            "columns": COLUMNS,
            "rows": [["ГП.26.0000001", "КБ1", "Жгут КБ1", "6", "шт"], ["ГП.26.0000002", "КБ2", "Жгут КБ2", "5", "шт"]],
            "blank_rows": 14, "sign_label": "Штамп / подпись кладовщика:", "sign_name": storekeeper,
        },
        {
            "kind": "pl", "title": "УПАКОВОЧНАЯ ВЕДОМОСТЬ",
            "head": [
                {"label": "Номер документа", "value": "PL591К.3-1", "side": SHIPPER[0]},
                {"label": "Упаковка №", "value": "BXN 2/2", "side": SHIPPER[1]},
                {"label": "Дата упаковки:", "value": "22.09.2026", "side": SHIPPER[2]},
                # Короб без габаритов и веса: клетки остаются пустыми — дописать от руки.
                {"label": "Габариты упаковки (cm)", "value": "", "side": SHIPPER[3]},
                {"label": "Вес (нетто), кг", "value": "", "side": SHIPPER[4]},
            ],
            "columns": COLUMNS,
            "rows": [["ГП.26.0000001", "КБ1", "Жгут КБ1", "4", "шт"]],
            "blank_rows": 15, "sign_label": "Штамп / подпись кладовщика:", "sign_name": storekeeper,
        },
    ]


def test_delivery_note_sums_each_kb_over_boxes_and_counts_places(shipping_api):
    client = shipping_api
    shipment = _packed_in_two_boxes(client)
    forms = _print(client, shipment["id"], "dn")

    assert forms == [{
        "kind": "dn", "title": "ОТГРУЗОЧНАЯ ВЕДОМОСТЬ",
        "head": [
            {"label": "Заказчик:", "value": "Заказчик", "side": ""},
            {"label": "Номер документа:", "value": "DN591К.3-1", "side": SHIPPER[0]},
            {"label": "Упаковочная ведомость:", "value": "PL591К.3-1", "side": SHIPPER[1]},
            # Ещё не отгружена — дата упаковки, как в образце (DN печатают вместе с PL).
            {"label": "Дата отгрузки:", "value": "22.09.2026", "side": SHIPPER[2]},
            # Номера УПД нет, тип — ЭДО: в образце так и пишут.
            {"label": "УПД №:", "value": "ЭДО", "side": SHIPPER[3]},
            {"label": "дата УПД:", "value": "", "side": SHIPPER[4]},
            {"label": "Кол-во упаковок:", "value": "2", "side": ""},
        ],
        "columns": COLUMNS,
        "rows": [["ГП.26.0000001", "КБ1", "Жгут КБ1", "10", "шт"], ["ГП.26.0000002", "КБ2", "Жгут КБ2", "5", "шт"]],
        "blank_rows": 14, "sign_label": "Штамп / подпись кладовщика:", "sign_name": "Гусев А.А.",
    }]


def test_delivery_note_takes_upd_number_and_date_and_the_shipping_day(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="10")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "2.5"}], upd_type="БУМАГА",
                         upd_number="УПД-17", upd_date="2026-09-21", upd_url="https://files.example/upd.pdf")
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    assert shipped.status_code == 200, shipped.text

    head = {row["label"]: row["value"] for row in _print(client, shipment["id"], "dn")[0]["head"]}
    pl = _print(client, shipment["id"], "pl")

    assert (head["УПД №:"], head["дата УПД:"], head["Дата отгрузки:"]) == ("УПД-17", "21.09.2026", "22.09.2026")
    assert head["Кол-во упаковок:"] == "1"
    assert pl[0]["rows"] == [["ГП.26.0000001", "КБ1", "Жгут КБ1", "2,5", "шт"]]


def test_bumaga_without_upd_number_leaves_the_cell_blank(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], upd_type="БУМАГА")

    head = {row["label"]: row["value"] for row in _print(client, shipment["id"], "dn")[0]["head"]}

    assert head["УПД №:"] == ""


def test_many_positions_need_no_blank_rows(shipping_api):
    client = shipping_api
    lines = []
    for number in range(1, 18):
        good = _stocked(client, item_index=f"КБ{number}", accepted="1")
        lines.append({"good_id": good["id"], "quantity": "1"})
    shipment = _shipment(client, lines)

    dn = _print(client, shipment["id"], "dn")[0]

    assert len(dn["rows"]) == 17 and dn["blank_rows"] == 0


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
    other = Actor(id=UUID("30000000-0000-0000-0000-000000000003"), name="Петров Пётр", department="Склад")
    with _acting(other, datetime(2026, 9, 22, 11, 0)):
        _lay_out(client, shipment, one_box)

    forms = _print(client, shipment["id"], "pl") + _print(client, shipment["id"], "dn")

    assert [form["sign_name"] for form in forms] == ["Петров П.", "Петров П."]


def test_signature_is_whoever_formed_documents_when_boxes_were_never_laid_out(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    with _acting(STOREKEEPER):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    assert _print(client, shipment["id"], "dn")[0]["sign_name"] == "Гусев А.А."


def test_signature_stays_blank_when_packed_by_an_api_token(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="3")
    with _acting(Actor(id=UUID("40000000-0000-0000-0000-000000000004"), name="API: склад", department="Токен API")):
        shipment = _shipment(client, [{"good_id": good["id"], "quantity": "3"}])

    forms = _print(client, shipment["id"], "pl") + _print(client, shipment["id"], "dn")

    assert [form["sign_name"] for form in forms] == ["", ""]


def test_box_labels_one_per_box_with_contents_weight_and_dims(shipping_api):
    client = shipping_api
    shipment = _packed_in_two_boxes(client)

    response = client.get(f"/api/shipping/shipments/{shipment['id']}/labels")

    assert response.status_code == 200, response.text
    common = {"shipper": SHIPPER[0], "consignee": "Получатель", "address": "Москва", "project": "591К.3", "pl_number": "PL591К.3-1"}
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
