"""Transactional Procurement use cases expressed against persistence ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor

from .domain import (
    MoneyEntry,
    ProcurementFacts,
    ProcurementLine,
    ProcurementStatus,
    ProcurementValidationError,
    create_backorder_line,
    create_line_from_requested,
    next_backorder_id,
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
    def add(self, line: ProcurementLine) -> None: ...


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
class SendSupplierRequestCommand:
    line_id: UUID
    expected_version: int
    reference: str
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
class ReconcileProcurementRateCommand:
    line_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


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

        line = create_line_from_requested(message)
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
                line = create_line_from_requested(message)
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


def send_supplier_request(command: SendSupplierRequestCommand, uow: UnitOfWork) -> ProcurementLine:
    with uow:
        current = _load_versioned(command.line_id, command.expected_version, uow)
        updated = current.send_supplier_request(command.reference, command.actor)
        result = _save_transition(
            current,
            updated,
            event_type="procurement.supplier_request_sent",
            actor=command.actor,
            now=command.now,
            uow=uow,
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
