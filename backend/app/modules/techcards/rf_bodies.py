"""Documented RF bodies, independent of supplier articles in БД.КОАКС.

Lengths are millimetres. Each segment shares one source for its four dimensions;
interface dimensions are objects with their own ``src``. Unknown geometry is absent,
not a solid cylinder inferred from the connector's outside dimensions.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


RF_BODIES_DIR = Path(__file__).resolve().parents[3] / "data" / "connectors" / "rf-bodies"
_PARTS = {"nut", "body", "insulator", "contact", "ferrule", "tail", "gasket"}
_INCHES = re.compile(r'inch|дюйм|["″]|\b\d+/\d+\b', re.I)
_SMA_DEPTH = {"male": (0.38, 1.14), "female": (1.88, 2.23)}


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected non-empty text")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{path}: expected a finite millimetre value")
    return value


def _sourced(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: dimension must have src")
    src = _text(value.get("src"), f"{path}.src")
    for key in ("unit", "units"):
        if key in value and value[key] not in {"mm", "мм"}:
            raise ValueError(f"{path}: dimensions must be in millimetres")
    # Thread designations may retain their standard inch name, never inch-valued geometry.
    if _INCHES.search(" ".join(str(item) for item in value.values())) and "пересчитано из дюймов" not in src:
        raise ValueError(f"{path}: inches require 'пересчитано из дюймов' in src")
    return value


def validate_rf_bodies(data: Any) -> dict[str, Any]:
    """Reject unsourced dimensions, invalid geometry and non-MIL SMA recessions."""
    if not isinstance(data, dict):
        raise ValueError("RF bodies: expected an object")
    series = _text(data.get("series"), "series")
    _text(data.get("title"), "title")
    standards = data.get("standard")
    if not isinstance(standards, list) or not standards:
        raise ValueError("standard: expected documents")
    for standard in standards:
        if not isinstance(standard, dict):
            raise ValueError("standard: expected a document object")
        for key in ("doc", "fig", "url"):
            _text(standard.get(key), f"standard.{key}")
    bodies = data.get("bodies")
    if not isinstance(bodies, list):
        raise ValueError("bodies: expected a list")
    ids: set[str] = set()
    for body in bodies:
        if not isinstance(body, dict):
            raise ValueError("body: expected an object")
        identifier = _text(body.get("id"), "body.id")
        if identifier in ids:
            raise ValueError(f"{identifier}: duplicate body id")
        ids.add(identifier)
        gender = body.get("gender")
        if gender not in {"male", "female"}:
            raise ValueError(f"{identifier}: gender must follow the contact (male/female)")
        if _number(body.get("angle"), f"{identifier}.angle") not in {0, 90}:
            raise ValueError(f"{identifier}: angle must be 0 or 90 degrees")
        for key in ("pin_type", "shield_type"):
            _text(body.get(key), f"{identifier}.{key}")
        cables = body.get("cables")
        if not isinstance(cables, list) or not cables:
            raise ValueError(f"{identifier}.cables: expected cable names")
        for cable in cables:
            _text(cable, f"{identifier}.cables")
        segments = body.get("segments")
        if not isinstance(segments, list):
            raise ValueError(f"{identifier}.segments: expected a list")
        for segment in segments:
            segment = _sourced(segment, f"{identifier}.segment")
            if segment.get("part") not in _PARTS:
                raise ValueError(f"{identifier}: unknown segment part")
            for key in ("x_from", "x_to", "r_in", "r_out"):
                _number(segment.get(key), f"{identifier}.{key}")
            if segment["x_from"] >= segment["x_to"] or not 0 <= segment["r_in"] < segment["r_out"]:
                raise ValueError(f"{identifier}: invalid segment bounds")
        interface = body.get("interface")
        if not isinstance(interface, dict):
            raise ValueError(f"{identifier}.interface: expected dimensions")
        for key, dimension in interface.items():
            dimension = _sourced(dimension, f"{identifier}.interface.{key}")
            if key == "thread":
                _text(dimension.get("value"), f"{identifier}.thread.value")
            elif "min" in dimension or "max" in dimension:
                low = _number(dimension.get("min"), f"{identifier}.{key}.min")
                high = _number(dimension.get("max"), f"{identifier}.{key}.max")
                if low < 0 or low > high:
                    raise ValueError(f"{identifier}.{key}: invalid dimension range")
            elif _number(dimension.get("value"), f"{identifier}.{key}.value") < 0:
                raise ValueError(f"{identifier}.{key}: negative dimension")
        depth = interface.get("depth", {})
        if series == "SMA" and (depth.get("min"), depth.get("max")) != _SMA_DEPTH[gender]:
            raise ValueError(f"{identifier}: SMA depth must match MIL-STD-348B")
    return data


def load_rf_bodies(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load every documented series; missing/invalid packaged data fails visibly."""
    root = RF_BODIES_DIR if directory is None else directory
    if not root.is_dir():
        raise FileNotFoundError(f"RF bodies directory is missing: {root}")
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            data = validate_rf_bodies(json.loads(path.read_text(encoding="utf-8")))
            if data["series"] in result:
                raise ValueError(f"duplicate RF series {data['series']}")
            result[data["series"]] = data
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
    return result
