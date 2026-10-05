"""Stage option `procurement.today`: the buyer's prioritized to-do list, computed on read from the lines' facts.

Pure function over line objects. No stage-start timestamps are stored on a line (only `created_at`, `order_date`,
`invoice_date`, `paid_at`, `expected_arrival`, `actual_arrival`), so ages degrade to the nearest real date and
the item says which one it is."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from .domain import ProcurementLine, ProcurementStatus

# Fixed priority order of the sections.
SECTIONS = (
    ("overdue", "Просрочена поставка"),
    ("arriving", "Приезжает сегодня/завтра"),
    ("awaiting_invoice", "Ждём счёт"),
    ("to_pay", "Ждёт оплаты"),
    ("quarantine", "Карантин / ожидание приёмки"),
    ("available", "Новые доступные"),
)


def _item(line: ProcurementLine, day: date | None, days: int | None, hint: str) -> dict:
    return {
        "id": str(line.id),
        "name": line.invoice_item_name or line.calculated_name,
        "article": line.design_article or line.designation,
        "supplier": line.supplier,
        "order_number": line.order_number,
        "date": day.isoformat() if day else None,
        "days": days,
        "hint": hint,
    }


def _age_anchor(line: ProcurementLine) -> date:
    """Best available start of waiting for an invoice: the order date, else the day the line was created."""
    return line.order_date or line.created_at.date()


def build_today(lines: Iterable[ProcurementLine], today: date, available_count: int = 0) -> list[dict]:
    """Sections {key, title, count, items} in fixed priority order; empty sections are omitted."""
    tomorrow = today + timedelta(days=1)
    buckets: dict[str, list[tuple[tuple, dict]]] = {key: [] for key, _ in SECTIONS}
    for line in lines:
        status = line.status
        expected = line.expected_arrival
        if status is ProcurementStatus.IN_TRANSIT:
            if expected is not None and expected < today:
                late = (today - expected).days
                buckets["overdue"].append(
                    ((expected,), _item(line, expected, late, f"Ждали {expected:%d.%m}: позвоните поставщику, обновите ожидаемую дату"))
                )
            elif expected is not None and expected <= tomorrow:
                word = "сегодня" if expected == today else "завтра"
                buckets["arriving"].append(
                    ((expected,), _item(line, expected, (today - expected).days, f"Приезжает {word}: подготовьте приёмку"))
                )
        elif status is ProcurementStatus.AWAITING_INVOICE:
            anchor = _age_anchor(line)
            age = (today - anchor).days
            buckets["awaiting_invoice"].append(
                ((anchor,), _item(line, anchor, age, f"Ждём счёт {age} дн.: напомните поставщику"))
            )
        elif status is ProcurementStatus.TO_PAY:
            anchor = line.invoice_date or _age_anchor(line)
            age = (today - anchor).days
            buckets["to_pay"].append(((anchor,), _item(line, anchor, age, "Счёт получен: передайте в оплату")))
        elif status is ProcurementStatus.QUARANTINE:
            # The inspection date (ФАКТ. ДАТА) when the tick set it, else the expected date.
            anchor = line.actual_arrival or expected
            age = (today - anchor).days if anchor else None
            buckets["quarantine"].append(
                ((anchor or date.max,), _item(line, anchor, age, "Товар на приёмке: проверьте результат контроля"))
            )
    sections = []
    for key, title in SECTIONS:
        if key == "available":
            if available_count > 0:
                summary = {
                    "id": None,
                    "name": f"Свободных позиций: {available_count}",
                    "article": None,
                    "supplier": None,
                    "order_number": None,
                    "date": None,
                    "days": None,
                    "hint": "Откройте «Очереди» и возьмите в работу",
                }
                sections.append({"key": key, "title": title, "count": available_count, "items": [summary]})
            continue
        entries = sorted(buckets[key], key=lambda entry: entry[0])  # oldest / earliest first
        if entries:
            sections.append({"key": key, "title": title, "count": len(entries), "items": [item for _, item in entries]})
    return sections
