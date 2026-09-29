"""Infer warehouse identity fields from the shared technology catalog."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.techcards.connector_housings import housing_of, normalize_article


# Stock05.gs STOCK05_COAX_FAM_RE_ (SUPPLYMAP 39a5a97): ISMA and the 2.92 / 3.5 mm precision families.
COAX_PATTERN = re.compile(
    r"(RP-SMA|RP-TNC|ISMA|SMA|SMB|SMP|MMCX|MCX|BNC|TNC|QMA|FME|UHF|2\.92|3\.5|N|F)-"
    r"([A-Z]*?)(\d{1,3})?([A-Z]*)"
)
RG_NUMBERS = {
    "6", "8", "11", "58", "59", "142", "174", "178", "179", "188",
    "213", "223", "316", "400", "402", "405",
}
# JS \w and \b are ASCII-only; the SUPPLYMAP rules keep that meaning here.
_WORD = r"[A-Za-z0-9_]"
_SUPPLIER_TAIL = re.compile(r"@.*\Z")

# Stock05.gs STOCK05_SERIES_RULES_: series without a housing drawing, matched on the article key before housing
# recognition. n_at — group holding the pin count (n — the count itself), rows / rows_at, sex, tail after the
# rows; contact — a terminal; name — a fixed name with {n} and {sex}.
SERIES_RULES: tuple[dict[str, Any], ...] = (
    {"re": r"^39014(\d{2})\d$", "series": "Mini-Fit Jr.", "n_at": 1, "rows": 1},
    {"re": r"^50375(\d{2})\d$", "series": "Mini-SPOX", "n_at": 1, "rows": 1},
    {"re": r"^470541000$", "series": "KK 254", "n": 4, "rows": 1},
    {"re": r"^51005(\d{2})00$", "series": "MX2.0 (51005)", "n_at": 1, "rows": 1},
    {"re": r"^51110(\d{2})5\d$", "series": "Milli-Grid", "n_at": 1, "rows": 2},
    {"re": r"^51191(\d{2})00$", "series": "Mini-Latch", "n_at": 1, "rows": 1},
    {"re": r"^90143(\d{4})$", "series": "C-Grid III", "n_at": 1, "rows": 2},
    {"re": r"^105307([12])2(\d{2})$", "series": "Nano-Fit", "n_at": 2, "rows_at": 1},
    # JST XH and analogues (Connfly DS1069 is "Wafer & Housing XH Type").
    {"re": r"^S(\d{1,2})BXH", "series": "XH", "n_at": 1, "rows": 1, "sex": "вилка", "tail": ", угловая на плату"},
    {"re": r"^B(\d{1,2})BXH", "series": "XH", "n_at": 1, "rows": 1, "sex": "вилка", "tail": ", на плату"},
    {"re": r"^A2501(\d{2})AW", "series": "XH", "n_at": 1, "rows": 1, "sex": "вилка", "tail": ", угловая на плату"},
    {"re": rf"^DS1069(\d{{1,2}})M{_WORD}*RW", "series": "XH", "n_at": 1, "rows": 1, "sex": "вилка",
     "tail": ", угловая на плату"},
    {"re": r"^DS1069(\d{1,2})F", "series": "XH", "n_at": 1, "rows": 1, "sex": "розетка"},
    {"re": r"^IDS1069SC[BW]0*(\d{1,2})$", "series": "XH", "n_at": 1, "rows": 1, "sex": "розетка"},
    {"re": r"^TDS1069SC", "contact": "XH", "sex": "Female"},
    # JST PHD and its Scondar SCT2004H-2x03 analogue.
    {"re": r"PHDR(\d{2})V", "series": "PHD", "n_at": 1, "rows": 2},
    {"re": r"^SCT2000?4H2X?0?(\d)", "series": "PHD", "n_at": 1, "mul": 2, "rows": 2},
    # JST contacts: SM — SYM/SHM pin, SYF/SHF socket; XH — SXH socket.
    {"re": r"^S[YH]M001T", "contact": "SM", "sex": "Male"},
    {"re": r"^S[YH]F001T", "contact": "SM", "sex": "Female"},
    {"re": r"^SXH001T", "contact": "XH", "sex": "Female"},
    {"re": r"^DS1107", "series": "USB-A", "name": "USB-A вилка под пайку"},
    # Circular and other connectors (TE, Turck, CAZN, Amass, Schurter).
    {"re": r"^[TТ]4140012(\d{2})1", "series": "M12", "n_at": 1, "name": "M12 вилка {n}pin (A-код), на плату"},
    {"re": r"^FS4([4-8])\d*1825", "series": "M12", "n_at": 1, "name": "M12 вилка {n}pin, панельная, провода 0,5 м"},
    {"re": r"^M12P(\d)A", "series": "M12", "n_at": 1, "name": "M12 {n}pin (A-код)"},
    {"re": r"^GX16M?(\d{1,2})[AB]?$", "series": "GX16", "n_at": 1, "name": "GX16 {n}pin"},
    {"re": r"QS8S([MF])", "series": "QS8", "sex_at": 1, "name": "QS8-S {sex} (безыскровый)"},
    {"re": r"^61360138", "series": "Schurter 6136", "name": "Ввод питания IEC C20 с автоматом (Schurter 6136)"},
    {"re": r"7-?0026A", "series": "DC 2,5×0,7", "name": "DC-штекер 2,5×0,7 мм"},
    {"re": r"^PBS10B", "series": "PBS-10B", "name": "Кнопка PBS-10B без фиксации", "type": "Кнопка"},
)

# Supplier coax codes «S-211F»: letter — family, 1st digit 1 plug / 2 socket, 2nd digit 2 — right angle,
# trailing letter — cable (RG in words wins). 3xx/4xx (panel, flange) are not decoded.
VENDOR_FAMILIES = {"S": "SMA", "N": "N", "T": "TNC", "B": "BNC", "U": "UHF"}
VENDOR_CABLES = {"F": "RG-58", "L": "RG-174", "B": "RG-213"}
VENDOR_CODE = re.compile(rf"(?:^|[\s,(])([SNTBU])-([12])(\d)\d([FLB])?(?!{_WORD})")
VENDOR_WORDS = re.compile(rf"(?:^|[\s,(])([SNTBU])-[A-Z0-9]+\s+(male|female)(?!{_WORD})", re.IGNORECASE)
COAX_DESCRIPTION = re.compile(
    r"(?:^|[\s,(])(RP-SMA|RP-TNC|ISMA|SMA|SMB|SMP|MMCX|MCX|BNC|TNC|QMA|FME|UHF|2\.92(?:mm)?|3\.5(?:mm)?|N|F)"
    rf"(?:-[A-Z]*\d[A-Z0-9]*)?[\s\-(]*(male|female)(?!{_WORD})",
    re.IGNORECASE,
)
RG_IN_TEXT = re.compile(r"RG[\s-]?(\d{1,3})(?!\d|[.,]\d|X)", re.IGNORECASE)


@dataclass(frozen=True)
class ArticleIdentity:
    series: str
    name: str
    item_type: str
    source: str


def _article_key(value: object) -> str:
    key = re.sub(r"[\s.\-/()_]", "", str(value or "").upper())
    # Molex is written with leading zeros up to 10 digits: 0430250600 = 430250600, 0039012025 = 39012025.
    return key.lstrip("0") if re.fullmatch(r"0\d{9}", key) else key


def _field(fields: Mapping[str, str], *names: str) -> str:
    return next((str(fields.get(name) or "").strip() for name in names if fields.get(name)), "")


_SERIES_LIST_SPLIT = re.compile(r"\s*[,;]\s*")


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(part for part in _SERIES_LIST_SPLIT.split(str(value or "").strip()) if part)


def _terminals_by_series(terminals: Iterable[Mapping[str, str]]) -> tuple[Mapping[str, str], ...]:
    """Stock05.gs stock05TerBySeries_ (SUPPLYMAP fb6c798, DRIFT-fb6c798): a БД.ТЕР contact row's
    series may be a comma/semicolon-separated list of connector series it fits («NVR, NV, VB, VHR»).
    Expand into one record per series; the product name for a series is the longest product name the
    series key starts with (one product name in the row → all series get it; none matches → the whole
    original product text, unsplit)."""
    out: list[Mapping[str, str]] = []
    for fields in terminals:
        series_list = _split_list(_field(fields, "terSeries", "series"))
        if len(series_list) <= 1:
            out.append(fields)
            continue
        product_list = _split_list(_field(fields, "terComponent", "product"))
        whole_product = _field(fields, "terComponent", "product")
        for series in series_list:
            series_key = _article_key(series)
            if len(product_list) == 1:
                product = product_list[0]
            else:
                candidates = sorted(
                    (item for item in product_list if series_key.startswith(_article_key(item))),
                    key=len, reverse=True,
                )
                product = candidates[0] if candidates else ""
            out.append({**fields, "terSeries": series, "series": series,
                        "terComponent": product or whole_product, "product": product or whole_product})
    return tuple(out)


def _without_manufacturer(value: object) -> str:
    return re.sub(
        r"^(Molex|JST|Hirose|JAE|TE|Amphenol|Connfly)\s+", "", str(value or ""),
        flags=re.IGNORECASE,
    )


def _without_supplier(value: str) -> str:
    """The «@SUPPLIER …» tail (0039012025@MOLEX) is not part of the article."""
    return _SUPPLIER_TAIL.sub("", value, count=1)


def _sex(code: str) -> str:
    if re.search(r"[JPM]", code):
        return "male"
    if re.search(r"[KF]", code):
        return "female"
    return ""


def _series_rule(key: str) -> ArticleIdentity | None:
    for rule in SERIES_RULES:
        match = re.search(rule["re"], key)
        if not match:
            continue
        if rule.get("contact"):
            return ArticleIdentity(
                rule["contact"], f"Контакт {rule['contact']} {rule['sex']}", "Терминал", "серия по артикулу",
            )
        count = (int(match[rule["n_at"]]) if rule.get("n_at") else int(rule.get("n", 0))) * int(rule.get("mul", 1))
        if rule.get("name"):
            sex = ("вилка" if match[rule["sex_at"]] == "M" else "розетка") if rule.get("sex_at") else ""
            name = rule["name"].replace("{n}", str(count), 1).replace("{sex}", sex, 1)
            return ArticleIdentity(rule["series"], name, rule.get("type", "Соединитель"), "серия по артикулу")
        if count <= 0:
            return None
        rows = int(match[rule["rows_at"]]) if rule.get("rows_at") else rule.get("rows")
        rows_text = " (однорядный)" if rows == 1 else " (двухрядный)" if rows == 2 else ""
        sex = f" {rule['sex']}" if rule.get("sex") else ""
        return ArticleIdentity(
            rule["series"], f"{rule['series']}{sex} {count}pin{rows_text}{rule.get('tail', '')}",
            "Соединитель", "серия по артикулу",
        )
    return None


def _coax_family(value: str) -> str:
    family = re.sub(r"MM$", "", value.upper())
    if family == "ISMA":
        return "SMA"
    return f"{family}mm" if family in {"2.92", "3.5"} else family


def _rg_list(text: str) -> str:
    """Every RG cable of the text in order, without repeats: «RG316 RG174» → «RG-316/RG-174»; RG8X is not RG-8."""
    found: list[str] = []
    for match in RG_IN_TEXT.finditer(text):
        number = str(int(match[1]))
        if number in RG_NUMBERS and f"RG-{number}" not in found:
            found.append(f"RG-{number}")
    return "/".join(found)


def _rg086(text: str) -> str:
    """«086» / «0.086» — the .086" semi-rigid cable is RG-405."""
    return "RG-405" if re.search(rf"(?:^|[^\d.])0?\.?086(?!{_WORD})", text) else ""


def _coax_identity(raw: str) -> ArticleIdentity | None:
    cables = _rg_list(raw)
    right_angle = re.search(rf"R/A(?!{_WORD})", raw, re.IGNORECASE) or re.search("углов", raw, re.IGNORECASE)
    angle = " R/A" if right_angle else ""

    # Supplier code first: «N-111F» would otherwise read as N with the tail F — a socket.
    vendor = VENDOR_CODE.search(raw)
    if vendor:
        letter, sex_digit, angle_digit, cable_letter = vendor.groups()
        family = VENDOR_FAMILIES[letter]
        cable = cables or VENDOR_CABLES.get(cable_letter or "", "")
        name = (
            f"{family}-{'male' if sex_digit == '1' else 'female'}{' R/A' if angle_digit == '2' else ''}"
            f"{f' {cable}' if cable else ''}"
        )
        return ArticleIdentity(family, name, "Коаксиальный соединитель", "код поставщика")

    # The connector code is the first word; plating, supplier, execution («-F») and cable group («K1.5») tails
    # carry no sex. «MCX KWHT» is the same as «MCX-KWHT».
    token = re.sub(
        rf"^(MMCX|MCX|SMA|SMB|SMP|BNC|TNC)\s+(?=[JK][A-Z]*(?!{_WORD}))", r"\1-", _without_supplier(raw),
        count=1, flags=re.IGNORECASE,
    )
    token = re.split(r"[\s,(]+", token)[0].upper()
    token = re.sub(r"GOLD$", "", token)
    token = re.sub(r"(\d[A-Z]{0,2})-F(-\d+)?$", r"\1", token)
    token = re.sub(r"(\d)\.\d+$", r"\1", token)
    match = COAX_PATTERN.fullmatch(token)
    if match:
        family = _coax_family(match[1])
        head, raw_number, tail = match[2] or "", match[3], match[4] or ""
        if not raw_number:
            head, tail = head + tail, ""
        # Sex by the code only (descriptions carry typos); right angle and cables may come from the description.
        sex = _sex(head[:1]) or _sex(tail[-1:])
        code_angle = " R/A" if re.match(r"[JK]W", head) else angle
        code_rg = f"RG-{int(raw_number)}" if raw_number and str(int(raw_number)) in RG_NUMBERS else ""
        # B2 / B3 of SMA and the precision families: .086 = RG-405, .141 = RG-402.
        b_rg = (
            {"2": "RG-405", "3": "RG-402"}.get(raw_number or "", "")
            if family in {"SMA", "2.92mm", "3.5mm"} and head.endswith("B") else ""
        )
        cable = cables or code_rg or b_rg or _rg086(raw)
        if sex or cable:
            name = f"{family}{f'-{sex}' if sex else ''}{code_angle}{f' {cable}' if cable else ''}"
            return ArticleIdentity(family, name, "Коаксиальный соединитель", "разбор кода")

    # A description instead of a code: family and sex only when both are words; cables — every RG joined by «/».
    described = COAX_DESCRIPTION.search(raw)
    if described:
        family = _coax_family(described[1])
        cable = cables or _rg086(raw)
        return ArticleIdentity(
            family, f"{family}-{described[2].lower()}{angle}{f' {cable}' if cable else ''}",
            "Коаксиальный соединитель", "разбор описания",
        )

    # Supplier letter with the sex in words («S-M015R male»).
    words = VENDOR_WORDS.search(raw)
    if words:
        family = VENDOR_FAMILIES[words[1].upper()]
        return ArticleIdentity(
            family, f"{family}-{words[2].lower()}{angle}{f' {cables}' if cables else ''}",
            "Коаксиальный соединитель", "код поставщика",
        )
    return None


def infer_article_identity(
    article: object,
    records: Iterable[TechOperationRecord] = (),
) -> ArticleIdentity | None:
    raw = str(article or "").strip()
    if not raw:
        return None
    bare = _without_supplier(raw)
    key = _article_key(normalize_article(bare))
    if not key:
        return None

    records = tuple(records)
    terminals = tuple(item.fields for item in records if item.tab is TechOperationTab.TERMINAL)
    coax = tuple(item.fields for item in records if item.tab is TechOperationTab.COAX)

    terminal = next((
        fields for fields in terminals
        if _article_key(_field(fields, "terArticle", "article")) == key
    ), None)
    if terminal is not None:
        product = _field(terminal, "terComponent", "product")
        if product:
            contact = _field(terminal, "terType", "contact")
            name = f"Контакт {product}{f' {contact}' if contact else ''}"
            return ArticleIdentity(product, name, "Терминал", "БД.ТЕР")

    by_rule = _series_rule(key)
    if by_rule is not None:
        return by_rule

    matching_series = sorted((
        fields for fields in _terminals_by_series(terminals)
        if _field(fields, "terComponent", "product")
        and len(_article_key(_field(fields, "terSeries", "series"))) >= 4
        and key.startswith(_article_key(_field(fields, "terSeries", "series")))
    ), key=lambda fields: len(_article_key(_field(fields, "terSeries", "series"))), reverse=True)
    by_series = matching_series[0] if matching_series else None

    # A 9-digit Molex number inside a description («MOLEX PicoBlade 510470300 корпус вилки…») is an article too.
    number = re.search(r"(?:^|\D)0?(\d{9})(?!\d)", raw)
    housing = housing_of(bare) or housing_of(key) or (housing_of(number[1]) if number else None)
    if housing and housing["kind"] not in {"lug", "banana"} and housing["n"] > 0:
        series = (
            _field(by_series, "terComponent", "product") if by_series
            else re.sub(r"\s*\(.*\)$", "", _without_manufacturer(housing["title"]))
        )
        rows = ""
        if housing["kind"] == "grid":
            rows = (
                " (однорядный)" if housing["rows"] == 1
                else " (двухрядный)" if housing["rows"] == 2
                else f" ({housing['rows']}-рядный)"
            )
        # The sex in the family title («SM (розетка)» / «SM (вилка)») goes into the name: SMR and SMP differ.
        sex = re.search(r"\((вилка|розетка)\)", housing["title"])
        return ArticleIdentity(
            series, f"{series}{f' {sex[1]}' if sex else ''} {housing['n']}pin{rows}", "Соединитель",
            "семейство корпуса",
        )

    if by_series:
        series_key = _article_key(_field(by_series, "terSeries", "series"))
        product = _field(by_series, "terComponent", "product")
        pins = int(key[5:7]) if re.fullmatch(r"\d{5}", series_key) and re.fullmatch(r"\d{9}", key) else 0
        return ArticleIdentity(
            product,
            f"{product} {pins}pin" if pins > 0 else "",
            "Соединитель" if pins > 0 else "",
            "БД.ТЕР (главный артикул серии)",
        )

    coax_record = next((
        fields for fields in coax
        if _article_key(_field(fields, "coaxArticle", "article")) == key
    ), None)
    if coax_record is not None:
        connector_type = _field(coax_record, "coaxType", "type")
        if connector_type:
            wire = re.sub(
                r"^RG\s*-?\s*(\d+)", r"RG-\1", _field(coax_record, "coaxWire", "wire"),
                flags=re.IGNORECASE,
            )
            return ArticleIdentity(
                re.sub(r"-(male|female).*$", "", connector_type, flags=re.IGNORECASE),
                f"{connector_type}{f' {wire}' if wire else ''}",
                "Коаксиальный соединитель",
                "БД.КОАКС",
            )

    return _coax_identity(raw)
