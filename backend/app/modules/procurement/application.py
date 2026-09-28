"""Transactional Procurement use cases expressed against persistence ports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor

from .domain import (
    KpEntry,
    MoneyEntry,
    ProcurementFacts,
    ProcurementLine,
    ProcurementStatus,
    ProcurementValidationError,
    SupplierRequestGroup,
    create_backorder_line,
    create_line_from_requested,
    group_supplier_requests,
    next_backorder_id,
    next_order_number,
    next_retransfer_revision,
    next_system_id,
    supplier_reference,
    supplier_request_blockers,
    supplier_request_letter,
    split_line,
)
from .money import ExchangeRate, FactMoney, is_foreign, normalize_currency, rate_day


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
    def list(self, limit: int = 10_000) -> Sequence[ProcurementLine]: ...
    def legacy_ids_with_prefix(self, prefix: str) -> Sequence[str]: ...
    def ready_for_request(self) -> Sequence[ProcurementLine]: ...
    def order_numbers_with_prefix(self, prefix: str) -> Sequence[str]: ...
    def has_lines_for_request(self, source_request_id: UUID) -> bool: ...
    def revisions_for_designation(self, designation: str) -> Sequence[str]:
        """Revisions of every line whose НАЗНАЧЕНИЕ has the same `designation_key`."""
        ...
    def add(self, line: ProcurementLine) -> None: ...
    def add_order(self, order: SupplierOrder) -> None: ...
    def get_order(self, order_number: str) -> SupplierOrder | None: ...


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


def _line_from_requested(message: OutboxMessage, lines: ProcurementRepository) -> ProcurementLine:
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
) -> ProcurementLine:
    with uow:
        existing = uow.procurement.by_source_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.source_event_id != message.id:
                raise ProcurementValidationError(
                    "Ключ события закупки уже использован другим событием"
                )
            return existing

        line = _line_from_requested(message, uow.procurement)
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
                line = _line_from_requested(message, uow.procurement)
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

        updated = [line.take_in_work(command.actor) for line in current]
        changed = [
            (before, after)
            for before, after in zip(current, updated, strict=True)
            if before != after
        ]
        if not changed:
            return updated

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
        return updated


def save_procurement_facts(
    command: SaveProcurementFactsCommand,
    uow: UnitOfWork,
) -> ProcurementLine:
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
                return current
            raise ConcurrentProcurementUpdate("Строка изменилась после загрузки формы")
        updated = current.update_facts(command.facts, command.actor)
        if updated == current:
            return current

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
        return updated


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
