"""«База данных → Разъёмы» по сериям (владелец, 10.10.2026: «идти по сериям: серия GHR, внутри все доступные
разъёмы и их модели»).

A series groups the housing families of `connector_housings.FAMILIES` (SM: SMR + SMP, Micro-Fit: four families) and
lists every housing the maker offers. The JST lists below are the «Housing» and «Header» tables of the JST catalogue
sheets (jst-mfg.com/product/pdf/eng/e<series>.pdf, read 10.10.2026): each `(format, counts)` pair rebuilds the model
numbers exactly as the sheet prints them, e.g. ("GHR-{:02d}V-S", (2, …, 15)) is GHR-02V-S … GHR-15V-S. Headers follow
the JST rule «B… — top entry, S… — side entry, BM/SM — SMT». Series without such a table (Molex, Hirose, TE, …) show
only the housings the ERP already knows — the family's sample article — and say «диапазон не сверен»; no model number
is made up. Compatible crimp terminals come from БД.ТЕР by its «Series» column (housing prefixes such as «GHDR, GHR»).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.workflows.terminal_crimp import crimp_display

from .connector_housings import CATALOG_SAMPLES, FAMILIES, housing_of, normalize_article

JST_PDF = "https://www.jst-mfg.com/product/pdf/eng/{}.pdf"
TOP, SIDE = "Вилка на плату, вертикальная", "Вилка на плату, угловая"
TOP_SMT, SIDE_SMT = "Вилка на плату SMD, вертикальная", "Вилка на плату SMD, угловая"


def _counts(first: int, last: int, *extra: int) -> tuple[int, ...]:
    return tuple(range(first, last + 1)) + extra


# JST series by catalogue sheet. `variants`: (title, family id, format, counts); `headers`: (title, format, counts);
# `terminals`: housing prefixes as БД.ТЕР «Series» writes them.
JST_SERIES: tuple[dict[str, Any], ...] = (
    {"id": "jst-gh", "title": "JST GH", "sheet": "eGH", "terminals": ("GHR",),
     "variants": (("Корпус", "jst-gh", "GHR-{:02d}V-S", _counts(2, 15)),),
     "headers": ((TOP_SMT, "BM{:02d}B-GHS-TBT", _counts(2, 15)), (SIDE_SMT, "SM{:02d}B-GHS-TB", _counts(2, 15)))},
    {"id": "jst-sh", "title": "JST SH", "sheet": "eSH", "terminals": ("SHR",),
     "variants": (("С выступами (-B)", "jst-sh", "SHR-{:02d}V-S-B", _counts(2, 16, 20)),
                  ("Без выступов", "jst-sh", "SHR-{:02d}V-S", _counts(2, 15))),
     "headers": ((TOP_SMT, "BM{:02d}B-SRSS-TB", _counts(2, 16)), (SIDE_SMT, "SM{:02d}B-SRSS-TB", _counts(2, 15, 20)))},
    {"id": "jst-nsh", "title": "JST NSH", "sheet": "eNSH", "terminals": ("NSHR",),
     "variants": (("Корпус", "jst-nsh", "NSHR-{:02d}V-S", _counts(2, 15)),),
     "headers": ((TOP_SMT, "BM{:02d}B-NSHSS-TBT", _counts(2, 15)), (SIDE_SMT, "SM{:02d}B-NSHSS-TB", _counts(2, 15)))},
    {"id": "jst-xh", "title": "JST XH", "sheet": "eXH", "terminals": ("XHP",),
     "variants": (("Корпус", "jst-xh", "XHP-{}", _counts(1, 16, 20)),),
     "headers": ((TOP, "B{}B-XH-A", _counts(2, 16, 20)), (SIDE, "S{}B-XH-A", _counts(2, 16)))},
    {"id": "jst-ph", "title": "JST PH", "sheet": "ePH", "terminals": ("PHR",),
     "variants": (("Корпус", "jst-ph", "PHR-{}", _counts(2, 16)),),
     "headers": ((TOP, "B{}B-PH-K-S", _counts(2, 16)), (SIDE, "S{}B-PH-K-S", _counts(2, 16)))},
    {"id": "jst-eh", "title": "JST EH", "sheet": "eEH", "terminals": ("EHR",),
     "variants": (("Корпус", "jst-eh", "EHR-{}", _counts(2, 15)),),
     "headers": ((TOP, "B{}B-EH", _counts(2, 15)), (SIDE, "S{}B-EH", _counts(2, 15)))},
    {"id": "jst-zh", "title": "JST ZH", "sheet": "eZH", "terminals": ("ZHR",),
     "variants": (("Корпус", "jst-zh", "ZHR-{}", _counts(2, 13)),),
     "headers": ((TOP, "B{}B-ZR", _counts(2, 13)), (SIDE, "S{}B-ZR", _counts(2, 13)))},
    {"id": "jst-vh", "title": "JST VH", "sheet": "eVH", "terminals": ("VHR",),
     "variants": (("Тип N", "jst-vh", "VHR-{}N", _counts(2, 11)),
                  ("Тип M — фиксаторы контактов закрыты", "jst-vh", "VHR-{}M", _counts(2, 7, 9))),
     "headers": ((TOP, "B{}P-VH", _counts(2, 10)), (SIDE, "S{}P-VH", _counts(2, 7)))},
    {"id": "jst-pa", "title": "JST PA", "sheet": "ePA-F", "terminals": ("PAP",),
     "variants": (("Корпус", "jst-pa", "PAP-{:02d}V-S", _counts(2, 16)),),
     "headers": ((TOP, "B{:02d}B-PASK", _counts(2, 16)), (SIDE, "S{:02d}B-PASK-2", _counts(2, 16)))},
    {"id": "jst-ze", "title": "JST ZE", "sheet": "eZE", "terminals": ("ZER",),
     "variants": (("Корпус", "jst-ze", "ZER-{:02d}V-S", _counts(2, 16)),),
     "headers": ((TOP, "B{:02d}B-ZESK-1D", _counts(2, 16)), (SIDE, "S{:02d}B-ZESK-2D", _counts(2, 16)))},
    {"id": "jst-cz", "title": "JST CZ", "sheet": "eCZ-F", "terminals": ("CZHR",),
     "variants": (("Корпус", "jst-cz", "CZHR-{:02d}V-S", _counts(2, 20)),),
     "headers": ((TOP, "B{:02d}B-CZHK-B-1", _counts(2, 20)), (SIDE, "S{:02d}B-CZHK-B-1", _counts(2, 20)))},
    {"id": "jst-pni", "title": "JST PNI", "sheet": "ePNI-WB", "terminals": ("PNIR", "PNIRP"),
     "variants": (("Корпус", "jst-pni", "PNIRP-{:02d}V-S", _counts(2, 10)),),
     "headers": ((TOP, "B{:02d}B-PNISK-1A", _counts(2, 10)),)},
    {"id": "jst-phd", "title": "JST PHD", "sheet": "ePHD", "terminals": ("PHDR",),
     "variants": (("Корпус, 2 ряда", "jst-phd", "PHDR-{:02d}VS", tuple(range(8, 35, 2))),),
     "headers": ((TOP, "B{}B-PHDSS", tuple(range(10, 35, 2))), (SIDE, "S{}B-PHDSS", tuple(range(10, 35, 2))))},
    # SM 18 circuits is a two-row housing (12.4 mm across) — the family draws one row up to 12, so it is left out.
    {"id": "jst-sm", "title": "JST SM", "sheet": "eSM", "terminals": ("SMR", "SMP"),
     "variants": (("Розетка SMR, чёрная", "jst-sm-r", "SMR-{:02d}V-B", _counts(2, 12)),
                  ("Розетка SMR, белая", "jst-sm-r", "SMR-{:02d}V-N", _counts(2, 12)),
                  ("Вилка SMP, чёрная", "jst-sm-p", "SMP-{:02d}V-BC", _counts(2, 12)),
                  ("Вилка SMP, белая", "jst-sm-p", "SMP-{:02d}V-NC", _counts(2, 12))),
     "headers": (), "note": "18 конт. — двухрядный корпус, в базе не рисуется"},
    {"id": "jst-rcy", "title": "JST RCY", "sheet": "eRCY", "terminals": ("SYR", "SYP"),
     "variants": (("Розетка SYR, штыри", "jst-rcy", "SYR-{:02d}T", (2,)),
                  ("Розетка SYR, белая V-0", "jst-rcy", "SYR-{:02d}TV", (2,)),
                  ("Вилка SYP, гнёзда", "jst-rcy-p", "SYP-{:02d}T-1", (2,)),
                  ("Вилка SYP, белая V-0", "jst-rcy-p", "SYP-{:02d}TV-1", (2,))),
     "headers": ()},
)

# Other series: which families form one series and how БД.ТЕР names their terminals (a «Series» token, or the whole
# «Series» / «Product Name» cell). A family not listed here is a series of its own, without terminals.
GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "molex-picoblade", "title": "Molex PicoBlade", "families": ("molex-picoblade", "molex-picoblade-plug"), "terminals": ("51021", "51047")},
    {"id": "molex-minifit-jr", "title": "Molex Mini-Fit Jr.", "families": ("molex-minifit-jr",), "terminals": ("5557",)},
    {"id": "molex-microfit", "title": "Molex Micro-Fit 3.0",
     "families": ("molex-microfit", "molex-microfit-1r", "molex-microfit-plug", "molex-microfit-plug-1r"),
     "terminals": ("43025", "43645", "43020", "43640")},
    {"id": "molex-clikmate", "title": "Molex CLIK-Mate", "families": ("molex-clikmate",), "terminals": ("502578",)},
    {"id": "molex-picoclasp", "title": "Molex Pico-Clasp", "families": ("molex-picoclasp-1r", "molex-picoclasp"), "terminals": ("501189", "501330")},
    {"id": "molex-milligrid", "title": "Molex Milli-Grid", "families": ("molex-milligrid",), "terminals": ("51110",)},
    {"id": "connfly-ds1071", "title": "Connfly DS1071 (Dupont 2,54)", "families": ("connfly-ds1071",), "terminals": ("I-DS1071-SCB01", "I-DS1071-SCB02"),
     "datasheet": "https://www.connfly.com/static/upload/file/pdf/DS1071.pdf"},
    {"id": "connfly-ds1069", "title": "Connfly DS1069 (аналог JST XH)", "families": ("connfly-ds1069",), "terminals": ("I-DS1069",),
     "datasheet": "https://www.connfly.com/static/upload/file//DS1069.pdf"},
    {"id": "hirose-df11", "title": "Hirose DF11", "families": ("hirose-df11",), "terminals": ("DF11", "DF11EP")},
    {"id": "hirose-df13", "title": "Hirose DF13", "families": ("hirose-df13",), "terminals": ("DF13",)},
    {"id": "hirose-df14", "title": "Hirose DF14", "families": ("hirose-df14",), "terminals": ("DF14",)},
    {"id": "jae-fix", "title": "JAE FI-X", "families": ("jae-fix",), "terminals": ("FI-X",)},
    {"id": "te-multilock-040", "title": "TE Multilock 040", "families": ("te-multilock-040",), "terminals": ("Multilock 040 MK-II",)},
    {"id": "te-superseal", "title": "TE Superseal 1.5", "families": ("te-superseal",), "terminals": ("Superseal 1.5",)},
    {"id": "deutsch-dt", "title": "Deutsch DT", "families": ("deutsch-dt",), "terminals": ("Size 16 S&F",)},
)

MAKERS = ("JST", "Molex", "Connfly", "Hirose", "JAE", "TE", "Deutsch", "Amass", "Weipu", "CNLINKO")


def _key(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _tokens(value: object) -> set[str]:
    text = str(value or "")
    return {_key(token) for token in re.split(r"[,;]", text) if token.strip()} | {_key(text)}


def _number(value: object) -> float | None:
    try:
        return float(str(value or "").strip().replace(",", "."))
    except ValueError:
        return None


def terminals_for(keys: Iterable[str], records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """БД.ТЕР rows whose «Series» names one of the housing prefixes (or whose «Series» / «Product Name» is the key)."""
    wanted = {_key(key) for key in keys}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fields in records:
        names = _tokens(fields.get("terSeries")) | {_key(fields.get("terComponent"))}
        article = str(fields.get("terArticle") or "").strip()
        if not article or not (names & wanted) or article in seen:
            continue
        seen.add(article)
        kind = str(fields.get("terType") or "").strip()
        out.append({
            "article": article,
            "maker": str(fields.get("terManufacturer") or "").strip(),
            "product": str(fields.get("terComponent") or "").strip(),
            "series": str(fields.get("terSeries") or "").strip(),
            "kind": {"female": "гнездо", "male": "штырь"}.get(kind.casefold(), kind),
            "awg_from": str(fields.get("awgFrom") or "").strip(), "awg_to": str(fields.get("awgTo") or "").strip(),
            "mm2_from": str(fields.get("mm2From") or "").strip(), "mm2_to": str(fields.get("mm2To") or "").strip(),
            "strip": str(fields.get("terStrip") or "").strip(),
            "applicator": str(fields.get("terApplicator") or "").strip(),
            **crimp_display(fields.get("terCrimpHeight"), fields.get("terPullForceMin"), fields.get("terPullForceMax")),
        })
    return out


def wire_range(terminals: Iterable[Mapping[str, Any]]) -> dict[str, float] | None:
    """Wire the series takes, over all its terminals: thickest/thinnest AWG and mm²."""
    awg = [v for t in terminals for v in (_number(t["awg_from"]), _number(t["awg_to"])) if v is not None]
    mm2 = [v for t in terminals for v in (_number(t["mm2_from"]), _number(t["mm2_to"])) if v is not None]
    if not awg and not mm2:
        return None
    result: dict[str, float] = {}
    if awg:
        result.update(awg_thin=max(awg), awg_thick=min(awg))
    if mm2:
        result.update(mm2_min=min(mm2), mm2_max=max(mm2))
    return result


def _headers_by_count(headers: Iterable[tuple[str, str, tuple[int, ...]]]) -> dict[int, list[dict[str, str]]]:
    out: dict[int, list[dict[str, str]]] = {}
    for title, fmt, counts in headers:
        for count in counts:
            out.setdefault(count, []).append({"title": title, "article": fmt.format(count)})
    return out


def _housing(article: str, headers: Mapping[int, list[dict[str, str]]] | None = None) -> dict[str, Any] | None:
    layout = housing_of(article)
    if layout is None:
        return None
    return {"article": article, "n": layout["n"], "housing": layout, "headers": (headers or {}).get(layout["n"], [])}


def _summary(series: dict[str, Any]) -> dict[str, Any]:
    housings = [item for variant in series["variants"] for item in variant["housings"]]
    counts = sorted({item["n"] for item in housings})
    layouts = [item["housing"] for item in housings]
    first = layouts[0] if layouts else {}
    series.update(
        housing_count=len(housings), counts=counts,
        pitch=first.get("pitch"), color=first.get("color"),
        checked=bool(layouts) and all(layout.get("conf") == "high" for layout in layouts),
        sample=housings[0] if housings else None,
    )
    return series


def connector_series(terminal_records: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Every series with all its housings (each drawn by `housing_of`), the board headers by contact count, the
    compatible terminals and the wire range they take. JST first (catalogue ranges), then the rest by family order."""
    records = list(terminal_records)
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for spec in JST_SERIES:
        headers = _headers_by_count(spec["headers"])
        variants = []
        for title, family, fmt, counts in spec["variants"]:
            used.add(family)
            housings = [h for h in (_housing(fmt.format(count), headers) for count in counts) if h]
            variants.append({"title": title, "family": family, "housings": housings})
        terminals = terminals_for(spec["terminals"], records)
        result.append(_summary({
            "id": spec["id"], "title": spec["title"], "maker": "JST", "variants": variants,
            "range_checked": True, "range_src": f"JST {spec['sheet']}, Housing",
            "datasheet": JST_PDF.format(spec["sheet"]), "note": spec.get("note", ""),
            "terminals": terminals, "wire": wire_range(terminals),
        }))
    groups = {family: group for group in GROUPS for family in group["families"]}
    by_id: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        if family["id"] in used:
            continue
        group = groups.get(family["id"], {"id": family["id"], "title": family["title"], "families": (family["id"],), "terminals": ()})
        articles = _known_articles(family)
        housings = [h for h in (_housing(article) for article in articles) if h]
        if not housings:
            continue
        series = by_id.get(group["id"])
        if series is None:
            terminals = terminals_for(group["terminals"], records)
            series = by_id[group["id"]] = {
                "id": group["id"], "title": group["title"],
                "maker": next((maker for maker in MAKERS if group["title"].startswith(maker + " ")), ""),
                "variants": [], "range_checked": False, "range_src": "", "datasheet": group.get("datasheet", ""),
                "note": "", "terminals": terminals, "wire": wire_range(terminals),
            }
            result.append(series)
        series["variants"].append({"title": family["title"], "family": family["id"], "housings": housings})
    for series in result[len(JST_SERIES):]:
        _summary(series)
    return result


def _known_articles(family: Mapping[str, Any]) -> list[str]:
    """Articles of a series without a catalogue table: the TE Superseal table the family already has, else the sample."""
    if family.get("table"):
        return sorted((f"{key}-1" for key in family["table"]), key=lambda article: (family["table"][article[:-2]], article))
    sample = CATALOG_SAMPLES.get(family["id"])
    return [sample] if sample else []


def find_in_series(article: object, series: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The series and housing an article belongs to: exact model number first, then the series of the family the
    article is drawn by (a count the catalogue table lacks still opens its series)."""
    key = normalize_article(article)
    layout = housing_of(article)
    family = layout["family"] if layout else None
    fallback = None
    for item in series:
        for variant in item["variants"]:
            for housing in variant["housings"]:
                if normalize_article(housing["article"]) == key:
                    return {"series": item["id"], "article": housing["article"], "housing": housing["housing"]}
            if family and variant["family"] == family and fallback is None:
                fallback = {"series": item["id"], "article": None, "housing": layout}
    if fallback:
        return fallback
    return {"series": None, "article": None, "housing": layout} if layout else None
