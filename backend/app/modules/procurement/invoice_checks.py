"""What the buyer should look at once an invoice file is read (owner Витя, 26.10.2026: «полноценный разборщик всех данных,
как у нас в roller_buyer»): the invoice's seller against the order's supplier, its total against the order lines' sums,
its positions against the order lines. Pure rules over the reading (`app.shared.invoice_reading`) and the order lines;
nothing is changed — the buyer reads the checks next to the form and decides.

The supplier's INN lives in purchasing's supplier directory, not in procurement: it comes through the `SupplierInns`
port (`app.workflows.procurement_invoice_registry`). Without it the seller is compared by name.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.shared.invoice_reading import InvoiceReading

from .document_items import DocumentItem
from .document_match import OrderLineCandidate, match_document_items
from .domain import ProcurementLine

OK, WARNING, INFO = "ok", "warning", "info"
# Kopecks: a rouble of rounding between the invoice and the lines' sums is the same total.
TOTAL_TOLERANCE = 100
_LEGAL_FORMS = re.compile(
    r"\b(?:ооо|оао|зао|пао|нао|ао|ип|тоо|общество с ограниченной ответственностью"
    r"|(?:непубличное |закрытое |публичное |открытое )?акционерное общество|индивидуальный предприниматель)\b"
)


@dataclass(frozen=True)
class InvoiceCheck:
    kind: str  # seller | total | lines
    level: str  # ok | warning | info
    text: str

    def as_json(self) -> dict:
        return {"kind": self.kind, "level": self.level, "text": self.text}


class SupplierInns(Protocol):
    """Purchasing's supplier directory as procurement needs it: a supplier's INN by name, a supplier's name by INN."""

    def inn_of(self, supplier: str) -> str | None: ...
    def supplier_of_inn(self, inn: str) -> str | None: ...


def rub(kop: int) -> str:
    """Kopecks → «1 234,50 ₽»."""
    sign = "−" if kop < 0 else ""
    rubles, rest = divmod(abs(kop), 100)
    return f"{sign}{rubles:,}".replace(",", " ") + f",{rest:02d} ₽"


def org_key(name: str | None) -> str:
    """«ООО "Чип и Дип"» → «чипидип»: legal form, quotes, spaces and case dropped, «ё» as «е»."""
    key = str(name or "").lower().replace("ё", "е")
    key = _LEGAL_FORMS.sub(" ", key)
    return re.sub(r"[^0-9a-zа-я]", "", key)


def same_org(a: str | None, b: str | None) -> bool:
    ka, kb = org_key(a), org_key(b)
    return bool(ka and kb and (ka == kb or (min(len(ka), len(kb)) >= 4 and (ka in kb or kb in ka))))


def _seller(reading: InvoiceReading, supplier: str, suppliers: SupplierInns | None) -> InvoiceCheck | None:
    header = reading.header
    inn, seller = header.seller_inn, header.seller_name or "продавец"
    if not supplier.strip():
        return InvoiceCheck("seller", INFO, f"В заказе поставщик не указан; в счёте — {seller}"
                            + (f", ИНН {inn}" if inn else ""))
    if not inn:
        return InvoiceCheck("seller", INFO, f"ИНН продавца в счёте не прочитан — сверьте, что счёт от «{supplier}»")
    known_inn = suppliers.inn_of(supplier) if suppliers is not None else None
    if known_inn:
        if known_inn == inn:
            return InvoiceCheck("seller", OK, f"ИНН {inn} — поставщик заказа «{supplier}»")
        return InvoiceCheck("seller", WARNING, f"Счёт от {seller}, ИНН {inn}; у поставщика заказа «{supplier}» в "
                                               f"справочнике ИНН {known_inn}")
    owner = suppliers.supplier_of_inn(inn) if suppliers is not None else None
    if owner:
        if same_org(owner, supplier):
            return InvoiceCheck("seller", OK, f"ИНН {inn} — поставщик заказа «{supplier}»")
        return InvoiceCheck("seller", WARNING, f"ИНН {inn} в справочнике у «{owner}», а в заказе поставщик «{supplier}»")
    if same_org(header.seller_name, supplier):
        return InvoiceCheck("seller", OK, f"Продавец {seller} — поставщик заказа (ИНН {inn} в справочнике не записан)")
    return InvoiceCheck("seller", INFO, f"Счёт от {seller}, ИНН {inn}; в заказе поставщик «{supplier}» — сверьте")


def _kopecks(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1)))


def _total(reading: InvoiceReading, lines: Sequence[ProcurementLine]) -> InvoiceCheck | None:
    total = reading.header.total
    if total is None:
        return None
    vat = "без НДС" if reading.header.vat_free else (f"НДС {rub(reading.header.vat)}" if reading.header.vat else "")
    amount = rub(total) + (f" ({vat})" if vat else "")
    foreign = [line for line in lines if line.money.source_currency or (line.money.currency or "RUB") != "RUB"]
    if foreign:
        return InvoiceCheck("total", INFO, f"Сумма счёта {amount}; строки заказа в валюте — сверьте сумму вручную")
    priced = [line.money.sum for line in lines if line.money.sum is not None]
    if len(priced) < len(lines):
        return InvoiceCheck("total", INFO, f"Сумма счёта {amount}; суммы внесены не у всех строк заказа "
                                           f"({len(priced)} из {len(lines)}) — сверка после ввода цен")
    order = _kopecks(sum(priced, Decimal(0)))
    if abs(order - total) <= TOTAL_TOLERANCE:
        return InvoiceCheck("total", OK, f"Сумма счёта {amount} = сумме строк заказа")
    return InvoiceCheck("total", WARNING, f"Сумма счёта {amount}, строк заказа — {rub(order)} "
                                          f"(разница {rub(total - order)})")


def _quantity(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    return format(Decimal(str(value)).normalize(), "f")


def _lines(reading: InvoiceReading, lines: Sequence[ProcurementLine]) -> list[InvoiceCheck]:
    if not reading.lines or not lines:
        return []
    items = [
        DocumentItem(name=line.name, article=line.article, unit=line.unit,
                     quantity=Decimal(str(line.qty)) if line.qty is not None else None)
        for line in reading.lines
    ]
    candidates = [
        OrderLineCandidate(
            key=str(line.id), article=line.design_article,
            names=(line.site_name, line.calculated_name, line.invoice_item_name),
            quantity=line.actual_quantity if line.actual_quantity is not None else line.to_buy,
        )
        for line in lines
    ]
    matches, free_items, free_keys = match_document_items(items, candidates)
    by_key = {str(line.id): line for line in lines}
    checks = []
    if not free_keys:
        checks.append(InvoiceCheck("lines", OK, f"Позиций в счёте: {len(items)}; все строки заказа ({len(lines)}) "
                                                f"нашлись в счёте"))
    else:
        missing = ", ".join(by_key[key].legacy_id for key in free_keys)
        checks.append(InvoiceCheck("lines", WARNING, f"Позиций в счёте: {len(items)}; не нашлись в счёте строки "
                                                     f"заказа: {missing}"))
    differ = []
    for match in matches:
        line, item = by_key[match.key], items[match.item_index]
        ordered = line.actual_quantity if line.actual_quantity is not None else line.to_buy
        if item.quantity is not None and ordered is not None and Decimal(item.quantity) != Decimal(ordered):
            differ.append(f"{line.legacy_id} — в заказе {_quantity(ordered)}, в счёте {_quantity(item.quantity)}")
    if differ:
        checks.append(InvoiceCheck("lines", WARNING, "Кол-во не совпадает: " + "; ".join(differ)))
    if free_items:
        names = "; ".join(items[index].name[:60] for index in free_items[:3])
        more = f" и ещё {len(free_items) - 3}" if len(free_items) > 3 else ""
        checks.append(InvoiceCheck("lines", INFO, f"В счёте сверх заказа: {names}{more}"))
    return checks


def invoice_checks(
    reading: InvoiceReading, supplier: str, lines: Sequence[ProcurementLine], suppliers: SupplierInns | None = None,
) -> list[InvoiceCheck]:
    """Seller, total and positions of the invoice against the order; nothing for a file without text."""
    if reading.scan:
        return []
    checks = [check for check in (_seller(reading, supplier, suppliers), _total(reading, lines)) if check is not None]
    return checks + _lines(reading, lines)
