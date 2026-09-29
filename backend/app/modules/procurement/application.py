"""Transactional Procurement use cases expressed against persistence ports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
import logging

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.notifications import NotificationOutbox, notification_requested, publish_notification
from app.shared.print_sheet import PrintSheet

from .domain import (
    UNSET as UNSET_FACT,
    _PAID_STATUSES,
    KpEntry,
    MoneyEntry,
    PERMISSION_NUMBER_PREFIX,
    ProcurementFacts,
    ProcurementLine,
    ProcurementStatus,
    ProcurementValidationError,
    SupplierRequestGroup,
    ReplacementDecision,
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
    supplier_request_letter,
    split_line,
)
from .money import ExchangeRate, FactMoney, is_foreign, normalize_currency, rate_day


_log = logging.getLogger(__name__)

SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


class ProcurementRepository(Protocol):
    def by_source_idempotency_key(self, key: str) -> ProcurementLine | None: ...
    def get(self, line_id: UUID) -> ProcurementLine | None: ...
    def available(
        self, limit: int, after_id: UUID | None = None
    ) -> Sequence[ProcurementLine]: ...
    def by_buyer(
        self, actor: Actor, limit: int, after_id: UUID | None = None
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
    def add(self, line: ProcurementLine) -> None: ...
    def add_order(self, order: SupplierOrder) -> None: ...
    def get_order(self, order_number: str) -> SupplierOrder | None: ...


class SupplierDirectory(Protocol):
    """Port to References: the «ссылка → поставщик» map (`supplierMap_`) and its learning (`learnSupplier_`)."""

    def supplier_map(self) -> dict[str, str]: ...
    def learn(self, link: str | None, supplier: str | None, actor: Actor, now: datetime) -> bool: ...


class AppendOnlyRepository(Protocol):
    def add(self, item: AuditEvent) -> None: ...


class ConcurrentProcurementUpdate(RuntimeError):
    pass


class DispatchOutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class UnitOfWork(Protocol):
    procurement: ProcurementRepository
    audit: AppendOnlyRepository
    outbox: AppendOnlyRepository

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


@dataclass(frozen=True)
class BuildSupplierRequestsCommand:
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SupplierRequestsResult:
    orders: list[SupplierOrder]
    lines: list[ProcurementLine]
    skipped: list[str]
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
class RecordInvoiceCommand:
    line_id: UUID
    expected_version: int
    invoice_url: str
    expected_arrival: date
    actor: Actor
    now: datetime


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

        line = _line_from_requested(message, uow.procurement, suppliers)
        uow.procurement.add(line)
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
                line = _line_from_requested(message, uow.procurement, suppliers)
            except ProcurementValidationError as error:
                uow.outbox.mark_failed(message.id, now, str(error))
                uow.commit()
                raise PoisonOutboxMessage(message.id, str(error)) from error
            uow.procurement.add(line)
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
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return line


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
) -> list[ProcurementLine]:
    return take_procurement_in_work_batch(command, uow)[0]


def take_procurement_in_work_batch(
    command: TakeProcurementInWorkCommand,
    uow: UnitOfWork,
) -> tuple[list[ProcurementLine], list[ProcurementLine]]:
    """`portalTakeInWork` → `takeRowsInWork_`: every selected «Новый» line becomes the buyer's; a line that
    is already further along (taken by another buyer, or past «В работе») is skipped, not an error.
    Returns (lines now the buyer's, skipped lines). The buyer's own «В работе» line is a read-only retry."""
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
        changed: list[tuple[ProcurementLine, ProcurementLine]] = []
        for before in current:
            try:
                after = before.take_in_work(command.actor)
            except ProcurementValidationError:
                skipped.append(before)
                continue
            taken.append(after)
            if after != before:
                changed.append((before, after))
        if not changed:
            return taken, skipped

        for before, after in changed:
            uow.procurement.add(after)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=after.id,
                    event_type="procurement.taken_in_work",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": after.legacy_id,
                        "from_status": before.status.value,
                        "to_status": after.status.value,
                    },
                )
            )
        uow.commit()
        return taken, skipped


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
    """
    with uow:
        groups, other_buyer = group_supplier_requests(uow.procurement.ready_for_request(), command.actor)
        if not groups:
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
        skipped: list[str] = []
        for group in groups:
            blocker = supplier_request_blockers(group)
            if blocker:
                skipped.append(blocker)
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
    order = SupplierOrder(
        order_number=number,
        order_date=order_date,
        supplier=group.supplier,
        task_type=group.task_type,
        delivery_point=group.delivery_point,
        buyer=command.actor,
        created_at=command.now,
        letter=supplier_request_letter(group, number),
        line_ids=tuple(line.id for line in group.lines),
    )
    uow.procurement.add_order(order)
    return order, sent


def supplier_order(order_number: str, uow: UnitOfWork) -> SupplierOrder:
    with uow:
        order = uow.procurement.get_order(order_number)
    if order is None:
        raise KeyError(f"Supplier order not found: {order_number}")
    return order


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
        return KpResult(_invoice_order(saved, command.invoice_url, command, uow))
    except (ProcurementValidationError, ConcurrentProcurementUpdate) as error:
        return KpResult(saved, invoice_error=str(error))


def _invoice_order(
    lines: list[ProcurementLine], invoice_url: str, command: SaveKpCommand, uow: UnitOfWork,
) -> list[ProcurementLine]:
    """`uploadInvoice` over the order rows: one invoice for all of them, all or none go «В оплату»."""
    with uow:
        invoiced = []
        for line in lines:
            current = _load_versioned(line.id, line.version, uow)
            try:
                if current.expected_arrival is None:
                    raise ProcurementValidationError(
                        "Нельзя загрузить счёт — сначала заполните: " + ", ".join(current.pay_missing())
                    )
                updated = current.record_invoice(invoice_url, current.expected_arrival, command.actor)
            except ProcurementValidationError as error:
                raise ProcurementValidationError(f"{current.legacy_id}: {error}") from error
            invoiced.append(
                _save_transition(
                    current,
                    updated,
                    event_type="procurement.invoice_recorded",
                    actor=command.actor,
                    now=command.now,
                    uow=uow,
                    details={"order_reference": current.order_number or ""},
                )
            )
        uow.commit()
    return invoiced


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
    """`portalSplitPurchaseRow` → `applyPendingSplits_`: one request → N suppliers, N new lines «Новый».

    Any buyer may split any line (`portalCanWork_`, no ownership check). The source line keeps its data;
    only its version moves, so a repeated click on a stale form gets 409 instead of N more copies.
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
        uow.commit()
        return source, copies


def _validate_split_actor(actor: Actor) -> None:
    if not str(actor.name or "").strip():
        raise ProcurementValidationError("Не определён закупщик")


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
) -> list[ProcurementLine]:
    with uow:
        if after_id is not None and uow.procurement.get(after_id) is None:
            raise KeyError(f"Procurement cursor not found: {after_id}")
        return list(uow.procurement.by_buyer(actor, limit, after_id))


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
    """`portalSavePurchaseFacts`: the marked rows of «Заполнить данные», row by row. A marked row with an
    empty ПОСТАВЩИК refuses the whole batch before the first write (it would silently drop out of the
    supplier request); a row that changed since the form was loaded is skipped (SUPPLYMAP: «строка
    сдвинулась»); the rest are saved, reach «Готов к запросу» when complete and teach the map."""
    if not rows:
        raise ProcurementValidationError("Нет изменений для сохранения")
    no_supplier = [
        row.legacy_id or str(row.line_id)
        for row in rows
        if row.facts.supplier is UNSET_FACT or not str(row.facts.supplier or "").strip()
    ]
    if no_supplier:
        raise ProcurementValidationError(
            "Не заполнен «Поставщик»: " + ", ".join(no_supplier) + ". Без него строка не уйдёт в запрос."
        )
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
