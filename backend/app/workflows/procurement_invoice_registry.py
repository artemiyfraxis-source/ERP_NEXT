"""Procurement ↔ purchasing for supplier invoices (owner Витя, 26.10.2026: «нужен полноценный разборщик всех данных, как
у нас в roller_buyer»; status, twins and backfill — owner's session 27.10.2026).

* `PurchasingSupplierInns` — procurement's `SupplierInns` port over purchasing's supplier directory: the INN of the
  order's supplier, the supplier owning an INN — for the checks shown when an invoice is uploaded.
* `push_after_upload` — every invoice file a buyer uploads to an order («Файл счёта», «Вставить счёт») goes to
  purchasing's «Счета» (`purchasing.invoice_uploads`: a new row of source «procurement», or attached to the registry's
  invoice of the same seller, № and date — its Kaiten card, an earlier upload), so the document chain matches it with
  its UPDs. Run after the answer; never fails the upload — the file is stored in procurement whatever the registry says.
* `sync_invoice_status` — the registry invoice follows the procurement lines carrying its files (СЧЕТ = the stored
  file's path): every live line paid («В пути» and later) → «paid» with the latest payment day and the lines' sum; all
  of them cancelled → «cancelled»; no line carries any of its files and a newer invoice was uploaded for the same line or
  order (or the line carries a newer file) → «replaced» — a wrong file uploaded and replaced is no live invoice; else
  «to_pay». A Kaiten card is only ever moved to «paid». Backorder copies («-Д») share the original's payment and are
  not counted. Run after the procurement commands that move an invoice (pay, invoice, replace — `sync_after_command`,
  never fails the command) and once a minute by the worker (`invoice_status_tick`) for everything else (cancel, …).

Modules never import each other; this workflow is the only place the two meet for invoices, the same shape as
`receipt_upd_registry` for the warehouse's UPDs. It reads procurement's lines and files read-only; purchasing writes
its own rows.

Backfill of the invoices uploaded in procurement before the registry took them (files some procurement line carries as
its СЧЕТ, oldest first, the same twin rules), then the status of every uploaded invoice:

    deploy/erp.sh cli python -m app.workflows.procurement_invoice_registry --backfill           # что попадёт, без записи
    deploy/erp.sh cli python -m app.workflows.procurement_invoice_registry --backfill --apply   # записать (повтор безвреден)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.modules.procurement.domain import ProcurementStatus, is_backorder_id
from app.modules.procurement.infrastructure import ProcurementFileRow, ProcurementLineRow
from app.modules.purchasing.infrastructure import SupplierNameRow, SupplierRow
from app.modules.purchasing.invoice_uploads import (
    InvoiceUploadFact,
    ProcurementInvoiceState,
    UploadedInvoiceDocument,
    apply_procurement_state,
    invoices_of,
    record_uploaded_invoice,
    upload_facts,
)
from app.modules.purchasing.suppliers import supplier_key
from app.shared.identity import Actor
from app.shared.invoice_reading import PARSER_VERSION, InvoiceReading, line_json, read_invoice
from app.shared.pdf_text import PdfLimitExceeded

log = logging.getLogger(__name__)
TOO_HEAVY = "файл слишком тяжёлый для разбора (превышен лимит обработки)"
SYNC_ACTOR = Actor(id=UUID(int=0), name="Счета ← закупка", department="Система")
_MOSCOW = ZoneInfo("Europe/Moscow")
# СЧЕТ of a line holding an invoice file stored by procurement (an absolute ERP link included).
_FILE_IN_URL = re.compile(
    r"/api/procurement/files/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.IGNORECASE,
)
_PAID = frozenset({ProcurementStatus.IN_TRANSIT.value, ProcurementStatus.QUARANTINE.value, ProcurementStatus.DONE.value})
_CANCELLED = ProcurementStatus.CANCELLED.value
_CHUNK = 500


class PurchasingSupplierInns:
    """`app.modules.procurement.invoice_checks.SupplierInns` over purchasing's directory (read-only)."""

    def __init__(self, purchasing_factory: Callable):
        self.purchasing_factory = purchasing_factory

    def _by_name(self, session, name: str) -> SupplierRow | None:
        key = supplier_key(name) if name.strip() else ""
        if not key:
            return None
        row = session.scalar(select(SupplierRow).where(SupplierRow.name_key == key))
        if row is None:
            supplier_id = session.scalar(select(SupplierNameRow.supplier_id).where(SupplierNameRow.name_key == key))
            row = session.get(SupplierRow, supplier_id) if supplier_id is not None else None
        return row

    def inn_of(self, supplier: str) -> str | None:
        with self.purchasing_factory() as uow:
            row = self._by_name(uow.session, supplier)
            return row.inn if row is not None and row.inn else None

    def supplier_of_inn(self, inn: str) -> str | None:
        with self.purchasing_factory() as uow:
            rows = sorted(uow.session.scalars(select(SupplierRow).where(SupplierRow.inn == inn)),
                          key=lambda row: row.archived_at is not None)
            return rows[0].name if rows else None


def uploaded_document(
    file_id: UUID, file_name: str, uploaded_at: datetime, order_number: str, supplier: str, reading: InvoiceReading,
    line_id: UUID | None = None,
) -> UploadedInvoiceDocument:
    h = reading.header
    return UploadedInvoiceDocument(
        file_key=str(file_id), file_name=file_name, uploaded_at=uploaded_at, order_number=order_number,
        supplier=supplier, number=h.number, date=h.date, seller_name=h.seller_name, seller_inn=h.seller_inn,
        total=h.total, vat=h.vat, vat_free=h.vat_free, lines=[line_json(line) for line in reading.lines],
        text=reading.text, issues=reading.issues, parser=PARSER_VERSION, line_id=line_id,
    )


def _read(content: bytes) -> InvoiceReading:
    try:
        return read_invoice(content)
    except PdfLimitExceeded:
        return InvoiceReading(issues=(TOO_HEAVY,))


def push_uploaded_invoice(
    purchasing_factory: Callable, file_id: UUID, file_name: str, uploaded_at: datetime, content: bytes,
    reading: InvoiceReading | None, order_number: str, supplier: str, actor: Actor, now: datetime,
    line_id: UUID | None = None, procurement_factory: Callable | None = None,
) -> dict[str, Any]:
    """One uploaded invoice file into «Счета»; `reading` None — read here (the «Вставить счёт» path reads prices with
    its own reader). With `procurement_factory` the invoices of that line and order then take their status (an upload
    may replace an earlier wrong file)."""
    doc = uploaded_document(file_id, file_name, uploaded_at, order_number, supplier, reading or _read(content), line_id)
    with purchasing_factory() as uow:
        result = record_uploaded_invoice(uow, doc, content, actor, now)
        uow.commit()
    if procurement_factory is not None:
        sync_invoice_status(
            procurement_factory, purchasing_factory, now, file_keys={str(file_id)},
            line_ids={line_id} if line_id else set(), order_numbers={order_number} if order_number else set(),
        )
    return {"file": str(file_id), **result}


def push_after_upload(
    purchasing_factory: Callable | None, file_id: UUID, file_name: str, uploaded_at: datetime, content: bytes,
    reading: InvoiceReading | None, order_number: str, supplier: str, actor: Actor, now: datetime,
    line_id: UUID | None = None, procurement_factory: Callable | None = None,
) -> dict[str, Any] | None:
    """The hook of the invoice upload: never raises — the file is stored in procurement whatever the registry says."""
    if purchasing_factory is None:
        return None
    try:
        return push_uploaded_invoice(
            purchasing_factory, file_id, file_name, uploaded_at, content, reading, order_number, supplier, actor, now,
            line_id, procurement_factory,
        )
    except Exception:  # noqa: BLE001 — the registry is purchasing's business; the upload is done
        log.exception("счёт %s не попал в реестр «Счета»", file_id)
        return None


# ---- status: the procurement lines carrying the invoice --------------------------------------------------------------


@dataclass(frozen=True)
class CarryingLine:
    """A procurement line whose СЧЕТ is a stored invoice file."""

    id: UUID
    legacy_id: str
    status: str
    paid_at: datetime | None
    sum: Decimal | None  # СУММА (факт), roubles
    currency: str | None
    order_number: str
    file_key: str


def file_key_of(url: str | None) -> str | None:
    found = _FILE_IN_URL.search(str(url or ""))
    return found.group(1).lower() if found else None


def _moscow_day(value: datetime) -> date:
    return value.astimezone(_MOSCOW).date() if value.tzinfo is not None else value.date()


def _carrying_lines(session) -> list[CarryingLine]:
    rows = session.execute(
        select(ProcurementLineRow.id, ProcurementLineRow.legacy_id, ProcurementLineRow.status,
               ProcurementLineRow.paid_at, ProcurementLineRow.actual_sum, ProcurementLineRow.currency,
               ProcurementLineRow.order_number, ProcurementLineRow.invoice_url)
        .where(ProcurementLineRow.invoice_url.like("%/api/procurement/files/%"))
    )
    out = []
    for line_id, legacy_id, status, paid_at, total, currency, order_number, url in rows:
        key = file_key_of(url)
        if key and not is_backorder_id(legacy_id):
            out.append(CarryingLine(line_id, legacy_id, status, paid_at, total, currency, order_number or "", key))
    return out


def _uploaded_times(session, keys: Iterable[str]) -> dict[str, datetime]:
    """procurement_files.uploaded_at of these files."""
    ids = sorted({UUID(key) for key in keys})
    out: dict[str, datetime] = {}
    for start in range(0, len(ids), _CHUNK):
        for file_id, uploaded_at in session.execute(
            select(ProcurementFileRow.id, ProcurementFileRow.uploaded_at).where(ProcurementFileRow.id.in_(ids[start:start + _CHUNK]))
        ):
            out[str(file_id)] = uploaded_at
    return out


def _after(a: datetime, b: datetime) -> bool:
    """a later than b, naive (SQLite) and aware values alike."""
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return a > b


def _paid_state(lines: list[CarryingLine]) -> ProcurementInvoiceState:
    days = [_moscow_day(line.paid_at) for line in lines if line.paid_at is not None]
    sums = [line.sum for line in lines]
    rub = all(value is not None for value in sums) and all((line.currency or "RUB") == "RUB" for line in lines)
    total = int((sum(sums, Decimal(0)) * 100).quantize(Decimal(1))) if rub and sums else None
    return ProcurementInvoiceState("paid", max(days, default=None), total)


def invoice_state(
    uploads: list[InvoiceUploadFact], all_uploads: list[InvoiceUploadFact], by_key: dict[str, list[CarryingLine]],
    line_files: dict[UUID, str], order_files: dict[str, list[str]], uploaded_at: dict[str, datetime],
) -> ProcurementInvoiceState:
    """The state of one registry invoice from its uploaded files (`uploads`): see the module docstring."""
    carrying = {line.id: line for upload in uploads for line in by_key.get(upload.file_key, [])}
    live = [line for line in carrying.values() if line.status != _CANCELLED]
    if live:
        if all(line.status in _PAID or line.paid_at is not None for line in live):
            return _paid_state(live)
        return ProcurementInvoiceState("to_pay")
    if carrying:
        return ProcurementInvoiceState("cancelled")
    own = {upload.file_key for upload in uploads}

    def superseded(upload: InvoiceUploadFact) -> bool:
        newer_upload = any(
            other.file_key not in own and _after(other.uploaded_at, upload.uploaded_at)
            and ((upload.line_id is not None and other.line_id == upload.line_id)
                 or (upload.order_number and other.order_number == upload.order_number))
            for other in all_uploads
        )
        if newer_upload:
            return True
        current = [line_files.get(upload.line_id)] if upload.line_id is not None else []
        current += order_files.get(upload.order_number, []) if upload.order_number else []
        return any(
            key and key not in own and key in uploaded_at and _after(uploaded_at[key], upload.uploaded_at)
            for key in current
        )

    if uploads and all(superseded(upload) for upload in uploads):
        return ProcurementInvoiceState("replaced")
    return ProcurementInvoiceState("to_pay")


@dataclass(frozen=True)
class ProcurementFacts:
    """What procurement says about the invoice files, read in one short session (before purchasing writes)."""

    lines: list[CarryingLine]
    by_key: dict[str, list[CarryingLine]]
    line_files: dict[UUID, str]  # a line's current invoice file
    order_files: dict[str, list[str]]  # the invoice files an order's lines carry
    uploaded_at: dict[str, datetime]  # when procurement stored those files
    orders_of_lines: dict[UUID, str]


def procurement_facts(procurement_factory: Callable, line_ids: Iterable[UUID] = ()) -> ProcurementFacts:
    with procurement_factory() as procurement:
        session = procurement.session
        lines = _carrying_lines(session)
        wanted = sorted({line_id for line_id in line_ids if line_id})
        orders_of_lines: dict[UUID, str] = {}
        for start in range(0, len(wanted), _CHUNK):
            for line_id, number in session.execute(select(ProcurementLineRow.id, ProcurementLineRow.order_number).where(
                ProcurementLineRow.id.in_(wanted[start:start + _CHUNK]))):
                orders_of_lines[line_id] = number or ""
        by_key: dict[str, list[CarryingLine]] = {}
        line_files: dict[UUID, str] = {}
        order_files: dict[str, list[str]] = {}
        for line in lines:
            by_key.setdefault(line.file_key, []).append(line)
            line_files[line.id] = line.file_key
            if line.order_number:
                order_files.setdefault(line.order_number, []).append(line.file_key)
        uploaded_at = _uploaded_times(session, set(line_files.values()))
    return ProcurementFacts(lines, by_key, line_files, order_files, uploaded_at, orders_of_lines)


def apply_states(
    purchasing, facts: ProcurementFacts, now: datetime, *, file_keys: set[str] | None = None,
    line_ids: set[UUID] | None = None, order_numbers: set[str] | None = None,
) -> Counter:
    """The states onto the registry inside the purchasing unit of work `purchasing` (the caller commits): all uploaded
    invoices, or those of these files / lines / orders. Counts per new status («unchanged» for the rest)."""
    session = purchasing.session
    all_uploads = upload_facts(session)
    if file_keys is None and line_ids is None and order_numbers is None:
        targets = {upload.invoice_id for upload in all_uploads}
    else:
        lines_wanted = set(line_ids or ())
        orders = set(order_numbers or ()) | {facts.orders_of_lines.get(line_id, "") for line_id in lines_wanted}
        orders.discard("")
        keys = set(file_keys or ()) | {
            line.file_key for line in facts.lines if line.id in lines_wanted or line.order_number in orders
        }
        targets = set(invoices_of(session, file_keys=keys, line_ids=lines_wanted, order_numbers=orders))
    grouped: dict[UUID, list[InvoiceUploadFact]] = {}
    for upload in all_uploads:
        if upload.invoice_id in targets:
            grouped.setdefault(upload.invoice_id, []).append(upload)
    counts: Counter = Counter()
    for invoice_id, uploads in grouped.items():
        state = invoice_state(uploads, all_uploads, facts.by_key, facts.line_files, facts.order_files, facts.uploaded_at)
        changed = apply_procurement_state(purchasing, invoice_id, state, SYNC_ACTOR, now)
        counts[changed or "unchanged"] += 1
    return counts


def sync_invoice_status(
    procurement_factory: Callable, purchasing_factory: Callable, now: datetime, *, file_keys: set[str] | None = None,
    line_ids: set[UUID] | None = None, order_numbers: set[str] | None = None,
) -> Counter:
    """The status of the uploaded invoices — all of them, or those of these files / lines / orders — from the
    procurement lines: procurement is read first, then purchasing writes and commits."""
    facts = procurement_facts(procurement_factory, line_ids or ())
    with purchasing_factory() as purchasing:
        counts = apply_states(purchasing, facts, now, file_keys=file_keys, line_ids=line_ids, order_numbers=order_numbers)
        purchasing.commit()
    return counts


def sync_after_command(
    procurement_factory: Callable | None, purchasing_factory: Callable | None, now: datetime, *,
    line_ids: Iterable[UUID] = (), order_numbers: Iterable[str] = (),
) -> Counter | None:
    """The hook after a procurement command that moves an invoice (paid, invoice, replacement): never raises — the
    command is done whatever the registry says; the worker's tick repeats the sync."""
    if procurement_factory is None or purchasing_factory is None:
        return None
    try:
        return sync_invoice_status(
            procurement_factory, purchasing_factory, now, line_ids=set(line_ids),
            order_numbers={number for number in order_numbers if number},
        )
    except Exception:  # noqa: BLE001 — the registry is purchasing's business; the command is done
        log.exception("статус счетов в «Счета» не обновлён после команды закупки")
        return None


def invoice_status_tick(now: datetime, sessions) -> int:
    """The worker's pass over every uploaded invoice: how many changed status."""
    from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
    from app.modules.purchasing.infrastructure import SqlAlchemyPurchasingUnitOfWork

    counts = sync_invoice_status(
        lambda: SqlAlchemyProcurementUnitOfWork(sessions), lambda: SqlAlchemyPurchasingUnitOfWork(sessions), now,
    )
    return sum(count for status, count in counts.items() if status != "unchanged")


# ---- backfill ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillReport:
    files: int  # stored files some line carries as its invoice
    pushed: Counter  # created / attached / updated / unchanged
    statuses: Counter  # the status sync afterwards


def backfill(procurement_factory: Callable, purchasing_factory: Callable, actor: Actor, now: datetime,
             apply: bool = False) -> BackfillReport:
    """The invoice files procurement lines carry, oldest first, into «Счета» with the twin rules, then the status of
    every uploaded invoice. Procurement is read whole first (the files' contents too — a one-off over the invoices of
    a few weeks), then everything is written in one purchasing transaction: committed with `apply`, else rolled back
    (the dry run counts exactly what the run would do)."""
    facts = procurement_facts(procurement_factory)
    keys = sorted({line.file_key for line in facts.lines})
    with procurement_factory() as procurement:
        session = procurement.session
        files = []
        for start in range(0, len(keys), _CHUNK):
            files += list(session.execute(
                select(ProcurementFileRow.id, ProcurementFileRow.line_id, ProcurementFileRow.file_name,
                       ProcurementFileRow.uploaded_at, ProcurementFileRow.content)
                .where(ProcurementFileRow.id.in_([UUID(key) for key in keys[start:start + _CHUNK]]))
            ))
        files.sort(key=lambda row: (row.uploaded_at.replace(tzinfo=None), str(row.id)))
        owners = {}
        line_ids = sorted({row.line_id for row in files})
        for start in range(0, len(line_ids), _CHUNK):
            for line_id, order_number, supplier in session.execute(
                select(ProcurementLineRow.id, ProcurementLineRow.order_number, ProcurementLineRow.supplier)
                .where(ProcurementLineRow.id.in_(line_ids[start:start + _CHUNK]))
            ):
                owners[line_id] = (order_number or "", supplier or "")
    pushed: Counter = Counter()
    with purchasing_factory() as purchasing:
        for row in files:
            content = bytes(row.content)
            order_number, supplier = owners.get(row.line_id, ("", ""))
            doc = uploaded_document(row.id, row.file_name, row.uploaded_at, order_number, supplier, _read(content),
                                    row.line_id)
            pushed[record_uploaded_invoice(purchasing, doc, content, actor, now)["status"]] += 1
        statuses = apply_states(purchasing, facts, now)
        if apply:
            purchasing.commit()
    return BackfillReport(len(files), pushed, statuses)


def format_report(report: BackfillReport, applied: bool) -> str:
    head = "Записано" if applied else "Проверка без записи (--apply — записать)"
    names = {"created": "новых счетов", "attached": "присоединено к уже известным (Kaiten, прежняя загрузка)",
             "updated": "обновлено", "unchanged": "без изменений"}
    lines = [head, f"Файлов счетов в строках закупки: {report.files}"]
    lines += [f"  {names.get(key, key)}: {count}" for key, count in sorted(report.pushed.items())]
    lines.append("Статус по строкам закупки:")
    lines += [f"  {key}: {count}" for key, count in sorted(report.statuses.items())] or ["  —"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Счета, загруженные в закупке, → реестр «Счета» закупок.")
    parser.add_argument("--backfill", action="store_true", help="перенести счета, загруженные до реестра")
    parser.add_argument("--apply", action="store_true", help="записать; без него — только показать (повтор безвреден)")
    args = parser.parse_args(argv)
    if not args.backfill:
        parser.print_help()
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_procurement_uow_factory, build_purchasing_uow_factory
    from app.shared.clock import moscow_now

    engine = build_engine(url)
    try:
        report = backfill(
            build_procurement_uow_factory(engine), build_purchasing_uow_factory(engine),
            Actor(id=uuid.uuid4(), name="перенос счетов закупки в реестр", department=""), moscow_now(),
            apply=args.apply,
        )
    finally:
        engine.dispose()
    print(format_report(report, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
