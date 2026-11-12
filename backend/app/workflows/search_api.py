"""Rights-filtered global search with at most eight results per group.

New SQL groups match all words before LIMIT; specialized legacy ranking streams a bounded candidate set.
A result names its place and carries the section and query parameters that the destination consumes.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import date
from functools import lru_cache
from collections.abc import Callable, Iterable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, and_, cast, event, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.references.coverings import classify as classify_covering
from app.modules.references.infrastructure import CoaxCableRow, EquipmentModelRow, HookupWireRow, ReferenceEntryRow, TechOperationRecordRow, WireGaugeRow
from app.modules.shipping.infrastructure import FinishedGoodRow, ShipmentLineRow, ShipmentRow
from app.modules.techcards.infrastructure import OperationCardTemplateRow, ProjectTechcardRow
from app.modules.techcards.connector_series import connector_series
from app.modules.purchasing.deps import purchasing_uow_factory
from app.modules.purchasing.infrastructure import (
    InternalProjectRow, InvoiceRow, ItemAliasRow, ItemRow, RfqRow, SupplierRow, UpdDocumentRow,
)
from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
from app.modules.warehouse.infrastructure import StockLotRow
from app.modules.warehouse.word_search import _FOLD, fold, matches_words
from app.shared import sqlite_casefold  # noqa: F401 — erp_casefold() on SQLite connections
from app.shared.http_auth import Principal, authenticated_principal, section_allowed

router = APIRouter(prefix="/api/search", tags=["search"])

PER_GROUP = 8
MAX_CANDIDATES = 2000
MAX_QUERY = 120
# A GRN: letters, year, serial, optional package — «PPM.26.0000001», «FRX.26.0000003-П2».
GRN_LIKE = re.compile(r"^[a-zа-яё]{2,6}\.\d{2}\.\d{3,}(?:[-\s]\S{1,6})?$", re.IGNORECASE)

_AMBIGUOUS = {chr(code) for code in _FOLD} | set(_FOLD.values()) | {"ё", "е"}

G_LINES, G_PROJECTS, G_ITEMS = "Строки закупки", "Проекты", "Номенклатура"
G_SUPPLIERS, G_STOCK, G_RFQ = "Поставщики", "Склад", "Запросы КП"
G_DB, G_SHIPMENTS, G_GOODS = "База данных", "Отгрузки", "Готовая продукция"
G_TECHCARDS, G_TASKS, G_UPD, G_INVOICES, G_STAFF = "Техкарты", "Задания цеха", "Реестр УПД", "Счета", "Сотрудники"
G_EQUIPMENT = "Оборудование"
G_KITS = "Комплектация и выдача"
# The stages of the two screens of one request (FulfillmentPage MODES): picking, then handing out; closed — archive.
_PICK_STAGES = frozenset({"Новая", "Недоукомплектован", "Укомплектован", "Готов к выдаче"})
_GIVE_STAGES = frozenset({"КБ Выдана", "КБ Возвращена"})
_CLOSED_STAGES = frozenset({"Готово", "Отменена"})


def _result(group: str, title: str, subtitle: str, section: str, **params: str) -> dict[str, Any]:
    return {"group": group, "title": title, "subtitle": subtitle, "section": section, "params": params}


def _prefilter(query: str) -> list[str]:
    """LIKE patterns for the most selective word. Look-alikes and ё/е become `_` (any one character), a Cyrillic
    the pattern is casefolded and compared case-insensitively by `_narrow`. [] — no prefilter."""
    # A word with a dash may span two columns (GRN «…0000003-П2» = grn + package): any dash-free part is enough.
    raw = [part for word in query.strip().casefold().split() for part in word.split("-") if part]
    if not raw:
        return []

    def solid(word: str) -> int:
        return sum(1 for char in word if char not in _AMBIGUOUS)

    best = max(raw, key=lambda word: (solid(word), len(word)))
    if solid(best) == 0:
        return []
    masked = "".join(
        "_" if char in _AMBIGUOUS else ("\\" + char if char in "%_\\" else char) for char in best
    )
    return [f"%{masked}%"]


def _narrow(columns: Iterable[Any], patterns: list[str], session: Session):
    """PostgreSQL ILIKE folds Unicode case; SQLite compares the Unicode-casefolded text (sqlite_casefold)."""
    if not patterns:
        return None
    if session.get_bind().dialect.name == "sqlite":
        return or_(*[func.erp_casefold(column).like(pattern, escape="\\") for column in columns for pattern in patterns])
    return or_(*[column.ilike(pattern, escape="\\") for column in columns for pattern in patterns])


@event.listens_for(Engine, "connect")
def _search_sqlite_functions(connection, _record) -> None:
    if isinstance(connection, sqlite3.Connection):
        connection.create_function("erp_search_fold", 1, fold, deterministic=True)


def _contains(columns: Iterable[Any], words: list[str], session: Session):
    """Fold the displayed natural keys before LIMIT, including words spread across columns."""
    columns = list(columns)
    text = func.coalesce(cast(columns[0], String), "")
    for column in columns[1:]:
        text = text + " " + func.coalesce(cast(column, String), "")
    if session.get_bind().dialect.name == "sqlite":
        text = func.erp_search_fold(text)
    else:
        text = func.lower(text)
        for source, target in _FOLD.items():
            text = func.replace(text, chr(source), target)
        text = func.regexp_replace(text, r"(?<=\d),(?=\d)", ".", "g")
    compare = text.like if session.get_bind().dialect.name == "sqlite" else text.ilike
    return and_(*(compare("%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\")
                  for word in words))


def _scan(session: Session, columns: list[Any], patterns: list[str], match_columns: Iterable[Any],
          *, order: Any = None, base: Any = None, words: list[str] | None = None) -> Iterator[Any]:
    statement = select(*columns)
    if base is not None:
        statement = statement.where(base)
    if words is not None:
        statement = statement.where(_contains(match_columns, words, session)).limit(PER_GROUP)
    else:
        narrow = _narrow(match_columns, patterns, session)
        if narrow is not None:
            statement = statement.where(narrow)
    if order is not None:
        statement = statement.order_by(order)
    # Older groups rank in Python. Cap total work and stream one query without repeated sorting/skipping.
    if words is not None:
        yield from session.execute(statement)
    else:
        with session.execute(statement.limit(MAX_CANDIDATES).execution_options(yield_per=64)) as result:
            for rows in result.partitions():
                yield from rows


def _text(*parts: object) -> str:
    return fold(" ".join(str(part) for part in parts if part))


def _hit(words: list[str], text: str) -> bool:
    return all(word in text for word in words)


def _dot(*parts: object) -> str:
    return " · ".join(str(part) for part in parts if part)


def _lines(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = ProcurementLineRow
    columns = [row.legacy_id, row.calculated_name, row.design_article, row.order_number, row.supplier,
               row.project_reference, row.designation, row.invoice_item_name]
    found: list[dict] = []
    for legacy, name, article, order, supplier, project, designation, invoice in _scan(
        session, columns, patterns, columns, order=row.created_at.desc(),
    ):
        if _hit(words, _text(legacy, name, article, order, supplier, project, designation, invoice)):
            found.append(_result(
                G_LINES, _dot(legacy, name),
                _dot(project or designation, article, f"№ {order}" if order else "", supplier),
                "procurement", tab="sheet", q=legacy,
            ))
            if len(found) >= PER_GROUP:
                break
    return found


def _projects(session: Session, request: Request, words: list[str], patterns: list[str]) -> list[dict]:
    found: list[dict] = []
    registry = getattr(request.app.state, "project_registry", None)
    numbers = (registry.open_projects() if registry is not None else None) or []
    for number in numbers:
        if number.startswith("ВП-"):
            continue  # internal projects come from their own table, with names
        if _hit(words, _text(number)):
            found.append(_result(G_PROJECTS, number, "Проект", "projects", project=number))
            if len(found) >= PER_GROUP:
                return found
    row = InternalProjectRow
    columns = [row.number, row.name, row.purpose, row.closed_at]
    for number, name, purpose, closed in _scan(session, columns, patterns, [row.number, row.name],
                                               order=row.number):
        if _hit(words, _text(number, name)):
            found.append(_result(
                G_PROJECTS, _dot(number, name), _dot("Внутренний проект", purpose, "закрыт" if closed else ""),
                "projects", tab="internal",
            ))
            if len(found) >= PER_GROUP:
                break
    return found[:PER_GROUP]


def _items(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row, alias = ItemRow, ItemAliasRow
    found: dict[Any, dict] = {}
    for item_id, name, part, category in _scan(
        session, [row.id, row.name, row.part_number, row.category], patterns, [row.name, row.part_number],
        order=row.name, base=row.archived_at.is_(None),
    ):
        if _hit(words, _text(name, part)):
            found[item_id] = _result(G_ITEMS, name, _dot(part, category), "purchasingrefs", tab="items", q=name)
            if len(found) >= PER_GROUP:
                return list(found.values())
    statement = select(row.id, row.name, row.part_number, alias.alias_text).join(
        alias, alias.item_id == row.id).where(row.archived_at.is_(None))
    narrow = _narrow([alias.alias_text], patterns, session)
    if narrow is not None:
        statement = statement.where(narrow)
    for item_id, name, part, alias_text in session.execute(statement):
        if item_id not in found and _hit(words, _text(name, part, alias_text)):
            found[item_id] = _result(G_ITEMS, name, _dot(part, f"у поставщика: {alias_text}"), "purchasingrefs",
                                     tab="items", q=name)
            if len(found) >= PER_GROUP:
                break
    return list(found.values())


def _suppliers(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = SupplierRow
    found: list[dict] = []
    for name, inn, key in _scan(session, [row.name, row.inn, row.is_key], patterns, [row.name, row.inn],
                                order=row.name, base=row.archived_at.is_(None)):
        if _hit(words, _text(name, inn)):
            found.append(_result(G_SUPPLIERS, name, _dot(f"ИНН {inn}" if inn else "", "ключевой" if key else ""),
                                 "purchasingrefs", tab="directory", q=name))
            if len(found) >= PER_GROUP:
                break
    return found


def _stock(session: Session, query: str, patterns: list[str]) -> list[dict]:
    row = StockLotRow
    columns = [row.grn, row.package, row.article, row.name, row.storage_location, row.series, row.purpose,
               row.item_type, row.inbound, row.written_off, row.reserved, row.unit]
    exact = fold(query).replace(" ", "")
    ranked: list[tuple[int, dict]] = []
    for lot in _scan(session, columns, patterns,
                     [row.grn, row.article, row.name, row.storage_location, row.series], order=row.grn):
        if not matches_words(lot, query):
            continue
        grn = f"{lot.grn}-{lot.package}" if lot.package else lot.grn
        free = lot.inbound - lot.written_off - lot.reserved
        rank = 0 if exact in {fold(grn), fold(lot.grn)} else 1
        ranked.append((rank, _result(
            G_STOCK, grn, _dot(lot.article, lot.name, lot.storage_location, f"{free:g} {lot.unit}"),
            "stock", q=grn,
        )))
    ranked.sort(key=lambda pair: pair[0])
    return [result for _rank, result in ranked[:PER_GROUP]]


def _rfqs(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = RfqRow
    found: list[dict] = []
    for number, title, closed in _scan(session, [row.number, row.title, row.closed_at], patterns,
                                       [row.number, row.title], order=row.number.desc()):
        if _hit(words, _text(number, title)):
            found.append(_result(G_RFQ, _dot(number, title), "закрыт" if closed else "открыт", "procurement",
                                 tab="rfq"))
            if len(found) >= PER_GROUP:
                break
    return found


# «База данных»: the stored tab of a record -> (tab of the screen, its label, the access key of the tab). Every base is
# its own tab (migration 0165: «Наконечники» and each cable kind too); a БД.МАТ row goes by what it is.
_PLAIN_TABS = {"op": ("templates", "Операции", "erptctpl"), "ob": ("ob", "Оборудование", "erpref"),
               "coax": ("coax", "Коаксиальные разъёмы", "erpref"), "ter": ("ter", "Терминалы", "erpref"),
               "lug": ("lug", "Наконечники", "erpref"), "cab_coax": ("cab_coax", "Коаксиальные кабели", "erpref"),
               "prov": ("cab_wire", "Монтажные провода", "erpref"), "cab_multi": ("cab_multi", "Многожильные кабели", "erpref"),
               "cab_lan": ("cab_lan", "Кабели Ethernet", "erpref")}
_DB_STORED_TABS = ("op", "ob", "coax", "ter", "lug", "prov", "cab_coax", "cab_multi", "cab_lan", "mat")
_SOLDER_GROUP = "припой/флюс"


def _db_place(tab: str, fields: dict) -> tuple[str, str, str] | None:
    if tab in _PLAIN_TABS:
        return _PLAIN_TABS[tab]
    if tab == "mat":
        if classify_covering(fields)[0]:
            return "cov", "Оболочки", "erpref"
        group = str((fields or {}).get("Группа", "")).strip().casefold().replace("ё", "е")
        return ("mat_use", "Расходники", "erpref") if group == _SOLDER_GROUP else None
    return None


def _database(session: Session, words: list[str], patterns: list[str], keys: set[str]) -> list[dict]:
    row = TechOperationRecordRow
    stored = [tab for tab in _DB_STORED_TABS if tab != "op" or "erptctpl" in keys]
    if "erpref" not in keys:
        stored = [tab for tab in stored if tab == "op"]
    found: list[dict] = []
    columns = [row.tab, row.display_text, row.fields, row.normalized_search]
    for tab, text, fields, haystack in _scan(
        session, columns, patterns, [row.display_text, row.normalized_search], order=row.sort_key, base=row.tab.in_(stored),
    ):
        if not _hit(words, _text(text, haystack)):
            continue
        place = _db_place(tab, fields)
        if place is None or place[2] not in keys:
            continue
        found.append(_result(G_DB, text[:140], f"База данных → {place[1]}", "references", tab=place[0], q=text[:80]))
        if len(found) >= PER_GROUP:
            break
    return found


def _reference_maps(session: Session, words: list[str]) -> list[dict]:
    row = ReferenceEntryRow
    places = {"supplier": ("suppliers", "Домен → поставщик"), "country": ("countries", "Изготовитель → страна")}
    found = []
    for kind, key, value in _scan(session, [row.kind, row.key, row.value], [], [row.key, row.value],
                                  base=row.kind.in_(places), order=row.position, words=words):
        tab, label = places[kind]
        found.append(_result(G_DB, _dot(key, value), f"База данных → {label}", "references", tab=tab, q=key))
    row = WireGaugeRow
    for awg, section in _scan(session, [row.awg, row.section_mm2], [], [row.awg, row.section_mm2],
                              order=row.position, words=words):
        title = _dot(f"AWG {awg}" if awg else "", f"{section:g} мм²")
        found.append(_result(G_DB, title, "База данных → Сечения AWG ↔ мм²", "references", tab="gauges", q=awg or f"{section:g}"))
    return found[:PER_GROUP]


@lru_cache(maxsize=1)
def _catalog_search() -> tuple:
    # The connector catalogue is static. Reuse its series and article list, never reconstruct it per keystroke.
    return tuple((series["id"], series["title"], _text(series["title"], series["maker"],
                  *(housing["article"] for variant in series["variants"] for housing in variant["housings"]),
                  *(entry["article"] for entry in series["catalog"]))) for series in connector_series())


def _reference_cards(session: Session, words: list[str], keys: set[str]) -> list[dict]:
    found: list[dict] = []
    if "erptctpl" in keys:
        for series_id, title, haystack in _catalog_search():
            if _hit(words, haystack):
                found.append(_result(G_DB, title, "База данных → Разъёмы", "references", tab="connectors", series=series_id))
                if len(found) >= PER_GROUP:
                    return found
        row = OperationCardTemplateRow
        for title, name, code in _scan(session, [row.title, row.name, row.code], [], [row.title, row.name, row.code],
                                       order=row.code, words=words):
            found.append(_result(G_DB, _dot(title or name, code), "База данных → Операции", "references", tab="templates", q=code))
    if "erpref" in keys:
        for model, key, tab, label in ((CoaxCableRow, CoaxCableRow.name, "cab_coax", "Коаксиальные кабели"),
                                       (HookupWireRow, HookupWireRow.mark, "cab_wire", "Монтажные провода")):
            for (name,) in _scan(session, [key], [], [key], order=key, words=words):
                found.append(_result(G_DB, name, f"База данных → {label}", "references", tab=tab, q=name))
    return found[:PER_GROUP]


def _equipment(session: Session, words: list[str]) -> list[dict]:
    model, unit = EquipmentModelRow, TechOperationRecordRow
    columns = [unit.id, unit.display_text, model.name, model.article, model.maker]
    statement = (select(*columns).join(model, unit.equipment_model_id == model.id)
                 .where(unit.tab == "ob", _contains([unit.display_text, unit.normalized_search, model.name, model.article, model.maker], words, session))
                 .order_by(model.name, unit.sort_key, unit.id).limit(PER_GROUP))
    return [_result(G_EQUIPMENT, _dot(name, article, text), _dot("База данных → Оборудование", maker),
                    "references", tab="ob", equipment=str(unit_id))
            for unit_id, text, name, article, maker in session.execute(statement)]


def _shipments(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row, line = ShipmentRow, ShipmentLineRow
    own = [row.number, row.customer, row.receiver, row.track_number, row.upd_number, row.pl_number, row.dn_number]
    # EXISTS avoids a row per line and lets separate query words occur on different lines.
    matches = [or_(_contains(own, [word], session), select(line.id).where(
        line.shipment_id == row.id, _contains([line.project], [word], session)).exists()) for word in words]
    rows = session.execute(select(row.id, row.number, row.status, row.customer, row.receiver)
                           .where(*matches).order_by(row.created_at.desc(), row.id).limit(PER_GROUP)).all()
    projects: dict[Any, set[str]] = {}
    if rows:
        for shipment_id, project in session.execute(select(line.shipment_id, line.project).where(line.shipment_id.in_([r.id for r in rows]))):
            projects.setdefault(shipment_id, set()).add(project)
    return [_result(G_SHIPMENTS, _dot(number, customer),
                    _dot(f"Отгрузка → {number}", status, receiver, *sorted(projects.get(shipment_id, ()))),
                    "intake", tab="journal", q=number, shipment=str(shipment_id))
            for shipment_id, number, status, customer, receiver in rows]


def _goods(session: Session, words: list[str], patterns: list[str], warehouse: bool) -> list[dict]:
    row = FinishedGoodRow
    columns = [row.project, row.item_index, row.customer, row.article, row.name, row.grn, row.location,
               row.accepted, row.shipped, row.unit]
    found: list[dict] = []
    for project, index, customer, article, name, grn, location, accepted, shipped, unit in _scan(
        session, columns, patterns, [row.project, row.item_index, row.customer, row.article, row.name, row.grn],
        order=row.project, words=words,
    ):
        if _hit(words, _text(project, index, customer, article, name, grn)):
            left = float(accepted - shipped)
            subtitle = _dot("Готовая продукция → " + project, f"КБ {index}" if index else "", customer, location, f"на складе ГП {left:g} {unit}")
            # The warehouse screen takes the list and the word; a shipper without «Склад» gets the shipping screen.
            section, params = ("stock", {"view": "goods", "q": project}) if warehouse else ("intake", {"tab": "all", "q": project})
            found.append(_result(G_GOODS, _dot(name, article), subtitle, section, **params))
            if len(found) >= PER_GROUP:
                break
    return found


def _techcards(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = ProjectTechcardRow
    found: list[dict] = []
    for card_id, project, index, name, status, revision in _scan(
        session, [row.id, row.project, row.item_index, row.product_name, row.status, row.revision], patterns,
        [row.project, row.item_index, row.product_name], order=row.updated_at.desc(), words=words,
    ):
        if _hit(words, _text(project, index, name)):
            found.append(_result(G_TECHCARDS, _dot(project, f"КБ {index}" if index else "", name),
                                 _dot(f"Техкарты → {project}", status, f"ред. {revision}"), "techcards", project=project, card=str(card_id)))
            if len(found) >= PER_GROUP:
                break
    return found


def _tasks(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = ProductionTaskRow
    found: list[dict] = []
    for task_id, number, project, item, name, quantity, status in _scan(
        session, [row.id, row.number, row.project, row.item, row.name, row.quantity, row.status], patterns,
        [row.number, row.project, row.item, row.name], order=row.created_at.desc(), words=words,
    ):
        if _hit(words, _text(number, project, item, name)):
            found.append(_result(G_TASKS, _dot(number, name),
                                 _dot(f"Задания цеха → {number}", project, f"КБ {item}" if item else "", f"{quantity} шт", status), "production", task=str(task_id)))
            if len(found) >= PER_GROUP:
                break
    return found


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _upd(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = UpdDocumentRow
    found: list[dict] = []
    for doc_id, number, doc_date, seller, inn, status in _scan(
        session, [row.id, row.number, row.doc_date, row.seller_name, row.seller_inn, row.status], patterns,
        [row.number, row.seller_name, row.seller_inn], order=row.doc_date.desc(), words=words,
        base=row.twin_of.is_(None),
    ):
        if _hit(words, _text(number, seller, inn)):
            found.append(_result(
                G_UPD, _dot(f"УПД {number}" if number else "УПД", _day(doc_date)),
                _dot(f"Реестр УПД → {number}", seller, f"ИНН {inn}" if inn else "", status), "primarydocs", tab="upd", upd=doc_id,
            ))
            if len(found) >= PER_GROUP:
                break
    return found


def _invoices(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = InvoiceRow
    found: list[dict] = []
    for number, doc_date, seller, inn, title in _scan(
        session, [row.number, row.doc_date, row.seller_name, row.seller_inn, row.title], patterns,
        [row.number, row.seller_name, row.seller_inn, row.title], order=row.card_created.desc(), words=words,
    ):
        if _hit(words, _text(number, seller, inn, title)):
            found.append(_result(
                G_INVOICES, _dot(f"Счёт {number}" if number else "Счёт", _day(doc_date)),
                _dot(f"Счета → {number}", seller, f"ИНН {inn}" if inn else "", "" if seller else title), "primarydocs", tab="invoices",
                q=number or title[:60],
            ))
            if len(found) >= PER_GROUP:
                break
    return found


def _staff(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = IdentityUserRow
    found: list[dict] = []
    for user_id, name, department, job, active in _scan(
        session, [row.id, row.name, row.department, row.job_title, row.active], patterns,
        [row.name, row.job_title, row.department], order=row.name, words=words,
    ):
        if _hit(words, _text(name, job, department)):
            found.append(_result(G_STAFF, name, _dot(f"Сотрудники → {name}", job, department, "" if active else "не работает"), "staff",
                                 user=str(user_id)))
            if len(found) >= PER_GROUP:
                break
    return found


def _kits(session: Session, words: list[str], patterns: list[str], pick: bool, give: bool) -> list[dict]:
    """Kit requests («Запросы на комплектацию» / «Выдача со списанием», owner 28.10.2026: «НШВ» must find the requests
    that carry it) — by number, purpose, project, КБ, requester and the article / name of their lines. Opens the screen
    the request stands on (the other one when only that is allowed); a closed one — its archive."""
    request, line = FulfillmentRequestRow, FulfillmentLineRow
    by_lines: dict[Any, list[str]] = {}
    statement = select(line.request_id, line.article, line.name)
    narrow = _narrow([line.article, line.name], patterns, session)
    if narrow is not None:
        statement = statement.where(narrow)
    for request_id, article, name in session.execute(statement):
        # «НШВИ 0.5-8 НШВИ 0.5-8»: the name already says the article — once is enough
        by_lines.setdefault(request_id, []).append(name if article and article in name else f"{article} {name}".strip())
    own = [request.number, request.purpose, request.project_key, request.item_index, request.requester]
    statement = select(request.id, request.number, request.status, request.purpose, request.project_key,
                       request.item_index, request.requester).order_by(request.created_at.desc())
    narrow = _narrow(own, patterns, session)
    if narrow is not None:
        statement = statement.where(or_(narrow, request.id.in_(list(by_lines)))) if by_lines else statement.where(narrow)
    found: list[dict] = []
    for request_id, number, status, purpose, project, item, requester in session.execute(statement):
        lines = by_lines.get(request_id, [])
        if not _hit(words, _text(number, purpose, project, item, requester, status, *lines)):
            continue
        on_give = status in _GIVE_STAGES
        section = "handout" if (on_give and give) or not pick else "issues"
        params = {"q": number, **({"archive": "1"} if status in _CLOSED_STAGES else {})}
        where = "Выдача со списанием" if section == "handout" else "Запросы на комплектацию"
        hit_line = next((text for text in lines if _hit(words, _text(text))), "")
        found.append(_result(G_KITS, _dot(number, purpose or _dot(project, item)),
                             _dot(where, status, hit_line[:60]), section, **params))
        if len(found) >= PER_GROUP:
            break
    return found


def _may(principal: Principal, *sections: str) -> bool:
    return section_allowed(principal, sections, "view")


@router.get("")
def search(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    factory: Annotated[Callable, Depends(purchasing_uow_factory)],
    q: Annotated[str, Query(max_length=MAX_QUERY)] = "",
) -> dict:
    started = time.perf_counter()
    words = fold(q).split()
    if not words:
        return {"query": q, "results": [], "ms": 0}
    patterns = _prefilter(q)
    procurement = _may(principal, "procurement")
    groups: list[tuple[str, Callable[[Session], list[dict]]]] = []
    if procurement:
        groups.append((G_LINES, lambda s: _lines(s, words, patterns)))
    if _may(principal, "projects"):
        groups.append((G_PROJECTS, lambda s: _projects(s, request, words, patterns)))
    if procurement:
        groups.append((G_ITEMS, lambda s: _items(s, words, patterns)))
    if procurement:
        groups.append((G_SUPPLIERS, lambda s: _suppliers(s, words, patterns)))
    if _may(principal, "warehouse"):
        groups.append((G_STOCK, lambda s: _stock(s, q, patterns)))
    if procurement:
        groups.append((G_RFQ, lambda s: _rfqs(s, words, patterns)))
    keys = {key for key in ("erpref", "erptctpl") if _may(principal, key)}
    if keys:
        groups.append((G_DB, lambda s: (_database(s, words, patterns, keys) +
                                        (_reference_maps(s, words) if "erpref" in keys else []) +
                                        _reference_cards(s, words, keys))[:PER_GROUP]))
    if "erpref" in keys:
        groups.append((G_EQUIPMENT, lambda s: _equipment(s, words)))
    if _may(principal, "shipping", "shipping_goods"):
        groups.append((G_SHIPMENTS, lambda s: _shipments(s, words, patterns)))
        groups.append((G_GOODS, lambda s: _goods(s, words, patterns, _may(principal, "warehouse"))))
    pick, give = _may(principal, "fulfillment"), _may(principal, "handout")
    if pick or give:
        groups.append((G_KITS, lambda s: _kits(s, words, patterns, pick, give)))
    if _may(principal, "techcards"):
        groups.append((G_TECHCARDS, lambda s: _techcards(s, words, patterns)))
    if _may(principal, "production"):
        groups.append((G_TASKS, lambda s: _tasks(s, words, patterns)))
    if procurement:
        groups.append((G_UPD, lambda s: _upd(s, words, patterns)))
        groups.append((G_INVOICES, lambda s: _invoices(s, words, patterns)))
    if principal.full_access:  # «Сотрудники» is outside the matrix: superadmin / admin only
        groups.append((G_STAFF, lambda s: _staff(s, words, patterns)))
    # A scanned / typed GRN: the lot first.
    if GRN_LIKE.match(q.strip()):
        groups.sort(key=lambda pair: pair[0] != G_STOCK)
    results: list[dict] = []
    if groups:
        with factory() as uow:
            for _name, run in groups:
                results.extend(run(uow.session))
    return {"query": q, "results": results, "ms": round((time.perf_counter() - started) * 1000)}
