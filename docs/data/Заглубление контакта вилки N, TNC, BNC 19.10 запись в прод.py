"""Запись в прод 19.10.2026 (вторая) — «Заглубление контакта, мм» у прямых вилок N, TNC, BNC в БД.КОАКС.

Правило владельца 19.10 для ВИЛОК: «заглубление контакта» = размер от уступа штыря до REF; торец накидной гайки практически
совпадает с REF. Чертёж radiolab.ru, вилка N: D = 5,33…5,84 мм — от линии уступа штыря до REF, кончик штыря на REF.
Для TNC и BNC тот же по смыслу и по числу размер: radiolab.ru TNC, вилка: F = 5,33…5,84; MIL-STD-348A рис. 313-1 (TNC) E и
рис. 301-1 (BNC) E = 0,210…0,230 дюйма = 5,33…5,84 мм — от REF до торца изолятора / основания штыря. Торец гайки ≈ REF,
поэтому из размера ничего не вычитается. Прежняя запись TNC «2,06…3,00» (край гайки вперёд + H) заменяется: ключ замены —
текущее значение «2,06…3,00» (не ""): если в ячейке что-то иное, строка не трогается и попадает в список расхождений.

Берутся ТОЛЬКО «N-male», «TNC-male», «BNC-male» (прямые; угловые R/A, flange, RP- не пишутся).
Порядок (live-data protocol, prod-reference-write): dry → backup-now → apply → dry (всё «refused/без изменений» = записано).
Журнал ячеек — docs/data/«Заглубление контакта вилки N, TNC, BNC 19.10.csv» (откат — старые значения из него)."""
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
ACTOR = Actor(id=UUID(int=0), name="Заглубление контакта вилки N TNC BNC 19.10.2026", department="System")
DEPTH, SOURCE = "Заглубление контакта, мм", "Заглубление контакта, источник"
VALUE = "5,33…5,84"
OLD_TNC = "2,06…3,00"
U = "https://www.radiolab.ru/ru/attach_file/"
SOURCES = {
    "N-male": f"radiolab.ru, N, вилка: D = 5,33…5,84 мм — от уступа штыря до REF (торец гайки ≈ REF, кончик штыря на REF); стандарт N (MIL-C-39012, IEC 169-16), общий для серии; {U}nradiolab.pdf; 19.10.2026",
    "TNC-male": f"radiolab.ru, TNC, вилка: F = 5,33…5,84 мм — от REF до основания штыря (торец изолятора), он же E 0,210…0,230 дюйма на MIL-STD-348A рис. 313-1; торец гайки ≈ REF; тот же размер, что D у вилки N; {U}tncradiolab.pdf; 19.10.2026",
    "BNC-male": f"radiolab.ru, BNC, вилка: E = 5,33…5,84 мм — от REF до основания штыря (торец изолятора), он же E 0,210…0,230 дюйма на MIL-STD-348A рис. 301-1; торец гайки ≈ REF; тот же размер, что D у вилки N; {U}bncradiolab.pdf; 19.10.2026",
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
    if kind not in SOURCES:
        skipped += 1
        continue
    if "угл" in str(fields.get("Исполнение") or "").casefold():
        skipped += 1
        continue
    if half(article) and half(article) != "male":
        conflicts.append(f"{article} × {wire}: артикул называет {half(article)}, тип «{kind}»")
        continue
    cur = str(fields.get(DEPTH) or "").strip()
    cur_src = str(fields.get(SOURCE) or "")
    expected = OLD_TNC if kind == "TNC-male" else ""
    if cur != expected:
        conflicts.append(f"{article} × {wire} ({kind}): в ячейке «{cur}», ожидалось «{expected}» — не трогаю")
        continue
    rows.append({"fields": {"coaxArticle": article, "coaxWire": wire, DEPTH: VALUE, SOURCE: SOURCES[kind]},
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
