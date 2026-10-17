"""Упаковочная ведомость (PL, форма 04.2) и отгрузочная ведомость (DN, форма 04.1) — по образцам владельца 15.10.2026.

Образцы: `PL533К.10-1.docx` и `DN533К.10-1.docx` (форма 04.2 / Rev.4 / 25.05.2026, 04.1 / Rev.4 / 24.05.2026). Сервер
отдаёт значения полей формы, раскладку листа (логотип, реквизиты, разделы «1. … 4.», двуязычные подписи) рисует
`frontend/src/shippingPrint.ts`. Лист строится из текущей отправки при каждой печати, поэтому «Перевыпустить» — это
просто печать заново: номера PL/DN остаются, содержимое следует за отправкой (короба, УПД).

Реквизиты DN «Заказ №», «Номер договора», «Номер спецификации», «Номер счета», «Оплачивает», «Адрес заказчика»,
«Время работы» — из карточки проекта (`ProjectAddressee`). Пустое поле — клетка для руки, ERP не пишет туда заглушек.
«Штамп / подпись кладовщика» — фамилия того, кто упаковал, «Фамилия И.О.» как «Гусев А.А.» в образце (владелец
13.10.2026); упаковщика нет — пустая строка под подпись. Этикетки коробов — по одной на место: образца у владельца
нет, поэтому раскладка своя — отправитель, получатель, проект и PL, «Упаковка N/M», КБ с количеством, вес нетто, габариты.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from uuid import UUID

from .domain import (
    PackedItem, ProjectAddressee, Shipment, ShipmentBox, ShippingValidationError, UpdType, default_boxes, packed_by_index,
    total_gross,
)

SHIPPER_SHORT = 'ООО "ЗЭП ФРАКСИС"'
# Не люди: системные действия (нулевой id) и токены API — подписи кладовщика из них нет.
_NOBODY = UUID(int=0)
_NOT_A_PERSON = ("API:",)
# «PL533К.10-1» → «PL | 533К.10 | - | 1»: номер документа в образце — четыре клетки.
_NUMBER = re.compile(r"^(PL|DN)(.+)-(\d+)$")


def person_short(name: str) -> str:
    """«Гусев Алексей Андреевич» → «Гусев А.А.»: фамилия (первое слово карточки) и инициалы остальных слов.

    Уже сокращённое («Гусев А.А.») остаётся как есть; одно слово — без инициалов; e-mail вместо имени — пусто.
    """
    text = " ".join(str(name or "").split())
    if not text or "@" in text or text.startswith(_NOT_A_PERSON):
        return ""
    surname, _, rest = text.partition(" ")
    initials = "".join(part[0].upper() + "." for part in re.split(r"[.\s]+", rest) if part)
    return f"{surname} {initials}" if initials else surname


def packer_short(packer: tuple[UUID, str] | None) -> str:
    """Подпись кладовщика: кто упаковал (`ShipmentRepository.packer`), «Фамилия И.О.»; никого — пусто."""
    if packer is None or packer[0] == _NOBODY:
        return ""
    return person_short(packer[1])


@dataclass(frozen=True)
class FormRow:
    """Строка раздела «Описание»: № КБ с артикулом («КБ1 FRX.2600204»), наименование, кол-во, ед.; у PL — номер
    этикетки (GRN партии ГП), у DN — № упаковочной ведомости и места («BXN 1, 2»)."""

    part: str
    name: str
    quantity: str
    unit: str
    batch: str = ""
    pl: str = ""
    boxes: str = ""


@dataclass(frozen=True)
class PackingList:
    """PL одного короба (форма 04.2): номер, место «BXN i / N», вид, габариты, нетто/брутто, пломба, позиции, подписи."""

    number: tuple[str, ...]
    place: str
    places: str
    package: str
    dims: str
    net: str
    gross: str
    seal: str
    rows: tuple[FormRow, ...]
    packed_at: str
    shipped_at: str
    packer: str

    def to_dict(self) -> dict:
        return {"kind": "pl", "title": "".join(self.number), **asdict(self)}


@dataclass(frozen=True)
class DeliveryNote:
    """DN отправки (форма 04.1): шапка с реквизитами заказа, «Данные о поставке», позиции по КБ, УПД, итоги."""

    number: tuple[str, ...]
    date: str
    customer: str
    po: str
    contract: str
    spec: str
    invoice: str
    terms: str
    pays: str
    customer_address: str
    shipping_address: str
    hours: str
    contact: str
    phone: str
    carrier: str
    track: str
    tk_note: str
    rows: tuple[FormRow, ...]
    upd_edo: bool
    upd_paper: bool
    paper_box: str
    note: str
    places: str
    gross: str
    upd: str
    shipped_at: str
    packer: str

    def to_dict(self) -> dict:
        return {"kind": "dn", "title": "".join(self.number), **asdict(self)}


def _day(value: date | None) -> str:
    """Даты в формах — как в образце: «18.09.26»."""
    return value.strftime("%d.%m.%y") if value else ""


def _qty(value) -> str:
    return format(value.normalize(), "f").replace(".", ",")


def _number(text: str) -> tuple[str, ...]:
    match = _NUMBER.match(text)
    return (match.group(1), match.group(2), "-", match.group(3)) if match else (text, "", "", "")


def _part(item: PackedItem | None, item_index: str, projects: tuple[str, ...] = ()) -> str:
    """«КБ1 FRX.2600204»; артикул, который сам — «КБ1» или «проект + КБ» («533К.11КБ1»), не повторяется: «КБ1»."""
    article = item.article if item else ""
    fold = lambda text: "".join(text.split()).casefold()  # noqa: E731
    if fold(article) in {fold(item_index), *(fold(project + item_index) for project in projects)}:
        article = ""
    return " ".join(part for part in (item_index, article) if part)


def _projects(shipment: Shipment) -> tuple[str, ...]:
    return tuple(dict.fromkeys(line.project for line in shipment.lines if line.project))


def _prepare(shipment: Shipment) -> tuple[list[PackedItem], tuple[ShipmentBox, ...]]:
    packed = packed_by_index(shipment.lines)
    if not packed:
        raise ShippingValidationError("Нечего печатать: в отправке ничего не упаковано.")
    if not shipment.pl_number or not shipment.dn_number:
        raise ShippingValidationError("Сначала сформируйте отгрузочные документы")
    return packed, shipment.boxes or default_boxes(packed)


def build_packing_lists(shipment: Shipment, packer: str = "") -> list[PackingList]:
    """PL на каждый короб (владелец 15.09.2026): у всех коробов один № PL, место — «BXN i / N»."""
    packed, boxes = _prepare(shipment)
    by_index = {item.item_index: item for item in packed}
    forms = []
    for box in boxes:
        rows = []
        for entry in box.items:
            item = by_index.get(entry.item_index)
            rows.append(FormRow(
                part=_part(item, entry.item_index, _projects(shipment)), name=item.name if item else "", quantity=_qty(entry.quantity),
                unit=(item.unit if item else "") or "шт", batch=(item.grn if item else "") or "",
            ))
        forms.append(PackingList(
            number=_number(shipment.pl_number), place=str(box.place), places=str(len(boxes)), package=box.kind,
            dims=box.dims, net=box.net, gross=box.gross, seal=box.seal, rows=tuple(rows),
            # До отгрузки PL печатают при упаковке — в «Shipping Date» стоит дата упаковки, как в образце.
            packed_at=_day(shipment.packed_at), shipped_at=_day(shipment.shipped_at or shipment.packed_at), packer=packer,
        ))
    return forms


def _upd(shipment: Shipment) -> str:
    """«Номер УПД/ТТН»: «385 от 18.09.26»; без номера — пусто (дописать), отправка без УПД — «без УПД»."""
    if shipment.upd_number:
        return f"{shipment.upd_number} от {_day(shipment.upd_date)}" if shipment.upd_date else shipment.upd_number
    return "без УПД" if shipment.upd_type == UpdType.NONE else ""


def _places(numbers: list[int]) -> str:
    return f"BXN {', '.join(str(number) for number in numbers)}" if numbers else ""


def build_delivery_note(
    shipment: Shipment, customer: str, packer: str = "", card: ProjectAddressee | None = None,
) -> DeliveryNote:
    """DN одна на отправку: строка на каждую КБ (сумма по всем коробам) с местами, где она лежит; итог — число мест."""
    packed, boxes = _prepare(shipment)
    rows = tuple(FormRow(
        part=_part(item, item.item_index, _projects(shipment)), name=item.name, quantity=_qty(item.quantity), unit=item.unit or "шт",
        pl=shipment.pl_number,
        boxes=_places([box.place for box in boxes if any(entry.item_index == item.item_index for entry in box.items)]),
    ) for item in packed)
    # Получатель, отличный от заказчика, — в адресе доставки: ТК везёт ему.
    receiver = shipment.receiver if shipment.receiver and shipment.receiver != customer else ""
    return DeliveryNote(
        number=_number(shipment.dn_number),
        # До отгрузки DN печатают вместе с PL — в образце стоит дата упаковки.
        date=_day(shipment.shipped_at or shipment.packed_at),
        customer=customer, po=card.po if card else "", contract=card.contract if card else "",
        spec=card.spec if card else "", invoice=card.invoice if card else "",
        terms=shipment.terms.value if shipment.terms else "", pays=card.pays if card else "",
        customer_address=card.customer_address if card else "",
        shipping_address=", ".join(part for part in (receiver, shipment.shipping_point) if part),
        hours=card.hours if card else "", contact=shipment.contact, phone=shipment.phone, carrier=shipment.carrier,
        track=shipment.track_number, tk_note=shipment.tk_note, rows=rows,
        upd_edo=shipment.upd_type == UpdType.EDO, upd_paper=shipment.upd_type == UpdType.PAPER,
        # Бумажная УПД едет в первом коробе.
        paper_box=_places([boxes[0].place]), note=shipment.note, places=str(len(boxes)), gross=total_gross(boxes),
        upd=_upd(shipment), shipped_at=_day(shipment.shipped_at or shipment.packed_at), packer=packer,
    )


@dataclass(frozen=True)
class BoxLabel:
    """Этикетка одного места: что и кому, № PL и место «Упаковка N/M», состав короба, вес нетто и габариты.

    Пустой вес или габариты — короб не измерен: на этикетке остаётся строка дописать от руки.
    """

    shipper: str
    consignee: str
    address: str
    project: str
    pl_number: str
    place: str
    items: tuple[tuple[str, str, str], ...]
    net: str
    dims: str

    def to_dict(self) -> dict:
        return {
            "shipper": self.shipper, "consignee": self.consignee, "address": self.address,
            "project": self.project, "pl_number": self.pl_number, "place": self.place,
            "items": [{"item_index": index, "quantity": quantity, "unit": unit} for index, quantity, unit in self.items],
            "net": self.net, "dims": self.dims,
        }


def build_box_labels(shipment: Shipment) -> list[BoxLabel]:
    """Этикетка на каждый короб (как PL): у всех один № PL, место — «Упаковка i/N»."""
    packed, boxes = _prepare(shipment)
    units = {item.item_index: item.unit for item in packed}
    projects = list(dict.fromkeys(line.project for line in shipment.lines if line.picked > 0 and line.project))
    return [
        BoxLabel(
            shipper=SHIPPER_SHORT, consignee=shipment.receiver, address=shipment.shipping_point,
            project=", ".join(projects), pl_number=shipment.pl_number, place=f"Упаковка {box.place}/{len(boxes)}",
            items=tuple((entry.item_index, _qty(entry.quantity), units.get(entry.item_index) or "шт") for entry in box.items),
            net=box.net, dims=box.dims,
        )
        for box in boxes
    ]
