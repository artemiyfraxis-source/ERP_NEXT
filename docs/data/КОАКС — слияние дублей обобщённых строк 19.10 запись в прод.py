"""Запись в прод 19.10.2026 — БД.КОАКС: слияние «обобщённых» строк (серия + кабель: «SMA-Male 141», «SMA-male RG-402»…) со
строками настоящего артикула (S-115-141, S-111L…). Владелец 19.10: один артикул — одна строка (память one-article-one-row).

Пара G → R (G — обобщённая строка, R — строка настоящего артикула, КАБЕЛЬ ТОТ ЖЕ) находится сама, по данным базы: в
«Наименовании» G стоит «Разъём <номер детали> (…)» (его проставила запись «реальные артикулы и коды поставщика 19.10»), и у
того же кабеля есть строка R с «Артикулом» = номер детали. Строки без пары (нет R) не трогаются, они в списке «без пары».
G, у которой в «Наименовании» стоит её собственный артикул («Разъём S-231L NGT (…)» при «Артикуле» «S-231L NGT»), — свой товар,
не обобщённая строка: в пары не берётся. Длины L1–L3 из G в пустую ячейку R копируются только если у G они подтверждены документом
(«L1–L3:» в её «Источнике данных»), цеховое значение без документа в R не переносится (владелец 19.10: документация превыше).

Что делает (журнал — CSV в конце вывода, откат — режим rollback):
  1. Значения, ячейка за ячейкой. У G есть значение, у R пусто — копируется в R (тем же путём, что «Изменить» в «Базе
     данных», с защитой old: ячейка R должна быть пустой; правка тех же свойств идёт и в остальные строки артикула R, как у
     правки в экране: «у одного артикула одни значения»). Заполнено у обоих и РАЗНОЕ (в том числе у другой строки артикула
     R) — G НЕ сливается совсем, строка попадает в список «разногласия» с обоими значениями. «Артикул поставщика» —
     объединение по продавцам (код того же продавца у обоих разный — разногласие). «Ссылка / Цена / Дата цены / Поставщик» —
     предложение строки: копируется целиком, только когда у R все четыре пусты; иначе остаётся предложение R (оно
     дешевле или то, что выбрано). «Наименование», «Примечание», «Источник данных» у R не трогаются.
  2. Ссылки на G по имени: во всех таблицах (кроме журналов и самой БД.КОАКС) ищется целое слово «Артикул G»; находки
     по таблицам и колонкам выводятся. Историческими считаются списания, приёмка, заявки поставщику, журнал связей
     справочника и журналы изменений (их имя остаётся как было, их не трогаем). Остальное — живая ссылка (комплекты,
     техкарты и их ревизии, ЭТК-черновики, шаблоны, комплектация…): G не трогается совсем (ни партии, ни пометка), список —
     «ссылки остались». Комплекты и конфигурации техкарт скрипт НЕ правит никогда.
  3. Партии (warehouse_lots) с «Артикул» = артикулу G (без учёта регистра и пробелов): переписываются на артикул R ТОЛЬКО
     операцией приложения warehouse.application.edit_lot_identity (с версией партии и записью warehouse.identity_edited в
     журнал): меняется только артикул, наименование и серия партии передаются как были; количества, приход/списано/
     резерв, движения и списания не меняются (проверяется: снимок всех партий до и после равен, кроме артикула и версии).
     Партию нельзя отнести к кабелю, поэтому она переписывается, только если ВСЕ строки БД.КОАКС с этим артикулом G готовы к
     слиянию и ведут на один артикул R; иначе остаётся, G не трогается.
  4. Удаления строки справочника в приложении нет — G помечается «Примечание: дубль → <артикул R> (19.10.2026)» (только когда
     значения слиты, партии переписаны и живых ссылок нет). Остальные строки не удаляются.
  5. Сверка остатков: по каждому артикулу R — сумма (приход − списано) и резерв партий «G + R» до записи равна сумме у R
     после; по всем партиям — число партий и все количества равны.

Порядок на сервере (cd /opt/erp/app/deploy; compose ... run --rm -T api python - <режим> < этот файл):
  dry        — проверка, ничего не пишется: пары, ячейки, партии, пометки, ссылки, остатки
  apply      — запись (ПЕРЕД ней: sudo -n bash /opt/erp/app/deploy/erp.sh backup-now); в конце CSV-журнал отката
  dry        — после записи: «уже слито», остатки равны
  rollback <csv> — откат по журналу (compose run -v $PWD/журнал.csv:/tmp/журнал.csv ... python - rollback /tmp/журнал.csv)
"""
import csv
import io
import json
import os
import re
import sys
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, String, Text, create_engine, inspect, text

from app.database import build_engine, build_references_uow_factory, build_warehouse_uow_factory
from app.modules.references.application import FieldEdit, _audit, _coax_card_columns, _edit_in, _same_article, custom_columns
from app.modules.references.domain import MAX_ROW_EDITS, ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.modules.references.technology import is_coax_cable_column, schema_keys
from app.modules.warehouse.application import EditLotIdentityCommand, edit_lot_identity
from app.shared.clock import moscow_now
from app.shared.identity import Actor

ACTOR = Actor(id=UUID(int=0), name="Слияние дублей БД.КОАКС 19.10.2026", department="System")
REASON = "слияние обобщённой строки со строкой настоящего артикула (19.10.2026)"
NOTE = "Примечание"
NOTE_PREFIX = "дубль → "
SUPPLIER_CODE = "Артикул поставщика"
OFFER = ("Поставщик", "Ссылка", "Цена, ₽", "Дата цены")
# Не сравниваются и не копируются: ключ строки, свои поля строки.
LENGTHS = frozenset({"L1", "L2", "L3", "L+", "L-"})
OWN = frozenset({"Артикул", "Провод", "Наименование", NOTE, "Источник данных", SUPPLIER_CODE, *OFFER})
PART_NUMBER = re.compile(r"^Разъ[её]м\s+([^\s(]+)")
# Журналы и истории: имя в них остаётся как было, не блокирует.
HISTORY_TABLES = frozenset({
    "audit_events", "outbox_messages", "tech_operation_snapshots", "warehouse_writeoffs", "reference_stock_links",
    "receiving_lines", "receiving_files", "procurement_supplier_orders", "procurement_supplier_order_documents",
    "procurement_files", "finished_goods_moves", "worker_heartbeats", "operations_job_marks", "notifications",
})
SKIP_TABLES = frozenset({"warehouse_lots"}) | {  # партии — отдельно; люди и доступ — не про артикулы
    name for name in ("identity_users", "identity_credentials", "identity_identifiers", "identity_role_access",
                      "identity_user_access", "identity_level_access", "identity_user_level_access", "identity_sessions",
                      "identity_api_tokens", "identity_leave_requests", "identity_leave_signatures", "identity_documents",
                      "identity_document_versions", "identity_document_acks", "identity_departments", "identity_settings",
                      "identity_login_throttles")}


def fold(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def same(a: object, b: object) -> bool:
    return fold(str(a).replace(",", ".")) == fold(str(b).replace(",", "."))


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


@dataclass
class Pair:
    g: object  # TechOperationRecord
    r: object
    part: str
    copies: list = field(default_factory=list)       # (поле, было у R, стало, идёт ли в остальные строки артикула R)
    conflicts: list = field(default_factory=list)    # (поле, значение G, значение R / соседа)
    offer_kept: str = ""
    merged: bool = False        # значения слиты (в базе уже или будут)
    refs: list = field(default_factory=list)         # (вид, таблица, колонка, число, образец)
    lots: list = field(default_factory=list)         # ids партий G
    lots_skipped: str = ""
    mark: bool = False
    already_marked: bool = False


def sheet(fields_: dict) -> dict[str, str]:
    keys = schema_keys("coax")
    return {name: str(value or "").strip() for name, value in fields_.items()
            if name not in keys and not is_coax_cable_column(name)}


def article_of(record) -> str:
    return str(record.fields.get("coaxArticle") or record.fields.get("Артикул") or "").strip()


def cable_of(record) -> str:
    return str(record.fields.get("coaxWire") or record.fields.get("Провод") or "").strip()


def find_pairs(records):
    by_key = {(fold(article_of(r)), cable_key(cable_of(r))): r for r in records}
    pairs, unpaired = [], []
    for g in records:
        mfr = str(g.fields.get("Производитель") or g.fields.get("coaxMfr") or "")
        if "trl" in mfr.casefold() or article_of(g).upper().startswith("ISMA"):
            continue
        match = PART_NUMBER.match(str(g.fields.get("Наименование") or "").strip())
        if not match:
            continue
        part = match.group(1)
        if fold(part) == fold(article_of(g)):
            continue
        # «Разъём S-231L NGT (…)»: настоящий артикул G — всё до скобки, а не первое слово; такая G — свой товар, не обобщённая
        if fold(match.string.strip()).startswith(fold(f"Разъём {article_of(g)} (")):
            continue
        real = by_key.get((fold(part), cable_key(cable_of(g))))
        if real is None or real.id == g.id:
            unpaired.append((g, part))
            continue
        pairs.append(Pair(g=g, r=real, part=part))
    return pairs, unpaired


def plan_values(pair: Pair, state: dict, groups: dict, known: set[str]) -> None:
    """Расчёт ячеек на рабочем состоянии `state` (id → поля); пара обновляет `state` как это сделает запись."""
    g, r = state[pair.g.id], state[pair.r.id]
    gs, rs = sheet(g), sheet(r)
    siblings = [sheet(state[i]) for i in groups[fold(article_of(pair.r))] if i != pair.r.id]
    sibling_ids = [i for i in groups[fold(article_of(pair.r))] if i != pair.r.id]
    edits: list[tuple[str, str, str, bool]] = []  # поле, было, стало, идёт ли в остальные строки артикула R
    g_documented = "L1–L3:" in gs.get("Источник данных", "") or "L1–L3:" in str(g.get("Источник данных", ""))
    for name in sorted(set(gs) - OWN):
        want = gs[name]
        if not want:
            continue
        have = rs.get(name, "")
        if not have and name in LENGTHS and not g_documented:
            continue  # длины без документа у G (цеховое значение) в пустую ячейку R не копируются
        if have:
            if not same(have, want):
                pair.conflicts.append((name, want, have))
            continue
        clash = [s_[name] for s_ in siblings if s_.get(name) and not same(s_[name], want)]
        if clash:
            pair.conflicts.append((name, want, f"{clash[0]} (у другой строки артикула {pair.r.fields.get('Артикул')})"))
            continue
        edits.append((name, "", want, True))
    code_r = sellers(rs.get(SUPPLIER_CODE, ""))
    merged = dict(code_r)
    code_conflict = False
    for name, code in sellers(gs.get(SUPPLIER_CODE, "")).items():
        have = merged.get(name, "")
        if have and code and have != code:
            pair.conflicts.append((f"{SUPPLIER_CODE} ({name})", code, have))
            code_conflict = True
        elif name not in merged or (code and not have):
            merged[name] = code
    new_code = join_sellers(merged)
    if not code_conflict and new_code != rs.get(SUPPLIER_CODE, ""):
        odd = [s_[SUPPLIER_CODE] for s_ in siblings if s_.get(SUPPLIER_CODE) not in (None, "", rs.get(SUPPLIER_CODE, ""), new_code)]
        if odd:
            pair.conflicts.append((SUPPLIER_CODE, new_code, f"{odd[0]} (у другой строки артикула)"))
        else:
            edits.append((SUPPLIER_CODE, rs.get(SUPPLIER_CODE, ""), new_code, True))
    offer_g = {n: gs.get(n, "") for n in OFFER}
    offer_r = {n: rs.get(n, "") for n in OFFER}
    if any(offer_g.values()):
        if not any(offer_r.values()):
            edits += [(n, "", v, False) for n, v in offer_g.items() if v]
        elif any(offer_g[n] and offer_g[n] != offer_r[n] for n in OFFER):
            pair.offer_kept = "; ".join(f"{n}: у G «{offer_g[n]}», у R «{offer_r[n]}»" for n in OFFER
                                        if offer_g[n] and offer_g[n] != offer_r[n])
    if pair.conflicts:
        pair.copies, pair.merged = [], False
        return
    pair.copies, pair.merged = edits, True
    for name, _, new, shared in edits:  # рабочее состояние после записи (как _same_article у правки в экране)
        r[name] = new
        if shared:
            for sibling_id in sibling_ids:
                if name in state[sibling_id] or name in known:
                    state[sibling_id][name] = new


def scan_references(url: str, articles: dict[str, str]):
    """artikul (строка G) → [(вид, таблица, колонка, число строк, образец)]; вид: «история» | «живая»."""
    patterns = {art: re.compile(r"(?<![\w-])" + re.escape(fold(art)) + r"(?![\w-])") for art in articles}
    found: dict[str, dict] = {art: {} for art in articles}
    engine = create_engine(url)
    inspector = inspect(engine)
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            if table in SKIP_TABLES:
                continue
            columns = [c["name"] for c in inspector.get_columns(table) if isinstance(c["type"], (String, Text, JSON))
                       or "JSON" in type(c["type"]).__name__.upper()]
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
            "SELECT id, version, warehouse, grn, package, article, series, name, inbound, written_off, reserved, storage_location "
            "FROM warehouse_lots")).mappings().all()
    return [dict(row) for row in rows]


def lot_id(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def totals(lots, articles: set[str]):
    selected = [lot for lot in lots if fold(lot["article"]) in articles]
    return (len(selected), sum(Decimal(str(l["inbound"])) - Decimal(str(l["written_off"])) for l in selected),
            sum(Decimal(str(l["reserved"])) for l in selected))


def snapshot(lots):
    return {lot_id(l["id"]): tuple(str(l[k]) for k in ("warehouse", "grn", "package", "series", "name", "inbound", "written_off",
                                                        "reserved", "storage_location")) for l in lots}


def make_plan(url: str, factory):
    with factory() as uow:
        records = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
        known = set(_coax_card_columns()) | set(custom_columns(TechOperationTab.COAX, uow))
    state = {r.id: dict(r.fields) for r in records}
    groups: dict[str, list] = defaultdict(list)
    for r in records:
        groups[fold(article_of(r))].append(r.id)
    pairs, unpaired = find_pairs(records)
    for pair in pairs:
        plan_values(pair, state, groups, known)
    refs = scan_references(url, {article_of(p.g): "" for p in pairs})
    lots = read_lots(url)
    by_g_article: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        by_g_article[fold(article_of(pair.g))].append(pair)
    for pair in pairs:
        pair.refs = refs[article_of(pair.g)]
        live = [item for item in pair.refs if item[0] == "живая"]
        mine = [l for l in lots if fold(l["article"]) == fold(article_of(pair.g))]
        family = by_g_article[fold(article_of(pair.g))]
        every_row = groups[fold(article_of(pair.g))]
        targets = {fold(article_of(p.r)) for p in family}
        if not pair.merged:
            pair.lots_skipped = "значения не слиты (разногласия)" if mine else ""
        elif live:
            pair.lots_skipped = "есть живые ссылки" if mine else ""
        elif mine and (len(family) != len(every_row) or any(not p.merged for p in family) or len(targets) != 1):
            pair.lots_skipped = "партию нельзя отнести к одному кабелю/артикулу R"
        else:
            pair.lots = [lot_id(l["id"]) for l in mine]
        already = fold(str(state[pair.g.id].get(NOTE) or "")).startswith(NOTE_PREFIX.strip().casefold())
        pair.mark = pair.merged and not live and not pair.lots_skipped and not already
        pair.already_marked = already
    return records, state, pairs, unpaired, lots


def planned_lots(lots, pairs):
    """Партии, какими они станут после записи (для dry): артикул переписываемых — артикул R."""
    target = {lid: article_of(p.r) for p in pairs for lid in p.lots}
    return [dict(l, article=target.get(lot_id(l["id"]), l["article"])) for l in lots]


def write_cells(factory, pair, probe, now):
    """Правка ячеек R: свойства разъёма — как «Изменить» в экране (с правкой остальных строк артикула), предложение — только
    в строку R. dry — одна общая сессия без commit, apply — commit на каждую правку."""
    groups = (tuple(FieldEdit(n, o, v) for n, o, v, shared in pair.copies if shared),
              tuple(FieldEdit(n, o, v) for n, o, v, shared in pair.copies if not shared))
    for shared, edits in zip((True, False), groups):
        for i in range(0, len(edits), MAX_ROW_EDITS):
            chunk = edits[i:i + MAX_ROW_EDITS]
            with (nullcontext(probe) if probe is not None else factory()) as uow:
                record, updated = _edit_in(pair.r.id, chunk, now, uow)
                if shared:
                    _same_article(updated, chunk, now, uow)
                if probe is None:
                    _audit("references.tech_record_edited", ACTOR, now, {
                        "record_id": str(record.id), "tab": record.tab.value,
                        "fields": [e.field for e in chunk], "reason": REASON}, uow)
                    uow.commit()


def print_pairs(pairs, unpaired):
    print("\n== пары и ячейки ==")
    for pair in pairs:
        head = f"«{article_of(pair.g)}» → «{article_of(pair.r)}» × {cable_of(pair.g)}"
        if pair.conflicts:
            print(f"   РАЗНОГЛАСИЕ (не сливается): {head}")
            for name, g, r in pair.conflicts:
                print(f"        {name}: у G «{g}», у R «{r}»")
            for kind, table, column, count, sample in pair.refs:
                print(f"        ссылка ({kind}): {table}.{column} × {count} «{sample}»")
            continue
        status = "уже слито" if not pair.copies and not pair.mark and not pair.lots else "к записи"
        print(f"   {status}: {head}")
        for name, old, new, _ in pair.copies:
            print(f"        ячейка R: {name}: «{old}» → «{new}»")
        if pair.offer_kept:
            print(f"        предложение осталось у R ({pair.offer_kept})")
        for kind, table, column, count, sample in pair.refs:
            print(f"        ссылка ({kind}): {table}.{column} × {count} «{sample}»")
        if pair.lots:
            print(f"        партий к переписи на «{article_of(pair.r)}»: {len(pair.lots)}")
        if pair.lots_skipped:
            print(f"        партии НЕ переписаны: {pair.lots_skipped}")
        print(f"        пометка G «дубль → …»: {'да' if pair.mark else ('уже стоит' if pair.already_marked else 'нет')}")
    print("\n== без пары (в названии номер детали, строки R на этом кабеле нет) ==")
    for g, part in unpaired:
        print(f"   «{article_of(g)}» × {cable_of(g)} → номер детали «{part}»")


def write_everything(url, factory, warehouse, records, pairs, now, log):
    """apply: ячейки R, партии (edit_lot_identity), пометки G; возвращает число отказов."""
    problems = 0
    for pair in pairs:
        if not pair.merged or not pair.copies:
            continue
        try:
            write_cells(factory, pair, None, now)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            problems += 1
            pair.merged = pair.mark = False
            pair.lots = []
            print(f"   ОТКАЗ записи ячеек R «{article_of(pair.r)}» (G «{article_of(pair.g)}»): {error}")
    with factory() as uow:  # журнал отката: что реально поменялось в БД.КОАКС (R и строки её артикула)
        after = {r.id: dict(r.fields) for r in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)}
    by_id = {r.id: r for r in records}
    owner_pair = {}
    for pair in pairs:
        owner_pair.setdefault(fold(article_of(pair.r)), pair)
    for rid, fields_ in after.items():
        was_fields = by_id[rid].fields
        for name in sorted(set(fields_) | set(was_fields)):
            if str(was_fields.get(name, "")) != str(fields_.get(name, "")) and not is_coax_cable_column(name) \
                    and name not in schema_keys("coax"):
                pair = owner_pair.get(fold(article_of(by_id[rid])))
                log.append(["ячейка", str(rid), article_of(pair.g) if pair else "", article_of(by_id[rid]),
                            cable_of(by_id[rid]), name, str(was_fields.get(name, "")), str(fields_.get(name, ""))])
    for pair in pairs:  # партии — только операцией приложения
        for lid in pair.lots:
            with warehouse() as uow:
                lot = uow.warehouse.get(lid)
            try:
                updated = edit_lot_identity(EditLotIdentityCommand(
                    lot_id=lid, expected_version=lot.version, article=article_of(pair.r), name=lot.name,
                    series=lot.series, actor=ACTOR, now=now), warehouse())
            except Exception as error:  # noqa: BLE001 — в отчёт, остальные партии идут дальше
                problems += 1
                print(f"   ОТКАЗ партии {lot.grn}/{lot.package}: {error}")
                continue
            log.append(["партия", str(lid), article_of(pair.g), article_of(pair.r),
                        f"{lot.warehouse} {lot.grn} {lot.package}", "Артикул", lot.article, updated.article])
    _, _, fresh_pairs, _, _ = make_plan(url, factory)  # пометка — только когда партий на G не осталось
    wanted = {p.g.id for p in fresh_pairs if p.mark and not p.lots and not p.copies}
    for pair in pairs:
        if pair.g.id not in wanted:
            continue
        old = str(pair.g.fields.get(NOTE) or "").strip()
        new = f"{NOTE_PREFIX}{article_of(pair.r)} (19.10.2026)" + (f"; {old}" if old else "")
        with factory() as uow:
            try:
                record, _ = _edit_in(pair.g.id, (FieldEdit(NOTE, old, new),), now, uow)
                _audit("references.tech_record_edited", ACTOR, now, {
                    "record_id": str(record.id), "tab": record.tab.value, "fields": [NOTE], "reason": REASON}, uow)
                uow.commit()
            except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                problems += 1
                print(f"   ОТКАЗ пометки G «{article_of(pair.g)}»: {error}")
                continue
        log.append(["ячейка", str(pair.g.id), article_of(pair.g), article_of(pair.r), cable_of(pair.g), NOTE, old, new])
    return problems


def print_stock(lots, after_lots, pairs, dry):
    """Сверка остатков: приход − списано | резерв | партий по семье «G + R» до и R (+ оставшееся на G) после."""
    print("\n== остатки: сумма (приход − списано) | резерв | партий ==")
    problems = 0
    families: dict[str, set[str]] = {}
    shown: dict[str, str] = {}
    for pair in pairs:
        shown[fold(article_of(pair.r))] = article_of(pair.r)
        families.setdefault(fold(article_of(pair.r)), {fold(article_of(pair.r))}).add(fold(article_of(pair.g)))
    for r_article, names in sorted(families.items()):
        count_b, total_b, reserve_b = totals(lots, names)
        if count_b == 0:
            continue
        count_r, total_r, reserve_r = totals(after_lots, {r_article})
        count_g, total_g, reserve_g = totals(after_lots, names - {r_article})
        ok = (total_b, reserve_b, count_b) == (total_r + total_g, reserve_r + reserve_g, count_r + count_g)
        print(f"   {shown[r_article]}: G+R до {total_b} | {reserve_b} | {count_b};  R {'после (план)' if dry else 'после'} "
              f"{total_r} | {reserve_r} | {count_r}; на G осталось {total_g} | {reserve_g} | {count_g}  "
              f"{'ИТОГО РАВНО' if ok else 'РАЗНИЦА'}")
        problems += 0 if ok else 1
    everything = snapshot(after_lots) == snapshot(lots) and len(after_lots) == len(lots)
    print(f"   все партии: {len(lots)} → {len(after_lots)}, количества, места и движения без изменений: "
          f"{'ДА' if everything else 'НЕТ'}")
    return problems + (0 if everything else 1)


def main(mode: str, arg: str = "") -> int:
    url = os.environ["ERP_DATABASE_URL"]
    engine = build_engine(url)
    factory = build_references_uow_factory(engine)
    warehouse = build_warehouse_uow_factory(engine)
    if mode == "rollback":
        return rollback(arg, factory, warehouse)
    dry = mode == "dry"
    records, state, pairs, unpaired, lots = make_plan(url, factory)
    print(f"строк БД.КОАКС: {len(records)}; пар G → R: {len(pairs)}; партий: {len(lots)}")
    print_pairs(pairs, unpaired)
    log = [["вид", "id", "артикул G", "артикул R", "кабель", "поле", "было", "стало"]]
    now = moscow_now()
    problems = 0
    if dry:  # ячейки проверяются записью в общей сессии без commit: отказ, который будет при записи, виден сейчас
        with factory() as probe:
            for pair in pairs:
                if pair.merged and pair.copies:
                    try:
                        write_cells(factory, pair, probe, now)
                    except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                        problems += 1
                        print(f"   ОТКАЗ при проверке: R «{article_of(pair.r)}» (G «{article_of(pair.g)}»): {error}")
        after_lots = planned_lots(lots, pairs)
    else:
        problems += write_everything(url, factory, warehouse, records, pairs, now, log)
        after_lots = read_lots(url)
    problems += print_stock(lots, after_lots, pairs, dry)
    if not dry:
        print("\n== CSV-журнал отката (сохранить в docs/data/«КОАКС — слияние дублей 19.10.csv») ==")
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(log)
        print(buffer.getvalue())
    summary = {
        "пар": len(pairs), "разногласий": sum(1 for p in pairs if p.conflicts),
        "ячеек к записи": sum(len(p.copies) for p in pairs), "партий": sum(len(p.lots) for p in pairs),
        "пометок": sum(1 for p in pairs if p.mark), "живых ссылок": sum(1 for p in pairs for i in p.refs if i[0] == "живая"),
        "проблем": problems}
    print("ИТОГО", json.dumps(summary, ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
    return 1 if problems else 0


def rollback(path: str, factory, warehouse) -> int:
    now = moscow_now()
    problems = 0
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in reversed(rows):
        if row["вид"] == "партия":
            lid = UUID(row["id"])
            with warehouse() as uow:
                lot = uow.warehouse.get(lid)
            if lot is None or lot.article != row["стало"]:
                print(f"   отказ: партия {row['id']} — артикул сейчас «{lot.article if lot else '—'}», ждали «{row['стало']}»")
                problems += 1
                continue
            edit_lot_identity(EditLotIdentityCommand(lot_id=lid, expected_version=lot.version, article=row["было"],
                                                     name=lot.name, series=lot.series, actor=ACTOR, now=now), warehouse())
            print(f"   откат партии {row['id']}: «{row['стало']}» → «{row['было']}»")
        else:
            with factory() as uow:
                try:
                    record, _ = _edit_in(UUID(row["id"]), (FieldEdit(row["поле"], row["стало"], row["было"]),), now, uow)
                    _audit("references.tech_record_edited", ACTOR, now, {
                        "record_id": str(record.id), "tab": record.tab.value, "fields": [row["поле"]],
                        "reason": "откат слияния дублей БД.КОАКС"}, uow)
                    uow.commit()
                    print(f"   откат ячейки {row['артикул R']} × {row['кабель']}: {row['поле']} «{row['стало']}» → «{row['было']}»")
                except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                    problems += 1
                    print(f"   отказ: {row['артикул R']} {row['поле']}: {error}")
    print("ИТОГО откат, проблем:", problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run_mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
    if run_mode not in ("dry", "apply", "rollback"):
        raise SystemExit("режим: dry | apply | rollback <csv>")
    raise SystemExit(main(run_mode, sys.argv[2] if len(sys.argv) > 2 else ""))
