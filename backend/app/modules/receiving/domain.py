"""Pure receiving and incoming-control rules ported from SUPPLYMAP."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from datetime import datetime
from decimal import Decimal, DecimalException
from enum import Enum
from typing import Iterable
from uuid import UUID, uuid4

from app.shared.document_url import normalize_document_url as _normalize_external_url
from app.shared.events import OutboxMessage
from app.shared.identity import Actor
from app.shared.unit_mass import mass_per_unit
from app.shared.warehouse_location import existing_storage_location


class ReceivingValidationError(ValueError):
    """A delivery cannot be inspected without trustworthy source facts."""


# A file uploaded to ERP (УПД/М-15, документ качества) is linked by its own download path — SUPPLYMAP
# linked the Drive file. Anything else must be an absolute http(s) link.
RECEIVING_FILE_PATH = "/api/receiving/files/"
_STORED_FILE = re.compile(
    re.escape(RECEIVING_FILE_PATH) + r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def normalize_document_url(value: str | None) -> str | None:
    text = str(value or "").strip()
    if _STORED_FILE.fullmatch(text):
        return text
    return _normalize_external_url(value)


class ReceivingStatus(str, Enum):
    IN_TRANSIT = "В пути"
    WAITING = "Ожидание"
    ACCEPTED = "Принято"
    QUARANTINE = "Карантин"
    RETURN = "Возврат"
    VK_ARCHIVE = "Архив ВК"


# Kept for lines inspected before 05.10.2026 and for the act layout: a missing УПД no longer blocks a line.
MISSING_SHIPPING_DOCUMENT = "Отсутствие / несоответствие УПД/М-15"
# Owner 05.10.2026: УПД often comes later by ЭДО — the line is accepted without it, with this note until
# «Внести УПД» adds the document. No alert, no hold.
SHIPPING_DOCUMENT_PENDING_NOTE = "УПД не приложен — придёт по ЭДО"
MISSING_QUALITY_DOCUMENT = "Отсутствие / несоответствие документа по качеству"
WRONG_ITEM = "Не тот товар (несоответствие счету)"
EXPIRED = "Истёк срок годности"
SHORT_FACT = "Недостача (факт < УПД)"
SHORT_ORDER = "Недовоз (УПД < заказ)"
SURPLUS = "Излишек (факт > УПД)"
MECHANICAL_DAMAGE = "Механическое повреждение / брак"

DOCUMENT_NONCONFORMITIES = {MISSING_SHIPPING_DOCUMENT, MISSING_QUALITY_DOCUMENT}
TERMINAL_NONCONFORMITIES = {WRONG_ITEM, EXPIRED}
QUANTITY_FAULTS = {SHORT_FACT, SHORT_ORDER}
QUALITY_FAULTS = {MECHANICAL_DAMAGE, WRONG_ITEM}
MANUAL_NONCONFORMITIES = QUALITY_FAULTS
# Owner, 05.10.2026: good stock is never held back by a count or a missing certificate. A shortfall (недовоз,
# недостача) is accepted by the fact quantity and still gets its acts (АН, РК) and alert; the remainder of a
# недовоз goes to the `-Д` backorder. A missing quality document is only a warning on the line and the ВК act.
WARNING_NONCONFORMITIES = frozenset({SURPLUS, MISSING_QUALITY_DOCUMENT})
NON_BLOCKING_NONCONFORMITIES = WARNING_NONCONFORMITIES | QUANTITY_FAULTS
# «Провести поставку»: the one deviation a receiver may mark on a line → its nature of nonconformity.
DELIVERY_DEVIATIONS: dict[str, str] = {
    "none": "",
    "brak": MECHANICAL_DAMAGE,
    "peresort": WRONG_ITEM,
    "no_quality_doc": MISSING_QUALITY_DOCUMENT,
    "expired": EXPIRED,
}

# Config.gs:221-233 — «Ожидание» без решения держится не дольше QUAR_WORKDAYS рабочих дней от даты
# акта ВК; дальше тик сам переводит строку в «Карантин» и зовёт ответственного по характеру несоответствия.
QUARANTINE_WORKDAYS = 2
# Open deliveries: ОЖИД. ДАТА still means something until the line is accepted, returned or archived.
EXPECTED_ARRIVAL_FOLLOWS = frozenset({ReceivingStatus.IN_TRANSIT, ReceivingStatus.WAITING, ReceivingStatus.QUARANTINE})
# «Внести УПД»: everything but «Архив ВК» (history) and «Возврат» (closed).
ATTACH_DOCUMENT_STATUSES = frozenset({
    ReceivingStatus.IN_TRANSIT, ReceivingStatus.WAITING, ReceivingStatus.QUARANTINE, ReceivingStatus.ACCEPTED,
})
TECHNOLOGIST_NONCONFORMITIES = {MECHANICAL_DAMAGE}
NOTIFY_TECHNOLOGIST = "Технолог"
NOTIFY_SUPPLY = "Снабжение"

# SupplierReturn.gs:15-22 — причины возврата поставщику. Первые три — литералы характера несоответствия
# входного контроля: возврат по браку продолжает ВК, и формулировки должны совпадать с актами.
SUPPLIER_RETURN_REASONS = (
    MECHANICAL_DAMAGE,
    WRONG_ITEM,
    EXPIRED,
    "Пересорт / несоответствие спецификации",
    "Не нужна — возврат из-за брака основной позиции",
    "Отказ от поставки (иное)",
)
SUPPLIER_RETURN_DISPOSITION = "Возврат поставщику"


class SupplierReturnMode(str, Enum):
    """SupplierReturn.gs:24-26: ждём замену → строка снова «В пути»; заказ закрыт → «Возврат»."""

    WAIT = "wait"
    CLOSE = "close"


def supplier_return_basis(reason: str, claim: str = "", comment: str = "") -> str:
    """SupplierReturn.gs supplierReturnBasis_: одна строка для журнала склада и ОПИСАНИЯ НЕСООТВ."""
    claim_text = str(claim or "").strip()
    comment_text = str(comment or "").strip()
    return (
        f"Возврат поставщику: {str(reason or '').strip()}"
        + (f" · претензия {claim_text}" if claim_text else "")
        + (f" · {comment_text}" if comment_text else "")
    )


def workdays_between(start: date, end: date) -> int:
    """Full Mon–Fri days after ``start`` up to and including ``end``; holidays ignored (Schema.gs:564)."""
    count = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def latest_overdue_inspection_date(today: date) -> date:
    """The latest act date whose «Ожидание» is already overdue on ``today``."""
    day = today
    while workdays_between(day, today) < QUARANTINE_WORKDAYS:
        day -= timedelta(days=1)
    return day


def quarantine_notify_targets(nonconformities: Iterable[str]) -> tuple[str, ...]:
    """OwnerTick.gs quarNotifyTargets_: брак → технолог, остальное или пусто → снабжение, смесь → оба."""
    items = [str(item).strip() for item in nonconformities if str(item).strip()]
    technologist = any(item in TECHNOLOGIST_NONCONFORMITIES for item in items)
    supply = not technologist or any(item not in TECHNOLOGIST_NONCONFORMITIES for item in items)
    return tuple(
        target
        for target, wanted in ((NOTIFY_TECHNOLOGIST, technologist), (NOTIFY_SUPPLY, supply))
        if wanted
    )


@dataclass(frozen=True)
class InspectionInput:
    fact_quantity: Decimal
    document_quantity: Decimal
    ordered_quantity: Decimal
    # Informational only since 05.10.2026: a missing УПД is not a nonconformity (SHIPPING_DOCUMENT_PENDING_NOTE).
    has_shipping_document: bool
    quality_document_required: bool
    quality_document_present: bool
    expiry_date: date | None
    inspection_date: date
    manual_nonconformities: tuple[str, ...] = ()
    disposition: str | None = None
    shipping_document_url: str | None = None
    quality_document_url: str | None = None
    series: str = ""
    # МАССА 1 ШТ: the weight of 1, 10 or 100 pieces as typed; any one is enough (Receiving.gs:437-441).
    mass_1: object = None
    mass_10: object = None
    mass_100: object = None
    # Receiving.gs:685-693 — historical receipt judges expiry on the УПД date, not on today.
    verdict_date: date | None = None
    # receiveFields_/batchFields_ (Receiving.gs:252-324): реквизиты накладной и партии изготовителя.
    document_number: str = ""
    document_date: date | None = None
    document_name: str = ""
    note: str = ""
    manufacture_date: date | None = None
    nonconformity_description: str = ""
    # Лист закупки ИЗГОТОВИТЕЛЬ/СТРАНА/ТИП ДОК./№ и ДАТА ДОК. КАЧЕСТВА, Лист ожидания СТАНДАРТ → акт ВК.
    manufacturer: str = ""
    country: str = ""
    quality_document_type: str = ""
    quality_document_number: str = ""
    standard: str = ""
    quality_document_date: date | None = None


# RC-9: акты приёмки (Config.gs DOC_TYPES): вид → (префикс номера, заголовок).
ACT_KINDS: dict[str, tuple[str, str]] = {
    "incoming": ("ВК", "Акт входного контроля"),
    "nonconformity": ("АН", "Акт о несоответствии"),
    "complaint": ("РК", "Акт рекламации"),
    "usepermit": ("РИ", "Разрешение на использование"),
}


def act_number_prefix(kind: str, day: date) -> str:
    return f"{ACT_KINDS[kind][0]}-{day.strftime('%Y%m%d')}-"


def next_act_number(kind: str, day: date, existing: Iterable[str]) -> str:
    """SystemId.gs nextSystemId_: ПРЕФИКС-ГГГГММДД-NN, NN — следующий за наибольшим за этот день."""
    prefix = act_number_prefix(kind, day)
    top = 0
    for value in existing:
        text = str(value or "")
        if text.startswith(prefix):
            tail = text[len(prefix):]
            if tail.isdigit():
                top = max(top, int(tail))
    return f"{prefix}{top + 1:02d}"


def verdict_reference_date(
    *,
    today: date,
    historical: bool,
    document_date: date | None = None,
    arrival_date: date | None = None,
) -> date:
    """«Сегодня» для вердикта ВК: у исторической приёмки — ДАТА УПД/М-15, иначе ФАКТ. ДАТА, иначе сегодня."""
    if not historical:
        return today
    return document_date or arrival_date or today


@dataclass(frozen=True)
class InspectionResult:
    verdict: str
    status: ReceivingStatus
    nonconformities: tuple[str, ...]
    accepted_quantity: Decimal
    backorder_quantity: Decimal
    missing_documents: bool
    nonconformity_act_required: bool
    complaint_required: bool


def _positive(value: Decimal, label: str) -> Decimal:
    normalized = Decimal(value)
    if normalized <= 0:
        raise ReceivingValidationError(f"{label} должно быть больше нуля")
    return normalized


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def inspect_delivery(data: InspectionInput) -> InspectionResult:
    fact = _positive(data.fact_quantity, "Фактическое количество")
    document = _positive(data.document_quantity, "Количество по УПД/М-15")
    ordered = _positive(data.ordered_quantity, "Заказанное количество")
    unknown = set(data.manual_nonconformities) - MANUAL_NONCONFORMITIES
    if unknown:
        raise ReceivingValidationError("Неизвестный пункт ручного входного контроля")

    nature: list[str] = []
    for item in data.manual_nonconformities:
        _append_unique(nature, item)
    if data.quality_document_required and not data.quality_document_present:
        _append_unique(nature, MISSING_QUALITY_DOCUMENT)
    reference = data.verdict_date or data.inspection_date
    if data.expiry_date is not None and data.expiry_date < reference:
        _append_unique(nature, EXPIRED)
    if fact < document:
        _append_unique(nature, SHORT_FACT)
    if document < ordered:
        _append_unique(nature, SHORT_ORDER)
    if fact > document:
        _append_unique(nature, SURPLUS)

    terminal = any(item in TERMINAL_NONCONFORMITIES for item in nature)
    nonconforming = [item for item in nature if item != SURPLUS]
    blocking = [item for item in nature if item not in NON_BLOCKING_NONCONFORMITIES]
    if terminal:
        status = ReceivingStatus.RETURN
        verdict = "Не годен"
    elif blocking:
        status = ReceivingStatus.QUARANTINE if str(data.disposition or "").strip() else ReceivingStatus.WAITING
        verdict = "Условно годен"
    else:
        status = ReceivingStatus.ACCEPTED
        verdict = "Годен"

    act_items = [item for item in nonconforming if item not in DOCUMENT_NONCONFORMITIES]
    quantity_fault = any(item in QUANTITY_FAULTS for item in nature)
    quality_fault = any(item in QUALITY_FAULTS for item in nature)
    complaint = quantity_fault or (
        quality_fault and str(data.disposition or "").strip() == "Возврат поставщику"
    )
    return InspectionResult(
        verdict=verdict,
        status=status,
        nonconformities=tuple(nature),
        accepted_quantity=fact,
        backorder_quantity=max(ordered - document, Decimal("0")),
        missing_documents=any(item in DOCUMENT_NONCONFORMITIES for item in nature),
        nonconformity_act_required=bool(act_items),
        complaint_required=complaint,
    )


def _number(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def refuse_backorder_remainder(
    *,
    legacy_id: str,
    unit: str,
    backorder_quantity: Decimal,
    backorder_legacy_ids: Iterable[str],
    document_cap: Decimal,
    fact_cap: Decimal,
    document_quantity: Decimal,
    fact_quantity: Decimal,
) -> None:
    """The remainder of a недовоз belongs to its `-Д` line: once the backorder is ordered, a re-inspection
    of the delivery may not take more than the УПД it was computed from (the count may still be corrected up
    to that УПД or the surplus already recorded) — otherwise the same remainder would be accepted twice."""
    if document_quantity <= document_cap and fact_quantity <= fact_cap:
        return
    target = ", ".join(backorder_legacy_ids) or f"{re.sub(r'(?:-В\d+)+$', '', legacy_id)}-Д"
    raise ReceivingValidationError(
        f"{legacy_id}: остаток {_number(backorder_quantity)} {unit} уже заказан дозавозом «{target}» — "
        f"довоз принимается по строке дозавоза. По этой строке — не больше {_number(document_cap)} {unit} "
        f"по УПД и {_number(fact_cap)} {unit} по факту"
    )


# The receiver's queue: open deliveries first, then accepted but not yet on stock, then the rest; «Архив ВК»
# (history of a re-done inspection) last and only on request.
QUEUE_IN_TRANSIT, QUEUE_WAITING, QUEUE_QUARANTINE, QUEUE_TO_STOCK, QUEUE_DONE, QUEUE_ARCHIVE = range(6)
QUEUE_PRESETS: dict[str, frozenset[int]] = {
    "open": frozenset({QUEUE_IN_TRANSIT, QUEUE_WAITING, QUEUE_QUARANTINE}),
    "to_stock": frozenset({QUEUE_TO_STOCK}),
    "done": frozenset({QUEUE_DONE}),
    "archive": frozenset({QUEUE_ARCHIVE}),
    "all": frozenset(range(6)),
}
QUEUE_DEFAULT = frozenset(range(QUEUE_ARCHIVE))


def queue_rank(status: ReceivingStatus, warehouse_event_emitted: bool) -> int:
    if status is ReceivingStatus.IN_TRANSIT:
        return QUEUE_IN_TRANSIT
    if status is ReceivingStatus.WAITING:
        return QUEUE_WAITING
    if status is ReceivingStatus.QUARANTINE:
        return QUEUE_QUARANTINE
    if status is ReceivingStatus.ACCEPTED and not warehouse_event_emitted:
        return QUEUE_TO_STOCK
    if status is ReceivingStatus.VK_ARCHIVE:
        return QUEUE_ARCHIVE
    return QUEUE_DONE


def _base_designation(value: str) -> str:
    return re.sub(r"\.\d{3,}$", "", str(value or "").strip())


_INTERNAL_PROJECT_GOAL = re.compile(r"ВП-\d{3,}")


def grn_prefix_for(*, purpose: str, link: str, goal: str) -> str | None:
    if re.search(r"давальческ", str(link or ""), re.IGNORECASE):
        return "CSM"
    goal_text = str(goal or "").strip()
    # An internal project «ВП-001» does not depend on the wording of its purpose: НИОКР (RND) → R&D, any other → FRX.
    if _INTERNAL_PROJECT_GOAL.fullmatch(goal_text.upper()):
        purpose_key = _base_designation(str(purpose or "").strip()).casefold()
        return "R&D" if purpose_key in {"r&d", "rnd"} or purpose_key.startswith("ниокр") else "FRX"
    source = goal_text or str(purpose or "").strip()
    normalized = _base_designation(source).casefold()
    if normalized in {"r&d", "rnd"}:
        return "R&D"
    if normalized == "frx":
        return "FRX"
    if re.fullmatch(r"\d+[кk](?:\.\d+)*", normalized):
        return "PPM"
    if normalized in {"давальческое", "csm"}:
        return "CSM"
    return None


def next_grn(prefix: str, year: int, existing: Iterable[str]) -> str:
    normalized_prefix = str(prefix or "").strip().upper()
    if normalized_prefix not in {"R&D", "FRX", "PPM", "CSM", "OLD"}:
        raise ReceivingValidationError("Неизвестный идентификатор ГРН")
    head = f"{normalized_prefix}.{int(year) % 100:02d}."
    pattern = re.compile(rf"^{re.escape(head)}(\d{{7}})$")
    maximum = 0
    for value in existing:
        match = pattern.fullmatch(str(value or "").strip())
        if match:
            maximum = max(maximum, int(match.group(1)))
    return f"{head}{maximum + 1:07d}"


@dataclass(frozen=True)
class ReceivingLine:
    id: UUID
    source_event_id: UUID
    source_idempotency_key: str
    procurement_line_id: UUID
    legacy_id: str
    status: ReceivingStatus
    version: int
    created_at: datetime
    warehouse: str
    article: str
    series: str
    name: str
    item_type: str
    purpose: str
    goal: str
    source_link: str
    supplier: str
    ordered_quantity: Decimal
    unit: str
    quality_document_required: bool
    document_quantity: Decimal | None = None
    fact_quantity: Decimal | None = None
    shipping_document_url: str | None = None
    quality_document_url: str | None = None
    expiry_date: date | None = None
    unit_mass_g: Decimal | None = None
    disposition: str | None = None
    verdict: str | None = None
    nonconformities: tuple[str, ...] = ()
    accepted_quantity: Decimal | None = None
    backorder_quantity: Decimal = Decimal("0")
    grn: str | None = None
    package: str = ""
    storage_location: str | None = None
    inspector: Actor | None = None
    warehouse_event_emitted: bool = False
    inspected_on: date | None = None
    revision_of_id: UUID | None = None
    revision_root_id: UUID | None = None
    # ОПИСАНИЕ НЕСООТВ.: free text of the nonconformity; a supplier return appends its basis here.
    nonconformity_description: str = ""
    # How many supplier returns the line went through; a «wait» return re-opens the line for the
    # replacement, whose own hand-over to the warehouse needs a fresh outbox key.
    supplier_returns: int = 0
    # Context of the order (Лист закупки → Лист ожидания): № ЗАКАЗА and ОЖИД. ДАТА for the queue.
    order_number: str = ""
    expected_arrival: date | None = None
    # № УПД/М-15, ДАТА УПД/М-15, НАИМЕНОВАНИЕ (УПД/М-15), ПРИМЕЧАНИЕ ВК, ДАТА ИЗГОТОВЛЕНИЯ.
    document_number: str = ""
    document_date: date | None = None
    document_name: str = ""
    inspection_note: str = ""
    manufacture_date: date | None = None
    # A batch (RC-13) or package (RC-8) line `-ПN` split off this line; the order stays one procurement line.
    split_of_id: UUID | None = None
    # Реквизиты изготовителя и документа качества для акта ВК (Config.gs:269-270, 517-525).
    manufacturer: str = ""
    country: str = ""
    quality_document_type: str = ""
    quality_document_number: str = ""
    standard: str = ""
    quality_document_date: date | None = None
    # RC-9: № и дата актов строки (№ АКТ ВК/АКТ ВК ДАТА, № АКТ НС, № АКТ РЕКЛ., № РАЗРЕШ. ИСПОЛЬЗ.).
    vk_act_number: str = ""
    vk_act_date: date | None = None
    nc_act_number: str = ""
    nc_act_date: date | None = None
    complaint_act_number: str = ""
    complaint_act_date: date | None = None
    use_permit_number: str = ""
    use_permit_date: date | None = None

    @property
    def shipping_document_pending(self) -> bool:
        """Inspected without УПД (no file, no №) — non-blocking note until «Внести УПД» adds it."""
        return bool(
            self.verdict is not None
            and self.status not in (ReceivingStatus.VK_ARCHIVE, ReceivingStatus.RETURN)
            and not str(self.shipping_document_url or "").strip()
            and not str(self.document_number or "").strip()
        )

    def act(self, kind: str) -> tuple[str, date | None]:
        return {
            "incoming": (self.vk_act_number, self.vk_act_date),
            "nonconformity": (self.nc_act_number, self.nc_act_date),
            "complaint": (self.complaint_act_number, self.complaint_act_date),
            "usepermit": (self.use_permit_number, self.use_permit_date),
        }[kind]

    @property
    def lineage_root_id(self) -> UUID:
        return self.revision_root_id or self.id

    @property
    def base_legacy_id(self) -> str:
        return re.sub(r"(?:-В\d+)+$", "", self.legacy_id)

    def inspect(
        self,
        data: InspectionInput,
        actor: Actor,
        *,
        existing_grns: Iterable[str],
        manual_grn_prefix: str | None = None,
        existing_act_numbers: Iterable[str] = (),
    ) -> ReceivingLine:
        if not str(actor.name or "").strip():
            raise ReceivingValidationError("Не определён приёмщик")
        if self.warehouse_event_emitted:
            raise ReceivingValidationError(
                "Переданная на склад поставка закрыта для повторного контроля"
            )
        if self.status is ReceivingStatus.RETURN:
            raise ReceivingValidationError(
                "Возвращённая поставка закрыта для повторного контроля"
            )
        if self.status is ReceivingStatus.VK_ARCHIVE:
            raise ReceivingValidationError(
                "Строка в «Архив ВК» — контроль проводится по её ревизии"
            )
        try:
            shipping_document_url = normalize_document_url(data.shipping_document_url)
            quality_document_url = normalize_document_url(data.quality_document_url)
        except ValueError as error:
            raise ReceivingValidationError(str(error)) from error
        result = inspect_delivery(data)
        try:
            unit_mass = mass_per_unit(data.mass_1, data.mass_10, data.mass_100)
        except ValueError as error:
            raise ReceivingValidationError(str(error)) from error
        grn = self.grn
        if grn is None:
            prefix = grn_prefix_for(purpose=self.purpose, link=self.source_link, goal=self.goal)
            if prefix is None:
                candidate = str(manual_grn_prefix or "").strip().upper()
                if candidate not in {"CSM", "PPM"}:
                    raise ReceivingValidationError(
                        "Назначение не распознано — выберите идентификатор ГРН CSM или PPM"
                    )
                prefix = candidate
            grn = next_grn(prefix, data.inspection_date.year, existing_grns)
        # RC-9 (Receiving.gs:704-761): акт ВК — при первом контроле; АН и РК — когда вердикт их требует и
        # их ещё нет (догенерация); РИ — вывод из «Ожидания»/«Карантина» в «Принято» после акта ВК.
        # Дата актов ВК/АН/РК — дата УПД у исторической приёмки (dateFromCol + useDocDate), иначе сегодня.
        taken = [str(value) for value in existing_act_numbers]
        act_day = data.verdict_date or data.inspection_date

        def issue(kind: str, current: str, current_day: date | None, day: date) -> tuple[str, date | None]:
            if current:
                return current, current_day
            number = next_act_number(kind, day, taken)
            taken.append(number)
            return number, day

        vk = issue("incoming", self.vk_act_number, self.vk_act_date, act_day)
        nc = (
            issue("nonconformity", self.nc_act_number, self.nc_act_date, act_day)
            if result.nonconformity_act_required
            else (self.nc_act_number, self.nc_act_date)
        )
        complaint = (
            issue("complaint", self.complaint_act_number, self.complaint_act_date, act_day)
            if result.complaint_required
            else (self.complaint_act_number, self.complaint_act_date)
        )
        released = (
            bool(self.vk_act_number)
            and self.status in {ReceivingStatus.WAITING, ReceivingStatus.QUARANTINE}
            and result.status is ReceivingStatus.ACCEPTED
        )
        permit = (
            issue("usepermit", self.use_permit_number, self.use_permit_date, data.inspection_date)
            if released
            else (self.use_permit_number, self.use_permit_date)
        )
        return replace(
            self,
            status=result.status,
            version=self.version + 1,
            document_quantity=data.document_quantity,
            fact_quantity=data.fact_quantity,
            shipping_document_url=shipping_document_url,
            quality_document_url=quality_document_url,
            series=str(data.series or "").strip() or self.series,
            expiry_date=data.expiry_date,
            # Nothing usable typed → the mass weighed before stays (Receiving.gs:441 `if (per.ok)`).
            unit_mass_g=self.unit_mass_g if unit_mass is None else unit_mass,
            disposition=str(data.disposition or "").strip() or None,
            verdict=result.verdict,
            nonconformities=result.nonconformities,
            accepted_quantity=result.accepted_quantity,
            backorder_quantity=result.backorder_quantity,
            grn=grn,
            inspector=actor,
            inspected_on=data.inspection_date,
            document_number=str(data.document_number or "").strip() or self.document_number,
            document_date=data.document_date or self.document_date,
            document_name=str(data.document_name or "").strip() or self.document_name,
            inspection_note=str(data.note or "").strip(),
            manufacture_date=data.manufacture_date,
            manufacturer=str(data.manufacturer or "").strip() or self.manufacturer,
            country=str(data.country or "").strip() or self.country,
            quality_document_type=str(data.quality_document_type or "").strip() or self.quality_document_type,
            quality_document_number=str(data.quality_document_number or "").strip() or self.quality_document_number,
            standard=str(data.standard or "").strip() or self.standard,
            quality_document_date=data.quality_document_date or self.quality_document_date,
            nonconformity_description=(
                str(data.nonconformity_description or "").strip() or self.nonconformity_description
            ),
            vk_act_number=vk[0],
            vk_act_date=vk[1],
            nc_act_number=nc[0],
            nc_act_date=nc[1],
            complaint_act_number=complaint[0],
            complaint_act_date=complaint[1],
            use_permit_number=permit[0],
            use_permit_date=permit[1],
        )

    def waiting_overdue(self, today: date) -> bool:
        # OwnerTick.gs waitingToQuarantineSweep_: отсчёт от даты акта ВК; без даты таймер не идёт.
        return bool(
            self.status is ReceivingStatus.WAITING
            and self.inspected_on is not None
            and not self.warehouse_event_emitted
            and workdays_between(self.inspected_on, today) >= QUARANTINE_WORKDAYS
        )

    def quarantine_overdue(self, today: date) -> ReceivingLine:
        if not self.waiting_overdue(today):
            raise ReceivingValidationError(
                "В «Карантин» по таймеру уходит только «Ожидание» старше "
                f"{QUARANTINE_WORKDAYS} рабочих дней"
            )
        return replace(self, status=ReceivingStatus.QUARANTINE, version=self.version + 1)

    def follow_expected_arrival(self, expected_arrival: date | None) -> ReceivingLine:
        """ОЖИД. ДАТА is the buyer's (OwnerTick.gs syncExpectedDateToWaitlist_): a delivery still waited for
        follows it; an accepted, returned or archived one keeps the date it had."""
        if self.status not in EXPECTED_ARRIVAL_FOLLOWS or self.expected_arrival == expected_arrival:
            return self
        return replace(self, expected_arrival=expected_arrival, version=self.version + 1)

    def attach_shipping_document(
        self, *, url: str | None, number: str, document_date: date | None
    ) -> ReceivingLine:
        """Массовое внесение УПД: только реквизиты документа, статус, ВК и склад не трогаем.
        Принятая (и уже переданная на склад) позиция тоже: УПД по ЭДО приходит позже приёмки.
        Пустое значение не затирает уже внесённое."""
        if self.status not in ATTACH_DOCUMENT_STATUSES:
            raise ReceivingValidationError(
                f"Позиция {self.legacy_id}: УПД вносится для «В пути», «Ожидание», «Карантин» и «Принято», "
                f"сейчас «{self.status.value}»"
            )
        try:
            normalized_url = normalize_document_url(url)
        except ValueError as error:
            raise ReceivingValidationError(str(error)) from error
        clean_number = str(number or "").strip()
        if not (normalized_url or clean_number or document_date):
            raise ReceivingValidationError("Укажите файл, № или дату УПД")
        updated = replace(
            self,
            shipping_document_url=normalized_url or self.shipping_document_url,
            document_number=clean_number or self.document_number,
            document_date=document_date or self.document_date,
        )
        if updated == self:
            return self
        return replace(updated, version=self.version + 1)

    def place(self, location: str) -> ReceivingLine:
        if self.warehouse_event_emitted:
            raise ReceivingValidationError(
                "Переданная на склад поставка закрыта для изменения размещения"
            )
        if self.status is not ReceivingStatus.ACCEPTED:
            raise ReceivingValidationError("Разместить на складе можно только принятую позицию")
        try:
            normalized = existing_storage_location(location)
        except ValueError as error:
            raise ReceivingValidationError(str(error)) from error
        if normalized == self.storage_location:
            return self
        return replace(self, storage_location=normalized, version=self.version + 1)

    def open_vk_revision(
        self, *, existing_legacy_ids: Iterable[str], now: datetime
    ) -> tuple[ReceivingLine, ReceivingLine]:
        """Receiving.gs:474-551 «Провести новый ВК»: the inspected line goes to «Архив ВК» as history
        of its acts; a copy `-ВN` keeps the order context and the delivery's УПД and starts over
        «В пути» with empty acceptance fields."""
        if self.status is ReceivingStatus.VK_ARCHIVE:
            raise ReceivingValidationError("Строка уже в архиве ВК")
        if self.verdict is None:
            raise ReceivingValidationError(
                "По строке ещё не проведён ВК — новый ВК не нужен, проводите обычную приёмку"
            )
        if self.warehouse_event_emitted:
            raise ReceivingValidationError(
                "Позиция уже передана на склад — новый ВК по ней не проводится"
            )
        base = self.base_legacy_id
        taken = {str(value or "").strip() for value in existing_legacy_ids}
        number = 2
        while f"{base}-В{number}" in taken:
            number += 1
        legacy_id = f"{base}-В{number}"
        if len(legacy_id) > 32:
            raise ReceivingValidationError("System ID ревизии ВК длиннее 32 символов")
        archived = replace(self, status=ReceivingStatus.VK_ARCHIVE, version=self.version + 1)
        revision = ReceivingLine(
            id=uuid4(),
            source_event_id=uuid4(),
            source_idempotency_key=f"receiving:{self.id}:vk-revision",
            procurement_line_id=self.procurement_line_id,
            legacy_id=legacy_id,
            status=ReceivingStatus.IN_TRANSIT,
            version=1,
            created_at=now,
            warehouse=self.warehouse,
            article=self.article,
            series=self.series,
            name=self.name,
            item_type=self.item_type,
            purpose=self.purpose,
            goal=self.goal,
            source_link=self.source_link,
            supplier=self.supplier,
            ordered_quantity=self.ordered_quantity,
            unit=self.unit,
            quality_document_required=self.quality_document_required,
            shipping_document_url=self.shipping_document_url,
            revision_of_id=self.id,
            revision_root_id=self.lineage_root_id,
        )
        return archived, revision

    def return_to_supplier(
        self,
        *,
        reason: str,
        mode: SupplierReturnMode,
        claim: str = "",
        comment: str = "",
    ) -> ReceivingLine:
        """SupplierReturn.gs supplierReturnApply_: an accepted line goes back to the supplier.

        РЕШЕНИЕ becomes «Возврат поставщику», the basis is appended to ОПИСАНИЕ НЕСООТВ.; «close» ends the
        order («Возврат»), «wait» puts the line back «В пути» for the replacement and lets its next
        acceptance reach the warehouse again (supplierReturnFreePushed_)."""
        if self.status is not ReceivingStatus.ACCEPTED:
            raise ReceivingValidationError(
                f"Вернуть поставщику можно только принятую позицию (статус «{self.status.value}»)"
            )
        reason_text = str(reason or "").strip()
        if not reason_text:
            raise ReceivingValidationError("Укажи причину возврата.")
        if reason_text not in SUPPLIER_RETURN_REASONS:
            raise ReceivingValidationError("Неизвестная причина возврата")
        basis = supplier_return_basis(reason_text, claim, comment)
        previous = self.nonconformity_description.strip()
        wait = mode is SupplierReturnMode.WAIT
        return replace(
            self,
            status=ReceivingStatus.IN_TRANSIT if wait else ReceivingStatus.RETURN,
            version=self.version + 1,
            disposition=SUPPLIER_RETURN_DISPOSITION,
            nonconformity_description=f"{previous}; {basis}" if previous else basis,
            supplier_returns=self.supplier_returns + 1,
            warehouse_event_emitted=False if wait else self.warehouse_event_emitted,
        )

    def _split_copy(self, *, legacy_id: str, now: datetime, key: str) -> ReceivingLine:
        if len(legacy_id) > 32:
            raise ReceivingValidationError("System ID части длиннее 32 символов")
        return replace(
            self,
            id=uuid4(),
            source_event_id=uuid4(),
            source_idempotency_key=key,
            legacy_id=legacy_id,
            version=1,
            created_at=now,
            split_of_id=self.id,
            revision_of_id=None,
            revision_root_id=None,
            supplier_returns=0,
        )

    def split_into_batches(
        self, quantities: list[Decimal], *, existing_legacy_ids: Iterable[str], now: datetime
    ) -> list[ReceivingLine]:
        """Receiving.gs applyWaitlistBatchSplits_: поставка пришла несколькими партиями изготовителя.

        This line becomes batch 1 (КОЛ-ВО ЗКП = its quantity); every other batch is a new line `<ID>-ПN`
        with the order context and ЗКП = its quantity, still «В пути» — each then gets its own ВК, ГРН and
        acts. Quantities are those of the batches (УПД, else fact)."""
        if self.status not in {ReceivingStatus.IN_TRANSIT, ReceivingStatus.WAITING, ReceivingStatus.QUARANTINE}:
            raise ReceivingValidationError("Разбить на партии можно только поставку до приёмки")
        if self.warehouse_event_emitted or self.grn:
            raise ReceivingValidationError("У строки уже есть ГРН — разбивка на партии только до первого ВК")
        if len(quantities) < 2:
            raise ReceivingValidationError("Для разбивки нужно минимум 2 партии")
        amounts = [_positive(Decimal(value), f"Партия {index}: кол-во") for index, value in enumerate(quantities, 1)]
        taken = {str(value or "").strip() for value in existing_legacy_ids}
        first = replace(self, ordered_quantity=amounts[0], version=self.version + 1)
        lines = [first]
        number = 2
        for index, amount in enumerate(amounts[1:], start=2):
            while f"{self.legacy_id}-П{number}" in taken:
                number += 1
            legacy_id = f"{self.legacy_id}-П{number}"
            taken.add(legacy_id)
            copy = self._split_copy(legacy_id=legacy_id, now=now, key=f"receiving:{self.id}:batch:{legacy_id}")
            lines.append(replace(copy, ordered_quantity=amount, status=ReceivingStatus.IN_TRANSIT))
        return lines

    def unpack(
        self, quantities: list[Decimal], *, existing_legacy_ids: Iterable[str], now: datetime
    ) -> list[ReceivingLine]:
        """Receiving.gs applyUnpackSplits_ «Разукомплектовка (пакеты)»: a received position is split into
        packages sharing its ГРН before it is placed. This line becomes package 1; packages 2..N are full
        copies `<ID>-ПN` (acts, ГРН, status kept) with their own КОЛ-ВО ФАКТ and ПАКЕТ = N; ЗКП and УПД stay on
        package 1 only. Each package is placed and goes to stock on its own."""
        if self.status is not ReceivingStatus.ACCEPTED:
            raise ReceivingValidationError("Разукомплектовка — только для строк «Принято» (после ВК)")
        if not self.grn:
            raise ReceivingValidationError("У строки нет ГРН — сначала проведи входной контроль")
        if self.package:
            raise ReceivingValidationError("Строка уже является пакетом разукомплектовки")
        if self.storage_location or self.warehouse_event_emitted:
            raise ReceivingValidationError(
                "Строка уже разложена на склад — разукомплектовка возможна только до раскладки"
            )
        total = self.fact_quantity
        if total is None or total <= 0:
            raise ReceivingValidationError("У строки нет корректного КОЛ-ВО ФАКТ")
        if len(quantities) < 2:
            raise ReceivingValidationError("Нужно минимум 2 части")
        amounts = [_positive(Decimal(value), f"Часть {index}: кол-во") for index, value in enumerate(quantities, 1)]
        if abs(sum(amounts) - total) > Decimal("0.001"):
            raise ReceivingValidationError(
                f"Сумма частей {format(sum(amounts).normalize(), 'f')} ≠ исходному {format(total.normalize(), 'f')}"
            )
        taken = {str(value or "").strip() for value in existing_legacy_ids}
        first = replace(
            self, fact_quantity=amounts[0], accepted_quantity=amounts[0], package="1", version=self.version + 1
        )
        lines = [first]
        number = 2
        for index, amount in enumerate(amounts[1:], start=2):
            while f"{self.legacy_id}-П{number}" in taken:
                number += 1
            legacy_id = f"{self.legacy_id}-П{number}"
            taken.add(legacy_id)
            copy = self._split_copy(legacy_id=legacy_id, now=now, key=f"receiving:{self.id}:package:{index}")
            lines.append(replace(
                copy,
                fact_quantity=amount,
                accepted_quantity=amount,
                document_quantity=None,
                ordered_quantity=amount,
                backorder_quantity=Decimal("0"),
                package=str(index),
            ))
        return lines

    @property
    def warehouse_event_key(self) -> str:
        """Outbox key of the hand-over to the warehouse; each replacement after a «wait» return has its own."""
        base = f"receiving:{self.id}:warehouse"
        return f"{base}:r{self.supplier_returns}" if self.supplier_returns else base

    @property
    def ready_for_warehouse(self) -> bool:
        return bool(
            self.status is ReceivingStatus.ACCEPTED
            and self.grn
            and self.storage_location
            and self.accepted_quantity
            and not self.warehouse_event_emitted
        )

    def mark_warehouse_event_emitted(self) -> ReceivingLine:
        if not self.ready_for_warehouse:
            raise ReceivingValidationError("Позиция не готова к передаче на склад")
        return replace(self, warehouse_event_emitted=True, version=self.version + 1)


def create_line_from_expected(message: OutboxMessage) -> ReceivingLine:
    if message.topic != "receiving.expected":
        raise ReceivingValidationError(f"Unexpected topic: {message.topic}")
    payload = message.payload
    try:
        warehouse = str(payload["warehouse"]).strip()
        if warehouse not in {"ЦАР", "ЖУК"}:
            raise ReceivingValidationError("Неизвестный склад приёмки")
        quantity = _positive(Decimal(str(payload["ordered_quantity"])), "Заказанное количество")
        return ReceivingLine(
            id=uuid4(),
            source_event_id=message.id,
            source_idempotency_key=message.idempotency_key,
            procurement_line_id=UUID(str(payload["procurement_line_id"])),
            legacy_id=str(payload["legacy_id"]).strip(),
            status=ReceivingStatus.IN_TRANSIT,
            version=1,
            created_at=message.created_at,
            warehouse=warehouse,
            article=str(payload.get("article") or payload["name"]).strip(),
            series=str(payload.get("series") or "").strip(),
            name=str(payload["name"]).strip(),
            item_type=str(payload["item_type"]).strip(),
            purpose=str(payload["purpose"]).strip(),
            goal=str(payload.get("goal") or "").strip(),
            source_link=str(payload.get("source_link") or "").strip(),
            supplier=str(payload["supplier"]).strip(),
            ordered_quantity=quantity,
            unit=str(payload["unit"]).strip(),
            quality_document_required=bool(payload.get("quality_document_required", False)),
            order_number=str(payload.get("order_number") or "").strip(),
            expected_arrival=(
                date.fromisoformat(str(payload["expected_arrival"])) if payload.get("expected_arrival") else None
            ),
            manufacturer=str(payload.get("manufacturer") or "").strip(),
            country=str(payload.get("country") or "").strip(),
            quality_document_type=str(payload.get("quality_document_type") or "").strip(),
            quality_document_number=str(payload.get("quality_document_number") or "").strip(),
            standard=str(payload.get("standard") or "").strip(),
            # «Наименование по УПД» starts as the invoice name from procurement; the receiver edits it.
            document_name=str(payload.get("invoice_item_name") or "").strip(),
            quality_document_date=(
                date.fromisoformat(str(payload["quality_document_date"]))
                if payload.get("quality_document_date")
                else None
            ),
        )
    except (KeyError, ValueError, TypeError, DecimalException) as error:
        if isinstance(error, ReceivingValidationError):
            raise
        raise ReceivingValidationError(f"Invalid receiving.expected payload: {error}") from error
