"""Запись в прод 19.10.2026 — «Заглубление контакта, мм» у прямых розеток N и TNC в БД.КОАКС (владелец 19.10: «на все
разъёмы не угловые»; расстояние от переднего торца корпуса до контакта, диапазон мин…макс в мм, без дюймов).

Источник — габаритно-присоединительные размеры radiolab.ru (стандарты N: IEC 169-16 / MIL-C-39012; TNC: IEC 169-17),
розетка (мама). Цепочка по линиям чертежа: торец корпуса → REF (размер до REF) → контакт (размер от контакта до REF);
заглубление = (торец → REF) − (контакт → REF), мин = мин − макс, макс = макс − мин:
  N розетка:   F − E = (9,04 − 5,26)…(9,19 − 4,75) = 3,78…4,44 мм (F — торец → REF, E — передний край контакта → REF);
  TNC розетка: G − H = (8,31 − 5,23)…(8,51 − 4,72) = 3,08…3,79 мм (G — торец → REF, H — передний край контакта → REF).
Проверка масштабом чертежа: на N глубина по рисунку ≈ 4,1 мм, на TNC ≈ 3,75 мм — внутри диапазонов. Стандарты
(MIL-STD-348, IEC 61169) платные / недоступны (everyspec 403), второй источник — только масштаб самого чертежа.

Берутся ТОЛЬКО прямые по типу строки: «N-female», «N-female flange», «N-female bulkhead», «TNC-female», «TNC-female bulkhead»
(угловые R/A не пишутся). Заполненная ячейка возвращается отказом (old = "").
Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Журнал ячеек — docs/data/«Заглубление контакта N и TNC розетка 19.10.csv»."""
import os
import re
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import ColumnCommand, add_column, custom_columns
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor
from uuid import UUID

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Заглубление контакта N и TNC 19.10.2026", department="System")
DEPTH, SOURCE = "Заглубление контакта, мм", "Заглубление контакта, источник"
URL_N, URL_TNC = ("https://www.radiolab.ru/ru/attach_file/nradiolab.pdf", "https://www.radiolab.ru/ru/attach_file/tncradiolab.pdf")
N_VALUE = ("3,78…4,44",
           f"radiolab.ru, N series connectors, габаритно-присоединительные размеры, розетка (мама): F − E = (9,04 − 5,26)…(9,19 − 4,75) = "
           f"3,78…4,44 мм — торец корпуса → REF (F) минус контакт → REF (E); стандарт N (IEC 169-16), общий для серии, не лист артикула; "
           f"{URL_N}; 19.10.2026")
TNC_VALUE = ("3,08…3,79",
             f"radiolab.ru, TNC series connectors, габаритно-присоединительные размеры, розетка (мама): G − H = (8,31 − 5,23)…(8,51 − 4,72) = "
             f"3,08…3,79 мм — торец корпуса → REF (G) минус контакт → REF (H); стандарт TNC (IEC 169-17), общий для серии, не лист артикула; "
             f"{URL_TNC}; 19.10.2026")
STRAIGHT = re.compile(r"^(N|TNC)-female(?: bulkhead| flange)?$", re.IGNORECASE)

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def half(article: str) -> str:
    """Какую половину называет артикул: male / female / '' (не называет)."""
    name = article.casefold()
    return "female" if re.search(r"(?<![a-z])female(?![a-z])", name) else "male" if re.search(r"(?<![a-z])male(?![a-z])", name) else ""


with factory() as uow:
    records = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
    columns = custom_columns(TechOperationTab.COAX, uow)

rows, skipped, conflicts = [], 0, []
for record in records:
    fields = record.fields
    kind = str(fields.get("Тип/Серия") or fields.get("coaxType") or "").strip()
    article = str(fields.get("Артикул") or fields.get("coaxArticle") or "").strip()
    wire = str(fields.get("Провод") or fields.get("coaxWire") or "").strip()
    match = STRAIGHT.match(kind)
    angled = "угл" in str(fields.get("Исполнение") or "").casefold() or "r/a" in kind.casefold()
    if not match or angled:
        skipped += 1
        continue
    side = "female"
    if half(article) and half(article) != side:
        conflicts.append(f"{article} × {wire}: артикул называет {half(article)}, тип «{kind}»")
        continue
    value, source = N_VALUE if match.group(1).upper() == "N" else TNC_VALUE
    rows.append({"fields": {"coaxArticle": article, "coaxWire": wire, DEPTH: value, SOURCE: source},
                 "old": {DEPTH: "", SOURCE: ""}, "kind": kind})

print(f"БД.КОАКС: строк {len(records)}, N/TNC розеток к записи {len(rows)}, пропущено (иные серии / угловые) {skipped}")
for line in conflicts:
    print("   не пишу (расхождение):", line)
for row in rows:
    f = row["fields"]
    print(f"   {f['coaxArticle']} × {f['coaxWire']} | {row['kind']} | {f[DEPTH]}")

missing = [name for name in (DEPTH, SOURCE) if name not in columns]
if missing and not dry:
    for name in missing:
        try:
            written = add_column(ColumnCommand(TechOperationTab.COAX, name, ACTOR, now), factory())
        except ReferenceValidationError as error:
            raise SystemExit(f"столбец «{name}»: {error}")
        print(f"== столбец «{name}» добавлен, строк {written}")
elif missing:
    print("== столбцов ещё нет — apply добавит:", ", ".join(missing), "(проверка import_rows — после их добавления)")
if missing and dry:
    raise SystemExit(0)


def call(dry_run: bool):
    return import_rows("coax", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in rows], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
if data["created"]:
    raise SystemExit(f"СТОП — строки не найдены, ничего не записано: {data['created']}")
if not dry:
    data = call(False)
print(f"== import_rows(coax): строк {len(rows)} → создано {len(data['created'])}, обновлено {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"]:
    print("   отказ:", item)
