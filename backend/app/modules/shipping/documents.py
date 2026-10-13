"""Упаковочная ведомость (PL) и отгрузочная ведомость (DN) — по образцу владельца (13.10.2026).

Образец: лист «ТЕСТ» таблицы «учет ТМЦ» и выпущенные по нему `PL-DN_№228К.20.2-*.docx` в папке
проекта. Шапка — «подпись | значение | реквизиты ЗЭП ФРАКСИС», таблица «GRN | № КБ | ОПИСАНИЕ |
кол-во | Ед. изм.», пустые строки для дописки от руки, «Штамп / подпись кладовщика». Лист строится
из текущей отправки при каждой печати, поэтому «Перевыпустить» — это просто печать заново: номера
PL/DN остаются, содержимое следует за отправкой (короба, УПД).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .domain import (
    PackedItem, Shipment, ShipmentBox, ShippingValidationError, UpdType, default_boxes, packed_by_index,
)

# Реквизиты отправителя — как в образце; в ERP их больше нигде нет.
SHIPPER = (
    'ООО "ЗЭП ФРАКСИС"',
    "ИНН 5040191181 / КПП 504001001    ОГРН 1245000065100",
    "Адрес: 140188, М.О., г. Жуковский, ул. Молодежная, д. 29",
    "Сайт: https://fraxis.ru    Электронный адрес: order@fraxis.ru",
    "Телефон для связи: +7 (495) 085-14-79 доб. 1    +7 (910) 084-54-25",
)
COLUMNS = ("GRN", "№ КБ", "ОПИСАНИЕ", "кол-во", "Ед. изм.")
# В образце под позиции 16 строк: занятые + пустые для дописки от руки.
TABLE_ROWS = 16
SIGN_LABEL = "Штамп / подпись кладовщика:"


@dataclass(frozen=True)
class ShippingForm:
    """Один лист PL или DN: шапка (подпись, значение, строка реквизитов), таблица позиций, подпись.

    Пустое значение в шапке — поле, которое в ERP не заполнено: на листе остаётся клетка для руки.
    """

    kind: str
    title: str
    head: tuple[tuple[str, str, str], ...]
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    blank_rows: int
    sign_label: str
    sign_name: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "title": self.title,
            "head": [{"label": label, "value": value, "side": side} for label, value, side in self.head],
            "columns": list(self.columns), "rows": [list(row) for row in self.rows],
            "blank_rows": self.blank_rows, "sign_label": self.sign_label, "sign_name": self.sign_name,
        }


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _qty(value) -> str:
    return format(value.normalize(), "f").replace(".", ",")


def _with_shipper(pairs: list[tuple[str, str]], skip: int = 0) -> tuple[tuple[str, str, str], ...]:
    """Реквизиты отправителя идут справа сверху вниз, начиная со строки `skip` (в DN — после «Заказчик»)."""
    side = [""] * skip + list(SHIPPER)
    side += [""] * (len(pairs) - len(side))
    return tuple((label, value, side[number]) for number, (label, value) in enumerate(pairs))


def _row(item: PackedItem | None, item_index: str, quantity) -> tuple[str, ...]:
    return (
        item.grn if item else "", item_index, item.name if item else "", _qty(quantity),
        item.unit if item else "шт",
    )


def _blanks(rows: list) -> int:
    return max(TABLE_ROWS - len(rows), 0)


def _prepare(shipment: Shipment) -> tuple[list[PackedItem], tuple[ShipmentBox, ...]]:
    packed = packed_by_index(shipment.lines)
    if not packed:
        raise ShippingValidationError("Нечего печатать: в отправке ничего не упаковано.")
    if not shipment.pl_number or not shipment.dn_number:
        raise ShippingValidationError("Сначала сформируйте отгрузочные документы")
    return packed, shipment.boxes or default_boxes(packed)


def build_packing_lists(shipment: Shipment) -> list[ShippingForm]:
    """PL на каждый короб (владелец 15.09.2026): у всех коробов один № PL, место — «BXN i/N»."""
    packed, boxes = _prepare(shipment)
    by_index = {item.item_index: item for item in packed}
    forms = []
    for box in boxes:
        rows = [_row(by_index.get(entry.item_index), entry.item_index, entry.quantity) for entry in box.items]
        forms.append(ShippingForm(
            kind="pl", title="УПАКОВОЧНАЯ ВЕДОМОСТЬ",
            head=_with_shipper([
                ("Номер документа", shipment.pl_number),
                ("Упаковка №", f"BXN {box.place}/{len(boxes)}"),
                ("Дата упаковки:", _day(shipment.packed_at)),
                ("Габариты упаковки (cm)", box.dims),
                ("Вес (нетто), кг", box.net),
            ]),
            columns=COLUMNS, rows=tuple(rows), blank_rows=_blanks(rows),
            sign_label=SIGN_LABEL, sign_name=shipment.manager,
        ))
    return forms


def _upd_number(shipment: Shipment) -> str:
    """Номера УПД ещё нет, а передаётся она через ЭДО — в образце так и пишут «ЭДО»."""
    if shipment.upd_number:
        return shipment.upd_number
    return "ЭДО" if shipment.upd_type == UpdType.EDO else ""


def build_delivery_note(shipment: Shipment, customer: str) -> ShippingForm:
    """DN одна на отправку: строка на каждую КБ (сумма по всем коробам), число мест — число коробов."""
    packed, boxes = _prepare(shipment)
    rows = [_row(item, item.item_index, item.quantity) for item in packed]
    return ShippingForm(
        kind="dn", title="ОТГРУЗОЧНАЯ ВЕДОМОСТЬ",
        head=_with_shipper([
            ("Заказчик:", customer),
            ("Номер документа:", shipment.dn_number),
            ("Упаковочная ведомость:", shipment.pl_number),
            # До отгрузки DN печатают вместе с PL — в образце стоит дата упаковки.
            ("Дата отгрузки:", _day(shipment.shipped_at or shipment.packed_at)),
            ("УПД №:", _upd_number(shipment)),
            ("дата УПД:", _day(shipment.upd_date)),
            ("Кол-во упаковок:", str(len(boxes))),
        ], skip=1),
        columns=COLUMNS, rows=tuple(rows), blank_rows=_blanks(rows),
        sign_label=SIGN_LABEL, sign_name=shipment.manager,
    )
