"""«Настройки закупки» (as Roller_Buyer /settings): one row per key in purchasing_settings, defaults in code.

Reading never fails: a missing or malformed value falls back to the default. Writing validates every value."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .infrastructure import PurchasingSettingRow


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    hint: str
    kind: str  # "int" | "decimal" | "bool" | "list" (a list of names)
    default: Any
    minimum: Decimal | None = None
    maximum: Decimal | None = None


# Stored values of the first two stay RND / FRX (GRN prefixes and old data depend on them); the screen shows full words
# (frontend purposeLabels.ts). «Коммерческий проект» is implicit for commercial projects and is not in the directory.
COMMERCIAL_PURPOSE = "Коммерческий проект"
PROTECTED_PURPOSES = ("RND", "FRX")
DEFAULT_PURPOSES = (
    "RND", "FRX", "Хоз деятельность", "Обеспечение производства", "Ремонт и обслуживание", "Склад", "Расходники",
    "Оснастка и инструмент", "Оборудование и основные средства", "Испытания и контроль качества",
    "Гарантия и рекламации", "ИТ и офис",
)
MAX_PURPOSES = 60
MAX_PURPOSE_LENGTH = 64

SETTINGS: tuple[Setting, ...] = (
    Setting("gate_min_quotes", "Минимум КП для выбора", "Меньше КП с ценой — выбор только с причиной обхода.", "int", 3, Decimal(1), Decimal(10)),
    Setting("gate_require_non_key", "Нужен КП не от ключевого", "Хотя бы одно КП от неключевого поставщика, иначе — причина.", "bool", True),
    Setting("default_vat_rate", "НДС по умолчанию, %", "Подставляется в новое КП.", "decimal", Decimal("22"), Decimal(0), Decimal(99)),
    Setting("quote_wait_days", "Ждать ответа на запрос, дней", "Дольше — поставщик попадает в «нет ответа».", "int", 3, Decimal(0), Decimal(60)),
    Setting("quote_valid_days", "КП действует, дней", "Если срок не указан в КП.", "int", 14, Decimal(1), Decimal(365)),
    Setting("delivery_warn_days", "Предупреждать о поставке за, дней", "«Приезжает скоро» и риск к сроку.", "int", 3, Decimal(0), Decimal(60)),
    Setting("delivery_default", "Доставка для подбора КП, ₽", "За каждого поставщика, если в его КП доставка не указана: «всё у него» и «Подбор».", "decimal", Decimal("1500"), Decimal(0), Decimal(1000000)),
    Setting("rotation_non_key", "Неключевых в подсказке", "Сколько неключевых поставщиков (давно не спрашивали) добавлять к ключевым.", "int", 1, Decimal(0), Decimal(5)),
    Setting("purposes", "Назначения заявок", "Назначение задаёт проект: у внутреннего проекта — из этого списка, у проекта заказчика — «Коммерческий проект». Назначение, которое уже встречается в заявках или проектах, убрать нельзя.", "list", DEFAULT_PURPOSES),
)
BY_KEY = {setting.key: setting for setting in SETTINGS}


class InvalidSetting(ValueError):
    pass


def _coerce_names(setting: Setting, raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        raise InvalidSetting(f"«{setting.label}»: нужен список")
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise InvalidSetting(f"«{setting.label}»: названия — текст")
        name = " ".join(item.split())
        if not name:
            continue
        if len(name) > MAX_PURPOSE_LENGTH:
            raise InvalidSetting(f"«{setting.label}»: название длиннее {MAX_PURPOSE_LENGTH} знаков")
        if name.casefold() in seen:
            raise InvalidSetting(f"«{setting.label}»: «{name}» повторяется")
        seen.add(name.casefold())
        names.append(name)
    if len(names) > MAX_PURPOSES:
        raise InvalidSetting(f"«{setting.label}»: не больше {MAX_PURPOSES} назначений")
    if setting.key == "purposes":
        if COMMERCIAL_PURPOSE.casefold() in seen:
            raise InvalidSetting(f"«{COMMERCIAL_PURPOSE}» не входит в список: оно подставляется для проектов заказчика")
        for protected in PROTECTED_PURPOSES:
            if protected.casefold() not in seen:
                raise InvalidSetting(f"Назначение «{protected}» убрать нельзя: от него зависит префикс ГРН")
    return names


def _coerce(setting: Setting, raw: Any) -> Any:
    if setting.kind == "list":
        return _coerce_names(setting, raw)
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw
        raise InvalidSetting(f"«{setting.label}»: нужно да или нет")
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise InvalidSetting(f"«{setting.label}»: нужно число") from None
    if setting.kind == "int" and number != number.to_integral_value():
        raise InvalidSetting(f"«{setting.label}»: нужно целое число")
    if (setting.minimum is not None and number < setting.minimum) or (setting.maximum is not None and number > setting.maximum):
        raise InvalidSetting(f"«{setting.label}»: от {setting.minimum} до {setting.maximum}")
    return int(number) if setting.kind == "int" else number


def _default(setting: Setting) -> Any:
    return list(setting.default) if setting.kind == "list" else setting.default


def load(session: Session) -> dict[str, Any]:
    stored = {row.key: row.value for row in session.scalars(select(PurchasingSettingRow))}
    values: dict[str, Any] = {}
    for setting in SETTINGS:
        try:
            values[setting.key] = _coerce(setting, stored[setting.key]) if setting.key in stored else _default(setting)
        except InvalidSetting:
            values[setting.key] = _default(setting)
    return values


def save(
    session: Session, changes: dict[str, Any], actor_id: UUID | None, now: datetime,
    in_use: Callable[[Session], set[str]] | None = None,
) -> dict[str, Any]:
    """``in_use`` (a workflow port) names the purposes that stored data already carries; those cannot be removed."""
    unknown = [key for key in changes if key not in BY_KEY]
    if unknown:
        raise InvalidSetting(f"Нет таких настроек: {', '.join(unknown)}")
    for key, raw in changes.items():
        value = _coerce(BY_KEY[key], raw)
        if key == "purposes" and in_use is not None:
            kept = {name.casefold() for name in value}
            removed = [name for name in load(session)["purposes"] if name.casefold() not in kept]
            busy = in_use(session)
            blocked = [name for name in removed if " ".join(name.split()).casefold() in busy]
            if blocked:
                raise InvalidSetting(f"Назначение уже используется, убрать нельзя: {', '.join(blocked)}")
        stored = str(value) if isinstance(value, Decimal) else value  # a list is stored as JSON
        row = session.get(PurchasingSettingRow, key)
        if row is None:
            session.add(PurchasingSettingRow(key=key, value=stored, updated_at=now, updated_by=actor_id))
        else:
            row.value, row.updated_at, row.updated_by = stored, now, actor_id
    session.flush()
    return load(session)
