"""Full stock names of connector housings, crimp contacts and lugs (owner 27.11.2026: «где папа где мама»).

`connector_name` builds OUR name of a «Соединитель», «Терминал» or «Наконечник» lot from its article and the БД.ТЕР
records; any other type → None. Gender follows the CONTACTS (owner 17.10.2026): pins — «вилка (папа)», sockets —
«розетка (мама)»; crimp contacts «штырь (папа)» / «гнездо (мама)». The contacts of a housing come, in this order, from
1. the maker-sheet mating side of the housing family (`connector_housings.MATING` / `MATING_BY_ARTICLE`, via
   `housing_of`);
2. `FAMILY_CONTACTS` below — families whose maker sheet names the contact kind of every housing in the family;
3. the БД.ТЕР contact rows whose series token is exactly the article's (letters before the first digit, or a 5–6 digit
   Molex number; the next character of the article is not a letter, so SHDR-20 is not series SHD); all such rows agree;
4. the `sex` of the `warehouse_article.SERIES_RULES` rule the article matches.
No source → no gender word, `complete=False`, `missing` names «пол». Series, pins and rows reuse
`infer_article_identity` and `housing_of` read-only; ETK names and drawings do not change.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.modules.references.domain import TERMINAL_TABS, TechOperationRecord, TechOperationTab
from app.modules.techcards.connector_housings import GENDER_WORD, housing_of
from app.workflows.stock_name_types import LotHints, StockName
from app.workflows.warehouse_article import (
    SERIES_RULES, _article_key, _field, _split_list, _without_supplier, infer_article_identity,
)

CONNECTOR = "Соединитель"
CONTACT = "Терминал"
LUG = "Наконечник"

# The phrase of each contact kind (owner 27.11.2026): a housing by its contacts, a crimp contact by itself.
HOUSING_GENDER = {"pin": f"{GENDER_WORD['pin']} (папа)", "socket": f"{GENDER_WORD['socket']} (мама)"}
CONTACT_GENDER = {"pin": "штырь (папа)", "socket": "гнездо (мама)"}

# Housing families whose every housing takes one contact kind, by the maker's sheet. Only families MATING does not
# hold; where both know a family they must agree (test). Each entry: (contacts, source).
FAMILY_CONTACTS: dict[str, tuple[str, str]] = {
    # JST wire-to-board crimp housings: the catalogue lists only socket contacts for each of them.
    "jst-xh": ("socket", "JST eXH: корпус XHP, контакт SXH-001T-P0.6 (гнездо)"),
    "jst-ph": ("socket", "JST ePH: корпус PHR, контакт SPH-002T-P0.5S (гнездо)"),
    "jst-zh": ("socket", "JST eZH: корпус ZHR, контакт SZH-002T-P0.5 (гнездо)"),
    "jst-eh": ("socket", "JST eEH: корпус EHR, контакт SEH-001T-P0.6 (гнездо)"),
    "jst-gh": ("socket", "JST eGH: корпус GHR, контакт SSHL-002T-P0.2 (гнездо)"),
    "jst-sh": ("socket", "JST eSH: корпус SHR, контакт SSH-003T-P0.2 (гнездо)"),
    "jst-vh": ("socket", "JST eVH: корпус VHR, контакт SVH-21T-P1.1 (гнездо)"),
    "jst-pa": ("socket", "JST ePA-F: корпус PAP, контакт SPHD-002T-P0.5 (гнездо)"),
    "jst-phd": ("socket", "JST ePHD: корпус PHDR, контакт SPHD-002T-P0.5 (гнездо)"),
    "jst-shl": ("socket", "JST eSHL: корпус SHLP, контакт SSHL-003T-P0.2 (гнездо)"),
    # Molex crimp housings whose sheet names the female terminal series.
    "molex-minifit-jr": ("socket", "Molex 5557 (39-01-2xxx) Receptacle, терминал 5556 female (39-00-0038)"),
    "molex-minispox": ("socket", "Molex 5264 Mini-SPOX Receptacle (50-37-5xx3), терминал 5263 female (08-70-1039)"),
    "molex-sl-2r": ("socket", "Molex 70450 SL Crimp Housing Dual Row (22-55-2xx1), терминал 70058 female (16-02-0086)"),
    "molex-kk254": ("socket", "Molex KK 254 housing 2695/6471 (22-01-2xx5/3xx7), терминал 2759 female (08-50-0113)"),
    # Connfly «Dupont» / XH-type crimp housings: Connfly lists only the T-DS… female terminal for each.
    "connfly-ds1066": ("socket", "Connfly DS1066 Housing, терминал T-DS1066-SC600 (female)"),
    "connfly-ds1069": ("socket", "Connfly DS1069 Housing (аналог JST XHP), терминал T-DS1069-SC600 (female)"),
    "connfly-ds1071": ("socket", "Connfly DS1071 Housing (Dupont 2,54), терминал T-DS1071-SC600 (female)"),
    "connfly-ds1071-01": ("socket", "Connfly DS1071-01 Housing (Dupont 2,0), терминал T-DS1071-01-SC800 (female)"),
    "hsm-hc": ("socket", "Hsuan Mao HC 2,0 H2011 (аналог JST PAP), терминал T2011 (female)"),
}

# Families of a fixed contact count: no «(однорядный)» (owner's standard: Amass XT / QS8), nor for standard sockets.
NO_ROWS = ("amass-", "rj45", "usb-", "dc-barrel")
# Families whose article letters / tails tell two articles apart: kept verbatim, never decoded (owner 27.11.2026).
# (family, article regex) → groups `variant`, `sex`, `tail`.
VARIANTS: tuple[tuple[str, str], ...] = (
    ("amass-xt", r"^(?P<variant>XT\d{2}[A-Z]*)-?(?P<sex>[MF])(?P<tail>(?:\.[A-Z0-9]+)*)$"),
    ("amass-qs8", r"^(?P<variant>QS8(?:-?[A-Z]+?)?)-?(?P<sex>[MF])(?P<tail>(?:\.[A-Z0-9]+)*)$"),
    ("weipu-sa8", r"^(?P<variant>SA81\d)/[PS]\d(?P<tail>[A-ZА-Я]*)$"),
    ("cnlinko-lp12", r"^LP-12-[CJ]0?\d(?P<tail>[A-Z]{2}(?:-\d+)*)$"),
    ("aviation-gx", r"^(?P<variant>GX\d{2}M?)-\d+(?P<tail>[A-Z]*)$"),
)
VARIANT_FAMILIES = frozenset(family for family, _ in VARIANTS)
# One article that is a pair or a Y-adapter is not one connector: review (owner 27.11.2026).
PAIR = re.compile(r"\bM\s*\+\s*F\b", re.IGNORECASE)
ADAPTER = re.compile(r"ШТАНЫ", re.IGNORECASE)
_PINS_IN_NAME = re.compile(r"(\d+)pin")
_ROWS_IN_NAME = re.compile(r"\((однорядный|двухрядный)\)")
_GENDER_IN_NAME = re.compile(r"(?<!\w)(вилка|розетка)(?!\w)")
_COLOUR_TAIL = re.compile(r"(?:\.[A-Z0-9]+)+$")

MAKERS = {
    "MOLEX": "Molex", "CONNFLY": "Connfly", "HIROSE": "Hirose", "AMASS": "Amass", "WEIPU": "Weipu", "WEI": "Weipu",
    "CNLINKO": "CNLINKO", "TYCO": "TE", "TE CONNECTIVITY": "TE", "TE": "TE", "SCONDAR": "Scondar", "KLSEL": "KLS",
    "JST": "JST", "JAE": "JAE", "TURCK": "Turck", "CAZN": "CAZN", "SCHURTER": "Schurter", "КВТ": "КВТ", "VELL": "Vell",
}
NOT_A_MAKER = {"", "КИТАЙ", "CHINA", "NONAME", "TY"}


def connector_name(hints: LotHints, records: Sequence[TechOperationRecord]) -> StockName | None:
    """OUR full name of a connector housing, crimp contact or lug; None for any other stock type."""
    item_type = str(hints.item_type or "").strip()
    if item_type == CONNECTOR:
        return _housing_name(hints, tuple(records))
    if item_type == CONTACT:
        return _lug_name(hints, sleeve_only=True) or _contact_name(hints, tuple(records))
    if item_type == LUG:
        return _lug_name(hints) or StockName(
            "", False, ("марка", "сечение-размер"), "наконечник КВТ", _attrs(maker=_maker(hints.manufacturer)),
        )
    return None


# --- housings -------------------------------------------------------------------------------------------------------

def _housing_name(hints: LotHints, records: tuple[TechOperationRecord, ...]) -> StockName:
    raw = str(hints.article or "").strip()
    maker = _maker(hints.manufacturer)
    if PAIR.search(raw):
        return StockName("", False, ("пол: в артикуле пара вилка + розетка",), "артикул", _attrs(maker=maker))
    if ADAPTER.search(raw) or ADAPTER.search(hints.name or ""):
        return StockName("", False, ("состав: переходник (штаны)",), "артикул", _attrs(maker=maker))
    if re.match(r"RA-?\d", raw, re.I):
        # main 27.11: housing_of reads RA-3011 as 30 contacts, the stock says «RA 3pin» — a wrong pin count is worse
        # than none, so the RA housings wait for a maker sheet.
        return StockName("", False, ("число контактов: RA по справочнику и по складу расходится",), "артикул",
                         _attrs(maker=maker))
    bare = re.sub(r"(?<=\d)[хХ](?=\d)", "x", _without_supplier(raw)).strip()
    key = _article_key(bare)
    housing = _housing(bare, key, raw)
    family = housing["family"] if housing else ""
    identity = infer_article_identity(raw, records)
    if identity is not None and identity.item_type not in ("", CONNECTOR):
        identity = None
    rule, rule_match = _series_rule(key)

    gender, source = _housing_gender(housing, bare, key, records, rule, rule_match)
    variant, tail = _variant(family, bare)
    series = _series(identity, housing, records, key, rule, variant)
    pins = int(housing["n"]) if housing and int(housing.get("n") or 0) > 0 else _pins_from(identity)
    execution = str(rule.get("tail", "")).lstrip(", ").strip() if rule else ""
    template = None if family in VARIANT_FAMILIES else _template(rule, rule_match, identity)
    if rule and rule.get("name") and family in VARIANT_FAMILIES:
        # «QS8-S {sex} (безыскровый)»: the article's series goes by VARIANTS, the rule keeps the execution.
        found = re.search(r"\(([^)]*)\)", rule["name"])
        execution = found[1] if found else execution
    rows = _rows(housing, identity) if not template else ""

    if template is not None:
        name = _from_template(template, gender, pins if housing else 0)
        if not series:
            series = template["series"]
    else:
        parts = [series] if series else []
        if gender:
            parts.append(HOUSING_GENDER[gender])
        if pins:
            parts.append(f"{pins}pin{f' ({rows})' if rows else ''}")
        name = " ".join(parts)
        for extra in (tail, execution):
            if extra:
                name = f"{name}, {extra}"
    missing = tuple(label for label, absent in (
        ("серия", not series), ("пол", not gender), ("число контактов", not pins and not template),
    ) if absent)
    attrs = _attrs(
        family=family or series, variant=" ".join(part for part in (variant, tail) if part), gender=gender,
        pins=str(pins or ""), execution=execution, maker=maker,
    )
    return StockName(_capital(name), not missing, missing, source, attrs)


def _housing(bare: str, key: str, raw: str) -> dict | None:
    number = re.search(r"(?:^|\D)0?(\d{9})(?!\d)", raw)
    for candidate in (bare, key, number[1] if number else "", _COLOUR_TAIL.sub("", bare.upper())):
        housing = housing_of(candidate) if candidate else None
        if housing and housing["kind"] not in {"lug", "banana"}:
            if housing.get("mate") is None and _COLOUR_TAIL.search(bare.upper()):
                # «XT60EW-M.G.B»: the colour/plating tail hides the maker's gender letter from MATING_BY_ARTICLE.
                stripped = housing_of(_COLOUR_TAIL.sub("", bare.upper()))
                if stripped and stripped["family"] == housing["family"]:
                    return stripped
            return housing
    return None


def _housing_gender(
    housing: Mapping | None, bare: str, key: str, records: tuple[TechOperationRecord, ...],
    rule: Mapping | None, rule_match: re.Match | None,
) -> tuple[str, str]:
    mate = (housing or {}).get("mate") or {}
    if mate.get("contacts") in HOUSING_GENDER:
        return mate["contacts"], f"MATING: {mate.get('src', '')}"
    family = (housing or {}).get("family", "")
    if family in FAMILY_CONTACTS:
        contacts, source = FAMILY_CONTACTS[family]
        return contacts, f"FAMILY_CONTACTS: {source}"
    by_ter = ter_series_gender(key, records)
    if by_ter:
        return by_ter
    by_rule = _rule_sex(rule, rule_match)
    if by_rule:
        return by_rule, "правило серии (warehouse_article.SERIES_RULES)"
    return "", ""


def series_token_matches(key: str, series: str) -> bool:
    """Is a БД.ТЕР series the exact token of the article key: letters only, or a 5–6 digit Molex number, and the
    article goes on with no letter after it (SHDR20 is not series SHD, XHP4 is series XHP)?"""
    token = _article_key(series)
    if not (token.isalpha() and token.isascii() or token.isdigit() and 5 <= len(token) <= 6):
        return False
    if not key.startswith(token):
        return False
    rest = key[len(token):]
    return not rest or not rest[0].isalpha()


def ter_series_gender(key: str, records: Sequence[TechOperationRecord]) -> tuple[str, str] | None:
    """The contacts of a housing by the БД.ТЕР contact rows of its series: every matching row must agree."""
    found: dict[str, str] = {}
    for record in records:
        if record.tab is not TechOperationTab.TERMINAL:
            continue
        gender = _row_gender(record.fields)
        if not gender:
            continue
        for series in _split_list(_field(record.fields, "terSeries", "series")):
            if series_token_matches(key, series):
                found.setdefault(gender, series)
    if len(found) != 1:
        return None
    gender, series = next(iter(found.items()))
    return gender, f"БД.ТЕР: серия {series}"


def _series_rule(key: str) -> tuple[Mapping | None, re.Match | None]:
    for rule in SERIES_RULES:
        match = re.search(rule["re"], key)
        if match:
            return (rule, match) if not rule.get("contact") else (None, None)
    return None, None


def _rule_sex(rule: Mapping | None, match: re.Match | None) -> str:
    if not rule or match is None:
        return ""
    word = rule.get("sex", "")
    if rule.get("sex_at"):
        word = "вилка" if match[rule["sex_at"]] == "M" else "розетка"
    if not word and rule.get("name"):
        found = _GENDER_IN_NAME.search(rule["name"])
        word = found[1] if found else ""
    return {"вилка": "pin", "розетка": "socket"}.get(word, "")


def _variant(family: str, bare: str) -> tuple[str, str]:
    article = bare.upper().split()[0] if bare.split() else ""
    for family_id, pattern in VARIANTS:
        if family_id != family:
            continue
        match = re.match(pattern, article)
        if match:
            groups = match.groupdict()
            return groups.get("variant") or "", (groups.get("tail") or "").lstrip(".-")
    return "", ""


def _series(
    identity, housing: Mapping | None, records: tuple[TechOperationRecord, ...], key: str, rule: Mapping | None,
    variant: str,
) -> str:
    family = (housing or {}).get("family", "")
    if family.startswith("amass-") and variant:
        return f"Amass {variant}"
    title = _plain_title(str((housing or {}).get("title", "")))
    if variant and title:
        # «Weipu SA8» + «SA810» → «Weipu SA810»: the article's own series replaces the family's.
        head, _, last = title.rpartition(" ")
        return f"{head} {variant}".strip() if variant.startswith(last) else f"{title} {variant}"
    if identity is not None and identity.series:
        return identity.series
    if title:
        return title
    for record in records:
        if record.tab is TechOperationTab.TERMINAL and any(
            series_token_matches(key, series) for series in _split_list(_field(record.fields, "terSeries", "series"))
        ):
            product = _field(record.fields, "terComponent", "product")
            if product:
                return product
    return str(rule.get("series", "")) if rule else ""


def _plain_title(title: str) -> str:
    title = re.sub(r"\s*\(.*\)$", "", title).strip()
    return re.sub(r"^(Molex|JST|Hirose|JAE|TE|Amphenol|Connfly)\s+", "", title, flags=re.IGNORECASE)


def _pins_from(identity) -> int:
    found = _PINS_IN_NAME.search(identity.name) if identity is not None else None
    return int(found[1]) if found else 0


def _rows(housing: Mapping | None, identity) -> str:
    if housing:
        if housing.get("kind", "grid") != "grid" or housing["family"].startswith(NO_ROWS):
            return ""
        rows = int(housing.get("rows") or 1)
        return "однорядный" if rows == 1 else "двухрядный" if rows == 2 else f"{rows}-рядный"
    found = _ROWS_IN_NAME.search(identity.name) if identity is not None else None
    return found[1] if found else ""


def _template(rule: Mapping | None, match: re.Match | None, identity) -> dict | None:
    """A series rule with a fixed name («M12 вилка {n}pin (A-код), на плату»): series, pins and the rest of it."""
    if not rule or not rule.get("name") or match is None or rule.get("type", CONNECTOR) != CONNECTOR:
        return None
    rendered = identity.name if identity is not None and identity.source == "серия по артикулу" else ""
    if not rendered:
        return None
    rendered = _GENDER_IN_NAME.sub("", rendered, count=1)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    series = str(rule["series"])
    body = rendered[len(series):] if rendered.startswith(series) else ""
    if not body and rendered != series:
        series, body = rendered, ""
    pins = _PINS_IN_NAME.search(body)
    if pins:
        body = (body[:pins.start()] + body[pins.end():])
    return {"series": series, "pins": int(pins[1]) if pins else 0, "rest": re.sub(r"\s+", " ", body).strip()}


def _from_template(template: Mapping, gender: str, housing_pins: int) -> str:
    pins = template["pins"] or housing_pins
    name = template["series"]
    if gender:
        name += f" {HOUSING_GENDER[gender]}"
    if pins:
        name += f" {pins}pin"
    rest = template["rest"]
    if rest:
        name += rest if rest.startswith(",") else f" {rest}" if rest.startswith("(") else f", {rest}"
    return name


# --- crimp contacts -------------------------------------------------------------------------------------------------

def _contact_name(hints: LotHints, records: tuple[TechOperationRecord, ...]) -> StockName:
    raw = str(hints.article or "").strip()
    key = _article_key(_without_supplier(raw))
    maker = _maker(hints.manufacturer)
    row = next((
        record.fields for record in records
        if record.tab in TERMINAL_TABS and key and key in {
            _article_key(_field(record.fields, "terArticle", "article")),
            _article_key(_field(record.fields, "terArticleBag")),
        }
    ), None)
    series = _field(row, "terComponent", "product") if row else ""
    gender = _row_gender(row) if row else ""
    source = "БД.ТЕР: артикул контакта" if row and (series or gender) else ""
    for rule in SERIES_RULES:
        if rule.get("contact") and re.search(rule["re"], key):
            series = series or rule["contact"]
            if not gender:
                gender = {"male": "pin", "female": "socket"}.get(str(rule["sex"]).lower(), "")
                source = source or "правило серии (warehouse_article.SERIES_RULES)"
            break
    section = _contact_section(row) if row else ""
    name = " ".join(part for part in ("Контакт", series, CONTACT_GENDER.get(gender, "")) if part)
    if section:  # review 27.11: the wire range tells SXH-001T from SXH-002T and keeps «…, 0,05–0,2 мм²» names
        name = f"{name}, {section}"
    missing = tuple(label for label, absent in (("серия", not series), ("пол", not gender)) if absent)
    attrs = _attrs(family=series, gender=gender, maker=maker, section=section)
    return StockName(name, not missing, missing, source or "артикул", attrs)


def _row_gender(fields: Mapping[str, str] | None) -> str:
    value = _field(fields or {}, "terGender", "terType", "Тип контакта").lower()
    return "pin" if value.startswith("male") else "socket" if value.startswith("female") else ""


def _contact_section(fields: Mapping[str, str]) -> str:
    low, high = (_field(fields, *names).replace(".", ",") for names in (("mm2From", "От мм2"), ("mm2To", "До мм2")))
    if low or high:
        return f"{low or high}–{high or low} мм²" if low != high else f"{low} мм²"
    low, high = _field(fields, "awgFrom", "От AWG"), _field(fields, "awgTo", "До AWG")
    return f"AWG {low}–{high}" if low and high else ""


# --- lugs (КВТ marks) -----------------------------------------------------------------------------------------------

# The КВТ mark at the start: flat receptacles/tabs carry -М (мама) / -П (папа); «(н)» — nylon, kept.
LUG_MARK = re.compile(
    r"^\s*(?P<mark>ВРПИ|РППИ|РПИ|НШВИ\(2\)|НШВИ|НШВ|НШИ|НШП|НВИ|НВ|НКИ|НК|НИ|ПМ|ГСИ|ГС|ТМЛ|ТМ)"
    r"(?:-?(?P<sex>[МП])(?![А-ЯЁа-яё]))?(?P<nylon>\(н\))?(?![А-ЯЁа-яё])",
)
LUG_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*\(?\s*(\d+(?:[.,]\d+)?)\)?")
SLEEVE_SECTION = re.compile(r"^\s*(\d+(?:[.,]\d+)?)")
SLEEVES = {"ГС": "Гильза соединительная", "ГСИ": "Гильза соединительная изолированная"}
LUG_GENDER = {"М": "socket", "П": "pin"}


def _lug_name(hints: LotHints, *, sleeve_only: bool = False) -> StockName | None:
    """«Наконечник НВИ 1,5-4», «Наконечник РПИ-М 1,5-4,8, розетка (мама)», «Гильза соединительная ГС 2,5»."""
    for text in (hints.article, hints.name, hints.upd_name):
        found = _lug_from(str(text or ""), sleeve_only)
        if found is not None:
            mark, sex, section, size = found
            gender = LUG_GENDER.get(sex, "")
            maker = _maker(hints.manufacturer)
            attrs = _attrs(family=mark, gender=gender, maker=maker, section=section, size=size)
            base = mark.split("(")[0]
            if base in SLEEVES:
                name = f"{SLEEVES[base]} {mark}{f' {section}' if section else ''}"
                missing = () if section else ("сечение",)
            else:
                name = f"Наконечник {mark}{f' {section}-{size}' if section and size else ''}"
                if gender:
                    name += f", {HOUSING_GENDER[gender]}"
                missing = () if section and size else ("сечение-размер",)
            return StockName(name, not missing, missing, "марка КВТ по артикулу", attrs)
    return None


def _lug_from(text: str, sleeve_only: bool) -> tuple[str, str, str, str] | None:
    upper = text.strip().upper().replace("(Н)", "(н)")
    upper = re.sub(r"^ГИЛЬЗЫ?\s+СОЕДИНИТЕЛЬН\w*\s+", "", upper)
    upper = re.sub(r"^НАКОНЕЧНИК\s+(?:[А-ЯЁ]+\s+)*?(?=[А-ЯЁ]{2,5}[-\s\d(])", "", upper)
    match = LUG_MARK.match(upper)
    if not match:
        return None
    base = match["mark"]
    if sleeve_only and base not in SLEEVES:
        return None
    sex = match["sex"] or ""
    rest = upper[match.end():]
    # «(н)» — nylon — is kept wherever it stands: «НВИ(н) 1,5-4» and «НВИ 1,5-4 (н)» are one item (review 27.11).
    nylon = match["nylon"] or ("(н)" if "(н)" in rest else "")
    mark = base + (f"-{sex}" if sex else "") + nylon
    if base in SLEEVES:
        section = SLEEVE_SECTION.match(rest.lstrip("- "))
        return mark, sex, _comma(section[1]) if section else "", ""
    size = LUG_SIZE.search(rest)
    if not size:
        return mark, sex, "", ""
    return mark, sex, _comma(size[1]), _comma(size[2])


def _comma(value: str) -> str:
    return value.replace(".", ",")


# --- shared ---------------------------------------------------------------------------------------------------------

ATTR_KEYS = ("family", "variant", "gender", "pins", "execution", "cable", "mount", "maker", "section", "size")


def _attrs(**values: str) -> dict[str, str]:
    """Every key T3 reads to tell colliding names apart; unknown ones stay empty."""
    return {key: str(values.get(key, "") or "") for key in ATTR_KEYS}


def _maker(value: object) -> str:
    text = " ".join(str(value or "").split())
    if text.upper() in NOT_A_MAKER:
        return ""
    return MAKERS.get(text.upper(), text)


def _capital(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name
