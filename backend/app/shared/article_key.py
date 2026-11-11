"""Additive article comparison policy; never use this to rewrite display or stored keys."""

from __future__ import annotations

import re

from .article_maker import normalize_maker

_SEPARATORS = re.compile(r"[\s\-‐‑‒–—]+")
_NUMERIC = re.compile(r"[0-9]+")
# Numeric families already identified as Molex in connector_housings.py / connector_catalog_data.py.
# Full patterns, not article length or an arbitrary numeric prefix, establish identity.
_FAMILIES = re.compile(
    r"(?:51021[0-9]{2}00|51047[0-9]{2}00|430(?:20|25)[0-9]{2}0[0-9]"
    r"|43640[0-9]{2}0[0-9]|43645[0-9]{2}00|39012[0-9]{2}[0-9]|22552[0-9]{2}1"
    r"|502578[0-9]{2}(?:0[0-9]|1[146])|501189[0-9]{2}1[01]"
    r"|501330[0-9]{2}(?:00|31)|51110[0-9]{2}[56][01]"
    r"|78172[05]00[0-9]|34(?:967|791)[0-9]{4}|39039[0-9]{2}2"
    r"|10530[78][12]2[0-9]{2}|(?:200277|201444)[12]1[0-9]{2}|206461[0-9]{4}"
    r"|1727080[0-2][0-9]{2}|(?:200453|215913)10[0-9]{2}"
    r"|172256[12][01][0-9]{2}|172258[34]1[0-9]{2}|20534102[0-9]{2}"
    r"|(?:504051|503764)[0-9]{2}01|505570[0-9]{2}0[01]"
    r"|(?:505565|214526)[0-9]{2}0[0-9]|2145252[0-9]{3}"
    r"|(?:51065|502351|87439)[0-9]{2}0[01]|50375[0-9]{2}3"
    r"|2127242[0-9]{2}[01]|21409220[0-9]0|2147192[0-9]{2}0"
    r"|2201[23][0-9]{2}[17]|901230[12][0-9]{2}|15119200[0-9]{2}"
    r"|502439[0-9]{2}00|9508[0-9]{3}|51163[0-9]{2}00|505794[0-9]{2}"
    r"|501646[0-9]{2}0[0124]|3502300[0-9]{2})"
)


def is_molex_article(
    value: object, *, manufacturer: object = "", catalog_molex: bool = False,
) -> bool:
    """An explicit different maker vetoes family inference and catalogue evidence."""
    maker = normalize_maker(manufacturer)
    if maker:
        return maker == "MOLEX"
    digits = _SEPARATORS.sub("", str(value if value is not None else "").strip())
    return catalog_molex or bool(_FAMILIES.fullmatch(digits.lstrip("0")))


def article_key(
    value: object, *, manufacturer: object = "", catalog_molex: bool = False,
) -> str:
    """Fold separators/leading zeros only for numeric articles of confirmed Molex origin.

    Other articles, including suffixes, retain their spelling (apart from outer whitespace).
    Callers must also check manufacturer compatibility: the key does not encode a maker.
    Empty and entirely zero numeric articles cannot identify a component.
    """
    article = str(value if value is not None else "").strip()
    digits = _SEPARATORS.sub("", article)
    if not _NUMERIC.fullmatch(digits):
        return article
    if not digits.strip("0"):
        return ""
    if is_molex_article(article, manufacturer=manufacturer, catalog_molex=catalog_molex):
        return digits.lstrip("0")
    return article
