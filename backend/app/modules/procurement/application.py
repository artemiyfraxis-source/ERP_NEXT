"""Transactional Procurement use cases expressed against persistence ports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Collection, Mapping, Protocol, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
import logging

from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.shared.events import AuditEvent, OutboxMessage
from app.shared.file_types import stored_content_type
from app.shared.identity import Actor
from app.shared.notifications import NotificationOutbox, notification_requested, publish_notification
from app.shared.print_sheet import PrintSheet

from .invoice_file import (
    INVOICE_FILE_LIMIT, INVOICE_FILE_PATH, InvoiceInfo, invoice_file_name, kp_file_name, recognise_invoice,
)
from .domain import (
    AUTOFILL_TOPIC,
    FACTS_EDITABLE,
    LINE_DOCUMENT_KINDS,
    _PAID_STATUSES,
    KpEntry,
    MoneyEntry,
    PERMISSION_NUMBER_PREFIX,
    ProcurementFacts,
    ProcurementTraceability,
    ProcurementLine,
    ProcurementStatus,
    ProcurementValidationError,
    RECEIPT_STAGES,
    SupplierRequestGroup,
    ReplacementDecision,
    STOCK_COVER_WAREHOUSES,
    StockCover,
    removal_blocker,
    requested_buyer,
    create_backorder_line,
    create_line_from_requested,
    group_supplier_requests,
    BY_SUPPLIER_CLOSED,
    next_backorder_id,
    supplier_from_link,
    next_order_number,
    next_retransfer_revision,
    next_system_id,
    supplier_reference,
    supplier_request_blockers,
    supplier_request_document_title,
    supplier_request_folder,
    supplier_request_letter,
    split_line,
    stage_from_receipts,
)
from .request_pdf import render_request_pdf
from .money import ExchangeRate, FactMoney, is_foreign, normalize_currency, rate_day
from .invoice_file import invoice_date as invoice_day
from .document_items import SupplierDocument, read_supplier_document
from .document_match import DocumentMatch, OrderLineCandidate, match_document_items


_log = logging.getLogger(__name__)

SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


class ProcurementRepository(Protocol):
    def by_source_idempotency_key(self, key: str) -> ProcurementLine | None: ...
    def get(self, line_id: UUID) -> ProcurementLine | None: ...
    def available(
        self, limit: int, after_id: UUID | None = None
    ) -> Sequence[ProcurementLine]: ...
    def by_buyer(
        self,
        actor: Actor,
        limit: int,
        after_id: UUID | None = None,
        accepted_since: date | None = None,
    ) -> Sequence[ProcurementLine]: ...
    def in_work(
        self, limit: int, after_id: UUID | None = None
    ) -> Sequence[ProcurementLine]: ...
    def list(self, limit: int = 10_000) -> Sequence[ProcurementLine]: ...
    def legacy_ids_with_prefix(self, prefix: str) -> Sequence[str]: ...
    def ready_for_request(self) -> Sequence[ProcurementLine]: ...
    def order_numbers_with_prefix(self, prefix: str) -> Sequence[str]: ...
    def permission_numbers_with_prefix(self, prefix: str) -> Sequence[str]: ...
    def has_lines_for_request(self, source_request_id: UUID) -> bool: ...
    def by_source_request_ids(self, source_request_ids: Sequence[UUID]) -> Sequence[ProcurementLine]:
        """RQ-MINE: the current pipeline stage of every handed-off line for a batch of requests."""
        ...
    def revisions_for_designation(self, designation: str) -> Sequence[str]:
        """Revisions of every line whose НАЗНАЧЕНИЕ has the same `designation_key`."""
        ...
    def search(
        self,
        *,
        query: str,
        status: str | None,
        buyer: str | None,
        limit: int,
        after_id: UUID | None = None,
    ) -> Sequence[ProcurementLine]: ...
    def buyer_names(self) -> Sequence[str]: ...
    def excluding_statuses(self, statuses: Sequence[str]) -> Sequence[ProcurementLine]: ...
    def with_statuses(self, statuses: Sequence[str]) -> Sequence[ProcurementLine]: ...
    def add(self, line: ProcurementLine) -> None: ...
    def delete(self, line_id: UUID, expected_version: int) -> bool:
        """Removes the line (with its released «Со склада» history); False — it changed since it was read."""
        ...
    def add_order(self, order: SupplierOrder) -> None: ...
    def get_order(self, order_number: str) -> SupplierOrder | None: ...
    def add_order_document(self, document: SupplierOrderDocument, content: bytes) -> None: ...
    def get_order_document(self, order_number: str) -> tuple[SupplierOrderDocument, bytes] | None: ...


class SupplierDirectory(Protocol):
    """Port to References: the «ссылка → поставщик» map (`supplierMap_`) and its learning (`learnSupplier_`)."""

    def supplier_map(self) -> dict[str, str]: ...
    def learn(self, link: str | None, supplier: str | None, actor: Actor, now: datetime) -> bool: ...
    def country_for_manufacturer(self, manufacturer: str) -> str: ...


class AppendOnlyRepository(Protocol):
    def add(self, item: AuditEvent) -> None: ...


class ConcurrentProcurementUpdate(RuntimeError):
    pass


class OrderLinesMismatch(ConcurrentProcurementUpdate):
    """The rows sent for an order-wide action are not the order's rows of that stage (the order moved on)."""


class DispatchOutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


@dataclass(frozen=True)
class StoredInvoiceFile:
    """An invoice file in ERP storage (`uploadInvoice` put it in the Drive folder «Счета»)."""

    id: UUID
    line_id: UUID
    file_name: str
    content_type: str
    size: int
    uploaded_by: str
    uploaded_at: datetime
    # «other» — invoices, КП, order documents; «permit» / «quality» — РАЗРЕШЕНИЕ / ДОК. КАЧЕСТВА of the line.
    kind: str = "other"


class InvoiceFileRepository(Protocol):
    def add(self, stored: StoredInvoiceFile, content: bytes) -> None: ...
    def get(self, file_id: UUID) -> tuple[StoredInvoiceFile, bytes] | None: ...
    def of_lines(self, line_ids: Sequence[UUID], kinds: Sequence[str]) -> list[StoredInvoiceFile]: ...


class UnitOfWork(Protocol):
    procurement: ProcurementRepository
    audit: AppendOnlyRepository
    outbox: AppendOnlyRepository
    files: InvoiceFileRepository

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


class DispatchUnitOfWork(UnitOfWork, Protocol):
    outbox: DispatchOutboxRepository


@dataclass(frozen=True)
class StatusNotificationDue:
    line_id: UUID
    version: int
    legacy_id: str
    status: str
    initiator_id: UUID | None
    designation: str
    name: str
    expected_arrival: date | None


class StatusNotificationRepository(Protocol):
    def status_notifications_due(self, statuses: Sequence[str], limit: int) -> Sequence[StatusNotificationDue]: ...
    def mark_status_notified(self, line_id: UUID, status: str) -> bool: ...


class StatusNotificationUnitOfWork(Protocol):
    procurement: StatusNotificationRepository
    outbox: NotificationOutbox

    def __enter__(self) -> StatusNotificationUnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


# NOTIFY_PURCHASE_STATUSES (Config.gs:219): the statuses the initiator hears about. Internal
# «Новый»/«В работе»/«Готов к запросу» are deliberately silent (noise).
NOTIFY_INITIATOR_STATUSES = (
    ProcurementStatus.APPROVAL,
    ProcurementStatus.AWAITING_INVOICE,
    ProcurementStatus.TO_PAY,
    ProcurementStatus.IN_TRANSIT,
    ProcurementStatus.QUARANTINE,
    ProcurementStatus.DONE,
    ProcurementStatus.CANCELLED,
)


def notify_initiators(now: datetime, uow: StatusNotificationUnitOfWork, *, limit: int = 500) -> int:
    """OwnerTick.gs:872-903 `notifyInitiatorsSheet_`: a line that reached a notified status other than
    the last one announced tells its initiator once. The mark is set with or without a recipient
    (unmatched initiator = nobody to tell), so a line is never re-scanned for the same status.
    Every status path (buyer actions, receiving sync, cancel) is covered because this reads the state."""
    sent = 0
    with uow:
        due = uow.procurement.status_notifications_due(
            [status.value for status in NOTIFY_INITIATOR_STATUSES], limit,
        )
        for item in due:
            if not uow.procurement.mark_status_notified(item.line_id, item.status):
                continue
            body = (
                "Статус вашей заявки изменился.\n\n"
                f"System ID: {item.legacy_id}\nНазначение: {item.designation}\n"
                f"Наименование: {item.name}\nНовый статус: {item.status}"
                + (
                    f"\nОжидаемая дата поступления: {item.expected_arrival.strftime('%d.%m.%Y')}"
                    if item.expected_arrival else ""
                )
            )
            if publish_notification(uow.outbox, notification_requested(
                key=f"procurement:{item.line_id}:status:v{item.version}:{item.status}",
                now=now,
                title=f"Заявка {item.legacy_id} — статус: {item.status}",
                body=body,
                kind="status",
                reference=item.legacy_id,
                user_ids=(item.initiator_id,),
            )):
                sent += 1
        if due:
            uow.commit()
    return sent


class ExchangeRates(Protocol):
    def rate_to_rub(self, currency: str, day: date) -> Decimal | None: ...


class ManualRateRequired(RuntimeError):
    """The CBR gave no rate for a foreign invoice and the buyer has not typed one yet."""

    def __init__(self, currency: str, day: date):
        super().__init__(f"ЦБ не отдал курс {currency} на {day:%d.%m.%Y} — введите курс вручную")
        self.currency = currency
        self.day = day


class RateUnavailable(RuntimeError):
    def __init__(self, currency: str, day: date):
        super().__init__(f"ЦБ не отдал курс {currency} на {day:%d.%m.%Y} — сверьте позже")
        self.currency = currency
        self.day = day


class PoisonOutboxMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


@dataclass(frozen=True)
class TakeProcurementInWorkCommand:
    line_ids: Sequence[UUID]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SaveProcurementFactsCommand:
    line_id: UUID
    facts: ProcurementFacts
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SupplierOrderDocument:
    """Файл документа запроса в хранилище ERP (SUPPLYMAP: копия шаблона-Google Doc в папке год/месяц/поставщик)."""

    order_number: str
    file_name: str
    folder: str
    content_type: str
    size: int
    created_by: str
    created_at: datetime


SUPPLIER_ORDER_DOCUMENT_TYPE = "application/pdf"


@dataclass(frozen=True)
class SupplierOrder:
    """An issued supplier request: № ЗАКАЗА, ДАТА ЗАКАЗА and the letter (SUPPLYMAP's Google Doc)."""

    order_number: str
    order_date: date
    supplier: str
    task_type: str
    delivery_point: str
    buyer: Actor
    created_at: datetime
    letter: str
    line_ids: tuple[UUID, ...]
    # Документ запроса; None — заказ выдан до хранения документов (его ссылка «ЗАКАЗ» пуста).
    document: SupplierOrderDocument | None = None


@dataclass(frozen=True)
class BuildSupplierRequestsCommand:
    actor: Actor
    now: datetime
    # Only these lines (the buyer ticked them on «Мои позиции»), in this order; None — every ready line of the buyer.
    line_ids: Collection[UUID] | None = None


@dataclass(frozen=True)
class SkippedLine:
    """A line left out of the supplier request, and why (in Russian)."""

    line_id: UUID
    legacy_id: str
    reason: str


@dataclass(frozen=True)
class SupplierRequestsResult:
    orders: list[SupplierOrder]
    lines: list[ProcurementLine]
    skipped: list[SkippedLine]
    other_buyer: int


@dataclass(frozen=True)
class SetReplacementCommand:
    line_id: UUID
    expected_version: int
    replacement: bool
    decision: ReplacementDecision | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SetTraceabilityCommand:
    line_id: UUID
    expected_version: int
    trace: ProcurementTraceability
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SetNoteCommand:
    line_id: UUID
    expected_version: int
    note: str | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SetActualArrivalCommand:
    line_id: UUID
    expected_version: int
    actual_arrival: date | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SetExpectedArrivalCommand:
    line_id: UUID
    expected_version: int
    expected_arrival: date
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RecordInvoiceCommand:
    line_id: UUID
    expected_version: int
    invoice_url: str
    expected_arrival: date
    actor: Actor
    now: datetime
    invoice_number: str | None = None
    invoice_date: date | None = None


@dataclass(frozen=True)
class ReplaceInvoiceCommand:
    line_id: UUID
    expected_version: int
    invoice_url: str
    actor: Actor
    now: datetime
    invoice_number: str | None = None
    invoice_date: date | None = None


@dataclass(frozen=True)
class MarkProcurementPaidCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime
    manual_rate: Decimal | None = None


@dataclass(frozen=True)
class SaveProcurementMoneyCommand:
    line_id: UUID
    expected_version: int
    entry: MoneyEntry
    actor: Actor
    now: datetime
    manual_rate: Decimal | None = None


@dataclass(frozen=True)
class KpRow:
    line_id: UUID
    expected_version: int
    entry: KpEntry


@dataclass(frozen=True)
class SaveKpCommand:
    """«Внести КП»: all «Жду счет» lines of one supplier order, optionally with one invoice for all."""

    order_reference: str
    rows: Sequence[KpRow]
    actor: Actor
    now: datetime
    manual_rate: Decimal | None = None
    invoice_url: str | None = None


@dataclass(frozen=True)
class KpResult:
    lines: list[ProcurementLine]
    invoice_error: str = ""


@dataclass(frozen=True)
class ConfirmBackorderCommand:
    line_id: UUID
    expected_version: int
    expected_arrival: date
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class CancelBackorderCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


class OfflineAction(str, Enum):
    """`PUR_STATUS_OFFLINE` moves: into «Согласование», back out of it, or to «Отменено»."""

    APPROVAL = "approval"
    RESUME = "resume"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ProcurementOfflineCommand:
    line_id: UUID
    expected_version: int
    action: OfflineAction
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SplitProcurementLineCommand:
    line_id: UUID
    expected_version: int
    copies: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ReconcileProcurementRateCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


def _transfer_rules(
    line: ProcurementLine, message: OutboxMessage, suppliers: SupplierDirectory | None
) -> ProcurementLine:
    """`requestRowsToPurchaseLocked_` (Pipeline.gs:82-86): ПОСТАВЩИК from the ССЫЛКА's domain via the
    «ссылка → поставщик» map at the moment of transfer; no match leaves it untouched. ПРИОРИТЕТ is already
    recomputed for the transfer day by Requests (`build_procurement_requested_snapshot`, Pipeline.gs:87-92)."""
    if suppliers is not None and line.link:
        supplier = supplier_from_link(line.link, suppliers.supplier_map())
        if supplier:
            line = replace(line, supplier=supplier)
    return line


def _line_from_requested(
    message: OutboxMessage,
    lines: ProcurementRepository,
    suppliers: SupplierDirectory | None = None,
) -> ProcurementLine:
    return _transfer_rules(_new_line_from_requested(message, lines), message, suppliers)


def _new_line_from_requested(message: OutboxMessage, lines: ProcurementRepository) -> ProcurementLine:
    """`requestRowsToPurchaseLocked_` (Pipeline.gs:98-112): the first transfer of a request is Рев.A under
    the request's System ID; a repeated transfer appends a NEW line — fresh System ID, revision = max letter
    among lines of the same НАЗНАЧЕНИЕ + 1, «Новый» — and leaves the earlier line and its buyer data intact.
    """
    line = create_line_from_requested(message)
    repeated = lines.has_lines_for_request(line.source_request_id)
    # SUPPLYMAP finds «already transferred» by System ID. A retransfer or split line may already hold the
    # request's ID (both number over procurement IDs only), so a first transfer onto a taken ID also gets a
    # fresh one — but stays Рев.A: it is the request's first line (PR-2).
    id_taken = line.legacy_id in lines.legacy_ids_with_prefix(line.legacy_id)
    if not repeated and not id_taken:
        return line
    day = line.created_at.date()
    legacy_id = next_system_id(lines.legacy_ids_with_prefix(f"SM-{day:%Y%m%d}-"), day)
    revision = next_retransfer_revision(lines.revisions_for_designation(line.designation)) if repeated else "A"
    return create_line_from_requested(message, legacy_id=legacy_id, revision=revision)


def _add_requested_line(
    message: OutboxMessage,
    uow: UnitOfWork,
    suppliers: SupplierDirectory | None,
) -> ProcurementLine:
    """The line of one `procurement.requested`. Owner, 05.10: «В закупку» puts it straight on the desk of whoever
    pressed it — taken in work as «Взять в работу» would (facts prefilled, the stage from the facts, the page fill
    queued in this transaction). A message without the buyer (sent before the switch) still lands «Новый»."""
    line = _line_from_requested(message, uow.procurement, suppliers)
    buyer = requested_buyer(message)
    taken = line.take_in_work(buyer) if buyer is not None else line
    uow.procurement.add(taken)
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=line.id,
            event_type="procurement.created",
            actor=SYSTEM_ACTOR,
            occurred_at=message.created_at,
            details={
                "legacy_id": line.legacy_id,
                "source_event_id": str(message.id),
            },
        )
    )
    if buyer is not None:
        uow.audit.add(_taken_event(line, taken, buyer, message.created_at))
        _queue_autofill(taken, message.created_at, uow)
    return taken


def consume_procurement_requested(
    message: OutboxMessage,
    uow: UnitOfWork,
    suppliers: SupplierDirectory | None = None,
) -> ProcurementLine:
    with uow:
        existing = uow.procurement.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                raise ProcurementValidationError(
                    "Ключ события закупки уже использован другим событием"
                )
            return existing

        line = _add_requested_line(message, uow, suppliers)
        uow.commit()
        return line


def dispatch_next_procurement_requested(
    now: datetime,
    uow: DispatchUnitOfWork,
    suppliers: SupplierDirectory | None = None,
) -> ProcurementLine | None:
    with uow:
        message = uow.outbox.next_unpublished("procurement.requested", now)
        if message is None:
            return None
        existing = uow.procurement.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                reason = "Ключ события закупки уже использован другим событием"
                uow.outbox.mark_failed(message.id, now, reason)
                uow.commit()
                raise PoisonOutboxMessage(message.id, reason)
            line = existing
        else:
            try:
                line = _add_requested_line(message, uow, suppliers)
            except ProcurementValidationError as error:
                uow.outbox.mark_failed(message.id, now, str(error))
                uow.commit()
                raise PoisonOutboxMessage(message.id, str(error)) from error
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return line


class ReceiptStatusLookup(Protocol):
    """Receiving's view of the deliveries of each procurement line (read-only port, see
    `app.workflows.receipt_stage`). Procurement never reads Receiving's tables itself."""

    def statuses_for(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, list[str]]: ...

    def arrival_dates_for(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, date]:
        """ФАКТ. ДАТА: the latest inspection date among each line's deliveries (lines without one are absent)."""
        ...


class ExpectedArrivalCopier(Protocol):
    """Write port into Receiving: ОЖИД. ДАТА of a line's open deliveries (batches `-ПN` too) follows the line.
    Best effort — a failure is left to the next tick, which copies again."""

    def copy_expected_arrivals(self, dates: Mapping[UUID, date | None], actor: Actor, now: datetime) -> int: ...


class ReceiptSync(ReceiptStatusLookup, ExpectedArrivalCopier, Protocol):
    pass


def sync_receipt_stages(now: datetime, uow: UnitOfWork, receipts: ReceiptSync) -> int:
    """OwnerTick.gs `syncPurchaseStatusFromWaitlist_`: the receiving status of a paid line's deliveries
    moves the line forward — «В пути» → «Карантин» → «Готово» (only when every batch is accepted).
    Forward only; «Согласование»/«Отменено» and unpaid lines are never touched. Reads the state on every
    tick, so a missed tick or an old receipt catches up by itself.

    The same pass keeps the two dates of SUPPLYMAP's owner tick: ФАКТ. ДАТА from the latest inspection
    (`syncActualArrival_`) and ОЖИД. ДАТА copied into the open deliveries (`syncExpectedDateToWaitlist_`) —
    the buyer's edit copies at once, the tick repairs a copy that missed (a delivery created later, a failure)."""
    with uow:
        lines = list(uow.procurement.with_statuses([status.value for status in RECEIPT_STAGES]))
        if not lines:
            return 0
        ids = [line.id for line in lines]
        statuses = receipts.statuses_for(ids)
        arrivals = receipts.arrival_dates_for(ids)
        moved = 0
        for line in lines:
            stage = stage_from_receipts(statuses.get(line.id, ()))
            updated = line.follow_receipts(stage, arrivals.get(line.id))
            if updated is line:
                continue
            uow.procurement.add(updated)
            if updated.status is not line.status:
                uow.audit.add(
                    AuditEvent(
                        id=uuid4(),
                        aggregate_id=line.id,
                        event_type="procurement.receipt_stage",
                        actor=SYSTEM_ACTOR,
                        occurred_at=now,
                        details={
                            "legacy_id": line.legacy_id,
                            "from_status": line.status.value,
                            "to_status": updated.status.value,
                        },
                    )
                )
            if updated.actual_arrival != line.actual_arrival:
                uow.audit.add(
                    AuditEvent(
                        id=uuid4(),
                        aggregate_id=line.id,
                        event_type="procurement.actual_arrival",
                        actor=SYSTEM_ACTOR,
                        occurred_at=now,
                        details={
                            "legacy_id": line.legacy_id,
                            "actual_arrival": updated.actual_arrival.isoformat() if updated.actual_arrival else "",
                        },
                    )
                )
            moved += 1
        if moved:
            uow.commit()
    copied = receipts.copy_expected_arrivals({line.id: line.expected_arrival for line in lines}, SYSTEM_ACTOR, now)
    return moved + copied


def dispatch_next_backorder_requested(
    now: datetime,
    uow: DispatchUnitOfWork,
) -> ProcurementLine | None:
    """`applyBackorders_`: a short first inspection of a paid line becomes a `-Д` line in «Согласование»."""
    with uow:
        message = uow.outbox.next_unpublished("procurement.backorder_requested", now)
        if message is None:
            return None

        def poison(reason: str) -> PoisonOutboxMessage:
            uow.outbox.mark_failed(message.id, now, reason)
            uow.commit()
            return PoisonOutboxMessage(message.id, reason)

        existing = uow.procurement.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                raise poison("Ключ дозавоза уже использован другим событием")
            line = existing
        else:
            try:
                original = uow.procurement.get(UUID(str(message.payload.get("procurement_line_id"))))
            except ValueError:
                original = None
            if original is None:
                raise poison("Исходная строка закупки для дозавоза не найдена")
            legacy_id = next_backorder_id(
                original.legacy_id,
                uow.procurement.legacy_ids_with_prefix(original.legacy_id + "-Д"),
            )
            try:
                line = create_backorder_line(original, message, legacy_id)
            except ProcurementValidationError as error:
                raise poison(str(error)) from error
            uow.procurement.add(line)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=line.id,
                    event_type="procurement.backorder_created",
                    actor=SYSTEM_ACTOR,
                    occurred_at=message.created_at,
                    details={
                        "legacy_id": line.legacy_id,
                        "from_legacy_id": original.legacy_id,
                        "quantity": str(line.actual_quantity),
                        "receiving_line_id": str(message.payload.get("receiving_line_id") or ""),
                    },
                )
            )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return line


def take_procurement_in_work(
    command: TakeProcurementInWorkCommand,
    uow: UnitOfWork,
    suppliers: SupplierDirectory | None = None,
) -> list[ProcurementLine]:
    return take_procurement_in_work_batch(command, uow, suppliers)[0]


def take_procurement_in_work_batch(
    command: TakeProcurementInWorkCommand,
    uow: UnitOfWork,
    suppliers: SupplierDirectory | None = None,
) -> tuple[list[ProcurementLine], list[ProcurementLine]]:
    """`portalTakeInWork` → `takeRowsInWork_`: every selected «Новый» line becomes the buyer's; a line that
    is already further along (taken by another buyer, or past «В работе») is skipped, not an error.
    Returns (lines now the buyer's, skipped lines). The buyer's own «В работе» line is a read-only retry.

    `inWorkSideEffects_` (Guards.gs:458): an empty ПОСТАВЩИК is filled from the ССЫЛКА's domain through the
    «домен → поставщик» map right here (a map lookup, cheap). The page-dependent fields — НАИМЕНОВАНИЕ (САЙТ),
    ИЗГОТОВИТЕЛЬ and the СТРАНА from it — are only queued (`procurement.autofill_requested`, same transaction):
    50 lines must not wait on 50 supplier sites, `app.workflows.procurement_autofill` fills them in the background."""
    line_ids = list(dict.fromkeys(command.line_ids))
    if not line_ids:
        raise ProcurementValidationError("Не выбрано ни одной строки закупки")

    with uow:
        current: list[ProcurementLine] = []
        for line_id in line_ids:
            line = uow.procurement.get(line_id)
            if line is None:
                raise KeyError(f"Procurement line not found: {line_id}")
            current.append(line)

        taken: list[ProcurementLine] = []
        skipped: list[ProcurementLine] = []
        changed: list[tuple[ProcurementLine, ProcurementLine, tuple[str, ...]]] = []
        supplier_map: dict[str, str] | None = None
        for before in current:
            try:
                after = before.take_in_work(command.actor)
            except ProcurementValidationError:
                skipped.append(before)
                continue
            filled: tuple[str, ...] = ()
            if after != before:
                if suppliers is not None and not (after.supplier or "").strip() and (after.link or "").strip():
                    if supplier_map is None:
                        supplier_map = _supplier_map(suppliers)
                    after, filled = after.fill_empty(supplier=supplier_from_link(after.link, supplier_map))
                    after = after.with_work_stage()
                changed.append((before, after, filled))
            taken.append(after)
        if not changed:
            return taken, skipped

        for before, after, filled in changed:
            uow.procurement.add(after)
            uow.audit.add(_taken_event(before, after, command.actor, command.now))
            if filled:
                uow.audit.add(_autofilled_event(after, filled, command.actor, command.now))
            _queue_autofill(after, command.now, uow)
        uow.commit()
        return taken, skipped


def _taken_event(before: ProcurementLine, after: ProcurementLine, actor: Actor, now: datetime) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        aggregate_id=after.id,
        event_type="procurement.taken_in_work",
        actor=actor,
        occurred_at=now,
        details={
            "legacy_id": after.legacy_id,
            "from_status": before.status.value,
            "to_status": after.status.value,
        },
    )


def _queue_autofill(line: ProcurementLine, now: datetime, uow: UnitOfWork) -> None:
    """НАИМЕНОВАНИЕ (САЙТ), ИЗГОТОВИТЕЛЬ and СТРАНА from the page behind the link: queued in the caller's transaction,
    filled by `app.workflows.procurement_autofill` while the line keeps this version."""
    if not line.autofill_wanted:
        return
    uow.outbox.add(
        OutboxMessage(
            id=uuid4(),
            topic=AUTOFILL_TOPIC,
            idempotency_key=f"autofill:{line.id}:v{line.version}",
            created_at=now,
            payload={
                "line_id": str(line.id),
                "version": str(line.version),
                "legacy_id": line.legacy_id,
            },
        )
    )


def _supplier_map(suppliers: SupplierDirectory) -> dict[str, str]:
    """The map is a convenience: a References failure must not stop the buyer from taking lines into work."""
    try:
        return suppliers.supplier_map()
    except Exception:  # noqa: BLE001
        _log.warning("supplier map unavailable for the take-in-work fill", exc_info=True)
        return {}


def _autofilled_event(
    line: ProcurementLine, fields: Sequence[str], actor: Actor, now: datetime
) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        aggregate_id=line.id,
        event_type="procurement.autofilled",
        actor=actor,
        occurred_at=now,
        details={"legacy_id": line.legacy_id, "fields": list(fields)},  # type: ignore[dict-item]
    )


@dataclass(frozen=True)
class PageInfo:
    """What one page behind a ССЫЛКА gives (`fetchPageInfo_`): the item's name on the site and its brand."""

    title: str = ""
    brand: str = ""


class PageInfoFetcher(Protocol):
    """Port to the supplier's site. A network error or a code >= 400 is an EMPTY `PageInfo`, never an exception."""

    def info(self, url: str) -> PageInfo: ...


@dataclass(frozen=True)
class AutofillRequest:
    """One queued line, as it stood when the take queued it: what the background fill may still write."""

    message_id: UUID
    line_id: UUID
    version: int
    legacy_id: str
    link: str
    site_name: str
    manufacturer: str
    country: str


@dataclass(frozen=True)
class ApplyAutofillCommand:
    request: AutofillRequest
    site_name: str
    manufacturer: str
    country: str
    now: datetime


def next_autofill_request(now: datetime, uow: DispatchUnitOfWork) -> AutofillRequest | None:
    """Claim the next queued line that still has something to fill; None — the queue is empty.

    A line that is gone, changed since the take (`version`), off the buyer's desk or already complete needs no
    fill: its message is closed here, silently. The live message stays open until `apply_autofill` closes it,
    so the site fetch in between runs outside any transaction."""
    with uow:
        while True:
            message = uow.outbox.next_unpublished(AUTOFILL_TOPIC, now)
            if message is None:
                uow.commit()
                return None
            try:
                line_id = UUID(str(message.payload.get("line_id")))
                version = int(str(message.payload.get("version")))
            except ValueError as error:
                reason = "procurement.autofill_requested: invalid line_id or version"
                uow.outbox.mark_failed(message.id, now, reason)
                uow.commit()
                raise PoisonOutboxMessage(message.id, reason) from error
            line = uow.procurement.get(line_id)
            if (
                line is None
                or line.version != version
                or line.status not in FACTS_EDITABLE
                or not line.autofill_wanted
            ):
                uow.outbox.mark_published(message.id, now)
                continue
            uow.commit()  # closes the stale messages above and releases the claim before the site is fetched
            return AutofillRequest(
                message_id=message.id,
                line_id=line.id,
                version=line.version,
                legacy_id=line.legacy_id,
                link=(line.link or "").strip(),
                site_name=(line.site_name or "").strip(),
                manufacturer=(line.trace.manufacturer or "").strip(),
                country=(line.trace.country or "").strip(),
            )


def apply_autofill(command: ApplyAutofillCommand, uow: DispatchUnitOfWork) -> tuple[str, ...]:
    """Write what the site gave into the line's EMPTY fields and close the message. Returns the fields written.

    The line is read again: a version other than the queued one means the buyer (or the pipeline) touched it
    while the site was being fetched — nothing is written. A field the buyer filled meanwhile is not overwritten
    either (`fill_empty`). The version is NOT bumped: an open «Заполнить данные» form must stay valid, and the
    write is still guarded by the version at the database (a concurrent change makes the commit fail, not win)."""
    request = command.request
    with uow:
        line = uow.procurement.get(request.line_id)
        written: tuple[str, ...] = ()
        if line is not None and line.version == request.version and line.status in FACTS_EDITABLE:
            filled, written = line.fill_empty(
                site_name=command.site_name, manufacturer=command.manufacturer, country=command.country
            )
            if written:
                uow.procurement.add(filled)
                uow.audit.add(_autofilled_event(filled, written, SYSTEM_ACTOR, command.now))
        uow.outbox.mark_published(request.message_id, command.now)
        uow.commit()
        return written


def fail_autofill(message_id: UUID, now: datetime, error: str, uow: DispatchUnitOfWork) -> None:
    """The fill broke for a reason of ours (not the site's — those are an empty `PageInfo`): retry with backoff."""
    with uow:
        uow.outbox.mark_failed(message_id, now, error)
        uow.commit()


def save_procurement_facts(
    command: SaveProcurementFactsCommand,
    uow: UnitOfWork,
    suppliers: SupplierDirectory | None = None,
) -> ProcurementLine:
    line, before = _save_procurement_facts(command, uow)
    # `portalSavePurchaseFacts` → `learnSupplier_`: the map learns «домен ссылки → поставщик» only when the
    # buyer fills an EMPTY supplier (a changed supplier is a one-off purchase and must not retrain the map).
    if suppliers is not None and before is not None and not (before.supplier or "").strip() and (line.supplier or "").strip():
        try:
            suppliers.learn(line.link, line.supplier, command.actor, command.now)
        except Exception:  # the facts are already saved; a failed map write must not turn them into an error
            _log.warning("learnSupplier failed for %s", line.legacy_id, exc_info=True)
    return line


def _save_procurement_facts(
    command: SaveProcurementFactsCommand,
    uow: UnitOfWork,
) -> tuple[ProcurementLine, ProcurementLine | None]:
    with uow:
        current = uow.procurement.get(command.line_id)
        if current is None:
            raise KeyError(f"Procurement line not found: {command.line_id}")
        if current.version != command.expected_version:
            try:
                retry_result = current.update_facts(command.facts, command.actor)
            except ProcurementValidationError as error:
                raise ConcurrentProcurementUpdate(
                    "Строка изменилась после загрузки формы"
                ) from error
            if retry_result == current:
                return current, None
            raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы")
        updated = current.update_facts(command.facts, command.actor)
        if updated == current:
            return current, None

        uow.procurement.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=updated.id,
                event_type="procurement.facts_saved",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "legacy_id": updated.legacy_id,
                    "from_status": current.status.value,
                    "to_status": updated.status.value,
                },
            )
        )
        uow.commit()
        return updated, current


def _load_versioned(line_id: UUID, expected_version: int, uow: UnitOfWork) -> ProcurementLine:
    current = uow.procurement.get(line_id)
    if current is None:
        raise KeyError(f"Procurement line not found: {line_id}")
    if current.version != expected_version:
        raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы")
    return current


def _save_transition(
    current: ProcurementLine,
    updated: ProcurementLine,
    *,
    event_type: str,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
    details: dict[str, str] | None = None,
) -> ProcurementLine:
    if updated == current:
        return current
    uow.procurement.add(updated)
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=updated.id,
            event_type=event_type,
            actor=actor,
            occurred_at=now,
            details={
                "legacy_id": updated.legacy_id,
                "from_status": current.status.value,
                "to_status": updated.status.value,
                **(details or {}),
            },
        )
    )
    return updated


def build_supplier_requests(
    command: BuildSupplierRequestsCommand, uow: UnitOfWork
) -> SupplierRequestsResult:
    """`buildSupplierRequestsCore_`: one order per supplier + task type over the buyer's ready lines.

    № ЗАКАЗА is `<prefix>-NNN`, numbered through the whole procurement table per prefix; ДАТА ЗАКАЗА is
    the Moscow day. Every line of a formed group goes «Жду счет» in the same transaction as its order.

    Ticked lines (owner, 05.10): a ticked line that cannot go — not ready, not the buyer's, in a group that is not
    formed — is returned in `skipped` with the reason and the rest are formed; nothing to form is not an error then.
    """
    with uow:
        ready = uow.procurement.ready_for_request()
        skipped: list[SkippedLine] = []
        if command.line_ids is not None:
            chosen = list(dict.fromkeys(command.line_ids))
            chosen_ids = set(chosen)
            ready = [line for line in ready if line.id in chosen_ids]
            ready_ids = {line.id for line in ready}
            for line_id in chosen:
                if line_id not in ready_ids:
                    skipped.append(_not_ready_for_request(line_id, uow.procurement.get(line_id), command.actor))
            skipped.extend(
                SkippedLine(line.id, line.legacy_id, "строка другого закупщика — запрос формирует тот, кто взял её в работу")
                for line in ready
                if line.buyer is None or line.buyer.id != command.actor.id
            )
        groups, other_buyer = group_supplier_requests(ready, command.actor)
        if not groups and command.line_ids is None:
            message = f"Нет ваших строк «{ProcurementStatus.READY.value}»"
            if other_buyer:
                message += (
                    f". Есть {other_buyer} строк(и) других закупщиков — "
                    "их формирует тот, кто взял их в работу"
                )
            raise ProcurementValidationError(message)
        order_date = _moscow_day(command.now)
        issued: dict[str, list[str]] = {}
        orders: list[SupplierOrder] = []
        lines: list[ProcurementLine] = []
        for group in groups:
            blocker = supplier_request_blockers(group)
            if blocker:
                skipped.extend(SkippedLine(line.id, line.legacy_id, blocker) for line in group.lines)
                continue
            taken = issued.setdefault(
                group.prefix, list(uow.procurement.order_numbers_with_prefix(group.prefix))
            )
            number = next_order_number(taken, group.prefix)
            taken.append(number)
            order, sent = _issue_order(group, number, order_date, command, uow)
            orders.append(order)
            lines.extend(sent)
        if orders:
            uow.commit()
        return SupplierRequestsResult(orders=orders, lines=lines, skipped=skipped, other_buyer=other_buyer)


def _not_ready_for_request(line_id: UUID, line: ProcurementLine | None, actor: Actor) -> SkippedLine:
    """Why a ticked line is not among the ready ones."""
    if line is None:
        return SkippedLine(line_id, "", "строка не найдена")
    if line.buyer is not None and line.buyer.id != actor.id:
        reason = "строка другого закупщика — запрос формирует тот, кто взял её в работу"
    elif line.status is ProcurementStatus.IN_WORK:
        missing = line.request_missing()
        reason = ("не заполнены: " + ", ".join(missing)) if missing else "строка «В работе» — сохраните её данные"
    elif line.status is ProcurementStatus.NEW:
        reason = "строка не взята в работу"
    elif line.order_number:
        reason = f"запрос уже сформирован ({line.order_number}), статус «{line.status.value}»"
    else:
        reason = f"статус «{line.status.value}» — запрос формируется только из «{ProcurementStatus.READY.value}»"
    return SkippedLine(line.id, line.legacy_id, reason)


def _issue_order(
    group: SupplierRequestGroup,
    number: str,
    order_date: date,
    command: BuildSupplierRequestsCommand,
    uow: UnitOfWork,
) -> tuple[SupplierOrder, list[ProcurementLine]]:
    reference = supplier_reference(group.request_date, group.designation, number)
    sent = [
        _save_transition(
            line,
            line.send_supplier_request(number, order_date, reference, command.actor),
            event_type="procurement.supplier_request_sent",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={"order_number": number},
        )
        for line in group.lines
    ]
    letter = supplier_request_letter(group, number)
    title = supplier_request_document_title(number, order_date)
    content = render_request_pdf(f"Запрос {title}", letter)
    document = SupplierOrderDocument(
        order_number=number,
        file_name=f"{title}.pdf",
        folder=supplier_request_folder(order_date, group.supplier),
        content_type=SUPPLIER_ORDER_DOCUMENT_TYPE,
        size=len(content),
        created_by=command.actor.name,
        created_at=command.now,
    )
    order = SupplierOrder(
        order_number=number,
        order_date=order_date,
        supplier=group.supplier,
        task_type=group.task_type,
        delivery_point=group.delivery_point,
        buyer=command.actor,
        created_at=command.now,
        letter=letter,
        line_ids=tuple(line.id for line in group.lines),
        document=document,
    )
    uow.procurement.add_order(order)
    uow.procurement.add_order_document(document, content)
    return order, sent


def supplier_order(order_number: str, uow: UnitOfWork) -> SupplierOrder:
    with uow:
        order = uow.procurement.get_order(order_number)
    if order is None:
        raise KeyError(f"Supplier order not found: {order_number}")
    return order


def supplier_order_document(order_number: str, uow: UnitOfWork) -> tuple[SupplierOrderDocument, bytes]:
    """PDF документа запроса (ссылка «ЗАКАЗ» строк заказа). Заказы, выданные до PDF, хранят HTML-письмо —
    для них PDF собирается из текста письма заказа."""
    with uow:
        found = uow.procurement.get_order_document(order_number)
        order = uow.procurement.get_order(order_number) if found is not None else None
    if found is None or order is None:
        raise KeyError(f"Supplier order document not found: {order_number}")
    meta, content = found
    if content.startswith(b"%PDF"):
        return meta, content
    title = supplier_request_document_title(order.order_number, order.order_date)
    pdf = render_request_pdf(f"Запрос {title}", order.letter)
    return replace(meta, file_name=f"{title}.pdf", content_type=SUPPLIER_ORDER_DOCUMENT_TYPE, size=len(pdf)), pdf


def procurement_line(line_id: UUID, uow: UnitOfWork) -> ProcurementLine:
    """Read a single line, e.g. to render «РАЗРЕШЕНИЕ НА ЗАМЕНУ» (`_peek`'s non-CBR twin)."""
    with uow:
        current = uow.procurement.get(line_id)
    if current is None:
        raise KeyError(f"Procurement line not found: {line_id}")
    return current


# Печатный бланк (браузерная печать — app.shared.print_sheet, DOC-1). Порт docTemplateSpec_('replacement')
# (SUPPLYMAP DocGen.gs:658-679); ИЗГОТОВИТЕЛЬ/СТРАНА с того же spec живут на партии склада в ERP_NEXT, а
# не на строке закупки — опущены здесь (решено агентом, см. ARCHITECTURE.md) до отдельного переноса.
REPLACEMENT_PERMISSION_CONCLUSION = (
    "Применённый аналог годен к применению, требованиям конструкторской документации соответствует."
)


def build_replacement_permission_sheet(line: ProcurementLine) -> PrintSheet:
    """«РАЗРЕШЕНИЕ НА ЗАМЕНУ №…»: exists only once РЕШ. ЗАМЕНЫ is «Разрешено» (`applyReplacementDecision_`,
    Guards.gs:632-637) — any other outcome voids `permission_number`, so there is nothing to print."""
    if line.replacement_decision is not ReplacementDecision.ALLOWED or not line.permission_number:
        raise ProcurementValidationError(f"Замена по {line.legacy_id} не разрешена — печатать нечего")
    quantity = f"{format(line.calculated_quantity.normalize(), 'f')} {line.calculated_unit}".strip()
    when = line.permission_date.strftime("%d.%m.%Y") if line.permission_date else ""
    return PrintSheet(
        title=f"РАЗРЕШЕНИЕ НА ЗАМЕНУ {line.permission_number}",
        subtitle=f"Дата: {when} · Назначение: {line.designation}",
        columns=("Поле", "Значение"),
        rows=(
            ("Заменяется (артикул КД)", line.design_article or ""),
            ("Применяется", line.site_name or ""),
            ("Категория", line.category),
            ("Кол-во", quantity),
            ("Поставщик", line.supplier or ""),
        ),
        note=f"Заключение: {REPLACEMENT_PERMISSION_CONCLUSION}",
        sign="ИСПОЛНИТЕЛЬ: ______________________        ЗАКАЗЧИК: ______________________",
    )


def set_procurement_traceability(
    command: SetTraceabilityCommand,
    uow: UnitOfWork,
    suppliers: SupplierDirectory | None = None,
) -> ProcurementLine:
    """ИЗГОТОВИТЕЛЬ … СТАНДАРТ of one line. An edited ИЗГОТОВИТЕЛЬ with an empty СТРАНА takes the country
    from the «изготовитель → страна» map (Guards.gs:369-371, `countryForManufacturer_`)."""
    trace = command.trace
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        maker = (trace.manufacturer or "").strip()
        if (
            suppliers is not None
            and maker
            and maker != (current.trace.manufacturer or "")
            and not (trace.country or "").strip()
        ):
            country = suppliers.country_for_manufacturer(maker)
            if country:
                trace = replace(trace, country=country)
        updated = current.set_traceability(trace, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.traceability_saved",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        if result != current:
            uow.commit()
        return result


def set_procurement_note(command: SetNoteCommand, uow: UnitOfWork) -> ProcurementLine:
    """КОММЕНТАРИЙ of one line (the buyer's free note)."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.set_note(command.note, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.note_saved",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={"note": updated.note or ""},
        )
        if result != current:
            uow.commit()
        return result


@dataclass(frozen=True)
class SetDatasheetCommand:
    line_id: UUID
    expected_version: int
    datasheet: str | None
    actor: Actor
    now: datetime


def set_procurement_datasheet(command: SetDatasheetCommand, uow: UnitOfWork) -> ProcurementLine:
    """DATASHEET of one line: a link or a text, edited like КОММЕНТАРИЙ."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.set_datasheet(command.datasheet, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.datasheet_saved",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={"datasheet": updated.datasheet or ""},
        )
        if result != current:
            uow.commit()
        return result


@dataclass(frozen=True)
class UploadLineDocumentCommand:
    """A РАЗРЕШЕНИЕ (`permit`) or ДОК. КАЧЕСТВА (`quality`) file chosen in the line card."""

    line_id: UUID
    kind: str
    file_name: str
    content_type: str
    content: bytes
    actor: Actor
    now: datetime


def upload_line_document(command: UploadLineDocumentCommand, uow: UnitOfWork) -> tuple[ProcurementLine, StoredInvoiceFile]:
    """Store the file under its kind with the line, in one transaction. A quality document sets ДОК. КАЧЕСТВА «Да»
    on the line (`attach_document`) — no version is asked: adding a file never overwrites what the buyer typed."""
    if command.kind not in LINE_DOCUMENT_KINDS:
        raise ProcurementValidationError("Вид файла: разрешение или документ качества")
    if not command.content:
        raise ProcurementValidationError("Файл не передан")
    if len(command.content) > INVOICE_FILE_LIMIT:
        raise ProcurementValidationError("Файл больше 20 МБ")
    with uow:
        current = uow.procurement.get(command.line_id)
        if current is None:
            raise KeyError("Строка закупки не найдена")
        updated = current.attach_document(command.kind, command.actor)
        stored = StoredInvoiceFile(
            id=uuid4(),
            line_id=current.id,
            file_name=str(command.file_name or "").strip()[:512] or "файл",
            content_type=stored_content_type(command.content),
            size=len(command.content),
            uploaded_by=command.actor.name,
            uploaded_at=command.now,
            kind=command.kind,
        )
        uow.files.add(stored, command.content)
        if updated != current:
            uow.procurement.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=current.id,
                event_type="procurement.document_file_uploaded",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "legacy_id": current.legacy_id,
                    "file_id": str(stored.id),
                    "kind": command.kind,
                    "name": stored.file_name,
                    "quality_document": updated.quality_document,
                },
            )
        )
        uow.commit()
    return updated, stored


def line_documents(line_ids: Sequence[UUID], uow: UnitOfWork) -> dict[UUID, list[StoredInvoiceFile]]:
    """РАЗРЕШЕНИЕ and ДОК. КАЧЕСТВА files of a list of lines — one query for the whole list."""
    if not line_ids:
        return {}
    with uow:
        found = uow.files.of_lines(list(line_ids), tuple(LINE_DOCUMENT_KINDS))
    result: dict[UUID, list[StoredInvoiceFile]] = {}
    for stored in found:
        result.setdefault(stored.line_id, []).append(stored)
    return result


def set_procurement_actual_arrival(command: SetActualArrivalCommand, uow: UnitOfWork) -> ProcurementLine:
    """ФАКТ. ДАТА by hand (optional): the buyer fills or corrects it; receiving's tick keeps filling it too."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.set_actual_arrival(command.actual_arrival, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.actual_arrival_set",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "actual_arrival": command.actual_arrival.isoformat() if command.actual_arrival else "",
                "previous_actual_arrival": current.actual_arrival.isoformat() if current.actual_arrival else "",
            },
        )
        if result != current:
            uow.commit()
        return result


def set_procurement_expected_arrival(
    command: SetExpectedArrivalCommand,
    uow: UnitOfWork,
    receiving: ExpectedArrivalCopier | None = None,
) -> ProcurementLine:
    """ОЖИД. ДАТА by hand (`gateAwaitInvoice_`). A line already in receiving copies the date into its open
    deliveries at once (`syncExpectedDateToWaitlist_`); the receipt tick repeats the copy if this one missed."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.set_expected_arrival(command.expected_arrival, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.expected_arrival_set",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "expected_arrival": command.expected_arrival.isoformat(),
                "previous_expected_arrival": current.expected_arrival.isoformat() if current.expected_arrival else "",
            },
        )
        if result != current:
            uow.commit()
    if receiving is not None and result.status in RECEIPT_STAGES:
        receiving.copy_expected_arrivals({result.id: result.expected_arrival}, command.actor, command.now)
    return result


def set_procurement_replacement(command: SetReplacementCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        permission_number = current.permission_number
        permission_date = current.permission_date
        newly_allowed = (
            command.replacement
            and command.decision is ReplacementDecision.ALLOWED
            and current.replacement_decision is not ReplacementDecision.ALLOWED
        )
        if newly_allowed:
            # `generateDocFor_`/`nextSystemId_` (DocGen.gs:112-151, SystemId.gs:13): number once, on the
            # transition into «Разрешено» — an unchanged «Разрешено» short-circuits above and never gets here.
            permission_date = _moscow_day(command.now)
            taken = list(uow.procurement.permission_numbers_with_prefix(PERMISSION_NUMBER_PREFIX))
            permission_number = next_system_id(taken, permission_date, PERMISSION_NUMBER_PREFIX)
        updated = current.set_replacement(
            command.replacement,
            command.decision,
            command.actor,
            permission_number=permission_number,
            permission_date=permission_date,
        )
        result = _save_transition(
            current,
            updated,
            event_type="procurement.replacement_set",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "replacement": "Да" if updated.replacement else "Нет",
                "decision": updated.replacement_decision.value if updated.replacement_decision else "",
                "permission_number": updated.permission_number or "",
            },
        )
        if result != current:
            uow.commit()
        return result


def record_invoice(command: RecordInvoiceCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.record_invoice(
            command.invoice_url,
            command.expected_arrival,
            command.actor,
            invoice_number=command.invoice_number,
            invoice_date=command.invoice_date,
        )
        result = _save_transition(
            current,
            updated,
            event_type="procurement.invoice_recorded",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        uow.commit()
        return result


@dataclass(frozen=True)
class UploadInvoiceFileCommand:
    """The file chosen in the invoice form; `line_id` is the line (or the first line of the order)."""

    line_id: UUID
    file_name: str
    content_type: str
    content: bytes
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class UploadedInvoice:
    file: StoredInvoiceFile
    url: str
    info: InvoiceInfo
    hint: str


def invoice_file_url(file_id: UUID) -> str:
    return f"{INVOICE_FILE_PATH}{file_id}"


def _invoice_upload_line(command: UploadInvoiceFileCommand, uow: UnitOfWork) -> ProcurementLine:
    line = uow.procurement.get(command.line_id)
    if line is None:
        raise KeyError("Строка закупки не найдена")
    line._require_owner(command.actor)
    if line.status is ProcurementStatus.CANCELLED:
        raise ProcurementValidationError("Счёт отменённой строки не загружается")
    return line


def upload_invoice_file(command: UploadInvoiceFileCommand, uow: UnitOfWork) -> UploadedInvoice:
    """`uploadInvoice`, first half: recognise № and date, store the file named after them.

    SUPPLYMAP writes the link, № and date into the rows in the same call; ERP hands them to the invoice
    form, which the buyer sends through the usual invoice / replacement / «Внести КП» command, so the pay
    gate and the status move stay in one place.
    """
    if not command.content:
        raise ProcurementValidationError("Файл не передан")
    if len(command.content) > INVOICE_FILE_LIMIT:
        raise ProcurementValidationError("Файл счёта больше 20 МБ")
    # Access first, then the (budgeted) reading outside any transaction, then the store re-checks the line (audit F05).
    with uow:
        _invoice_upload_line(command, uow)
    info, hint = recognise_invoice(command.content)
    with uow:
        line = _invoice_upload_line(command, uow)
        stored = StoredInvoiceFile(
            id=uuid4(),
            line_id=line.id,
            file_name=invoice_file_name(
                info, line.supplier or "", line.designation, line.order_number or "", command.file_name,
            ),
            content_type=stored_content_type(command.content),
            size=len(command.content),
            uploaded_by=command.actor.name,
            uploaded_at=command.now,
        )
        uow.files.add(stored, command.content)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=line.id,
                event_type="procurement.invoice_file_uploaded",
                actor=command.actor,
                occurred_at=command.now,
                details={"file_id": str(stored.id), "name": stored.file_name},
            )
        )
        uow.commit()
    return UploadedInvoice(file=stored, url=invoice_file_url(stored.id), info=info, hint=hint)


def invoice_file(file_id: UUID, uow: UnitOfWork) -> tuple[StoredInvoiceFile, bytes] | None:
    with uow:
        return uow.files.get(file_id)


def replace_invoice(command: ReplaceInvoiceCommand, uow: UnitOfWork) -> ProcurementLine:
    """Invoice correction: the audit event keeps the replaced invoice and marks the line «счёт заменён»."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.replace_invoice(
            command.invoice_url,
            command.actor,
            invoice_number=command.invoice_number,
            invoice_date=command.invoice_date,
        )
        result = _save_transition(
            current,
            updated,
            event_type="procurement.invoice_replaced",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "previous_invoice_url": current.invoice_url or "",
                "previous_invoice_number": current.invoice_number or "",
                "previous_invoice_date": current.invoice_date.isoformat() if current.invoice_date else "",
            },
        )
        if result != current:
            uow.commit()
        return result


_WAREHOUSE_BY_POINT = {
    "г. москва, ул. 6-я радиальная, д. 17": "ЦАР",
    "г. раменское, ул. 100-й свирской дивизии, д. 52": "ЖУК",
}


def warehouse_for_delivery_point(value: str) -> str:
    normalized = " ".join(str(value or "").casefold().split())
    warehouse = _WAREHOUSE_BY_POINT.get(normalized)
    if warehouse is None:
        raise ProcurementValidationError("Для точки отгрузки не настроен склад приёмки")
    return warehouse


_MOSCOW = ZoneInfo("Europe/Moscow")


def _moscow_day(value: datetime) -> date:
    return value.astimezone(_MOSCOW).date() if value.tzinfo is not None else value.date()


def _peek(line_id: UUID, uow: UnitOfWork) -> ProcurementLine:
    # Read outside the write transaction so the CBR request never holds row locks.
    with uow:
        current = uow.procurement.get(line_id)
    if current is None:
        raise KeyError(f"Procurement line not found: {line_id}")
    return current


def _choose_rate(
    line: ProcurementLine,
    currency: str | None,
    day: date,
    manual_rate: Decimal | None,
    rates: ExchangeRates | None,
) -> ExchangeRate | None:
    """Stored CBR rate of that day, else a fresh CBR rate, else the rate the buyer typed."""
    code = normalize_currency(currency)
    if not is_foreign(code):
        return None
    stored = line.money.rate if line.money.source_currency == code else None
    if stored is not None and not stored.manual and stored.day == day:
        return stored
    value = rates.rate_to_rub(code, day) if rates is not None else None
    if value is not None:
        return ExchangeRate(value, day)
    if manual_rate is not None:
        if manual_rate <= 0:
            raise ProcurementValidationError("Курс должен быть больше нуля")
        return ExchangeRate(manual_rate, day, manual=True)
    if stored is not None and stored.day == day:
        return stored  # the earlier manual rate stays until the CBR answers
    raise ManualRateRequired(code, day)


def _plain(value: object) -> str:
    return "" if value is None else str(value)


def _money_details(before: FactMoney, after: FactMoney) -> dict[str, str]:
    details = {
        "currency": _plain(after.currency),
        "price": _plain(after.price),
        "price_with_vat": _plain(after.price_with_vat),
        "sum": _plain(after.sum),
    }
    if after.source_currency:
        details.update(
            source_currency=after.source_currency,
            source_price=_plain(after.source_price),
            source_price_with_vat=_plain(after.source_price_with_vat),
            source_sum=_plain(after.source_sum),
            exchange_rate=_plain(after.exchange_rate),
            exchange_rate_date=_plain(after.exchange_rate_date),
            exchange_rate_source="manual" if after.exchange_rate_manual else "cbr",
        )
    if before.exchange_rate_manual and before.exchange_rate != after.exchange_rate:
        details.update(previous_exchange_rate=_plain(before.exchange_rate), previous_sum=_plain(before.sum))
    return details


def save_procurement_money(
    command: SaveProcurementMoneyCommand,
    uow: UnitOfWork,
    rates: ExchangeRates | None = None,
) -> ProcurementLine:
    current = _peek(command.line_id, uow)
    day = rate_day(
        paid_on=_moscow_day(current.paid_at) if current.paid_at else None,
        invoice_date=command.entry.invoice_date,
        stored=current.money.exchange_rate_date,
        today=_moscow_day(command.now),
    )
    rate = _choose_rate(current, command.entry.amounts.currency, day, command.manual_rate, rates)
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.update_money(command.entry, rate, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.money_saved",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details=_money_details(current.money, updated.money),
        )
        if result != current:
            uow.commit()
        return result


def reconcile_procurement_rate(
    command: ReconcileProcurementRateCommand,
    uow: UnitOfWork,
    rates: ExchangeRates | None = None,
) -> ProcurementLine:
    current = _peek(command.line_id, uow)
    money = current.money
    if not money.exchange_rate_manual or not money.source_currency or money.exchange_rate_date is None:
        raise ProcurementValidationError("Курс уже взят у ЦБ — сверять нечего")
    value = rates.rate_to_rub(money.source_currency, money.exchange_rate_date) if rates is not None else None
    if value is None:
        raise RateUnavailable(money.source_currency, money.exchange_rate_date)
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.reconcile_rate(ExchangeRate(value, money.exchange_rate_date), command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.rate_reconciled",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details=_money_details(current.money, updated.money),
        )
        uow.commit()
        return result


def mark_procurement_paid(
    command: MarkProcurementPaidCommand,
    uow: UnitOfWork,
    rates: ExchangeRates | None = None,
) -> ProcurementLine:
    rate = None
    peeked = _peek(command.line_id, uow)
    if peeked.status is ProcurementStatus.TO_PAY and peeked.money.source_currency:
        rate = _choose_rate(
            peeked, peeked.money.source_currency, _moscow_day(command.now), command.manual_rate, rates,
        )
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.mark_paid(command.actor, command.now, rate)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.paid",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details=_money_details(current.money, updated.money),
        )
        if result != current:
            uow.outbox.add(_receiving_expected(updated, command.now))
            uow.commit()
        return result


def _order_reference(value: str) -> str:
    reference = str(value or "").strip()
    if not reference:
        raise ProcurementValidationError("Не задан № заказа")
    return reference


def kp_order_lines(order_reference: str, actor: Actor, uow: UnitOfWork) -> list[ProcurementLine]:
    """`openKpForm_` + `kpOrderRows_`: the buyer's order lines still waiting for the invoice."""
    reference = _order_reference(order_reference)
    with uow:
        lines = [
            line
            for line in uow.procurement.by_buyer(actor, 10_000)
            if line.order_number == reference
            and line.status is ProcurementStatus.AWAITING_INVOICE
        ]
    if not lines:
        raise ProcurementValidationError(
            f"Нет строк заказа {reference} в статусе «{ProcurementStatus.AWAITING_INVOICE.value}»"
        )
    return lines


def _kp_rate(line: ProcurementLine, entry: KpEntry, command: SaveKpCommand, rates) -> ExchangeRate | None:
    day = rate_day(
        paid_on=None,
        invoice_date=entry.money.invoice_date,
        stored=line.money.exchange_rate_date,
        today=_moscow_day(command.now),
    )
    return _choose_rate(line, entry.money.amounts.currency, day, command.manual_rate, rates)


def save_kp(
    command: SaveKpCommand,
    uow: UnitOfWork,
    rates: ExchangeRates | None = None,
) -> KpResult:
    """`submitKp_`: one transaction for the whole order, then the optional invoice for every line.

    An invoice the pay gate refuses does not roll the offer data back — the error is returned, as the
    form reports `invoiceError` after the rows are already written.
    """
    reference = _order_reference(command.order_reference)
    if not command.rows:
        raise ProcurementValidationError("Нет строк для сохранения")
    line_ids = [row.line_id for row in command.rows]
    if len(set(line_ids)) != len(line_ids):
        raise ProcurementValidationError("Строка заказа передана дважды")
    chosen = {row.line_id: _kp_rate(_peek(row.line_id, uow), row.entry, command, rates) for row in command.rows}
    with uow:
        saved = []
        changed = False
        for row in command.rows:
            current = _load_versioned(row.line_id, row.expected_version, uow)
            if current.order_number != reference:
                raise ProcurementValidationError(f"{current.legacy_id} не входит в заказ {reference}")
            updated = current.apply_kp(row.entry, chosen[row.line_id], command.actor)
            result = _save_transition(
                current,
                updated,
                event_type="procurement.kp_saved",
                actor=command.actor,
                now=command.now,
                uow=uow,
                details={"order_reference": reference, **_money_details(current.money, updated.money)},
            )
            changed = changed or result != current
            saved.append(result)
        if changed:
            uow.commit()
    if not command.invoice_url:
        return KpResult(saved)
    try:
        with uow:
            current = [_load_versioned(line.id, line.version, uow) for line in saved]
            invoiced = _invoice_order(current, command.invoice_url, command.actor, command.now, uow)
            uow.commit()
        return KpResult(invoiced)
    except (ProcurementValidationError, ConcurrentProcurementUpdate) as error:
        return KpResult(saved, invoice_error=str(error))


def _invoice_order(
    lines: Sequence[ProcurementLine],
    invoice_url: str,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
    *,
    expected_arrival: date | None = None,
    invoice_number: str | None = None,
    invoice_date: date | None = None,
    order_wide: bool = False,
) -> list[ProcurementLine]:
    """`uploadInvoice` over the order rows (already loaded in the caller's transaction): one invoice for all of
    them, all or none — every row is checked before the first write, the caller commits. «Жду счет» rows go
    «В оплату». The order-wide invoice (`order_wide`, owner 05.10) also fills an empty НАИМЕНОВАНИЕ (Счет) and
    replaces the invoice of «В оплату» rows (with ОЖИД. ДАТА, when it moved)."""
    planned: list[tuple[ProcurementLine, list[tuple[ProcurementLine, str, dict[str, str]]]]] = []
    for current in lines:
        try:
            steps = _invoice_steps(
                current, invoice_url, actor,
                expected_arrival=expected_arrival, invoice_number=invoice_number, invoice_date=invoice_date,
                order_wide=order_wide,
            )
        except ProcurementValidationError as error:
            raise ProcurementValidationError(f"{current.legacy_id}: {error}") from error
        planned.append((current, steps))
    invoiced = []
    for current, steps in planned:
        line = current
        for updated, event_type, details in steps:
            line = _save_transition(line, updated, event_type=event_type, actor=actor, now=now, uow=uow, details=details)
        invoiced.append(line)
    return invoiced


def _invoice_steps(
    current: ProcurementLine,
    invoice_url: str,
    actor: Actor,
    *,
    expected_arrival: date | None,
    invoice_number: str | None,
    invoice_date: date | None,
    order_wide: bool,
) -> list[tuple[ProcurementLine, str, dict[str, str]]]:
    """The audited transitions the invoice makes on one row, each built on the previous one."""
    named = current.with_invoice_item_name() if order_wide else current
    order = {"order_reference": current.order_number or ""}
    if current.status is ProcurementStatus.TO_PAY and order_wide:
        replaced = named.replace_invoice(invoice_url, actor, invoice_number=invoice_number, invoice_date=invoice_date)
        if replaced.version == current.version and replaced != current:
            replaced = replace(replaced, version=current.version + 1)  # only НАИМЕНОВАНИЕ (Счет) was filled
        steps = []
        if replaced != current:
            steps.append((replaced, "procurement.invoice_replaced", {
                **order,
                "previous_invoice_url": current.invoice_url or "",
                "previous_invoice_number": current.invoice_number or "",
                "previous_invoice_date": current.invoice_date.isoformat() if current.invoice_date else "",
            }))
        base = steps[-1][0] if steps else current
        if expected_arrival is not None and expected_arrival != base.expected_arrival:
            steps.append((base.set_expected_arrival(expected_arrival, actor), "procurement.expected_arrival_set", {
                **order,
                "expected_arrival": expected_arrival.isoformat(),
                "previous_expected_arrival": base.expected_arrival.isoformat() if base.expected_arrival else "",
            }))
        return steps
    arrival = expected_arrival or current.expected_arrival
    if arrival is None and current.status is ProcurementStatus.AWAITING_INVOICE:
        raise ProcurementValidationError(
            "Нельзя загрузить счёт — сначала заполните: " + ", ".join(named.invoice_missing())
        )
    updated = named.record_invoice(
        invoice_url, arrival or current.created_at.date(), actor,
        invoice_number=invoice_number, invoice_date=invoice_date,
    )
    return [(updated, "procurement.invoice_recorded", order)]


def _receiving_expected(line: ProcurementLine, now: datetime) -> OutboxMessage:
    """Self-contained snapshot for Receiving: it never reads Procurement tables."""
    return OutboxMessage(
        id=uuid4(),
        topic="receiving.expected",
        idempotency_key=f"procurement:{line.id}:receiving",
        created_at=now,
        payload={
            "procurement_line_id": str(line.id),
            "legacy_id": line.legacy_id,
            "warehouse": warehouse_for_delivery_point(line.delivery_point),
            "article": line.design_article or line.calculated_name,
            "name": line.calculated_name,
            "item_type": line.item_type,
            "purpose": line.designation,
            "goal": line.project_reference,
            "source_link": line.link,
            "supplier": line.supplier,
            "ordered_quantity": str(line.actual_quantity),
            "unit": line.actual_unit,
            "quality_document_required": line.quality_document == "Да",
            # № ЗАКАЗА and ОЖИД. ДАТА: the receiving queue groups and orders by them (portalReceiveRows).
            "order_number": line.order_number or "",
            "expected_arrival": line.expected_arrival.isoformat() if line.expected_arrival else None,
            # Traceability typed in procurement (Config.gs:523-525: ПРОИЗВОДИТЕЛЬ, СТРАНА, № ДОКУМЕНТА joined
            # into the ВК act from Закупка); Receiving fills only what it has empty.
            "manufacturer": line.trace.manufacturer or "",
            "country": line.trace.country or "",
            "quality_document_type": line.trace.quality_document_type or "",
            "quality_document_number": line.trace.quality_document_number or "",
            "quality_document_date": (
                line.trace.quality_document_date.isoformat() if line.trace.quality_document_date else None
            ),
            "standard": line.trace.standard or "",
            "invoice_item_name": line.invoice_item_name or "",
        },
    )


def confirm_procurement_backorder(command: ConfirmBackorderCommand, uow: UnitOfWork) -> ProcurementLine:
    """Resend confirmed: the paid remainder is expected by Receiving like any delivery."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.confirm_backorder(command.expected_arrival, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.backorder_confirmed",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        if result != current:
            uow.outbox.add(_receiving_expected(updated, command.now))
            uow.commit()
        return result


def cancel_procurement_backorder(command: CancelBackorderCommand, uow: UnitOfWork) -> ProcurementLine:
    """`applyBackorderCancellations_`: the remainder goes back to Requests as a new request."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.cancel_backorder(command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.backorder_cancelled",
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        if result != current:
            uow.outbox.add(
                OutboxMessage(
                    id=uuid4(),
                    topic="requests.reorder_requested",
                    idempotency_key=f"procurement:{updated.id}:reorder",
                    created_at=command.now,
                    payload={
                        "source_request_id": str(updated.source_request_id),
                        "procurement_line_id": str(updated.id),
                        "legacy_id": updated.legacy_id,
                        "quantity": str(updated.actual_quantity),
                        "unit": updated.actual_unit,
                        "buyer_id": str(command.actor.id),
                        "buyer_name": command.actor.name,
                        "buyer_department": command.actor.department,
                    },
                )
            )
            uow.commit()
        return result


_OFFLINE_EVENTS = {
    OfflineAction.APPROVAL: "procurement.approval_requested",
    OfflineAction.RESUME: "procurement.approved",
    OfflineAction.CANCEL: "procurement.cancelled",
}


def set_procurement_offline_status(command: ProcurementOfflineCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        if command.action is OfflineAction.APPROVAL:
            updated = current.put_on_approval(command.actor)
        elif command.action is OfflineAction.RESUME:
            updated = current.resume_from_approval(command.actor)
        else:
            updated = current.cancel(command.actor)
            if updated != current and current.stock_covered > 0:
                # A cancelled line needs nothing: its «Со склада» reserve goes back to the lots.
                covers = [cover for cover in uow.stock_covers.for_line(current.id) if cover.active]
                released, _, _ = _release_covers(current, covers, command.actor, command.now, uow)
                updated = replace(updated, stock_covered=updated.stock_covered - released)
        result = _save_transition(
            current,
            updated,
            event_type=_OFFLINE_EVENTS[command.action],
            actor=command.actor,
            now=command.now,
            uow=uow,
        )
        if result != current:
            uow.commit()
        return result


def split_procurement_line(
    command: SplitProcurementLineCommand, uow: UnitOfWork
) -> tuple[ProcurementLine, list[ProcurementLine]]:
    """`portalSplitPurchaseRow` → `applyPendingSplits_`: one request → N suppliers, N new lines.

    Any buyer may split any line (`portalCanWork_`, no ownership check). The source line keeps its data;
    only its version moves, so a repeated click on a stale form gets 409 instead of N more copies.
    Owner, 05.10: the copies go to the splitter's «В работе» with an empty ПОСТАВЩИК (audited as taken, the page
    fill queued as on «Взять в работу»).
    """
    _validate_split_actor(command.actor)
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        day = _moscow_day(command.now)
        copies = split_line(
            current,
            command.copies,
            uow.procurement.legacy_ids_with_prefix(f"SM-{day:%Y%m%d}-"),
            day,
            command.now,
            buyer=command.actor,
        )
        source = replace(current, version=current.version + 1)
        _save_transition(
            current,
            source,
            event_type="procurement.split",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "copies": str(len(copies)),
                "legacy_ids": ", ".join(copy.legacy_id for copy in copies),
            },
        )
        for copy in copies:
            uow.procurement.add(copy)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=copy.id,
                    event_type="procurement.created",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": copy.legacy_id,
                        "split_from": current.legacy_id,
                        "source_request_id": str(copy.source_request_id),
                    },
                )
            )
            uow.audit.add(_taken_event(replace(copy, status=ProcurementStatus.NEW), copy, command.actor, command.now))
            _queue_autofill(copy, command.now, uow)
        uow.commit()
        return source, copies


def _validate_split_actor(actor: Actor) -> None:
    if not str(actor.name or "").strip():
        raise ProcurementValidationError("Не определён закупщик")


# --- «Со склада» (owner 2026-10-04) -------------------------------------------------------------------

STOCK_CANDIDATES_LIMIT = 50


class StockCoverRepository(Protocol):
    def get(self, cover_id: UUID) -> StockCover | None: ...
    def for_line(self, line_id: UUID) -> Sequence[StockCover]: ...
    def active_on_lots(self, lot_ids: Sequence[UUID]) -> Sequence[StockCover]: ...
    def active_without_lot(self, line_ids: Sequence[UUID]) -> Sequence[StockCover]: ...
    def reserved_on_lot(self, lot_id: UUID) -> Decimal: ...
    def add(self, cover: StockCover) -> None: ...


class StockLotRepository(Protocol):
    """Port to the warehouse lots: the cover reserves on the lot through the lot's own РЕЗЕРВ rules."""

    def get(self, lot_id: UUID) -> StockLot | None: ...
    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLot]: ...
    def add(self, lot: StockLot) -> None: ...


class StockCoverUnitOfWork(UnitOfWork, Protocol):
    stock_covers: StockCoverRepository
    warehouse: StockLotRepository


@dataclass(frozen=True)
class CoverFromStockCommand:
    line_id: UUID
    expected_version: int
    # None — «Есть на складе, без ГРН»: the line is covered, no lot holds a reserve.
    lot_id: UUID | None
    quantity: Decimal
    actor: Actor
    now: datetime
    # Only without a lot, optional: ЦАР / ЖУК / "" (otherwise the lot's own warehouse).
    warehouse: str = ""


@dataclass(frozen=True)
class BindStockCoverCommand:
    line_id: UUID
    expected_version: int
    cover_id: UUID
    lot_id: UUID
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ReleaseStockCoverCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime
    # None — every active cover of the line; otherwise only this one.
    cover_id: UUID | None = None


@dataclass(frozen=True)
class StockCandidates:
    line: ProcurementLine
    query: str
    lots: list[StockLot]
    # Active «Со склада» covers on the listed lots (who already holds their РЕЗЕРВ), by lot id.
    holders: dict[UUID, list[StockCover]]
    covers: list[StockCover]


def _shown(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _lot_label(lot_grn: str, package: str) -> str:
    return lot_grn + (f"/{package}" if package else "")


def _lot_audit(
    lot_id: UUID, event_type: str, cover: StockCover, quantity: Decimal, actor: Actor, now: datetime, uow,
) -> None:
    """The lot's own trail («Журнал склада»): who reserved / released how much of it and for which line."""
    uow.audit.add(AuditEvent(
        id=uuid4(),
        aggregate_id=lot_id,
        event_type=event_type,
        actor=actor,
        occurred_at=now,
        details={
            "quantity": _shown(quantity),
            "unit": cover.unit,
            "holder": cover.label,
            "procurement_line_id": str(cover.line_id),
            "cover_id": str(cover.id),
        },
    ))


def stock_candidates(
    line_id: UUID, uow: StockCoverUnitOfWork, *, query: str = "", limit: int = STOCK_CANDIDATES_LIMIT,
) -> StockCandidates:
    """Lots that can cover the line: the warehouse search (ё = е, Latin look-alikes, comma = dot) by the typed
    query, otherwise by АРТИКУЛ (КД) and, when it finds nothing, by НАИМЕНОВАНИЕ (Расчет). Lots with a balance;
    the same unit first, then the most available."""
    with uow:
        line = uow.procurement.get(line_id)
        if line is None:
            raise KeyError(f"Procurement line not found: {line_id}")
        typed = str(query or "").strip()
        searches = [typed] if typed else [
            text for text in (str(line.design_article or "").strip(), line.calculated_name.strip()) if text
        ]
        used = ""
        found: list[StockLot] = []
        for text in searches:
            found = list(uow.warehouse.list(limit=500, query=text))
            used = text
            if found:
                break
        unit = line.calculated_unit.strip().casefold().rstrip(".")
        found.sort(key=lambda lot: (
            lot.unit.strip().casefold().rstrip(".") != unit, -lot.available, lot.warehouse, lot.grn, lot.package,
        ))
        lots = found[:limit]
        holders: dict[UUID, list[StockCover]] = {}
        for cover in uow.stock_covers.active_on_lots([lot.id for lot in lots]):
            holders.setdefault(cover.lot_id, []).append(cover)
        covers = list(uow.stock_covers.for_line(line.id))
        return StockCandidates(line=line, query=used, lots=lots, holders=holders, covers=covers)


def without_grn_quantities(lines: Sequence[ProcurementLine], uow: StockCoverUnitOfWork) -> dict[UUID, Decimal]:
    """How much of each line is covered «Есть на складе, без ГРН» and still waits for «Привязать ГРН» (0 when
    nothing). Only lines with «Со склада» are looked up; a unit of work without covers (test fakes) gives zeros."""
    result = {line.id: Decimal("0") for line in lines}
    covered = [line.id for line in lines if line.stock_covered > 0]
    if not covered:
        return result
    with uow:
        covers = getattr(uow, "stock_covers", None)
        found = list(covers.active_without_lot(covered)) if covers is not None else []
    for cover in found:
        if cover.lot_id is None and cover.line_id in result:
            result[cover.line_id] += cover.quantity
    return result


def cover_from_stock(command: CoverFromStockCommand, uow: StockCoverUnitOfWork) -> tuple[ProcurementLine, StockCover]:
    """«Со склада»: reserve `quantity` of one lot for the line in the same transaction as the line moves.

    The reserve is the lot's РЕЗЕРВ (as kitting reserves), so ДОСТУПНО drops for everyone and a reserve over
    what is available is refused. All of КОЛ-ВО (Расчет) covered → «Готово» without money; a part → the rest
    stays to buy (one line, `stock_covered` + `to_buy`, no split).

    Without `lot_id` — «Есть на складе, без ГРН» (owner, 06.10): the same line rules (stages, buyer, no more than
    is left to buy) in the line's own unit, but no lot is touched and nothing is reserved."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        quantity = Decimal(command.quantity)
        if command.lot_id is None:
            warehouse = str(command.warehouse or "").strip()
            if warehouse and warehouse not in STOCK_COVER_WAREHOUSES:
                raise ProcurementValidationError(f"Склад «{warehouse}» неизвестен — ЦАР, ЖУК или не указан")
            updated, trimmed = current.cover_from_stock(command.quantity, current.calculated_unit, command.actor)
            cover = _new_cover(
                current, command, trimmed, lot_id=None, warehouse=warehouse, grn="", package="", storage_location="",
                unit=current.calculated_unit,
            )
        else:
            lot = uow.warehouse.get(command.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {command.lot_id}")
            updated, trimmed = current.cover_from_stock(command.quantity, lot.unit, command.actor)
            uow.warehouse.add(_reserve_on(lot, quantity))
            cover = _new_cover(
                current, command, trimmed, lot_id=lot.id, warehouse=lot.warehouse, grn=lot.grn, package=lot.package,
                storage_location=lot.storage_location, unit=lot.unit,
            )
            _lot_audit(lot.id, "warehouse.reserved_for_procurement", cover, quantity, command.actor, command.now, uow)
        uow.stock_covers.add(cover)
        _save_transition(
            current,
            updated,
            event_type="procurement.stock_covered",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "cover_id": str(cover.id),
                "lot_id": str(cover.lot_id or ""),
                "warehouse": cover.warehouse,
                "grn": cover.grn,
                "package": cover.package,
                "quantity": _shown(quantity),
                "stock_covered": _shown(updated.stock_covered),
                "to_buy": _shown(updated.to_buy),
            },
        )
        uow.commit()
        return updated, cover


def _new_cover(
    line: ProcurementLine, command: CoverFromStockCommand, trimmed: Decimal, *, lot_id: UUID | None, warehouse: str,
    grn: str, package: str, storage_location: str, unit: str,
) -> StockCover:
    return StockCover(
        id=uuid4(),
        line_id=line.id,
        legacy_id=line.legacy_id,
        project_reference=line.project_reference,
        lot_id=lot_id,
        warehouse=warehouse,
        grn=grn,
        package=package,
        storage_location=storage_location,
        unit=unit,
        quantity=Decimal(command.quantity),
        trimmed=trimmed,
        created_at=command.now,
        created_by=command.actor.name,
        created_by_id=command.actor.id,
    )


def _reserve_on(lot: StockLot, quantity: Decimal) -> StockLot:
    """The lot with `quantity` more in РЕЗЕРВ, or the buyer-facing refusal when it has less available."""
    try:
        return lot.reserve(quantity)
    except WarehouseValidationError as error:
        raise ProcurementValidationError(
            f"На партии {_lot_label(lot.grn, lot.package)} доступно {_shown(max(lot.available, Decimal('0')))}"
            f" {lot.unit} — зарезервировать {_shown(quantity)} нельзя"
        ) from error


def bind_stock_cover(command: BindStockCoverCommand, uow: StockCoverUnitOfWork) -> tuple[ProcurementLine, StockCover]:
    """«Привязать ГРН» to a cover made «Есть на складе, без ГРН»: the chosen lot reserves the whole covered quantity
    with the checks of «Со склада» (unit, ДОСТУПНО) — a lot with less is refused, never a partial binding. The line
    keeps its quantities and stage; its version moves so a stale form gets 409."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        cover = uow.stock_covers.get(command.cover_id)
        if cover is None or cover.line_id != current.id:
            raise KeyError(f"Stock cover not found: {command.cover_id}")
        lot = uow.warehouse.get(command.lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {command.lot_id}")
        updated = current.bind_stock_cover(lot.unit, command.actor)
        bound = cover.bind_lot(
            lot_id=lot.id, warehouse=lot.warehouse, grn=lot.grn, package=lot.package,
            storage_location=lot.storage_location, unit=lot.unit,
        )
        uow.warehouse.add(_reserve_on(lot, bound.quantity))
        uow.stock_covers.add(bound)
        _lot_audit(lot.id, "warehouse.reserved_for_procurement", bound, bound.quantity, command.actor, command.now, uow)
        _save_transition(
            current,
            updated,
            event_type="procurement.stock_cover_bound",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "cover_id": str(bound.id),
                "lot_id": str(lot.id),
                "warehouse": lot.warehouse,
                "grn": lot.grn,
                "package": lot.package,
                "quantity": _shown(bound.quantity),
            },
        )
        uow.commit()
        return updated, bound


def _cover_place(cover: StockCover) -> str:
    if cover.lot_id is None:
        return f"{cover.warehouse} без ГРН".strip()
    return f"{cover.warehouse} {_lot_label(cover.grn, cover.package)}"


def _release_covers(
    line: ProcurementLine, covers: Sequence[StockCover], actor: Actor, now: datetime, uow,
) -> tuple[Decimal, Decimal, list[StockCover]]:
    """Give each cover's held quantity back to its lot; a cover «без ГРН» holds none, only the cover closes.
    → (released, КОЛ-ВО (факт) to give back, the covers)."""
    released = Decimal("0")
    restore = Decimal("0")
    done: list[StockCover] = []
    for cover in covers:
        if cover.lot_id is not None:
            lot = uow.warehouse.get(cover.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {cover.lot_id}")
            try:
                uow.warehouse.add(lot.release_reserve(cover.quantity))
            except WarehouseValidationError as error:
                raise ProcurementValidationError(
                    f"На партии {_lot_label(lot.grn, lot.package)} резерв меньше, чем держит «Со склада» — "
                    "проверьте РЕЗЕРВ на складе"
                ) from error
            _lot_audit(lot.id, "warehouse.procurement_reserve_released", cover, cover.quantity, actor, now, uow)
        released += cover.quantity
        restore += min(cover.trimmed, cover.quantity)
        done.append(cover.release(now))
        uow.stock_covers.add(done[-1])
    return released, restore, done


def release_stock_cover(
    command: ReleaseStockCoverCommand, uow: StockCoverUnitOfWork,
) -> tuple[ProcurementLine, list[StockCover]]:
    """«Снять "Со склада"»: the reserve (what a kit has not taken over yet) goes back to the lot and the line
    reopens — «Готово» by the cover returns to the stage its data reached, the quantity is to buy again."""
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        covers = [
            cover for cover in uow.stock_covers.for_line(current.id)
            if cover.active and (command.cover_id is None or cover.id == command.cover_id)
        ]
        if not covers:
            raise ProcurementValidationError("На строке нет резерва «Со склада»")
        total = sum((cover.quantity for cover in covers), Decimal("0"))
        restore = sum((min(cover.trimmed, cover.quantity) for cover in covers), Decimal("0"))
        updated = current.release_stock_cover(total, restore, command.actor)
        _, _, released = _release_covers(current, covers, command.actor, command.now, uow)
        _save_transition(
            current,
            updated,
            event_type="procurement.stock_cover_released",
            actor=command.actor,
            now=command.now,
            uow=uow,
            details={
                "cover_ids": ", ".join(str(cover.id) for cover in covers),
                "lots": ", ".join(_cover_place(cover) for cover in covers),
                "quantity": _shown(total),
                "stock_covered": _shown(updated.stock_covered),
                "to_buy": _shown(updated.to_buy),
            },
        )
        uow.commit()
        return updated, released


def available_procurement(
    uow: UnitOfWork, *, limit: int = 100, after_id: UUID | None = None
) -> list[ProcurementLine]:
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        return list(uow.procurement.available(limit, after_id))


def in_work_procurement(
    uow: UnitOfWork, *, limit: int = 100, after_id: UUID | None = None
) -> list[ProcurementLine]:
    """«Лист закупки — просмотр, что едет» (ID-6): every buyer's lines between «В работе» and «Готово»."""
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        return list(uow.procurement.in_work(limit, after_id))


def buyer_procurement(
    actor: Actor,
    uow: UnitOfWork,
    *,
    limit: int = 100,
    after_id: UUID | None = None,
    accepted_since: date | None = None,
) -> list[ProcurementLine]:
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        return list(uow.procurement.by_buyer(actor, limit, after_id, accepted_since))


def procurement_lines(
    uow: UnitOfWork,
    *,
    query: str = "",
    status: str | None = None,
    buyer: str | None = None,
    limit: int = 100,
    after_id: UUID | None = None,
) -> tuple[list[ProcurementLine], list[str]]:
    """The whole procurement sheet (`portalViewDefs_` purchase): every status and buyer, a search over the
    line's text and filters by status and buyer; also the buyer names for the filter."""
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        lines = list(
            uow.procurement.search(
                query=" ".join(str(query or "").split()),
                status=status or None,
                buyer=buyer or None,
                limit=limit,
                after_id=after_id,
            )
        )
        return lines, sorted(set(uow.procurement.buyer_names()), key=str.casefold)


def procurement_by_supplier(uow: UnitOfWork) -> list[ProcurementLine]:
    """`portalPurchaseBySupplier`: every buyer's open lines (not «В пути»/«Готово»/«Отменено») for the
    per-supplier overview; grouping happens in the client, as in SUPPLYMAP."""
    with uow:
        return list(uow.procurement.excluding_statuses([status.value for status in BY_SUPPLIER_CLOSED]))


@dataclass(frozen=True)
class MarkPaidBatchItem:
    line_id: UUID
    expected_version: int


@dataclass(frozen=True)
class MarkPaidBatchCommand:
    items: tuple[MarkPaidBatchItem, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class MarkPaidBatchResult:
    lines: list[ProcurementLine]
    already: int
    no_invoice: list[str]
    failed: list[tuple[str, str]]


def mark_procurement_paid_batch(
    command: MarkPaidBatchCommand,
    uow_factory,
    rates: ExchangeRates | None = None,
) -> MarkPaidBatchResult:
    """`portalMarkPaid` → `markRowsPaid_`: each selected line independently — already paid is counted and
    skipped, a line without an invoice (not «В оплату») is listed and skipped, the rest go «В пути»."""
    items = list({item.line_id: item for item in command.items}.values())
    if not items:
        raise ProcurementValidationError("Не выбрано ни одной строки")
    done: list[ProcurementLine] = []
    already = 0
    no_invoice: list[str] = []
    failed: list[tuple[str, str]] = []
    for item in items:
        try:
            line = _peek(item.line_id, uow_factory())
        except KeyError:
            failed.append((str(item.line_id), "Строка не найдена"))
            continue
        if line.paid_at is not None or line.status in _PAID_STATUSES:
            already += 1
            continue
        if line.status is not ProcurementStatus.TO_PAY or not line.invoice_url:
            no_invoice.append(line.legacy_id)
            continue
        try:
            done.append(
                mark_procurement_paid(
                    MarkProcurementPaidCommand(
                        line_id=item.line_id,
                        expected_version=item.expected_version,
                        actor=command.actor,
                        now=command.now,
                    ),
                    uow_factory(),
                    rates,
                )
            )
        except ManualRateRequired as error:
            failed.append((line.legacy_id, f"{error} — проведите оплату строки отдельно с ручным курсом"))
        except (ProcurementValidationError, ConcurrentProcurementUpdate, RateUnavailable) as error:
            failed.append((line.legacy_id, str(error)))
    return MarkPaidBatchResult(lines=done, already=already, no_invoice=no_invoice, failed=failed)


# --- Заказ целиком (owner, 05.10): «Цена факт» по строкам, счёт и оплата один раз на заказ ------------------------


@dataclass(frozen=True)
class FactPriceRow:
    line_id: UUID
    expected_version: int
    price_with_vat: Decimal | None
    legacy_id: str = ""


@dataclass(frozen=True)
class FactPricesResult:
    lines: list[ProcurementLine]
    skipped: list[str]
    failed: list[tuple[str, str]]


def _fact_price_details(before: FactMoney, after: FactMoney) -> dict[str, str]:
    return {
        **_money_details(before, after),
        "vat_rate": _plain(after.vat_rate),
        "previous_currency": _plain(before.currency),
        "previous_price": _plain(before.price),
        "previous_price_with_vat": _plain(before.price_with_vat),
        "previous_sum": _plain(before.sum),
    }


def save_fact_price(row: FactPriceRow, actor: Actor, now: datetime, uow: UnitOfWork) -> ProcurementLine:
    """«Цена факт» of one order row. A stale version whose edit is already there is a read-only retry."""
    with uow:
        current = uow.procurement.get(row.line_id)
        if current is None:
            raise KeyError(f"Procurement line not found: {row.line_id}")
        if current.version != row.expected_version:
            try:
                retry = current.set_fact_price(row.price_with_vat, actor)
            except ProcurementValidationError as error:
                raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы") from error
            if retry == current:
                return current
            raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы")
        updated = current.set_fact_price(row.price_with_vat, actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.fact_price_saved",
            actor=actor,
            now=now,
            uow=uow,
            details={"order_reference": current.order_number or "", **_fact_price_details(current.money, updated.money)},
        )
        if result != current:
            uow.commit()
        return result


def save_fact_prices_batch(
    rows: Sequence[FactPriceRow], actor: Actor, now: datetime, uow_factory,
) -> FactPricesResult:
    """«Цена факт» of several order rows, row by row like «Сохранить всё»: a row changed since it was loaded is
    skipped, a refused row (stage, foreign currency, someone else's) is listed with the reason."""
    if not rows:
        raise ProcurementValidationError("Нет изменений для сохранения")
    saved: list[ProcurementLine] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    for row in rows:
        label = row.legacy_id or str(row.line_id)
        try:
            saved.append(save_fact_price(row, actor, now, uow_factory()))
        except (ConcurrentProcurementUpdate, KeyError):
            skipped.append(label)
        except ProcurementValidationError as error:
            failed.append((label, str(error)))
    return FactPricesResult(lines=saved, skipped=skipped, failed=failed)


@dataclass(frozen=True)
class OrderLineRef:
    line_id: UUID
    expected_version: int


@dataclass(frozen=True)
class InvoiceSupplierOrderCommand:
    order_number: str
    invoice_url: str
    expected_arrival: date
    lines: Sequence[OrderLineRef]
    actor: Actor
    now: datetime
    invoice_number: str | None = None
    invoice_date: date | None = None


@dataclass(frozen=True)
class PaySupplierOrderCommand:
    order_number: str
    lines: Sequence[OrderLineRef]
    actor: Actor
    now: datetime
    manual_rate: Decimal | None = None


def _order_refs(refs: Sequence[OrderLineRef]) -> set[UUID]:
    ids = [ref.line_id for ref in refs]
    if not ids:
        raise ProcurementValidationError("Не выбрано ни одной строки заказа")
    if len(set(ids)) != len(ids):
        raise ProcurementValidationError("Строка заказа передана дважды")
    return set(ids)


def _buyer_order_lines(reference: str, actor: Actor, uow: UnitOfWork) -> list[ProcurementLine]:
    lines = [line for line in uow.procurement.by_buyer(actor, 10_000) if line.order_number == reference]
    if not lines:
        raise KeyError(f"Заказ {reference}: нет ваших строк")
    return lines


def _stage_ids(lines: Sequence[ProcurementLine], status: ProcurementStatus) -> set[UUID]:
    return {line.id for line in lines if line.status is status}


def _versioned_rows(
    lines: Sequence[ProcurementLine], refs: Sequence[OrderLineRef],
) -> list[ProcurementLine]:
    by_id = {line.id: line for line in lines}
    rows = []
    for ref in refs:
        line = by_id[ref.line_id]
        if line.version != ref.expected_version:
            raise ConcurrentProcurementUpdate(f"{line.legacy_id}: строка изменилась после загрузки формы")
        rows.append(line)
    return rows


def invoice_supplier_order(command: InvoiceSupplierOrderCommand, uow: UnitOfWork) -> list[ProcurementLine]:
    """One invoice for the whole supplier order (owner, 05.10), all rows or none.

    The rows sent must be exactly the order's «Жду счет» rows — they go «В оплату» (`_invoice_order`, as «Внести
    КП» does) — or exactly all its «В оплату» rows, whose invoice is replaced. Any other set means the order moved
    on since the form was loaded (`OrderLinesMismatch`). An empty НАИМЕНОВАНИЕ (Счет) takes the site name, else
    the calculated one; a row the pay gate refuses names itself and nothing is written."""
    reference = _order_reference(command.order_number)
    requested = _order_refs(command.lines)
    with uow:
        order_lines = _buyer_order_lines(reference, command.actor, uow)
        awaiting = _stage_ids(order_lines, ProcurementStatus.AWAITING_INVOICE)
        to_pay = _stage_ids(order_lines, ProcurementStatus.TO_PAY)
        if requested != awaiting and requested != to_pay or not requested:
            raise OrderLinesMismatch(
                f"Заказ {reference} изменился: счёт вносится сразу на все его строки "
                f"«{ProcurementStatus.AWAITING_INVOICE.value}» ({len(awaiting)}) или на все строки "
                f"«{ProcurementStatus.TO_PAY.value}» ({len(to_pay)}) — обновите данные"
            )
        rows = _versioned_rows(order_lines, command.lines)
        invoiced = _invoice_order(
            rows, command.invoice_url, command.actor, command.now, uow,
            expected_arrival=command.expected_arrival,
            invoice_number=command.invoice_number,
            invoice_date=command.invoice_date,
            order_wide=True,
        )
        if any(line != row for line, row in zip(invoiced, rows)):
            uow.commit()
        return invoiced


def pay_supplier_order(
    command: PaySupplierOrderCommand,
    uow: UnitOfWork,
    rates: ExchangeRates | None = None,
) -> list[ProcurementLine]:
    """«Оплачено» once for the whole supplier order (owner, 05.10): every «В оплату» row goes «В пути» and is
    expected by Receiving, all or none. The rows sent must be exactly the order's «В оплату» rows. A foreign
    invoice is fixed at the payment-day rate as the single-row payment does (`ManualRateRequired` when the CBR is
    down); one typed rate cannot serve two currencies, so such an order is paid row by row."""
    reference = _order_reference(command.order_number)
    requested = _order_refs(command.lines)
    with uow:
        peeked = [line for line in _buyer_order_lines(reference, command.actor, uow) if line.id in requested]
    # The CBR is asked outside the write transaction, so it never holds row locks.
    foreign = [line for line in peeked if line.status is ProcurementStatus.TO_PAY and line.money.source_currency]
    currencies = sorted({line.money.source_currency for line in foreign})
    if command.manual_rate is not None and len(currencies) > 1:
        raise ProcurementValidationError(
            f"В заказе {reference} счета в разных валютах ({', '.join(currencies)}) — ручной курс вводится по строкам"
        )
    day = _moscow_day(command.now)
    chosen = {
        line.id: _choose_rate(line, line.money.source_currency, day, command.manual_rate, rates) for line in foreign
    }
    with uow:
        order_lines = _buyer_order_lines(reference, command.actor, uow)
        to_pay = _stage_ids(order_lines, ProcurementStatus.TO_PAY)
        if not to_pay or requested != to_pay:
            raise OrderLinesMismatch(
                f"Заказ {reference} изменился: оплата проводится сразу по всем его строкам "
                f"«{ProcurementStatus.TO_PAY.value}» ({len(to_pay)}) — обновите данные"
            )
        planned = []
        for current in _versioned_rows(order_lines, command.lines):
            try:
                updated = current.mark_paid(command.actor, command.now, chosen.get(current.id))
                expected = _receiving_expected(updated, command.now)
            except ProcurementValidationError as error:
                raise ProcurementValidationError(f"{current.legacy_id}: {error}") from error
            planned.append((current, updated, expected))
        paid = []
        for current, updated, expected in planned:
            paid.append(
                _save_transition(
                    current,
                    updated,
                    event_type="procurement.paid",
                    actor=command.actor,
                    now=command.now,
                    uow=uow,
                    details={"order_reference": reference, **_money_details(current.money, updated.money)},
                )
            )
            uow.outbox.add(expected)
        uow.commit()
        return paid


def buyer_in_work_lines(actor: Actor, uow: UnitOfWork) -> list[ProcurementLine]:
    """`portalPurchaseInWork`: all of the buyer's own «В работе» lines for the «Заполнить данные» form."""
    with uow:
        return [
            line
            for line in uow.procurement.by_buyer(actor, 10_000)
            if line.status is ProcurementStatus.IN_WORK
        ]


@dataclass(frozen=True)
class FactsBatchRow:
    line_id: UUID
    expected_version: int
    facts: ProcurementFacts
    legacy_id: str = ""


@dataclass(frozen=True)
class FactsBatchResult:
    lines: list[ProcurementLine]
    skipped: list[str]
    failed: list[tuple[str, str]]

    @property
    def ready(self) -> int:
        return sum(1 for line in self.lines if line.status is ProcurementStatus.READY)


def save_procurement_facts_batch(
    rows: Sequence[FactsBatchRow],
    actor: Actor,
    now: datetime,
    uow_factory,
    suppliers: SupplierDirectory | None = None,
) -> FactsBatchResult:
    """`portalSavePurchaseFacts` / «Сохранить всё»: the edited rows of the buyer's desk, row by row. A row that
    changed since it was loaded is skipped (SUPPLYMAP: «строка сдвинулась»); the rest are saved and the stage
    follows their facts — complete ones reach «Готов к запросу», incomplete ones stay (or return) «В работе».
    Owner, 05.10: a row without ПОСТАВЩИК no longer refuses the batch; an omitted supplier stays as it is."""
    if not rows:
        raise ProcurementValidationError("Нет изменений для сохранения")
    saved: list[ProcurementLine] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    for row in rows:
        label = row.legacy_id or str(row.line_id)
        try:
            saved.append(
                save_procurement_facts(
                    SaveProcurementFactsCommand(
                        line_id=row.line_id,
                        facts=row.facts,
                        expected_version=row.expected_version,
                        actor=actor,
                        now=now,
                    ),
                    uow_factory(),
                    suppliers,
                )
            )
        except (ConcurrentProcurementUpdate, KeyError):
            skipped.append(label)
        except ProcurementValidationError as error:
            failed.append((label, str(error)))
    return FactsBatchResult(lines=saved, skipped=skipped, failed=failed)


# ── Удаление проекта: строки закупки без движения (owner 04.10) ─────────


@dataclass(frozen=True)
class RemoveRequestLinesCommand:
    """The lines of these requests go if nothing moved on any of them; ``apply=False`` only reports."""

    request_ids: tuple[UUID, ...]
    actor: Actor
    now: datetime
    project: str = ""
    apply: bool = True


@dataclass(frozen=True)
class RequestLines:
    """One request's procurement lines: removed (or to remove) together, or all kept for `blocker`."""

    lines: tuple[str, ...]
    blocker: str | None = None


def line_removal_label(line: ProcurementLine) -> str:
    return f"{line.legacy_id} · {line.calculated_name} («{line.status.value}»)"


def remove_untouched_request_lines(command: RemoveRequestLinesCommand, uow: UnitOfWork) -> dict[UUID, RequestLines]:
    """A deleted project's purchase requests take their procurement lines along when nothing moved on them
    (`removal_blocker`): all lines of a request go in one transaction or none, so a split or re-transferred
    request never loses half of its lines. Requests without lines are absent from the result. A line changed
    between the read and the delete fails the whole call (`ConcurrentProcurementUpdate`): nothing is removed."""
    result: dict[UUID, RequestLines] = {}
    with uow:
        by_request: dict[UUID, list[ProcurementLine]] = {}
        for line in uow.procurement.by_source_request_ids(list(dict.fromkeys(command.request_ids))):
            by_request.setdefault(line.source_request_id, []).append(line)
        removed = 0
        for request_id, lines in by_request.items():
            labels = tuple(line_removal_label(line) for line in lines)
            blocked = next(((line, why) for line in lines if (why := removal_blocker(line)) is not None), None)
            if blocked is not None:
                line, why = blocked
                result[request_id] = RequestLines(labels, f"{line.legacy_id} «{line.status.value}»: {why}")
                continue
            result[request_id] = RequestLines(labels)
            if not command.apply:
                continue
            for line in lines:
                if not uow.procurement.delete(line.id, line.version):
                    raise ConcurrentProcurementUpdate(f"Строку {line.legacy_id} изменили во время удаления")
                uow.audit.add(AuditEvent(
                    id=uuid4(), aggregate_id=line.id, event_type="procurement.line_deleted", actor=command.actor,
                    occurred_at=command.now, details={
                        "project": command.project, "legacy_id": line.legacy_id, "revision": line.revision,
                        "status": line.status.value, "name": line.calculated_name, "reason": "project_deleted",
                    },
                ))
                removed += 1
        if removed:
            uow.commit()
    return result


# --- «Вставить КП» / «Вставить счёт» (owner, 05.10): prices read from the supplier's document --------------------

DOCUMENT_KINDS = ("invoice", "kp")
_DOCUMENT_STAGES = (ProcurementStatus.AWAITING_INVOICE, ProcurementStatus.TO_PAY)


@dataclass(frozen=True)
class RecogniseOrderDocumentCommand:
    order_number: str
    kind: str  # «invoice» / «kp»
    file_name: str
    content_type: str
    content: bytes
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RecognisedOrderDocument:
    kind: str
    file: StoredInvoiceFile
    url: str
    document: SupplierDocument
    lines: list[ProcurementLine]
    matches: list[DocumentMatch]
    unmatched_items: list[int]
    unmatched_lines: list[ProcurementLine]


@dataclass(frozen=True)
class DocumentPriceRow:
    """One order line as the buyer confirmed it: no price — the document does not price it, the stored one stays."""

    line_id: UUID
    expected_version: int
    price_with_vat: Decimal | None = None
    price: Decimal | None = None
    vat_rate: Decimal | None = None
    invoice_item_name: str | None = None


@dataclass(frozen=True)
class ApplyOrderDocumentCommand:
    order_number: str
    kind: str
    file_url: str
    rows: Sequence[DocumentPriceRow]
    actor: Actor
    now: datetime
    invoice_number: str | None = None
    invoice_date: date | None = None
    expected_arrival: date | None = None


def foreign_document_message(currency: str) -> str:
    return f"Документ в {currency}: цены и курс вносите через «Внести КП»"


def _document_kind(kind: str) -> str:
    if kind not in DOCUMENT_KINDS:
        raise ProcurementValidationError("Документ — счёт или КП")
    return kind


def _document_lines(reference: str, actor: Actor, uow: UnitOfWork) -> list[ProcurementLine]:
    lines = sorted(
        (line for line in _buyer_order_lines(reference, actor, uow) if line.status in _DOCUMENT_STAGES),
        key=lambda line: line.legacy_id,
    )
    if not lines:
        raise ProcurementValidationError(
            f"Нет строк заказа {reference} в статусе «{ProcurementStatus.AWAITING_INVOICE.value}» или "
            f"«{ProcurementStatus.TO_PAY.value}»"
        )
    return lines


def recognise_order_document(command: RecogniseOrderDocumentCommand, uow: UnitOfWork) -> RecognisedOrderDocument:
    """The КП or the invoice dropped on the order: kept in ERP storage like an invoice file, its items table read and
    matched to the buyer's order lines in «Жду счет» / «В оплату». The lines are not touched — the buyer checks the
    matches and sends them to `apply_order_document`."""
    kind = _document_kind(command.kind)
    reference = _order_reference(command.order_number)
    if not command.content:
        raise ProcurementValidationError("Файл не передан")
    if len(command.content) > INVOICE_FILE_LIMIT:
        raise ProcurementValidationError("Файл больше 20 МБ")
    # The buyer's order lines first, the (budgeted) reading outside any transaction, then the store (audit F05).
    with uow:
        _document_lines(reference, command.actor, uow)
    document = read_supplier_document(command.file_name, command.content, kind)
    with uow:
        lines = _document_lines(reference, command.actor, uow)
        first = lines[0]
        info = InvoiceInfo(number=document.number, date=document.date)
        stored = StoredInvoiceFile(
            id=uuid4(),
            line_id=first.id,
            file_name=(
                invoice_file_name(info, first.supplier or "", first.designation, reference, command.file_name)
                if kind == "invoice"
                else kp_file_name(info, first.supplier or "", reference, command.file_name)
            ),
            content_type=stored_content_type(command.content),
            size=len(command.content),
            uploaded_by=command.actor.name,
            uploaded_at=command.now,
        )
        uow.files.add(stored, command.content)
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=first.id,
                event_type="procurement.document_file_uploaded",
                actor=command.actor,
                occurred_at=command.now,
                details={
                    "file_id": str(stored.id),
                    "name": stored.file_name,
                    "kind": kind,
                    "order_reference": reference,
                    "items": str(len(document.items)),
                    "currency": document.currency,
                },
            )
        )
        uow.commit()
    candidates = [
        OrderLineCandidate(
            key=str(line.id),
            article=line.design_article,
            names=(line.site_name, line.calculated_name, line.invoice_item_name),
            quantity=line.actual_quantity if line.actual_quantity is not None else line.to_buy,
        )
        for line in lines
    ]
    matches, free_items, free_keys = match_document_items(document.items, candidates)
    by_key = {str(line.id): line for line in lines}
    return RecognisedOrderDocument(
        kind=kind,
        file=stored,
        url=invoice_file_url(stored.id),
        document=document,
        lines=lines,
        matches=matches,
        unmatched_items=free_items,
        unmatched_lines=[by_key[key] for key in free_keys],
    )


def _stored_document(file_url: str, uow: UnitOfWork) -> tuple[StoredInvoiceFile, bytes]:
    text = str(file_url or "").strip()
    try:
        file_id = UUID(text[len(INVOICE_FILE_PATH):]) if text.startswith(INVOICE_FILE_PATH) else None
    except ValueError:
        file_id = None
    found = uow.files.get(file_id) if file_id is not None else None
    if found is None:
        raise ProcurementValidationError("Файл документа не найден в ERP — вставьте его ещё раз")
    return found


def apply_order_document(command: ApplyOrderDocumentCommand, uow: UnitOfWork) -> list[ProcurementLine]:
    """«Вставить КП / счёт», second half: the prices the buyer confirmed, in one transaction, all rows or none.

    КП: «Цена факт» (with the document's НДС rate and net price) on the rows sent, which stay where they are; the КП
    file is named in each row's audit event (the line has no КП field). Invoice: the same prices, then the order-wide
    invoice (`invoice_supplier_order` rules) — the rows sent must be exactly the order's «Жду счет» rows (they go
    «В оплату») or exactly its «В оплату» rows (the invoice is replaced). A row refused by its stage, the invoice gate
    (prices are not part of it — a row without one goes on with a warning) or a foreign-currency document refuses the
    whole call; a stale row is `ConcurrentProcurementUpdate`."""
    kind = _document_kind(command.kind)
    reference = _order_reference(command.order_number)
    refs = [OrderLineRef(row.line_id, row.expected_version) for row in command.rows]
    requested = _order_refs(refs)
    with uow:
        order_lines = _buyer_order_lines(reference, command.actor, uow)
        by_id = {line.id: line for line in order_lines}
        stored, content = _stored_document(command.file_url, uow)
        if stored.line_id not in by_id:
            raise ProcurementValidationError(f"Файл загружен не к заказу {reference}")
        document = read_supplier_document(stored.file_name, content, kind)
        if document.currency != "RUB":
            raise ProcurementValidationError(foreign_document_message(document.currency))
        outside = [row.line_id for row in command.rows if row.line_id not in by_id]
        if outside:
            raise ProcurementValidationError(f"Строка {outside[0]} не входит в заказ {reference}")
        if kind == "invoice":
            awaiting = _stage_ids(order_lines, ProcurementStatus.AWAITING_INVOICE)
            to_pay = _stage_ids(order_lines, ProcurementStatus.TO_PAY)
            if requested != awaiting and requested != to_pay:
                raise OrderLinesMismatch(
                    f"Заказ {reference} изменился: счёт вносится сразу на все его строки "
                    f"«{ProcurementStatus.AWAITING_INVOICE.value}» ({len(awaiting)}) или на все строки "
                    f"«{ProcurementStatus.TO_PAY.value}» ({len(to_pay)}) — обновите данные"
                )
        rows = _versioned_rows(order_lines, refs)
        planned = []
        for row, current in zip(command.rows, rows):
            try:
                updated = current.apply_document_price(
                    command.actor,
                    price_with_vat=row.price_with_vat,
                    price=row.price,
                    vat_rate=row.vat_rate,
                    invoice_item_name=row.invoice_item_name,
                )
            except ProcurementValidationError as error:
                raise ProcurementValidationError(f"{current.legacy_id}: {error}") from error
            planned.append((current, updated))
        source = {"order_reference": reference, "document_kind": kind, "document_url": command.file_url}
        priced = [
            _save_transition(
                current, updated, event_type="procurement.fact_price_saved", actor=command.actor, now=command.now,
                uow=uow, details={
                    **source, **_fact_price_details(current.money, updated.money),
                    "invoice_item_name": updated.invoice_item_name or "",
                },
            )
            for current, updated in planned
        ]
        result = priced
        if kind == "invoice":
            # The pay gate is checked on every row before the first invoice write; a refusal rolls the prices back too.
            result = _invoice_order(
                priced, command.file_url, command.actor, command.now, uow,
                expected_arrival=command.expected_arrival,
                invoice_number=command.invoice_number or document.number or None,
                invoice_date=command.invoice_date or invoice_day(document.date),
                order_wide=True,
            )
        if any(after != before for after, before in zip(result, rows)):
            uow.commit()
        return result
