"""«Журнал склада» (SK-16): one newest-first list of what happened to the stock.

SUPPLYMAP Stock05.gs stock05Log_ (Portal.gs portalStock05Log, owner 22.09.2026) merges two roads: the traceability
Journal rows of stage «Склад» (receipt, move, row edit) and the СПИСАНИЕ sheet (write-offs and returns). In ERP the
first road is the warehouse audit events, the second the `warehouse_writeoffs` journal (SK-13), which also holds the
kitting issues. Audit events that only mirror a `warehouse_writeoffs` row are left out, so a movement is listed once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.shared.clock import as_utc

from .domain import WriteOffEntry


JOURNAL_LIMIT = 400  # STOCK05_LOG_LIMIT_: the rest is reached by narrowing the search
# The same movement already sits in warehouse_writeoffs: listing the audit event too would double it.
MIRRORED_BY_WRITEOFFS = frozenset({
    "warehouse.written_off", "warehouse.writeoff_returned", "warehouse.legacy_writeoff_history",
    "warehouse.returned_to_supplier",
})
WRITEOFF_KIND_LABELS = {
    "writeoff": "Списание",
    "return": "Возврат",
    "issue": "Выдача",
    "supplier_return": "Возврат поставщику",
}


@dataclass(frozen=True)
class JournalEvent:
    """A warehouse audit event with the lot it touched (grn/package/name/warehouse are empty when unknown)."""

    occurred_at: datetime
    event_type: str
    actor_name: str
    details: Mapping[str, object]
    grn: str = ""
    package: str = ""
    name: str = ""
    warehouse: str = ""


@dataclass(frozen=True)
class JournalRow:
    occurred_at: datetime
    kind: str  # «Событие», «Списание», «Возврат», «Выдача»
    lot: str  # «ГРН / ПАКЕТ»
    name: str
    event: str
    who: str
    warehouse: str


@dataclass(frozen=True)
class JournalPage:
    rows: tuple[JournalRow, ...]
    total: int
    truncated: int


class JournalReader(Protocol):
    def audit_events(self) -> list[JournalEvent]: ...
    def writeoff_entries(self) -> list[WriteOffEntry]: ...


def _number(value: object) -> str:
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return str(value or "")
    return format(amount.normalize(), "f")


def _text(details: Mapping[str, object], key: str) -> str:
    value = details.get(key)
    return "" if value is None else str(value).strip()


def _route(details: Mapping[str, object]) -> str:
    return f"{_text(details, 'from') or '—'} → {_text(details, 'to') or '—'}"


_RECEIPT_CHANGES = (("storage_location", "место"), ("purpose", "назначение"), ("expiry_date", "срок годности"),
                    ("unit_mass_g", "масса 1 шт"))


def event_text(event_type: str, details: Mapping[str, object]) -> str:
    """СОБЫТИЕ of one warehouse audit event, in the words of the SUPPLYMAP Journal."""
    if event_type == "warehouse.received":
        changes = [
            f"{label}: {_text(details, f'{key}_from') or '—'} → {_text(details, f'{key}_to') or '—'}"
            for key, label in _RECEIPT_CHANGES if f"{key}_to" in details
        ]
        return "; ".join([f"Приход из приёмки: +{_number(details.get('quantity'))}", *changes])
    if event_type == "warehouse.manually_received":
        place = _text(details, "storage_location")
        return f"Внесено вручную: +{_number(details.get('quantity'))}" + (f" · место {place}" if place else "")
    if event_type == "warehouse.legacy_imported":
        place = _text(details, "storage_location")
        return (f"Перенос остатков из SUPPLYMAP: приход {_number(details.get('inbound'))}, "
                f"списано {_number(details.get('written_off'))}") + (f" · место {place}" if place else "")
    if event_type == "warehouse.legacy_synced":
        return f"Догрузка из Google: {_text(details, 'changes') or '—'}"
    if event_type == "warehouse.moved":
        return f"Перемещение: {_route(details)}"
    if event_type == "warehouse.transferred":
        return (f"Перемещение на другой склад: {_text(details, 'from_warehouse')} → {_text(details, 'to_warehouse')}, "
                f"место {_route(details)}")
    if event_type == "warehouse.location_corrected":
        return f"Правка места: {_route(details)}"
    if event_type == "warehouse.location_moved":
        return f"Перенос места целиком: {_route(details)}"
    if event_type == "warehouse.identity_edited":
        parts = [f"{label}: {_text(details, key)}" for key, label in
                 (("article", "артикул"), ("name", "наименование"), ("series", "серия")) if _text(details, key)]
        return "Правка артикула и наименования" + (f": {'; '.join(parts)}" if parts else "")
    if event_type == "warehouse.row_edited":
        reason = _text(details, "reason")
        return f"Правка строки: {_text(details, 'changes') or '—'}" + (f" ({reason})" if reason else "")
    if event_type == "warehouse.item_types_saved":
        return f"Справочник типов сохранён: {_text(details, 'count')}"
    if event_type == "warehouse.rack_layout_saved":
        return f"Склад 0.5: {_text(details, 'message')}"
    if event_type in _RESERVE_EVENTS:
        unit = _text(details, "unit")
        quantity = _number(details.get("quantity")) + (f" {unit}" if unit else "")
        return f"{_RESERVE_EVENTS[event_type]}: {quantity} · {_text(details, 'holder') or '—'}"
    return event_type


# «Со склада» of procurement lines (owner 2026-10-04): the lot's РЕЗЕРВ moves without a write-off row.
_RESERVE_EVENTS = {
    "warehouse.reserved_for_procurement": "Резерв «Со склада»",
    "warehouse.procurement_reserve_released": "Снят резерв «Со склада»",
    "warehouse.procurement_reserve_taken_by_kit": "Резерв «Со склада» передан в комплектацию",
}


def _event_row(event: JournalEvent) -> JournalRow:
    details = event.details or {}
    grn = event.grn or _text(details, "grn")
    package = event.package or _text(details, "package")
    warehouse = _text(details, "to_warehouse") or event.warehouse or _text(details, "warehouse")
    return JournalRow(
        occurred_at=event.occurred_at, kind="Событие", lot=grn + (f" / {package}" if package else ""),
        name=event.name, event=event_text(event.event_type, details), who=event.actor_name, warehouse=warehouse,
    )


def _writeoff_row(entry: WriteOffEntry) -> JournalRow:
    """stock05Log_ СПИСАНИЕ branch: «−КОЛ-ВО ЕД · ОСНОВАНИЕ · НАЗНАЧЕНИЕ · проведено / ждёт проводки»."""
    sign = "+" if entry.kind == "return" else "−"
    event = (f"{sign}{_number(entry.quantity)} {entry.unit} · {entry.reason or '—'}"
             + (f" · {entry.purpose}" if entry.purpose else "")
             + (" · проведено" if entry.posted else " · ждёт проводки"))
    return JournalRow(
        occurred_at=entry.occurred_at, kind=WRITEOFF_KIND_LABELS.get(entry.kind, entry.kind),
        lot=entry.grn + (f" / {entry.package}" if entry.package else ""), name=entry.name, event=event,
        who=entry.author or entry.actor_name, warehouse=entry.warehouse,
    )


def _matches(row: JournalRow, words: list[str]) -> bool:
    haystack = " ".join((row.lot, row.name, row.event, row.who, row.warehouse, row.kind)).lower()
    return all(word in haystack for word in words)


def journal_page(
    events: Iterable[JournalEvent],
    writeoffs: Iterable[WriteOffEntry],
    query: str = "",
    limit: int = JOURNAL_LIMIT,
) -> JournalPage:
    """Every word of the query must appear (ГРН, наименование, событие, кто, склад, вид). Newest first; equal times
    keep the order events → write-offs, as the stable sort of stock05Log_ does."""
    words = str(query or "").lower().split()
    rows = [_event_row(event) for event in events if event.event_type not in MIRRORED_BY_WRITEOFFS]
    rows += [_writeoff_row(entry) for entry in writeoffs]
    found = [row for row in rows if _matches(row, words)]
    found.sort(key=lambda row: as_utc(row.occurred_at), reverse=True)  # stable: ties keep their order
    return JournalPage(rows=tuple(found[:limit]), total=len(found), truncated=max(0, len(found) - limit))


def warehouse_journal(uow, query: str = "", limit: int = JOURNAL_LIMIT) -> JournalPage:
    with uow:
        reader = uow.journal
        if not hasattr(reader, "writeoff_lines"):
            return journal_page(reader.audit_events(), reader.writeoff_entries(), query, limit)
        if str(query or "").split():
            # A search matches the rendered row text, so every row is read; light rows, no entities.
            return journal_page(reader.audit_events(), reader.writeoff_lines(), query, limit)
        # No search: the newest `limit` rows of the merged list come from the newest `limit` of each stream (both
        # already newest first, the sort is stable), and the total is two counts.
        page = journal_page(reader.audit_events(limit), reader.writeoff_lines(limit), "", limit)
        total = reader.totals()
        return JournalPage(rows=page.rows, total=total, truncated=max(0, total - limit))
