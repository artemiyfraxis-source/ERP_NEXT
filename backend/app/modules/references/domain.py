"""Pure reference-map and technology-catalog rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable, Mapping
from uuid import UUID, uuid4


class ReferenceValidationError(ValueError):
    pass


class StaleRecordError(Exception):
    pass


class ReferenceKind(str, Enum):
    SUPPLIER = "supplier"
    COUNTRY = "country"
    GOST_AWG = "gost_awg"
    # «Кабели и провода»: БД.ПРОВ mark → the list the user moved it to (coax / wire / multi). The row itself stores its
    # list (`TechOperationRecord.cable_kind`, migration 0129); this map remembers the move by the mark, so a row of that
    # mark created anew (a mirror reload, an import) lands in the same list.
    CABLE_KIND = "cable_kind"
    # A column the owner added to a tech-operation table («+ Столбец», 14.10.2026): key «<tab>:<name>», value — the name;
    # БД.ПРОВ: «prov:<cable list>:<name>» — each cable list has its own columns (owner 20.10.2026). Fields are JSON, so
    # the column is real in every record of the tab (of the list); this entry says it is the owner's.
    CUSTOM_COLUMN = "custom_column"
    # The shared column set of a table (owner 22.10.2026: «показывать все, но выбрать только нужные»): one entry per
    # table column, key «<table view>|<column key>», value «1» shown / «0» hidden, position — the column's place. A
    # table without entries has no shared set: it opens with its own defaults. Only visibility and order live here —
    # the data of a hidden column stays in the records, so the generator and the warehouse read it as before.
    COLUMN_VIEW = "column_view"


# The three maps of «Соответствия» / ГОСТ→AWG; a maps save never touches the other kinds.
MAP_KINDS = (ReferenceKind.SUPPLIER, ReferenceKind.COUNTRY, ReferenceKind.GOST_AWG)
# «Прочие кабели» is gone (owner 20.10.2026): every БД.ПРОВ row is коаксиальный, монтажный, многожильный or — since
# 25.10.2026 (migration 0150) — an ETHERNET cable («lan»).
CABLE_KIND_VALUES = ("coax", "wire", "multi", "lan")
CABLE_KIND_REASON = "Список кабеля: коаксиальные, монтажные, многожильные или кабели Ethernet"


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


# Seed «Изготовитель → страна» (owner 12.10.2026: the map was empty on production). Country = the maker's home
# country (HQ / brand origin), in Russian. The effective map is the saved entries plus this seed for every key not
# saved (`merge_country_pairs`), so one manual row never hides the rest. Keys match by `normalized_manufacturer_key`
# (case and spaces only), so spelling variants («Würth»/«Wurth», «ООО …») are separate rows.
# Names typed in our reference data (БД.ТЕР / БД.МАТ / БД.ОБ / БД.КОАКС) whose country is certain:
_COUNTRY_SEED_OUR_DATA: tuple[tuple[str, str], ...] = (
    ("3M", "США"),
    ("Agilent", "США"),
    ("Bambu Lab", "Китай"),
    ("Bixolon", "Южная Корея"),
    ("Breks (Брянск)", "Россия"),
    ("Chenzhen Aituo Automation Device", "Китай"),
    ("Connfly", "Китай"),
    ("CREALITY", "Китай"),
    ("DKC", "Россия"),
    ("Dremel", "США"),
    ("Felder GmbH Loettechnik", "Германия"),
    ("Henkel (Loctite)", "Германия"),
    ("Hirose", "Япония"),
    ("Hsuan Mao", "Тайвань"),
    ("HSM (Hsuan Mao)", "Тайвань"),
    ("Huber+Suhner", "Швейцария"),
    ("I-PEX", "Япония"),
    ("I-PEX (аналог)", "Япония"),
    ("JAE", "Япония"),
    ("Jonnesway", "Тайвань"),
    ("JST", "Япония"),
    ("Kafuter", "Китай"),
    ("KLS Electronic", "Китай"),
    ("Makita", "Япония"),
    ("Ningbo Daai Machine", "Китай"),
    ("Ningbo SMAN", "Китай"),
    ("Raychman", "Россия"),
    ("Raychman / Протон", "Россия"),
    ("REMEZA", "Беларусь"),
    ("REXANT", "Россия"),
    ("Robatech", "Швейцария"),
    ("Rosenberger", "Германия"),
    ("Samtec", "США"),
    ("Schlemmer", "Германия"),
    ("Shenzhen Woer (WOER)", "Китай"),
    ("Sumitomo", "Япония"),
    ("Supvan technology", "Китай"),
    ("TE", "Швейцария"),
    ("TE Connectivity", "Швейцария"),
    ("TE Connectivity / AMP", "Швейцария"),
    ("TE Connectivity / Deutsch", "Швейцария"),
    ("TE Connectivity / Raychem", "Швейцария"),
    ("Testo", "Германия"),
    ("TOPEX", "Польша"),
    ("Ural PAK", "Россия"),
    ("Vancol Electric (Китай)", "Китай"),
    ("Wanptek", "Китай"),
    ("Yueqing Chaodao", "Китай"),
    ("Yueqing Vancol (Китай)", "Китай"),
    ("Yueqing Vancol (Китай) / МПО Электромонтаж", "Китай"),
    ("ВНИИР-Прогресс", "Россия"),
    ("Зубр", "Россия"),
    ("КВТ", "Россия"),
    ("КВТ Калуга", "Россия"),
    ("Лакокраска-Я", "Россия"),
    ("СЦНК", "Россия"),
    ("Технология-Пласт", "Россия"),
    ("Электромонтаж", "Россия"),
    ("Этиол / Спектр изопласт", "Россия"),
    ("ДКС", "Россия"),
)

# Main makers of our field (connectors, RF, terminals, wire and cable, tools, heat-shrink, electronics). An ambiguous
# brand takes its HQ country: TE / Raychem / Tyco / AMP / Deutsch → Швейцария, Weller → Германия.
_COUNTRY_SEED_GENERAL: tuple[tuple[str, str], ...] = (
    ("ABB", "Швейцария"),
    ("Alpha Wire", "США"),
    ("AMP", "Швейцария"),
    ("Amphenol", "США"),
    ("Anderson Power Products", "США"),
    ("Balluff", "Германия"),
    ("Belden", "США"),
    ("Binder", "Германия"),
    ("Bosch", "Германия"),
    ("Bourns", "США"),
    ("Cembre", "Италия"),
    ("Cinch", "США"),
    ("Deutsch", "Швейцария"),
    ("Draka", "Нидерланды"),
    ("Eaton", "Ирландия"),
    ("Ersa", "Германия"),
    ("Fischer Connectors", "Швейцария"),
    ("Fluke", "США"),
    ("General Cable", "США"),
    ("Glenair", "США"),
    ("Habia Cable", "Швеция"),
    ("Hakko", "Япония"),
    ("Harting", "Германия"),
    ("Harwin", "Великобритания"),
    ("Hellermann Tyton", "Германия"),
    ("HellermannTyton", "Германия"),
    ("Helukabel", "Германия"),
    ("Hirschmann", "Германия"),
    ("Hosiden", "Япония"),
    ("Huber & Suhner", "Швейцария"),
    ("Huber Suhner", "Швейцария"),
    ("Igus", "Германия"),
    ("Infineon", "Германия"),
    ("ITT Cannon", "США"),
    ("Klauke", "Германия"),
    ("Klein Tools", "США"),
    ("Knipex", "Германия"),
    ("Lapp", "Германия"),
    ("Legrand", "Франция"),
    ("Lemo", "Швейцария"),
    ("Leoni", "Германия"),
    ("Littelfuse", "США"),
    ("Lumberg", "Германия"),
    ("Metcal", "США"),
    ("Mini-Circuits", "США"),
    ("Molex", "США"),
    ("Murata", "Япония"),
    ("Murrelektronik", "Германия"),
    ("Neutrik", "Лихтенштейн"),
    ("Nexans", "Франция"),
    ("NXP", "Нидерланды"),
    ("ODU", "Германия"),
    ("Omron", "Япония"),
    ("Panasonic", "Япония"),
    ("Panduit", "США"),
    ("Pasternack", "США"),
    ("Pepperl+Fuchs", "Германия"),
    ("Phoenix Contact", "Германия"),
    ("Pressmaster", "Швейцария"),
    ("Prysmian", "Италия"),
    ("Radiall", "Франция"),
    ("Raychem", "Швейцария"),
    ("Rennsteig", "Германия"),
    ("Rittal", "Германия"),
    ("Schneider Electric", "Франция"),
    ("Siemens", "Германия"),
    ("Souriau", "Франция"),
    ("Staubli", "Швейцария"),
    ("Stäubli", "Швейцария"),
    ("STMicroelectronics", "Швейцария"),
    ("Sumitomo Electric", "Япония"),
    ("Switchcraft", "США"),
    ("TDK", "Япония"),
    ("Telegaertner", "Германия"),
    ("Telegartner", "Германия"),
    ("Telegärtner", "Германия"),
    ("Texas Instruments", "США"),
    ("Times Microwave", "США"),
    ("Turck", "Германия"),
    ("Tyco Electronics", "Швейцария"),
    ("Vishay", "США"),
    ("Wago", "Германия"),
    ("Weidmuller", "Германия"),
    ("Weidmüller", "Германия"),
    ("Weipu", "Китай"),
    ("Weller", "Германия"),
    ("Wiha", "Германия"),
    ("Wurth Elektronik", "Германия"),
    ("Würth Elektronik", "Германия"),
    ("Yageo", "Тайвань"),
    ("Yazaki", "Япония"),
    ("Беларускабель", "Беларусь"),
    ("Иркутсккабель", "Россия"),
    ("Кабельный завод «Спецкабель»", "Россия"),
    ("Камкабель", "Россия"),
    ("Карачевский завод «Электродеталь»", "Россия"),
    ("Кирскабель", "Россия"),
    ("Кольчугинский завод", "Россия"),
    ("Москабельмет", "Россия"),
    ("Подольсккабель", "Россия"),
    ("Рыбинсккабель", "Россия"),
    ("Сарансккабель", "Россия"),
    ("Севкабель", "Россия"),
    ("Спецкабель", "Россия"),
    ("Томсккабель", "Россия"),
    ("Уралкабель", "Россия"),
    ("Элекон", "Россия"),
    ("Электродеталь", "Россия"),
    ("Электрокабель Кольчугинский", "Россия"),
    ("Электроконтакт", "Россия"),
)

COUNTRY_BY_MANUFACTURER: tuple[tuple[str, str], ...] = tuple(
    sorted({
        " ".join(name.casefold().split()): (name, country)
        for name, country in _COUNTRY_SEED_GENERAL + _COUNTRY_SEED_OUR_DATA
    }.values(), key=lambda pair: pair[0].casefold())
)


def merge_country_pairs(saved: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """The effective «Изготовитель → страна» map: the saved rows (as saved, in their order) and then every seed row
    whose normalized key is not saved — saved wins per key, the seed fills the rest."""
    result = [(key, value) for key, value in saved]
    taken = set()
    for key, _ in result:
        try:
            taken.add(normalized_manufacturer_key(key))
        except ReferenceValidationError:
            continue
    result.extend((name, country) for name, country in COUNTRY_BY_MANUFACTURER if normalized_manufacturer_key(name) not in taken)
    return result


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


def cable_key(value: object) -> str:
    """The ETK generator's cable key (tcGenLogic `coaxCableDiaLookup_` / `coaxDiaFromRecords`): lower case without
    whitespace and hyphens — «RG-316», «RG 316» and «rg316» are one cable."""
    return re.sub(r"[\s-]", "", str(value or "").lower())


def normalized_cable_key(value: str) -> str:
    key = cable_key(value)
    if not key:
        raise ReferenceValidationError("Марка кабеля обязательна")
    return key


def _awg_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 40:
        raise ReferenceValidationError("AWG укажите целым числом от 1 до 40")
    return str(int(raw))


_KEY_NORMALIZERS = {
    ReferenceKind.SUPPLIER: normalized_supplier_key,
    ReferenceKind.COUNTRY: normalized_manufacturer_key,
    ReferenceKind.GOST_AWG: normalized_section_key,
    ReferenceKind.CABLE_KIND: normalized_cable_key,
    ReferenceKind.CUSTOM_COLUMN: lambda key: " ".join(str(key).casefold().split()),
    ReferenceKind.COLUMN_VIEW: lambda key: " ".join(str(key).casefold().split()),
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
        if kind == ReferenceKind.CABLE_KIND and clean_value not in CABLE_KIND_VALUES:
            raise ReferenceValidationError(CABLE_KIND_REASON)
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
    # БД.ПРОВ: the cable list of the row (CABLE_KIND_VALUES); None on every other tab — `check_cable_kind`, and the
    # CHECK ck_tech_operation_records_cable_kind in the database.
    cable_kind: str | None = None

    @classmethod
    def create(
        cls, *, tab: TechOperationTab, display_text: str, search_text: str,
        source_sheet: str, sort_key: str, fields: dict[str, str], mirror_key: str = "", edited_in_erp: bool = False,
        cable_kind: str | None = None,
    ) -> TechOperationRecord:
        display = str(display_text or "").strip()
        if not display:
            raise ReferenceValidationError("Отображаемое значение техоперации обязательно")
        check_cable_kind(tab, cable_kind)
        clean_fields = {str(key): str(value or "").strip() for key, value in fields.items() if str(key).strip()}
        search = " ".join(f"{display} {search_text or ''} {' '.join(clean_fields.values())}".casefold().split())
        return cls(
            uuid4(), tab, display, search, str(source_sheet or "").strip(),
            str(sort_key or display).strip(), clean_fields, mirror_key, edited_in_erp, cable_kind,
        )


def check_cable_kind(tab: TechOperationTab, cable_kind: str | None) -> None:
    """A БД.ПРОВ row is in exactly one cable list; a row of any other tab is in none."""
    if tab == TechOperationTab.WIRE and cable_kind not in CABLE_KIND_VALUES:
        raise ReferenceValidationError(CABLE_KIND_REASON)
    if tab != TechOperationTab.WIRE and cable_kind is not None:
        raise ReferenceValidationError("Список кабеля бывает только у строки БД.ПРОВ")


def validate_entries(entries: tuple[ReferenceEntry, ...]) -> None:
    keys = [(entry.kind, entry.normalized_key) for entry in entries]
    if len(keys) != len(set(keys)):
        raise ReferenceValidationError("В справочнике есть повторяющийся ключ")


# SUPPLYMAP PortalRef: editable bases; СПР.КАБ and БД.ТК templates are view-only.
VIEW_ONLY_TABS = frozenset({TechOperationTab.KAB_COAX, TechOperationTab.KAB_AWG})
EDITABLE_TABS = frozenset(TechOperationTab) - VIEW_ONLY_TABS
VIEW_ONLY_BASES = ("spr_kab", "tk_templates")
MAX_ROW_EDITS = 200

# БД.ПРОВ sheet columns (SUPPLYMAP DbMirror.gs DB_MIRROR_NEW_) of all three cable lists together, in the order every list
# shows them (owner 23.10.2026: «сделай сортировку нормальную, чтобы своя инфа в своём месте»): identification,
# construction (core → insulation → screen → jacket → outer Ø), electrical, mechanics and conditions, supply, service.
# The same group order holds on screen, where the owner's own columns and the computed ones go into their group
# (frontend `cableColumnOrder.ts`).
_PROV_COLUMNS = (
    "Марка", "Цвет", "Цвета", "Тип провода", "Стандарт", "Производители",
    "AWG", "Сечение, мм2", "Число жил", "Жила: проволок × диаметр, мм", "Изоляция", "Экран", "Наружный диаметр, мм",
    "Волновое сопротивление, Ом", "Частота до, ГГц", "Напряжение, В", "Ток, А",
    "Масса, г/м", "Темп. от, °C", "Темп. до, °C",
    "Поставщик", "Цена посл., ₽/м без НДС", "Закупок",
    "Ссылка на datasheet", "Источник данных", "Категория",
)
# Each cable list has its own columns (owner 20.10.2026: «у каждого своя структура»): the sheet columns less those the
# list never fills on prod (20.10.2026) — a coax cable has no AWG, current or category, a монтажный wire no wave
# impedance or category, a многожильный cable no current. The owner's own columns come on top, per list
# (`ReferenceKind.CUSTOM_COLUMN`). The coax D1–D3 and the hook-up structure (series, conductor material, insulation
# thickness) are no БД.ПРОВ cells: they live in СПР.КАБ and `hookup_wires`.
# A монтажный wire has one colour per row (owner 20.10.2026: «каждый цвет своя строка»): its «Цвет» is one colour, the
# list «Цвета» (the colours of a multicore cable's cores, a coax jacket's colour) is no column of it.
# Cleaned 23.10.2026 (owner: «почистил мусорные столбцы?»; «если что-то заполняется на 3 % — это мусор для БД»): a column
# that says one thing for every row of the list (монтажный «Экран» = «нет», «Число жил» = 1), the old free text a
# structured column replaced (coax «Изоляция» → «Материал диэлектрика» + «Материал оболочки», «Экран» → «Материал
# экрана»; multicore «Изоляция» → «Материал изоляции жилы» + «Материал оболочки»; монтажный «Изоляция» = «Материал
# оболочки») and a column filled in ≤ 5 % of the list's rows (coax «Сечение» 0,5 %, монтажный «Ток» 4 %, multicore
# «Категория» and «Волновое сопротивление» 1 %) is no column of the list. A filled value stays in the record (and the
# readers — the harness catalogue, the coax materials, the kind rule — read it as before); the supply columns stay in
# every list however sparse (owner 19.10.2026).
_PROV_NEVER = {
    "coax": ("AWG", "Ток, А", "Категория", "Цвет", "Сечение, мм2", "Число жил", "Изоляция", "Экран"),
    "wire": ("Волновое сопротивление, Ом", "Категория", "Цвета", "Экран", "Число жил", "Изоляция", "Ток, А",
             "Частота до, ГГц"),
    "multi": ("Ток, А", "Цвет", "Категория", "Изоляция", "Волновое сопротивление, Ом", "Частота до, ГГц"),
}
# «Частота до, ГГц» of a coax cable (owner 25.10.2026: one frequency field, as at the connectors): the rated maximum
# operating frequency; empty — the card's `max_operating_ghz` stands in (the table and the VSWR norm read the same,
# `coax_vswr.cable_max_ghz`). Never the cut-off.
CABLE_MAX_GHZ_FIELD = "Частота до, ГГц"
# Removed 25.10.2026 (owner, at the «Колонки» of each list: «всё, что без галочки, удаляй, они не нужны уже», then per
# column): nothing stays hidden — a column is shown or removed. Every list loses the service and stale supply columns
# («Цена посл.», «Закупок», «Дата цены» — the price tooltip «Цена на <дата>» still reads it — and «Источник данных»);
# the coax cables also «Цвета» and «Напряжение» (one frequency «Частота до» instead), the многожильные «Цвета» (the
# jacket colour, 31 %); their core columns «Жила: проволок × диаметр» (97 %) and «Материал жилы» (100 %) stay.
# «Стандарт» stays everywhere it was (the passport prints it). A removed column is no column of the list:
# not in the table, «Колонки», the create form, the file of «Скачать для правки» or `columns_by_kind`. Its values stay
# in the records, and the readers (harness catalogue, the kind rule, item names, the price tooltip) read them as before;
# an existing row still takes a write to it (`CABLE_WRITABLE`: the row's card and editor, `import_rows`, the prod-write
# scripts), a new row — only to «Источник данных» (`CABLE_NEW_ROW_EXTRA`: provenance, appended by loads and journals).
_SUPPLY_DROPPED = ("Цена посл., ₽/м без НДС", "Закупок", "Дата цены", "Источник данных")
_PROV_DROPPED = {
    # 26.10.2026 the coax cables also «Тип провода» (owner: «тип провода в коаксиальных тоже удали» — it says «коаксиальный»
    # for the whole list; the kind rule still reads it)
    "coax": ("Цвета", "Напряжение, В", "Тип провода", *_SUPPLY_DROPPED),
    "wire": _SUPPLY_DROPPED,
    "multi": ("Цвета", *_SUPPLY_DROPPED),
    # the многожильные cells an ETHERNET row brings and the list does not show: the row's card shows and edits them
    "lan": ("Число жил", "Материал экрана", "Напряжение, В", "Масса, г/м", "Темп. от, °C", "Темп. до, °C", "Цвета",
            *_SUPPLY_DROPPED),
}
# «ETHERNET» (owner 25.10.2026: «интернетные вынести вообще в другую вкладку — новая вкладка, новая БД»): the LAN
# cables have their own columns, all of them shown (group order — frontend `cableColumnOrder.ts`): identification,
# category (Cat5e…), the screen code (U/UTP, F/UTP…), pairs, the core, materials, laying (inside / outside), outer Ø,
# supply. «Категория», «Экран», «Число пар», «Прокладка» are read from the type, the mark and «Число жил» where empty
# (`cables.lan_fill`, migration 0150). By the owner's rule (≤ 5 % filled — no column) the list has no «Волновое
# сопротивление» (1,7 % on prod, and 100 Ом for every row) and no «Ссылка» (0 %); the supply and material columns,
# the owner's own columns of the многожильные, are the list's own here.
_LAN_COLUMNS = (
    "Марка", "Тип провода", "Категория", "Стандарт", "Производители",
    "AWG", "Сечение, мм2", "Число пар", "Жила: проволок × диаметр, мм", "Материал жилы", "Материал изоляции жилы", "Экран",
    "Материал оболочки", "Прокладка", "Наружный диаметр, мм",
    "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка на datasheet",
)
CABLE_DROPPED: dict[str, frozenset[str]] = {kind: frozenset(names) for kind, names in _PROV_DROPPED.items()}
CABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    **{kind: tuple(name for name in _PROV_COLUMNS if name not in _PROV_NEVER[kind] and name not in CABLE_DROPPED[kind])
       for kind in CABLE_KIND_VALUES if kind != "lan"},
    "lan": _LAN_COLUMNS,
}
# What a row of the list takes a write to: its columns and the removed ones (an existing row — edit, load, file).
CABLE_WRITABLE: dict[str, tuple[str, ...]] = {
    kind: (*CABLE_COLUMNS[kind], *_PROV_DROPPED[kind]) for kind in CABLE_KIND_VALUES
}
CABLE_NEW_ROW_EXTRA = ("Источник данных",)  # a removed column a new row of a load may still fill (provenance)


def cable_list_own(kind: str, own: Iterable[str]) -> list[str]:
    """The owner's own columns of a cable list less the removed ones (`CABLE_DROPPED`)."""
    return [name for name in own if name not in CABLE_DROPPED[kind]]


# БД.КОАКС sheet columns that are no column of the table any more (owner 23.10.2026: «если что-то заполняется на 3 % —
# это мусор для БД»): «L+», «L-», «Программа» (0 %), «Габариты» (3,5 %), «Материал корпуса» (2 %). Values stay in the
# records; the ETK generator reads L+ / L− / program from its own fields (`lPlus`, `lMinus`, `program`) as before.
# 25.10.2026 (owner, «Колонки» of the table: «всё, что без галочки, удаляй»): «Пол», «Исполнение», «Полярность»,
# «Монтаж», «Дата цены», «Примечание», «Источник данных» — the card of a connector still shows them, the quick filters
# «Штекеры / Гнёзда / Угловые» read «Пол» / «Исполнение» (or the type) as before, «Цена» keeps «Цена на <дата>».
COAX_DROPPED_COLUMNS = frozenset({"L+", "L-", "L−", "Программа", "Габариты", "Материал корпуса",
                                  "Пол", "Исполнение", "Полярность", "Монтаж", "Дата цены", "Примечание",
                                  "Источник данных"})


# --- «Монтажные провода»: one row per colour (owner 20.10.2026: «имелось ввиду каждый цвет своя строка») ---
# A wire row is identified by its mark AND its colour (`wire_identity`): «НВ-4 0,20 красный» and «НВ-4 0,20 синий» are two
# rows with the same values but the colour and the seller's offer (`WIRE_ROW_OWN`); every other cell is the mark's and
# is kept equal across its colours (`application._same_wire_mark`). A reader that finds a wire by its mark alone takes
# any row of it; with a colour known — the row of that colour (`pick_wire_row`).
WIRE_COLOR_FIELD = "Цвет"
CORE_COLORS_FIELD = "Цвета"
WIRE_ROW_OWN = frozenset({
    "Марка", WIRE_COLOR_FIELD, "Поставщик", "Артикул поставщика", "Ссылка", "Цена поставщика, ₽/м", "Дата цены",
    "Цена посл., ₽/м без НДС", "Закупок", "Источник данных",
})
WIRE_ONE_COLOR_REASON = "«Цвет»: один цвет на строку — другой цвет монтажного провода добавьте своей строкой"


def color_key(value: object) -> str:
    """A colour compared without case, «ё» and extra spaces: «Жёлто-зелёный» = «желто-зеленый»."""
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def split_colors(text: object) -> list[str]:
    """«чёрный, красный; белый» → the colours in their order, as typed (trimmed), a repeat (`color_key`) dropped."""
    seen: set[str] = set()
    colors: list[str] = []
    for piece in re.split(r"[,;]", str(text or "")):
        color = " ".join(piece.split())
        if color and color_key(color) not in seen:
            seen.add(color_key(color))
            colors.append(color)
    return colors


def check_wire_color(value: object) -> None:
    if len(split_colors(value)) > 1:
        raise ReferenceValidationError(WIRE_ONE_COLOR_REASON)


def wire_identity(fields: Mapping[str, str], mark: str | None = None) -> tuple[str, str]:
    """(mark key, colour key) — the natural key of a монтажный wire row."""
    return cable_key(fields.get("Марка") if mark is None else mark), color_key(fields.get(WIRE_COLOR_FIELD))


def wire_color_rows(fields: Mapping[str, str]) -> list[dict[str, str]]:
    """A монтажный wire row that still holds its colours as a list («Цвета», the mirror sheet) → one row per colour,
    every other cell the same; «Цвета» becomes «Цвет» in place (the column order kept), an empty list — one row with an
    empty «Цвет». A row without «Цвета», or with its own «Цвет» filled, is left as it is."""
    if CORE_COLORS_FIELD not in fields or str(fields.get(WIRE_COLOR_FIELD) or "").strip():
        return [dict(fields)]
    return [{(WIRE_COLOR_FIELD if key == CORE_COLORS_FIELD else key): (color if key == CORE_COLORS_FIELD else value)
             for key, value in fields.items() if key != WIRE_COLOR_FIELD}
            for color in split_colors(fields.get(CORE_COLORS_FIELD)) or [""]]


def pick_wire_row(rows: Iterable[Mapping[str, str]], color: object = "") -> Mapping[str, str] | None:
    """The row of one mark a reader takes: the one of `color` when given and there, else the first (the rows of a mark
    share every cell but the colour and the offer, so any of them gives the same values)."""
    rows = list(rows)
    wanted = color_key(color)
    if wanted:
        for row in rows:
            if color_key(row.get(WIRE_COLOR_FIELD)) == wanted:
                return row
    return rows[0] if rows else None


def cable_fields(columns: Iterable[str], fields: Mapping[str, str], pad: Iterable[str] = ()) -> dict[str, str]:
    """The fields of a БД.ПРОВ row shaped to its list `columns`: a filled value always stays (in any column), an empty
    cell stays only in a column of the list. `pad` — columns of the list given to the row empty when it lacks them
    (they come first)."""
    allowed = set(columns)
    kept = {key: value for key, value in fields.items() if key in allowed or str(value or "").strip()}
    return {**{name: "" for name in pad if name in allowed}, **kept}


# Column schema of the sheet-header bases (SUPPLYMAP DbMirror.gs DB_MIRROR_NEW_). Fields of these bases are keyed by
# the sheet header, so an empty tab can still offer a «+ Запись» form. op/ob/ter/coax fields are internal keys.
# БД.ПРОВ: the sheet columns of the cable lists (each list's own — `CABLE_COLUMNS`).
BASE_COLUMNS = {
    TechOperationTab.WIRE: _PROV_COLUMNS,
    TechOperationTab.MATERIAL: (
        "Наименование", "Группа", "Производитель", "Серия", "Размер до, мм", "Размер после, мм", "Коэф. усадки",
        "Цвет", "Клеевой слой", "Материал", "Темп. от, °C", "Темп. до, °C", "Ед. изм.", "Закупок", "Цена посл., ₽ без НДС",
        "Поставщик", "Ссылка на datasheet", "Источник данных",
    ),
}
# The v14 GAS snapshot never carries these; a snapshot without them must not wipe portal-edited rows.
# Same for СПР.КАБ (REF-SPRKAB): only the mirror carries it.
# Tabs whose table accepts the owner's own columns («+ Столбец»): БД.ПРОВ (each cable list its own, 20.10.2026),
# БД.МАТ, БД.КОАКС, БД.ТЕР and БД.ОП (14.10.2026, «Операции» shows them beside the code). БД.ОБ has none: its list is one
# row per model.
CUSTOM_COLUMN_TABS = (TechOperationTab.WIRE, TechOperationTab.MATERIAL, TechOperationTab.COAX, TechOperationTab.TERMINAL,
                      TechOperationTab.OPERATION)
MAX_COLUMN_NAME = 60


def custom_column_prefix(tab: TechOperationTab, cable_kind: str | None = None) -> str:
    """Key prefix of the owner's columns of a tab (`ReferenceKind.CUSTOM_COLUMN`): «mat:»; БД.ПРОВ per list — «prov:coax:»."""
    if tab == TechOperationTab.WIRE:
        if cable_kind not in CABLE_KIND_VALUES:
            raise ReferenceValidationError("Укажите список кабелей: " + CABLE_KIND_REASON.split(": ", 1)[1])
        return f"{tab.value}:{cable_kind}:"
    return f"{tab.value}:"


SHEET_TABLE_TABS =(TechOperationTab.WIRE, TechOperationTab.MATERIAL, TechOperationTab.KAB_COAX, TechOperationTab.KAB_AWG)


def _clean_cell(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("="):
        raise ReferenceValidationError("Формулы вводить нельзя")
    return text


# --- «Склад → база данных»: the journal of stock articles taken into a record (T14, 13.10.2026) ---

class StockLinkEvent(str, Enum):
    LINKED = "linked"  # the stock article is tied to an existing record; the record does not change
    CREATED = "created"  # a new record made from the stock article
    UPDATED = "updated"  # fields of an existing record changed from the stock article


@dataclass(frozen=True)
class StockArticle:
    """One warehouse lot as the journal keeps it: a snapshot (the lot may be renamed later, the row stays as it was)."""
    lot_id: UUID
    warehouse: str
    grn: str
    package: str
    article: str
    name: str


@dataclass(frozen=True)
class StockLinkChange:
    field: str
    old: str
    new: str


@dataclass(frozen=True)
class StockLinkEntry:
    id: UUID
    occurred_at: datetime
    event: StockLinkEvent
    article: StockArticle
    # The record as it was right after the event. `record_id` is None once the record is gone (a mirror reload
    # deletes rows it does not keep); the tab and the text stay, so the row still says what was linked.
    record_id: UUID | None
    record_tab: TechOperationTab
    record_text: str
    changes: tuple[StockLinkChange, ...]
    actor_id: UUID
    actor_name: str
