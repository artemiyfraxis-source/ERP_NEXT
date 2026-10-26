"""«Приход на склад» (owner 23.10.2026): a delivery header and its lines accepted one by one.

The storekeeper opens a delivery (склад, поставщик, счёт, УПД, назначение по умолчанию, скан УПД), then types the lines
— ideally only article and quantity, the rest is filled from the last lot of the article, the reference DB, the
purchasing nomenclature and the procurement line of the invoice. «Принять» on a line asks for the place and the number
of bags: the line becomes ONE lot through the quick entry's own path (`manual_lot` + `place_manual_lots`: the same row
checks, OLD.YY numbering and `warehouse.manually_received` event, now with the delivery header), and one label job
with that many copies goes to the print queue in the same transaction. A label that cannot be made never costs the
lot: the lot stays, `label_error` says why."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from io import BytesIO
from pathlib import PurePath
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.file_types import sniffed_type
from app.shared.identity import Actor

from . import scan_jobs
from .application import ManualStockItem, UnitOfWork, manual_lot, place_manual_lots, receipt_audit
from .domain import (
    RECEIPT_SCAN_PATH,
    ReceiptConflict,
    ReceiptHeader,
    StockLot,
    WarehouseReceipt,
    WarehouseValidationError,
)
from .label_jobs import COPIES_MAX, add_job
from .labels.settings import DEFAULT_PRINT_SETTINGS
from .labels_application import lot_layout_in, tspl_job

RECEIPTS_LIST_LIMIT = 100
SCAN_FILE_LIMIT = 20 * 1024 * 1024
SCAN_KINDS = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}
PLACES_LIMIT = 5
LOT_INFO_FIELDS = ("name", "series", "item_type", "unit", "manufacturer", "datasheet", "certificate", "description",
                   "storage_conditions")
ARTICLE_INFO_FIELDS = (*LOT_INFO_FIELDS, "expiry_date")


class ExpectedInReceiving(RuntimeError):
    """The line is already on its way through «Приёмка»: accepting it here too would count it twice (409)."""


# (delivery, article) → the receiving line awaiting it (anything with `order_number` and `supplier`) or None.
ExpectedLookup = Callable[[WarehouseReceipt, str], object | None]


def expected_text(found) -> str:
    supplier = f" ({found.supplier})" if found.supplier else ""
    return f"Позиция ожидается в Приёмке: заказ {found.order_number or '—'}{supplier}. Принять здесь всё равно?"


def _audit(uow, receipt: WarehouseReceipt, event_type: str, actor: Actor, now: datetime) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=receipt.id, event_type=event_type, actor=actor, occurred_at=now,
        details={**receipt_audit(receipt), "warehouse": receipt.warehouse, "purpose": receipt.purpose},
    ))


def create_receipt(uow: UnitOfWork, header: ReceiptHeader, actor: Actor, now: datetime) -> WarehouseReceipt:
    receipt = WarehouseReceipt.create(header, now=now, created_by=actor.name)
    with uow:
        uow.receipts.add(receipt)
        _audit(uow, receipt, "warehouse.receipt_created", actor, now)
        uow.commit()
    return receipt


def update_receipt(
    uow: UnitOfWork, receipt_id: UUID, version: int, header: ReceiptHeader, actor: Actor, now: datetime,
) -> tuple[WarehouseReceipt, int]:
    """The header retyped under its version (ReceiptConflict — someone saved it first). Returns it and its lot count."""
    with uow:
        current = uow.receipts.get(receipt_id)
        if current is None:
            raise KeyError("Приход не найден")
        if current.version != version:
            raise ReceiptConflict("Приход изменён другим пользователем: обновите и повторите")
        count = uow.receipts.lots_count([receipt_id]).get(receipt_id, 0)
        updated = current.edited(header, now=now, has_lots=count > 0)
        uow.receipts.save(updated)
        _audit(uow, updated, "warehouse.receipt_edited", actor, now)
        uow.commit()
    return updated, count


def recent_receipts(uow: UnitOfWork, limit: int = 20) -> list[tuple[WarehouseReceipt, int]]:
    """The newest deliveries with their accepted line counts (to continue one)."""
    with uow:
        receipts = uow.receipts.recent(max(1, min(limit, RECEIPTS_LIST_LIMIT)))
        counts = uow.receipts.lots_count([receipt.id for receipt in receipts])
    return [(receipt, counts.get(receipt.id, 0)) for receipt in receipts]


def receipt_with_lots(uow: UnitOfWork, receipt_id: UUID) -> tuple[WarehouseReceipt, list[StockLot]]:
    with uow:
        receipt = uow.receipts.get(receipt_id)
        if receipt is None:
            raise KeyError("Приход не найден")
        return receipt, uow.receipts.lots(receipt_id)


def lot_receipts(uow: UnitOfWork, lots: Iterable[StockLot]) -> dict[UUID, WarehouseReceipt]:
    """The delivery of each lot of a page, read once for the whole page (no lot of a delivery — no read at all)."""
    ids = {lot.receipt_id for lot in lots if lot.receipt_id is not None}
    if not ids:
        return {}
    with uow:
        receipts = getattr(uow, "receipts", None)
        return receipts.by_ids(ids) if receipts is not None else {}


@dataclass(frozen=True)
class AcceptLineCommand:
    receipt_id: UUID
    item: ManualStockItem  # its warehouse is ignored: the delivery's wins
    labels: int
    confirm_expected: bool
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class AcceptedLine:
    receipt: WarehouseReceipt
    lot: StockLot
    label_job_id: UUID | None
    label_error: str


def _queue_labels(uow, lot: StockLot, copies: int, actor: Actor, now: datetime) -> tuple[UUID | None, str]:
    """One job with `copies` labels of the new lot, in the transaction of the lot. Any failure to make the label is
    reported, never raised: the lot is on stock either way and «Этикетка…» prints it later."""
    session = getattr(uow, "session", None)
    if session is None:
        return None, "Партия принята; очередь печати недоступна — напечатайте этикетку из карточки партии"
    try:
        settings = uow.label_settings.load() or DEFAULT_PRINT_SETTINGS
        tspl = tspl_job(lot_layout_in(session, lot, now, settings), settings, copies)
        return add_job(session, tspl=tspl, copies=copies, kind="lot", lot_id=lot.id, created_by=actor.name,
                       now=now), ""
    except Exception as error:  # noqa: BLE001 — a label (fonts, settings, layout) must never cost the accepted lot
        return None, f"Партия принята, этикетка не поставлена в очередь: {error}"


def accept_receipt_line(
    command: AcceptLineCommand, uow: UnitOfWork, expected: ExpectedLookup | None = None,
) -> AcceptedLine:
    """«Принять»: the line → one lot of the delivery (НАЗНАЧЕНИЕ: the line's, else the delivery's, else «Склад») and its
    labels into the print queue, one transaction. Row errors are WarehouseValidationError (422); the line awaited in
    «Приёмка» is ExpectedInReceiving (409) until the storekeeper confirms it."""
    if not 1 <= command.labels <= COPIES_MAX:
        raise WarehouseValidationError(f"Этикеток — от 1 до {COPIES_MAX}")
    with uow:
        receipt = uow.receipts.get(command.receipt_id)
        if receipt is None:
            raise KeyError("Приход не найден")
        item = replace(
            command.item, warehouse=receipt.warehouse,
            purpose=str(command.item.purpose or "").strip() or receipt.purpose,
        )
        draft = manual_lot(item, receipt)
        if not command.confirm_expected and expected is not None:
            found = expected(receipt, draft.article)
            if found is not None:
                raise ExpectedInReceiving(expected_text(found))
        [placed] = place_manual_lots(uow, [(0, draft)], command.actor, command.now, receipt)
        job_id, label_error = _queue_labels(uow, placed.lot, command.labels, command.actor, command.now)
        uow.commit()
    return AcceptedLine(receipt=receipt, lot=placed.lot, label_job_id=job_id, label_error=label_error)


def checked_scan(file_name: str, content: bytes) -> tuple[str, str]:
    """A scan ERP keeps: its file name (a default one when none came) and what the bytes are (PDF / JPG / PNG)."""
    if not content:
        raise WarehouseValidationError("Файл не передан")
    if len(content) > SCAN_FILE_LIMIT:
        raise WarehouseValidationError("Скан УПД больше 20 МБ")
    kind = sniffed_type(content)
    if kind not in SCAN_KINDS:
        raise WarehouseValidationError("Скан УПД — файл PDF, JPG или PNG")
    return str(file_name or "").strip() or f"УПД{SCAN_KINDS[kind]}", kind


def as_pdf(name: str, kind: str, content: bytes) -> tuple[str, str, bytes]:
    """A scan is kept as PDF (owner 23.10.2026: «хранить надо PDF»): a JPG / PNG becomes a one-page PDF at its own
    resolution (300 dpi when the picture says none). A picture Pillow cannot open is kept as it came."""
    if kind == "application/pdf":
        return name, kind, content
    from PIL import Image  # only a picture scan needs it

    try:
        with Image.open(BytesIO(content)) as picture:
            dpi = picture.info.get("dpi") or (300, 300)
            page = picture if picture.mode in ("L", "RGB") else picture.convert("RGB")
            buffer = BytesIO()
            page.save(buffer, "PDF", resolution=float(dpi[0]) or 300.0)
    except (OSError, ValueError):
        return name, kind, content
    return str(PurePath(name).with_suffix(".pdf")), "application/pdf", buffer.getvalue()


def store_scan(uow: UnitOfWork, file_name: str, content: bytes, actor: Actor, now: datetime) -> scan_jobs.ScanJob:
    """«Файл…»: the УПД scan kept in the database (as PDF) with a done scan job pointing at it, one transaction — the
    job is the screen's handle while the file is being read (a model takes 8–100 s, longer than a request may)."""
    name, kind, content = as_pdf(*checked_scan(file_name, content), content)
    file_id = uuid4()
    with uow:
        uow.receipts.add_file(file_id, name, kind, content, actor.name, now)
        uow.session.flush()  # the file row before the job points at it
        job = scan_jobs.add_done_job(uow.session, file_id=file_id, requested_by=actor.name, now=now)
        uow.commit()
    return job


def store_job_scan(uow: UnitOfWork, job_id: UUID, file_name: str, content: bytes, now: datetime) -> str:
    """The scanner agent's file for a claimed job: kept like a hand-uploaded scan (in the name of the storekeeper who
    pressed the button) and the job done, one transaction; returns the file's link. A file ERP does not keep fails the
    job with the reason and raises WarehouseValidationError; KeyError / ScanJobClosed — no such job / not claimed."""
    try:
        name, kind = checked_scan(file_name, content)
    except WarehouseValidationError as error:
        scan_jobs.fail_job(uow, job_id, error=str(error), now=now)
        raise
    name, kind, content = as_pdf(name, kind, content)
    file_id = uuid4()
    with uow:
        session = uow.session
        job = scan_jobs.open_for_result(session, job_id, now)
        uow.receipts.add_file(file_id, name, kind, content, job.requested_by, now)
        session.flush()  # the file row before the job points at it
        scan_jobs.finish_with_file(session, job_id, file_id, now)
        uow.commit()
    return f"{RECEIPT_SCAN_PATH}{file_id}"


SCAN_PAGES_MAX = 20


@dataclass(frozen=True)
class MergedScan:
    job: scan_jobs.ScanJob  # done, pointing at the merged file
    pages: list[UUID]  # the files of the sheets, in page order


def scan_file_id(link: str) -> UUID | None:
    """The file of a scan link («/api/stock/receipts/files/<id>»); None — not one."""
    link = str(link)
    tail = link[len(RECEIPT_SCAN_PATH):] if link.startswith(RECEIPT_SCAN_PATH) else ""
    try:
        return UUID(tail)
    except ValueError:
        return None


def merge_scans(uow: UnitOfWork, links: Sequence[str], actor: Actor, now: datetime) -> MergedScan:
    """A multi-page УПД scanned sheet by sheet (the flatbed has no feeder): the stored scans, in page order, become ONE
    PDF kept as a new file «УПД.pdf» with a done scan job on it (one transaction). Foreign link / unreadable file /
    over 20 MB — WarehouseValidationError."""
    from pypdf import PdfReader, PdfWriter  # only a merge needs it
    from pypdf.errors import PyPdfError

    if not 2 <= len(links) <= SCAN_PAGES_MAX:
        raise WarehouseValidationError(f"Склеить можно от 2 до {SCAN_PAGES_MAX} страниц")
    writer = PdfWriter()
    pages: list[UUID] = []
    for link in links:
        page_id = scan_file_id(link)
        found = scan_file(uow, page_id) if page_id is not None else None
        if found is None:
            raise WarehouseValidationError("Скан не найден — загрузите страницу заново")
        name, content = found
        kind = sniffed_type(content)
        try:
            _name, kind, content = as_pdf(name, kind, content)
            if kind != "application/pdf":
                raise ValueError(kind)
            for page in PdfReader(BytesIO(content)).pages:
                writer.add_page(page)
        except (PyPdfError, ValueError, OSError):
            raise WarehouseValidationError("Страницу скана не удалось прочитать — загрузите её заново") from None
        pages.append(page_id)
    buffer = BytesIO()
    writer.write(buffer)
    merged = buffer.getvalue()
    if len(merged) > SCAN_FILE_LIMIT:
        raise WarehouseValidationError("Скан УПД больше 20 МБ")
    file_id = uuid4()
    with uow:
        uow.receipts.add_file(file_id, "УПД.pdf", "application/pdf", merged, actor.name, now)
        uow.session.flush()
        job = scan_jobs.add_done_job(uow.session, file_id=file_id, requested_by=actor.name, now=now)
        uow.commit()
    return MergedScan(job=job, pages=pages)


def scan_file(uow: UnitOfWork, file_id: UUID) -> tuple[str, bytes] | None:
    with uow:
        return uow.receipts.file(file_id)


def scan_recognition(uow: UnitOfWork, file_id: UUID) -> str:
    """The reading kept with the scan file ('' — not read yet)."""
    with uow:
        return uow.receipts.recognition(file_id)


def keep_scan_recognition(uow: UnitOfWork, file_id: UUID, recognition: str) -> None:
    with uow:
        uow.receipts.keep_recognition(file_id, recognition)
        uow.commit()


UPD_LLM_EDITABLE = ("base_url", "model", "fallback_model", "effort")


def upd_llm_settings(uow: UnitOfWork) -> dict:
    """The saved «Распознавание УПД» settings ({} — never saved), the key included: server side only."""
    with uow:
        return uow.upd_llm_settings.load()


def save_upd_llm_settings(uow: UnitOfWork, values: dict, actor: Actor, now: datetime) -> dict:
    """`values`: the editable fields, and `api_key` only when it changes ('' — removed). The journal gets who changed
    what, never the key."""
    with uow:
        before = uow.upd_llm_settings.load()
        uow.upd_llm_settings.save(values, now, actor.name)
        changed = [name for name in UPD_LLM_EDITABLE if name in values and values[name] != before.get(name, "")]
        parts = [f"{name} = {values[name] or 'по умолчанию'}" for name in changed]
        if "api_key" in values and values["api_key"] != before.get("api_key", ""):
            parts.append("ключ API заменён" if values["api_key"] else "ключ API удалён")
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=UUID(int=0), event_type="warehouse.upd_llm_settings_saved", actor=actor,
            occurred_at=now, details={"message": "; ".join(parts) or "без изменений"},
        ))
        saved = uow.upd_llm_settings.load()
        uow.commit()
    return saved


@dataclass(frozen=True)
class ArticleFacts:
    info: dict | None
    places: list[str]


def article_facts(
    uow: UnitOfWork,
    article: str,
    warehouse: str | None,
    others: Sequence[tuple[str, dict | None]],
) -> ArticleFacts:
    """What «Приход на склад» fills for a typed article: the last lot of it first (every passport field but the expiry
    — a new lot has its own), then `others` in their order — each fills only what is still empty. `source` names
    whatever filled something. `places` — where the article lies with a balance (in `warehouse` when given), the most
    recent first."""
    with uow:
        lots = uow.warehouse.article_lots(article)
    info = {name: "" for name in LOT_INFO_FIELDS}
    info["expiry_date"] = None
    sources: list[str] = []

    def fill(label: str, values: dict | None) -> None:
        if not values:
            return
        filled = False
        for name in ARTICLE_INFO_FIELDS:
            value = values.get(name)
            if value in (None, "") or info[name] not in (None, ""):
                continue
            info[name] = value.isoformat() if isinstance(value, date) else str(value).strip()
            filled = filled or bool(info[name])
        if filled:
            sources.append(label)

    if lots:
        last = lots[0][0]
        fill(f"партия {last.grn}", {name: getattr(last, name) for name in LOT_INFO_FIELDS})
    for label, values in others:
        fill(label, values)
    places = list(dict.fromkeys(
        lot.storage_location for lot, _ in lots
        if lot.balance > 0 and lot.storage_location and (not warehouse or lot.warehouse == warehouse)
    ))[:PLACES_LIMIT]
    return ArticleFacts(info={**info, "source": ", ".join(sources)} if sources else None, places=places)
