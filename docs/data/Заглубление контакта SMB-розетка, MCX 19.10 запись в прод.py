"""Запись в прод 19.10.2026 (третья) — «Заглубление контакта, мм» у прямых SMB-female, MCX-male, MCX-female в БД.КОАКС.

Правила владельца 19.10: розетка — от торца корпуса до контакта; вилка — от основания штыря (торец изолятора) до REF,
торец гайки/оболочки ≈ REF. Только мм; буквы прослежены по выносным линиям, не по симметрии. Пишется только полный диапазон
min…max, подтверждённый документом (SMB — MIL-STD-348B + radiolab; MCX — radiolab + Amphenol Connex).

SMB-female (розетка = интерфейс со штырём, MIL-STD-348B рис. 311-1): C (REF → торец корпуса) = 3,33…3,58, D (REF → кончик
  штыря) = 2,39…2,97; штырь утоплен: C − D = 0,36…1,19 (radiolab SMB, розетка: D 3,33…3,58 и H ≤ 2,97 — совпадает).
MCX-male (вилка со штырём): основание штыря/торец изолятора → REF = 2,80…3,20 (radiolab MCX, вилка, J; Amphenol Connex
  MCX plug, G 2,81…3,20; торец оболочки ≈ REF, H/D ≤ 0,30 вперёд).
MCX-female (розетка): F (торец → REF) = 4,00…4,12; E (передний срез контакта → REF) = 2,60…2,80 (radiolab H; Amphenol Connex
  jack E 2,61…2,79): F − E = 1,20…1,52.

Ключ защиты: в ячейке должно быть пусто (old=""), иначе строка не трогается и попадает в список расхождений.
Берутся ТОЛЬКО точные типы «SMB-female», «MCX-male», «MCX-female» (прямые; R/A, flange, bulkhead — нет).
Порядок: dry → backup-now → apply → dry. Журнал — docs/data/«Заглубление контакта SMB-розетка, MCX 19.10.csv»."""
import os
import re
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import custom_columns
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Заглубление контакта SMB MCX 19.10.2026", department="System")
DEPTH, SOURCE = "Заглубление контакта, мм", "Заглубление контакта, источник"
R = "https://www.radiolab.ru/ru/attach_file/"
PEIG = "https://www.peigenesis.com/images/content/pei_tabs/amphenol/rf-series/connex/mcx.pdf"
TARGETS = {
    "SMB-female": ("0,36…1,19", f"MIL-STD-348B рис. 311-1 (SMB, штырь): C 3,33…3,58 (REF → торец корпуса) − D 2,39…2,97 (REF → кончик штыря) = 0,36…1,19 мм; radiolab.ru SMB, розетка: D 3,33…3,58, H ≤ 2,97 — совпадает; {R}smbradiolab.pdf; 19.10.2026"),
    "MCX-male": ("2,80…3,20", f"radiolab.ru MCX, вилка: J = 2,80…3,20 мм — от основания штыря (торец изолятора) до REF, торец оболочки ≈ REF; Amphenol Connex MCX, plug: G 2,81…3,20; {R}mcxradiolab.pdf; {PEIG}; 19.10.2026"),
    "MCX-female": ("1,20…1,52", f"radiolab.ru MCX, розетка: G (торец → REF) 4,00…4,12 − H (передний срез контакта → REF) 2,60…2,80 = 1,20…1,52 мм; Amphenol Connex MCX, jack: F 4,00…4,12, E 2,61…2,79 — совпадает; {R}mcxradiolab.pdf; {PEIG}; 19.10.2026"),
}

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()

with factory() as uow:
    records = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
    columns = custom_columns(TechOperationTab.COAX, uow)
missing = [name for name in (DEPTH, SOURCE) if name not in columns]
if missing:
    raise SystemExit(f"СТОП — нет столбцов: {missing}")


def half(article: str) -> str:
    name = article.casefold()
    return "female" if re.search(r"(?<![a-z])female(?![a-z])", name) else "male" if re.search(r"(?<![a-z])male(?![a-z])", name) else ""


rows, skipped, conflicts = [], 0, []
for record in records:
    fields = record.fields
    kind = str(fields.get("Тип/Серия") or fields.get("coaxType") or "").strip()
    article = str(fields.get("Артикул") or fields.get("coaxArticle") or "").strip()
    wire = str(fields.get("Провод") or fields.get("coaxWire") or "").strip()
    if kind not in TARGETS:
        skipped += 1
        continue
    if "угл" in str(fields.get("Исполнение") or "").casefold():
        skipped += 1
        continue
    want = kind.rsplit("-", 1)[-1]
    if half(article) and half(article) != want:
        conflicts.append(f"{article} × {wire}: артикул называет {half(article)}, тип «{kind}»")
        continue
    cur = str(fields.get(DEPTH) or "").strip()
    cur_src = str(fields.get(SOURCE) or "")
    expected = ""
    if cur != expected:
        conflicts.append(f"{article} × {wire} ({kind}): в ячейке «{cur}», ожидалось «{expected}» — не трогаю")
        continue
    rows.append({"fields": {"coaxArticle": article, "coaxWire": wire, DEPTH: TARGETS[kind][0], SOURCE: TARGETS[kind][1]},
                 "old": {DEPTH: cur, SOURCE: cur_src}, "kind": kind, "was": cur})

print(f"БД.КОАКС: строк {len(records)}, к записи {len(rows)}, пропущено {skipped}")
for line in conflicts:
    print("   не пишу:", line)
for row in rows:
    f = row["fields"]
    print(f"   {f['coaxArticle']} × {f['coaxWire']} | {row['kind']} | было «{row['was']}» → {f[DEPTH]}")


def call(dry_run: bool):
    return import_rows("coax", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in rows], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
if data["created"]:
    raise SystemExit(f"СТОП — строки не найдены, ничего не записано: {data['created']}")
print(f"== пробный import_rows: обновится {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"]:
    print("   отказ:", item)
if not dry:
    data = call(False)
    print(f"== записано: создано {len(data['created'])}, обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
    for item in data["refused"]:
        print("   отказ:", item)
