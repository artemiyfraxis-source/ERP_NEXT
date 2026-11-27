"""OUR full stock name of an article (owner 27.11.2026: «непонятно где папа где мама, совершенно разные наименования»).

`build_stock_name` hands the lot's hints to the builder of its item type: connectors, crimp contacts and lugs —
`stock_name_connectors.connector_name`, coax connectors — `stock_name_coax.coax_name`; any other type has no builder
(None: wires, tubes, braids keep the names they have). `resolve_collisions` makes sure two DIFFERENT articles never
end up with one name: the maker is added where it tells them apart, then «вариант <хвост артикула>» for the families
whose articles differ only by a code tail, else the name goes to review («одинаковые данные»).

`infer_article_identity` (ETK, td_auto, «Снабжение») is not touched: this is a separate layer for the stock.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace

from app.modules.warehouse.item_types import canonical_item_type, type_key
from app.shared.article_match import compact
from app.workflows.stock_name_types import LotHints, StockName

CONNECTOR_TYPES = ("Соединитель", "Терминал", "Наконечник")
COAX_TYPES = ("Коаксиальный соединитель",)
_CONNECTOR_KEYS = frozenset(type_key(kind) for kind in CONNECTOR_TYPES)
_COAX_KEYS = frozenset(type_key(kind) for kind in COAX_TYPES)

# Families whose articles differ only by a tail the builder does not decode (Weipu SA810/P4 ↔ SA810/P4IIB, CNLINKO
# LP-12-C02PE-02-001 ↔ …-03-001, Amass XT): the tail tells them apart on a collision.
VARIANT_FAMILIES = ("amass xt", "weipu sa8", "cnlinko lp-12")
SAME_DATA = "одинаковые данные"

Builder = Callable[[LotHints, Sequence[object]], StockName | None]


def has_builder(item_type: object) -> bool:
    """The item type has a full-name builder (connectors, contacts, lugs, coax)."""
    kind = type_key(canonical_item_type(item_type))
    return kind in _CONNECTOR_KEYS or kind in _COAX_KEYS


def is_coax(item_type: object) -> bool:
    return type_key(canonical_item_type(item_type)) in _COAX_KEYS


def _load(module: str, function: str) -> Builder | None:
    """The builder function, imported when first needed; a builder module not merged yet means «no builder»."""
    name = f"app.workflows.{module}"
    try:
        loaded = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name == name:
            return None
        raise
    return getattr(loaded, function)


def build_stock_name(hints: LotHints, records: Iterable[object] = ()) -> StockName | None:
    """The full name by the item type of `hints`, or None when the type has no builder."""
    kind = type_key(canonical_item_type(hints.item_type))
    if kind in _CONNECTOR_KEYS:
        builder = _load("stock_name_connectors", "connector_name")
    elif kind in _COAX_KEYS:
        builder = _load("stock_name_coax", "coax_name")
    else:
        return None
    return None if builder is None else builder(hints, tuple(records))


# ── Collisions ─────────────────────────────────────────────────────────────────────────────────────


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _review(item: StockName) -> StockName:
    return replace(item, complete=False, missing=(SAME_DATA,))


def _with_maker(name: str, maker: str) -> str:
    if not maker or _name_key(name).endswith(_name_key(f", {maker}")):
        return name
    return f"{name}, {maker}"


def _tails(articles: Sequence[str]) -> dict[str, str]:
    """What follows the code tokens all the articles share: SA810/P4 ↔ SA810/P4IIB → «P4», «P4IIB»."""
    tokens = {article: list(re.finditer(r"[0-9A-Za-zА-Яа-яЁё]+", article)) for article in articles}
    common = 0
    while (all(len(found) > common for found in tokens.values())
           and len({found[common].group().casefold() for found in tokens.values()}) == 1):
        common += 1
    return {article: (article[found[common].start():].strip() if len(found) > common else article.strip())
            for article, found in tokens.items()}


def _variant_family(item: StockName) -> bool:
    # The connector builder writes housing ids («amass-xt», «cnlinko-lp12»), people write «Amass XT» (review 27.11):
    # compare letters and digits only.
    family = re.sub(r"[^0-9a-zа-яё]", "", str(item.attrs.get("family") or "").casefold())
    return any(family.startswith(re.sub(r"[^0-9a-zа-яё]", "", prefix)) for prefix in VARIANT_FAMILIES)


def _separate(by_article: Mapping[str, list[str]], result: dict[str, StockName]) -> None:
    """One name shared by several articles (`by_article`: compact key → the spellings of that article)."""
    first = {code: spellings[0] for code, spellings in by_article.items()}
    makers: dict[str, list[str]] = {}
    for code, key in first.items():
        maker = " ".join(str(result[key].attrs.get("maker") or "").split())
        makers.setdefault(maker.casefold(), []).append(code)
    if len(makers) >= 2:  # the maker tells at least some of them apart: it goes into every name of the group
        for code, spellings in by_article.items():
            maker = " ".join(str(result[first[code]].attrs.get("maker") or "").split())
            for key in spellings:
                result[key] = replace(result[key], name=_with_maker(result[key].name, maker))
        rest = [codes for codes in makers.values() if len(codes) >= 2]
    else:
        rest = [list(by_article)]
    rest = [group for codes in rest for group in _split_by_mount(codes, first, by_article, result)]
    for codes in rest:
        if all(_variant_family(result[first[code]]) for code in codes):
            tails = _tails([first[code] for code in codes])
            if len({tail.casefold() for tail in tails.values()}) == len(codes):
                for code in codes:
                    for key in by_article[code]:
                        result[key] = replace(result[key], name=f"{result[key].name}, вариант {tails[first[code]]}")
                continue
        for code in codes:
            for key in by_article[code]:
                result[key] = _review(result[key])


def _split_by_mount(
    codes: list[str], first: Mapping[str, str], by_article: Mapping[str, list[str]], result: dict[str, StockName],
) -> list[list[str]]:
    """Coax of one maker still sharing a name: «обжим» / «пайка» / «прижим» (БД.КОАКС «Монтаж», attrs `mount`) tells
    them apart when it differs (main 27.11). Returns the groups that still share a name."""
    mounts: dict[str, list[str]] = {}
    for code in codes:
        mounts.setdefault(str(result[first[code]].attrs.get("mount") or "").strip().casefold(), []).append(code)
    if len(mounts) < 2:
        return [codes]
    for code in codes:
        mount = str(result[first[code]].attrs.get("mount") or "").strip()
        if mount:
            for key in by_article[code]:
                result[key] = replace(result[key], name=f"{result[key].name}, {mount}")
    return [group for group in mounts.values() if len(group) >= 2]


def _collisions(named: Mapping[str, StockName]) -> dict[str, dict[str, list[str]]]:
    """Complete name → {compact article key → its spellings}, only names shared by two or more articles."""
    by_name: dict[str, dict[str, list[str]]] = {}
    for key, item in named.items():
        if item.complete and item.name.strip():
            by_name.setdefault(_name_key(item.name), {}).setdefault(compact(key) or key, []).append(key)
    return {name: articles for name, articles in by_name.items() if len(articles) >= 2}


def resolve_collisions(named: Mapping[str, StockName]) -> dict[str, StockName]:
    """`named`: article → its built name. Equal complete names of DIFFERENT articles (by `compact`: «XHP-4» and
    «xhp 4» are one article, no collision) are told apart by the maker, else by «вариант <хвост артикула>» (Amass XT,
    Weipu SA8, CNLINKO LP-12 only), else go to review: complete=False, missing=(«одинаковые данные»,)."""
    result = dict(named)
    for articles in _collisions(result).values():
        _separate(articles, result)
    for articles in _collisions(result).values():  # a told-apart name met another one: review, never a guess
        for spellings in articles.values():
            for key in spellings:
                result[key] = _review(result[key])
    return result
