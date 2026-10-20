"""Запись в прод 20.10.2026 — БД.КОАКС: «Артикул» у каждой строки — номер детали из названия товара у поставщика.

Владелец 20.10: «так и не поправили не тот артикул — артикул на сайте один, а записан другой» (SMA-female RG-174 вместо S-211L).
19.10 обобщённые строки G («SMA-female RG-174») слили в строки настоящего артикула R и только ПОМЕТИЛИ «дубль → R»: удалить строку
в приложении было нельзя. Теперь есть `references.application.delete_record`; прод-скрипт делает то же самое тем же путём
(репозиторий + событие `references.record_deleted` с полной старой строкой), потому что на сервере ещё стоит прежний код.

Что делает (по группам; каждая группа — одна транзакция, отказ группы не трогает остальные):
  1. УДАЛЕНИЕ помеченных дублей: строки с «Примечание: дубль → …» (34 на 20.10). Перед удалением заново, на момент записи:
     нет ссылок на артикул G по имени ни в одной таблице, кроме журналов и БД.КОАКС (комплекты, конфигурации и ревизии
     техкарт, ЭТК-черновики, шаблоны, комплектация), и нет партий склада с артикулом G. История (списания, приёмка, заявки
     поставщику, журнал связей справочника, журналы) не считается. Есть ссылка — строка остаётся, причина в выводе.
  2. СЛИЯНИЕ+УДАЛЕНИЕ дублей по коду поставщика (владелец 20.10: «тот же код поставщика + тот же кабель» — один товар):
     строка G и строка R — один товар на том же кабеле (список ниже, каждая пара проверена по странице товара).
     Пустые свойства R берутся из G (только если у остальных строк артикула R они тоже пусты), разное — остаётся значение R,
     а старое значение G пишется в «Источник данных» R («документация превыше»: R — по странице товара). Затем G удаляется.
  3. ПЕРЕИМЕНОВАНИЕ «Артикул» строк без пары в номер детали из названия товара (список ниже; номер — из «Наименования»
     или заголовка страницы по «Ссылке»). У нового артикула уже есть строки на другом кабеле — тогда у артикула одни
     значения (владелец 19.10): пустое у R заполняется из G, разное у G приводится к значению R, старое значение G пишется в
     его «Источник данных». Партии склада на артикул G: их нет (проверяется заново) — есть партия, строка остаётся.
Ничего не пишется в склад, комплекты и техкарты; ключ строки — натуральный (артикул + кабель), ID не используется.
Откат: режим rollback <csv> по журналу (удалённые строки возвращаются со своим ID, ячейки — к старым значениям).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл     (в конце — CSV-журнал отката)
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл     (после: «уже сделано»)
  rollback: compose run --rm -T -v $PWD/журнал.csv:/tmp/журнал.csv api python - rollback /tmp/журнал.csv < этот файл
"""
import csv
import io
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, String, Text, create_engine, inspect, text

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import COAX_ROW_OWN, FieldEdit, _audit, _edit_in, _search_text
from app.modules.references.domain import (MAX_ROW_EDITS, ReferenceValidationError, StaleRecordError, TechOperationRecord,
                                           TechOperationTab, cable_key)
from app.modules.references.technology import is_coax_cable_column
from app.shared.clock import moscow_now
from app.shared.identity import Actor

ACTOR = Actor(id=UUID(int=0), name="Артикулы БД.КОАКС по названию товара 20.10.2026", department="System")
DATE = "20.10.2026"
NOTE = "Примечание"
SOURCE = "Источник данных"
CODES = "Артикул поставщика"
MARK = "дубль →"

# G (удаляется) → R (остаётся): один товар на том же кабеле, сверено по странице товара / коду поставщика.
MERGE_DELETE = [
    ("N-female RG-316", "RG-174", "N-211L", "RG-174"),        # kroks.ru 656: «Разъем N-211L N(female) на кабели RG-174, RG-316»
    ("N-female RG-316", "RG-316", "N-211L", "RG-316"),
    ("TNC-Female RG316", "RG-174", "T-231L", "RG-174"),       # kroks.ru 1008: «Разъем T-231L TNC(female) …»
    ("TNC-Female RG316", "RG-316", "T-231L", "RG-316"),
    ("MCX-female RG-174", "RG-174", "11-211L", "RG-174"),     # kroks.ru: «Разъем 11-211L MCX(female) на кабели RG-174, RG-316»
    ("MCX-female RG-316", "RG-316", "11-211L", "RG-316"),
    ("MHF1-plug RG-178", "RG-178", "U.FL", "RG-178"),         # gsm-repiteri.ru 3148 — одна страница, один код, один кабель
    ("Fakra Z-Female", "RG-174", "FAKRA-female (тип Z) для RG-174/RG-316", "RG-174"),  # gsm-repiteri.ru 3166 — одна страница
    ("Fakra Z-Female", "RG-316", "FAKRA-female (тип Z) для RG-174/RG-316", "RG-316"),
    ("MMCX-male RG-174", "RG-174", "Kroks-1440", "RG-174"),   # одна страница kroks.ru и тот же кабель; у поставщика номера детали нет
    ("MMCX-male RG-316", "RG-316", "Kroks-1440", "RG-316"),
]
# G → номер детали из названия товара; третье — «Наименование», если у строки пусто.
RENAMES = [
    ("N-female RG-142", "RG-142", "N-211F", ""), ("N-female RG-223", "RG-223", "N-211F", ""),
    ("N-female RG-400", "RG-400", "N-211F", ""),
    ("N-male RG-142", "RG-142", "N-111F", ""), ("N-male RG-223", "RG-223", "N-111F", ""),
    ("N-male RG-400", "RG-400", "N-111F", ""),
    ("SMA-female RG-141", "RG-141", "S-211F", ""), ("SMA-female RG-223", "RG-223", "S-211F", ""),
    ("SMA-male R/A RG-223", "RG-223", "S-121F", ""),
    ("MCX-male RG-316", "RG-316", "11-111L", "Разъем 11-111L MCX(male) на кабели RG-174, RG-316, обжимной"),
    ("SMA-Male R/A RG178", "RG-178", "S-121L", "Разъем S-121L SMA(male) на кабели RG-174, RG-178, RG-316, угловой, обжимной"),
    ("MMCX-male RG-178", "RG-178", "MMCX-J2", ""),
    ("MMCX-male R/A RG-178", "RG-178", "MMCX-JW2", ""),
]
HISTORY_TABLES = frozenset({
    "audit_events", "outbox_messages", "tech_operation_snapshots", "warehouse_writeoffs", "reference_stock_links",
    "receiving_lines", "receiving_files", "procurement_supplier_orders", "procurement_supplier_order_documents",
    "procurement_files", "finished_goods_moves", "worker_heartbeats", "operations_job_marks", "notifications",
})
SKIP_TABLES = frozenset({"warehouse_lots"}) | {
    name for name in ("identity_users", "identity_credentials", "identity_identifiers", "identity_role_access",
                      "identity_user_access", "identity_level_access", "identity_user_level_access", "identity_sessions",
                      "identity_api_tokens", "identity_leave_requests", "identity_leave_signatures", "identity_documents",
                      "identity_document_versions", "identity_document_acks", "identity_departments", "identity_settings",
                      "identity_login_throttles")}


def fold(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def article_of(record) -> str:
    return str(record.fields.get("coaxArticle") or record.fields.get("Артикул") or "").strip()


def cable_of(record) -> str:
    return str(record.fields.get("coaxWire") or record.fields.get("Провод") or "").strip()


def sellers(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in str(value or "").split(";"):
        if ":" in part:
            name, code = part.split(":", 1)
            result[name.strip()] = code.strip()
        elif part.strip():
            result[part.strip()] = ""
    return result


def join_sellers(values: dict[str, str]) -> str:
    return "; ".join(f"{name}: {code}" if code else name for name, code in values.items())


def shared_names(*field_dicts) -> list[str]:
    """Свойства разъёма, общие у всех строк артикула (COAX_ROW_OWN — своё у строки)."""
    names = set()
    for fields in field_dicts:
        names |= {n for n in fields if n not in COAX_ROW_OWN and not is_coax_cable_column(n)
                  and not n.startswith("coax") and n not in ("Поставщик",)}  # coax* — ключи схемы, идут за столбцами листа
    return sorted(names)


def scan_references(url: str, articles: list[str]) -> dict[str, list]:
    """артикул → [(вид, таблица, колонка, число строк, образец)]; вид «история» | «живая»."""
    patterns = {art: re.compile(r"(?<![\w-])" + re.escape(fold(art)) + r"(?![\w-])") for art in articles}
    found: dict[str, dict] = {art: {} for art in articles}
    engine = create_engine(url)
    inspector = inspect(engine)
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            if table in SKIP_TABLES:
                continue
            columns = [c["name"] for c in inspector.get_columns(table)
                       if isinstance(c["type"], (String, Text, JSON)) or "JSON" in type(c["type"]).__name__.upper()]
            if not columns:
                continue
            where = "WHERE tab <> 'coax'" if table == "tech_operation_records" else ""
            select = ", ".join(f'CAST("{name}" AS TEXT)' for name in columns)
            kind = "история" if table in HISTORY_TABLES else "живая"
            for row in connection.execute(text(f'SELECT {select} FROM "{table}" {where}')):
                for index, value in enumerate(row):
                    if not value:
                        continue
                    low = fold(value)
                    for art, pattern in patterns.items():
                        if fold(art) in low and pattern.search(low):
                            key = (kind, table, columns[index])
                            count, sample = found[art].get(key, (0, ""))
                            found[art][key] = (count + 1, sample or str(value)[:80])
    return {art: [(k[0], k[1], k[2], c, s) for k, (c, s) in items.items()] for art, items in found.items()}


def read_lots(url: str):
    with create_engine(url).connect() as connection:
        rows = connection.execute(text(
            "SELECT id, article, inbound, written_off, reserved FROM warehouse_lots")).mappings().all()
    return [dict(row) for row in rows]


def stock_totals(lots):
    by: dict[str, list] = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    for lot in lots:
        item = by[fold(lot["article"])]
        item[0] += 1
        item[1] += Decimal(str(lot["inbound"])) - Decimal(str(lot["written_off"]))
        item[2] += Decimal(str(lot["reserved"]))
    return {k: tuple(v) for k, v in by.items()}


class Run:
    """Один проход: журнал отката, счётчики, правка ячеек с событием."""

    def __init__(self, factory, dry: bool, now):
        self.factory, self.dry, self.now = factory, dry, now
        self.log: list[list[str]] = []
        self.counts = defaultdict(int)

    def edit(self, uow, record_id: UUID, edits: list[tuple[str, str, str]], reason: str):
        """edits: [(поле, было, стало)] — чанками MAX_ROW_EDITS; пишет журнал отката и событие."""
        rec = uow.tech_operations.get(record_id)
        art, cab = article_of(rec), cable_of(rec)
        for i in range(0, len(edits), MAX_ROW_EDITS):
            chunk = edits[i:i + MAX_ROW_EDITS]
            _edit_in(record_id, tuple(FieldEdit(f, o, n) for f, o, n in chunk), self.now, uow)
            _audit("references.tech_record_edited", ACTOR, self.now, {
                "record_id": str(record_id), "tab": "coax", "fields": [f for f, _, _ in chunk], "reason": reason}, uow)
            for f, o, n in chunk:
                self.log.append(["ячейка", str(record_id), art, cab, f, o, n])
            self.counts["ячеек"] += len(chunk)
        return uow.tech_operations.get(record_id)

    def delete(self, uow, record_id: UUID, reason: str):
        rec = uow.tech_operations.get(record_id)
        uow.tech_operations.delete(record_id)
        details = {"record_id": str(rec.id), "tab": rec.tab.value, "display_text": rec.display_text,
                   "sort_key": rec.sort_key, "source_sheet": rec.source_sheet, "mirror_key": rec.mirror_key,
                   "edited_in_erp": rec.edited_in_erp, "fields": dict(rec.fields), "reason": reason}
        _audit("references.record_deleted", ACTOR, self.now, details, uow)
        self.log.append(["удалена", str(rec.id), article_of(rec), cable_of(rec), "", json.dumps({
            "display_text": rec.display_text, "normalized_search": rec.normalized_search, "source_sheet": rec.source_sheet,
            "sort_key": rec.sort_key, "mirror_key": rec.mirror_key, "edited_in_erp": rec.edited_in_erp,
            "fields": dict(rec.fields)}, ensure_ascii=False), ""])
        self.counts["удалено"] += 1


def coax_rows(uow):
    return list(uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000))


def blockers(refs, lots, article) -> list[str]:
    live = [f"{t}.{c} × {n} «{s}»" for kind, t, c, n, s in refs.get(article, []) if kind == "живая"]
    mine = [l for l in lots if fold(l["article"]) == fold(article)]
    return live + ([f"партий склада: {len(mine)}"] if mine else [])


def merge_into(run: Run, uow, g, r, label: str) -> list[str]:
    """Пустые свойства R берутся у G (если у остальных строк артикула R они пусты), разное — остаётся R, старое G в «Источник»."""
    rows_r = [x for x in coax_rows(uow) if fold(article_of(x)) == fold(article_of(r))]
    notes: list[str] = []
    gf = dict(g.fields)
    for name in shared_names(gf, r.fields):
        want, have = str(gf.get(name, "")).strip(), str(r.fields.get(name, "")).strip()
        if not want:
            continue
        have = str(uow.tech_operations.get(r.id).fields.get(name, "")).strip()  # свежее значение: прошлые правки могли его сдвинуть
        if name == CODES:
            merged = sellers(have)
            for seller, code in sellers(want).items():
                if seller not in merged or (code and not merged[seller]):
                    merged[seller] = code
            new = join_sellers(merged)
            if new != have:
                for row in rows_r:
                    run.edit(uow, row.id, [(name, str(uow.tech_operations.get(row.id).fields.get(name, "")), new)], label)
            continue
        if not have:
            others = [str(uow.tech_operations.get(x.id).fields.get(name, "")).strip() for x in rows_r if x.id != r.id]
            if all(not o for o in others):
                for row in rows_r:
                    run.edit(uow, row.id, [(name, str(uow.tech_operations.get(row.id).fields.get(name, "")), want)], label)
            else:
                notes.append(f"{name}: у «{article_of(g)}» «{want}» (у других строк артикула иначе — не перенесено)")
        elif have.replace(",", ".") != want.replace(",", "."):
            notes.append(f"{name}: было у «{article_of(g)}» «{want}», оставлено «{have}»")
    offer = ("Ссылка", "Цена, ₽", "Дата цены")
    cur = uow.tech_operations.get(r.id)
    if not any(str(cur.fields.get(n, "")).strip() for n in offer) and any(str(gf.get(n, "")).strip() for n in offer):
        run.edit(uow, r.id, [(n, str(cur.fields.get(n, "")), str(gf.get(n, "")).strip()) for n in offer
                             if str(gf.get(n, "")).strip()], label)
    return notes


def append_note(run: Run, uow, record_id: UUID, note: str, label: str):
    rec = uow.tech_operations.get(record_id)
    old = str(rec.fields.get(SOURCE, "")).strip()
    new = f"{old}; {note}" if old else note
    run.edit(uow, record_id, [(SOURCE, old, new)], label)


def do_marked(run: Run, factory, url, lots, report):
    with factory() as uow:
        rows = [x for x in coax_rows(uow) if str(x.fields.get(NOTE, "")).strip().startswith(MARK)]
    refs = scan_references(url, sorted({article_of(x) for x in rows}))
    by_art = defaultdict(list)
    for x in rows:
        by_art[article_of(x)].append(x)
    for art, group in sorted(by_art.items()):
        why = blockers(refs, lots, art)
        if why:
            report.append(("НЕ удалена (пометка «дубль»)", art, "; ".join(why)))
            continue
        for g in group:
            with factory() as uow:
                try:
                    run.delete(uow, g.id, "слитый дубль («" + str(g.fields.get(NOTE, "")).strip() + "»): " + art)
                    if not run.dry:
                        uow.commit()
                    report.append(("удалена (пометка «дубль»)", f"{art} × {cable_of(g)}", str(g.fields.get(NOTE, "")).strip()))
                except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                    report.append(("ОТКАЗ удаления", f"{art} × {cable_of(g)}", str(error)))


def do_merge(run: Run, factory, url, lots, report):
    refs = scan_references(url, sorted({g for g, *_ in MERGE_DELETE}))
    blocked = {g: blockers(refs, lots, g) for g, *_ in MERGE_DELETE}
    for g_art, g_cab, r_art, r_cab in MERGE_DELETE:
        label = f"слияние дубля «{g_art}» в «{r_art}» (тот же товар и кабель, {DATE})"
        with factory() as uow:
            allrows = coax_rows(uow)
            g = next((x for x in allrows if fold(article_of(x)) == fold(g_art) and cable_key(cable_of(x)) == cable_key(g_cab)), None)
            r = next((x for x in allrows if fold(article_of(x)) == fold(r_art) and cable_key(cable_of(x)) == cable_key(r_cab)), None)
            if g is None:
                report.append(("уже сделано (слияние)", f"{g_art} × {g_cab}", ""))
                continue
            if r is None:
                report.append(("ОТКАЗ слияния", f"{g_art} × {g_cab}", f"нет строки R «{r_art}» × {r_cab}"))
                continue
            if blocked[g_art]:
                report.append(("НЕ слита (ссылки)", f"{g_art} × {g_cab}", "; ".join(blocked[g_art])))
                continue
            try:
                notes = merge_into(run, uow, g, r, label)
                if notes:
                    append_note(run, uow, r.id, f"{DATE}: слита строка «{g_art}» (тот же товар): " + "; ".join(notes), label)
                run.delete(uow, g.id, label)
                if not run.dry:
                    uow.commit()
                report.append(("слита и удалена", f"{g_art} × {g_cab} → {r_art}", "; ".join(notes)))
            except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                report.append(("ОТКАЗ слияния", f"{g_art} × {g_cab}", str(error)))


def do_renames(run: Run, factory, url, lots, report):
    refs = scan_references(url, sorted({g for g, *_ in RENAMES}))
    for g_art, cab, part, title in RENAMES:
        label = f"артикул по названию товара: «{g_art}» → «{part}» ({DATE})"
        with factory() as uow:
            allrows = coax_rows(uow)
            g = next((x for x in allrows if fold(article_of(x)) == fold(g_art) and cable_key(cable_of(x)) == cable_key(cab)), None)
            if g is None:
                done = any(fold(article_of(x)) == fold(part) and cable_key(cable_of(x)) == cable_key(cab) for x in allrows)
                report.append(("уже сделано (переименование)" if done else "пропуск (строки нет)", f"{g_art} × {cab}", ""))
                continue
            twins = [x for x in allrows if fold(article_of(x)) == fold(g_art)]
            if len(twins) > 1:
                report.append(("НЕ переименована", f"{g_art} × {cab}", "у артикула несколько строк"))
                continue
            if any(fold(article_of(x)) == fold(part) and cable_key(cable_of(x)) == cable_key(cab) for x in allrows):
                report.append(("НЕ переименована", f"{g_art} × {cab}", f"ключ «{part}» × {cab} уже занят"))
                continue
            why = blockers(refs, lots, g_art)
            if why:
                report.append(("НЕ переименована (ссылки)", f"{g_art} × {cab}", "; ".join(why)))
                continue
            rows_r = [x for x in allrows if fold(article_of(x)) == fold(part)]
            try:
                changed: list[str] = []
                if rows_r:
                    # 1. пустое у R, но есть у G — в строки артикула R (если они все пусты); коды поставщика — объединение
                    for name in shared_names(g.fields, *(x.fields for x in rows_r)):
                        want = str(g.fields.get(name, "")).strip()
                        vals = [str(uow.tech_operations.get(x.id).fields.get(name, "")).strip() for x in rows_r]
                        if not want:
                            continue
                        if name == CODES:
                            merged = sellers(vals[0])
                            for seller, code in sellers(want).items():
                                if seller not in merged or (code and not merged[seller]):
                                    merged[seller] = code
                            new = join_sellers(merged)
                            for row in rows_r:
                                old = str(uow.tech_operations.get(row.id).fields.get(name, ""))
                                if new != old:
                                    run.edit(uow, row.id, [(name, old, new)], label)
                        elif all(not v for v in vals):
                            for row in rows_r:
                                run.edit(uow, row.id, [(name, str(uow.tech_operations.get(row.id).fields.get(name, "")), want)], label)
                    # 2. G приводится к значениям артикула (по первой строке R после дополнения)
                    ref = uow.tech_operations.get(next(iter(rows_r)).id)
                    gcur = uow.tech_operations.get(g.id)
                    edits = []
                    for name in shared_names(gcur.fields, ref.fields):
                        have, target = str(gcur.fields.get(name, "")).strip(), str(ref.fields.get(name, "")).strip()
                        if target and have != target:
                            edits.append((name, have, target))
                            if have:
                                changed.append(f"{name}: было «{have}»")
                    if edits:
                        run.edit(uow, g.id, edits, label)
                gcur = uow.tech_operations.get(g.id)
                post = []
                if title and not str(gcur.fields.get("Наименование", "")).strip():
                    post.append(("Наименование", "", title))
                if post:
                    run.edit(uow, g.id, post, label)
                note = f"{DATE}: артикул «{g_art}» → «{part}» (номер детали из названия товара)"
                if changed:
                    note += "; значения приведены к строкам артикула " + part + " («документация превыше»): " + "; ".join(changed)
                gcur = uow.tech_operations.get(g.id)
                old = str(gcur.fields.get(SOURCE, "")).strip()
                run.edit(uow, g.id, [(SOURCE, old, f"{old}; {note}" if old else note)], label)
                gcur = uow.tech_operations.get(g.id)
                run.edit(uow, g.id, [("Артикул", str(gcur.fields.get("Артикул", "")), part)], label)
                after = uow.tech_operations.get(g.id)
                if article_of(after) != part:
                    raise ReferenceValidationError(f"после правки артикул «{article_of(after)}», ждали «{part}»")
                if not run.dry:
                    uow.commit()
                run.counts["переименовано"] += 1
                if rows_r:
                    run.counts["выровнено"] += 1
                report.append(("переименована", f"{g_art} × {cab} → {part}", "; ".join(changed)))
            except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                report.append(("ОТКАЗ переименования", f"{g_art} × {cab}", str(error)))


def main(mode: str, arg: str = "") -> int:
    url = os.environ["ERP_DATABASE_URL"]
    factory = build_references_uow_factory(build_engine(url))
    if mode == "rollback":
        return rollback(arg, factory)
    dry = mode == "dry"
    lots = read_lots(url)
    before = stock_totals(lots)
    with factory() as uow:
        total_before = len(coax_rows(uow))
    run = Run(factory, dry, moscow_now())
    report: list[tuple] = []
    # В dry все группы идут в одной общей сессии без commit (видно то, что будет при записи); выход без commit — откат.
    if dry:
        with factory() as probe:
            shared = lambda: _Shared(probe)  # noqa: E731
            do_marked(run, shared, url, lots, report)
            do_merge(run, shared, url, lots, report)
            do_renames(run, shared, url, lots, report)
    else:
        do_marked(run, factory, url, lots, report)
        do_merge(run, factory, url, lots, report)
        do_renames(run, factory, url, lots, report)
    print(f"строк БД.КОАКС до: {total_before}; партий склада: {len(lots)}")
    for kind, what, detail in report:
        print(f"   {kind}: {what}" + (f" — {detail}" if detail else ""))
    after_lots = read_lots(url)
    same = stock_totals(after_lots) == before and len(after_lots) == len(lots)
    print(f"\nостатки склада по артикулам (партий, приход − списано, резерв) до и после: {'РАВНЫ' if same else 'РАЗНИЦА'}")
    if not dry:
        with factory() as uow:
            print(f"строк БД.КОАКС после: {len(coax_rows(uow))}")
        print("\n== CSV-журнал отката ==")
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["вид", "id", "артикул", "кабель", "поле", "было", "стало"])
        writer.writerows(run.log)
        print(buffer.getvalue())
    bad = sum(1 for k, _, _ in report if k.startswith("ОТКАЗ"))
    kept = sum(1 for k, _, _ in report if k.startswith("НЕ "))
    print("ИТОГО", json.dumps({**run.counts, "отказов": bad, "оставлено из-за ссылок/ключа": kept, "остатки равны": same},
                              ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
    return 1 if bad or not same else 0


class _Shared:
    """Обёртка dry: общая сессия, выход из `with` ничего не фиксирует и не закрывает."""

    def __init__(self, uow):
        self.uow = uow

    def __enter__(self):
        return self.uow

    def __exit__(self, *exc):
        return False


def rollback(path: str, factory) -> int:
    now = moscow_now()
    problems = 0
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in reversed(rows):
        rid = UUID(row["id"])
        with factory() as uow:
            try:
                if row["вид"] == "удалена":
                    data = json.loads(row["было"])
                    if uow.tech_operations.get(rid) is not None:
                        print(f"   уже есть: {row['артикул']} × {row['кабель']}")
                        continue
                    record = TechOperationRecord(
                        id=rid, tab=TechOperationTab.COAX, display_text=data["display_text"],
                        normalized_search=data["normalized_search"], source_sheet=data["source_sheet"],
                        sort_key=data["sort_key"], fields=data["fields"], mirror_key=data["mirror_key"],
                        edited_in_erp=data["edited_in_erp"])
                    uow.tech_operations.save(record)
                    _audit("references.record_restored", ACTOR, now, {"record_id": str(rid), "reason": "откат 20.10.2026"}, uow)
                    print(f"   возвращена строка {row['артикул']} × {row['кабель']}")
                else:
                    _edit_in(rid, (FieldEdit(row["поле"], row["стало"], row["было"]),), now, uow)
                    _audit("references.tech_record_edited", ACTOR, now, {
                        "record_id": row["id"], "tab": "coax", "fields": [row["поле"]], "reason": "откат 20.10.2026"}, uow)
                    print(f"   откат: {row['поле']} «{row['стало'][:40]}» → «{row['было'][:40]}» ({row['артикул']})")
                uow.commit()
            except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                problems += 1
                print(f"   отказ: {row['артикул']} {row['поле']}: {error}")
    print("ИТОГО откат, проблем:", problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run_mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
    if run_mode not in ("dry", "apply", "rollback"):
        raise SystemExit("режим: dry | apply | rollback <csv>")
    raise SystemExit(main(run_mode, sys.argv[2] if len(sys.argv) > 2 else ""))
