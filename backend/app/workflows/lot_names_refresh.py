"""The stock brought in line with the receiving rules of 26.10.2026 (owner: «пора сам склад обновить»).

Since PR #720 a lot accepted from a УПД carries OUR short name (the catalog's, else the model's), the item type of the
stock dictionary, the maker, and — hidden — the row as the УПД prints it (`upd_name`). The lots taken in before keep
whatever the old warehouse wrote: often the article itself as the name, no type, no maker. Per lot:

* «Наим. по УПД» (`upd_name`) — where empty, the lot's current article («за наименование УПД взять сейчас артикул»);
  a filled one is never overwritten.
* «Наименование» — replaced ONLY when it is not a human name: empty, the article itself (`word_search.article_key`:
  case, separators, Latin/Cyrillic twins, Molex leading zeros aside), an «article@MAKER» / «article##6000» leftover,
  or the article with the maker («JST XHP-4»). «BNC-male RG-174», «Клемма JST XH 2.54 мм, 4 pin, розетка» stay.
* «Тип» — only where empty, and only a value of the stock's type dictionary (`receipt_llm.item_type_of`).
* «Производитель» — only where empty (`normalize_maker`).

Sources, as «Поставка» → article info fills a typed article (catalog > model > nothing, owner 26.10.2026): another lot
of the same article (a human name, a type, a maker — the newest first), the maker written in the article
(«XHP-4@JST»), the reference DB (`infer_article_identity`: series rules, БД.ТЕР, БД.КОАКС; then БД.ПРОВ / БД.МАТ names
of `item_names.ReferenceNames`; БД.ТЕР makers; the maker a connector family's title names), the purchasing
nomenclature; what is still missing — the model, ONE text call per batch of articles (`receipt_llm` settings: env, else «Склад» → «Распознавание УПД»). No key — the model
part is skipped and said so. Article, quantities, places, ГРН and receipt links are never touched; no row is added.

    deploy/erp.sh cli python -m app.workflows.lot_names_refresh --csv /app/data/import/lot-names.csv   # пробный прогон
    deploy/erp.sh cli python -m app.workflows.lot_names_refresh --apply --from /app/data/import/lot-names.csv
    deploy/erp.sh cli python -m app.workflows.lot_names_refresh --revert --from /app/data/import/lot-names.csv

The dry run (the default) prints «ГРН | артикул | поле | было → стало | источник» and the totals, and `--csv` writes the
same table (UTF-8 with BOM, «;» — opens in Russian Excel). `--apply --from` writes exactly the reviewed CSV — the
model is not called on apply: a value is written only where the lot still holds what the CSV says it «было» (a fill
only into an empty field), one transaction and one `warehouse.row_edited` audit event per lot (actor «обновление склада
26.10», «было → стало» per field, the source) — the stock journal shows it. A second apply finds everything written
and changes nothing; `--revert --from` puts «было» back where the lot still holds «стало».
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.references.domain import TechOperationTab
from app.modules.techcards.connector_housings import housing_of
from app.modules.warehouse import receipt_llm
from app.modules.warehouse.domain import UPD_NAME_LIMIT, StockLot
from app.modules.warehouse.item_types import ITEM_TYPES_CANON, canonical_item_type
from app.modules.warehouse.word_search import article_key
from app.shared.article_maker import normalize_maker, split_article_maker
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import StockVersionConflict
from app.workflows.item_names import ReferenceNames
from app.workflows.warehouse_article import infer_article_identity

log = logging.getLogger(__name__)

ACTOR_NAME = "обновление склада 26.10"
REVERT_ACTOR_NAME = "откат обновления склада 26.10"
REASON = "обновление склада 26.10 (наименование, тип, производитель, наим. по УПД)"
# Field → the label of the report / CSV and the column of the stock journal.
FIELDS = {"name": "наименование", "item_type": "тип", "manufacturer": "производитель", "upd_name": "наим. по УПД"}
JOURNAL_COLUMNS = {"name": "НАИМЕНОВАНИЕ", "item_type": "ТИП", "manufacturer": "ПРОИЗВОДИТЕЛЬ", "upd_name": "НАИМ. ПО УПД"}
LIMITS = {"name": 1024, "item_type": 255, "manufacturer": 255, "upd_name": UPD_NAME_LIMIT}
FILL_ONLY = ("item_type", "manufacturer", "upd_name")  # never overwritten: written only into an empty field
CSV_COLUMNS = ("id", "склад", "ГРН", "пакет", "артикул", "поле", "было", "стало", "источник")
DB, MODEL, ARTICLE = "база", "модель", "артикул"

# The maker a connector family's title starts with («JST XH», «Molex Micro-Fit 3.0»): the family IS that maker's.
HOUSING_MAKERS = frozenset({"molex", "jst", "hirose", "connfly", "te", "jae", "amass", "hsuan", "weipu", "cnlinko",
                            "deutsch", "kls", "samtec"})

MODEL_BATCH = 25  # articles per text call
MODEL_PARALLEL = 4
MODEL_BUDGET = 1800.0  # seconds for every call of a run: a CLI, not a request


# ── What is a human name ──────────────────────────────────────────────────────────────────────────


_EDGE = re.compile(r"^[\s(\[«\"',;:]+|[\s)\]»\"',;:.]+$")


def _article_keys(article: str) -> set[str]:
    bare = split_article_maker(article).article
    return {key for key in (article_key(article), article_key(bare)) if key}


def name_is_article(name: object, article: object, maker: object = "") -> bool:
    """True when «Наименование» is not a human name: empty, the article (as `article_key` sees it), an
    «article@MAKER» / «article##6000» leftover, or the article with the maker's word(s) around it."""
    text = " ".join(str(name or "").split())
    if not text:
        return True
    keys = _article_keys(str(article or ""))
    if not keys:
        return False
    split = split_article_maker(text)
    if article_key(text) in keys or article_key(split.article) in keys:
        return True
    makers = {normalize_maker(value) for value in (maker, split.maker, split_article_maker(article).maker)} - {""}
    if not makers:
        return False
    words = [_EDGE.sub("", word) for word in re.split(r"[\s,;]+", split.article)]
    rest = [word for word in words if word and normalize_maker(word) not in makers]
    return len(rest) < len(words) and bool(rest) and article_key(" ".join(rest)) in keys


# ── Sources ────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Found:
    value: str
    source: str  # «база: БД.ТЕР», «база: партия FRX…», «модель», «артикул»


@dataclass
class Catalog:
    """What ERP's own data knows of an article: the reference records (БД.ТЕР, БД.КОАКС, БД.ПРОВ, БД.МАТ) and the
    purchasing nomenclature (`purchasing(article)` → {name, manufacturer, item_type} | None)."""

    records: Sequence[Any] = ()
    purchasing: Callable[[str], Mapping[str, str] | None] | None = None
    _names: ReferenceNames | None = field(default=None, init=False, repr=False)
    _makers: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.records = tuple(self.records)
        self._names = ReferenceNames(self.records)
        for record in self.records:
            tab = record.tab.value if isinstance(record.tab, TechOperationTab) else str(record.tab)
            if tab != TechOperationTab.TERMINAL.value:
                continue
            fields = record.fields or {}
            key = article_key(fields.get("terArticle") or fields.get("article") or "")
            maker = normalize_maker(fields.get("terManufacturer") or "")
            if key and maker:
                self._makers.setdefault(key, maker)

    def lookup(self, article: str, types: Sequence[str]) -> dict[str, Found]:
        """{name, item_type, manufacturer} the reference DB, then the purchasing nomenclature give — each field from
        the first source that has it; a name only when it is a human one, a type only from `types`."""
        found: dict[str, Found] = {}

        def put(name: str, value: object, source: str) -> None:
            text = " ".join(str(value or "").split())
            if name in found or not text:
                return
            if name == "item_type":
                text = receipt_llm.item_type_of(canonical_item_type(text), types)
            elif name == "manufacturer":
                text = normalize_maker(text)
            elif name_is_article(text, article):
                return
            if text:
                found[name] = Found(text[:LIMITS[name]], source)

        identity = infer_article_identity(article, self.records)
        if identity is not None:
            put("name", identity.name, f"{DB}: {identity.source}")
            put("item_type", identity.item_type, f"{DB}: {identity.source}")
        if self._names is not None:
            put("name", self._names.name(article), f"{DB}: справочник")
        put("manufacturer", self._makers.get(article_key(article), ""), f"{DB}: БД.ТЕР")
        housing = housing_of(article)
        if housing:
            first = str(housing.get("title") or "").split(" ", 1)[0]
            put("manufacturer", first if first.casefold() in HOUSING_MAKERS else "", f"{DB}: семейство корпуса")
        item = self.purchasing(article) if self.purchasing is not None else None
        if item:
            for name in ("name", "item_type", "manufacturer"):
                put(name, item.get(name), f"{DB}: номенклатура закупок")
        return found


Model = Callable[[list[dict]], list[dict] | None]


# ── The plan ───────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Change:
    lot_id: UUID
    warehouse: str
    grn: str
    package: str
    article: str
    field: str  # name | item_type | manufacturer | upd_name
    was: str
    now: str
    source: str


@dataclass(frozen=True)
class Plan:
    lots: int  # lots looked at
    changes: tuple[Change, ...]
    model_note: str  # why the model did (not) help, for the report


def _group_key(lot: StockLot) -> str:
    return article_key(split_article_maker(lot.article).article)


def _newest_first(lots: Iterable[StockLot]) -> list[StockLot]:
    return sorted(lots, key=lambda lot: (lot.grn, lot.package), reverse=True)


def _sibling(lots: Sequence[StockLot], name: str, types: Sequence[str]) -> Found | None:
    """The newest lot of the article that already has the field (a human name, a dictionary type, a maker)."""
    for lot in _newest_first(lots):
        value = " ".join(str(getattr(lot, name) or "").split())
        if not value:
            continue
        if name == "name" and name_is_article(value, lot.article, lot.manufacturer):
            continue
        if name == "item_type":
            value = receipt_llm.item_type_of(canonical_item_type(value), types)
        elif name == "manufacturer":
            value = normalize_maker(value)
        if value:
            return Found(value[:LIMITS[name]], f"{DB}: партия {lot.grn}{f' / {lot.package}' if lot.package else ''}")
    return None


def _written_maker(lots: Sequence[StockLot]) -> Found | None:
    """«XHP-4@JST» in the article or the name: the maker the old warehouse glued on."""
    for lot in _newest_first(lots):
        for text in (lot.article, lot.name):
            maker = split_article_maker(text).maker
            if maker:
                return Found(maker, ARTICLE)
    return None


def _model_row(article: str, lots: Sequence[StockLot], known: Mapping[str, Found]) -> dict:
    newest = _newest_first(lots)
    first = lambda name: next((str(getattr(lot, name) or "").strip() for lot in newest if str(getattr(lot, name) or "").strip()), "")  # noqa: E731
    printed = next((lot.upd_name for lot in newest
                    if lot.upd_name.strip() and not name_is_article(lot.upd_name, lot.article, lot.manufacturer)), "")
    return {
        "article": article,
        "name": first("name"),
        "maker": known["manufacturer"].value if "manufacturer" in known else first("manufacturer"),
        "series": first("series"),
        "item_type": known["item_type"].value if "item_type" in known else first("item_type"),
        "upd_name": printed,
    }


def plan_changes(
    lots: Sequence[StockLot], catalog: Catalog, types: Sequence[str] = ITEM_TYPES_CANON, model: Model | None = None,
) -> Plan:
    """Every change the rules ask for, with its source. Pure: reads nothing, writes nothing. The model (if any) is
    asked once per article, only for what the catalog left missing."""
    groups: dict[str, list[StockLot]] = {}
    for lot in lots:
        groups.setdefault(_group_key(lot), []).append(lot)
    wanted: dict[str, dict[str, Found]] = {}
    asks: list[tuple[str, dict]] = []
    for key, group in groups.items():
        if not key:
            continue
        article = split_article_maker(_newest_first(group)[0].article).article
        needs = {
            "name": any(name_is_article(lot.name, lot.article, lot.manufacturer) for lot in group),
            "item_type": any(not lot.item_type.strip() for lot in group),
            "manufacturer": any(not lot.manufacturer.strip() for lot in group),
        }
        if not any(needs.values()):
            continue
        reference = catalog.lookup(article, types)
        known: dict[str, Found] = {}
        for name in ("name", "item_type", "manufacturer"):
            found = _sibling(group, name, types)
            if found is None and name == "manufacturer":
                found = _written_maker(group)
            found = found or reference.get(name)
            if found is not None:
                known[name] = found
        wanted[key] = known
        if any(needs[name] and name not in known for name in needs):
            asks.append((key, _model_row(article, group, known)))

    note = ""
    if asks and model is None:
        note = f"модель не спрашивали: {len(asks)} артикулов остались без наименования/типа/производителя из базы"
    elif asks:
        answers = model([row for _, row in asks]) or []
        answered = 0
        for (key, row), answer in zip(asks, answers):
            if not answer:
                continue
            answered += 1
            known = wanted[key]
            short = " ".join(str(answer.get("short_name") or "").split())
            if "name" not in known and short and not name_is_article(short, row["article"], row["maker"]):
                known["name"] = Found(short[:LIMITS["name"]], MODEL)
            kind = receipt_llm.item_type_of(answer.get("item_type"), types)
            if "item_type" not in known and kind:
                known["item_type"] = Found(kind, MODEL)
            maker = normalize_maker(answer.get("manufacturer"))
            if "manufacturer" not in known and maker:
                known["manufacturer"] = Found(maker[:LIMITS["manufacturer"]], MODEL)
        note = f"модель: спрошено артикулов {len(asks)}, ответ на {answered}"

    changes: list[Change] = []
    for lot in sorted(lots, key=lambda lot: (lot.warehouse, lot.grn, lot.package)):
        known = wanted.get(_group_key(lot), {})

        def change(name: str, now: str, source: str) -> None:
            was = str(getattr(lot, name) or "")
            if now and now != was:
                changes.append(Change(lot.id, lot.warehouse, lot.grn, lot.package, lot.article, name, was, now, source))

        if name_is_article(lot.name, lot.article, lot.manufacturer) and "name" in known:
            change("name", known["name"].value, known["name"].source)
        for name in ("item_type", "manufacturer"):
            if not str(getattr(lot, name) or "").strip() and name in known:
                change(name, known[name].value, known[name].source)
        if not lot.upd_name.strip():
            change("upd_name", " ".join(lot.article.split())[:LIMITS["upd_name"]], ARTICLE)
    return Plan(lots=len(lots), changes=tuple(changes), model_note=note)


# ── The model ──────────────────────────────────────────────────────────────────────────────────────


def describe_prompt(rows: Sequence[Mapping[str, str]], types: Sequence[str] = ITEM_TYPES_CANON) -> str:
    lines = []
    for index, row in enumerate(rows, start=1):
        parts = [f"артикул: {row.get('article', '')}"]
        for name, label in (("maker", "производитель"), ("series", "серия"), ("item_type", "тип"),
                            ("name", "наименование сейчас"), ("upd_name", "по документу поставщика")):
            if str(row.get(name) or "").strip():
                parts.append(f"{label}: {row[name]}")
        lines.append(f"{index}. " + "; ".join(parts))
    return (
        "Это позиции склада производства жгутов и кабельных сборок: артикул производителя и что о позиции известно. "
        'Для каждой строки верни ТОЛЬКО JSON без пояснений: {"lines": [{"n": номер строки, '
        + receipt_llm.item_fields(types) + "}]} "
        "Все строки, в их порядке. Наименование составь по артикулу и известным данным, если знаешь эту деталь "
        "(вид изделия, серия, число контактов, сечение, пол); не знаешь — пусто, не выдумывай.\n" + "\n".join(lines)
    )


def _model_batch(config: receipt_llm.LlmConfig, rows: Sequence[Mapping[str, str]], types: Sequence[str],
                 deadline: float, clock: Callable[[], float]) -> list[dict] | None:
    messages = [{"role": "user", "content": describe_prompt(rows, types)}]
    options: dict[str, object] = {"temperature": 0, "response_format": {"type": "json_object"},
                                  "reasoning_effort": "low"}
    for model in config.models():
        remaining = deadline - clock()
        if remaining < 5:
            break
        started = clock()
        try:
            data = receipt_llm.parse_answer(receipt_llm.message_text(
                receipt_llm.complete(config, model, messages, options, min(receipt_llm.CALL_TIMEOUT, remaining))))
        except receipt_llm.LlmError as error:
            log.warning("Склад: модель %s, наименования — %s (%.1f с)", model, error, clock() - started)
            continue
        log.info("Склад: модель %s, артикулов %d — %.1f с", model, len(rows), clock() - started)
        answer = data.get("lines") if isinstance(data.get("lines"), list) else []
        by_number = {str(row.get("n", "")).strip(): row for row in answer if isinstance(row, dict)}
        return [receipt_llm.row_fields(by_number.get(str(index), {}), types) for index in range(1, len(rows) + 1)]
    return None


def model_reader(config: receipt_llm.LlmConfig, types: Sequence[str],
                 clock: Callable[[], float] = time.monotonic) -> Model:
    """The text model over the articles, MODEL_BATCH per call, MODEL_PARALLEL calls at once, MODEL_BUDGET for the run.
    A batch no model answered gives {} rows (nothing is filled from it)."""
    def read(rows: list[dict]) -> list[dict]:
        deadline = clock() + MODEL_BUDGET
        batches = [rows[start:start + MODEL_BATCH] for start in range(0, len(rows), MODEL_BATCH)]
        with ThreadPoolExecutor(max_workers=MODEL_PARALLEL, thread_name_prefix="lot-names") as pool:
            answers = list(pool.map(lambda batch: _model_batch(config, batch, types, deadline, clock), batches))
        return [item for batch, answer in zip(batches, answers) for item in (answer or [{} for _ in batch])]

    return read


# ── Report and CSV ─────────────────────────────────────────────────────────────────────────────────


def _source_kind(source: str) -> str:
    return source.split(":", 1)[0]


def format_report(plan: Plan) -> str:
    lines = ["ГРН | артикул | поле | было → стало | источник"]
    for item in plan.changes:
        grn = item.grn + (f" / {item.package}" if item.package else "")
        lines.append(f"{item.warehouse} {grn} | {item.article} | {FIELDS[item.field]} | "
                     f"«{item.was or '—'}» → «{item.now}» | {item.source}")
    lots = len({item.lot_id for item in plan.changes})
    by_field = ", ".join(f"{label} {sum(item.field == name for item in plan.changes)}" for name, label in FIELDS.items())
    by_source = ", ".join(f"{kind} {sum(_source_kind(item.source) == kind for item in plan.changes)}"
                          for kind in (DB, MODEL, ARTICLE))
    lines.append(f"Итого: партий просмотрено {plan.lots}, меняется {lots}; изменений {len(plan.changes)} "
                 f"({by_field}); источник: {by_source}")
    if plan.model_note:
        lines.append(plan.model_note)
    return "\n".join(lines)


def write_csv(path: Path, changes: Iterable[Change]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(CSV_COLUMNS)
        for item in changes:
            writer.writerow((str(item.lot_id), item.warehouse, item.grn, item.package, item.article,
                             FIELDS[item.field], item.was, item.now, item.source))


_FIELD_BY_LABEL = {label: name for name, label in FIELDS.items()}


def read_csv(path: Path) -> list[Change]:
    """The reviewed CSV back as changes; a row with an unknown field or lot id is an error (nothing is applied)."""
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    changes = []
    for number, row in enumerate(rows, start=2):
        missing = [column for column in CSV_COLUMNS if column not in row]
        if missing:
            raise ValueError(f"{path}: нет колонок {', '.join(missing)}")
        name = _FIELD_BY_LABEL.get(str(row["поле"]).strip())
        if name is None:
            raise ValueError(f"{path}, строка {number}: неизвестное поле «{row['поле']}»")
        try:
            lot_id = UUID(str(row["id"]).strip())
        except ValueError as error:
            raise ValueError(f"{path}, строка {number}: id партии «{row['id']}» — не UUID") from error
        changes.append(Change(lot_id, row["склад"] or "", row["ГРН"] or "", row["пакет"] or "", row["артикул"] or "",
                              name, row["было"] or "", row["стало"] or "", row["источник"] or ""))
    return changes


# ── Apply ──────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Skipped:
    change: Change
    reason: str


@dataclass(frozen=True)
class ApplyResult:
    lots: int  # lots written
    written: tuple[Change, ...]
    already: int  # changes the lot already holds (a repeated apply)
    skipped: tuple[Skipped, ...]


def _checked(change: Change, types: Sequence[str]) -> tuple[str, str]:
    """(value to write, '') or ('', why not): the dictionary type, the field width, a name never emptied."""
    value = " ".join(change.now.split()) if change.field != "upd_name" else change.now.strip()
    if change.field == "item_type" and value:
        value = receipt_llm.item_type_of(canonical_item_type(value), types)
        if not value:
            return "", f"тип «{change.now}» не из справочника типов склада"
    if change.field == "manufacturer":
        value = normalize_maker(value)
    if change.field == "name" and not value:
        return "", "пустое наименование не пишется"
    if len(value) > LIMITS[change.field]:
        return "", f"длиннее {LIMITS[change.field]} символов"
    return value, ""


def apply_changes(
    changes: Sequence[Change], uow_factory: Callable[[], Any], actor: Actor, now: datetime,
    types: Sequence[str] = ITEM_TYPES_CANON, revert: bool = False,
) -> ApplyResult:
    """Writes the CSV's «стало» where the lot still holds its «было» (`revert` — «было» back where it holds «стало»).
    One transaction and one `warehouse.row_edited` event per lot. Article, quantities, places, ГРН are not fields here."""
    by_lot: dict[UUID, list[Change]] = {}
    for change in changes:
        by_lot.setdefault(change.lot_id, []).append(change)
    written: list[Change] = []
    skipped: list[Skipped] = []
    already = 0
    lots = 0
    for lot_id, items in by_lot.items():
        try:
            with uow_factory() as uow:
                lot = uow.warehouse.get(lot_id)
                if lot is None:
                    skipped.extend(Skipped(item, "партии нет на складе") for item in items)
                    continue
                values: dict[str, str] = {}
                done: list[tuple[Change, str, str]] = []
                for item in items:
                    before, target = (item.now, item.was) if revert else (item.was, item.now)
                    current = str(getattr(lot, item.field) or "")
                    if item.field in values:
                        skipped.append(Skipped(item, "поле партии уже есть в файле выше"))
                        continue
                    if not revert:
                        target, why = _checked(item, types)
                        if why:
                            skipped.append(Skipped(item, why))
                            continue
                        if item.field in FILL_ONLY and current.strip():
                            if current == target:
                                already += 1
                            else:
                                skipped.append(Skipped(item, f"поле уже заполнено: «{current}»"))
                            continue
                    if current == target:
                        already += 1
                        continue
                    if current != before:
                        skipped.append(Skipped(item, f"в партии сейчас «{current or '—'}», а не «{before or '—'}»"))
                        continue
                    if revert and item.field == "name" and not target.strip():
                        skipped.append(Skipped(item, "пустое наименование не пишется"))
                        continue
                    values[item.field] = target
                    done.append((item, current, target))
                if not values:
                    continue
                updated = replace(lot, **values, version=lot.version + 1)
                details = {
                    "grn": lot.grn, "package": lot.package, "warehouse": lot.warehouse,
                    "reason": ("откат: " if revert else "") + REASON,
                    "changes": "; ".join(f"{JOURNAL_COLUMNS[item.field]}: «{was or '—'}» → «{value or '—'}»"
                                         for item, was, value in done),
                }
                for item, was, value in done:
                    details[f"{item.field}_from"], details[f"{item.field}_to"] = was, value
                    details[f"{item.field}_source"] = item.source
                uow.warehouse.add(updated)
                uow.audit.add(AuditEvent(
                    id=uuid4(), aggregate_id=lot.id, event_type="warehouse.row_edited", actor=actor, occurred_at=now,
                    details=details,
                ))
                uow.commit()
        except (StockVersionConflict, StaleDataError, IntegrityError) as error:
            skipped.extend(Skipped(item, f"партия изменилась во время записи: {error}") for item in items)
            continue
        lots += 1
        written.extend(item for item, _, _ in done)
    return ApplyResult(lots=lots, written=tuple(written), already=already, skipped=tuple(skipped))


def format_applied(result: ApplyResult, revert: bool = False) -> str:
    lines = [f"{'Откачено' if revert else 'Записано'}: партий {result.lots}, изменений {len(result.written)}; "
             f"уже было так {result.already}; пропущено {len(result.skipped)}"]
    for item in result.skipped:
        change = item.change
        lines.append(f"  пропуск: {change.warehouse} {change.grn}{f' / {change.package}' if change.package else ''} | "
                     f"{change.article} | {FIELDS[change.field]} — {item.reason}")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Склад по правилам приёмки 26.10: наше наименование, тип, производитель, наим. по УПД.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать изменения (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="записать ровно то, что в файле --from (модель не зовётся)")
    mode.add_argument("--revert", action="store_true", help="вернуть «было» из файла --from")
    parser.add_argument("--csv", type=Path, help="пробный прогон: записать таблицу изменений в этот файл")
    parser.add_argument("--from", dest="source", type=Path, help="CSV пробного прогона для --apply / --revert")
    parser.add_argument("--no-model", action="store_true", help="пробный прогон без модели, только база")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if (args.apply or args.revert) and args.source is None:
        print("--apply / --revert пишут только проверенный файл: укажите --from <csv пробного прогона>", file=sys.stderr)
        return 2
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import (
        build_engine,
        build_purchasing_uow_factory,
        build_references_uow_factory,
        build_warehouse_uow_factory,
    )
    from app.modules.warehouse.reference_fields import item_types
    from app.modules.warehouse.scan_recognition import llm_config
    from app.workflows.item_names import reference_records
    from app.workflows.stock_receipt import purchasing_item

    engine = build_engine(url)
    try:
        warehouse = build_warehouse_uow_factory(engine)
        types = item_types(warehouse())
        if args.apply or args.revert:
            changes = read_csv(args.source)
            name = REVERT_ACTOR_NAME if args.revert else ACTOR_NAME
            result = apply_changes(changes, warehouse, Actor(id=uuid.uuid4(), name=name, department=""), moscow_now(),
                                   types, revert=args.revert)
            print(format_applied(result, revert=args.revert))
            return 0
        purchasing = build_purchasing_uow_factory(engine)
        catalog = Catalog(reference_records(build_references_uow_factory(engine)),
                          lambda article: purchasing_item(purchasing, article))
        model: Model | None = None
        if args.no_model:
            print("Модель не спрашиваем (--no-model): только база.", file=sys.stderr)
        else:
            config = llm_config(warehouse)
            if config.ready:
                model = model_reader(config, types)
            else:
                print("Ключ модели не задан (ERP_UPD_LLM_API_KEY или «Склад» → «Распознавание УПД»): "
                      "модельная часть пропущена, только база.", file=sys.stderr)
        with warehouse() as uow:
            lots = uow.warehouse.every()
        plan = plan_changes(lots, catalog, types, model)
    finally:
        engine.dispose()
    print(format_report(plan))
    if args.csv is not None:
        write_csv(args.csv, plan.changes)
        print(f"Таблица: {args.csv} (строк {len(plan.changes)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
