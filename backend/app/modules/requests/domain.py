"""Pure request-domain rules ported from the SUPPLYMAP intake flow."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from enum import Enum
from typing import Iterable, Sequence
from uuid import UUID, uuid4

from app.shared.identity import Actor


TASK_TYPE = "Закупка"
DEFAULT_CATEGORY = "Общепром"
STATIC_PURPOSES = (
    "RND",
    "FRX",
    "Хоз деятельность",
    "Обеспечение производства",
    "Опытный образец (НИОКР)",
    "Ремонт и обслуживание",
    "Склад",
    "Расходники",
)
DELIVERY_POINTS = (
    "г. Москва, ул. 6-я Радиальная, д. 17",
    "г. Раменское, ул. 100-й Свирской Дивизии, д. 52",
)
UNITS = ("шт", "кг", "м", "л", "уп", "компл")
CATEGORIES = ("Общепром", "ИП", "ОП")
REQUIRED_FIELDS = (
    "purpose",
    "delivery_point",
    "name",
    "item_type",
    "quantity",
    "unit",
    "quality_document",
    "procurement_deadline",
    "planned_date",
)


class ValidationError(ValueError):
    """A request violates a business invariant."""


class RequestStatus(str, Enum):
    NEW = "Новый"
    IN_WORK = "В работе"
    APPROVAL = "Согласование"
    IN_TRANSIT = "В пути"
    CLOSED = "Закрыто"
    CANCELLED = "Отменено"


@dataclass(frozen=True)
class RequestDraft:
    purpose: str
    delivery_point: str
    name: str
    item_type: str
    quantity: Decimal
    unit: str
    quality_document: str
    procurement_deadline: date
    planned_date: date
    project_reference: str | None = None
    category: str | None = None
    design_article: str | None = None
    link: str | None = None
    price: Decimal | None = None
    price_with_vat: Decimal | None = None
    comment: str | None = None


@dataclass(frozen=True)
class Request:
    id: UUID
    legacy_id: str
    designation: str
    status: RequestStatus
    version: int
    created_at: datetime
    actor: Actor
    task_type: str
    priority: str
    line_sum: Decimal | None
    purpose: str
    project_reference: str | None
    delivery_point: str
    name: str
    item_type: str
    category: str
    design_article: str | None
    quantity: Decimal
    unit: str
    link: str | None
    quality_document: str
    procurement_deadline: date
    planned_date: date
    price: Decimal | None
    price_with_vat: Decimal | None
    comment: str | None
    procurement_actor: Actor | None = None
    creation_key: str = ""
    creation_index: int = 0
    creation_fingerprint: str = ""

    def start_procurement(self, actor: Actor) -> Request:
        if self.status is not RequestStatus.NEW:
            raise ValidationError("В работу можно взять только новую заявку")
        _validate_actor(actor)
        return replace(
            self,
            status=RequestStatus.IN_WORK,
            version=self.version + 1,
            procurement_actor=actor,
        )


@dataclass(frozen=True)
class ProcurementRequestedSnapshot:
    source_request_id: UUID
    legacy_id: str
    idempotency_key: str
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


def _text(value: str | None) -> str:
    return str(value or "").strip()


def _validate_actor(actor: Actor) -> None:
    if not _text(actor.name):
        raise ValidationError("Не определён сотрудник")


def apply_quality_document_rule(category: str | None, value: str | None) -> str:
    normalized_category = _text(category) or DEFAULT_CATEGORY
    current = _text(value)
    if normalized_category.casefold() == "оп":
        return "Да"
    if normalized_category.casefold() == "ип" and not current:
        return "Да"
    return current


def validate_draft(draft: RequestDraft) -> RequestDraft:
    purpose_by_key = {item.casefold(): item for item in STATIC_PURPOSES}
    purpose = purpose_by_key.get(_text(draft.purpose).casefold())
    if purpose is None:
        raise ValidationError("Недопустимое НАЗНАЧЕНИЕ: выберите значение из справочника")

    delivery_point_by_key = {item.casefold(): item for item in DELIVERY_POINTS}
    delivery_point = delivery_point_by_key.get(_text(draft.delivery_point).casefold())
    if delivery_point is None:
        raise ValidationError("Недопустимая ТОЧКА ОТГРУЗКИ: выберите значение из справочника")

    category_by_key = {item.casefold(): item for item in CATEGORIES}
    category = category_by_key.get((_text(draft.category) or DEFAULT_CATEGORY).casefold())
    if category is None:
        raise ValidationError("Недопустимая КАТЕГОРИЯ")
    unit_by_key = {item.casefold(): item for item in UNITS}
    unit = unit_by_key.get(_text(draft.unit).casefold())
    if unit is None:
        raise ValidationError("Недопустимая ЕД.ИЗМ: выерите значение из справочника")
    quality_document = apply_quality_document_rule(category, draft.quality_document)
    normalized = replace(
        draft,
        purpose=purpose,
        project_reference=_text(draft.project_reference) or None,
        delivery_point=delivery_point,
        name=_text(draft.name),
        item_type=_text(draft.item_type),
        unit=unit,
        quality_document=quality_document,
        category=category,
        design_article=_text(draft.design_article) or None,
        link=_text(draft.link) or None,
        comment=_text(draft.comment) or None,
    )

    missing = [
        label
        for label, value in (
            ("ТОЧКА ОТГРУЗКИ", normalized.delivery_point),
            ("НАИМЕНОВАНИЕ", normalized.name),
            ("ТИП", normalized.item_type),
            ("ЕД.ИЗМ", normalized.unit),
            ("ДОК. КАЧЕСТВА", normalized.quality_document),
        )
        if not value
    ]
    if missing:
        raise ValidationError("Не заполнены обязательные поля: " + ", ".join(missing))
    if normalized.quantity <= 0:
        raise ValidationError("КОЛ-ВО должно быть больше нуля")
    if normalized.quality_document not in {"Да", "Нет"}:
        raise ValidationError("ДОК. КАЧЕСТВА должен быть «Да» или «Нет»")
    return normalized


def derive_priority(deadline: date, today: date) -> str:
    days = (deadline - today).days
    if days <= 3:
        return "Очень срочно"
    if days <= 7:
        return "Срочно"
    if days <= 14:
        return "Не срочно"
    return "В свободное время"


def calculate_line_sum(price_with_vat: Decimal | None, quantity: Decimal) -> Decimal | None:
    if price_with_vat is None:
        return None
    try:
        return (price_with_vat * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except DecimalException as error:
        raise ValidationError("Не удалось рассчитать сумму строки") from error


def next_legacy_id(existing_ids: Iterable[str], day: date, prefix: str = "SM") -> str:
    stem = f"{prefix}-{day:%Y%m%d}-"
    highest = 0
    for existing in existing_ids:
        value = str(existing)
        if not value.startswith(stem):
            continue
        suffix = value[len(stem) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{stem}{highest + 1:02d}"


def next_designation(base: str, existing_designations: Iterable[str]) -> str:
    normalized = _text(base)
    if not normalized:
        return ""
    pattern = re.compile(rf"^{re.escape(normalized)}\.(\d+)$")
    highest = 0
    for designation in existing_designations:
        match = pattern.match(_text(designation))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{normalized}.{highest + 1:03d}"


def create_request_batch(
    drafts: Sequence[RequestDraft],
    *,
    actor: Actor,
    now: datetime,
    existing_legacy_ids: Sequence[str],
    existing_designations: Sequence[str],
    creation_key: str = "",
    creation_fingerprint: str = "",
) -> list[Request]:
    if not drafts:
        raise ValidationError("Нет позиций")
    _validate_actor(actor)

    normalized_drafts = [validate_draft(item) for item in drafts]
    allocated_ids = list(existing_legacy_ids)
    allocated_designations = list(existing_designations)
    designation_by_purpose: dict[str, str] = {}
    requests: list[Request] = []

    for creation_index, item in enumerate(normalized_drafts):
        legacy_id = next_legacy_id(allocated_ids, now.date())
        allocated_ids.append(legacy_id)
        if item.purpose not in designation_by_purpose:
            designation = next_designation(item.purpose, allocated_designations)
            designation_by_purpose[item.purpose] = designation
            allocated_designations.append(designation)

        requests.append(
            Request(
                id=uuid4(),
                legacy_id=legacy_id,
                designation=designation_by_purpose[item.purpose],
                status=RequestStatus.NEW,
                version=1,
                created_at=now,
                actor=actor,
                task_type=TASK_TYPE,
                priority=derive_priority(item.procurement_deadline, now.date()),
                line_sum=calculate_line_sum(item.price_with_vat, item.quantity),
                purpose=item.purpose,
                project_reference=item.project_reference,
                delivery_point=item.delivery_point,
                name=item.name,
                item_type=item.item_type,
                category=item.category or DEFAULT_CATEGORY,
                design_article=item.design_article,
                quantity=item.quantity,
                unit=item.unit,
                link=item.link,
                quality_document=item.quality_document,
                procurement_deadline=item.procurement_deadline,
                planned_date=item.planned_date,
                price=item.price,
                price_with_vat=item.price_with_vat,
                comment=item.comment,
                creation_key=creation_key,
                creation_index=creation_index,
                creation_fingerprint=creation_fingerprint,
            )
        )
    return requests


def build_procurement_requested_snapshot(
    request: Request,
    *,
    today: date,
) -> ProcurementRequestedSnapshot:
    if request.status is not RequestStatus.IN_WORK:
        raise ValidationError('Перенос возможен только после перехода заявки в статус «В работе»')
    return ProcurementRequestedSnapshot(
        source_request_id=request.id,
        legacy_id=request.legacy_id,
        idempotency_key=f"request:{request.id}:procurement:v{request.version}",
        request_date=request.created_at.date(),
        initiator=request.actor.name,
        department=request.actor.department,
        task_type=request.task_type,
        designation=request.designation,
        project_reference=request.project_reference,
        priority=derive_priority(request.procurement_deadline, today),
        delivery_point=request.delivery_point,
        planned_date=request.planned_date,
        link=request.link,
        item_type=request.item_type,
        design_article=request.design_article,
        category=request.category,
        calculated_name=request.name,
        calculated_quantity=request.quantity,
        calculated_unit=request.unit,
        quality_document=request.quality_document,
        calculated_price=request.price,
        calculated_price_with_vat=request.price_with_vat,
        calculated_sum=request.line_sum,
    )
