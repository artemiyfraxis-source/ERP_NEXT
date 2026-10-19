"""Запись в прод 19.10.2026 — «Заглубление контакта, мм» у прямых разъёмов SMA в БД.КОАКС (владелец 19.10: «длина
заглубления тоже надо, на все разъёмы не угловые»; «допустимое расстояние от корпуса до контакта»).

Что это: расстояние от переднего торца корпуса до контакта, диапазон мин…макс в мм (никаких дюймов). Источник —
габаритно-присоединительные размеры SMA на radiolab.ru (стандарт SMA: MIL-C-39012, IEC 169-15), общие для серии:
  вилка   — размер E: 0,38…1,14 мм;
  розетка — цепочка по линиям: торец → REF (H) → изолятор (I) → контакт (J), H+I+J = 1,88…2,41 мм
            (H 1,88…1,98; I 0,00…0,25; J 0,00…0,18; владелец 19.10: «надо три числа сложить»).
Источник пишется в соседний столбец «Заглубление контакта, источник» с ссылкой и формулой.

Берутся ТОЛЬКО прямые SMA по типу строки: «SMA-male», «SMA-female», «SMA-female bulkhead», «SMA-female flange» (угловые —
R/A / «угловой» — и RP-SMA пропускаются). Строка, у которой артикул называет одну половину, а тип — другую («SMA-Male
flanged» с типом female flange), не пишется — сообщается отдельно. Остальные серии (N, BNC, TNC, MCX…) НЕ пишутся: по
чертежам radiolab цепочка торец → контакт однозначно не читается (список — в отчёте).

Заполненная ячейка возвращается отказом (old = ""); столбцы добавляются как «+ Столбец» (`add_column`), если их ещё нет.
Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Журнал ячеек (ключ, поле, было, стало, источник) — docs/data/«Заглубление контакта SMA 19.10.csv»."""
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
ACTOR = Actor(id=UUID(int=0), name="Заглубление контакта SMA 19.10.2026", department="System")
DEPTH, SOURCE = "Заглубление контакта, мм", "Заглубление контакта, источник"
URL = "https://www.radiolab.ru/ru/attach_file/smaradiolab.pdf"
MALE = ("0,38…1,14",
        f"radiolab.ru, SMA series connectors, габаритно-присоединительные размеры, вилка (папа): E = 0,38…1,14 мм — от торца "
        f"корпуса до контакта; стандарт SMA (MIL-C-39012, IEC 169-15), общий для серии, не лист артикула; {URL}; 19.10.2026")
FEMALE = ("1,88…2,41",
          f"radiolab.ru, SMA series connectors, габаритно-присоединительные размеры, розетка (мама): H + I + J = "
          f"(1,88 + 0,00 + 0,00)…(1,98 + 0,25 + 0,18) = 1,88…2,41 мм — торец → REF (H) → изолятор (I) → контакт (J); "
          f"стандарт SMA (MIL-C-39012, IEC 169-15), общий для серии, не лист артикула; {URL}; 19.10.2026")
STRAIGHT = re.compile(r"^SMA-(male|female)(?: bulkhead| flange)?$", re.IGNORECASE)

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
    side = match.group(1).casefold()
    if half(article) and half(article) != side:
        conflicts.append(f"{article} × {wire}: артикул называет {half(article)}, тип «{kind}»")
        continue
    value, source = MALE if side == "male" else FEMALE
    rows.append({"fields": {"coaxArticle": article, "coaxWire": wire, DEPTH: value, SOURCE: source},
                 "old": {DEPTH: "", SOURCE: ""}, "kind": kind})

print(f"БД.КОАКС: строк {len(records)}, SMA прямых к записи {len(rows)}, пропущено (не SMA / угловые / иные серии) {skipped}")
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
