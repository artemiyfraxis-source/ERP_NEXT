"""Запись в прод 19.10.2026 — «Заглубление контакта, мм» у прямых вилок TNC в БД.КОАКС (решение владельца 19.10: для вилок
«торец» = передний край накидной гайки; берётся гайка в крайнем переднем положении; диапазон мин…макс в мм, без дюймов).

Источник — MIL-STD-348A, рис. 313-1 «Interface, series TNC, pin contact» (бесплатный документ DoD; копия everyspec через
Web Archive): край гайки → REF = M, максимум 0,078 дюйма = 1,98 мм (позиция гайки, выдвинутой вперёд; гайка плавающая);
REF → кончик штыря = H 0,003…0,040 дюйма = 0,08…1,02 мм (то же у radiolab.ru TNC, вилка).
Заглубление = M + H = 1,98 + (0,08…1,02) = 2,06…3,00 мм (оценка для гайки в крайнем переднем положении; при отодвинутой гайке меньше).

Берутся ТОЛЬКО «TNC-male» (прямые; угловые R/A и RP-TNC не пишутся). Заполненная ячейка возвращается отказом (old = "").
Порядок (live-data protocol, prod-reference-write): dry → backup-now → apply → dry (всё «значение изменилось» = записано).
Журнал ячеек — docs/data/«Заглубление контакта TNC вилка 19.10.csv»."""
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
ACTOR = Actor(id=UUID(int=0), name="Заглубление контакта TNC вилка 19.10.2026", department="System")
DEPTH, SOURCE = "Заглубление контакта, мм", "Заглубление контакта, источник"
URL_M = "https://everyspec.com/MIL-STD/MIL-STD-0300-0499/MIL-STD-348A_420/"
URL_R = "https://www.radiolab.ru/ru/attach_file/tncradiolab.pdf"
TNC_VALUE = ("2,06…3,00",
             f"MIL-STD-348A рис. 313-1: край гайки в крайнем переднем положении (M 1,98) + REF → кончик штыря (H 0,08…1,02) = 2,06…3,00 мм; "
             f"гайка плавающая; H сверен с radiolab.ru TNC, вилка; стандарт TNC, общий для серии, не лист артикула; {URL_M}; {URL_R}; 19.10.2026")
STRAIGHT = re.compile(r"^TNC-male$", re.IGNORECASE)

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
    side = "male"
    if half(article) and half(article) != side:
        conflicts.append(f"{article} × {wire}: артикул называет {half(article)}, тип «{kind}»")
        continue
    value, source = TNC_VALUE
    rows.append({"fields": {"coaxArticle": article, "coaxWire": wire, DEPTH: value, SOURCE: source},
                 "old": {DEPTH: "", SOURCE: ""}, "kind": kind})

print(f"БД.КОАКС: строк {len(records)}, TNC вилок к записи {len(rows)}, пропущено (иные серии / угловые) {skipped}")
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
