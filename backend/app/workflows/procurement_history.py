"""«История строки» (option procurement.history): the life of one procurement line, rebuilt on read from stored facts.

Nothing is written for it. Sources: the request, the line's own columns and audit events, the request for quotes
(lines, suppliers marked sent, quotes, decision), receiving (deliveries of the line and their audit), the warehouse lot
that came out of it (journal and audit), backorder `-Д` lines. A fact that has no stored timestamp (an expected date set
before audit existed, an imported order) gets `at: null` and says «дата не хранится» — dates are never invented.
Read-only, one session; the workflow is the only place where procurement meets the other modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    QuoteLineRow,
    QuoteRow,
    RfqLineRow,
    RfqRow,
    RfqSupplierRow,
    SupplierRow,
)
from app.modules.purchasing.offers import cover, read_tiers
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.requests.infrastructure import RequestRow
from app.modules.warehouse.infrastructure import StockLotRow, WriteOffEntryRow
from app.shared.persistence import AuditEventRow

UNDATED = "дата не хранится"


def _join(*parts: object) -> str:
    return "; ".join(str(part) for part in parts if part not in (None, ""))


@dataclass
class _Event:
    at: datetime | date | None
    title: str
    details: str = ""
    who: str | None = None
    source: str = ""
    order: int = 0  # tie-break inside one moment: the order events were collected in

    def key(self) -> tuple:
        if self.at is None:
            return (1, datetime.min, self.order)
        moment = self.at if isinstance(self.at, datetime) else datetime(self.at.year, self.at.month, self.at.day)
        return (0, moment.replace(tzinfo=None), self.order)

    def result(self) -> dict:
        return {
            "at": self.at.isoformat() if self.at is not None else None,
            "time": isinstance(self.at, datetime),
            "title": self.title,
            "details": self.details if self.at is not None else _join(self.details, UNDATED),
            "who": self.who,
            "source": self.source,
        }


def _num(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _money(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        text = f"{Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError):
        return str(value)
    return text.replace(",", " ").replace(".", ",") + " ₽"


def _day(value: Any) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def _status_move(details: dict) -> str:
    before, after = details.get("from_status"), details.get("to_status")
    return f"этап «{before}» → «{after}»" if before and after and before != after else ""


def _procurement_event(kind: str, d: dict) -> tuple[str, str]:
    """(title, details) of one `procurement.*` audit event."""
    move = _status_move(d)
    match kind:
        case "procurement.created":
            return "Передана в закупку", "строка появилась в очереди закупки"
        case "procurement.taken_in_work":
            return "Взята в работу", move
        case "procurement.autofilled":
            return "Заполнено по ссылке", "название на сайте, изготовитель, страна"
        case "procurement.facts_saved":
            return "Данные заполнены", move
        case "procurement.supplier_request_sent":
            return "Запрос поставщику отправлен", _join(f"заказ № {d['order_number']}" if d.get("order_number") else "", move)
        case "procurement.expected_arrival_set":
            previous = _day(d.get("previous_expected_arrival"))
            now = _day(d.get("expected_arrival"))
            return "Ожидаемая дата поставки", _join(f"{previous} → {now}" if previous else now, move)
        case "procurement.actual_arrival_set":
            return "Фактическая дата поставки", _join(_day(d.get("actual_arrival")) or "снята", move)
        case "procurement.actual_arrival":
            return "Фактическая дата из приёмки", ""
        case "procurement.invoice_recorded":
            return "Счёт внесён", move
        case "procurement.invoice_file_uploaded":
            return "Файл счёта загружен", ""
        case "procurement.invoice_replaced":
            return "Счёт заменён", move
        case "procurement.document_file_uploaded":
            return "Документ к заказу загружен", ""
        case "procurement.money_saved" | "procurement.fact_price_saved" | "procurement.kp_saved":
            title = "КП внесено" if kind.endswith("kp_saved") else "Цена и сумма сохранены"
            return title, _join(
                f"цена с НДС {_money(d.get('price_with_vat'))}" if d.get("price_with_vat") else "",
                f"сумма {_money(d.get('sum'))}" if d.get("sum") else "", move)
        case "procurement.rate_reconciled":
            return "Курс пересчитан", _join(f"курс {_num(d.get('exchange_rate'))}", f"сумма {_money(d.get('sum'))}")
        case "procurement.paid":
            rate = (
                f"{d.get('source_currency')}: курс {_num(d.get('exchange_rate'))} на {_day(d.get('exchange_rate_date'))}"
                if d.get("source_currency") else ""
            )
            return "Оплачено", _join(f"сумма {_money(d.get('sum'))}" if d.get("sum") else "", rate, move)
        case "procurement.receipt_stage":
            return "Этап по приёмке", move
        case "procurement.backorder_created":
            return "Создана как недопоставка", _join(
                f"остаток по {d['from_legacy_id']}" if d.get("from_legacy_id") else "", _num(d.get("quantity")))
        case "procurement.backorder_confirmed":
            return "Недопоставка подтверждена", move
        case "procurement.backorder_cancelled":
            return "Недопоставка отменена", move
        case "procurement.stock_covered":
            lot = f"ГРН {d['grn']}" + (f"/{d['package']}" if d.get("package") else "") if d.get("grn") else ""
            return "Закрыто со склада", _join(lot, _num(d.get("quantity")))
        case "procurement.stock_cover_released":
            return "«Со склада» снято", _join(f"ГРН {d['grn']}" if d.get("grn") else "")
        case "procurement.traceability_saved":
            return "Прослеживаемость сохранена", ""
        case "procurement.note_saved":
            return "Комментарий изменён", ""
        case "procurement.replacement_set":
            return "Решение по замене", _join(d.get("decision"))
        case "procurement.split":
            return "Строка раздроблена", ""
        case "procurement.approval_requested":
            return "Отправлена на согласование", move
        case "procurement.approved":
            return "Согласована, возобновлена", move
        case "procurement.cancelled":
            return "Отменена", move
        case "procurement.line_deleted":
            return "Строка удалена", ""
    return kind.removeprefix("procurement."), move


_RECEIVING_TITLES = {
    "receiving.expected": "Ожидается поставка",
    "receiving.sent_to_warehouse": "Передано на склад",
    "receiving.auto_quarantined": "Автоматически в карантин",
    "receiving.returned_to_supplier": "Возврат поставщику",
    "receiving.placed": "Размещено на складе",
    "receiving.unpacked": "Партия разобрана",
    "receiving.vk_archived": "Входной контроль в архив",
    "receiving.vk_revision_opened": "Открыт повторный входной контроль",
    "receiving.document_attached": "Приложен документ",
    "receiving.file_attached": "Приложен файл",
}
_LOT_TITLES = {
    "warehouse.received": "Принято на склад",
    "warehouse.manually_received": "Принято на склад вручную",
    "warehouse.location_moved": "Перемещено",
    "warehouse.location_corrected": "Место исправлено",
    "warehouse.row_edited": "Строка склада исправлена",
    "warehouse.identity_edited": "Данные позиции исправлены",
    "warehouse.reserved_for_procurement": "Зарезервировано под закупку",
    "warehouse.procurement_reserve_released": "Резерв закупки снят",
}
_JOURNAL_TITLES = {
    "writeoff": "Списание", "return": "Возврат на склад", "issue": "Выдача", "legacy": "Списание (старый журнал)",
    "supplier_return": "Возврат поставщику",
}


def _actor(row: AuditEventRow) -> str | None:
    name = (row.actor_name or "").strip()
    system = (row.actor_department or "").strip().lower() in {"system", "система"}
    return None if not name or system else name


def line_history(session: Session, line_id: UUID) -> dict | None:
    """The events of one line, oldest first (undated ones last); None when the line does not exist."""
    line = session.get(ProcurementLineRow, line_id)
    if line is None:
        return None
    events: list[_Event] = []

    def add(at, title, details="", who=None, source=""):
        events.append(_Event(at, title, details, who, source, len(events)))

    users: dict[UUID, str] = {}

    def user(user_id: UUID | None) -> str | None:
        if user_id is None:
            return None
        if user_id not in users:
            found = session.get(IdentityUserRow, user_id)
            users[user_id] = found.name if found else ""
        return users[user_id] or None

    # 1. The request.
    request = session.get(RequestRow, line.source_request_id)
    if request is not None:
        add(request.created_at, f"Заявка {request.legacy_id} создана",
            _join(request.name, f"{_num(request.quantity)} {request.unit}", request.project_reference or request.purpose),
            request.actor_name, "заявка")

    # 2. The line's own audit.
    audit = list(session.scalars(
        select(AuditEventRow).where(AuditEventRow.aggregate_id == line.id).order_by(AuditEventRow.occurred_at)
    ))
    seen = {row.event_type for row in audit}
    for row in audit:
        title, details = _procurement_event(row.event_type, row.details or {})
        add(row.occurred_at, title, details, _actor(row), "закупка")

    # 3. Facts that audit does not cover (data imported or written before the trail existed): dated by the fact
    # itself when it is a date, else undated.
    if line.order_number and "procurement.supplier_request_sent" not in seen:
        add(line.order_date, "Запрос поставщику отправлен", f"заказ № {line.order_number}", None, "закупка")
    if (line.invoice_number or line.invoice_date or line.invoice_url) and "procurement.invoice_recorded" not in seen:
        add(line.invoice_date, "Счёт внесён", f"№ {line.invoice_number}" if line.invoice_number else "", None, "закупка")
    if line.paid_at is not None and "procurement.paid" not in seen:
        add(line.paid_at, "Оплачено", _money(line.actual_sum), None, "закупка")
    if line.expected_arrival and not seen & {"procurement.expected_arrival_set", "procurement.invoice_recorded"}:
        add(None, "Ожидаемая дата поставки", _day(line.expected_arrival), None, "закупка")
    if line.actual_arrival and not seen & {"procurement.actual_arrival_set", "procurement.actual_arrival"}:
        add(line.actual_arrival, "Фактическая дата поставки", "", None, "закупка")
    if line.supplier_request_reference and "procurement.supplier_request_sent" not in seen:
        add(None, "Номер запроса поставщику", line.supplier_request_reference, None, "закупка")

    # 4. Requests for quotes, 5. receiving and the lot, 6. backorders `-Д…` of this line.
    _rfq_events(session, line.id, add, user)
    _receiving_events(session, line.id, add)
    if line.legacy_id:
        for child in session.scalars(select(ProcurementLineRow).where(
            ProcurementLineRow.legacy_id.like(f"{line.legacy_id}-Д%"), ProcurementLineRow.id != line.id,
        )):
            qty = child.actual_quantity if child.actual_quantity is not None else child.calculated_quantity
            add(child.created_at, f"Недопоставка: строка {child.legacy_id}",
                _join(f"{_num(qty)} {child.actual_unit or child.calculated_unit}", f"этап «{child.status}»"), None, "недопоставка")

    events.sort(key=_Event.key)
    return {
        "line": {"id": str(line.id), "legacy_id": line.legacy_id, "name": line.invoice_item_name or line.calculated_name,
                 "status": line.status},
        "events": [event.result() for event in events],
    }


def _rfq_events(session: Session, line_id: UUID, add, user) -> None:
    for rfq_line in session.scalars(
        select(RfqLineRow).where(RfqLineRow.procurement_line_id == line_id).order_by(RfqLineRow.created_at)
    ):
        rfq = session.get(RfqRow, rfq_line.rfq_id)
        if rfq is None:
            continue
        budget = f"бюджет {_money(rfq_line.budget_unit_gross)} за ед. с НДС" if rfq_line.budget_unit_gross else ""
        add(rfq_line.created_at, f"Включена в запрос КП {rfq.number}",
            _join(f"{_num(rfq_line.quantity)} {rfq_line.unit}", budget), user(rfq.created_by), "запрос КП")
        for rfq_supplier, supplier in session.execute(
            select(RfqSupplierRow, SupplierRow).join(SupplierRow, SupplierRow.id == RfqSupplierRow.supplier_id)
            .where(RfqSupplierRow.rfq_id == rfq.id)
        ):
            if rfq_supplier.sent_at is not None:
                add(rfq_supplier.sent_at, f"Запрос КП отправлен: {supplier.name}", rfq.number, user(rfq.buyer_id), "запрос КП")
            if rfq_supplier.declined_at is not None:
                add(rfq_supplier.declined_at, f"Поставщик отказал: {supplier.name}", rfq_supplier.decline_note or "", None, "запрос КП")
        decision = session.scalar(select(DecisionRow).where(DecisionRow.rfq_line_id == rfq_line.id))
        for quote_line, quote, supplier in session.execute(
            select(QuoteLineRow, QuoteRow, SupplierRow).join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
            .join(SupplierRow, SupplierRow.id == QuoteRow.supplier_id)
            .where(QuoteLineRow.rfq_line_id == rfq_line.id).order_by(QuoteRow.created_at)
        ):
            if quote_line.declined:
                add(quote.created_at, f"КП от {supplier.name}: отказ", "", user(quote.created_by), "запрос КП")
                continue
            covered = cover(
                Decimal(rfq_line.quantity), price=quote_line.unit_price, vat_included=quote_line.vat_included,
                vat_rate=Decimal(quote_line.vat_rate), moq=quote_line.moq, pack=quote_line.pack,
                tiers=read_tiers(quote_line.tiers),
            )
            add(quote.created_at, f"КП получено: {supplier.name}",
                _join(f"{_money(covered.unit_gross)} за ед. с НДС",
                      f"на {_num(covered.quantity)} {rfq_line.unit} — {_money(covered.total_gross)}",
                      f"срок {quote_line.lead_days} дн." if quote_line.lead_days is not None else "",
                      "аналог" if quote_line.analog else "", f"получено {_day(quote.received_on)}"),
                user(quote.created_by), "запрос КП")
        if decision is not None:
            chosen = session.execute(
                select(SupplierRow.name).join(QuoteRow, QuoteRow.supplier_id == SupplierRow.id)
                .join(QuoteLineRow, QuoteLineRow.quote_id == QuoteRow.id).where(QuoteLineRow.id == decision.quote_line_id)
            ).scalar()
            add(decision.decided_at, f"Выбран поставщик: {chosen or '—'}",
                _join(f"{_num(decision.cover_quantity)} {rfq_line.unit}", f"{_money(decision.unit_gross)} за ед. с НДС",
                      f"итого {_money(decision.total_gross)}", f"КП с ценой: {decision.priced_quotes}",
                      f"не самое дешёвое: {decision.not_cheapest_reason}" if decision.not_cheapest_reason else "",
                      f"мало КП: {decision.gate_override_reason}" if decision.gate_override_reason else "",
                      f"аналог одобрил {decision.analog_approved_by}" if decision.analog_approved_by else ""),
                user(decision.decided_by), "запрос КП")
        elif rfq_line.removed_at is not None:
            add(rfq_line.removed_at, f"Выведена из запроса {rfq.number}", "решения не было", None, "запрос КП")


def _receiving_events(session: Session, line_id: UUID, add) -> None:
    deliveries = list(session.scalars(
        select(ReceivingLineRow).where(ReceivingLineRow.procurement_line_id == line_id).order_by(ReceivingLineRow.created_at)
    ))
    if not deliveries:
        return
    by_id = {row.id: row for row in deliveries}
    for row in session.scalars(
        select(AuditEventRow).where(AuditEventRow.aggregate_id.in_(list(by_id))).order_by(AuditEventRow.occurred_at)
    ):
        delivery = by_id[row.aggregate_id]
        if row.event_type == "receiving.inspected":
            lot = f"ГРН {delivery.grn}" + (f"/{delivery.package}" if delivery.package else "") if delivery.grn else ""
            accepted = f"принято {_num(delivery.accepted_quantity)} {delivery.unit}" if delivery.accepted_quantity is not None else ""
            backorder = f"недопоставка {_num(delivery.backorder_quantity)}" if delivery.backorder_quantity else ""
            add(row.occurred_at, f"Приёмка: {delivery.verdict}" if delivery.verdict else "Приёмка проведена",
                _join(_status_move(row.details or {}), lot, accepted, backorder,
                      f"УПД {delivery.document_number}" if delivery.document_number else ""),
                _actor(row), "приёмка")
        elif row.event_type in _RECEIVING_TITLES:
            add(row.occurred_at, _RECEIVING_TITLES[row.event_type], delivery.legacy_id, _actor(row), "приёмка")
    # The lots that came out of these deliveries: journal movements and the lot's own audit.
    keys = {(row.warehouse, row.grn, row.package) for row in deliveries if row.grn}
    lots: list[StockLotRow] = []
    for warehouse, grn, package in keys:
        lots += session.scalars(select(StockLotRow).where(
            StockLotRow.warehouse == warehouse, StockLotRow.grn == grn, StockLotRow.package == package))
    for lot in lots:
        label = f"ГРН {lot.grn}" + (f"/{lot.package}" if lot.package else "")
        for entry in session.scalars(
            select(WriteOffEntryRow).where(WriteOffEntryRow.lot_id == lot.id).order_by(WriteOffEntryRow.occurred_at)
        ):
            remaining = f"остаток {_num(entry.remaining_after)}" if entry.remaining_after is not None else ""
            add(entry.occurred_at, f"{_JOURNAL_TITLES.get(entry.kind, entry.kind)}: {_num(entry.quantity)} {entry.unit}",
                _join(label, entry.reason, entry.purpose, remaining), entry.actor_name or entry.author or None, "склад")
        for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == lot.id, AuditEventRow.event_type.in_(list(_LOT_TITLES)))
            .order_by(AuditEventRow.occurred_at)
        ):
            d = row.details or {}
            if d.get("procurement_line_id") not in (None, "", str(line_id)):
                continue  # a reserve held for another line
            add(row.occurred_at, _LOT_TITLES[row.event_type], _join(label, d.get("holder"), d.get("quantity")), _actor(row), "склад")
