"""Pure, norm-owned sweep derivation. Incomplete profiles cannot authorize acquisition."""

from __future__ import annotations

import math
from collections.abc import Mapping

C_M_S = 299_792_458


def _number(value):
    try:
        result = float(value) if not isinstance(value, bool) else math.nan
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def derive_profile(norm: Mapping, *, length_m=None, velocity_factor=None, max_points=None,
                   instrument_band_ghz=None) -> dict:
    problems = []
    band = norm.get("bandMhz")
    if isinstance(band, Mapping):
        band = [band.get("from"), band.get("to")]
    band = [_number(v) for v in band] if isinstance(band, (tuple, list)) and len(band) == 2 else [None, None]
    lower, upper = band
    valid_band = lower is not None and upper is not None and 0 < lower < upper
    if not valid_band:
        problems.append("Не задана полная полоса нормы КСВН")
    limit = _number(norm.get("vswrMax"))
    if norm.get("status") != "ok" or limit is None or limit < 1:
        limit = None
        problems.append("Норма КСВН не определена")
    length, velocity = _number(length_m), _number(velocity_factor)
    if length is None or length <= 0:
        problems.append("Не задана длина кабеля; требуется согласованное число точек")
    if velocity is None or not 0 < velocity <= 1:
        problems.append("Не задан коэффициент укорочения кабеля; требуется согласованное число точек")
    capacity = max_points if type(max_points) is int and 2 <= max_points <= 10_000_001 else None
    if capacity is None:
        problems.append("Не подтверждено максимальное число точек ВАЦ")
    ripple = step = required = points = None
    if valid_band and length is not None and length > 0 and velocity is not None and 0 < velocity <= 1:
        ripple = C_M_S * velocity / (2 * length) / 1e6
        step = ripple / 10
        count = (upper - lower) / step if step > 0 else math.inf
        if not math.isfinite(count) or count > 10_000_000:
            problems.append("Требуемая сетка превышает допустимый объём измерения")
        else:
            required = max(401, math.ceil(count) + 1)
            points = min(required, capacity) if capacity is not None else None
            if points is not None and points < required:
                problems.append(f"ВАЦ поддерживает {points} точек, требуется {required}; полоса не сокращена")
    if instrument_band_ghz is None:
        problems.append("Не подтверждена рабочая полоса ВАЦ")
    elif valid_band:
        low, high = (_number(v) for v in instrument_band_ghz)
        if low is None or high is None or low > lower / 1000 or high < upper / 1000:
            problems.append("Полоса нормы выходит за рабочую полосу ВАЦ")
    return dict(status="blocked" if problems else "ready", problems=problems, band_mhz=band,
                sweep="LIN", points=points, required_points=required, ripple_mhz=ripple,
                target_step_mhz=step, actual_step_mhz=(upper - lower) / (points - 1) if valid_band and points else None,
                if_bandwidth_hz=1000, power_dbm=0, impedance_ohm=50, format="SWR",
                smoothing=False, averaging=False, traces=["S11", "S22"],
                limit_line={"band_mhz": band, "vswr_max": limit}, norm=dict(norm))
