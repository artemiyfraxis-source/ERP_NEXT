"""Pure reference-map and technology-catalog rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable
from urllib.parse import urlparse
from uuid import UUID, uuid4


class ReferenceValidationError(ValueError):
    pass


class ReferenceKind(str, Enum):
    SUPPLIER = "supplier"
    COUNTRY = "country"
    GOST_AWG = "gost_awg"


class TechOperationTab(str, Enum):
    EQUIPMENT = "ob"
    OPERATION = "op"
    TERMINAL = "ter"
    COAX = "coax"


def normalized_supplier_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ReferenceValidationError("Ключ поставщика обязателен")
    candidate = raw if "://" in raw else f"https://{raw}"
    host = (urlparse(candidate).hostname or "").removeprefix("www.")
    if not host or "." not in host:
        raise ReferenceValidationError("Для поставщика укажите домен или ссылку")
    return host


def normalized_manufacturer_key(value: str) -> str:
    key = " ".join(str(value or "").strip().casefold().split())
    if not key:
        raise ReferenceValidationError("Изготовитель обязателен")
    return key


def normalized_section_key(value: str) -> str:
    raw = str(value or "").strip().replace(",", ".")
    try:
        section = Decimal(raw)
    except InvalidOperation:
        section = Decimal(0)
    if not section.is_finite() or section <= 0:
        raise ReferenceValidationError("Сечение ГОСТ укажите числом в мм², например 0,35")
    return format(section.normalize(), "f")


def _awg_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 40:
        raise ReferenceValidationError("AWG укажите целым числом от 1 до 40")
    return str(int(raw))


_KEY_NORMALIZERS = {
    ReferenceKind.SUPPLIER: normalized_supplier_key,
    ReferenceKind.COUNTRY: normalized_manufacturer_key,
    ReferenceKind.GOST_AWG: normalized_section_key,
}


@dataclass(frozen=True)
class ReferenceEntry:
    id: UUID
    kind: ReferenceKind
    key: str
    normalized_key: str
    value: str
    position: int

    @classmethod
    def create(cls, kind: ReferenceKind, key: str, value: str, position: int) -> ReferenceEntry:
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if not clean_value:
            raise ReferenceValidationError("Значение справочника обязательно")
        if kind == ReferenceKind.GOST_AWG:
            clean_value = _awg_value(clean_value)
        normalized = _KEY_NORMALIZERS[kind](clean_key)
        return cls(uuid4(), kind, clean_key, normalized, clean_value, position)


def gost_awg_map(entries: Iterable[ReferenceEntry]) -> dict[str, int]:
    """Workshop ГОСТ section → AWG (SUPPLYMAP СПР.КАБ) for wire-section lookup."""
    return {entry.normalized_key: int(entry.value) for entry in entries if entry.kind == ReferenceKind.GOST_AWG}


@dataclass(frozen=True)
class TechOperationRecord:
    id: UUID
    tab: TechOperationTab
    display_text: str
    normalized_search: str
    source_sheet: str
    sort_key: str
    fields: dict[str, str]

    @classmethod
    def create(
        cls, *, tab: TechOperationTab, display_text: str, search_text: str,
        source_sheet: str, sort_key: str, fields: dict[str, str],
    ) -> TechOperationRecord:
        display = str(display_text or "").strip()
        if not display:
            raise ReferenceValidationError("Отображаемое значение техоперации обязательно")
        clean_fields = {str(key): str(value or "").strip() for key, value in fields.items() if str(key).strip()}
        search = " ".join(f"{display} {search_text or ''} {' '.join(clean_fields.values())}".casefold().split())
        return cls(
            uuid4(), tab, display, search, str(source_sheet or "").strip(),
            str(sort_key or display).strip(), clean_fields,
        )


def validate_entries(entries: tuple[ReferenceEntry, ...]) -> None:
    keys = [(entry.kind, entry.normalized_key) for entry in entries]
    if len(keys) != len(set(keys)):
        raise ReferenceValidationError("В справочнике есть повторяющийся ключ")
