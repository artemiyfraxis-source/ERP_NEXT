"""Packing list (форма 04.2) and delivery note (форма 04.1) as browser print sheets (`shipBuildDocs_`).

SUPPLYMAP fills Google Doc templates and keeps file links; here the sheet is built from the current
shipment every time, so «Перевыпустить» is simply printing again: numbers PL/DN stay, the content
follows the shipment (боксы, адресат, УПД).
"""

from __future__ import annotations

from datetime import date, datetime

from app.shared.print_sheet import PrintSheet

from .domain import (
    PackedItem, ProjectAddressee, Shipment, ShipmentBox, ShippingValidationError, UpdType,
    default_boxes, packed_by_index, total_gross,
)


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _plain(value) -> str:
    return format(value.normalize(), "f")


def _header(pairs: list[tuple[str, str]]) -> str:
    return " · ".join(f"{label}: {value or '—'}" for label, value in pairs)


def _prepare(shipment: Shipment) -> tuple[list[PackedItem], tuple[ShipmentBox, ...]]:
    packed = packed_by_index(shipment.lines)
    if not packed:
        raise ShippingValidationError("Нечего печатать: в отправке ничего не упаковано.")
    if not shipment.pl_number or not shipment.dn_number:
        raise ShippingValidationError("Сначала сформируйте отгрузочные документы")
    return packed, shipment.boxes or default_boxes(packed)


def build_packing_lists(shipment: Shipment) -> list[PrintSheet]:
    """PL на каждый короб (владелец 15.09.2026): у всех коробов один № PL, место — «BXN i / N»."""
    packed, boxes = _prepare(shipment)
    by_index = {item.item_index: item for item in packed}
    sheets = []
    for box in boxes:
        rows = []
        for entry in box.items:
            item = by_index.get(entry.item_index)
            rows.append((
                entry.item_index, item.article if item else "", item.name if item else "",
                _plain(entry.quantity), item.unit if item else "шт", item.grn if item else "",
            ))
        sheets.append(PrintSheet(
            title="Упаковочная ведомость / Packing list",
            subtitle=_header([
                ("№ документа", shipment.pl_number), ("Отправка", shipment.number),
                ("Дата упаковки", _day(shipment.packed_at)),
                ("Упаковка №", f"BXN {box.place} / {len(boxes)}"), ("Вид упаковки", box.kind),
                ("Габариты (ШхДхВ), см", box.dims), ("Вес изделий (нетто), кг", box.net),
            ]),
            columns=("#", "Чертёжный номер / Артикул", "Описание", "Кол-во", "Ед. изм.", "ГРН / Номер этикетки"),
            rows=tuple(rows),
            note=(
                "Вес короба может различаться на ________ грамм ввиду разницы расхода упаковочных материалов. "
                + _header([
                    ("Вес (брутто), кг", box.gross), ("Номер пломбы", box.seal),
                    ("Дата отгрузки", _day(shipment.shipped_at)),
                ])
            ),
            sign=(
                f"УПАКОВАЛ / Storekeeper: {shipment.manager}  ________\n"
                "ПРОВЕРИЛ / Checked: ________\n"
                "Форма / Form 04.2 · Rev.4 · 25.05.2026"
            ),
        ))
    return sheets


def _ticks(upd_type: UpdType | None) -> tuple[str, str]:
    """`shipDnTicks_`: отметка по типу УПД — тексты как в шаблоне."""
    edo = "☑" if upd_type == UpdType.EDO else "☐"
    paper = "☑" if upd_type == UpdType.PAPER else "☐"
    place = "1" if upd_type == UpdType.PAPER else ""
    return f"{edo} УПД передана посредством ЭДО", f"{paper} УПД в бумажном виде находится в BXN {place}".rstrip()


def build_delivery_note(shipment: Shipment, project: ProjectAddressee | None, now: datetime) -> PrintSheet:
    """DN одна на отправку: строка на каждую КБ в каждом коробе, с № PL и местом (`shipDnRows_`)."""
    packed, boxes = _prepare(shipment)
    by_index = {item.item_index: item for item in packed}
    card = project or ProjectAddressee(project=shipment.lines[0].project)
    rows = []
    for box in boxes:
        for entry in box.items:
            item = by_index.get(entry.item_index)
            rows.append((
                entry.item_index, item.article if item else "", item.name if item else "",
                _plain(entry.quantity), item.unit if item else "шт", shipment.pl_number, f"BXN {box.place}",
            ))
    edo, paper = _ticks(shipment.upd_type)
    return PrintSheet(
        title="Отгрузочная ведомость / Delivery note",
        subtitle=_header([
            ("№ документа", shipment.dn_number), ("Отправка", shipment.number), ("Дата", now.strftime("%d.%m.%Y")),
            ("Заказчик", shipment.customer), ("Заказ №", card.po), ("Договор №", card.contract),
            # № спецификации по умолчанию — номер проекта (так во всех отгрузочных владельца).
            ("Спецификация №", card.spec or shipment.lines[0].project), ("Счёт №", card.invoice),
            ("Условия поставки", shipment.terms.value if shipment.terms else ""), ("Оплачивает", card.pays),
            ("Получатель", shipment.receiver or shipment.customer), ("Адрес заказчика", card.customer_address),
            ("Адрес доставки", shipment.shipping_point), ("Время работы", card.hours),
            ("Контактное лицо", shipment.contact), ("Телефон", shipment.phone),
            ("Перевозчик / ТК", shipment.carrier), ("№ ТТН / трек", shipment.track_number),
            ("Примечания для ТК", shipment.tk_note),
        ]),
        columns=("#", "Чертёжный номер / Артикул", "Описание", "Кол-во", "Ед. изм.", "Упаковочная ведомость №", "Номер места"),
        rows=tuple(rows),
        note=(
            f"{edo}. {paper}. Упаковочные ведомости (PL) находятся внутри коробов. "
            "Соотносить PL с номером места BXN — по п. 2 «Описание». "
            + _header([
                ("Общее кол-во упаковок, шт", str(len(boxes))), ("Общий вес (брутто), кг", total_gross(boxes)),
                ("Номер УПД / ТТН", shipment.upd_number), ("Дата отгрузки", _day(shipment.shipped_at)),
            ])
        ),
        sign=(
            "ОТПУСТИЛ / Storekeeper: ________\n"
            "ПОЛУЧИЛ / Received by: ________\n"
            "Форма / Form 04.1 · Rev.4 · 24.05.2026"
        ),
    )
