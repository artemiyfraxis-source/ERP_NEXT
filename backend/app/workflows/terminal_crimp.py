"""Crimp height and pull force of a БД.ТЕР terminal as the series view shows them (canonical text, one pull column)."""

from __future__ import annotations

from app.modules.references.technology import normalize_crimp_text, parse_crimp_profiles


def _label(high: int, low: int) -> str:
    return f"{high} AWG" if high == low else f"{high}–{low} AWG"


def _range(low: str, high: str) -> str:
    return low if not high or low == high else high if not low else f"{low}–{high}"


def crimp_display(height: object, pull_min: object, pull_max: object, insulation: object = None) -> dict[str, str]:
    """`crimp_height`, `ins_crimp_height` and `pull_force` in the canonical text of `normalize_crimp_text`. Pull
    force «от»/«до» become one range («50–70», or per AWG «24 AWG: 29,3–35»); a «(15 Н)» written inside a height
    profile becomes its pull force when the sheet has no pull columns for that row."""
    return {**_conductor_and_pull(height, pull_min, pull_max), "ins_crimp_height": normalize_crimp_text(insulation)}


def _conductor_and_pull(height: object, pull_min: object, pull_max: object) -> dict[str, str]:
    crimp = normalize_crimp_text(height)
    low, high = normalize_crimp_text(pull_min), normalize_crimp_text(pull_max)
    low_profiles, high_profiles = parse_crimp_profiles(low), parse_crimp_profiles(high)
    if low_profiles or high_profiles:
        by_high = {(p.awg_high, p.awg_low): p.height for p in high_profiles}
        seen = set()
        lines = []
        for p in low_profiles:
            key = (p.awg_high, p.awg_low)
            seen.add(key)
            lines.append(f"{_label(*key)}: {_range(p.height, by_high.get(key, ''))}")
        lines += [f"{_label(*key)}: {value}" for key, value in by_high.items() if key not in seen]
        return {"crimp_height": crimp, "pull_force": "; ".join(lines)}
    pull = _range(low, high)
    profiles = parse_crimp_profiles(crimp)
    if not pull and any(p.pull_force for p in profiles):
        return {
            "crimp_height": "; ".join(f"{_label(p.awg_high, p.awg_low)}: {p.height}" for p in profiles),
            "pull_force": "; ".join(f"{_label(p.awg_high, p.awg_low)}: {p.pull_force}" for p in profiles if p.pull_force),
        }
    return {"crimp_height": crimp, "pull_force": pull}
