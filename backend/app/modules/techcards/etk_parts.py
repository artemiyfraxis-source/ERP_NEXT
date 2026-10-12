"""ETK component rows as SUPPLYMAP shows them (TcBuild.gs kitUseArt_, etkPartRow_, etkAttachParts_, owner 28.09).

A row is the article to apply / the name by the warehouse article rules / the KD article when it differs / the GRN
only from a lot binding. The estimator writes the original over a KD analogue in the kit name (533К.11: article
BL2018TS, name SHF-001T-0.8BS), so a code in the name that differs from the article is the article to apply. Applied
on read, so issued ETKs get it without a rebuild; the KD analogue is replaced in operation headers and harness nodes.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
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


@dataclass(frozen=True)
class EtkLot:
    """A lot bound to a position of the project КБ: the kit line's article and name (the КД article and the name the
    item actually used is taken from, `kit_article`), its ГРН and ПАКЕТ."""

    article: str
    name: str
    grn: str
    package: str = ""


@dataclass(frozen=True)
class EtkLots:
    """The current lot bindings of the ETK's project + КБ (owner, 12.10.2026: «после того как Алексей привяжет к позиции
    ГРН, нужно чтобы это синкалось в техкарте»). `issued` — lots actually handed to the shop (kit request and its
    «Довыдача»); `kitset` — the lots chosen in the project's «Состав». The first one that has the row's article wins."""

    issued: tuple[EtkLot, ...] = ()
    kitset: tuple[EtkLot, ...] = ()


def _article_keys(*values: object) -> frozenset[str]:
    return frozenset(key for key in (_squash(str(value or "")) for value in values) if key)


def _lot_keys(lot: EtkLot) -> frozenset[str]:
    return _article_keys(lot.article, kit_article(lot.article, lot.name)["art"])


def lots_text(lots: Sequence[EtkLot]) -> str:
    """«ГРН1, ГРН2» in the binding order, each ГРН once; the ПАКЕТ is named only when one ГРН came in different
    packages («ГРН1 · 1, ГРН1 · 2») — a single package of a ГРН tells the fitter nothing more."""
    packages: dict[str, list[str]] = {}
    for lot in lots:
        grn = lot.grn.strip()
        if grn:
            seen = packages.setdefault(grn, [])
            if lot.package.strip() not in seen:
                seen.append(lot.package.strip())
    parts: list[str] = []
    for grn, seen in packages.items():
        parts.extend([grn] if len(seen) < 2 else [f"{grn} · {package}" if package else grn for package in seen])
    return ", ".join(parts)


def _bound_grn(row: Mapping[str, Any], lots: Sequence[tuple[frozenset[str], EtkLot]]) -> str:
    keys = _article_keys(row.get("art"), row.get("kd"))
    return lots_text([lot for lot_keys, lot in lots if lot_keys & keys]) if keys else ""


def attach_parts(
    document: Mapping[str, Any] | None,
    identify: Callable[[str], str] | None = None,
    lots: EtkLots | None = None,
) -> dict[str, Any] | None:
    """`etkAttachParts_`: component rows through `part_row`; a KD article is replaced by the article to apply in the
    operation connector and in the harness nodes. With `lots`, a row's ГРН is the current binding of its article (the
    article to apply or the КД one, either way round): issued lots first, else the «Состав» lot, else the snapshot."""
    if document is None:
        return None
    result = deepcopy(dict(document))
    use: dict[str, str] = {}
    issued = [(_lot_keys(lot), lot) for lot in lots.issued] if lots else []
    kitset = [(_lot_keys(lot), lot) for lot in lots.kitset] if lots else []
    for operation in result.get("operations") or []:
        rows = []
        for row in operation.get("components") or []:
            converted = part_row(row, identify) if isinstance(row, Mapping) else row
            if isinstance(converted, Mapping) and converted.get("kd"):
                use[str(converted["kd"])] = str(converted["art"])
            if isinstance(converted, dict) and (issued or kitset):
                grn = _bound_grn(converted, issued) or _bound_grn(converted, kitset)
                if grn:
                    converted["grn"] = grn
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
