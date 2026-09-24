"""Connector-family pin layouts ported from current SUPPLYMAP TcHousing.gs."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping


IVORY = "#efe6cf"
BLACK = "#2b2b2b"
ALIASES = {"LP-12": "LP-12-C02PE"}


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
    _jst("jst-sh", "SH", r"^SHR-0*(\d+)V-S", 1.0, "mark", note="ориентир — метка на корпусе; по детали не сверено"),
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
    {"id": "connfly-ds1069", "title": "Connfly DS1069 (аналог JST SM)", "pattern": r"^I-DS1069-SC[BW]0*(\d+)", "pitch": 2.5, "rows": 1, "order": "row", "start": "TR", "latch": "lever", "color": IVORY, "conf": "low"},
    {"id": "amass-xt", "title": "Amass XT", "pattern": r"^XT-?(30|60|90)", "fixed": 2, "rows": 1, "order": "row", "start": "TR", "latch": "none", "color": "#f2c200", "cavity": "round", "labels": ("", ""), "conf": "low"},
    {"id": "amass-qs8", "title": "Amass QS8", "pattern": r"^QS8", "fixed": 2, "rows": 1, "order": "row", "start": "TR", "latch": "none", "color": "#d7322d", "cavity": "round", "labels": ("", ""), "conf": "low"},
    {"id": "rj45", "title": "RJ45 8P8C", "pattern": r"^8P8C", "fixed": 8, "pitch": 1.02, "rows": 1, "order": "row", "start": "TR", "latch": "lever", "color": "#dfe8ea", "conf": "high"},
    {"id": "weipu-sa8", "title": "Weipu SA8", "pattern": r"^SA81[0125]/([PS])(\d)", "kind": "round", "count_group": 2, "male_group": 1, "round": {2: ((.45, 0), (-.45, 0)), 3: ((-.36, -.22), (0, .43), (.37, -.22)), 4: ((-.32, -.33), (-.32, .32), (.32, .32), (.32, -.33)), 6: ((-.29, -.44), (-.47, .14), (0, .49), (.49, .14), (.31, -.44), (0, 0)), 8: ((-.26, -.55), (-.58, -.13), (-.47, .38), (0, .61), (.47, .38), (.58, -.13), (.26, -.55), (0, 0))}, "color": BLACK, "conf": "high"},
    {"id": "cnlinko-lp12", "title": "CNLINKO LP-12", "pattern": r"^LP-12-[CJ]0?(\d)([PS])", "kind": "round", "count_group": 1, "male_group": 2, "round": {2: ((.36, -.09), (-.33, -.09)), 3: ((.37, -.21), (-.33, -.21), (.03, .41)), 4: ((0, -.4), (.42, 0), (-.4, 0), (0, .41))}, "color": BLACK, "conf": "medium"},
    {"id": "usb-a", "title": "USB-A 2.0", "pattern": r"^USB-?A", "fixed": 4, "rows": 1, "order": "row", "start": "TL", "latch": "none", "color": "#c9ccd0", "labels": ("+5В", "D−", "D+", "GND"), "conf": "low", "note": "назначение по USB 2.0; край контакта 1 — по номерам на колодке вилки"},
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
    for family in FAMILIES:
        match = re.search(family["pattern"], normalized, flags=re.I)
        if not match:
            continue
        rows = int(family.get("rows", 1))
        if family.get("grid"):
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
        if family.get("kind") == "round":
            points = family["round"].get(count)
            if not points:
                continue
            male = match[int(family["male_group"])] == "P"
            round_layout = [
                {"pin": index, "x": -x if male else x, "y": y}
                for index, (x, y) in enumerate(points, start=1)
            ]
            rows = columns = 1
        color = family.get("color", IVORY)
        for pattern, replacement in family.get("color_by", ()):
            if re.search(pattern, normalized):
                color = replacement
        return {
            "art": str(article or ""), "family": family["id"], "title": family["title"],
            "pitch": family.get("pitch"), "rows": rows, "cols": columns, "n": count,
            "kind": family.get("kind", "grid"), "color": color,
            "latch": family.get("latch", "none"), "cavity": family.get("cavity", "square"),
            "labels": list(family["labels"]) if family.get("labels") else None,
            "grid": [[1]] if round_layout or family.get("kind") in {"lug", "banana"} else housing_grid(rows, columns, count, family.get("order", "row"), family.get("start", "TR"), bool(family.get("snake"))),
            "round": round_layout,
            "conf": "low" if alias else family.get("conf", "low"),
            "note": f"в наборе артикул без исполнения — нарисован {alias}" if alias else family.get("note", ""),
        }
    return None


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
