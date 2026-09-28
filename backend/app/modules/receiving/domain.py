"""Pure receiving and incoming-control rules ported from SUPPLYMAP."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from datetime import datetime
from decimal import Decimal, DecimalException
from enum import Enum
from typing import Iterable
from uuid import UUID, uuid4

from app.shared.document_url import normalize_document_url
from app.shared.events import OutboxMessage
from app.shared.identity import Actor
from app.shared.unit_mass import mass_per_unit
from app.shared.warehouse_location import normalize_storage_location


class ReceivingValidationError(ValueError):
    """A delivery cannot be inspected without trustworthy source facts."""


class ReceivingStatus(str, Enum):
    IN_TRANSIT = "В пути"
    WAITING = "Ожидание"
    ACCEPTED = "Принято"
    QUARANTINE = "Карантин"
    RETURN = "Возврат"


MISSING_SHIPPING_DOCUMENT = "Отсутствие / несоответствие УПД/М-15"
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


@dataclass(frozen=True)
class InspectionInput:
    fact_quantity: Decimal
    document_quantity: Decimal
    ordered_quantity: Decimal
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
    if not data.has_shipping_document:
        _append_unique(nature, MISSING_SHIPPING_DOCUMENT)
    if data.quality_document_required and not data.quality_document_present:
        _append_unique(nature, MISSING_QUALITY_DOCUMENT)
    if data.expiry_date is not None and data.expiry_date < data.inspection_date:
        _append_unique(nature, EXPIRED)
    if fact < document:
        _append_unique(nature, SHORT_FACT)
    if document < ordered:
        _append_unique(nature, SHORT_ORDER)
    if fact > document:
        _append_unique(nature, SURPLUS)

    terminal = any(item in TERMINAL_NONCONFORMITIES for item in nature)
    blocking = [item for item in nature if item != SURPLUS]
    if terminal:
        status = ReceivingStatus.RETURN
        verdict = "Не годен"
    elif blocking:
        status = ReceivingStatus.QUARANTINE if str(data.disposition or "").strip() else ReceivingStatus.WAITING
        verdict = "Условно годен"
    else:
        status = ReceivingStatus.ACCEPTED
        verdict = "Годен"

    act_items = [item for item in blocking if item not in DOCUMENT_NONCONFORMITIES]
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


def _base_designation(value: str) -> str:
    return re.sub(r"\.\d{3,}$", "", str(value or "").strip())


def grn_prefix_for(*, purpose: str, link: str, goal: str) -> str | None:
    if re.search(r"давальческ", str(link or ""), re.IGNORECASE):
        return "CSM"
    source = str(goal or "").strip() or str(purpose or "").strip()
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

    def inspect(
        self,
        data: InspectionInput,
        actor: Actor,
        *,
        existing_grns: Iterable[str],
        manual_grn_prefix: str | None = None,
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
        )

    def place(self, location: str) -> ReceivingLine:
        if self.warehouse_event_emitted:
            raise ReceivingValidationError(
                "Переданная на склад поставка закрыта для изменения размещения"
            )
        if self.status is not ReceivingStatus.ACCEPTED:
            raise ReceivingValidationError("Разместить на складе можно только принятую позицию")
        try:
            normalized = normalize_storage_location(location)
        except ValueError as error:
            raise ReceivingValidationError(str(error)) from error
        if normalized == self.storage_location:
            return self
        return replace(self, storage_location=normalized, version=self.version + 1)

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
        )
    except (KeyError, ValueError, TypeError, DecimalException) as error:
        if isinstance(error, ReceivingValidationError):
            raise
        raise ReceivingValidationError(f"Invalid receiving.expected payload: {error}") from error
