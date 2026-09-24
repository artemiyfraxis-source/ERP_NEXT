"""Pure reference-map and technology-catalog rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse
from uuid import UUID, uuid4


class ReferenceValidationError(ValueError):
    pass


class ReferenceKind(str, Enum):
    SUPPLIER = "supplier"
    COUNTRY = "country"


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
        normalized = normalized_supplier_key(clean_key) if kind == ReferenceKind.SUPPLIER else normalized_manufacturer_key(clean_key)
        return cls(uuid4(), kind, clean_key, normalized, clean_value, position)


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
