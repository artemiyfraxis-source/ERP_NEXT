"""«База данных → Разъёмы» по сериям (владелец, 10.10.2026: «идти по сериям: серия GHR, внутри все доступные
разъёмы и их модели»).

A series groups the housing families of `connector_housings.FAMILIES` (SM: SMR + SMP, Micro-Fit: four families) and
lists every housing the maker offers. The JST lists below are the «Housing» and «Header» tables of the JST catalogue
sheets (jst-mfg.com/product/pdf/eng/e<series>.pdf, read 10.10.2026): each `(format, counts)` pair rebuilds the model
numbers exactly as the sheet prints them, e.g. ("GHR-{:02d}V-S", (2, …, 15)) is GHR-02V-S … GHR-15V-S. Headers follow
the JST rule «B… — top entry, S… — side entry, BM/SM — SMT». The other makers' catalogues (and the later JST sheets)
are in `connector_catalog_data.CATALOG_SERIES` in the same format, each with the documents it was read from. Series
without such a table show only the housings the ERP already knows — the family's sample article — and say «диапазон не
сверен»; no model number is made up. Compatible crimp terminals come from БД.ТЕР by its «Series» column (housing
prefixes such as «GHDR, GHR»); the contacts the catalogue lists are checked against БД.ТЕР («нет в «Терминалы»»).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.workflows.terminal_crimp import crimp_display

from .connector_catalog_data import (
    AUTO, BTB, CATALOG_SERIES, IFACE, JST_PDF, LUGS, PLUG, POWER, RF, ROUND, SIDE, SIDE_SMT, TOP, TOP_SMT, WTB, WTW,
)
from .connector_housings import CATALOG_SAMPLES, FAMILIES, housing_of, normalize_article


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
    {"id": "jst-sm", "title": "JST SM", "sheet": "eSM", "terminals": ("SMR", "SMP", "SM", "BL2018"),
     "variants": (("Розетка SMR, чёрная", "jst-sm-r", "SMR-{:02d}V-B", _counts(2, 12)),
                  ("Розетка SMR, белая", "jst-sm-r", "SMR-{:02d}V-N", _counts(2, 12)),
                  ("Вилка SMP, чёрная", "jst-sm-p", "SMP-{:02d}V-BC", _counts(2, 12)),
                  ("Вилка SMP, белая", "jst-sm-p", "SMP-{:02d}V-NC", _counts(2, 12))),
     # eSM «Contact»: the receptacle SMR takes the pin contact, the plug SMP the socket contact.
     "contacts": (("SYM-001T-P0.6", "штырь, в розетку SMR, AWG 28–22"), ("SHF-001T-0.8BS", "гнездо, в вилку SMP, AWG 28–22")),
     "headers": (), "note": "18 конт. — двухрядный корпус, в базе не рисуется"},
    {"id": "jst-rcy", "title": "JST RCY", "sheet": "eRCY", "terminals": ("SYR", "SYP"),
     "variants": (("Розетка SYR, штыри", "jst-rcy", "SYR-{:02d}T", (2,)),
                  ("Розетка SYR, белая V-0", "jst-rcy", "SYR-{:02d}TV", (2,)),
                  ("Вилка SYP, гнёзда", "jst-rcy-p", "SYP-{:02d}T-1", (2,)),
                  ("Вилка SYP, белая V-0", "jst-rcy-p", "SYP-{:02d}TV-1", (2,))),
     "headers": ()},
)

# Gallery sections in their fixed order (owner, 12.10.2026: grouping by type). Every series has exactly one.
CATEGORIES: tuple[str, ...] = (WTB, WTW, BTB, AUTO, ROUND, POWER, IFACE, PLUG, LUGS, RF)

# Category of the families that are a series of their own (no entry in GROUPS).
FAMILY_CATEGORY: dict[str, str] = {
    "amass-xt": POWER, "amass-qs8": POWER, "rj45": IFACE, "weipu-sa8": ROUND, "cnlinko-lp12": ROUND,
    "usb-a": IFACE, "aviation-gx": ROUND, "dc-barrel": PLUG, "banana": PLUG,
}

# Other series: which families form one series and how БД.ТЕР names their terminals (a «Series» token, or the whole
# «Series» / «Product Name» cell). A family not listed here is a series of its own, without terminals.
GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "molex-sl", "title": "Molex SL", "category": WTB, "families": ("molex-sl-1r", "molex-sl-2r"), "terminals": ("SL",)},
    {"id": "molex-igrid", "title": "Molex iGrid", "category": WTB, "families": ("molex-igrid",), "terminals": ("iGrid",)},
    {"id": "molex-boardin", "title": "Molex Board-in", "category": WTB, "families": ("molex-boardin",), "terminals": ("Board-in",)},
    {"id": "molex-clikmate", "title": "Molex CLIK-Mate", "category": WTB, "families": ("molex-clikmate",), "terminals": ("502578", "CLIK-Mate")},
    {"id": "molex-picoclasp", "title": "Molex Pico-Clasp", "category": WTB, "families": ("molex-picoclasp-1r", "molex-picoclasp"), "terminals": ("501189", "501330")},
    {"id": "te-multilock-040", "title": "TE Multilock 040", "category": AUTO, "families": ("te-multilock-040",), "terminals": ("Multilock 040 MK-II",)},
    {"id": "te-superseal", "title": "TE Superseal 1.5", "category": AUTO, "families": ("te-superseal",), "terminals": ("Superseal 1.5", "SUPERSEAL (legacy)")},
    {"id": "deutsch-dt", "title": "Deutsch DT", "category": AUTO, "families": ("deutsch-dt",),
     "terminals": ("Size 16 S&F", "DEUTSCH DT/DTM/DTP", "DEUTSCH DTM", "DEUTSCH DTP/DTHD (Solid)")},
    {"id": "dsub", "title": "D-Sub", "category": IFACE, "families": ("dsub",), "terminals": ("D-Sub High-End crimp contact",)},
    {"id": "lug", "title": "Наконечники и гильзы", "category": LUGS, "families": ("lug",),
     "terminals": ("НКИ", "НВИ", "НШВИ", "НК", "НШВ", "ГС", "ПМ")},
)

# Series known only from БД.ТЕР «Series» / «Product Name» (and the shop's data): no housing table in the ERP, so the card
# shows the terminals and the picture of the closest shape (`pic` = a family whose sample is drawn), never a made-up
# model number; `note` says why the housings are not there (16.10.2026: te.com and molex.com do not answer from the
# shop's network either; the automotive contact systems go into the carmakers' own housings).
# (id, title, maker, category, pic, terminal keys, note).
NO_TE = "чертежи TE недоступны (сайт TE закрыт), таблицы корпусов нет"
CAR_SYSTEM = "система контактов для корпусов автопроизводителей — своей таблицы корпусов у серии нет"
_LOOSE: tuple[tuple[str, str, str, str, str, tuple[str, ...], str], ...] = (
    # CTX50 terminals 34905 (349052447, 349056447) go into the Mini50 sealed receptacle housing 34967 (Molex part sheet of
    # 349052447; 349672001 = 2 positions per element14, 349671001 = 10 positions, polarization A per Arrow): the digit after
    # 34967 is not the position count, so no count can be read from the number and no table is made up; the closest shape.
    ("molex-ctx50", "Molex CTX50", "Molex", WTB, "jst-xh", ("CTX50",),
     "в номере Mini50 34967 нет числа контактов, чертёж Molex недоступен"),
    ("hsm-phu", "Hsuan Mao PHU / MHU / MX", "Hsuan Mao", WTB, "jst-ph", ("PHU/MHU/MX", "HC (PAP)"),
     "каталога Hsuan Mao с номерами корпусов нет"),
    ("jr-servo", "Сервоконнектор JR / Futaba", "", WTB, "connfly-ds1071", ("JR/Futaba servo connector contact",),
     "у сервоконнектора нет производителя и чертежа корпуса"),
    ("te-matenlok", "TE MATE-N-LOK", "TE", WTW, "molex-minifit-jr", ("MATE-N-LOK",), NO_TE),
    ("te-multilock-070", "TE Multilock 070", "TE", AUTO, "te-multilock-040", ("AMP MULTILOCK 070",), NO_TE),
    ("te-mcon12", "TE MCON 1.2", "TE", AUTO, "te-superseal", ("MCON 1.2 LL (Locking-Lance)", "MCON 1.2mm"), CAR_SYSTEM),
    ("te-mcp15", "TE MCP 1.5K", "TE", AUTO, "te-superseal", ("AMP MCP 1.5K", "AMP MCP 1.5K (Automation Ready)"), CAR_SYSTEM),
    ("te-mcp28", "TE MCP 2.8K", "TE", AUTO, "te-superseal", ("AMP MCP 2.8K", "AMP MCP 2.8 (HDSCS/Timer)", "AMP MCP 2.8mm", "MCP"),
     CAR_SYSTEM),
    ("te-microtimer", "TE Micro Timer", "TE", AUTO, "te-superseal", ("AMP TIMER, Micro Timer II", "AMP TIMER, Micro-Timer III"),
     CAR_SYSTEM),
    ("te-jpt", "TE Junior Power Timer", "TE", AUTO, "te-superseal", ("Junior Power Timer", "AMP TIMER (receptacle)"), CAR_SYSTEM),
    ("te-mqs", "TE MQS", "TE", AUTO, "te-superseal", ("MQS",), CAR_SYSTEM),
    ("te-ampseal", "TE AMPSEAL", "TE", AUTO, "te-superseal", ("AMPSEAL",), NO_TE),
    ("te-superseal-10", "TE Superseal 1.0", "TE", AUTO, "te-superseal", ("AMP SUPERSEAL 1.0",), NO_TE),
    ("te-amp-1528", "TE AMP 1.5/2.8", "TE", AUTO, "te-superseal", ("AMP 1.5/2.8 Connector System (RECPT AND TAB)",), CAR_SYSTEM),
    ("te-din72585", "TE Round 2,5 (DIN 72585)", "TE", AUTO, "te-superseal", ("AMP Round Connector System 2,5mm (DIN 72585)",),
     NO_TE),
    ("sumitomo-ts", "Sumitomo TS Sealed", "Sumitomo", AUTO, "te-superseal", ("TS Sealed Series (0,64мм/025)",), CAR_SYSTEM),
    ("te-amplimite", "TE AMPLIMITE HD-20", "TE", IFACE, "dsub", ("AMPLIMITE HD-20 (HDP-20)",), NO_TE),
    ("te-intercontec", "TE Intercontec", "TE", ROUND, "aviation-gx", ("Intercontec circular contact", "Intercontec circular contact socket"),
     NO_TE),
    ("flat-quick", "Плоские клеммы быстрого соединения", "", LUGS, "lug",
     ("РПИ-М", "РППИ-М", "РПИ-П", "РПИ-М(н)", "FDD", "Fully Insulated Female Disconnect", "FLVDDF", "InsulKrimp Quick Disconnect",
      "TAB, FASTIN-FASTON", "Контакт штыревой 6,3", "Клемма гнездовая с замком", "Контакт гнездовой",
      "Контакт гнездовой серии 2,8мм", "Контакт гнездовой серии 2,8мм (с фиксирующим усиком)"),
     "клеммы без корпуса"),
)

# ВЧ (coax) series from БД.КОАКС «Тип/Серия»: the part before «-male / -female / -plug». (id, title, category, bases).
_RF: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("rf-sma", "SMA", RF, ("SMA",)), ("rf-rpsma", "RP-SMA", RF, ("RP-SMA",)), ("rf-n", "N-тип", RF, ("N",)),
    ("rf-bnc", "BNC", RF, ("BNC",)), ("rf-tnc", "TNC", RF, ("TNC",)), ("rf-uhf", "UHF", RF, ("UHF",)),
    ("rf-mcx", "MCX", RF, ("MCX",)), ("rf-mmcx", "MMCX", RF, ("MMCX",)), ("rf-smb", "SMB", RF, ("SMB",)),
    ("rf-smp", "SMP", RF, ("SMP",)), ("rf-ssmc", "SSMC", RF, ("SSMC",)), ("rf-qma", "QMA", RF, ("QMA",)),
    ("rf-mhf", "MHF1 / U.FL", RF, ("MHF1", "U.FL")), ("rf-f", "F-тип", RF, ("F",)), ("rf-292", "2,92 мм", RF, ("2.92MM",)),
    ("rf-sr50", "СР-50", RF, ("СР-50",)), ("rf-fakra", "Fakra", AUTO, ("FAKRA",)),
)



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


def _headers_by_count(headers: Iterable[tuple]) -> dict[int, list[dict[str, Any]]]:
    """(title, format, counts[, family ids]) → headers by contact count; with family ids — only for the housings of
    those families (Hirose DF1B: 18 contacts in one row and 18 in two rows mate different headers, the in-line
    sockets none)."""
    out: dict[int, list[dict[str, Any]]] = {}
    for title, fmt, counts, *families in headers:
        only = (families[0],) if families and isinstance(families[0], str) else (tuple(families[0]) if families else ())
        for count in counts:
            out.setdefault(count, []).append({"title": title, "article": fmt.format(count), "families": only})
    return out


def _housing(article: str, headers: Mapping[int, list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    layout = housing_of(article)
    if layout is None:
        return None
    fitting = [{"title": h["title"], "article": h["article"]} for h in (headers or {}).get(layout["n"], [])
               if not h["families"] or layout["family"] in h["families"]]
    return {"article": article, "n": layout["n"], "housing": layout, "headers": fitting}


def _summary(series: dict[str, Any]) -> dict[str, Any]:
    housings = [item for variant in series["variants"] for item in variant["housings"]]
    counts = sorted({item["n"] for item in housings})
    layouts = [item["housing"] for item in housings]
    first = layouts[0] if layouts else {}
    series.update(
        housing_count=len(housings), counts=counts,
        pitch=first.get("pitch"), color=first.get("color"),
        checked=bool(layouts) and all(layout.get("conf") == "high" for layout in layouts),
        sample=housings[0] if housings else _picture(series.get("pic")),
    )
    series.pop("pic", None)
    series.setdefault("sources", [{"title": "Чертёж производителя", "url": series["datasheet"]}] if series.get("datasheet") else [])
    series.setdefault("contacts", [])
    return series


def _picture(family: str | None) -> dict[str, Any] | None:
    """Card picture of a series without housings: the closest family's sample, drawn by its rule (not a model number)."""
    sample = CATALOG_SAMPLES.get(family or "")
    layout = housing_of(sample) if sample else None
    return {"article": "", "n": layout["n"], "housing": layout, "headers": []} if layout else None


def _rf_base(kind: object) -> str:
    """БД.КОАКС «Тип/Серия» → the connector type: «SMA-male R/A» → SMA, «U.FL-female R/A» → U.FL, «QMA» → QMA."""
    text = str(kind or "").strip()
    match = re.match(r"^(.*?)-(?:male|female|plug)\b", text, flags=re.I)
    return _key(match[1] if match else text.split()[0] if text else "")


def _rf_catalog(bases: tuple[str, ...], coax_records: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    wanted = {_key(base) for base in bases}
    out, seen = [], set()
    for fields in coax_records:
        article = str(fields.get("coaxArticle") or "").strip()
        if not article or article in seen or _rf_base(fields.get("coaxType")) not in wanted:
            continue
        seen.add(article)
        out.append({"article": article, "kind": str(fields.get("coaxType") or "").strip(),
                    "maker": str(fields.get("coaxMfr") or "").strip(), "wire": str(fields.get("coaxWire") or "").strip()})
    return out


def _bare(series_id: str, title: str, maker: str, category: str, *, terminals: list[dict[str, Any]] | None = None,
          catalog: list[dict[str, str]] | None = None, pic: str | None = None, note: str = "") -> dict[str, Any]:
    """A series with no housing table in the ERP: only terminals / БД.КОАКС articles and the picture of the closest shape;
    `note` — why the housings are not there."""
    terminals = terminals or []
    return _summary({
        "id": series_id, "title": title, "maker": maker, "category": category, "variants": [], "range_checked": False,
        "range_src": "", "datasheet": "", "note": note, "terminals": terminals, "wire": wire_range(terminals),
        "catalog": catalog or [], "pic": pic,
    })


def _flat(article: object) -> str:
    """A part number without separators: Molex «39-01-2080» and «39012080», JST «SHJP-02V-S (HF)» and «…(HF)» meet."""
    return re.sub(r"[^0-9A-ZА-Я]", "", normalize_article(article))


def _variant_articles(variant: tuple) -> tuple[str, str, list[str]]:
    """(title, family, format, counts) → the part numbers the format rebuilds; (title, family, (part, …)) → as listed."""
    if len(variant) == 3:
        title, family, articles = variant
        return title, family, list(articles)
    title, family, fmt, counts = variant
    return title, family, [fmt.format(count) for count in counts]


def _jst_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The first JST sheets (`JST_SERIES`) in the catalogue format of `connector_catalog_data`."""
    return {**spec, "maker": "JST", "category": WTW if spec["id"] in {"jst-rcy", "jst-sm"} else WTB,
            "datasheet": JST_PDF.format(spec["sheet"]), "range_checked": True, "range_src": f"JST {spec['sheet']}, Housing",
            "sources": ((f"JST {spec['sheet']}", JST_PDF.format(spec["sheet"])),), "contacts": spec.get("contacts", ())}


def _catalog_series(spec: Mapping[str, Any], records: list[Mapping[str, Any]], known: set[str]) -> dict[str, Any]:
    """A series with its housing table from the maker's catalogue: every housing drawn by its family, the board headers
    by contact count, the БД.ТЕР terminals and the catalogue's contacts marked present / missing in БД.ТЕР."""
    headers = _headers_by_count(spec.get("headers", ()))
    variants = []
    for variant in spec["variants"]:
        title, family, articles = _variant_articles(variant)
        housings = [h for h in (_housing(article, headers) for article in articles) if h]
        variants.append({"title": title, "family": family, "housings": housings})
    terminals = terminals_for(spec["terminals"], records)
    return _summary({
        "id": spec["id"], "title": spec["title"], "maker": spec["maker"], "category": spec["category"],
        "variants": variants, "range_checked": bool(spec.get("range_checked", True)), "range_src": spec.get("range_src", ""),
        "datasheet": spec.get("datasheet", ""), "note": spec.get("note", ""), "terminals": terminals,
        "wire": wire_range(terminals), "catalog": [],
        "sources": [{"title": title, "url": url} for title, url in spec.get("sources", ())],
        "contacts": [{"article": article, "wire": wire, "in_base": _flat(article) in known}
                     for article, wire in spec.get("contacts", ())],
    })


# Makers sort the gallery inside a section (owner, 12.10.2026: «по производителю, затем по шагу»); a maker the list
# lacks sorts by name after them, a series without a maker last.
MAKERS = ("JST", "Molex", "Hirose", "JAE", "TE", "Connfly", "Samtec", "Hsuan Mao", "KLS", "Deutsch", "Amass", "Weipu",
          "CNLINKO", "Sumitomo")


def _order(series: Mapping[str, Any]) -> tuple:
    maker = series.get("maker") or ""
    rank = MAKERS.index(maker) if maker in MAKERS else len(MAKERS) + (1 if not maker else 0)
    pitch = series.get("pitch")
    return (CATEGORIES.index(series["category"]), rank, maker.casefold(), pitch if pitch is not None else 1e9,
            str(series["title"]).casefold())


def connector_series(
    terminal_records: Iterable[Mapping[str, Any]] = (), coax_records: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Every series with all its housings (each drawn by `housing_of`), the board headers by contact count, the
    compatible terminals and the wire range they take; each series has one `category`. The list runs by `CATEGORIES`,
    inside one by maker (`MAKERS`), then by pitch: catalogue series (JST sheets, `CATALOG_SERIES`), the families the
    ERP draws without a catalogue table, and the series known only from БД.ТЕР / БД.КОАКС."""
    records = list(terminal_records)
    coax = list(coax_records)
    known = {_flat(fields.get(key)) for fields in records for key in ("terArticle", "terArticleBag") if fields.get(key)}
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for spec in (*(_jst_spec(spec) for spec in JST_SERIES), *CATALOG_SERIES):
        series = _catalog_series(spec, records, known)
        used.update(variant["family"] for variant in series["variants"])
        result.append(series)
    ids = {series["id"] for series in result}
    # A family of a catalogue series whose drawing was not found (Molex CLIK-Mate 502578 beside 502439) keeps the
    # housing the ERP knows; the series then says «диапазон не сверен».
    catalogued = {series["id"]: series for series in result}
    for group in GROUPS:
        series = catalogued.get(group["id"])
        if series is None:
            continue
        for family in FAMILIES:
            if family["id"] in group["families"] and family["id"] not in used:
                housings = [h for h in (_housing(article) for article in _known_articles(family)) if h]
                if housings:
                    series["variants"].append({"title": family["title"], "family": family["id"], "housings": housings})
                    series["range_checked"] = False
                    used.add(family["id"])
        _summary(series)
    groups = {family: group for group in GROUPS for family in group["families"] if group["id"] not in ids}
    by_id: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        if family["id"] in used:
            continue
        group = groups.get(family["id"]) or {
            "id": family["id"], "title": family["title"], "families": (family["id"],), "terminals": (),
            "category": FAMILY_CATEGORY.get(family["id"], WTB),
        }
        if group["id"] in ids:
            continue
        articles = _known_articles(family)
        housings = [h for h in (_housing(article) for article in articles) if h]
        if not housings:
            continue
        series = by_id.get(group["id"])
        if series is None:
            terminals = terminals_for(group["terminals"], records)
            series = by_id[group["id"]] = {
                "id": group["id"], "title": group["title"], "category": group["category"],
                "maker": next((maker for maker in MAKERS if group["title"].startswith(maker + " ")), ""),
                "variants": [], "range_checked": False, "range_src": "", "datasheet": group.get("datasheet", ""),
                "note": "", "terminals": terminals, "wire": wire_range(terminals), "catalog": [],
            }
        series["variants"].append({"title": family["title"], "family": family["id"], "housings": housings})
    for series in by_id.values():
        result.append(_summary(series))
    ids |= set(by_id)
    for series_id, title, maker, category, pic, keys, note in _LOOSE:
        if series_id not in ids:
            result.append(_bare(series_id, title, maker, category, terminals=terminals_for(keys, records), pic=pic, note=note))
    for series_id, title, category, bases in _RF:
        result.append(_bare(series_id, title, "", category, catalog=_rf_catalog(bases, coax), pic="dc-barrel"))
    return sorted(result, key=_order)


def _known_articles(family: Mapping[str, Any]) -> list[str]:
    """Articles of a series without a catalogue table: the TE Superseal table the family already has, else the sample."""
    if family.get("table"):
        return sorted((f"{key}-1" for key in family["table"]), key=lambda article: (family["table"][article[:-2]], article))
    sample = CATALOG_SAMPLES.get(family["id"])
    return [sample] if sample else []


def find_in_series(article: object, series: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The series and housing an article belongs to: exact model number first (separators ignored, so «39012080»
    finds «39-01-2080»), then the series of the family the article is drawn by (a count the catalogue table lacks
    still opens its series)."""
    key = _flat(article)
    layout = housing_of(article)
    family = layout["family"] if layout else None
    fallback = None
    for item in series:
        for variant in item["variants"]:
            for housing in variant["housings"]:
                if _flat(housing["article"]) == key:
                    return {"series": item["id"], "article": housing["article"], "housing": housing["housing"]}
            if family and variant["family"] == family and fallback is None:
                fallback = {"series": item["id"], "article": None, "housing": layout}
    if fallback:
        return fallback
    return {"series": None, "article": None, "housing": layout} if layout else None
