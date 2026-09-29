"""Pure reference-map and technology-catalog rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable
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
    WIRE = "prov"
    MATERIAL = "mat"
    KAB_COAX = "kab_coax"  # СПР.КАБ, верхняя таблица: коаксиал, D1–D3
    KAB_AWG = "kab_awg"  # СПР.КАБ, нижняя таблица: AWG → сечение, Ø жилы, Ø изоляции по маркам


_URL_HOST = re.compile(r"^[a-z]+://([^/]+)")


def normalized_supplier_key(value: str) -> str:
    """SUPPLYMAP Schema.gs supplierKey_ (RF-1): a link or a bare domain → the host without `www.`, lower case; anything
    without a dot (or with a space) is a literal key kept as typed, lower case — «Склад», «Фраксис», «Вода»,
    «КабельРФ» are real keys of SUPPLIER_BY_LINK."""
    text = str(value or "").strip().lower()
    if not text:
        raise ReferenceValidationError("Ключ поставщика обязателен")
    match = _URL_HOST.match(text)
    host = match.group(1) if match else text.split("/")[0]
    if " " not in host and "." in host:
        return host.removeprefix("www.")
    return text


# SUPPLYMAP Config.gs SUPPLIER_BY_LINK: the seed shown while the maps were never saved (References.gs getReferences).
SUPPLIER_BY_LINK: tuple[tuple[str, str], ...] = (
    ("kroks.ru", 'ООО "Крокс Плюс"'),
    ("baltic-signal.ru", 'ООО "Интеллектуальная техника"'),
    ("arlight.moscow", 'ООО "Лайтверк"'),
    ("texenergo.ru", 'ООО "МФК Техенерго"'),
    ("cabeus.ru", 'ООО "Анлан"'),
    ("chipdip.ru", 'АО "ЧИП И ДИП"'),
    ("gsm-repiteri.ru", 'ООО "Интеллектуальная техника"'),
    ("фраксис", "Склад"),
    ("склад", "Склад"),
    ("paritet.su", "Паритет"),
    ("dart.moscow", 'ООО "Дарт Холдинг"'),
    ("кабельрф", 'ООО "ЭЛЕКТРОПОСТАВЩИК"'),
    ("cable.ru", 'ООО "ЭЛЕКТРОПОСТАВЩИК"'),
    ("etm.ru", 'АО "ТД "Электротехмонтаж"'),
    ("anycubic3d.ru", "ИП Донскова Валентина Ивановна"),
    ("1001krep.ru", 'ООО "1001 КРЕПЕЖ"'),
    ("krepcom.ru", 'ООО "Спецкрепеж"'),
    ("lider-3d.ru", 'ООО "Двигатель Прогресса"'),
    ("vseinstrumenti.ru", 'ООО "Все Инструменты"'),
    ("supereyes.ru", "ИП Реснянский Андрей Александрович"),
    ("vegatel.ru", 'ООО "Вегател"'),
    ("вода", 'ООО "Польза природы"'),
    ("ozon.ru", 'ООО "Интернет Решения"'),
    ("3d-m.ru", "ИП Гусев Максим Николаевич"),
    ("olmitec.ru", 'ООО "ОЛНАКС"'),
    ("electro-mpo.ru", 'ЗАО МПО "Электромонтаж"'),
    ("snabcable.ru", 'ООО "Снабкабель"'),
    ("promelec.ru", 'ООО ТД "Промэлектроника"'),
    ("kvt-pro.ru", 'ООО "Немецкие инструменты"'),
    ("elitan.ru", 'ООО "Элитан Трейд"'),
    ("prodiel.ru", 'ООО "Продиэл"'),
    ("dart.com.ru", 'ООО "Дарт Холдинг"'),
)


def pair_is_blank(key: str, value: str) -> bool:
    """References.gs saveReferences: a row with an empty key or value is dropped, not an error (RF-2)."""
    return not str(key or "").strip() or not str(value or "").strip()


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
    mirror_key: str = ""  # identity of the mirror row this record came from ('' — created in ERP); survives ERP edits
    edited_in_erp: bool = False  # edited/created in ERP: a mirror reload never overwrites or deletes it

    @classmethod
    def create(
        cls, *, tab: TechOperationTab, display_text: str, search_text: str,
        source_sheet: str, sort_key: str, fields: dict[str, str], mirror_key: str = "", edited_in_erp: bool = False,
    ) -> TechOperationRecord:
        display = str(display_text or "").strip()
        if not display:
            raise ReferenceValidationError("Отображаемое значение техоперации обязательно")
        clean_fields = {str(key): str(value or "").strip() for key, value in fields.items() if str(key).strip()}
        search = " ".join(f"{display} {search_text or ''} {' '.join(clean_fields.values())}".casefold().split())
        return cls(
            uuid4(), tab, display, search, str(source_sheet or "").strip(),
            str(sort_key or display).strip(), clean_fields, mirror_key, edited_in_erp,
        )


def validate_entries(entries: tuple[ReferenceEntry, ...]) -> None:
    keys = [(entry.kind, entry.normalized_key) for entry in entries]
    if len(keys) != len(set(keys)):
        raise ReferenceValidationError("В справочнике есть повторяющийся ключ")


# SUPPLYMAP PortalRef: editable bases; СПР.КАБ and БД.ТК templates are view-only.
VIEW_ONLY_TABS = frozenset({TechOperationTab.KAB_COAX, TechOperationTab.KAB_AWG})
EDITABLE_TABS = frozenset(TechOperationTab) - VIEW_ONLY_TABS
VIEW_ONLY_BASES = ("spr_kab", "tk_templates")
MAX_ROW_EDITS = 200

# Column schema of the sheet-header bases (SUPPLYMAP DbMirror.gs DB_MIRROR_NEW_). Fields of these bases are keyed by
# the sheet header, so an empty tab can still offer a «+ Запись» form. op/ob/ter/coax fields are internal keys.
BASE_COLUMNS = {
    TechOperationTab.WIRE: (
        "Марка", "Тип провода", "Стандарт / производитель", "Сечение, мм2", "AWG", "Жила: проволок × диаметр, мм",
        "Число жил", "Цвета", "Наружный диаметр, мм", "Изоляция", "Экран", "Волновое сопротивление, Ом", "Темп. от, °C",
        "Темп. до, °C", "Напряжение, В", "Ток, А", "Масса, г/м", "Закупок", "Цена посл., ₽/м без НДС", "Поставщик",
        "Ссылка на datasheet", "Источник данных",
    ),
    TechOperationTab.MATERIAL: (
        "Наименование", "Группа", "Производитель", "Серия", "Размер до, мм", "Размер после, мм", "Коэф. усадки",
        "Цвет", "Клеевой слой", "Материал", "Темп. от, °C", "Темп. до, °C", "Ед. изм.", "Закупок", "Цена посл., ₽ без НДС",
        "Поставщик", "Ссылка на datasheet", "Источник данных",
    ),
}
# The v14 GAS snapshot never carries these; a snapshot without them must not wipe portal-edited rows.
# Same for СПР.КАБ (REF-SPRKAB): only the mirror carries it.
SHEET_TABLE_TABS = (TechOperationTab.WIRE, TechOperationTab.MATERIAL, TechOperationTab.KAB_COAX, TechOperationTab.KAB_AWG)


def _clean_cell(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("="):
        raise ReferenceValidationError("Формулы вводить нельзя")
    return text
