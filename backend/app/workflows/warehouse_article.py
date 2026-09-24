"""Infer warehouse identity fields from the shared technology catalog."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.techcards.connector_housings import housing_of, normalize_article


COAX_PATTERN = re.compile(
    r"^(RP-SMA|RP-TNC|SMA|SMB|SMP|MMCX|MCX|BNC|TNC|QMA|FME|UHF|N|F)-"
    r"([A-Z]*?)(\d{1,3})?([A-Z]*)$"
)
RG_NUMBERS = {
    "6", "8", "11", "58", "59", "142", "174", "178", "179", "188",
    "213", "223", "316", "400", "402", "405",
}


@dataclass(frozen=True)
class ArticleIdentity:
    series: str
    name: str
    item_type: str
    source: str


def _article_key(value: object) -> str:
    key = re.sub(r"[\s.\-/()_]", "", str(value or "").upper())
    return key[1:] if re.fullmatch(r"0\d{9}", key) else key


def _field(fields: Mapping[str, str], *names: str) -> str:
    return next((str(fields.get(name) or "").strip() for name in names if fields.get(name)), "")


def _without_manufacturer(value: object) -> str:
    return re.sub(
        r"^(Molex|JST|Hirose|JAE|TE|Amphenol|Connfly)\s+", "", str(value or ""),
        flags=re.IGNORECASE,
    )


def _sex(code: str) -> str:
    if re.search(r"[JPM]", code):
        return "male"
    if re.search(r"[KF]", code):
        return "female"
    return ""


def infer_article_identity(
    article: object,
    records: Iterable[TechOperationRecord] = (),
) -> ArticleIdentity | None:
    raw = str(article or "").strip()
    if not raw:
        return None
    normalized = normalize_article(raw)
    key = _article_key(normalized)
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

    matching_series = sorted((
        fields for fields in terminals
        if _field(fields, "terComponent", "product")
        and len(_article_key(_field(fields, "terSeries", "series"))) >= 4
        and key.startswith(_article_key(_field(fields, "terSeries", "series")))
    ), key=lambda fields: len(_article_key(_field(fields, "terSeries", "series"))), reverse=True)
    by_series = matching_series[0] if matching_series else None

    housing = housing_of(raw) or housing_of(key)
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
        return ArticleIdentity(
            series, f"{series} {housing['n']}pin{rows}", "Соединитель", "семейство корпуса",
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

    code = re.sub(r"@.*$", "", normalized)
    code = re.sub(r"GOLD$", "", code)
    match = COAX_PATTERN.fullmatch(code)
    if match:
        family, head, raw_rg, tail = match.groups()
        sex = _sex(head[:1]) or _sex(tail[-1:])
        angle = " R/A" if re.match(r"^[JK]W", head) or re.search(r"R/A\b", raw, re.IGNORECASE) else ""
        rg = f"RG-{int(raw_rg)}" if raw_rg and str(int(raw_rg)) in RG_NUMBERS else ""
        if sex or rg:
            name = f"{family}{f'-{sex}' if sex else ''}{angle}{f' {rg}' if rg else ''}"
            return ArticleIdentity(family, name, "Коаксиальный соединитель", "разбор кода")
    return None
