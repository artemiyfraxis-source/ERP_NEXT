"""Roller_Buyer → ERP: the buyer's procurement flow — requests, requests for quotes, quotes, decisions, orders and the
purchasing settings (stage of Витя, 21.10.2026). The reference data it leans on (suppliers, items, the UPD registry,
Kaiten invoices with their files) comes first from `app.modules.purchasing.rb_import`.

    ERP_DATABASE_URL=… python -m app.workflows.rb_procurement_import --snapshot <copy of buyer.db>
        --data-dir <Roller_Buyer DATA_DIR> --buyer vitya [--apply]

`--snapshot` is a COPY of Roller_Buyer's database, opened read-only (the live `Roller_Buyer-data\\buyer.db` is
refused); `--data-dir` is read for the quote PDFs (files\\quotes) and the Kaiten invoice files (kaiten\\). Without
`--apply` the whole run is one transaction that is rolled back: the counts say what would be written.

What becomes what. Every row this workflow writes itself has a stable id (uuid5 of the Roller_Buyer table and id), so a
re-run finds it; what ERP's own commands write on the way (their audit, the order letter, the invoice file, the
receiving outbox) is theirs, and a re-run never repeats a step a line has already passed.
* request line → a request of the requests module (one position; the lines of one RB request share one НАЗНАЧЕНИЕ
  «<project>.NNN», so «Заявки» shows them as one заявка), handed to procurement at once, and its procurement line
  taken by the buyer — built by the requests and procurement domain factories, audited as ERP audits them, at RB's own
  moments. № ПРОЕКТА = the RB project's number (customer and name stay in RB: ERP's project registry is the production
  plan's import, there is no command to add a project to it);
* rfq / rfq line / rfq supplier / quote / quote line / decision → purchasing_rfqs … purchasing_decisions (the quote PDF
  into purchasing_document_files), the decision written into the line as the RFQ bridge writes it (`save_procurement_
  facts`: ПОСТАВЩИК, КОЛ-ВО, ЕД.ИЗМ → «Готов к запросу»);
* order → the procurement commands a buyer runs: «Сформировать запрос» (ERP's own № ЗАКАЗА PO-NNN, «Жду счет»),
  «Внести КП» with the order's prices and the Kaiten invoice file («В оплату»), «Оплачено» («В пути»), the order-wide
  ОЖИД. ДАТА when RB rescheduled it (the audit keeps the promised date — `purchasing_orders` reads «обещано» from it),
  then the order facts of «Счета и поставки» (в оплату, отгружен, трек, доставка, заметка);
* cancelled → «Отменено» with RB's reason; «Давальческое» (the customer supplies it) → «Отменено» with that reason;
  «Со склада» → stays «В работе» with a note: ERP's «Со склада» is a reserve on a lot the buyer picks himself;
* settings → purchasing_settings (RB's stored values over RB's defaults).

ERP's word wins. Each line run here gets an audit event `procurement.rb_imported` with the version the import left it
at. On a re-run a line whose version moved since (the buyer or ERP changed it) is left alone and reported; an untouched
line is carried forward to RB's newer state (a line RB took out of its request since is removed here too). Existing
request-for-quotes rows are never rewritten (a quote is never edited in RB either); only an empty «отправлен» is filled.
A line with no «нужно к» in RB gets ERP's default ПЛАНОВАЯ ДАТА: the request day + 14."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, event, func, inspect, select
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.application import (
    BuildSupplierRequestsCommand,
    KpRow,
    OrderLineRef,
    PaySupplierOrderCommand,
    SaveKpCommand,
    SaveProcurementFactsCommand,
    SetNoteCommand,
    SetOrderExpectedArrivalCommand,
    UploadInvoiceFileCommand,
    build_supplier_requests,
    pay_supplier_order,
    save_kp,
    save_procurement_facts,
    set_order_expected_arrival,
    set_procurement_note,
    upload_invoice_file,
)
from app.modules.procurement.domain import (
    FACTS_EDITABLE,
    KpEntry,
    MoneyEntry,
    ProcurementFacts,
    ProcurementLine,
    ProcurementStatus,
    create_line_from_requested,
    next_system_id,
)
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.money import MoneyInput
from app.modules.procurement.requests_application import CancelManyCommand, CancelManyItem, cancel_procurement_lines
from app.modules.purchasing import items as purchasing_items
from app.modules.purchasing import orders as purchasing_orders
from app.modules.purchasing import settings as purchasing_settings
from app.modules.purchasing.decision_reasons import GATE_OVERRIDE_REASONS, NOT_CHEAPEST_REASONS
from app.modules.purchasing.doc_refs import norm_doc_number
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    DocumentFileRow,
    LineItemRow,
    OrderFactRow,
    QuoteLineRow,
    QuoteRow,
    RfqDeclinedSuggestionRow,
    RfqLineRow,
    RfqRow,
    RfqSupplierRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierRow,
)
from app.modules.purchasing.offers import (
    Offer,
    baseline,
    cheapest,
    cover,
    current_offers,
    gate_status,
    gross_unit,
    read_tiers,
)
from app.modules.purchasing.rb_import import ACTOR as IMPORT_ACTOR
from app.modules.purchasing.rb_import import open_snapshot, resolve_items, resolve_suppliers
from app.modules.purchasing.rfq import next_number as next_rfq_number
from app.modules.requests.application import _procurement_requested_payload
from app.modules.requests.domain import (
    COMMERCIAL_PURPOSE,
    RequestDraft,
    build_procurement_requested_snapshot,
    create_request_batch,
    normalize_project_reference,
    project_reference_valid,
)
from app.modules.requests.infrastructure import RequestRow, SqlAlchemyUnitOfWork as SqlAlchemyRequestsUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, Base, SqlAlchemyOutboxRepository
from app.workflows.purchasing_orders import order_lines as order_line_facts

MSK = ZoneInfo("Europe/Moscow")
IMPORTED = "procurement.rb_imported"
_NS = "roller-buyer-flow"
_UNITS = {"m": "м", "pcs": "шт", "pack": "уп", "kg": "кг", "l": "л", "set": "компл"}
_QUOTE_SOURCES = {"manual": "manual", "excel": "paste", "google": "file"}
_EPSILON = Decimal("0.000000001")
TOLLING_REASON = "Давальческое: поставляет заказчик (Roller_Buyer)"
CANCEL_FALLBACK = "Отменено в Roller_Buyer"
STOCK_NOTE = "Roller_Buyer: со склада — в ERP поставьте «Со склада» на партию"
# RB settings (packages/shared/src/settings.ts DEFAULT_SETTINGS) → «Настройки закупки»; money in kopecks there.
RB_SETTINGS = {
    "gateMinQuotes": ("gate_min_quotes", 3, 1),
    "defaultVatRate": ("default_vat_rate", 22, 1),
    "quoteWaitDays": ("quote_wait_days", 3, 1),
    "deliveryWarnDays": ("delivery_warn_days", 3, 1),
    "boardOrderDays": ("board_order_days", 5, 1),
    "boardLeadDays": ("board_lead_days", 14, 1),
    "rfqDeliveryDefault": ("delivery_default", 150_000, 100),
}


def _id(kind: str, value: Any) -> UUID:
    """Stable id of a row made from a Roller_Buyer row: the same id on every re-run."""
    return uuid5(NAMESPACE_URL, f"{_NS}:{kind}:{value}")


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _noon(day: date, minutes: int = 0) -> datetime:
    """A day of Roller_Buyer (it keeps dates only) as a moment: noon in Moscow, `minutes` later for the next step."""
    return datetime.combine(day, time(12), MSK) + timedelta(minutes=minutes)


def _not_before(moment: datetime, floor: datetime | None) -> datetime:
    """The step after `floor` on the same day; a step RB dated on an earlier day keeps its day (dates are facts)."""
    if floor is None or moment >= floor or floor.astimezone(MSK).date() != moment.astimezone(MSK).date():
        return moment
    return floor


def _utc(value: Any, fallback: datetime) -> datetime:
    """`datetime('now')` of SQLite: UTC without a zone."""
    if not value:
        return fallback
    try:
        moment = datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _rub(kopecks: Any) -> Decimal | None:
    return None if kopecks is None else (Decimal(int(kopecks)) / 100).quantize(Decimal("0.01"))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _positive(value: Any) -> Decimal | None:
    number = _dec(value)
    return number if number is not None and number > 0 else None


# ---- the one transaction ---------------------------------------------------------------------------------------


def enable_savepoints(engine: Engine) -> Engine:
    """pysqlite opens its own transactions and breaks SAVEPOINT; SQLAlchemy's recipe hands that back to SQLAlchemy,
    so a failed RB request rolls back alone (`begin_nested`) inside the import's one transaction."""
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _connect(dbapi_connection, _record):  # noqa: ANN001
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(connection):  # noqa: ANN001
            connection.exec_driver_sql("BEGIN")
    return engine


class _Joined:
    """A module's unit of work on the import's one session: its commit only flushes, its exit leaves the transaction
    to the import (one commit with --apply, one rollback without)."""

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def commit(self) -> None:
        self.session.flush()
        self.committed = True


class _ProcurementUow(_Joined, SqlAlchemyProcurementUnitOfWork):
    pass


class _RequestsUow(_Joined, SqlAlchemyRequestsUnitOfWork):
    pass


class _PurchasingUow(_Joined, SqlAlchemyPurchasingUnitOfWork):
    pass


# ---- the snapshot ----------------------------------------------------------------------------------------------


@dataclass
class RbLine:
    id: int
    request_id: int
    item_id: int
    quantity: Decimal
    need_by: date | None
    supply: str
    budget: int | None
    plan_source: str
    cancelled_at: date | None
    cancel_reason: str
    requote_at: date | None
    requote_note: str
    rfq_line: int | None = None
    quotes: int = 0
    decision: int | None = None
    order_line: int | None = None
    order: int | None = None
    stock_in: Decimal = Decimal("0")
    good: Decimal = Decimal("0")
    status: str = "new"


def derive_status(line: RbLine, order: dict | None, order_line: dict | None) -> str:
    """Roller_Buyer's `deriveLineStatus` (domain/status.ts) over `loadLineStatuses`' facts: the furthest fact wins."""
    if line.cancelled_at is not None:
        return "cancelled"
    if line.supply in ("tolling", "stock"):
        if line.stock_in >= line.quantity - _EPSILON:
            return "delivered"
        if line.stock_in > 0:
            return "partial"
        return "tolling" if line.supply == "tolling" else "from_stock"
    if order_line is not None and order is not None:
        ordered = Decimal(str(order_line["quantity"]))
        if order_line["short_closed_at"]:
            ordered = min(ordered, line.good)
        if line.good >= ordered - _EPSILON:
            return "delivered"
        if line.good > 0:
            return "partial"
        if order["shipped_at"]:
            return "shipped"
        if order["paid_at"]:
            return "paid"
        return "to_pay" if order["sent_to_pay_at"] else "ordered"
    if line.decision is not None:
        return "decided"
    if line.quotes > 0:
        return "quoted"
    if line.rfq_line is not None:
        return "in_rfq"
    return "requote" if line.requote_at is not None else "new"


class Snapshot:
    def __init__(self, connection: sqlite3.Connection):
        self.db = connection
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
        self.tables = tables
        self.settings = {row["key"]: row["value"] for row in self.rows("settings")}
        self.projects = {row["id"]: dict(row) for row in self.rows("projects")}
        self.requests = {row["id"]: dict(row) for row in self.rows("requests")}
        self.items = {row["id"]: dict(row) for row in self.rows("items")}
        self.suppliers = {row["id"]: dict(row) for row in self.rows("suppliers")}
        self.rfqs = {row["id"]: dict(row) for row in self.rows("rfqs")}
        self.rfq_lines = {row["id"]: dict(row) for row in self.rows("rfq_lines")}
        self.rfq_suppliers = [dict(row) for row in self.rows("rfq_suppliers")]
        self.quotes = {row["id"]: dict(row) for row in self.rows("quotes")}
        self.quote_lines = {row["id"]: dict(row) for row in self.rows("quote_lines")}
        self.decisions = {row["id"]: dict(row) for row in self.rows("decisions")}
        self.orders = {row["id"]: dict(row) for row in self.rows("orders")}
        self.order_lines = {row["id"]: dict(row) for row in self.rows("order_lines")}
        self.invoices = [dict(row) for row in self.rows("invoices")]
        receipts: dict[int, Decimal] = defaultdict(Decimal)
        for row in self.rows("receipts"):
            receipts[row["order_line_id"]] += Decimal(str(row["received"])) - Decimal(str(row["defect"] or 0))
        stock_in: dict[int, Decimal] = defaultdict(Decimal)
        if "stock_moves" in tables:
            for row in connection.execute(
                "select request_line_id, qty from stock_moves where request_line_id is not null and qty > 0 "
                "and kind in ('tolling', 'transfer')"
            ):
                stock_in[row["request_line_id"]] += Decimal(str(row["qty"]))
        rfq_line_of = {row["request_line_id"]: row_id for row_id, row in self.rfq_lines.items()}
        quotes_of = Counter(row["rfq_line_id"] for row in self.quote_lines.values())
        decision_of = {row["rfq_line_id"]: row_id for row_id, row in self.decisions.items()}
        order_line_of = {row["decision_id"]: row_id for row_id, row in self.order_lines.items()}
        self.lines: dict[int, RbLine] = {}
        for row in self.rows("request_lines"):
            line = RbLine(
                id=row["id"], request_id=row["request_id"], item_id=row["item_id"], quantity=Decimal(str(row["quantity"])),
                need_by=_day(row["need_by"]), supply=row["supply"] or "buy", budget=row["budget_unit_gross"],
                plan_source=_clean(row["plan_source"]), cancelled_at=_day(row["cancelled_at"]),
                cancel_reason=_clean(row["cancel_reason"]), requote_at=_day(row["requote_at"]),
                requote_note=str(row["requote_note"] or "").strip(), stock_in=stock_in.get(row["id"], Decimal("0")),
            )
            line.rfq_line = rfq_line_of.get(line.id)
            if line.rfq_line is not None:
                line.quotes = quotes_of.get(line.rfq_line, 0)
                line.decision = decision_of.get(line.rfq_line)
            if line.decision is not None:
                line.order_line = order_line_of.get(line.decision)
            order_line = self.order_lines.get(line.order_line) if line.order_line is not None else None
            if order_line is not None:
                line.order = order_line["order_id"]
                line.good = receipts.get(line.order_line, Decimal("0"))
            line.status = derive_status(line, self.orders.get(line.order) if line.order else None, order_line)
            self.lines[line.id] = line

    def rows(self, table: str) -> list[sqlite3.Row]:
        if table not in self.tables:
            return []
        return self.db.execute(f"select * from {table} order by 1").fetchall()


# ---- the report ------------------------------------------------------------------------------------------------


@dataclass
class Report:
    counts: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    skipped: list[str] = field(default_factory=list)
    differs: list[str] = field(default_factory=list)  # where ERP shows the line otherwise than RB, and why
    notes: list[str] = field(default_factory=list)
    tables: dict[str, tuple[int, int]] = field(default_factory=dict)

    def add(self, key: str, amount: int = 1) -> None:
        self.counts[key] += amount

    def skip(self, text: str) -> None:
        self.skipped.append(text)

    def lines(self) -> list[str]:
        out = ["== строки таблиц ERP (было → стало)"]
        out += [f"{name}: {before} → {after} ({after - before:+d})" for name, (before, after) in sorted(self.tables.items())
                if before != after]
        out.append("== что сделано")
        out += [f"{key}: {value}" for key, value in sorted(self.counts.items())]
        out.append("== статус RB → этап ERP (строк)")
        out += [f"{rb} → {erp}: {value}" for (rb, erp), value in sorted(self.statuses.items())]
        if self.differs:
            out.append(f"== отличия от RB ({len(self.differs)})")
            out += self.differs
        if self.notes:
            out.append("== заметки")
            out += self.notes
        out.append(f"== пропущено ({len(self.skipped)})")
        out += self.skipped
        return out


# ---- the importer ----------------------------------------------------------------------------------------------


class ProcurementFlowImporter:
    def __init__(self, session: Session, rb: Snapshot, data_dir: Path, buyer: Actor, now: datetime):
        self.session, self.rb, self.data_dir, self.buyer, self.now = session, rb, data_dir, buyer, now
        self.report = Report()
        self.suppliers: dict[int, UUID | None] = {}
        self.items: dict[int, UUID] = {}
        self.line_ids: dict[int, UUID] = {}  # RB request line → ERP procurement line (existing or made now)
        self.ours: set[UUID] = set()  # lines this run may move: made now, or untouched in ERP since the last run
        self.touched: set[UUID] = set()
        self.stamps: dict[UUID, dict] = {}  # the last `procurement.rb_imported` of each line
        self.files: dict[str, UUID] = {}

    # units of work on the one session
    def procurement(self) -> _ProcurementUow:
        return _ProcurementUow(lambda: self.session)

    def requests(self) -> _RequestsUow:
        return _RequestsUow(lambda: self.session)

    def purchasing(self) -> _PurchasingUow:
        return _PurchasingUow(lambda: self.session)

    def line(self, line_id: UUID) -> ProcurementLine | None:
        with self.procurement() as uow:
            return uow.procurement.get(line_id)

    def audit(self, aggregate_id: UUID, event_type: str, actor: Actor, at: datetime, details: dict, key: str) -> None:
        self.session.add(AuditEventRow(
            id=_id("audit", f"{event_type}:{key}"), aggregate_id=aggregate_id, event_type=event_type, actor_id=actor.id,
            actor_name=actor.name, actor_department=actor.department, occurred_at=at,
            details={name: str(value) for name, value in details.items()},
        ))

    def unit(self, label: str, work: Callable[[], None]) -> bool:
        """One RB request / request for quotes / order on its own savepoint: a refusal rolls back only it."""
        counts = Counter(self.report.counts)
        try:
            with self.session.begin_nested():
                work()
        except Exception as error:  # noqa: BLE001 — every refusal is reported with its reason, the rest goes on
            self.report.skip(f"{label}: {error}")
            self.report.counts = counts
            self.session.info.pop("_read_versions", None)  # app.shared.persistence: versions read before the rollback
            for rb_id, line_id in list(self.line_ids.items()):
                if self.session.get(ProcurementLineRow, line_id) is None:
                    del self.line_ids[rb_id]
                    self.ours.discard(line_id)
                    self.touched.discard(line_id)
            return False
        return True

    # ---- run

    def run(self) -> Report:
        self.suppliers = resolve_suppliers(self.session, self.rb.db)
        self.items = resolve_items(self.session, self.rb.db)
        before = self.table_counts()
        self.settings()
        self.find_existing()
        for request_id in sorted(self.rb.requests):
            self.unit(f"заявка RB {self.rb.requests[request_id]['number']}", lambda r=request_id: self.request(r))
        self.unit("строки, убранные из запросов КП в RB", self.withdraw_gone)
        for rfq_id in sorted(self.rb.rfqs):
            self.unit(f"запрос КП RB № {rfq_id}", lambda r=rfq_id: self.rfq(r))
        for decision_id in sorted(self.rb.decisions):
            self.unit(f"решение RB {decision_id}", lambda d=decision_id: self.decision(d))
        # ERP numbers orders as they are formed: RB's in the order they were placed.
        for order_id in sorted(self.rb.orders, key=lambda key: (str(self.rb.orders[key]["order_date"] or ""), key)):
            order = self.rb.orders[order_id]
            self.unit(f"заказ RB № {order['number']}", lambda o=order_id: self.order(o))
        for line in sorted(self.rb.lines.values(), key=lambda item: item.id):
            if line.id in self.line_ids:
                self.unit(f"строка RB {line.id}", lambda item=line: self.closing(item))
        self.stamp()
        self.session.flush()
        after = self.table_counts()
        self.report.tables = {name: (before.get(name, 0), after.get(name, 0)) for name in after}
        return self.report

    def table_counts(self) -> dict[str, int]:
        present = set(inspect(self.session.connection()).get_table_names())
        return {table.name: int(self.session.scalar(select(func.count()).select_from(table)) or 0)
                for table in Base.metadata.sorted_tables if table.name in present}

    # ---- settings

    def settings(self) -> None:
        current = purchasing_settings.load(self.session)
        changes: dict[str, Any] = {}
        for rb_key, (key, default, scale) in RB_SETTINGS.items():
            raw = self.rb.settings.get(rb_key)
            try:
                value = json.loads(raw) if raw is not None else default
                value = Decimal(str(value)) / scale
            except (ValueError, TypeError, ArithmeticError):
                self.report.notes.append(f"настройка RB {rb_key}: не число ({raw}) — взято {default}")
                value = Decimal(default) / scale
            shown = int(value) if value == value.to_integral_value() else value
            if Decimal(str(current[key])) != value:
                changes[key] = shown
                self.report.notes.append(f"настройка «{purchasing_settings.BY_KEY[key].label}»: {current[key]} → {shown}")
            else:
                self.report.notes.append(f"настройка «{purchasing_settings.BY_KEY[key].label}»: {shown} (как было)")
        if changes:
            purchasing_settings.save(self.session, changes, self.buyer.id, self.now)
            self.report.add("настройки закупки: изменены", len(changes))
        other = sorted(set(self.rb.settings) - set(RB_SETTINGS))
        if other:
            self.report.notes.append(f"настройки RB не про закупку, не перенесены: {', '.join(other)}")

    # ---- what earlier runs left

    def find_existing(self) -> None:
        stamps: dict[UUID, tuple[datetime, dict]] = {}
        for aggregate_id, occurred_at, details in self.session.execute(
            select(AuditEventRow.aggregate_id, AuditEventRow.occurred_at, AuditEventRow.details)
            .where(AuditEventRow.event_type == IMPORTED)
        ):
            moment = occurred_at.replace(tzinfo=None) if isinstance(occurred_at, datetime) else occurred_at
            if aggregate_id not in stamps or stamps[aggregate_id][0] <= moment:
                stamps[aggregate_id] = (moment, details or {})
        self.stamps = {line_id: details for line_id, (_, details) in stamps.items()}
        for rb_id in self.rb.lines:
            line_id = _id("line", rb_id)
            row = self.session.get(ProcurementLineRow, line_id)
            if row is None:
                if self.session.get(RequestRow, _id("request", rb_id)) is not None:
                    self.report.skip(f"строка RB {rb_id}: перенесена раньше и удалена в ERP — заново не заводится")
                continue
            self.line_ids[rb_id] = line_id
            if str(self.stamps.get(line_id, {}).get("version")) == str(row.version):
                self.ours.add(line_id)
            else:
                self.report.add("строки: изменены в ERP после переноса — оставлены как есть")

    # ---- requests and procurement lines

    def project(self, request: dict) -> tuple[str | None, str, str | None]:
        """(№ ПРОЕКТА, НАЗНАЧЕНИЕ purpose, base of the designation) of an RB request."""
        project = self.rb.projects.get(request["project_id"]) if request["project_id"] is not None else None
        number = _clean(project["number"]) if project else ""
        if number and project and project["kind"] == "order" and project_reference_valid(number):
            reference = normalize_project_reference(number)
            return reference, COMMERCIAL_PURPOSE, reference
        purposes = purchasing_settings.load(self.session)["purposes"]
        purpose = next((name for name in purposes if number and name.casefold() == number.casefold()), None)
        if project is not None and purpose is None:
            self.report.notes.append(
                f"проект RB «{number}» ({project['name']}) не в формате ERP — назначение «Обеспечение производства»")
        return None, purpose or "Обеспечение производства", None

    def request(self, request_id: int) -> None:
        request = self.rb.requests[request_id]
        rb_lines = sorted((line for line in self.rb.lines.values() if line.request_id == request_id), key=lambda x: x.id)
        missing = [line for line in rb_lines if line.id not in self.line_ids
                   and self.session.get(RequestRow, _id("request", line.id)) is None]
        if not missing:
            return
        reference, purpose, base = self.project(request)
        created = _utc(request["created_at"], self.now).astimezone(MSK)  # ERP keeps Moscow moments (moscow_now)
        received = _day(request["received_at"]) or created.date()
        drafts = []
        for line in missing:
            item = self.rb.items[line.item_id]
            planned = line.need_by or received + timedelta(days=14)
            deadline = min(received + timedelta(days=3 if request["urgent"] else 7), planned)
            drafts.append(RequestDraft(
                purpose=purpose, delivery_point=None, name=_clean(item["name"])[:1024],
                item_type=_clean(item.get("type"))[:255] or None, quantity=line.quantity,
                unit=_UNITS.get(item["unit"], item["unit"]), quality_document=None, procurement_deadline=deadline,
                planned_date=planned, project_reference=reference, design_article=_clean(item["part_number"])[:512] or None,
                link=line.plan_source or None, price_with_vat=_rub(line.budget),
                comment=_clean(request["note"]) or None,
            ))
        key = f"roller-buyer:request:{request_id}"
        with self.requests() as uow:
            batch = create_request_batch(
                drafts, actor=self.buyer, now=created, existing_legacy_ids=uow.requests.all_legacy_ids(),
                existing_designations=uow.requests.all_designations(), creation_key=key,
                creation_fingerprint=hashlib.sha256(key.encode()).hexdigest(), designation_base=base,
                directory_purposes=purchasing_settings.load(self.session)["purposes"],
            )
            sibling = self.session.scalar(select(RequestRow.designation).where(RequestRow.creation_key == key).limit(1))
            for line, made in zip(missing, batch):
                made = replace(made, id=_id("request", line.id), creation_index=line.id,
                               designation=sibling or made.designation)
                started = made.start_procurement(self.buyer)
                uow.requests.add(started)
                details = {"legacy_id": made.legacy_id, "designation": made.designation, "source": "roller_buyer",
                           "rb_request": request["number"], "rb_line": line.id}
                self.audit(made.id, "request.created", self.buyer, created, details, f"request:{line.id}")
                self.audit(made.id, "request.sent_to_procurement", self.buyer, created,
                           {"legacy_id": made.legacy_id}, f"sent:{line.id}")
                snapshot = build_procurement_requested_snapshot(started, today=created.date())
                message = OutboxMessage(
                    id=_id("outbox", line.id), topic="procurement.requested", idempotency_key=snapshot.idempotency_key,
                    created_at=created, payload=_procurement_requested_payload(snapshot),
                )
                uow.outbox.add(message)
                self.session.flush()
                SqlAlchemyOutboxRepository(self.session).mark_published(message.id, created)
                self.add_line(line, message, created)
            self.report.add("заявки: заведены", len(missing))

    def add_line(self, rb: RbLine, message: OutboxMessage, created: datetime) -> None:
        with self.procurement() as uow:
            line = create_line_from_requested(message)
            if line.legacy_id in uow.procurement.legacy_ids_with_prefix(line.legacy_id):
                day = created.date()
                line = replace(line, legacy_id=next_system_id(uow.procurement.legacy_ids_with_prefix(f"SM-{day:%Y%m%d}-"), day))
            line = replace(line, id=_id("line", rb.id))
            taken = line.take_in_work(self.buyer)
            uow.procurement.add(taken)
            self.audit(line.id, "procurement.created", IMPORT_ACTOR, created,
                       {"legacy_id": line.legacy_id, "source_event_id": message.id}, f"created:{rb.id}")
            self.audit(line.id, "procurement.taken_in_work", self.buyer, created, {
                "legacy_id": line.legacy_id, "from_status": line.status.value, "to_status": taken.status.value,
            }, f"taken:{rb.id}")
            uow.commit()
        self.line_ids[rb.id] = taken.id
        self.ours.add(taken.id)
        self.touched.add(taken.id)
        self.report.add("строки закупки: заведены")
        item_id = self.items.get(rb.item_id)
        if item_id is not None and self.session.get(LineItemRow, taken.id) is None:
            with self.purchasing() as uow:
                purchasing_items.link_line(uow, taken.id, item_id, self.buyer, created)
            self.report.add("строки закупки: связаны с номенклатурой")
        elif item_id is None:
            self.report.add("строки закупки: позиция RB не найдена в номенклатуре ERP")

    # ---- requests for quotes

    # RB keeps only days for the steps after the request. Each step gets noon of its day (Moscow) plus a few minutes, and
    # on the day of the step before it, never earlier than that step — ERP's history reads in RB's order. RB's days stay.

    def request_moment(self, rb: RbLine) -> datetime:
        return _utc(self.rb.requests[rb.request_id]["created_at"], self.now).astimezone(MSK)

    def rfq_moment(self, rfq_id: int, minutes: int = 0) -> datetime:
        lines = [self.rb.lines[item["request_line_id"]] for item in self.rb.rfq_lines.values()
                 if item["rfq_id"] == rfq_id and item["request_line_id"] in self.rb.lines]
        floor = max((self.request_moment(line) + timedelta(minutes=1) for line in lines), default=None)
        return _not_before(_noon(_day(self.rb.rfqs[rfq_id]["created_at"]) or self.now.date()), floor) + timedelta(minutes=minutes)

    def quote_moment(self, quote_id: int) -> datetime:
        rb = self.rb.quotes[quote_id]
        day = _day(rb["received_at"]) or _day(self.rb.rfqs[rb["rfq_id"]]["created_at"]) or self.now.date()
        return _not_before(_noon(day, 10), self.rfq_moment(rb["rfq_id"], 1)) + timedelta(seconds=quote_id)

    def decision_moment(self, decision_id: int) -> datetime:
        rb = self.rb.decisions[decision_id]
        quotes = {line["quote_id"] for line in self.rb.quote_lines.values() if line["rfq_line_id"] == rb["rfq_line_id"]}
        floor = max((self.quote_moment(quote) + timedelta(minutes=1) for quote in quotes), default=None)
        return _not_before(_noon(_day(rb["decided_at"]) or self.now.date(), 20), floor)

    def order_moment(self, order_id: int, day: date | None = None) -> datetime:
        decisions = [line.decision for line in self.rb.lines.values() if line.order == order_id and line.decision]
        floor = max((self.decision_moment(decision) + timedelta(minutes=1) for decision in decisions), default=None)
        order_day = _day(self.rb.orders[order_id]["order_date"]) or self.now.date()
        return _not_before(_noon(max(day or order_day, order_day), 30), floor)

    def rfq(self, rfq_id: int) -> None:
        rb = self.rb.rfqs[rfq_id]
        erp_id = _id("rfq", rfq_id)
        created = self.rfq_moment(rfq_id)
        row = self.session.get(RfqRow, erp_id)
        lines = sorted((item for item in self.rb.rfq_lines.values() if item["rfq_id"] == rfq_id), key=lambda x: x["id"])
        withdrawn = self.withdrawn(rfq_id)
        if not lines and not withdrawn and row is None:
            self.report.add("запросы КП: пустые в RB — пропущены")
            return
        if row is None:
            row = RfqRow(
                id=erp_id, number=next_rfq_number(self.session, created), title=f"Roller_Buyer № {rfq_id}",
                buyer_id=self.buyer.id, note=_clean(rb["note"]) or None, created_at=created, created_by=self.buyer.id,
                version=1,
            )
            self.session.add(row)
            self.session.flush()
            self.audit(erp_id, "purchasing.rfq_created", self.buyer, created, {
                "number": row.number, "lines": len(lines), "suppliers": "", "source": f"Roller_Buyer № {rfq_id}",
            }, f"rfq:{rfq_id}")
            self.report.add("запросы КП: заведены")
        for item in lines:
            self.rfq_line(row, item, created)
        for rb_line, note, removed_at, reason in withdrawn:
            self.removed_line(row, rb_line, note, removed_at, reason)
        self.rfq_suppliers(rfq_id, row)
        for quote_id in sorted(quote for quote, data in self.rb.quotes.items() if data["rfq_id"] == rfq_id):
            self.quote(row, quote_id)

    def rfq_line(self, rfq: RfqRow, item: dict, created: datetime) -> None:
        erp_id = _id("rfq-line", item["id"])
        if self.session.get(RfqLineRow, erp_id) is not None:
            return
        rb = self.rb.lines.get(item["request_line_id"])
        line_id = self.line_ids.get(item["request_line_id"])
        line = self.line(line_id) if line_id is not None else None
        if rb is None or line is None:
            self.report.skip(f"строка запроса КП RB {item['id']}: нет строки закупки")
            return
        live = self.session.scalar(select(RfqLineRow.id).where(
            RfqLineRow.procurement_line_id == line.id, RfqLineRow.removed_at.is_(None)))
        if live is not None:
            self.report.skip(f"{line.legacy_id}: уже в другом запросе КП ERP — строка RB {item['id']} не добавлена")
            return
        self.session.add(RfqLineRow(
            id=erp_id, rfq_id=rfq.id, procurement_line_id=line.id, item_id=self.items.get(rb.item_id),
            name=line.calculated_name[:512], article=(line.design_article or None) and line.design_article[:255],
            quantity=line.to_buy, unit=line.calculated_unit or "шт", need_by=line.planned_date,
            budget_unit_gross=line.calculated_price_with_vat, created_at=created,
        ))
        self.session.flush()
        self.report.add("запросы КП: строки")

    def withdrawn(self, rfq_id: int) -> list[tuple[RbLine, str, datetime, str]]:
        """Lines RB took out of this request (`rfq-withdraw.ts` deletes the RFQ line and keeps a note «Запрос КП № N от
        …: …» on the request line): the last round of each line in no request now → a removed line here."""
        out = []
        for line in self.rb.lines.values():
            if line.rfq_line is not None or not line.requote_note:
                continue
            last = line.requote_note.splitlines()[-1]
            found = re.match(r"Запрос КП № (\d+) от", last)
            if found and int(found.group(1)) == rfq_id:
                reason = "requote" if line.requote_at is not None else "new"
                at = _noon(line.requote_at) if line.requote_at else self.rfq_moment(rfq_id, 1)
                out.append((line, line.requote_note, at, reason))
        return out

    def removed_line(self, rfq: RfqRow, rb: RbLine, note: str, removed_at: datetime, reason: str) -> None:
        erp_id = _id("rfq-line-removed", f"{rfq.id}:{rb.id}")
        if self.session.get(RfqLineRow, erp_id) is not None:
            return
        line_id = self.line_ids.get(rb.id)
        line = self.line(line_id) if line_id is not None else None
        if line is None:
            return
        self.session.add(RfqLineRow(
            id=erp_id, rfq_id=rfq.id, procurement_line_id=line.id, item_id=self.items.get(rb.item_id),
            name=line.calculated_name[:512], article=(line.design_article or None) and line.design_article[:255],
            quantity=line.to_buy, unit=line.calculated_unit or "шт", need_by=line.planned_date,
            budget_unit_gross=line.calculated_price_with_vat, created_at=rfq.created_at, removed_at=removed_at,
            removed_reason=reason, removed_note=note, removed_by=self.buyer.id,
        ))
        self.session.flush()
        self.report.add("запросы КП: строки, убранные в RB («В новые» / «Перезапрос»)")

    def withdraw_gone(self) -> None:
        """A line RB has taken out of its request since the last run (its stamp names the RB request line): removed here
        too, unless ERP decided on it or the buyer moved the line."""
        for rb in self.rb.lines.values():
            line_id = self.line_ids.get(rb.id)
            before = str(self.stamps.get(line_id, {}).get("rfq_line") or "")
            if line_id not in self.ours or not before.isdigit() or int(before) in self.rb.rfq_lines:
                continue
            row = self.session.get(RfqLineRow, _id("rfq-line", int(before)))
            if row is None or row.removed_at is not None:
                continue
            if self.session.scalar(select(DecisionRow.id).where(DecisionRow.rfq_line_id == row.id)) is not None:
                self.report.skip(f"строка запроса КП «{row.name}»: в RB убрана, в ERP по ней решение — оставлена")
                continue
            row.removed_at = _noon(rb.requote_at) if rb.requote_at else self.now
            row.removed_reason = "requote" if rb.requote_at else "new"
            row.removed_note = (rb.requote_note.splitlines() or [""])[-1] or None
            row.removed_by = self.buyer.id
            self.report.add("запросы КП: строки убраны (в RB убраны после прошлого переноса)")
        self.session.flush()

    def rfq_suppliers(self, rfq_id: int, rfq: RfqRow) -> None:
        for item in (row for row in self.rb.rfq_suppliers if row["rfq_id"] == rfq_id):
            supplier_id = self.suppliers.get(item["supplier_id"])
            if supplier_id is None:
                self.report.skip(f"запрос КП RB № {rfq_id}: поставщик RB {item['supplier_id']} не найден в ERP")
                continue
            if item["declined_suggestion"]:
                if self.session.scalar(select(RfqDeclinedSuggestionRow.id).where(
                        RfqDeclinedSuggestionRow.rfq_id == rfq.id, RfqDeclinedSuggestionRow.supplier_id == supplier_id)) is None:
                    self.session.add(RfqDeclinedSuggestionRow(
                        id=_id("rfq-declined", item["id"]), rfq_id=rfq.id, supplier_id=supplier_id, reason="rotation",
                        created_at=rfq.created_at, created_by=self.buyer.id,
                    ))
                    self.report.add("запросы КП: отказ от подсказки")
                continue
            sent = _not_before(_noon(_day(item["sent_at"])), self.rfq_moment(rfq_id)) if _day(item["sent_at"]) else None
            self.asked(rfq, supplier_id, sent, item["id"])
        self.session.flush()

    def asked(self, rfq: RfqRow, supplier_id: UUID, sent_at: datetime | None, key: Any) -> None:
        row = self.session.scalar(select(RfqSupplierRow).where(
            RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier_id))
        if row is None:
            self.session.add(RfqSupplierRow(
                id=_id("rfq-supplier", key), rfq_id=rfq.id, supplier_id=supplier_id, reason="manual", sent_at=sent_at,
                created_at=rfq.created_at,
            ))
            self.session.flush()
            self.report.add("запросы КП: поставщики")
        elif row.sent_at is None and sent_at is not None:
            row.sent_at = sent_at
            self.report.add("запросы КП: отмечена отправка")

    def quote(self, rfq: RfqRow, quote_id: int) -> None:
        rb = self.rb.quotes[quote_id]
        erp_id = _id("quote", quote_id)
        supplier_id = self.suppliers.get(rb["supplier_id"])
        if supplier_id is None:
            self.report.skip(f"КП RB {quote_id}: поставщик не найден в ERP")
            return
        received = _day(rb["received_at"]) or rfq.created_at.date()
        entered = self.quote_moment(quote_id)
        new = self.session.get(QuoteRow, erp_id) is None
        if new:
            self.asked(rfq, supplier_id, entered, f"quote:{quote_id}")
            file_id = self.quote_file(rb["file_name"]) if rb["file_name"] else None
            valid = _day(rb["valid_until"])
            self.session.add(QuoteRow(
                id=erp_id, rfq_id=rfq.id, supplier_id=supplier_id, supplier_is_key=bool(rb["supplier_is_key"]),
                received_on=received, valid_until=valid if valid is None or valid >= received else received,
                currency="RUB", delivery_cost=_rub(rb["delivery_cost"]), min_order_sum=_rub(rb["min_order_sum"]),
                source=_QUOTE_SOURCES.get(rb["source"], "manual"), note=None, created_at=entered,
                file_name=(rb["file_name"] or None) and str(rb["file_name"])[:255], created_by=self.buyer.id,
                number=_clean(rb["number"])[:64] or None, file_id=file_id,
            ))
            self.session.flush()
        written = 0
        for line in sorted((row for row in self.rb.quote_lines.values() if row["quote_id"] == quote_id), key=lambda x: x["id"]):
            if self.session.get(QuoteLineRow, _id("quote-line", line["id"])) is not None:
                continue
            rfq_line_id = _id("rfq-line", line["rfq_line_id"])
            if self.session.get(RfqLineRow, rfq_line_id) is None:
                self.report.skip(f"строка КП RB {line['id']}: строки запроса нет в ERP")
                continue
            vat = Decimal(int(line["vat_rate"]))
            if vat >= 100:
                self.report.skip(f"строка КП RB {line['id']}: НДС {vat}% — в ERP до 99%")
                continue
            declined = bool(line["declined"])
            tiers = []
            for tier in _json(line["tiers"]):
                start, price = _positive(tier.get("from")), _positive(tier.get("unitPrice"))
                if start is not None and price is not None:
                    tiers.append({"from": format(start.normalize(), "f"), "price": format((price / 100).normalize(), "f")})
            self.session.add(QuoteLineRow(
                id=_id("quote-line", line["id"]), quote_id=erp_id, rfq_line_id=rfq_line_id,
                unit_price=None if declined else Decimal(int(line["unit_price"])) / 100, vat_included=bool(line["vat_included"]),
                vat_rate=vat, moq=_positive(line["moq"]), pack=_positive(line["pack"]), tiers=tiers,
                lead_days=line["lead_days"] if line["lead_days"] is None or line["lead_days"] >= 0 else None,
                in_stock=bool(line["in_stock"]), declined=declined, analog=bool(line["analog"]),
                analog_note=(_clean(line["analog_note"])[:512] or None) if line["analog"] else None,
            ))
            written += 1
        self.session.flush()
        if new:
            self.audit(rfq.id, "purchasing.quote_added", self.buyer, entered, {
                "number": rfq.number, "supplier": self.supplier_name(supplier_id), "lines": written,
            }, f"quote:{quote_id}")
            self.report.add("КП: заведены")
        if written:
            self.report.add("КП: строки", written)

    def quote_file(self, name: str) -> UUID | None:
        path = (self.data_dir / "files" / "quotes" / str(name)).resolve()
        root = (self.data_dir / "files" / "quotes").resolve()
        if root not in path.parents or not path.is_file():
            self.report.add("КП: файла нет на диске")
            return None
        return self.stored_file(path.read_bytes(), str(name))

    def stored_file(self, content: bytes, name: str) -> UUID:
        sha = hashlib.sha256(content).hexdigest()
        if sha in self.files:
            return self.files[sha]
        found = self.session.scalar(select(DocumentFileRow.id).where(DocumentFileRow.sha256 == sha))
        if found is None:
            found = _id("file", sha)
            self.session.add(DocumentFileRow(id=found, sha256=sha, file_name=_clean(name)[:1024] or "КП.pdf",
                                             size=len(content), content=content, created_at=self.now))
            self.session.flush()
            self.report.add("файлы КП: загружены")
        self.files[sha] = found
        return found

    def supplier_name(self, supplier_id: UUID) -> str:
        row = self.session.get(SupplierRow, supplier_id)
        return row.name if row is not None else ""

    # ---- decisions

    def decision(self, decision_id: int) -> None:
        rb = self.rb.decisions[decision_id]
        rfq_line = self.session.get(RfqLineRow, _id("rfq-line", rb["rfq_line_id"]))
        quote_line = self.session.get(QuoteLineRow, _id("quote-line", rb["quote_line_id"]))
        if rfq_line is None or quote_line is None:
            raise LookupError("строки запроса или КП нет в ERP")
        if self.session.scalar(select(DecisionRow.id).where(DecisionRow.rfq_line_id == rfq_line.id)) is not None:
            return
        if rfq_line.removed_at is not None:
            raise LookupError("строка убрана из запроса КП")
        if rfq_line.procurement_line_id not in self.ours:
            self.report.skip(f"решение RB {decision_id}: строка закупки изменена в ERP — решение не перенесено")
            return
        decided = self.decision_moment(decision_id)
        valid_days = int(purchasing_settings.load(self.session)["quote_valid_days"])
        offers: list[Offer] = []
        chosen_cover, supplier_id = None, None
        for line, quote in self.session.execute(
            select(QuoteLineRow, QuoteRow).join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
            .where(QuoteLineRow.rfq_line_id == rfq_line.id)
        ):
            covered = None if line.declined else cover(
                Decimal(rfq_line.quantity), price=line.unit_price, vat_included=line.vat_included,
                vat_rate=Decimal(line.vat_rate), moq=line.moq, pack=line.pack, tiers=read_tiers(line.tiers),
            )
            offers.append(Offer(
                quote_line_id=line.id, supplier_id=quote.supplier_id, is_key=quote.supplier_is_key,
                received_on=quote.received_on, gross_unit=covered.unit_gross if covered else None,
                total=covered.total_gross if covered else None, declined=line.declined, analog=line.analog,
                valid_until=quote.valid_until or quote.received_on + timedelta(days=valid_days), entered_at=quote.created_at,
            ))
            if line.id == quote_line.id:
                chosen_cover = covered
                supplier_id = quote.supplier_id
        if chosen_cover is None or chosen_cover.total_gross <= 0:
            raise LookupError("выбранное КП без цены")
        values = purchasing_settings.load(self.session)
        current = current_offers(offers, decided.date())
        best, base = cheapest(current), baseline(current)
        gate = gate_status(current, int(values["gate_min_quotes"]), bool(values["gate_require_non_key"]))
        supplier = self.session.get(SupplierRow, supplier_id)
        applied = self.apply_choice(rfq_line, supplier.name, chosen_cover.quantity, decided)
        gate_code, pricier_code = rb["gate_override_reason"] or None, rb["not_cheapest_reason"] or None
        self.session.add(DecisionRow(
            id=_id("decision", decision_id), rfq_line_id=rfq_line.id, quote_line_id=quote_line.id,
            cover_quantity=chosen_cover.quantity, unit_gross=chosen_cover.unit_gross, total_gross=chosen_cover.total_gross,
            baseline_unit_gross=base.gross_unit if base else None, cheapest_unit_gross=best.gross_unit if best else None,
            priced_quotes=gate.priced, gate_override_reason=_reason(gate_code, rb["gate_override_note"], GATE_OVERRIDE_REASONS),
            not_cheapest_reason=_reason(pricier_code, rb["not_cheapest_note"], NOT_CHEAPEST_REASONS),
            analog_approved_by=_clean(rb["analog_approved_by"])[:255] or None, gate_override_code=gate_code,
            not_cheapest_code=pricier_code, decided_at=decided, decided_by=self.buyer.id,
            applied_at=decided if applied else None,
        ))
        self.session.flush()
        rfq = self.session.get(RfqRow, rfq_line.rfq_id)
        self.audit(rfq.id, "purchasing.decision_made", self.buyer, decided, {
            "number": rfq.number, "line": rfq_line.name, "supplier": supplier.name,
            "quantity": format(chosen_cover.quantity.normalize(), "f"), "total": format(chosen_cover.total_gross, "f"),
        }, f"decision:{decision_id}")
        self.report.add("решения: заведены")
        if not applied:
            self.report.add("решения: не записаны в строку (строка не «В работе»/«Готов к запросу» или изменена в ERP)")

    def apply_choice(self, rfq_line: RfqLineRow, supplier: str, quantity: Decimal, at: datetime) -> bool:
        """ПОСТАВЩИК / КОЛ-ВО / ЕД.ИЗМ (факт) as the RFQ bridge writes them (`save_procurement_facts`)."""
        line = self.line(rfq_line.procurement_line_id)
        if line is None or line.id not in self.ours or line.status not in FACTS_EDITABLE:
            return False
        if line.buyer is None or line.buyer.id != self.buyer.id:
            return False
        save_procurement_facts(SaveProcurementFactsCommand(
            line_id=line.id, facts=ProcurementFacts(supplier=supplier, actual_quantity=quantity, actual_unit=rfq_line.unit),
            expected_version=line.version, actor=self.buyer, now=at,
        ), self.procurement())
        self.touched.add(line.id)
        return True

    # ---- orders

    def order(self, order_id: int) -> None:
        rb = self.rb.orders[order_id]
        rb_lines = [line for line in self.rb.lines.values() if line.order == order_id]
        if not rb_lines:
            return
        lines = {line.id: self.line(self.line_ids[line.id]) if line.id in self.line_ids else None for line in rb_lines}
        if any(line is None for line in lines.values()):
            raise LookupError("не все строки заказа есть в ERP")
        mine = [line for line in lines.values() if line.id in self.ours]
        if len(mine) != len(lines):
            self.report.skip(f"заказ RB № {rb['number']}: строки изменены в ERP — заказ не трогается")
            return
        supplier_id = self.suppliers.get(rb["supplier_id"])
        supplier = self.supplier_name(supplier_id) if supplier_id else ""
        order_day = _day(rb["order_date"]) or self.now.date()
        quantities = {self.line_ids[line.id]: Decimal(str(self.rb.order_lines[line.order_line]["quantity"])) for line in rb_lines}
        # 1. «Сформировать запрос»: the order's lines with the order's supplier and quantities, «Готов к запросу».
        statuses = {line.status for line in lines.values()}
        if statuses <= FACTS_EDITABLE:
            for line in lines.values():
                if (line.supplier or "") != supplier or line.actual_quantity != quantities[line.id]:
                    save_procurement_facts(SaveProcurementFactsCommand(
                        line_id=line.id, facts=ProcurementFacts(supplier=supplier, actual_quantity=quantities[line.id]),
                        expected_version=line.version, actor=self.buyer, now=self.order_moment(order_id),
                    ), self.procurement())
            result = build_supplier_requests(BuildSupplierRequestsCommand(
                actor=self.buyer, now=self.order_moment(order_id) + timedelta(minutes=1), line_ids=list(quantities),
            ), self.procurement())
            if result.skipped or len(result.orders) != 1:
                reasons = "; ".join(f"{item.legacy_id}: {item.reason}" for item in result.skipped)
                raise ValueError(f"запрос поставщику не сформирован как один заказ ({reasons})")
            self.touched.update(quantities)
            self.report.add("заказы: сформированы (№ ЗАКАЗА ERP)")
        lines = {key: self.line(key) for key in quantities}
        numbers = {line.order_number for line in lines.values()}
        if len(numbers) != 1 or None in numbers:
            raise ValueError("строки заказа разошлись по разным заказам ERP")
        number = numbers.pop()
        self.report.notes.append(f"заказ RB № {rb['number']} ({supplier}) → {number}")
        invoice, content, file_name = self.invoice_of(rb)
        invoice_day = _day(invoice["date"]) if invoice else None
        invoice_day = invoice_day or order_day
        # 2. «Внести КП»: the order's prices, the invoice № and date, the promised date; with the invoice file → «В оплату».
        awaiting = all(line.status is ProcurementStatus.AWAITING_INVOICE for line in lines.values())
        if awaiting and (content is not None or any(line.money.price_with_vat is None for line in lines.values())):
            url = None
            if content is not None:
                uploaded = upload_invoice_file(UploadInvoiceFileCommand(
                    line_id=next(iter(lines)), file_name=file_name, content_type="application/pdf", content=content,
                    actor=self.buyer, now=self.order_moment(order_id, invoice_day) + timedelta(minutes=2),
                ), self.procurement())
                url = uploaded.url
            promised = _day(rb["promised_date"]) or order_day
            rows = []
            for rb_line in rb_lines:
                line = lines[self.line_ids[rb_line.id]]
                order_line = self.rb.order_lines[rb_line.order_line]
                price = Decimal(int(order_line["unit_price"])) / 100
                vat = Decimal(int(order_line["vat_rate"]))
                amounts = (MoneyInput(currency="RUB", vat_rate=vat, price_with_vat=gross_unit(price, True, vat))
                           if order_line["vat_included"] else MoneyInput(currency="RUB", vat_rate=vat, price=price))
                rows.append(KpRow(line.id, line.version, KpEntry(
                    money=MoneyEntry(amounts=amounts, invoice_item_name=line.site_name or line.calculated_name,
                                     invoice_number=_clean(rb["number"])[:255], invoice_date=invoice_day),
                    actual_quantity=quantities[line.id], actual_unit=line.actual_unit or line.calculated_unit,
                    expected_arrival=promised,
                )))
            result = save_kp(SaveKpCommand(
                order_reference=number, rows=rows, actor=self.buyer, invoice_url=url,
                now=self.order_moment(order_id, invoice_day) + timedelta(minutes=3),
            ), self.procurement())
            if result.invoice_error:
                raise ValueError(f"счёт не принят: {result.invoice_error}")
            self.report.add("заказы: цены и счёт внесены" if url else "заказы: цены внесены, счёта нет — «Жду счет»")
        lines = {key: self.line(key) for key in quantities}
        if all(line.status is ProcurementStatus.AWAITING_INVOICE for line in lines.values()):
            self.report.differs.append(
                f"заказ RB № {rb['number']} → {number}: файла счёта нет ни в RB, ни в Kaiten — в ERP «Жду счет» "
                f"(в RB «{'оплачен' if rb['paid_at'] else 'заказан'}»): ERP вносит счёт и оплату только с файлом счёта")
        # 3. «Оплачено» once for the order → «В пути», expected by Receiving.
        paid_day = _day(rb["paid_at"])
        if paid_day is not None and all(line.status is ProcurementStatus.TO_PAY for line in lines.values()):
            paid_at = self.order_moment(order_id, max(paid_day, invoice_day)) + timedelta(minutes=4)
            pay_supplier_order(PaySupplierOrderCommand(
                order_number=number, lines=[OrderLineRef(line.id, line.version) for line in lines.values()],
                actor=self.buyer, now=paid_at,
            ), self.procurement())
            self.report.add("заказы: оплачены")
        lines = {key: self.line(key) for key in quantities}
        # 4. RB moved the date after the promise: the order-wide ОЖИД. ДАТА (the audit keeps the promised one).
        expected = _day(rb["expected_date"])
        invoiced = all(line.status in (ProcurementStatus.TO_PAY, ProcurementStatus.IN_TRANSIT) for line in lines.values())
        if expected is not None and invoiced and any(line.expected_arrival != expected for line in lines.values()):
            set_order_expected_arrival(SetOrderExpectedArrivalCommand(
                order_number=number, expected_arrival=expected,
                lines=[OrderLineRef(line.id, line.version) for line in lines.values()], actor=self.buyer, now=self.now,
            ), self.procurement())
            self.report.add("заказы: перенесена ожидаемая дата (обещанная — в журнале)")
        # 5. The facts procurement has no place for.
        self.order_facts(number, rb)

    def invoice_of(self, order: dict) -> tuple[dict | None, bytes | None, str]:
        """The Kaiten invoice of an RB order (RB matches it by supplier and number, `kaiten-rfq.ts` orderOf; a card the
        parser did not read is found by the number in its title or file name) and its invoice file."""
        number = norm_doc_number(order["number"] or "")
        if not number:
            return None, None, ""
        same = [inv for inv in self.rb.invoices if inv["supplier_id"] in (order["supplier_id"], None)]
        found = next((inv for inv in same if inv["number"] and norm_doc_number(inv["number"]) == number), None)
        how = "по номеру"
        if found is None and len(number) >= 4:
            found = next((inv for inv in same if number in norm_doc_number(inv["title"] or "")
                          or any(number in norm_doc_number(item.get("name", "")) for item in _json(inv["files"]))), None)
            how = "по названию карточки"
        if found is None:
            return None, None, ""
        files = _json(found["files"])
        pdfs = [item for item in files if str(item.get("name", "")).lower().endswith(".pdf") and item.get("path")]
        chosen = (next((item for item in pdfs if item.get("kind") == "invoice"), None)
                  or next((item for item in pdfs if number in norm_doc_number(item.get("name", ""))), None))
        self.report.notes.append(f"заказ RB № {order['number']}: счёт Kaiten {found['external_id']} ({how})"
                                 + ("" if chosen else " — без файла счёта"))
        if chosen is None:
            return found, None, ""
        path = (self.data_dir / str(chosen["path"]).replace("\\", "/")).resolve()
        if self.data_dir.resolve() not in path.parents or not path.is_file():
            self.report.notes.append(f"заказ RB № {order['number']}: файла {chosen['path']} нет на диске")
            return found, None, ""
        return found, path.read_bytes(), str(chosen["name"])

    def order_facts(self, number: str, rb: dict) -> None:
        with self.purchasing() as uow:
            facts = uow.session.get(OrderFactRow, number)
            lines = order_line_facts(uow.session, order_numbers=[number])
        invoiced = all(line.status != ProcurementStatus.AWAITING_INVOICE.value for line in lines if not line.is_backorder)
        wanted: dict[str, Any] = {}
        if invoiced:
            wanted["sent_to_pay_on"] = _day(rb["sent_to_pay_at"])
            wanted["shipped_on"] = _day(rb["shipped_at"])
        wanted["shipment_note"] = _clean(rb["shipment_note"])[:500] or None
        wanted["delivery_cost"] = _rub(rb["delivery_cost"])
        wanted["note"] = str(rb["note"] or "").strip()[:2000] or None
        given = {name: value for name, value in wanted.items()
                 if value is not None and (facts is None or not getattr(facts, name))}
        if not given:
            return
        purchasing_orders.save_facts(self.purchasing(), number, lines, purchasing_orders.FactsChange(
            version=facts.version if facts is not None else 0, **given), self.buyer, self.now)
        self.report.add("заказы: факты «Счета и поставки»")

    # ---- cancelled, tolling, from stock

    def closing(self, rb: RbLine) -> None:
        line = self.line(self.line_ids[rb.id])
        if line is None or line.id not in self.ours:
            return
        if rb.status == "cancelled" or rb.supply == "tolling":
            if line.status is ProcurementStatus.CANCELLED:
                return
            reason = (rb.cancel_reason or CANCEL_FALLBACK) if rb.status == "cancelled" else TOLLING_REASON
            at = _not_before(_noon(rb.cancelled_at, 50) if rb.cancelled_at else self.request_moment(rb),
                             self.request_moment(rb) + timedelta(minutes=2))
            result = cancel_procurement_lines(CancelManyCommand(
                items=(CancelManyItem(line.id, line.version),), reason=reason, actor=self.buyer, now=at,
            ), self.procurement)
            if result.skipped:
                raise ValueError(f"не отменена: {result.skipped[0][1]}")
            self.touched.add(line.id)
            self.report.add("строки: отменены" if rb.status == "cancelled" else "строки: давальческие → «Отменено»")
            if rb.status != "cancelled":
                self.report.differs.append(f"{line.legacy_id} «{line.calculated_name}»: в RB давальческое — в ERP «Отменено» "
                                           "с причиной (такого факта у строки закупки ERP нет)")
        elif rb.supply == "stock" and line.status in FACTS_EDITABLE and not line.note:
            set_procurement_note(SetNoteCommand(
                line_id=line.id, expected_version=line.version, note=STOCK_NOTE + (
                    f" (в RB перемещено {format(rb.stock_in.normalize(), 'f')})" if rb.stock_in > 0 else ""),
                actor=self.buyer, now=self.request_moment(rb) + timedelta(minutes=2),
            ), self.procurement())
            self.touched.add(line.id)
            self.report.add("строки: «Со склада» в RB — оставлены «В работе» с комментарием")
            self.report.differs.append(f"{line.legacy_id} «{line.calculated_name}»: в RB «со склада» — в ERP «В работе» "
                                       "с комментарием («Со склада» в ERP — резерв на выбранной партии)")

    # ---- the stamp a re-run reads

    def stamp(self) -> None:
        for rb in self.rb.lines.values():
            line_id = self.line_ids.get(rb.id)
            line = self.line(line_id) if line_id is not None else None
            if line is None:
                continue
            self.report.statuses[(rb.status, line.status.value)] += 1
            if line_id not in self.touched:
                continue
            request = self.rb.requests[rb.request_id]
            self.audit(line.id, IMPORTED, IMPORT_ACTOR, self.now, {
                "legacy_id": line.legacy_id, "rb_request": request["number"], "rb_line": rb.id, "rb_status": rb.status,
                "rfq_line": rb.rfq_line or "", "to_status": line.status.value, "version": line.version,
            }, f"{rb.id}:{line.version}:{self.now.isoformat()}")
        self.session.flush()


def _json(raw: Any) -> list:
    import json

    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _reason(code: str | None, note: str | None, labels: dict[str, str]) -> str | None:
    """The text stored with a reason code: the label (with RB's words when it has them), or the words for «Другое»."""
    if not code:
        return None
    words = _clean(note)
    if code == "other":
        return words[:2000] or labels.get(code, code)
    label = labels.get(code, code)
    return f"{label}: {words}"[:2000] if words else label


def buyer_actor(session: Session, login: str) -> Actor:
    user = session.scalar(select(IdentityUserRow).where(func.lower(IdentityUserRow.login) == login.strip().lower()))
    if user is None:
        raise SystemExit(f"Нет пользователя с логином {login}")
    return Actor(id=user.id, name=user.name or user.email, department=user.department)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roller_Buyer → ERP: заявки, запросы КП, КП, решения, заказы, настройки")
    parser.add_argument("--snapshot", required=True, type=Path, help="копия buyer.db")
    parser.add_argument("--data-dir", required=True, type=Path, help="DATA_DIR Roller_Buyer (только чтение)")
    parser.add_argument("--buyer", required=True, help="логин закупщика ERP, чьи это строки")
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон с откатом)")
    args = parser.parse_args(argv)
    from app.database import build_engine

    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    rb = Snapshot(open_snapshot(args.snapshot))
    engine = enable_savepoints(build_engine(database_url))
    now = datetime.now(MSK)
    with Session(bind=engine, expire_on_commit=False) as session:
        buyer = buyer_actor(session, args.buyer)
        report = ProcurementFlowImporter(session, rb, args.data_dir, buyer, now).run()
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print("\n".join(report.lines()))
    print("ЗАПИСАНО" if args.apply else "пробный прогон: ничего не записано (добавьте --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
