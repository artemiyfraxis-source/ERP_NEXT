"""«Наконечники» — the lugs of БД.ТЕР in their own list (owner 26.10.2026: «тип наконечник, а не терминал… и в базе их бы
тоже отдельно сделать, в другую БД — наконечники»).

БД.ТЕР holds two kinds of crimped wire ends: the crimp contacts of connector housings (JST SXH-001T-P0.6, Molex
Micro-Fit…) — «Терминалы» — and the lugs that end a wire on their own: ferrules НШВ / НШВИ, ring and fork lugs НКИ / НК /
НВИ / НВ, pin lugs НШИ / ПМ, flat (quick-disconnect) receptacles and tabs РПИ / РППИ and their imported twins (FDD, FDFD,
FDFN, FLVDDF, «Female / Quick Disconnect», FASTON), tube lugs ТМ / ТМЛ / ТА / ТАМ. Every row stores its list
(`TechOperationRecord.ter_kind`, migration 0155) — the rows stay in tab `ter`, so every reader (the ETK generator's crimp
lookup, the harness catalogue, item names, the warehouse article identity, the stock links) finds a lug as before.
Butt splices «ГС» and sleeves «ГМЛ» are no lugs (owner's brief 26.10.2026): they stay with the contacts.

The rule reads the row's product name, series and articles (schema key or sheet column): the first word of any of them
is a lug family (`LUG_FAMILY`), or the product / series names a disconnect, FASTON or «наконечник». A row the rule
misjudges is moved by hand (`set_ter_kind`), and the move holds — a mirror reload keeps a row edited in ERP.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

TER_KIND_VALUES = ("contact", "lug")
TER_KIND_LABELS = {"contact": "Терминалы", "lug": "Наконечники"}
TER_KIND_REASON = "Список строки БД.ТЕР: «Терминалы» (contact) или «Наконечники» (lug)"
CONTACT, LUG = TER_KIND_VALUES

# The lug families (case-insensitive), as the first word of the product name, series or article. Cyrillic codes of the
# Russian makers (КВТ, Электромонтаж, REXANT), Latin ones of the imported twins. A family is followed by a non-letter
# («НШВИ 0.5-8», «РПИ-М(н)», «FDD2-250», «НКИ1,5-4»): «НК» never takes «НКС…», «ТА» never «TAB».
LUG_FAMILY = re.compile(
    r"^(?:ншви|ншв|нши|ншп|ншк|нки|нк|нви|нв|пм|рппи|рпи|тмл|там|тм|та"
    r"|fdd|mdd|fdfd|mdfd|fdfn|mdfn|flvddf|flvdd)(?![a-zа-яё])",
    re.IGNORECASE,
)
# A product / series that names the lug outright (KST «Fully Insulated Female Disconnect», Molex «InsulKrimp Quick
# Disconnect», TE «FASTIN-FASTON»).
LUG_WORDS = re.compile(r"disconnect|faston|наконечник", re.IGNORECASE)

# Where the rule reads: schema key or the sheet column (folded header) of БД.ТЕР.
_PRODUCT = ("terComponent", "product name")
_SERIES = ("terSeries", "series", "серия")
_ARTICLES = ("terArticle", "terArticleBag", "артикул контакта (reel)", "артикул контакта (bag)", "артикул контакта")


def _folded(name: object) -> str:
    return " ".join(str(name or "").casefold().split())


def _values(fields: Mapping[str, object], names: tuple[str, ...]) -> list[str]:
    wanted = {_folded(name) for name in names}
    return [" ".join(str(value or "").split()) for key, value in fields.items() if _folded(key) in wanted and str(value or "").strip()]


def is_lug(fields: Mapping[str, object]) -> bool:
    """Is a БД.ТЕР row a lug (`LUG_FAMILY` / `LUG_WORDS`)?"""
    names = _values(fields, _PRODUCT) + _values(fields, _SERIES)
    if any(LUG_WORDS.search(text) for text in names):
        return True
    return any(LUG_FAMILY.match(text) for text in names + _values(fields, _ARTICLES))


def ter_kind_of(fields: Mapping[str, object]) -> str:
    """The list of a БД.ТЕР row by the rule: «lug» or «contact»."""
    return LUG if is_lug(fields) else CONTACT


# --- The columns of «Наконечники» ---------------------------------------------------------------------------------
# Only what lugs fill (owner 23.10.2026: «если что-то заполняется на 3 % — это мусор для БД»; on the reference mirror
# 26.10.2026, 112 lug rows): no шаг / высота обжима / усилие обрыва / диаметр изоляции «от» (0 %), no constant
# columns («Тип разъёма» = «Обжимная клемма», «Пневма автомат», «ОТР», «С зачисткой» — one value for every row), no
# «Источник высоты обжима» (a service note about a value lugs do not have). The values stay in the records.
# L+ / L− (owner 29.10.2026: «у наконечника такой же функционал с припусками L+ L- и длина зачистки в БД») — the same
# allowances as a contact's: the generator adds them to the cut length of a wire whose free end gets the lug.
# Group order (owner 23.10.2026: «своя инфа в своём месте»): identification → size (мм², AWG) → construction
# (insulation Ø, strip length, material) → electrical → tooling → the maker's document. Each slot lists its sheet
# header and its schema key (a GAS snapshot carries the keys only), folded.
LUG_COLUMN_SLOTS: tuple[tuple[str, ...], ...] = (
    ("производитель", "termanufacturer"),
    ("product name", "tercomponent"),
    ("series", "серия", "terseries"),
    ("артикул контакта (reel)", "артикул контакта", "terarticle"),
    ("артикул контакта (bag)", "terarticlebag"),
    ("тип контакта", "tertype"),
    ("от мм2", "mm2from"),
    ("до мм2", "mm2to"),
    ("от awg", "awgfrom"),
    ("до awg", "awgto"),
    ("диаметр изоляции до, мм", "terinsodto"),
    ("длина зачистки, мм", "длина зачистки", "terstrip"),
    ("l+", "terlplus"),
    ("l-", "l−", "terlminus"),
    ("материал контакта", "termaterial"),
    ("максисмальная сила тока, а", "максимальная сила тока, а", "termaxcurrent"),
    ("аппликатор", "terapplicator"),
    ("ссылка на datasheet на сайте производителя", "terdatasheet"),
)


def lug_columns(columns: list[str]) -> list[str]:
    """The columns of «Наконечники» out of the БД.ТЕР columns (sheet headers or schema keys), in the slot order; a slot
    the base has no column for is left out."""
    by_key: dict[str, str] = {}
    for name in columns:
        by_key.setdefault(_folded(name), name)
    picked: list[str] = []
    for slot in LUG_COLUMN_SLOTS:
        name = next((by_key[alias] for alias in slot if alias in by_key), None)
        if name is not None and name not in picked:
            picked.append(name)
    return picked
