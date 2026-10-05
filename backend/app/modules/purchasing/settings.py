"""«Настройки закупки» (as Roller_Buyer /settings): one row per key in purchasing_settings, defaults in code.

Reading never fails: a missing or malformed value falls back to the default. Writing validates every value."""

from __future__ import annotations

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
    kind: str  # "int" | "decimal" | "bool"
    default: Any
    minimum: Decimal | None = None
    maximum: Decimal | None = None


SETTINGS: tuple[Setting, ...] = (
    Setting("gate_min_quotes", "Минимум КП для выбора", "Меньше КП с ценой — выбор только с причиной обхода.", "int", 3, Decimal(1), Decimal(10)),
    Setting("gate_require_non_key", "Нужен КП не от ключевого", "Хотя бы одно КП от неключевого поставщика, иначе — причина.", "bool", True),
    Setting("default_vat_rate", "НДС по умолчанию, %", "Подставляется в новое КП.", "decimal", Decimal("22"), Decimal(0), Decimal(99)),
    Setting("quote_wait_days", "Ждать ответа на запрос, дней", "Дольше — поставщик попадает в «нет ответа».", "int", 3, Decimal(0), Decimal(60)),
    Setting("quote_valid_days", "КП действует, дней", "Если срок не указан в КП.", "int", 14, Decimal(1), Decimal(365)),
    Setting("delivery_warn_days", "Предупреждать о поставке за, дней", "«Приезжает скоро» и риск к сроку.", "int", 3, Decimal(0), Decimal(60)),
    Setting("rotation_non_key", "Неключевых в подсказке", "Сколько неключевых поставщиков (давно не спрашивали) добавлять к ключевым.", "int", 1, Decimal(0), Decimal(5)),
)
BY_KEY = {setting.key: setting for setting in SETTINGS}


class InvalidSetting(ValueError):
    pass


def _coerce(setting: Setting, raw: Any) -> Any:
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


def load(session: Session) -> dict[str, Any]:
    stored = {row.key: row.value for row in session.scalars(select(PurchasingSettingRow))}
    values: dict[str, Any] = {}
    for setting in SETTINGS:
        try:
            values[setting.key] = _coerce(setting, stored[setting.key]) if setting.key in stored else setting.default
        except InvalidSetting:
            values[setting.key] = setting.default
    return values


def save(session: Session, changes: dict[str, Any], actor_id: UUID | None, now: datetime) -> dict[str, Any]:
    unknown = [key for key in changes if key not in BY_KEY]
    if unknown:
        raise InvalidSetting(f"Нет таких настроек: {', '.join(unknown)}")
    for key, raw in changes.items():
        value = _coerce(BY_KEY[key], raw)
        stored = str(value) if isinstance(value, Decimal) else value
        row = session.get(PurchasingSettingRow, key)
        if row is None:
            session.add(PurchasingSettingRow(key=key, value=stored, updated_at=now, updated_by=actor_id))
        else:
            row.value, row.updated_at, row.updated_by = stored, now, actor_id
    session.flush()
    return load(session)
