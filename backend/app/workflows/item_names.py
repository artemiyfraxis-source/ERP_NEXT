"""«Наименование» of a project position on «Снабжение» (owner, 12.10.2026: «как-то плохо работает подтягивание с сайтов
местами» — XHP-4 shown «—» though ERP knows the JST XH housing, ПВАМ 0,35 красный shown «Автомобильный провод ПВАМ
(медный). Продажа от 5 м!»).

The name is resolved when the list is read, never written into the spec: the stored «Наименование» keeps what the
calculation, a person or a supplier page put there (`kit_article` reads it as the article actually used, and a person's
edit stays stronger than any lookup). Order, first that knows the item:

1. ERP's own references — connector housings and series, «Терминалы» (БД.ТЕР), coax (БД.КОАКС) through the warehouse
   article rules (`infer_article_identity`); hookup wires and cables (БД.ПРОВ: series + section + colour → «Провод ПВАМ
   0,35 мм² красный»); coverings and other materials (БД.МАТ: ТУТ/ТТК with Ø and colour). A row whose «Наименование»
   replaces the КД article (`kit_article`) is looked up by the article actually used.
2. A name typed in the spec without a supplier link (the calculation's or a person's).
3. The warehouse lot of the same article (the same letters and digits — `article_match.compact`).
4. The supplier-page title stored by «добор наименований», cleaned (`clean_site_title`); the raw title stays in the
   tooltip.

Nothing found → «—» and a hint saying why (not in the references, not on the warehouse, what the site gave).

    ERP_DATABASE_URL=... python -m app.workflows.item_names [--project 708К.4]

prints, per position, the stored name and the one the screen shows with its source — read-only (a dry run by nature:
the names are not stored, so there is nothing to apply).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.modules.references.domain import TechOperationTab
from app.modules.techcards.connector_housings import housing_of
from app.modules.techcards.etk_parts import kit_article
from app.modules.techcards.kitset_names import clean_site_title
from app.shared.article_match import compact
from app.workflows.warehouse_article import infer_article_identity

REF, SPEC, STOCK, SITE = "ref", "spec", "stock", "site"


class _Record(Protocol):
    tab: Any
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class ItemName:
    name: str
    source: str  # ref | spec | stock | site | "" — nothing found
    hint: str


def _tab(record: _Record) -> str:
    tab = record.tab
    return tab.value if isinstance(tab, TechOperationTab) else str(tab)


def _field(fields: Mapping[str, Any], *names: str) -> str:
    return next((str(fields.get(name) or "").strip() for name in names if str(fields.get(name) or "").strip()), "")


def _number(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _shown(value: Decimal) -> str:
    """3,0 → «3», 1.50 → «1,5»: the size as people write it."""
    text = format(value.normalize(), "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return text.replace(".", ",")


def _stem(colour: str) -> str:
    return colour.casefold().replace("ё", "е")[:4]


_CONTACT_SEX = {"female": "гнездо", "male": "штырь"}
_SIZE = r"(\d+(?:[.,]\d+)?)"
_WIRE_SIZE = re.compile(rf"^{_SIZE}\s*(awg|мм²|мм2|мм|mm²|mm2|mm)?$", re.I)
_COLOUR_WORD = re.compile(r"^[а-яё]+(?:-[а-яё]+)?\.?$", re.I)
_COVERING = re.compile(rf"^(\S+?)\s*{_SIZE}\s*/\s*{_SIZE}\s*(?:мм|mm)?\.?,?\s*(.*)$", re.I)
_CABLE_WORDS = re.compile(r"кабел|витая|шнур|коакс", re.I)
_NAMED = re.compile(r"провод|кабель|шнур", re.I)
# Heat-shrink groups of БД.МАТ: ТУТ — без клея, ТТК — с клеевым слоем.
_HEAT_SHRINK = frozenset({"тут", "ттк"})


@dataclass(frozen=True)
class _Wire:
    mark: str
    series: str  # compact
    size: Decimal | None
    awg: bool
    kind: str  # «Провод» | «Кабель» | ""


class ReferenceNames:
    """ERP's references as names: built once per request from the reference records (БД.ТЕР, БД.КОАКС, БД.ПРОВ, БД.МАТ)."""

    def __init__(self, records: Iterable[_Record] = ()):
        records = tuple(records)
        self._identity_records = tuple(
            record for record in records if _tab(record) in (TechOperationTab.TERMINAL.value, TechOperationTab.COAX.value)
        )
        self._makers = {
            compact(_field(record.fields, "terArticle", "article")): _field(record.fields, "terManufacturer")
            for record in records if _tab(record) == TechOperationTab.TERMINAL.value
        }
        self._wires = tuple(self._wire(record.fields) for record in records if _tab(record) == TechOperationTab.WIRE.value)
        self._wires = tuple(wire for wire in self._wires if wire.mark)
        self._materials = tuple(record.fields for record in records if _tab(record) == TechOperationTab.MATERIAL.value)
        self._covering_groups = {
            _field(fields, "Группа").casefold() for fields in self._materials if _field(fields, "Размер до, мм")
        } & _HEAT_SHRINK

    @staticmethod
    def _wire(fields: Mapping[str, Any]) -> _Wire:
        mark = _field(fields, "Марка")
        kind_text = _field(fields, "Тип провода")
        kind = "" if _NAMED.search(mark) else "Кабель" if _CABLE_WORDS.search(kind_text) else "Провод"
        parts = mark.rsplit(" ", 1)
        size = _WIRE_SIZE.match(parts[1]) if len(parts) == 2 else None
        if size:
            return _Wire(mark, compact(parts[0]), _number(size[1]), (size[2] or "").casefold() == "awg", kind)
        return _Wire(mark, compact(mark), None, False, kind)

    def name(self, article: object) -> str:
        """The item's name by ERP's references; '' — they do not know it."""
        text = " ".join(str(article or "").split())
        if not text:
            return ""
        return self._covering(text) or self._wire_name(text) or self._material(text) or self._connector(text)

    # ── connectors, contacts, coax (the warehouse article rules) ──
    def _connector(self, article: str) -> str:
        identity = infer_article_identity(article, self._identity_records)
        if identity is None or not identity.name:
            return ""
        if identity.item_type == "Терминал":
            maker = self._makers.get(compact(article), "")
            sex = next((word for key, word in _CONTACT_SEX.items() if key in identity.name.casefold()), "")
            series = identity.series if not maker or maker.casefold() in identity.series.casefold() else f"{maker} {identity.series}"
            return " ".join(part for part in ("Контакт обжимной", series, sex) if part)
        if identity.source == "семейство корпуса":
            housing = housing_of(article)
            if housing:
                title = re.sub(r"\s*\(.*\)$", "", housing["title"]).strip()
                paren = re.search(r"\((вилка|розетка)\)", housing["title"])
                male = housing.get("male")
                sex = paren[1] if paren else "вилка" if male is True else "розетка" if male is False else ""
                kind = "Разъём" if housing["kind"] in ("round", "dsub", "dt") else "Корпус"
                rows = ", двухрядный" if housing.get("rows") == 2 and housing["kind"] == "grid" else ""
                return " ".join(part for part in (kind, title, sex, f"{housing['n']} конт.") if part) + rows
        return identity.name

    # ── hookup wires and cables (БД.ПРОВ) ──
    def _wire_name(self, article: str) -> str:
        # A comma between words separates them; inside a number («0,35») it is the decimal comma.
        tokens = [token.strip(",") for token in re.split(r"[\s;]+", article) if token.strip(",")]
        if len(tokens) >= 2:
            size = _WIRE_SIZE.match(tokens[1].rstrip("."))
            if size:
                value, awg = _number(size[1]), (size[2] or "").casefold() == "awg"
                series = compact(tokens[0])
                for wire in self._wires:
                    if wire.size is not None and wire.series == series and wire.size == value and wire.awg == awg:
                        colour = " ".join(token.rstrip(".") for token in tokens[2:] if _COLOUR_WORD.match(token))
                        return self._wire_text(wire, colour)
        whole = compact(article)
        exact = [wire for wire in self._wires if compact(wire.mark) == whole]
        if exact:
            return self._wire_text(exact[0], "")
        # «U/UTP 2x2x0,48» ↔ «ParLan Patch U/UTP Cat5e 2x2x0,48»: every word of the article inside one mark.
        words = [compact(token) for token in tokens if compact(token)]
        if len(whole) < 4 or not any(char.isdigit() for char in whole):
            return ""
        inside = sorted(
            (wire for wire in self._wires if all(word in compact(wire.mark) for word in words)),
            key=lambda wire: len(wire.mark),
        )
        return self._wire_text(inside[0], "") if inside else ""

    @staticmethod
    def _wire_text(wire: _Wire, colour: str) -> str:
        section = " мм²" if wire.size is not None and not wire.awg else ""
        return " ".join(part for part in (wire.kind, f"{wire.mark}{section}", colour) if part)

    # ── coverings (БД.МАТ: ТУТ/ТТК with Ø and colour) ──
    def _covering(self, article: str) -> str:
        found = _COVERING.match(article)
        if not found:
            return ""
        series, before, after, rest = found[1].rstrip(","), _number(found[2]), _number(found[3]), found[4]
        group = next((group for group in self._covering_groups if series.casefold().startswith(group)), "")
        if not group or before is None or after is None:
            return ""
        colour = next((word.rstrip(".,") for word in rest.split() if _COLOUR_WORD.match(word.rstrip(","))), "")
        maker = ""
        for fields in self._materials:
            if (
                _field(fields, "Группа").casefold() == group
                and _number(_field(fields, "Размер до, мм")) == before
                and _number(_field(fields, "Размер после, мм")) == after
                and (not colour or _stem(_field(fields, "Цвет")) == _stem(colour))
                and compact(_field(fields, "Наименование").split(" ")[0]) == compact(series)
            ):
                maker = _field(fields, "Производитель")
                break
        glue = " клеевая" if group == "ттк" else ""
        text = f"Трубка термоусадочная{glue} {series} {_shown(before)}/{_shown(after)} мм"
        return text + (f", {colour}" if colour else "") + (f" ({maker})" if maker else "")

    # ── other materials: the same name in БД.МАТ ──
    def _material(self, article: str) -> str:
        whole = compact(article)
        if len(whole) < 4:
            return ""
        return next((
            _field(fields, "Наименование") for fields in self._materials
            if compact(_field(fields, "Наименование")) == whole
        ), "")


@dataclass(frozen=True)
class StockName:
    article: str
    name: str
    grn: str = ""


class StockNames:
    """Warehouse lots by the letters and digits of their article (hyphens, spaces, case, Cyrillic twins aside)."""

    def __init__(self, lots: Iterable[Any] = ()):
        self._by_key: dict[str, StockName] = {}
        for lot in lots:
            key = compact(getattr(lot, "article", ""))
            name = " ".join(str(getattr(lot, "name", "") or "").split())
            if key and name and compact(name) != key and key not in self._by_key:
                self._by_key[key] = StockName(article=str(lot.article), name=name, grn=str(getattr(lot, "grn", "") or ""))

    def get(self, article: object) -> StockName | None:
        key = compact(article)
        return self._by_key.get(key) if key else None


def site_domain(link: object) -> str:
    try:
        host = urlsplit(str(link or "").strip()).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_link(link: str) -> bool:
    return bool(re.match(r"^https?://", link.strip(), re.I))


def resolve_name(
    article: object, stored: object, link: object, references: ReferenceNames | None, stock: StockNames | None,
) -> ItemName:
    """The name a position shows and where it comes from (see the module docstring for the order)."""
    article = str(article or "").strip()
    stored = " ".join(str(stored or "").split())
    link = str(link or "").strip()
    used = kit_article(article, stored)
    replaced = bool(used["kd"])
    key = used["art"] if replaced else article
    via = f" по применяемому артикулу {key}" if replaced else ""
    if references is not None:
        found = references.name(key)
        if found:
            return ItemName(found, REF, f"Из справочника ERP{via}")
    if stored and (replaced or not _is_link(link)):
        return ItemName(stored, SPEC, "Из спецификации проекта" + (" (замена артикула КД)" if replaced else ""))
    lot = stock.get(key) if stock is not None else None
    if lot is not None:
        return ItemName(lot.name, STOCK, f"Со склада: партия {lot.grn} ({lot.article})" if lot.grn else f"Со склада ({lot.article})")
    domain = site_domain(link)
    if stored:
        cleaned = clean_site_title(stored) or stored
        hint = f"С сайта {domain}" if domain else "С сайта поставщика"
        return ItemName(cleaned, SITE, hint + (f"; на сайте: «{stored}»" if cleaned != stored else ""))
    reasons = ["нет в справочниках ERP", "на складе не найдено"]
    reasons.append(f"сайт {domain} не отдал название" if domain else "ссылки на сайт нет")
    return ItemName("", "", "Артикул неизвестен: " + ", ".join(reasons))


def reference_records(references_uow) -> list[Any]:
    """The reference records the names need (БД.ТЕР, БД.КОАКС, БД.ПРОВ, БД.МАТ); no factory → []."""
    if references_uow is None:
        return []
    with references_uow() as uow:
        return [
            record
            for tab in (TechOperationTab.TERMINAL, TechOperationTab.COAX, TechOperationTab.WIRE, TechOperationTab.MATERIAL)
            for record in uow.tech_operations.list(tab=tab, query="", limit=10_000)
        ]


def name_report(rows: Sequence[tuple[str, str, str, str]], references: ReferenceNames, stock: StockNames) -> list[str]:
    """(project, article, stored name, link) → one line per position: what is stored and what the screen shows."""
    labels = {REF: "справочник", SPEC: "спецификация", STOCK: "склад", SITE: "сайт", "": "—"}
    lines = []
    for project, article, stored, link in rows:
        shown = resolve_name(article, stored, link, references, stock)
        changed = "=" if shown.name == " ".join(stored.split()) else "→"
        lines.append(
            f"{project}\t{article}\t{stored or '—'}\t{changed}\t{shown.name or '—'}\t{labels[shown.source]}\t{shown.hint}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    """Read-only: `python -m app.workflows.item_names [--project N]` — per spec position, stored → shown name."""
    import argparse
    import os
    import sys

    from sqlalchemy.orm import Session, sessionmaker

    from app.database import build_engine
    from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
    from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
    from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--project", default="", help="только этот проект")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    engine = build_engine(url)
    try:
        sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        references = ReferenceNames(reference_records(lambda: SqlAlchemyReferencesUnitOfWork(sessions)))
        with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
            stock = StockNames(uow.warehouse.list(limit=10_000, query="", include_zero=False))
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            kitsets = [uow.kitsets.get(args.project)] if args.project else list(uow.kitsets.list())
        rows, seen = [], set()
        for kitset in kitsets:
            if kitset is None:
                continue
            for assembly in kitset.assemblies:
                for component in assembly.components:
                    key = (kitset.project, " ".join(component.article.split()).casefold())
                    if not component.article.strip() or key in seen:
                        continue
                    seen.add(key)
                    rows.append((kitset.project, component.article, component.name, component.link))
        lines = name_report(rows, references, stock)
        print("Проект\tАртикул КД\tВ спецификации\t\tНа экране\tИсточник\tПодсказка")
        print("\n".join(lines))
        sources = [line.split("\t")[5] for line in lines]
        print(
            f"Позиций: {len(lines)} · справочник {sources.count('справочник')} · спецификация {sources.count('спецификация')}"
            f" · склад {sources.count('склад')} · сайт {sources.count('сайт')} · без имени {sources.count('—')}"
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
