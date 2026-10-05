"""ETK component rows as SUPPLYMAP shows them (TcBuild.gs kitUseArt_, etkPartRow_, etkAttachParts_, owner 28.09).

A row is the article to apply / the name by the warehouse article rules / the KD article when it differs / the GRN
only from a lot binding. The estimator writes the original over a KD analogue in the kit name (533К.11: article
BL2018TS, name SHF-001T-0.8BS), so a code in the name that differs from the article is the article to apply. Applied
on read, so issued ETKs get it without a rebuild; the KD analogue is replaced in operation headers and harness nodes.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

_CYRILLIC = re.compile(r"[а-яё]", re.I)


def use_article(art: object, title: object) -> dict[str, str]:
    """`kitUseArt_`: a code in the name that differs from the article is the article to apply, the article is the KD
    one; Russian text with spaces is a name, not a code."""
    a, t = str(art or "").strip(), str(title or "").strip()
    is_code = bool(t) and t != a and not (_CYRILLIC.search(t) and re.search(r"\s", t)) and not re.search(r"\s{2,}", t)
    return {"art": t, "kd": a} if is_code and a else {"art": a or t, "kd": ""}


# A supplier-page title or a written-out description, not another designation: brackets, quotes, sentence marks.
_DESCRIPTION = re.compile(r"[()\[\]{}«»\"!?;:]|\.(?:\s|$)")
_DESIGNATION_WORDS = 6


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold().replace("ё", "е").replace(",", ".")


def kit_article(art: object, title: object) -> dict[str, str]:
    """The item a kit row actually uses (owner, 05.10: «Наименование» is what is used) and the KD article it replaced.

    `use_article` covers a code in the name (533К.11). A worded КД article — a wire «Силиконовый 28AWG черный», no
    part number — is replaced the same way by a worded name of the same kind: «Силиконовый 30AWG черный» (the
    estimator changed the wire in the calc). A name that only describes the article is not a replacement: it repeats
    the article, reads as a title (brackets, quotes, sentence marks — «Провод МК 26-13 (цветной МГТФ)») or is longer
    than a designation. A part-number article keeps its descriptive name as a name («Колодка 4pin» over GHR-04V-S).
    Used for the composition only (generator, supply); issued ETKs keep the article they were built with."""
    use = use_article(art, title)
    a, t = str(art or "").strip(), str(title or "").strip()
    if use["kd"] or not a or not t or not (_CYRILLIC.search(a) and re.search(r"\s", a)):
        return use
    same = _squash(a) in _squash(t)
    if same or _DESCRIPTION.search(t) or len(t.split()) > _DESIGNATION_WORDS:
        return use
    return {"art": t, "kd": a}


def part_row(row: Mapping[str, Any], identify: Callable[[str], str] | None = None) -> dict[str, Any]:
    """`etkPartRow_`: the name comes from the warehouse article rules, otherwise the kit name when it is text and not a
    repeat of the article (a wire «UL1061 30AWG белый» has name = article); GRN never comes from the name. `part`
    marks a row that is already converted, so a second pass changes nothing."""
    if row.get("part"):
        return dict(row)
    name = str(row.get("name") or "").strip()
    use = use_article(row.get("art"), name)
    by_article = identify(use["art"]) if identify and use["art"] else ""
    text = name if _CYRILLIC.search(name) and re.search(r"\s", name) and name != use["art"] else ""
    return {**row, "art": use["art"], "kd": use["kd"], "name": by_article or text,
            "grn": str(row.get("grn") or ""), "part": 1}


def attach_parts(document: Mapping[str, Any] | None, identify: Callable[[str], str] | None = None) -> dict[str, Any] | None:
    """`etkAttachParts_`: component rows through `part_row`; a KD article is replaced by the article to apply in the
    operation connector and in the harness nodes."""
    if document is None:
        return None
    result = deepcopy(dict(document))
    use: dict[str, str] = {}
    for operation in result.get("operations") or []:
        rows = []
        for row in operation.get("components") or []:
            converted = part_row(row, identify) if isinstance(row, Mapping) else row
            if isinstance(converted, Mapping) and converted.get("kd"):
                use[str(converted["kd"])] = str(converted["art"])
            rows.append(converted)
        if "components" in operation:
            operation["components"] = rows
    if use:
        for operation in result.get("operations") or []:
            for key in ("connector", "connector_article"):
                if operation.get(key) in use:
                    operation[key] = use[operation[key]]
        topology = result.get("topology")
        if isinstance(topology, dict):
            for node in topology.get("nodes") or []:
                if isinstance(node, dict) and node.get("conn") in use:
                    node["conn"] = use[node["conn"]]
    return result
