"""«КП из PDF и текста» (stage option procurement.quote_import): the text of a supplier's quote — pasted, or the text
layer of a PDF — becomes proposed quote lines mapped to the request's lines. Nothing is saved here: the buyer reviews
the form and saves it through `rfq.add_quote`.

Port of Roller_Buyer `packages/shared/src/quote-import.ts` (`applyPacks`, matching) and `apps/api/src/quote-import.ts`.
Roller read PDFs with Google; ERP has no such service, so a quote line is read from the text by one fixed shape
(«наименование  кол-во  ед.  цена  [сумма]  [срок]») and what does not fit is returned as unread, not guessed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select

from app.shared.identity import Actor
from app.shared.pdf_text import PdfLimitExceeded, pdf_text
from app.workflows.article_key import code_key

from . import items as items_repo
from . import rfq as rfq_service
from .infrastructure import ItemAliasRow, RfqLineRow, RfqSupplierRow

log = logging.getLogger(__name__)

PDF_LIMIT = 20 * 1024 * 1024
Refused = rfq_service.Refused

# ── packs: «1 уп × 8000 шт» is 8000 pieces, not one ─────────────────────────────────────────────────────────────

_PACK_WORDS = re.compile(
    r"(?:^|[^а-яa-z])(уп(?:ак(?:овк[аиу])?)?|упк|pack|pkg|reel|tray|tube|бобин[аы]?|катушк[аи]|рулон|бухт[аы]|коробк[аи]|кор)"
    r"\.?(?=$|[^а-яa-z])", re.I)
_END = r"\.?(?=$|[^а-яa-z])"
_COUNT = r"(\d[\d\s]*(?:[.,]\d+)?)\s*"
_UNIT_WORDS = {
    "шт": re.compile(_COUNT + r"(?:шт|штук|pcs|pc|ea)" + _END, re.I),
    "м": re.compile(_COUNT + r"(?:м|метр(?:ов|а)?|m)" + _END, re.I),
    "кг": re.compile(_COUNT + r"(?:кг|kg)" + _END, re.I),
    "л": re.compile(_COUNT + r"(?:л|литр(?:ов|а)?|l)" + _END, re.I),
}
# «уп. 8000», «упаковка по 8000», «reel 4000» — a number right after the pack word.
_AFTER_PACK = re.compile(
    r"(?:уп(?:ак(?:овк[аиу])?)?|pack|reel|бобин[аы]?|катушк[аи]|бухт[аы])\.?\s*(?:по\s*)?[x×/]?\s*(\d[\d\s]*)", re.I)
_SAME_UNIT = {
    "шт": re.compile(r"^(?:шт|штук[аи]?|pcs|pc|ea)\.?$", re.I),
    "м": re.compile(r"^(?:м|метр(?:ов|а)?|m)\.?$", re.I),
    "кг": re.compile(r"^(?:кг|kg)\.?$", re.I),
    "л": re.compile(r"^(?:л|литр(?:ов|а)?|l)\.?$", re.I),
}


@dataclass
class ImportedLine:
    source_name: str
    source_article: str = ""
    qty: Decimal | None = None
    unit: str = ""
    price: Decimal | None = None  # rubles per unit as written in the quote (per unit asked after `apply_packs`)
    vat_rate: Decimal | None = None
    lead_days: int | None = None
    in_stock: bool = False
    declined: bool = False
    analog: bool = False
    analog_note: str = ""
    rfq_line_id: UUID | None = None
    matched_by: str | None = None
    pack: Decimal | None = None  # pack size in the requested unit when the quote priced a pack
    moq: Decimal | None = None  # the smallest quantity the supplier sells, in the requested unit
    pack_note: str = ""  # what `apply_packs` did or could not do, for the buyer to check


@dataclass(frozen=True)
class Target:
    rfq_line_id: UUID
    name: str
    article: str | None
    quantity: Decimal
    unit: str  # normalised: шт / м / кг / л / уп / компл, or as typed
    item_id: UUID | None = None


def _size_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _pack_size(text: str, unit: str) -> Decimal | None:
    by_unit = _UNIT_WORDS.get(unit)
    found = by_unit.search(text) if by_unit else None
    after = _AFTER_PACK.search(text)
    raw = (found.group(1) if found else None) or (after.group(1) if after else None)
    if not raw:
        return None
    try:
        size = Decimal(re.sub(r"\s", "", raw).replace(",", "."))
    except ArithmeticError:
        return None
    return size if size > 1 else None


def apply_packs(lines: Sequence[ImportedLine], targets: Sequence[Target]) -> list[ImportedLine]:
    """A quote priced per pack («1 уп», the price of the pack, «8000 шт» in the name or the unit) is turned into the
    requested unit: price per unit = pack price / pack size, pack = moq = pack size. A pack of unknown size leaves the
    price empty (a pack price shown as a piece price is the error the owner hit in Roller, 12.10.2026). A quote for
    more than requested in the same unit becomes the minimum batch. Lines not matched to a request line, declined or
    without a price stay as they are."""
    by_id = {target.rfq_line_id: target for target in targets}
    result: list[ImportedLine] = []
    for line in lines:
        target = by_id.get(line.rfq_line_id) if line.rfq_line_id else None
        if target is None or line.declined or line.price is None:
            result.append(line)
            continue
        unit_text = line.unit.strip()
        short = target.unit
        if target.unit != "уп" and _PACK_WORDS.search(f" {unit_text} "):
            size = _pack_size(f"{unit_text} {line.source_name} {line.source_article}", target.unit)
            if size is None:
                line.price = None
                line.pack_note = (f"КП за упаковку («{unit_text}»), размер упаковки не указан — "
                                  f"впишите упаковку и цену за {short}")
            else:
                line.price = (line.price / size).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                line.pack = line.moq = size
                line.pack_note = (f"КП за упаковку {_size_text(size)} {short}: цена пересчитана за {short}, "
                                  f"упаковка и мин. партия — {_size_text(size)}")
        elif (line.qty is not None and line.qty > target.quantity and target.unit in _SAME_UNIT
              and _SAME_UNIT[target.unit].match(unit_text)):
            line.moq = line.qty
            line.pack_note = (f"КП на {_size_text(line.qty)} {short} при запросе {_size_text(target.quantity)} — "
                              f"записано как минимальная партия")
        result.append(line)
    return result


# ── reading the text ────────────────────────────────────────────────────────────────────────────────────────────

_NUM = r"(?:\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_UNIT_TOKEN = (
    r"(?:шт|штук[аи]?|pcs|pc|ea|м|метр(?:ов|а)?|m|кг|kg|л|литр(?:ов|а)?|l|уп(?:ак(?:овк[аиу])?)?|упк|pack|"
    r"компл(?:ект)?|set|бобин[аы]?|катушк[аи]|рулон|бухт[аы]|reel)"
)
_MONEY = r"(?:\s*(?:руб\.?|₽|р\.))?"
_LINE = re.compile(
    rf"^\s*(?:\d{{1,3}}[.)]?\s+)?(?P<name>.+?)\s+(?P<qty>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})\.?\s+"
    rf"(?P<price>{_NUM}){_MONEY}(?:\s+(?P<total>{_NUM}){_MONEY})?(?P<tail>.*)$", re.I)
_LEAD_WORD = re.compile(r"^\s*(?:%|дн|дней|день|нед|мес|раб|week|month)", re.I)
_TAIL_VAT = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*%")
_PRICE_HINT = re.compile(r"\d[.,]\d{2}\b")


def _number(text: str) -> Decimal | None:
    return rfq_service.parse_ru_number(text)


def read_text_lines(text: str) -> tuple[list[ImportedLine], list[str]]:
    """Free text (a PDF's text layer, a web page) → lines of the shape «наименование кол-во ед. цена [сумма] [срок]»
    and the lines that look like a priced position but were not read."""
    lines: list[ImportedLine] = []
    unread: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        raw = " ".join(raw.split())
        if not raw:
            continue
        match = _LINE.match(raw)
        price = _number(match.group("price")) if match else None
        qty = _number(match.group("qty")) if match else None
        if match is None or price is None or price <= 0 or qty is None or qty <= 0:
            if _PRICE_HINT.search(raw) and len(unread) < 30:
                unread.append(raw[:200])
            continue
        tail = match.group("tail") or ""
        total = match.group("total")
        if total and _LEAD_WORD.match(tail):  # «305,00 9 дн», «8000 22%»: not the sum
            tail, total = f"{total}{tail}", None
        days, in_stock, declined = rfq_service.parse_lead(tail)
        vat = _TAIL_VAT.search(tail)
        name = match.group("name").strip(" -–—;,")
        lines.append(ImportedLine(
            source_name=name, qty=qty, unit=match.group("unit"), price=price, lead_days=days, in_stock=in_stock,
            declined=declined, analog=bool(re.search(r"аналог", name, re.I)),
            vat_rate=Decimal(vat.group(1).replace(",", ".")) if vat and Decimal(vat.group(1).replace(",", ".")) < 100 else None,
        ))
    return lines, unread


def _without_vat(text: str) -> bool:
    """Prices «без НДС» in the document's own words (a column title or a caption), as the Excel paste reads it."""
    return bool(re.search(r"(?:цена|стоимость|сумма)[^\n]{0,24}без\s*ндс|без\s*ндс[^\n]{0,24}(?:цена|стоимость)", text, re.I))


def _table_lines(text: str) -> tuple[list[ImportedLine], bool]:
    """A tab-separated table (Excel's clipboard) → lines, and whether its price column says «без НДС»."""
    header, rows = rfq_service.parse_pasted_table(text)
    if not rows:
        return [], False
    columns = rfq_service.guess_columns(header, rows)
    if columns["name"] < 0 and columns["article"] < 0:
        return [], False
    title = header[columns["price"]] if header and columns["price"] >= 0 else ""
    lines = [
        ImportedLine(
            source_name=row.source_name, source_article=row.source_article, qty=row.qty, unit=row.unit, price=row.price,
            vat_rate=row.vat_rate, lead_days=row.lead_days, in_stock=row.in_stock, declined=row.declined,
            analog=row.analog,
        )
        for row in rfq_service.paste_rows(rows, columns)
    ]
    return lines, bool(re.search(r"без\s*ндс", title, re.I))


# ── matching ────────────────────────────────────────────────────────────────────────────────────────────────────


def _aliased(session: Any, supplier_id: UUID | None, targets: Sequence[Target]) -> dict[str, UUID]:
    """alias key → item id: the supplier's own wordings win over generic ones."""
    item_ids = [target.item_id for target in targets if target.item_id]
    if not item_ids:
        return {}
    where = ItemAliasRow.supplier_id.is_(None) if supplier_id is None else or_(
        ItemAliasRow.supplier_id.is_(None), ItemAliasRow.supplier_id == supplier_id)
    found: dict[str, UUID] = {}
    rows = session.execute(select(ItemAliasRow.alias_key, ItemAliasRow.item_id, ItemAliasRow.supplier_id).where(
        ItemAliasRow.item_id.in_(item_ids), where))
    for key, item_id, owner in sorted(rows, key=lambda row: row[2] is not None):  # generic first, own overwrite
        found[key] = item_id
    return found


def match_lines(lines: list[ImportedLine], targets: Sequence[Target], aliases: dict[str, UUID]) -> list[ImportedLine]:
    """Remembered wording of this supplier first, then the part number, then the name words (rfq.match_rows).
    A name match with a different part number is an analog."""
    by_item = {target.item_id: target for target in targets if target.item_id}
    taken: set[UUID] = set()
    for line in lines:
        for key in (items_repo.alias_key(f"{line.source_article} {line.source_name}"),
                    items_repo.alias_key(line.source_name), items_repo.alias_key(line.source_article)):
            item_id = aliases.get(key) if key else None
            target = by_item.get(item_id) if item_id else None
            if target is not None and target.rfq_line_id not in taken:
                line.rfq_line_id, line.matched_by = target.rfq_line_id, "alias"
                taken.add(target.rfq_line_id)
                break
    rest = [line for line in lines if line.rfq_line_id is None]
    free = [rfq_service.MatchTarget(target.rfq_line_id, target.name, target.article)
            for target in targets if target.rfq_line_id not in taken]
    adapters = [
        rfq_service.PastedRow(line.source_name, line.source_article, line.qty, line.unit, line.price, line.vat_rate,
                              line.lead_days, line.in_stock, line.declined, line.analog) for line in rest
    ]
    rfq_service.match_rows(adapters, free)
    by_id = {target.rfq_line_id: target for target in targets}
    for line, adapter in zip(rest, adapters):
        line.rfq_line_id, line.matched_by = adapter.rfq_line_id, adapter.matched_by
        target = by_id.get(adapter.rfq_line_id) if adapter.rfq_line_id else None
        if target and adapter.matched_by == "name" and line.source_article and target.article \
                and code_key(line.source_article) != code_key(target.article):
            line.analog = True
            line.analog_note = f"Предложено: {line.source_article} {line.source_name}".strip()[:512]
    return lines


# ── entry point ─────────────────────────────────────────────────────────────────────────────────────────────────


def text_of_pdf(content: bytes) -> str:
    if len(content) > PDF_LIMIT:
        raise Refused("PDF больше 20 МБ — сожмите файл или вставьте текст")
    if b"%PDF" not in content[:1024]:
        raise Refused("Это не PDF")
    try:
        text = pdf_text(content)
    except PdfLimitExceeded as error:
        raise Refused("PDF слишком сложный для чтения — вставьте текст КП или введите строки вручную") from error
    if not text.strip():
        raise Refused("В PDF нет текстового слоя (скан или защищённый файл) — вставьте текст КП или введите строки вручную")
    return text


def _json(line: ImportedLine) -> dict:
    return {
        "source_name": line.source_name, "source_article": line.source_article, "qty": rfq_service.num(line.qty),
        "unit": line.unit, "price": rfq_service.num(line.price), "vat_rate": rfq_service.num(line.vat_rate),
        "lead_days": line.lead_days, "in_stock": line.in_stock, "declined": line.declined, "analog": line.analog,
        "analog_note": line.analog_note, "rfq_line_id": str(line.rfq_line_id) if line.rfq_line_id else None,
        "matched_by": line.matched_by, "pack": rfq_service.num(line.pack), "moq": rfq_service.num(line.moq),
        "pack_note": line.pack_note,
    }


def parse_quote(uow: Any, actor: Actor, rfq_id: UUID, text: str, supplier_id: UUID | None, source: str) -> dict:
    """Proposed lines for the quote form. `source` — «paste» (typed or pasted) or «file» (a PDF's text layer)."""
    with uow:
        session = uow.session
        rfq = rfq_service._open(rfq_service._own(rfq_service._rfq(session, rfq_id), actor))
        asked = None
        if supplier_id is not None:
            rfq_supplier = session.scalar(select(RfqSupplierRow).where(
                RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier_id))
            asked = rfq_service.asked_lines(session, [rfq_supplier.id]).get(rfq_supplier.id) if rfq_supplier else None
        targets = [
            Target(line.id, line.name, line.article, line.quantity, items_repo.normalize_unit(line.unit), line.item_id)
            for line in rfq_service._live_lines(session, rfq.id) if asked is None or line.id in asked
        ]
        aliases = _aliased(session, supplier_id, targets)
        default_vat = Decimal(rfq_service.purchasing_settings.load(session)["default_vat_rate"])

    without_vat = _without_vat(text)
    unread: list[str] = []
    if "\t" in text:
        lines, titled = _table_lines(text)
        without_vat = without_vat or titled
    else:
        lines, unread = read_text_lines(text)
        if not lines:  # columns separated by runs of spaces (copied from a viewer): the same table reader
            lines, titled = _table_lines(re.sub(r" {2,}", "\t", text))
            lines = [row for row in lines if row.price is not None or row.declined]  # prose is not a table
            without_vat = without_vat or titled
    if not lines:
        raise Refused("Не нашёл в тексте строки КП. Нужны наименование, количество, единица и цена в одной строке "
                      "(или таблица из Excel с заголовком)")
    lines = apply_packs(match_lines(lines[:500], targets, aliases), targets)
    matched = [line for line in lines if line.rfq_line_id]
    return {
        "source": source, "vat_included": not without_vat, "default_vat_rate": rfq_service.num(default_vat),
        "lines": [_json(line) for line in matched],
        "unmatched": [_json(line) for line in lines if not line.rfq_line_id],
        "unread": unread,
    }


def remember_aliases(
    make_uow: Callable[[], Any], actor: Actor, now: datetime, rfq_id: UUID, supplier_id: UUID,
    aliases: Sequence[tuple[UUID, str]],
) -> int:
    """After a quote is saved: the supplier's wording of a position → our item, so the next quote matches by itself.
    Best effort — the quote is already saved, a refused alias (taken by another item, no item behind the line) is
    skipped. Returns how many were stored."""
    stored = 0
    try:
        uow = make_uow()
        with uow:
            session = uow.session
            item_of = {
                row.id: row.item_id for row in session.scalars(
                    select(RfqLineRow).where(RfqLineRow.rfq_id == rfq_id, RfqLineRow.removed_at.is_(None)))
            }
            for line_id, text in aliases:
                item_id = item_of.get(line_id)
                if item_id is None:
                    continue
                try:
                    _, created = items_repo.add_alias(uow, item_id, text, supplier_id, actor, now)
                except (items_repo.ItemConflict, items_repo.ItemInvalid, items_repo.ItemNotFound):
                    continue
                stored += int(created)
            uow.commit()
    except Exception:  # noqa: BLE001 — the aliases are a convenience; the saved quote must not fail on them
        log.warning("quote aliases were not stored for rfq %s", rfq_id, exc_info=True)
        return 0
    return stored
