"""Запись в прод 20.10.2026 — БД.КОАКС: слияние «B-111F» × RG-58 в «BNC-C58P» × RG-58 (один товар под двумя именами).

Основание: kroks.ru 810 продаёт товар под названием «Разъем B-111F (BNC-C58P) BNC(male) на кабель RG-58, обжимной»
(https://kroks.ru/shop/connectors/razemy/razem-bncmale-b-111f-pod-kabel-rg-58/) — B-111F — имя продавца, BNC-C58P —
артикул изготовителя RUICHI. Владелец 20.10: одна строка под артикулом изготовителя, предложение — самое дешёвое,
значения сливаются, документ изготовителя главнее (паспорт RUICHI 481183 с Промэлектроники).
Серия, пол, исполнение, кабель у строк одни (BNC-male, прямой, RG-58) — слияния через серии нет (ufl-not-mhf).

Что делает (одна транзакция на шаг, всё с защитой старого значения):
  1. Ссылки на «B-111F» по имени во всех таблицах (кроме журналов/историй и самих партий): есть живая ссылка
     (комплект, техкарта, ЭТК, шаблон, заявка) — ничего не делается, список выводится.
  2. Ячейки строки BNC-C58P (тем же путём, что «Изменить» в «Базе данных»: `_edit_in` + `_same_article`):
     Тип пина «пайка» → «обжим» и Частота «6» → «4» — по паспорту RUICHI 481183 (обжим центрального контакта, до 4 ГГц);
     Покрытие — со страницы gsm-repiteri.ru B-111F (у BNC-C58P пусто); Источник данных — пометка о слиянии.
     L1–L3 остаются у BNC-C58P (5 / 7,5 / 12): ни у одной строки нет чертежа разделки, длины B-111F (4 / 6,4 / 15,1)
     записаны в журнал и в «Источник данных». Предложение остаётся «Промэлектроники» (29,72 ₽ < gsm 50 ₽ < kroks 275 ₽).
  3. Партии склада с артикулом «B-111F» → «BNC-C58P» только `warehouse.application.edit_lot_identity` (версия партии,
     журнал warehouse.identity_edited); количества и движения не трогаются — снимок до/после сверяется.
  4. Строка «B-111F» × RG-58 удаляется `references.application.delete_record` (журнал references.record_deleted с полной
     старой записью — по нему строка восстанавливается).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл   (в конце — CSV-журнал отката)
  ... python - dry   < этот файл   (→ «уже слито»)
"""
import csv
import io
import json
import os
import re
import sys
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, String, Text, create_engine, inspect, text

from app.database import build_engine, build_references_uow_factory, build_warehouse_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in, _same_article, delete_record
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.warehouse.application import EditLotIdentityCommand, edit_lot_identity
from app.shared.clock import moscow_now
from app.shared.identity import Actor

ACTOR = Actor(id=UUID(int=0), name="Слияние B-111F → BNC-C58P 20.10.2026", department="System")
REASON = "слияние B-111F (имя продавца) в BNC-C58P (артикул RUICHI), 20.10.2026"
G, R, CABLE = "B-111F", "BNC-C58P", "RG-58"
HISTORY_TABLES = frozenset({
    "audit_events", "outbox_messages", "tech_operation_snapshots", "warehouse_writeoffs", "reference_stock_links",
    "receiving_lines", "receiving_files", "procurement_supplier_orders", "procurement_supplier_order_documents",
    "procurement_files", "finished_goods_moves", "worker_heartbeats", "operations_job_marks", "notifications",
})
SKIP_PREFIX = ("identity_",)
NOTE = ("20.10.2026: слита строка «B-111F» × RG-58 — это тот же товар (kroks.ru 810: «Разъем B-111F (BNC-C58P) BNC(male) на "
        "кабель RG-58, обжимной»); у B-111F было: gsm-repiteri.ru 1482, 50 ₽ от 17.10.2026 "
        "(https://gsm-repiteri.ru/prodazha/razyomi/razyem-b-111f-bnc-male-obzhimnoj-na-kabel-rg-58), kroks.ru 810, 275 ₽; "
        "L1–L3 у B-111F 4 / 6,4 / 15,1 (без документа) — оставлены длины BNC-C58P; Тип пина «пайка» → «обжим» и Частота "
        "«6» → «4» — паспорт RUICHI 481183 (https://cdn.promelec.ru/upload/items/2022/06/01/481183_.pdf): «Схема "
        "кримпирования… Место обжима центрального контакта», «Диапазон частот до 4 ГГц»; Покрытие — со страницы "
        "gsm-repiteri.ru B-111F")
CELLS = [  # поле, ждём (старое), новое; Источник данных дописывается отдельно
    ("Тип пина", "пайка", "обжим"),
    ("Частота до, ГГц", "6", "4"),
    ("Покрытие", "", "корпус: никель; центральный контакт: золото"),
]


def fold(v) -> str:
    return " ".join(str(v or "").casefold().split())


def find(records, article):
    hits = [r for r in records if fold(r.fields.get("Артикул")) == fold(article)
            and cable_key(r.fields.get("Провод")) == cable_key(CABLE)]
    return hits[0] if len(hits) == 1 else None


def scan_references(url: str, article: str):
    pattern = re.compile(r"(?<![\w-])" + re.escape(fold(article)) + r"(?![\w-])")
    found = {}
    engine = create_engine(url)
    inspector = inspect(engine)
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            if table == "warehouse_lots" or table.startswith(SKIP_PREFIX):
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
                    if value and fold(article) in fold(value) and pattern.search(fold(value)):
                        key = (kind, table, columns[index])
                        count, sample = found.get(key, (0, ""))
                        found[key] = (count + 1, sample or str(value)[:100])
    return [(k[0], k[1], k[2], c, s) for k, (c, s) in found.items()]


def read_lots(url: str):
    with create_engine(url).connect() as connection:
        rows = connection.execute(text(
            "SELECT id, version, warehouse, grn, package, article, series, name, inbound, written_off, reserved, "
            "storage_location FROM warehouse_lots")).mappings().all()
    return [dict(row) for row in rows]


def snapshot(lots):
    return {str(l["id"]): tuple(str(l[k]) for k in ("warehouse", "grn", "package", "series", "name", "inbound", "written_off",
                                                    "reserved", "storage_location")) for l in lots}


def main(mode: str) -> int:
    url = os.environ["ERP_DATABASE_URL"]
    engine = build_engine(url)
    refs_factory = build_references_uow_factory(engine)
    wh_factory = build_warehouse_uow_factory(engine)
    dry = mode == "dry"
    now = moscow_now()
    with refs_factory() as uow:
        records = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
    g, r = find(records, G), find(records, R)
    if r is None:
        print(f"ОТКАЗ: нет строки {R} × {CABLE}"); return 1
    if g is None:
        print(f"«{G}» × {CABLE} в базе нет — уже слито")
        print("ИТОГО", json.dumps({"слито": True}, ensure_ascii=False)); return 0
    others = [x for x in records if fold(x.fields.get("Артикул")) == fold(G) and x.id != g.id]
    if others:
        print(f"ОТКАЗ: у артикула {G} есть другие строки (кабели {[x.fields.get('Провод') for x in others]}) — партии не отнести")
        return 1
    if fold(g.fields.get("Тип/Серия")) != fold(r.fields.get("Тип/Серия")) or fold(g.fields.get("Исполнение")) != fold(r.fields.get("Исполнение")):
        print("ОТКАЗ: серия/исполнение строк разные"); return 1
    refs = scan_references(url, G)
    live = [x for x in refs if x[0] == "живая"]
    for x in refs:
        print(f"   ссылка ({x[0]}): {x[1]}.{x[2]} × {x[3]}: «{x[4]}»")
    if live:
        print(f"ОТКАЗ: на «{G}» есть живые ссылки — строка не сливается"); return 1
    lots_before = read_lots(url)
    mine = [l for l in lots_before if fold(l["article"]) == fold(G)]
    print(f"партий «{G}»: {len(mine)}: " + "; ".join(f"{l['grn']}/{l['package']} {l['warehouse']} приход {l['inbound']} "
                                                       f"списано {l['written_off']} резерв {l['reserved']}" for l in mine))
    problems = 0
    todo = []
    for field, old, new in CELLS:
        have = str(r.fields.get(field, "") or "")
        if have == new:
            continue
        if have != old:
            problems += 1; print(f"   ОТКАЗ: {R}: {field} — сейчас «{have}», ждали «{old}»"); continue
        todo.append((field, old, new))
    src_old = str(r.fields.get("Источник данных", "") or "")
    if NOTE not in src_old:
        todo.append(("Источник данных", src_old, (src_old + "; " if src_old else "") + NOTE))
    print(f"ячейки {R}: " + ", ".join(f"{f}: «{o}» → «{n[:60]}»" for f, o, n in todo))
    if problems:
        print("ИТОГО отказ"); return 1
    if dry:
        print("ИТОГО", json.dumps({"ячеек": len(todo), "партий": len(mine), "удалить строку": f"{G} × {CABLE}"}, ensure_ascii=False),
              "(проверка, ничего не записано)")
        return 0
    log = [["вид", "id", "артикул", "кабель", "поле", "было", "стало"]]
    with refs_factory() as uow:
        record, updated = _edit_in(r.id, tuple(FieldEdit(*t) for t in todo), now, uow)
        _same_article(updated, tuple(FieldEdit(*t) for t in todo), now, uow)
        _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(r.id), "tab": "coax",
                                                              "fields": [t[0] for t in todo], "reason": REASON}, uow)
        uow.commit()
    for f_, o, n in todo:
        log.append(["ячейка", str(r.id), R, CABLE, f_, o, n])
    for lot in mine:
        with wh_factory() as uow:
            current = uow.warehouse.get(lot["id"] if isinstance(lot["id"], UUID) else UUID(str(lot["id"])))
        edit_lot_identity(EditLotIdentityCommand(lot_id=current.id, expected_version=current.version, article=R,
                                                 name=current.name, series=current.series, actor=ACTOR, now=now), wh_factory())
        log.append(["партия", str(current.id), G, "", "article", G, R])
    lots_after = read_lots(url)
    sb, sa = snapshot(lots_before), snapshot(lots_after)
    same = sb == sa and len(lots_before) == len(lots_after)
    print("сверка партий (всё, кроме артикула):", "равно" if same else "РАЗЛИЧИЕ")
    total = lambda ls, a: sum(Decimal(str(l["inbound"])) - Decimal(str(l["written_off"])) for l in ls if fold(l["article"]) == fold(a))
    print(f"остаток {G}+{R} до: {total(lots_before, G) + total(lots_before, R)}, {R} после: {total(lots_after, R)}, {G} после: {total(lots_after, G)}")
    deleted = delete_record(g.id, ACTOR, now, refs_factory(), reason=REASON)
    log.append(["удалена", str(deleted.id), G, CABLE, "", json.dumps(dict(deleted.fields), ensure_ascii=False), ""])
    print("\n== CSV-журнал отката ==")
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(log)
    print(buffer.getvalue())
    print("ИТОГО", json.dumps({"ячеек": len(todo), "партий": len(mine), "удалена": f"{G} × {CABLE}", "сверка": same},
                              ensure_ascii=False), "(записано)")
    return 0 if same else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run_mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
    if run_mode not in ("dry", "apply"):
        raise SystemExit("режим: dry | apply")
    raise SystemExit(main(run_mode))
