"""Connector-family pin layouts ported from current SUPPLYMAP TcHousing.gs."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping


IVORY = "#efe6cf"
BLACK = "#2b2b2b"
ALIASES = {"LP-12": "LP-12-C02PE"}
# Analogue -> original the housing is drawn from (TcHousing.gs HOUSING_ALIAS_RULES_): BL2018P-nn is an analogue of
# the JST SMP-nnV-BC plug. Applied after ALIASES and before family detection, on the normalised article.
ALIAS_RULES: tuple[tuple[str, str], ...] = ((r"^BL2018P-0*(\d+)", r"SMP-0\1V-BC"),)


def _jst(identifier: str, series: str, pattern: str, pitch: float, latch: str, **extra) -> dict[str, Any]:
    return {
        "id": identifier, "title": f"JST {series}", "pattern": pattern,
        "pitch": pitch, "rows": 1, "order": "row", "start": "TL",
        "latch": latch, "color": IVORY, "conf": "low",
        "note": "по общему правилу JST: со стороны проводов контакт 1 слева",
        **extra,
    }


FAMILIES: tuple[dict[str, Any], ...] = (
    _jst("jst-gh", "GH", r"^GHR-0*(\d+)V-S", 1.25, "ramp", conf="medium"),
    _jst("jst-sh", "SH", r"^SHR-?0*(\d+)V-S", 1.0, "mark", note="ориентир — метка на корпусе; по детали не сверено"),
    _jst("jst-nsh", "NSH", r"^NSHR-0*(\d+)V-S", 1.0, "ramp"),
    _jst("jst-xh", "XH", r"^XHP-(\d+)", 2.5, "ramp", conf="medium"),
    _jst("jst-ph", "PH", r"^PHR-(\d+)", 2.0, "ramp", conf="medium"),
    _jst("jst-eh", "EH", r"^EHR-(\d+)", 2.5, "ramp"),
    _jst("jst-zh", "ZH", r"^ZHR-(\d+)", 1.5, "ramp"),
    _jst("jst-vh", "VH", r"^VHR-(\d+)", 3.96, "lever"),
    _jst("jst-pa", "PA", r"^PAP-0*(\d+)V", 2.0, "lever"),
    _jst("jst-ze", "ZE", r"^ZER-0*(\d+)V", 1.5, "lever"),
    _jst("jst-cz", "CZ", r"^CZHR-0*(\d+)V", 1.5, "ramp"),
    _jst("jst-pni", "PNI", r"^PNIRP-0*(\d+)V", 2.0, "lever"),
    _jst("jst-rcy", "RCY (SY)", r"^SY[RP]-0*(\d+)T", 2.5, "ramp", color="#c8322d"),
    _jst("jst-sm-r", "SM (розетка)", r"^SMR-0*(\d+)V", 2.5, "lever", max=12, start="TL", conf="high", color_by=((r"V-B", BLACK),)),
    _jst("jst-sm-p", "SM (вилка)", r"^SMP-0*(\d+)V", 2.5, "lever", max=12, start="TR", conf="high", color_by=((r"V-B", BLACK),)),
    {"id": "molex-picoblade", "title": "Molex PicoBlade", "pattern": r"^51021-?0?(\d{1,2})00$|^51047-?0?(\d{1,2})00$", "count_groups": (1, 2), "pitch": 1.25, "rows": 1, "order": "row", "start": "TR", "latch": "friction", "color": IVORY, "conf": "medium"},
    {"id": "molex-minifit-jr", "title": "Molex Mini-Fit Jr.", "pattern": r"^39-?01-?2(\d{2})\d$|^5557-(\d{2})R", "count_groups": (1, 2), "pitch": 4.2, "rows": 2, "order": "row", "start": "BR", "latch": "lever", "color": IVORY, "conf": "medium"},
    {"id": "molex-microfit", "title": "Molex Micro-Fit 3.0", "pattern": r"^43025-?(\d{2})0\d$", "pitch": 3.0, "rows": 2, "order": "row", "start": "BR", "latch": "lever", "color": BLACK, "conf": "medium"},
    {"id": "molex-microfit-1r", "title": "Molex Micro-Fit 3.0 (1 ряд)", "pattern": r"^43645-?0?(\d{1,2})00$", "pitch": 3.0, "rows": 1, "order": "row", "start": "TR", "latch": "lever", "color": BLACK, "conf": "low"},
    {"id": "molex-sl-2r", "title": "Molex SL (2 ряда)", "pattern": r"^22-?55-?2(\d{2})1$", "pitch": 2.54, "rows": 2, "order": "col", "start": "TR", "latch": "friction", "color": BLACK, "conf": "low"},
    {"id": "te-multilock-040", "title": "TE Multilock 040", "pattern": r"^1740(?:47|45|46|44)-\d$", "fixed": 20, "pitch": 2.5, "rows": 2, "order": "col", "start": "TR", "latch": "lever", "color": BLACK, "conf": "low"},
    {"id": "connfly-ds1071", "title": "Connfly DS1071 (Dupont 2,54)", "pattern": r"^I-DS1071-SC[BW]0?(\d)X0?(\d{1,2})", "grid": True, "pitch": 2.54, "order": "col", "start": "TR", "latch": "none", "color": BLACK, "conf": "low"},
    {"id": "connfly-ds1069", "title": "Connfly DS1069 (аналог JST XH)", "pattern": r"^I-DS1069-SC[BW]0*(\d+)", "pitch": 2.5, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "conf": "low"},
    {"id": "amass-xt", "title": "Amass XT", "pattern": r"^XT-?(30|60|90)", "fixed": 2, "rows": 1, "order": "row", "start": "TR", "latch": "none", "color": "#f2c200", "cavity": "round", "labels": ("", ""), "conf": "low"},
    {"id": "amass-qs8", "title": "Amass QS8", "pattern": r"^QS8", "fixed": 2, "rows": 1, "order": "row", "start": "TR", "latch": "none", "color": "#d7322d", "cavity": "round", "labels": ("", ""), "conf": "low"},
    {"id": "rj45", "title": "RJ45 8P8C", "pattern": r"^8P8C|^RJ-?45", "fixed": 8, "pitch": 1.02, "rows": 1, "order": "row", "start": "TR", "latch": "lever", "color": "#dfe8ea", "conf": "high"},
    {"id": "weipu-sa8", "title": "Weipu SA8", "pattern": r"^SA81[0125]/([PS])(\d)", "kind": "round", "count_group": 2, "male_group": 1, "round": {2: ((.45, 0), (-.45, 0)), 3: ((-.36, -.22), (0, .43), (.37, -.22)), 4: ((-.32, -.33), (-.32, .32), (.32, .32), (.32, -.33)), 6: ((-.29, -.44), (-.47, .14), (0, .49), (.49, .14), (.31, -.44), (0, 0)), 8: ((-.26, -.55), (-.58, -.13), (-.47, .38), (0, .61), (.47, .38), (.58, -.13), (.26, -.55), (0, 0))}, "color": BLACK, "conf": "high"},
    {"id": "cnlinko-lp12", "title": "CNLINKO LP-12", "pattern": r"^LP-12-[CJ]0?(\d)([PS])", "kind": "round", "count_group": 1, "male_group": 2, "round": {2: ((.36, -.09), (-.33, -.09)), 3: ((.37, -.21), (-.33, -.21), (.03, .41)), 4: ((0, -.4), (.42, 0), (-.4, 0), (0, .41))}, "color": BLACK, "conf": "medium"},
    {"id": "usb-a", "title": "USB-A 2.0", "pattern": r"^USB-?A", "fixed": 4, "rows": 1, "order": "row", "start": "TL", "latch": "none", "color": "#c9ccd0", "labels": ("+5В", "D−", "D+", "GND"), "conf": "low", "note": "назначение по USB 2.0; край контакта 1 — по номерам на колодке вилки"},
    # Series bought for real projects (SUPPLYMAP purchase summary, 30.09.2026) that had no drawing. Numbering of these
    # is by the family's general rule, not checked against each drawing — conf "low" keeps the warning on the sketch.
    # `erp_only`: drawn in the ERP but not in SUPPLYMAP TcHousing.gs, so the warehouse name autofill (Stock05 parity) skips them.
    {"id": "jst-phd", "title": "JST PHD (2 ряда)", "pattern": r"^PHDR-(\d+)VS", "pitch": 2.0, "rows": 2, "order": "col", "start": "TL", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low", "note": "нечётные контакты в верхнем ряду, чётные — в нижнем"},
    {"id": "molex-microfit-plug", "title": "Molex Micro-Fit 3.0 (вилка)", "pattern": r"^43020-?(\d{2})0\d$", "pitch": 3.0, "rows": 2, "order": "row", "start": "BL", "latch": "lever", "color": BLACK, "erp_only": True, "conf": "low"},
    {"id": "molex-microfit-plug-1r", "title": "Molex Micro-Fit 3.0 (вилка, 1 ряд)", "pattern": r"^43640-?0?(\d{1,2})0\d$", "pitch": 3.0, "rows": 1, "order": "row", "start": "TL", "latch": "lever", "color": BLACK, "erp_only": True, "conf": "low"},
    {"id": "molex-clikmate", "title": "Molex CLIK-Mate 1,5", "pattern": r"^502578-?0?(\d{1,2})00$", "pitch": 1.5, "rows": 1, "order": "row", "start": "TR", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low"},
    {"id": "molex-picoclasp", "title": "Molex Pico-Clasp (2 ряда)", "pattern": r"^501189-?(\d{2})10$", "pitch": 1.0, "rows": 2, "order": "col", "start": "TR", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low"},
    {"id": "molex-picoclasp-1r", "title": "Molex Pico-Clasp", "pattern": r"^501330-?0?(\d{1,2})00$", "pitch": 1.0, "rows": 1, "order": "row", "start": "TR", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low"},
    {"id": "molex-milligrid", "title": "Molex Milli-Grid 2,0", "pattern": r"^51110-?(\d{2})\d1$", "pitch": 2.0, "rows": 2, "order": "col", "start": "TL", "latch": "friction", "color": BLACK, "erp_only": True, "conf": "low"},
    {"id": "hirose-df13", "title": "Hirose DF13", "pattern": r"^DF13-(\d+)S-1[.,]25C", "pitch": 1.25, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low"},
    {"id": "hirose-df14", "title": "Hirose DF14", "pattern": r"^DF14-(\d+)S-1[.,]25C", "pitch": 1.25, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low"},
    {"id": "hirose-df11", "title": "Hirose DF11 (2 ряда)", "pattern": r"^DF11-(\d+)DS", "pitch": 2.0, "rows": 2, "order": "col", "start": "TL", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "low"},
    {"id": "jae-fix", "title": "JAE FI-X", "pattern": r"^FI-X(\d+)H", "pitch": 1.0, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": "#b9c0c6", "erp_only": True, "conf": "low"},
    # D-Sub: two staggered rows; the standard numbering is mirrored between the socket and the pin side.
    {"id": "dsub", "title": "D-Sub", "pattern": r"^I?-?DS1033-0?(\d+)([FM])|^D-?SUB-?0?(\d+)([FM])|^D[BE]-?0?(9|15|25)([FMPS])", "kind": "dsub", "count_groups": (1, 3, 5), "gender_groups": (2, 4, 6), "pitch": 2.77, "color": "#c9ccd0", "erp_only": True, "conf": "high", "note": ""},
    {"id": "deutsch-dt", "title": "Deutsch DT", "pattern": r"^DT0([46])-(\d+)([PS])", "kind": "dt", "count_group": 2, "pitch": 4.0, "rows": 2, "order": "row", "start": "TL", "snake": True, "latch": "lever", "color": "#8d9297", "cavity": "round", "erp_only": True, "conf": "low", "note": "нумерация по кругу; сверить с цифрами на корпусе"},
    {"id": "te-superseal", "title": "TE Superseal 1.5", "pattern": r"^(28210[4-8]|28208[0789]|282090)-1$", "table": {"282104": 2, "282105": 3, "282106": 4, "282107": 5, "282108": 6, "282080": 2, "282087": 3, "282088": 4, "282089": 5, "282090": 6}, "pitch": 3.5, "rows": 1, "order": "row", "start": "TL", "latch": "lever", "color": BLACK, "cavity": "round", "erp_only": True, "conf": "low"},
    {"id": "aviation-gx", "title": "Авиационный GX", "pattern": r"^GX(12|16|20)M?-(\d+)", "kind": "round", "count_group": 2, "round_auto": True, "color": "#b9c0c6", "erp_only": True, "conf": "low", "note": "нумерация — по цифрам на изоляторе"},
    {"id": "dc-barrel", "title": "Штекер питания 5,5×2,1", "pattern": r"^DC-?5[.,]5", "kind": "barrel", "fixed": 2, "rows": 1, "color": BLACK, "labels": ("центр", "корпус"), "erp_only": True, "conf": "high"},
    {"id": "lug", "title": "Наконечник", "pattern": r"^(НКИ|НВИ|ПМ|РПИ|РППИ|НШВИ)", "kind": "lug", "conf": "high"},
    {"id": "banana", "title": "Штекер банан 4 мм", "pattern": r"^(BANANA|БАНАН)", "kind": "banana", "color": "#d7322d", "conf": "high"},
)


def normalize_article(value: object) -> str:
    article = str(value or "").strip().upper()
    article = re.sub(r"^(MOLEX|JST|HIROSE|JAE|TE(?:\s+CONNECTIVITY)?|CONNFLY|AMASS|AMP)\s+", "", article)
    return re.sub(r"\s+", "", article)


def housing_grid(rows: int, columns: int, count: int, order: str, start: str, snake: bool = False) -> list[list[int]]:
    grid = [[0 for _ in range(columns)] for _ in range(rows)]
    row_order = list(range(rows)) if start.startswith("T") else list(reversed(range(rows)))
    column_order = list(range(columns)) if start.endswith("L") else list(reversed(range(columns)))
    pin = 1
    axes = column_order if order == "col" else row_order
    for offset, axis in enumerate(axes):
        secondary = row_order if order == "col" else column_order
        if snake and offset % 2:
            secondary = list(reversed(secondary))
        for inner in secondary:
            row, column = (inner, axis) if order == "col" else (axis, inner)
            if pin <= count:
                grid[row][column] = pin
                pin += 1
    return grid


def housing_of(article: object) -> dict[str, Any] | None:
    normalized = normalize_article(article)
    if not normalized:
        return None
    alias = ALIASES.get(normalized)
    if alias:
        normalized = normalize_article(alias)
    for pattern, replacement in ALIAS_RULES:
        if re.search(pattern, normalized):
            normalized = normalize_article(re.sub(pattern, replacement, normalized, count=1))
    for family in FAMILIES:
        match = re.search(family["pattern"], normalized, flags=re.I)
        if not match:
            continue
        rows = int(family.get("rows", 1))
        male = None
        if family.get("kind") == "dsub":
            count = int(next((match[index] for index in family["count_groups"] if match[index]), "0"))
            gender = next((match[index] for index in family["gender_groups"] if match[index]), "F").upper()
            male = gender in {"M", "P"}
            if count not in {9, 15, 25, 37}:
                continue
            grid = dsub_grid(count, male)
            return _layout(article, family, alias, count, 2, len(grid[0]), grid, None, male)
        if family.get("kind") == "dt":
            count = int(match[int(family["count_group"])])
            if count not in DT_LAYOUTS:
                continue
            male = match[1] == "4"
            grid = [list(row) for row in DT_LAYOUTS[count]]
            if male:
                grid = [list(reversed(row)) for row in grid]
            return _layout(article, family, alias, count, len(grid), len(grid[0]), grid, None, male)
        if family.get("table"):
            count = int(family["table"].get(match[1], 0))
            columns = count
        elif family.get("grid"):
            rows, columns = int(match[1]), int(match[2])
            count = rows * columns
        elif family.get("kind") in {"lug", "banana"}:
            rows, columns, count = 1, 1, 1
        elif family.get("fixed"):
            count = int(family["fixed"])
            columns = (count + rows - 1) // rows
        else:
            groups = family.get("count_groups", (family.get("count_group", 1),))
            raw_count = next((match[index] for index in groups if match[index]), "0")
            count = int(raw_count)
            columns = (count + rows - 1) // rows
        if count <= 0 or count > 200 or count > int(family.get("max", 200)):
            continue
        round_layout = None
        if family.get("round_auto"):
            if count < 2 or count > 12:
                continue
            round_layout = [
                {"pin": index + 1, "x": round(.55 * math.sin(2 * math.pi * index / count), 2),
                 "y": round(-.55 * math.cos(2 * math.pi * index / count), 2)}
                for index in range(count)
            ]
            rows = columns = 1
        elif family.get("kind") == "round":
            points = family["round"].get(count)
            if not points:
                continue
            male = match[int(family["male_group"])] == "P"
            round_layout = [
                {"pin": index, "x": -x if male else x, "y": y}
                for index, (x, y) in enumerate(points, start=1)
            ]
            rows = columns = 1
        if family.get("kind") == "round" and family.get("male_group"):
            male = match[int(family["male_group"])] == "P"
        grid = (
            [[1]] if round_layout or family.get("kind") in {"lug", "banana", "barrel"}
            else housing_grid(rows, columns, count, family.get("order", "row"), family.get("start", "TR"), bool(family.get("snake")))
        )
        color = family.get("color", IVORY)
        for pattern, replacement in family.get("color_by", ()):
            if re.search(pattern, normalized):
                color = replacement
        return _layout(article, family, alias, count, rows, columns, grid, round_layout, male, color)
    return None


def _layout(
    article: object, family: Mapping[str, Any], alias: str | None, count: int, rows: int, columns: int,
    grid: list[list[int]], round_layout: list[dict[str, Any]] | None, male: bool | None, color: str | None = None,
) -> dict[str, Any]:
    return {
        "art": str(article or ""), "family": family["id"], "title": family["title"],
        "pitch": family.get("pitch"), "rows": rows, "cols": columns, "n": count,
        "kind": family.get("kind", "grid"), "color": color or family.get("color", IVORY),
        "latch": family.get("latch", "none"), "cavity": family.get("cavity", "square"),
        "labels": list(family["labels"]) if family.get("labels") else None,
        "grid": grid, "round": round_layout, "male": male, "erp_only": bool(family.get("erp_only")),
        "conf": "low" if alias else family.get("conf", "low"),
        "note": f"в наборе артикул без исполнения — нарисован {alias}" if alias else family.get("note", ""),
    }


# Deutsch DT cavity layouts, wire side of the plug (DT06); pins run around the face. The receptacle (DT04) mirrors it.
DT_LAYOUTS: dict[int, tuple[tuple[int, ...], ...]] = {
    2: ((1, 2),), 3: ((1, 0, 2), (0, 3, 0)), 4: ((1, 2), (4, 3)), 6: ((1, 2, 3), (6, 5, 4)),
    8: ((1, 2, 3, 4), (8, 7, 6, 5)), 12: ((1, 2, 3, 4, 5, 6), (12, 11, 10, 9, 8, 7)),
}


def dsub_grid(count: int, male: bool) -> list[list[int]]:
    """D-Sub wire (solder-cup) side on a half-pitch grid: the wide row on top, the narrow row offset by half a pitch.
    On the mating face of the pin side 1 is top-left; the socket side and every wire side mirror that."""
    top = (count + 1) // 2
    columns = top * 2 - 1
    grid = [[0] * columns for _ in range(2)]
    for index in range(top):
        grid[0][index * 2] = index + 1
    for index in range(count - top):
        grid[1][index * 2 + 1] = top + index + 1
    # Wire side of the socket (F) reads 1→ from the left; of the pin side (M) — from the right.
    return [list(reversed(row)) for row in grid] if male else grid


# One representative article per family for the «Разъёмы» gallery (the most bought size where there is one).
CATALOG_SAMPLES: dict[str, str] = {
    "jst-gh": "GHR-04V-S", "jst-sh": "SHR-04V-S-B", "jst-nsh": "NSHR-04V-S", "jst-xh": "XHP-4", "jst-ph": "PHR-4",
    "jst-eh": "EHR-4", "jst-zh": "ZHR-4", "jst-vh": "VHR-4N", "jst-pa": "PAP-04V-S", "jst-ze": "ZER-04V-S",
    "jst-cz": "CZHR-04V-S", "jst-pni": "PNIRP-04V-S", "jst-rcy": "SYR-02T", "jst-sm-r": "SMR-04V-B",
    "jst-sm-p": "SMP-04V-BC", "molex-picoblade": "51021-0400", "molex-minifit-jr": "39012080",
    "molex-microfit": "43025-0400", "molex-microfit-1r": "43645-0400", "molex-sl-2r": "22-55-2081",
    "te-multilock-040": "174047-2", "connfly-ds1071": "I-DS1071-SCB02X5", "connfly-ds1069": "I-DS1069-SCW004",
    "amass-xt": "XT60H-M", "amass-qs8": "QS8-S-M", "rj45": "RJ45 8P8C", "weipu-sa8": "SA812/S4",
    "cnlinko-lp12": "LP-12-C04PE", "usb-a": "USB-A", "jst-phd": "PHDR-14VS", "molex-microfit-plug": "43020-0400",
    "molex-microfit-plug-1r": "43640-0400", "molex-clikmate": "5025780600", "molex-picoclasp": "5011892010",
    "molex-picoclasp-1r": "501330-0600", "molex-milligrid": "51110-1051", "hirose-df13": "DF13-4S-1.25C",
    "hirose-df14": "DF14-4S-1.25C", "hirose-df11": "DF11-8DS-2C", "jae-fix": "FI-X30HL", "dsub": "DS1033-09F",
    "deutsch-dt": "DT06-4S", "te-superseal": "282106-1", "aviation-gx": "GX12M-4", "dc-barrel": "DC5.5x2.1",
    "lug": "НКИ 1.5-4", "banana": "BANANA 4",
}


def connector_catalog() -> list[dict[str, Any]]:
    """Every family drawn once from its sample article — the base the harness scheme and the ETK sketch use."""
    items = []
    for family in FAMILIES:
        sample = CATALOG_SAMPLES.get(family["id"])
        housing = housing_of(sample) if sample else None
        if housing:
            items.append({"family": family["id"], "title": family["title"], "sample": sample, "housing": housing})
    return items


def attach_housings(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = deepcopy(dict(document))
    for operation in result.get("operations") or []:
        if operation.get("type") not in {"insTerm", "prsTerm", "solderConn"}:
            continue
        housing = housing_of(operation.get("connector_article") or operation.get("connector"))
        if housing:
            operation["housing"] = housing
    topology = result.get("topology")
    if isinstance(topology, dict):
        for node in topology.get("nodes") or []:
            housing = housing_of(node.get("conn"))
            if housing:
                node["housing"] = housing
    return result
