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
# ТИП is a combobox (REQUEST_FORM_FIELDS): pick one or type your own.
ITEM_TYPES = ("ЗИП", "Оборудование", "Расходники", "Комплектующие", "Инструмент", "СИЗ", "Услуга")
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


# № ПРОЕКТА (SUPPLYMAP Schema.gs projNoNorm_/projNoValid_): <цифры>К[буква] или <цифры>МФ, необязательные
# серия «.<1–3 буквы>» и подномера «.<цифры≥1>»; старая схема П<гг><мм>-<n>[.<n>]. «К» кириллическая:
# формат проверяется по СЫРОЙ строке, поэтому латинская K отклоняется, чтобы номер ввели верно.
_PROJECT_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")
_PROJECT_NUMBER = re.compile(
    r"(?:[1-9]\d*(?:К[А-Я]?|МФ)(?:\.(?:[А-Я]{1,3}|[1-9]\d*))*\.?|П\d{4}-[1-9]\d*(?:\.[1-9]\d*)?)"
)
_PROJECT_WITHOUT_VERSION = re.compile(r"([1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?)\.?")


def project_reference_valid(value: str | None) -> bool:
    return bool(_PROJECT_NUMBER.fullmatch(re.sub(r"\s+", "", _text(value).upper())))


def normalize_project_reference(value: str | None) -> str:
    """Canonical form: upper case, no spaces, Latin homoglyphs folded, «2000К» → «2000К.1»."""
    normalized = re.sub(r"\s+", "", _text(value).upper()).translate(_PROJECT_HOMOGLYPHS)
    match = _PROJECT_WITHOUT_VERSION.fullmatch(normalized)
    return f"{match.group(1)}.1" if match else normalized


def ensure_projects_registered(
    references: Sequence[str | None], registered: Iterable[str] | None
) -> None:
    """Every project reference must be an open registry project; ``None`` = registry unavailable."""
    if registered is None:
        return
    known = {normalize_project_reference(item) for item in registered}
    for index, reference in enumerate(references, start=1):
        if not _text(reference):
            continue
        project = normalize_project_reference(reference)
        if project not in known:
            raise ValidationError(
                f"Позиция {index}: проект «{project}» не найден в реестре открытых проектов"
            )


def import_project_warning(project: str | None, registered: Iterable[str] | None) -> str | None:
    """`importRequests_` (Imports.gs:72-79): an unknown calc project is a warning, never a refusal."""
    text = _text(project)
    if not text:
        return None
    if not project_reference_valid(text):
        return f"№ ПРОЕКТА «{text}» не в формате 591К.3 — проект записан только в назначение"
    try:
        ensure_projects_registered([text], registered)
    except ValidationError:
        return (
            f"Проект «{normalize_project_reference(text)}» не найден в реестре открытых проектов — "
            "импорт не заблокирован (переходный период)"
        )
    return None


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

    def retransfer(self, actor: Actor) -> Request:
        """Repeated «В закупку» (Pipeline.gs:98-112): a new aggregate version, so a new handoff event.

        Procurement turns it into a new revision line; the earlier line stays as history.
        """
        if self.status is not RequestStatus.IN_WORK:
            raise ValidationError("Повторно перенести можно только заявку «В работе»")
        _validate_actor(actor)
        return replace(self, version=self.version + 1, procurement_actor=actor)


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
    initiator_id: UUID | None = None


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
    # RQ-1 (Schema.gs passportRule_): Общепром — «Нет» по умолчанию, но можно поставить «Да».
    if normalized_category.casefold() == "общепром" and not current:
        return "Нет"
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
        raise ValidationError("Недопустимая ЕД.ИЗМ: выберите значение из справочника")
    quality_document = apply_quality_document_rule(category, draft.quality_document)
    project_reference = _text(draft.project_reference)
    if project_reference and not project_reference_valid(project_reference):
        raise ValidationError(
            f"Недопустимый № ПРОЕКТА «{project_reference}»: нужен формат 591К.3 (кириллическая «К»), "
            "51МФ.1 или П2510-1.2"
        )
    normalized = replace(
        draft,
        purpose=purpose,
        project_reference=normalize_project_reference(project_reference) or None,
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
    designation_base: str | None = None,
) -> list[Request]:
    """``designation_base`` set (import from a calculation) → the whole batch shares ``base.NNN``."""
    if not drafts:
        raise ValidationError("Нет позиций")
    _validate_actor(actor)

    # RQ-2 (Forms.gs:66-67): the failing position is named, «Позиция N: …».
    normalized_drafts = []
    for index, item in enumerate(drafts, start=1):
        try:
            normalized_drafts.append(validate_draft(item))
        except ValidationError as error:
            if str(error).startswith("Позиция "):
                raise
            raise ValidationError(f"Позиция {index}: {error}") from error
    allocated_ids = list(existing_legacy_ids)
    allocated_designations = list(existing_designations)
    designation_by_purpose: dict[str, str] = {}
    requests: list[Request] = []

    for creation_index, item in enumerate(normalized_drafts):
        legacy_id = next_legacy_id(allocated_ids, now.date())
        allocated_ids.append(legacy_id)
        group = _text(designation_base) or item.purpose
        if group not in designation_by_purpose:
            designation = next_designation(group, allocated_designations)
            designation_by_purpose[group] = designation
            allocated_designations.append(designation)

        requests.append(
            Request(
                id=uuid4(),
                legacy_id=legacy_id,
                designation=designation_by_purpose[group],
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
        initiator_id=request.actor.id,
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


# ── Импорт позиций из расчёта (Imports.gs, Schema.gs `parseZpk_`) ──────────────────────────────

ZPK_UNIT_BY_TYPE = {"Кабель": "м", "Провод": "м", "ТУТ": "м", "Шлейф": "м"}


@dataclass(frozen=True)
class CalcItem:
    """One ЗКП row as read from the calculation, before the employee fills the dialog."""

    item_type: str
    name: str
    quantity: str
    price: str
    price_with_vat: str
    link: str
    country: str
    unit: str


@dataclass(frozen=True)
class CalcImportItem:
    item_type: str
    name: str
    quantity: Decimal
    unit: str | None
    quality_document: str | None
    planned_date: date | None
    category: str | None = None
    link: str | None = None
    price: Decimal | None = None
    price_with_vat: Decimal | None = None


@dataclass(frozen=True)
class CalcImport:
    project: str
    purpose: str
    delivery_point: str | None
    procurement_deadline: date | None
    items: Sequence[CalcImportItem]


def _norm_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _find_col(header: Sequence[object], needle: str) -> int:
    """`findCol_`: first column whose normalized header contains ``needle``."""
    key = _norm_header(needle)
    for index, value in enumerate(header):
        if key in _norm_header(value):
            return index
    return -1


def unit_for_type(item_type: str | None) -> str:
    """`unitForType_`: cable-like types are measured in metres, everything else in pieces."""
    return ZPK_UNIT_BY_TYPE.get(_text(item_type), "шт")


def project_from_title(title: str | None) -> str:
    """`projectFromTitle_`: the calculation file name without the word «Расчёт»."""
    return re.sub(r"\s*расч[ёе]т\s*", " ", str(title or ""), flags=re.IGNORECASE).strip()


def parse_calc_number(value: object) -> Decimal | None:
    """`icrNum_`: spaces (incl. NBSP thousands separators) dropped, decimal comma allowed."""
    text = re.sub(r"\s", "", str(value if value is not None else "")).replace(",", ".", 1)
    match = re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)", text)
    if not match:
        return None
    return Decimal(match.group(0))


def parse_zpk(grid: Sequence[Sequence[object]]) -> list[CalcItem]:
    """`parseZpk_`: the header row has «Обозначение» and «Без НДС»; rows with an empty Обозначение are skipped."""
    header_row = next(
        (
            index
            for index, row in enumerate(grid)
            if _find_col(row, "Обозначение") >= 0 and _find_col(row, "Без НДС") >= 0
        ),
        -1,
    )
    if header_row < 0:
        return []
    head = grid[header_row]
    columns = {
        "type": _find_col(head, "Тип"),
        "name": _find_col(head, "Обозначение"),
        "qty": _find_col(head, "На проект"),
        "net": _find_col(head, "Без НДС"),
        "vat": _find_col(head, "С НДС"),
        "link": _find_col(head, "Ссылка"),
        "country": _find_col(head, "Место поставки"),
    }

    def cell(row: Sequence[object], key: str) -> str:
        index = columns[key]
        if index < 0 or index >= len(row):
            return ""
        return str(row[index] if row[index] is not None else "")

    items: list[CalcItem] = []
    for row in grid[header_row + 1 :]:
        name = cell(row, "name").strip()
        if not name:
            continue
        item_type = cell(row, "type")
        items.append(
            CalcItem(
                item_type=item_type,
                name=name,
                quantity=cell(row, "qty"),
                price=cell(row, "net"),
                price_with_vat=cell(row, "vat"),
                link=cell(row, "link"),
                country=cell(row, "country"),
                unit=unit_for_type(item_type),
            )
        )
    return items


def build_import_drafts(payload: CalcImport) -> list[RequestDraft]:
    """`importRequests_` gates, in its order and wording; nothing is created when one fails.

    One deadline for the whole batch (so one priority), the point is mandatory for «Закупка»,
    every position needs its unit, quality document and planned date. АРТИКУЛ КД = Обозначение.
    """
    items = list(payload.items)
    if not items:
        raise ValidationError("Нет позиций для импорта")
    if payload.procurement_deadline is None:
        raise ValidationError("Не указан дедлайн — заявки не создаются")
    if not _text(payload.delivery_point):
        raise ValidationError("Не выбрана точка отгрузки — заявки не создаются")
    for item in items:
        if not _text(item.unit):
            raise ValidationError(f"Не заполнена Ед.изм: {item.name}")
        if not _text(item.quality_document):
            raise ValidationError(f"Не выбран Документ по качеству (Да/Нет): {item.name}")
        if item.planned_date is None:
            raise ValidationError(f"Не указана план. дата поступления: {item.name}")

    # SUPPLYMAP's import never writes № ПРОЕКТА and never fails on the project (Imports.gs:72-79),
    # so a calc project outside the RQ-4 format stays only in НАЗНАЧЕНИЕ instead of blocking the batch.
    project = _text(payload.project)
    project_reference = project if project_reference_valid(project) else None
    drafts: list[RequestDraft] = []
    for item in items:
        draft = RequestDraft(
            purpose=payload.purpose,
            delivery_point=_text(payload.delivery_point),
            name=item.name,
            item_type=item.item_type,
            quantity=item.quantity,
            unit=_text(item.unit),
            quality_document=_text(item.quality_document),
            procurement_deadline=payload.procurement_deadline,
            planned_date=item.planned_date,
            project_reference=project_reference,
            category=_text(item.category) or DEFAULT_CATEGORY,
            design_article=item.name,
            link=item.link,
            price=item.price,
            price_with_vat=item.price_with_vat,
        )
        try:
            drafts.append(validate_draft(draft))
        except ValidationError as error:
            raise ValidationError(f"{error}: {_text(item.name)}") from error
    return drafts
