"""Pure Procurement rules for intake and buyer enrichment."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, DecimalException
from enum import Enum
from uuid import UUID, uuid4

from app.shared.document_url import normalize_document_url
from app.shared.events import OutboxMessage
from app.shared.identity import Actor

from .money import (
    EMPTY_MONEY,
    ExchangeRate,
    FactMoney,
    MoneyInput,
    MoneyValidationError,
    apply_currency_and_fact,
    pay_gate_missing,
    reconvert,
    rescale_to_quantity,
    round_kopecks,
)


class ProcurementValidationError(ValueError):
    """A procurement operation violates a business invariant."""


class _Unset:
    pass


UNSET = _Unset()


class ProcurementStatus(str, Enum):
    NEW = "Новый"
    IN_WORK = "В работе"
    READY = "Готов к запросу"
    AWAITING_INVOICE = "Жду счет"
    TO_PAY = "В оплату"
    IN_TRANSIT = "В пути"
    QUARANTINE = "Карантин"
    DONE = "Готово"
    APPROVAL = "Согласование"
    CANCELLED = "Отменено"


@dataclass(frozen=True)
class ProcurementFacts:
    supplier: str | None | _Unset = UNSET
    site_name: str | None | _Unset = UNSET
    link: str | None | _Unset = UNSET
    design_article: str | None | _Unset = UNSET
    calculated_name: str | None | _Unset = UNSET
    actual_quantity: Decimal | None | _Unset = UNSET
    actual_unit: str | None | _Unset = UNSET


@dataclass(frozen=True)
class MoneyEntry:
    """Invoice money form: typed amounts plus the invoice identity (НАИМЕНОВАНИЕ / № / ДАТА СЧЕТА)."""

    amounts: MoneyInput
    invoice_item_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None


MONEY_EDITABLE = frozenset({ProcurementStatus.AWAITING_INVOICE, ProcurementStatus.TO_PAY})


@dataclass(frozen=True)
class ProcurementLine:
    id: UUID
    source_event_id: UUID
    source_idempotency_key: str
    source_request_id: UUID
    legacy_id: str
    revision: str
    status: ProcurementStatus
    version: int
    created_at: datetime
    request_date: date
    initiator: str
    department: str
    task_type: str
    designation: str
    project_reference: str | None
    priority: str
    delivery_point: str
    planned_date: date
    link: str | None
    item_type: str
    design_article: str | None
    category: str
    calculated_name: str
    calculated_quantity: Decimal
    calculated_unit: str
    quality_document: str
    calculated_price: Decimal | None
    calculated_price_with_vat: Decimal | None
    calculated_sum: Decimal | None
    buyer: Actor | None = None
    supplier: str | None = None
    site_name: str | None = None
    actual_quantity: Decimal | None = None
    actual_unit: str | None = None
    supplier_request_reference: str | None = None
    expected_arrival: date | None = None
    invoice_url: str | None = None
    paid_at: datetime | None = None
    money: FactMoney = EMPTY_MONEY
    invoice_item_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None

    def pay_missing(self, expected_arrival: date | None = None) -> list[str]:
        return pay_gate_missing(
            self.money,
            quantity=self.actual_quantity,
            unit=self.actual_unit,
            invoice_item_name=self.invoice_item_name,
            expected_arrival=expected_arrival or self.expected_arrival,
        )

    def take_in_work(self, actor: Actor) -> ProcurementLine:
        _validate_actor(actor)
        if self.status is ProcurementStatus.IN_WORK:
            if _same_actor(self.buyer, actor):
                return self
            raise ProcurementValidationError("Строка уже взята в работу другим закупщиком")
        if self.status is not ProcurementStatus.NEW:
            raise ProcurementValidationError("В работу можно взять только новую строку")
        return replace(
            self,
            status=ProcurementStatus.IN_WORK,
            buyer=actor,
            version=self.version + 1,
        )

    def update_facts(self, facts: ProcurementFacts, actor: Actor) -> ProcurementLine:
        _validate_actor(actor)
        if not _same_actor(self.buyer, actor):
            raise ProcurementValidationError("Закупщик может изменять только свои строки")
        current = ProcurementFacts(
            supplier=self.supplier,
            site_name=self.site_name,
            link=self.link,
            design_article=self.design_article,
            calculated_name=self.calculated_name,
            actual_quantity=self.actual_quantity,
            actual_unit=self.actual_unit,
        )
        normalized = _merge_facts(facts, current)
        if normalized == current:
            return self
        if self.status is not ProcurementStatus.IN_WORK:
            raise ProcurementValidationError("Данные закупки можно менять только в статусе «В работе»")
        status = ProcurementStatus.READY if _facts_ready(normalized, self.delivery_point) else self.status
        return replace(
            self,
            supplier=normalized.supplier,
            site_name=normalized.site_name,
            link=normalized.link,
            design_article=normalized.design_article,
            calculated_name=normalized.calculated_name or self.calculated_name,
            actual_quantity=normalized.actual_quantity,
            actual_unit=normalized.actual_unit,
            status=status,
            version=self.version + 1,
        )

    def send_supplier_request(self, reference: str, actor: Actor) -> ProcurementLine:
        self._require_owner(actor)
        value = _text(reference)
        if not value:
            raise ProcurementValidationError("Не задан номер или ссылка запроса поставщику")
        if self.status is ProcurementStatus.AWAITING_INVOICE and self.supplier_request_reference == value:
            return self
        if self.status is not ProcurementStatus.READY:
            raise ProcurementValidationError("Запрос поставщику формируется только по готовой строке")
        return replace(
            self,
            supplier_request_reference=value,
            status=ProcurementStatus.AWAITING_INVOICE,
            version=self.version + 1,
        )

    def record_invoice(
        self,
        invoice_url: str,
        expected_arrival: date,
        actor: Actor,
    ) -> ProcurementLine:
        self._require_owner(actor)
        try:
            url = normalize_document_url(invoice_url)
        except ValueError as error:
            raise ProcurementValidationError(str(error)) from error
        if url is None:
            raise ProcurementValidationError("Счёт обязателен")
        if expected_arrival < self.created_at.date():
            raise ProcurementValidationError("Ожидаемая дата поступления не может быть раньше заказа")
        if self.status is not ProcurementStatus.AWAITING_INVOICE:
            raise ProcurementValidationError("Счёт принимается только на этапе «Жду счет»")
        missing = self.pay_missing(expected_arrival)
        if missing:
            raise ProcurementValidationError("Нельзя загрузить счёт — сначала заполните: " + ", ".join(missing))
        return replace(
            self,
            invoice_url=url,
            expected_arrival=expected_arrival,
            status=ProcurementStatus.TO_PAY,
            version=self.version + 1,
        )

    def update_money(
        self,
        entry: MoneyEntry,
        rate: ExchangeRate | None,
        actor: Actor,
    ) -> ProcurementLine:
        self._require_owner(actor)
        if self.status not in MONEY_EDITABLE:
            raise ProcurementValidationError("Суммы по счёту меняются только на этапах «Жду счет» и «В оплату»")
        try:
            money = apply_currency_and_fact(entry.amounts, self.actual_quantity, rate)
        except MoneyValidationError as error:
            raise ProcurementValidationError(str(error)) from error
        updated = replace(
            self,
            money=money,
            invoice_item_name=_text(entry.invoice_item_name) or None,
            invoice_number=_text(entry.invoice_number) or None,
            invoice_date=entry.invoice_date,
        )
        if updated == self:
            return self
        return replace(updated, version=self.version + 1)

    def mark_paid(
        self,
        actor: Actor,
        paid_at: datetime,
        rate: ExchangeRate | None = None,
    ) -> ProcurementLine:
        self._require_owner(actor)
        if self.status is ProcurementStatus.IN_TRANSIT and self.paid_at == paid_at:
            return self
        if self.status is not ProcurementStatus.TO_PAY:
            raise ProcurementValidationError("Оплатить можно только строку со счётом в оплате")
        money = self.money
        if money.source_currency:
            # Foreign invoice: the paid amount is fixed at the payment-day rate.
            if rate is None:
                raise ProcurementValidationError(f"Нет курса {money.source_currency} на день оплаты")
            money = reconvert(money, self.actual_quantity, rate)
        paid = replace(self, money=money)
        missing = paid.pay_missing()
        if missing:
            raise ProcurementValidationError("Нельзя провести оплату — сначала заполните: " + ", ".join(missing))
        return replace(
            paid,
            status=ProcurementStatus.IN_TRANSIT,
            paid_at=paid_at,
            version=self.version + 1,
        )

    @property
    def is_backorder(self) -> bool:
        return is_backorder_id(self.legacy_id)

    def confirm_backorder(self, expected_arrival: date, actor: Actor) -> ProcurementLine:
        """`promoteBackordersToShipped_`: supplier confirmed the resend — paid remainder goes «В пути»."""
        self._require_owner(actor)
        if not self.is_backorder:
            raise ProcurementValidationError("Подтвердить досыл можно только у строки дозавоза")
        if self.status is ProcurementStatus.IN_TRANSIT and self.expected_arrival == expected_arrival:
            return self
        if self.status is not ProcurementStatus.APPROVAL:
            raise ProcurementValidationError("Досыл подтверждается только из «Согласование»")
        if self.paid_at is None:
            raise ProcurementValidationError("Дозавоз не оплачен — досыл подтверждать нечем")
        if expected_arrival < self.created_at.date():
            raise ProcurementValidationError("Ожидаемая дата поступления не может быть раньше дозавоза")
        return replace(
            self,
            status=ProcurementStatus.IN_TRANSIT,
            expected_arrival=expected_arrival,
            version=self.version + 1,
        )

    def cancel_backorder(self, actor: Actor) -> ProcurementLine:
        """Supplier will not resend (money refunded): the backorder closes, a new request replaces it."""
        self._require_owner(actor)
        if not self.is_backorder:
            raise ProcurementValidationError("Отменить так можно только строку дозавоза")
        if self.status is ProcurementStatus.CANCELLED:
            return self
        if self.status is not ProcurementStatus.APPROVAL:
            raise ProcurementValidationError("Дозавоз отменяется только из «Согласование»")
        return replace(self, status=ProcurementStatus.CANCELLED, version=self.version + 1)

    def reconcile_rate(self, rate: ExchangeRate, actor: Actor) -> ProcurementLine:
        """Replace a manually typed rate with the CBR one for the same day and replay the amounts."""
        self._require_owner(actor)
        if not self.money.exchange_rate_manual:
            raise ProcurementValidationError("Курс уже взят у ЦБ — сверять нечего")
        if rate.manual:
            raise ProcurementValidationError("Сверка возможна только с курсом ЦБ")
        return replace(
            self,
            money=reconvert(self.money, self.actual_quantity, rate),
            version=self.version + 1,
        )

    def _require_owner(self, actor: Actor) -> None:
        _validate_actor(actor)
        if not _same_actor(self.buyer, actor):
            raise ProcurementValidationError("Закупщик может изменять только свои строки")


def _text(value: str | None) -> str:
    return str(value or "").strip()


def _validate_actor(actor: Actor) -> None:
    if not _text(actor.name):
        raise ProcurementValidationError("Не определён закупщик")


def _same_actor(left: Actor | None, right: Actor) -> bool:
    return left is not None and left.id == right.id


def _merge_text(value: str | None | _Unset, current: str | None) -> str | None:
    if value is UNSET:
        return current
    return _text(value) or None


def _merge_facts(facts: ProcurementFacts, current: ProcurementFacts) -> ProcurementFacts:
    if facts.supplier is not UNSET and not _text(facts.supplier):
        raise ProcurementValidationError("Поставщик не может быть пустым")
    if (
        facts.actual_quantity is not UNSET
        and facts.actual_quantity is not None
        and facts.actual_quantity <= 0
    ):
        raise ProcurementValidationError("Фактическое количество должно быть больше нуля")
    return ProcurementFacts(
        supplier=_merge_text(facts.supplier, current.supplier),
        site_name=_merge_text(facts.site_name, current.site_name),
        link=_merge_text(facts.link, current.link),
        design_article=_merge_text(facts.design_article, current.design_article),
        calculated_name=_merge_text(facts.calculated_name, current.calculated_name),
        actual_quantity=(
            current.actual_quantity if facts.actual_quantity is UNSET else facts.actual_quantity
        ),
        actual_unit=_merge_text(facts.actual_unit, current.actual_unit),
    )


def _facts_ready(facts: ProcurementFacts, delivery_point: str) -> bool:
    return bool(
        facts.supplier
        and _text(delivery_point)
        and facts.actual_quantity is not None
        and facts.actual_quantity > 0
        and facts.actual_unit
    )


def _required(payload: dict[str, str | None], key: str) -> str:
    value = _text(payload.get(key))
    if not value:
        raise ProcurementValidationError(f"procurement.requested: missing {key}")
    return value


def _decimal(payload: dict[str, str | None], key: str, *, required: bool = False) -> Decimal | None:
    raw = payload.get(key)
    if raw is None or _text(raw) == "":
        if required:
            raise ProcurementValidationError(f"procurement.requested: missing {key}")
        return None
    return Decimal(str(raw))


def create_line_from_requested(message: OutboxMessage) -> ProcurementLine:
    if message.topic != "procurement.requested":
        raise ProcurementValidationError(f"Unexpected topic: {message.topic}")
    payload = message.payload
    try:
        return ProcurementLine(
            id=uuid4(),
            source_event_id=message.id,
            source_idempotency_key=message.idempotency_key,
            source_request_id=UUID(_required(payload, "source_request_id")),
            legacy_id=_required(payload, "legacy_id"),
            revision="A",
            status=ProcurementStatus.NEW,
            version=1,
            created_at=message.created_at,
            request_date=date.fromisoformat(_required(payload, "request_date")),
            initiator=_required(payload, "initiator"),
            department=_required(payload, "department"),
            task_type=_required(payload, "task_type"),
            designation=_required(payload, "designation"),
            project_reference=_text(payload.get("project_reference")) or None,
            priority=_required(payload, "priority"),
            delivery_point=_required(payload, "delivery_point"),
            planned_date=date.fromisoformat(_required(payload, "planned_date")),
            link=_text(payload.get("link")) or None,
            item_type=_required(payload, "item_type"),
            design_article=_text(payload.get("design_article")) or None,
            category=_required(payload, "category"),
            calculated_name=_required(payload, "calculated_name"),
            calculated_quantity=_decimal(payload, "calculated_quantity", required=True),
            calculated_unit=_required(payload, "calculated_unit"),
            quality_document=_text(payload.get("quality_document")) or "Нет",
            calculated_price=_decimal(payload, "calculated_price"),
            calculated_price_with_vat=_decimal(payload, "calculated_price_with_vat"),
            calculated_sum=_decimal(payload, "calculated_sum"),
        )
    except (ValueError, TypeError, DecimalException) as error:
        if isinstance(error, ProcurementValidationError):
            raise
        raise ProcurementValidationError(f"Invalid procurement.requested payload: {error}") from error


BACKORDER_SUFFIX = "-Д"
_BACKORDER_ID = re.compile(r"-Д\d*$")


def is_backorder_id(legacy_id: str) -> bool:
    """`isBackorderId_` (Schema.gs:470): ID ends with -Д, -Д2, -Д3…"""
    return bool(_BACKORDER_ID.search(str(legacy_id or "")))


def next_backorder_id(base: str, existing: Iterable[str]) -> str:
    """`applyBackorders_`: <ID>-Д, then -Д2, -Д3; a backorder of a backorder becomes <ID>-Д-Д."""
    taken = set(existing)
    candidate, number = f"{base}{BACKORDER_SUFFIX}", 1
    while candidate in taken:
        number += 1
        candidate = f"{base}{BACKORDER_SUFFIX}{number}"
    return candidate


def create_backorder_line(original: ProcurementLine, message: OutboxMessage, legacy_id: str) -> ProcurementLine:
    """Copy of a paid line for the undelivered remainder: same price, payment and invoice, «Согласование».

    SUPPLYMAP copies the row whole; ERP keeps the exact (fractional) remainder and reprices the sum for it.
    """
    if message.topic != "procurement.backorder_requested":
        raise ProcurementValidationError(f"Unexpected topic: {message.topic}")
    if str(message.payload.get("procurement_line_id") or "") != str(original.id):
        raise ProcurementValidationError("procurement.backorder_requested: чужая строка закупки")
    try:
        quantity = Decimal(str(message.payload.get("backorder_quantity") or ""))
    except DecimalException as error:
        raise ProcurementValidationError("procurement.backorder_requested: invalid backorder_quantity") from error
    if not quantity.is_finite() or quantity <= 0:
        raise ProcurementValidationError("Недовоз должен быть больше нуля")
    if original.paid_at is None:
        raise ProcurementValidationError("Дозавоз ставится только по оплаченной строке")
    calculated_sum = (
        round_kopecks(original.calculated_price_with_vat * quantity)
        if original.calculated_price_with_vat is not None
        else None
    )
    return replace(
        original,
        id=uuid4(),
        source_event_id=message.id,
        source_idempotency_key=message.idempotency_key,
        legacy_id=legacy_id,
        status=ProcurementStatus.APPROVAL,
        version=1,
        created_at=message.created_at,
        calculated_quantity=quantity,
        calculated_sum=calculated_sum,
        actual_quantity=quantity,
        expected_arrival=None,
        money=rescale_to_quantity(original.money, original.actual_quantity or quantity, quantity),
    )
