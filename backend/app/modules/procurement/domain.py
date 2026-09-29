"""Pure Procurement rules for intake and buyer enrichment."""

from __future__ import annotations

import html
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

# A file uploaded to ERP is linked by its own download path (SUPPLYMAP linked the Drive file).
_STORED_INVOICE = re.compile(r"/api/procurement/files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def normalize_invoice_url(value: str | None) -> str | None:
    """СЧЕТ: an absolute http(s) link or the path of an invoice file stored by ERP."""
    text = str(value or "").strip()
    if _STORED_INVOICE.fullmatch(text):
        return text
    return normalize_document_url(value)


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


class ReplacementDecision(str, Enum):
    """РЕШ. ЗАМЕНЫ: the decision on a substitute the supplier offers instead of the requested item."""

    ALLOWED = "Разрешено"
    FORBIDDEN = "Запрещено"


# «РАЗРЕШЕНИЕ НА ЗАМЕНУ»: DOC_TYPES.replacement (Config.gs:500-504) — prefix «РЗ», number «РЗ-ГГГГММДД-NN».
PERMISSION_NUMBER_PREFIX = "РЗ"

# ЗАМЕНА is edited by the buyer from «В работе» until the goods are paid for (agent decision, see ARCHITECTURE.md).
REPLACEMENT_EDITABLE = frozenset(
    {
        ProcurementStatus.IN_WORK,
        ProcurementStatus.READY,
        ProcurementStatus.AWAITING_INVOICE,
        ProcurementStatus.TO_PAY,
    }
)


# ЕД.ИЗМ (факт): `PURCHASE_INWORK_COLS_` берёт список из валидации листа, фолбэк — `REQUEST_FORM_FIELDS['ЕД.ИЗМ']`
# (Config.gs:697). Валидации листа в ERP нет, поэтому список — тот же, что в форме заявки.
ACTUAL_UNITS = ("шт", "кг", "м", "л", "уп", "компл")

# PUR_STATUS_ORDER (Config.gs:56): linear pipeline order; «Согласование»/«Отменено» are outside it.
STATUS_ORDER = (
    ProcurementStatus.NEW,
    ProcurementStatus.IN_WORK,
    ProcurementStatus.READY,
    ProcurementStatus.AWAITING_INVOICE,
    ProcurementStatus.TO_PAY,
    ProcurementStatus.IN_TRANSIT,
    ProcurementStatus.QUARANTINE,
    ProcurementStatus.DONE,
)
# `portalPurchaseBySupplier`: the overview stages are PUR_STATUS_ORDER.slice(0, 5); «В пути» lives in
# receiving, «Готово» is already in stock, «Отменено» is closed — none of them enter the overview.
BY_SUPPLIER_STAGES = STATUS_ORDER[:5]
BY_SUPPLIER_CLOSED = frozenset(
    {ProcurementStatus.IN_TRANSIT, ProcurementStatus.DONE, ProcurementStatus.CANCELLED}
)

def supplier_key(value: object) -> str:
    """`supplierKey_` (Schema.gs:220): a URL/host → its host without «www.» (lower case); any other
    text (Склад, Фраксис, …) → itself in lower case."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    match = re.match(r"^[a-z]+://([^/]+)", text)
    host = match.group(1) if match else text.split("/")[0]
    if " " not in host and "." in host:
        return host[4:] if host.startswith("www.") else host
    return text


def supplier_from_link(link: object, suppliers: dict[str, str]) -> str:
    """`supplierFromLink_` (Schema.gs:231): the map's supplier for the link's key, otherwise ''."""
    key = supplier_key(link)
    return suppliers.get(key, "") if key else ""


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
class ProcurementTraceability:
    """Traceability columns of «Лист закупки» the buyer fills (TRACE_COLUMNS.PURCHASE, Config.gs:269):
    ИЗГОТОВИТЕЛЬ, СТРАНА, ТИП ДОК., № / ДАТА ДОК. КАЧЕСТВА — plus СТАНДАРТ, which SUPPLYMAP keeps on the
    waitlist; they travel to Receiving with the delivery (Config.gs:523-525 join into the ВК act)."""

    manufacturer: str | None = None
    country: str | None = None
    quality_document_type: str | None = None
    quality_document_number: str | None = None
    quality_document_date: date | None = None
    standard: str | None = None


@dataclass(frozen=True)
class MoneyEntry:
    """Invoice money form: typed amounts plus the invoice identity (НАИМЕНОВАНИЕ / № / ДАТА СЧЕТА)."""

    amounts: MoneyInput
    invoice_item_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None


@dataclass(frozen=True)
class KpEntry:
    """One row of the «Внести КП» form (`KP_EDIT_FIELDS`): fact quantity and unit, money, invoice, date."""

    money: MoneyEntry
    actual_quantity: Decimal | None = None
    actual_unit: str | None = None
    expected_arrival: date | None = None


MONEY_EDITABLE = frozenset({ProcurementStatus.AWAITING_INVOICE, ProcurementStatus.TO_PAY})


_PAID_STATUSES = frozenset({ProcurementStatus.IN_TRANSIT, ProcurementStatus.QUARANTINE, ProcurementStatus.DONE})
_BACKORDER_OFFLINE = "У дозавоза свои действия: подтвердить досыл или отменить дозавоз"


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
    order_number: str | None = None
    order_date: date | None = None
    replacement: bool | None = None
    replacement_decision: ReplacementDecision | None = None
    permission_number: str | None = None
    permission_date: date | None = None
    # Инициатор as a user: status notifications go to this id; None = the name matched nobody (not notified).
    initiator_id: UUID | None = None
    trace: ProcurementTraceability = ProcurementTraceability()

    @property
    def replacement_open(self) -> bool:
        """`zamOpen` (SupplierRequest.gs): ЗАМЕНА = Да without the decision «Разрешено»."""
        return self.replacement is True and self.replacement_decision is not ReplacementDecision.ALLOWED

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

    def send_supplier_request(
        self,
        order_number: str,
        order_date: date,
        reference: str,
        actor: Actor,
    ) -> ProcurementLine:
        """`buildSupplierRequestsCore_`: the line joins supplier order № ЗАКАЗА of ДАТА ЗАКАЗА → «Жду счет»."""
        self._require_owner(actor)
        number = _text(order_number)
        if not number:
            raise ProcurementValidationError("Не задан № заказа")
        if self.status is ProcurementStatus.AWAITING_INVOICE and self.order_number == number:
            return self
        if self.status is not ProcurementStatus.READY:
            raise ProcurementValidationError("Запрос поставщику формируется только по готовой строке")
        return replace(
            self,
            order_number=number,
            order_date=order_date,
            supplier_request_reference=_text(reference) or number,
            status=ProcurementStatus.AWAITING_INVOICE,
            version=self.version + 1,
        )

    def set_replacement(
        self,
        replacement: bool,
        decision: ReplacementDecision | None,
        actor: Actor,
        permission_number: str | None = None,
        permission_date: date | None = None,
    ) -> ProcurementLine:
        """`applyReplacement_` / `applyReplacementDecision_` (Guards.gs): the buyer marks ЗАМЕНА by hand.

        Да waits for a decision (none yet = open); Нет clears the decision, as the sheet writes «—» there.

        `permission_number`/`permission_date` are precomputed by the caller (numbering needs the
        repository) and are only kept when the transition lands on «Разрешено» — any other outcome
        voids «РАЗРЕШЕНИЕ НА ЗАМЕНУ» (`clearReplacementBlock_`, Guards.gs:632-637), same as ЗАМЕНА=Нет
        or a decision changed away from «Разрешено».
        """
        self._require_owner(actor)
        if not replacement and decision is not None:
            raise ProcurementValidationError("Решение по замене ставится только при ЗАМЕНА = Да")
        if self.replacement == replacement and self.replacement_decision == decision:
            return self
        if self.status not in REPLACEMENT_EDITABLE:
            raise ProcurementValidationError(
                "Замену отмечают в работе у закупщика — от «В работе» до «В оплату»"
            )
        keeps_permission = replacement and decision is ReplacementDecision.ALLOWED
        return replace(
            self,
            replacement=replacement,
            replacement_decision=decision,
            permission_number=permission_number if keeps_permission else None,
            permission_date=permission_date if keeps_permission else None,
            version=self.version + 1,
        )

    def set_traceability(self, trace: ProcurementTraceability, actor: Actor) -> ProcurementLine:
        """The buyer types ИЗГОТОВИТЕЛЬ / СТРАНА / ТИП ДОК. / № и ДАТА ДОК. КАЧЕСТВА / СТАНДАРТ on their line.
        They are not required for the invoice (PUR_PAY_REQUIRED, ENFORCE_QUALITY_DOC=false), so they are
        editable from «В работе» to «В оплату»; after payment the delivery snapshot is with Receiving, which
        keeps editing them there."""
        self._require_owner(actor)
        normalized = ProcurementTraceability(
            manufacturer=_text(trace.manufacturer) or None,
            country=_text(trace.country) or None,
            quality_document_type=_text(trace.quality_document_type) or None,
            quality_document_number=_text(trace.quality_document_number) or None,
            quality_document_date=trace.quality_document_date,
            standard=_text(trace.standard) or None,
        )
        if normalized == self.trace:
            return self
        if self.status not in REPLACEMENT_EDITABLE:
            raise ProcurementValidationError(
                "Прослеживаемость заполняют в работе у закупщика — от «В работе» до «В оплату»; дальше — в Приёмке"
            )
        return replace(self, trace=normalized, version=self.version + 1)

    def record_invoice(
        self,
        invoice_url: str,
        expected_arrival: date,
        actor: Actor,
        invoice_number: str | None = None,
        invoice_date: date | None = None,
    ) -> ProcurementLine:
        """`uploadInvoice` on a «Жду счет» line: СЧЕТ, and № / ДАТА СЧЕТА from the file when recognised."""
        self._require_owner(actor)
        try:
            url = normalize_invoice_url(invoice_url)
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
            invoice_number=_text(invoice_number) or self.invoice_number,
            invoice_date=invoice_date or self.invoice_date,
            expected_arrival=expected_arrival,
            status=ProcurementStatus.TO_PAY,
            version=self.version + 1,
        )

    def replace_invoice(
        self,
        invoice_url: str,
        actor: Actor,
        invoice_number: str | None = None,
        invoice_date: date | None = None,
    ) -> ProcurementLine:
        """`uploadInvoice` on a line that already has an invoice: correction, not a new stage.

        СЧЕТ keeps only the new file (the old one stays in the audit trail); № and ДАТА СЧЕТА from the
        file overwrite, an empty value leaves the stored one. ОПЛАТА, status and dates stay as they are.
        """
        self._require_owner(actor)
        try:
            url = normalize_invoice_url(invoice_url)
        except ValueError as error:
            raise ProcurementValidationError(str(error)) from error
        if url is None:
            raise ProcurementValidationError("Счёт обязателен")
        if self.invoice_url is None:
            raise ProcurementValidationError("Счёта ещё нет — загрузите его на этапе «Жду счет»")
        if self.status is ProcurementStatus.CANCELLED:
            raise ProcurementValidationError("Счёт отменённой строки не заменяется")
        # ОЖИД. ДАТА is not part of the invoice: a backorder line clears it until the resend is confirmed.
        missing = [name for name in self.pay_missing() if name != "ОЖИД. ДАТА"]
        if missing:
            raise ProcurementValidationError("Нельзя загрузить счёт — сначала заполните: " + ", ".join(missing))
        updated = replace(
            self,
            invoice_url=url,
            invoice_number=_text(invoice_number) or self.invoice_number,
            invoice_date=invoice_date or self.invoice_date,
        )
        if updated == self:
            return self
        return replace(updated, version=self.version + 1)

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

    def apply_kp(self, entry: KpEntry, rate: ExchangeRate | None, actor: Actor) -> ProcurementLine:
        """`submitKp_`: writes the form fields of one order line; the status stays «Жду счет»."""
        self._require_owner(actor)
        if self.status is not ProcurementStatus.AWAITING_INVOICE:
            raise ProcurementValidationError(
                f"«Внести КП» доступно только в статусе «{ProcurementStatus.AWAITING_INVOICE.value}». "
                f"{self.legacy_id}: «{self.status.value}»"
            )
        quantity = entry.actual_quantity
        if quantity is not None and quantity <= 0:
            raise ProcurementValidationError(f"{self.legacy_id}: КОЛ-ВО (факт) должно быть больше нуля")
        if entry.expected_arrival is not None and entry.expected_arrival < self.created_at.date():
            raise ProcurementValidationError(
                f"{self.legacy_id}: ожидаемая дата поступления не может быть раньше заказа"
            )
        try:
            money = apply_currency_and_fact(entry.money.amounts, quantity, rate)
        except MoneyValidationError as error:
            raise ProcurementValidationError(f"{self.legacy_id}: {error}") from error
        updated = replace(
            self,
            actual_quantity=quantity,
            actual_unit=_text(entry.actual_unit) or None,
            expected_arrival=entry.expected_arrival,
            money=money,
            invoice_item_name=_text(entry.money.invoice_item_name) or None,
            invoice_number=_text(entry.money.invoice_number) or None,
            invoice_date=entry.money.invoice_date,
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

    def put_on_approval(self, actor: Actor) -> ProcurementLine:
        """`PUR_STATUS_OFFLINE`: «Согласование» stands outside the linear order, set at any stage before payment."""
        self._require_offline_allowed(actor)
        if self.status is ProcurementStatus.APPROVAL:
            return self
        if self.status is ProcurementStatus.CANCELLED:
            raise ProcurementValidationError("Строка отменена — отмена окончательная")
        return replace(self, buyer=self.buyer or actor, status=ProcurementStatus.APPROVAL, version=self.version + 1)

    def resume_from_approval(self, actor: Actor) -> ProcurementLine:
        """Approved: the line returns to the stage its own data has reached (`statusRegressed_` skips offline)."""
        if self.is_backorder:
            raise ProcurementValidationError(_BACKORDER_OFFLINE)
        self._require_owner(actor)
        if self.status is not ProcurementStatus.APPROVAL:
            raise ProcurementValidationError("Строка не на согласовании")
        return replace(self, status=self._stage_from_data(), version=self.version + 1)

    def cancel(self, actor: Actor) -> ProcurementLine:
        """«Отменено» is terminal and reachable from any stage before payment."""
        self._require_offline_allowed(actor)
        if self.status is ProcurementStatus.CANCELLED:
            return self
        return replace(self, buyer=self.buyer or actor, status=ProcurementStatus.CANCELLED, version=self.version + 1)

    def _require_offline_allowed(self, actor: Actor) -> None:
        if self.is_backorder:
            raise ProcurementValidationError(_BACKORDER_OFFLINE)
        _validate_actor(actor)
        # An unclaimed «Новый» line has no owner yet: whoever parks or cancels it takes it.
        if self.buyer is not None:
            self._require_owner(actor)
        if self.status in _PAID_STATUSES:
            raise ProcurementValidationError(
                "Строка оплачена и передана в приёмку — согласование и отмена уже невозможны"
            )

    def _stage_from_data(self) -> ProcurementStatus:
        if self.invoice_url:
            return ProcurementStatus.TO_PAY
        if self.supplier_request_reference:
            return ProcurementStatus.AWAITING_INVOICE
        if self.buyer is None:
            return ProcurementStatus.NEW
        facts = ProcurementFacts(
            supplier=self.supplier, actual_quantity=self.actual_quantity, actual_unit=self.actual_unit
        )
        return ProcurementStatus.READY if _facts_ready(facts, self.delivery_point) else ProcurementStatus.IN_WORK

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


def create_line_from_requested(
    message: OutboxMessage,
    *,
    legacy_id: str | None = None,
    revision: str = "A",
) -> ProcurementLine:
    """First transfer → «Новый», Рев.A; a re-transfer passes its new System ID and revision letter."""
    if message.topic != "procurement.requested":
        raise ProcurementValidationError(f"Unexpected topic: {message.topic}")
    payload = message.payload
    try:
        return ProcurementLine(
            id=uuid4(),
            source_event_id=message.id,
            source_idempotency_key=message.idempotency_key,
            source_request_id=UUID(_required(payload, "source_request_id")),
            legacy_id=legacy_id or _required(payload, "legacy_id"),
            revision=revision,
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
            initiator_id=UUID(_text(payload.get("initiator_id"))) if _text(payload.get("initiator_id")) else None,
        )
    except (ValueError, TypeError, DecimalException) as error:
        if isinstance(error, ProcurementValidationError):
            raise
        raise ProcurementValidationError(f"Invalid procurement.requested payload: {error}") from error


def revision_to_number(revision: str) -> int:
    """`letterToNum_` (Schema.gs:114): 'A'→1, 'Z'→26, 'AA'→27; anything but Latin letters → 0."""
    text = str(revision or "").strip().upper()
    if not re.fullmatch(r"[A-Z]+", text):
        return 0
    number = 0
    for char in text:
        number = number * 26 + (ord(char) - 64)
    return number


def number_to_revision(number: int) -> str:
    """`numToLetter_` (Schema.gs:123): 1→'A', 26→'Z', 27→'AA'; below 1 → ''."""
    letters = ""
    while number > 0:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def designation_key(designation: str | None) -> str:
    """НАЗНАЧЕНИЕ as `requestRowsToPurchaseLocked_` groups revisions: `normHeader_` (Schema.gs:5) —
    whitespace runs collapsed, trimmed, case-insensitive."""
    return " ".join(str(designation or "").split()).casefold()


def next_retransfer_revision(revisions: Iterable[str]) -> str:
    """`nextRetransferRevision_` (Schema.gs:134): max letter among the designation's lines + 1; none → 'A'."""
    return number_to_revision(max((revision_to_number(item) for item in revisions), default=0) + 1)


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


# --- Запрос поставщику (SupplierRequest.gs) -------------------------------------------------------

ORDER_PREFIX = {"закупка": "PO"}
DEFAULT_ORDER_PREFIX = "PO"
SUPPLIER_REQUEST_VAT = "22%"
NO_SUPPLIER = "(без поставщика)"
# SUPPLIER_REQUEST.folder (Config.gs): корневая папка документов запросов.
SUPPLIER_REQUEST_FOLDER = "Запросы поставщикам"


@dataclass(frozen=True)
class DeliveryReceiver:
    """Получатель на точке отгрузки (`SUPPLIER_REQUEST.receivers`, Config.gs): контакт для доставки."""

    name: str
    phone: str
    address: str = ""


# SUPPLIER_REQUEST.receivers (Config.gs): ключ — ТОЧКА ОТГРУЗКИ как есть; 'default' — для прочих точек.
SUPPLIER_REQUEST_RECEIVERS: dict[str, DeliveryReceiver] = {
    "default": DeliveryReceiver(name="Гусев Алексей", phone="+79259647705"),
    "г. Москва, ул. 6-я Радиальная, д. 17": DeliveryReceiver(name="Гусев Алексей", phone="+79259647705"),
    "г. Раменское, ул. 100-й Свирской Дивизии, д. 52": DeliveryReceiver(
        name="Малышев Степан", phone="+7 977 846 3541"
    ),
}


def delivery_receiver(point: str) -> DeliveryReceiver:
    """`SUPPLIER_REQUEST.receivers[g.ship] || receivers['default']` — точное совпадение ключа, как в JS."""
    return SUPPLIER_REQUEST_RECEIVERS.get(point) or SUPPLIER_REQUEST_RECEIVERS["default"]


def _norm(value: str | None) -> str:
    """`normHeader_`: collapse whitespace, trim, lower-case."""
    return " ".join(str(value or "").split()).lower()


def order_prefix(task_type: str | None) -> str:
    """`ORDER_PREFIX[normHeader_(ТИП ЗАДАЧИ)] || 'PO'`."""
    return ORDER_PREFIX.get(_norm(task_type), DEFAULT_ORDER_PREFIX)


def order_number(prefix: str, number: int) -> str:
    """`orderNumber_`: at least three digits (001…999), 1000+ without padding."""
    return f"{prefix}-{number:03d}"


def next_order_number(existing: Iterable[str | None], prefix: str) -> str:
    """`nextOrderNumber_`: max of `<prefix>-<digits>` already issued (case-insensitive) + 1."""
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    highest = 0
    for value in existing:
        match = pattern.match(str(value or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return order_number(prefix, highest + 1)


@dataclass(frozen=True)
class SupplierRequestGroup:
    """One supplier order: the buyer's «Готов к запросу» lines of one supplier and one task type."""

    supplier: str
    task_type: str
    lines: tuple[ProcurementLine, ...]

    @property
    def delivery_point(self) -> str:
        # SUPPLYMAP takes the first non-empty ТОЧКА ОТГРУЗКИ of the group.
        return next((_text(line.delivery_point) for line in self.lines if _text(line.delivery_point)), "")

    @property
    def designation(self) -> str:
        return _text(self.lines[0].designation)

    @property
    def request_date(self) -> date:
        return self.lines[0].request_date

    @property
    def prefix(self) -> str:
        return order_prefix(self.task_type)


def group_supplier_requests(
    lines: Iterable[ProcurementLine], actor: Actor
) -> tuple[list[SupplierRequestGroup], int]:
    """Group the actor's «Готов к запросу» lines by supplier + task type, in line order.

    Returns the groups and the number of ready lines owned by other buyers (skipped: each buyer
    forms requests for their own lines only, otherwise two buyers' work would merge into one order).
    """
    _validate_actor(actor)
    groups: dict[tuple[str, str], list[ProcurementLine]] = {}
    other_buyer = 0
    for line in lines:
        if line.status is not ProcurementStatus.READY:
            continue
        if not _same_actor(line.buyer, actor):
            other_buyer += 1
            continue
        key = (_text(line.supplier) or NO_SUPPLIER, _text(line.task_type))
        groups.setdefault(key, []).append(line)
    return (
        [
            SupplierRequestGroup(supplier=supplier, task_type=task_type, lines=tuple(members))
            for (supplier, task_type), members in groups.items()
        ],
        other_buyer,
    )


def supplier_request_blockers(group: SupplierRequestGroup) -> str | None:
    """Why `buildSupplierRequestsCore_` would not form this group; None — it may be formed."""
    tag = group.supplier + (f" ({group.task_type})" if group.task_type else "")
    if group.supplier == NO_SUPPLIER:
        return f"{NO_SUPPLIER}: не сформирован"
    if not group.delivery_point:
        return f"{group.supplier}: нет адреса отгрузки"
    if any(line.actual_quantity is None or not _text(line.actual_unit) for line in group.lines):
        return f"{tag}: не заполнены КОЛ-ВО/ЕД.ИЗМ (факт) — не сформирован"
    if any(line.replacement_open for line in group.lines):
        return f"{tag}: есть незакрытая замена (нет решения «Разрешено») — не сформирован"
    return None


def supplier_reference(request_date: date | None, designation: str, order_no: str) -> str:
    """`supplierRef_`: «REQ# <Дата запроса> - MR#<НАЗНАЧЕНИЕ> <№ заказа>»."""
    shown = request_date.strftime("%d.%m.%Y") if request_date else ""
    return f"REQ# {shown} - MR#{_text(designation)} {_text(order_no)}"


def _quantity_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f").replace(".", ",")


def position_paragraphs(lines: Iterable[ProcurementLine]) -> list[str]:
    """`positionParagraphs_`: numbered name, then tab-indented quantity, link and quality document."""
    out: list[str] = []
    for index, line in enumerate(lines, start=1):
        name = _text(line.site_name) or _text(line.calculated_name)
        quantity = f"{_quantity_text(line.actual_quantity)} {_text(line.actual_unit)}".strip()
        out.append(f"{index}. Наименование: {name}")
        if quantity:
            out.append(f"\tКол-во: {quantity}")
        if _text(line.link):
            out.append(f"\tСсылка: {_text(line.link)}")
        if _norm(line.quality_document) == "да":
            document_type = _text(line.trace.quality_document_type)
            out.append(f"\tДокумент по качеству: требуется{f' ({document_type})' if document_type else ''}.")
    return out


def supplier_request_letter(group: SupplierRequestGroup, order_no: str) -> str:
    """`supplierTemplateBody_` filled for one group (the Google Doc of SUPPLYMAP, as plain text)."""
    receiver = delivery_receiver(group.delivery_point)
    return "\n".join(
        [
            f"{group.supplier} · {supplier_reference(group.request_date, group.designation, order_no)}",
            "",
            "Добрый день!",
            "",
            "Прошу сообщить о наличии и условиях поставки для следующих позиций:",
            "",
            *position_paragraphs(group.lines),
            "",
            "По каждой позиции по возможности, прошу указать изготовителя (бренд) и страну происхождения.",
            f"Работаем с НДС {SUPPLIER_REQUEST_VAT}. Карточка организации — во вложении.",
            f"Адрес доставки: {receiver.address or group.delivery_point}.",
            f"Контактное лицо для доставки: {receiver.name}, тел. {receiver.phone}.",
        ]
    )


_MONTHS_RU = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
_FOLDER_FORBIDDEN = re.compile(r'[\\/:*?"<>|]+')


def sanitize_folder_name(value: str | None) -> str:
    """`sanitizeFolderName_` (DocGen.gs): недопустимые для папки символы → пробел, пробелы схлопнуты."""
    return " ".join(_FOLDER_FORBIDDEN.sub(" ", str(value or "")).split())


def date_supplier_segments(day: date, supplier: str) -> list[str]:
    """`dateSupplierSegments_` (DocGen.gs): год / «NN Месяц» / поставщик («Без поставщика»)."""
    month = f"{day.month:02d} {_MONTHS_RU[day.month - 1]}"
    return [str(day.year), month, sanitize_folder_name(supplier) or "Без поставщика"]


def supplier_request_folder(day: date, supplier: str) -> str:
    """Папка документа: «Запросы поставщикам/год/месяц/поставщик» (`ensureFolderPath_(reqRoot, …)`)."""
    return "/".join([SUPPLIER_REQUEST_FOLDER, *date_supplier_segments(day, supplier)])


def supplier_request_document_title(order_no: str, day: date) -> str:
    """Имя копии шаблона: `orderNo + ' от ' + dd.MM.yyyy`."""
    return f"{order_no} от {day:%d.%m.%Y}"


# Шаблон SUPPLYMAP — обычный Google Doc: абзацы Arial 11, межстрочный 1,15, поля 2,54 см; подпозиции — с табом.
_DOCUMENT_CSS = (
    "@page{size:A4;margin:2.54cm}"
    "body{font-family:Arial,Helvetica,sans-serif;font-size:11pt;line-height:1.15;color:#000;"
    "max-width:16cm;margin:2.54cm auto;background:#fff}"
    "p{margin:0;white-space:pre-wrap;tab-size:4}"
    "@media print{body{margin:0;max-width:none}}"
)


def supplier_request_document_html(title: str, letter: str) -> str:
    """Документ запроса: шаблон `supplierTemplateBody_` с подставленными полями, по абзацу на строку."""
    paragraphs = "".join(f"<p>{html.escape(line) if line else '&nbsp;'}</p>" for line in letter.split("\n"))
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{_DOCUMENT_CSS}</style></head>"
        f"<body>{paragraphs}</body></html>"
    )


SPLIT_MAX_COPIES = 30
_LEADING_DIGITS = re.compile(r"^\d+")


def next_system_id(existing_ids: Iterable[str], day: date, prefix: str = "SM") -> str:
    """`nextSystemId_` (SystemId.gs:13) over procurement IDs: <prefix>-YYYYMMDD-NN, max + 1.

    Like `parseInt`, only the leading digits of the tail count, so `SM-…-03-Д` counts as 03.
    """
    stem = f"{prefix}-{day:%Y%m%d}-"
    highest = 0
    for existing in existing_ids:
        value = str(existing)
        if value.startswith(stem):
            match = _LEADING_DIGITS.match(value[len(stem) :])
            if match:
                highest = max(highest, int(match.group()))
    return f"{stem}{highest + 1:02d}"


def split_line(
    source: ProcurementLine,
    copies: int,
    existing_ids: Iterable[str],
    day: date,
    now: datetime,
) -> list[ProcurementLine]:
    """`applyPendingSplitsToSheet_` (Pipeline.gs:184-220): «Раздробить по поставщикам».

    N copies (1–30) of the line with NEW System IDs, «Новый», Рев.A. They keep the request data
    (`REQUEST_TO_PURCHASE_MAP`: dates, initiator, designation, point, link, article, calculated
    name/quantity/price) and drop everything the buyer filled: buyer, supplier, facts, order, invoice,
    money, payment. The source line stays as it is; any status may be split, as in SUPPLYMAP.
    """
    if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= SPLIT_MAX_COPIES:
        raise ProcurementValidationError(f"Число копий — 1–{SPLIT_MAX_COPIES}.")
    taken = list(existing_ids)
    result: list[ProcurementLine] = []
    for number in range(1, copies + 1):
        legacy_id = next_system_id(taken, day)
        taken.append(legacy_id)
        result.append(
            ProcurementLine(
                id=uuid4(),
                source_event_id=uuid4(),
                source_idempotency_key=f"procurement:{source.id}:split:v{source.version}:{number}",
                source_request_id=source.source_request_id,
                legacy_id=legacy_id,
                revision="A",
                status=ProcurementStatus.NEW,
                version=1,
                created_at=now,
                request_date=source.request_date,
                initiator=source.initiator,
                department=source.department,
                task_type=source.task_type,
                designation=source.designation,
                project_reference=source.project_reference,
                priority=source.priority,
                delivery_point=source.delivery_point,
                planned_date=source.planned_date,
                link=source.link,
                item_type=source.item_type,
                design_article=source.design_article,
                category=source.category,
                calculated_name=source.calculated_name,
                calculated_quantity=source.calculated_quantity,
                calculated_unit=source.calculated_unit,
                quality_document=source.quality_document,
                calculated_price=source.calculated_price,
                calculated_price_with_vat=source.calculated_price_with_vat,
                calculated_sum=source.calculated_sum,
                initiator_id=source.initiator_id,
            )
        )
    return result
