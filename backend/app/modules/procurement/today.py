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
    # Option procurement.rfq: fed from the buyer's open requests for quotes (app.workflows.procurement_rfq_followups).
    ("rfq_no_answer", "Нет ответа на запрос КП"),
    ("rfq_to_decide", "Выбрать поставщика"),
    ("in_work", "В работе: запросить, выбрать поставщика"),
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
        "project": line.project_reference or NO_PROJECT,
        "status": line.status.value,
    }


NO_PROJECT = "Без проекта"
# Follow-up fields: {field key: {procurement line id: {rfq_id, rfq_number, date, days, hint}}}.
Followups = dict[str, dict]
FOLLOWUP_KEYS = ("rfq_no_answer", "rfq_to_decide")
CARDS_PER_FIELD = 30


def _age_anchor(line: ProcurementLine) -> date:
    """Best available start of waiting for an invoice: the order date, else the day the line was created."""
    return line.order_date or line.created_at.date()


def build_today(
    lines: Iterable[ProcurementLine], today: date, available_count: int = 0, followups: Followups | None = None,
) -> list[dict]:
    """Sections {key, title, count, items} in fixed priority order; empty sections are omitted."""
    tomorrow = today + timedelta(days=1)
    buckets: dict[str, list[tuple[tuple, dict]]] = {key: [] for key, _ in SECTIONS}
    for line in lines:
        for key in FOLLOWUP_KEYS:
            follow = (followups or {}).get(key, {}).get(line.id)
            if follow is not None:
                item = _item(line, follow["date"], follow["days"], follow["hint"])
                item["rfq_id"], item["rfq_number"] = follow["rfq_id"], follow["rfq_number"]
                buckets[key].append(((follow["date"],), item))
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
        elif status in (ProcurementStatus.IN_WORK, ProcurementStatus.READY):
            anchor = line.created_at.date()
            age = (today - anchor).days
            hint = "Заполните поставщика и количество" if status is ProcurementStatus.IN_WORK else "Отправьте запрос поставщику"
            buckets["in_work"].append(((anchor,), _item(line, anchor, age, hint)))
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


def _project_cards(items: list[dict]) -> list[dict]:
    """One card per project inside a field: how many positions, the worst age, the suppliers involved."""
    cards: dict[str, dict] = {}
    for item in items:
        card = cards.setdefault(item["project"], {"project": item["project"], "count": 0, "days": None, "date": None, "suppliers": set()})
        card["count"] += 1
        if item["days"] is not None and (card["days"] is None or item["days"] > card["days"]):
            card["days"] = item["days"]
        if item["date"] and (card["date"] is None or item["date"] < card["date"]):
            card["date"] = item["date"]
        if item["supplier"]:
            card["suppliers"].add(item["supplier"])
    ordered = sorted(cards.values(), key=lambda card: (-(card["days"] if card["days"] is not None else -10**6), card["project"]))
    return [{**card, "suppliers": sorted(card["suppliers"])} for card in ordered]


def build_board(
    lines: Iterable[ProcurementLine], today: date, available_count: int = 0, followups: Followups | None = None,
) -> dict:
    """«Сегодня» as a board: fields in priority order with project cards (a project may sit in several fields).
    Positions are not sent here — a card opens them through `project_lines` (5 000 lines = 1.8 MB otherwise)."""
    sections = build_today(list(lines), today, available_count, followups)
    for section in sections:
        if section["key"] == "available":
            section["projects"] = []
            continue
        cards = _project_cards(section["items"])
        # The most urgent cards only: a column of hundreds of projects is not read, it is scrolled past.
        section["projects"], section["projects_more"] = cards[:CARDS_PER_FIELD], max(0, len(cards) - CARDS_PER_FIELD)
        section["items"] = []
    return {"sections": sections}


def project_lines(
    lines: Iterable[ProcurementLine], project: str, today: date, followups: Followups | None = None,
) -> list[dict]:
    """Every open line of one project with its stage and the board field it sits in (None — not on the board).
    A line may sit in several fields: `field` is the first, `fields` all of them; a request follow-up adds `rfq`."""
    lines = [line for line in lines if (line.project_reference or NO_PROJECT) == project]
    fields_of: dict[str, list[str]] = {}
    rfq_of: dict[str, dict] = {}
    for section in build_today(lines, today, followups=followups):
        for item in section["items"]:
            fields_of.setdefault(item["id"], []).append(section["title"])
            if "rfq_id" in item:
                rfq_of.setdefault(item["id"], {"id": item["rfq_id"], "number": item["rfq_number"], "hint": item["hint"]})
    closed = {ProcurementStatus.DONE, ProcurementStatus.CANCELLED}
    rows = [
        {
            "id": str(line.id),
            "name": line.invoice_item_name or line.calculated_name,
            "article": line.design_article or line.designation,
            "supplier": line.supplier,
            "order_number": line.order_number,
            "status": line.status.value,
            "expected": line.expected_arrival.isoformat() if line.expected_arrival else None,
            "quantity": str(line.actual_quantity) if line.actual_quantity is not None else None,
            "unit": line.actual_unit,
            "field": (fields_of.get(str(line.id)) or [None])[0],
            "fields": fields_of.get(str(line.id), []),
            "rfq": rfq_of.get(str(line.id)),
        }
        for line in lines
        if line.status not in closed
    ]
    rows.sort(key=lambda row: (row["field"] is None, row["field"] or "", row["name"] or ""))
    return rows
