"""Запись в прод 17.10.2026 — БД.ТЕР: контакты каталога разъёмов, которых не было в базе (владелец 17.10: «всё что
в каталоге должно быть и в базе»). Данные — «БД.ТЕР — контакты каталога 17.10.csv» (документы производителя, источник
у каждой строки; что пусто и почему — .md рядом).

1. 14 новых записей — `import_rows("ter")` по артикулу (terArticle): ключи схемы + «Тип разъема», «Источник высоты обжима».
   Поля технолога (terApplicator, mm2From, mm2To) загрузка контактов не пишет — они дописываются следом `_edit_in`,
   как правка технолога в «Базе данных», old="".
   CC81L-2830-01-L — не новая запись: это россыпь контакта CC81R-2830-01-L (катушка), он уже в базе — артикул россыпи
   дописывается ему в terArticleBag (п. 2).
2. Правки существующих записей — `_edit_in` со сверкой старого значения:
   - CC81R-2830-01-L: terArticleBag «» → «CC81L-2830-01-L»; высота по изоляции «30 AWG: 1,40; 28 AWG: 1,35» →
     по таблице Samtec (Rev BZ) «30 AWG: 0,84–1,09; 28 AWG: 0,89–1,14».
   - JST EL: по каталогу JST (eEL.pdf) штырь SLM идёт в корпус ELR, гнездо SLF — в ELP; в базе было наоборот.

  ... python - dry < этот файл ; erp.sh backup-now ; ... python - apply < этот файл ; ... python - dry
"""
import csv
import io
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "контакты каталога в БД.ТЕР по документам производителя (17.10.2026)"
CSV_TEXT = r'''@@CSV@@'''
EDITS = {  # id: (артикул, [(поле, было, стало)])
    "84bc22de-5794-4660-bf84-8142fed475b6": ("CC81R-2830-01-L", [("terArticleBag", "", "CC81L-2830-01-L"),
        ("terInsCrimpHeight", "30 AWG: 1,40; 28 AWG: 1,35", "30 AWG: 0,84–1,09; 28 AWG: 0,89–1,14")]),
    "ed0a89bd-e462-43fd-92f9-e68518123b4b": ("SLM-01T-P1.3E", [("terSeries", "ELP", "ELR")]),
    "bde63ef0-4ed6-48ac-846b-058b6494f447": ("SLM-41T-P1.3E", [("terSeries", "ELP, EL", "ELR, EL")]),
    "8f8af5de-9460-4e1c-9794-856653684b1e": ("SLF-41T-P1.3E", [("terSeries", "ELR", "ELP")]),
    "9263c61e-a41e-4251-8081-4f5e24e1c502": ("SLF-01T-P1.3E", [("terSeries", "ELR", "ELP")]),
}

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()

TECH_KEYS = ("terApplicator", "mm2From", "mm2To")
rows = []
tech: dict[str, dict[str, str]] = {}
for item in csv.DictReader(io.StringIO(CSV_TEXT.lstrip("﻿"))):
    if not item["terArticle"].strip():
        continue  # CC81L — россыпь CC81R, п. 2
    fields = {key: value.strip() for key, value in item.items() if value and value.strip()}
    fields["Тип разъема"] = "Обжимной контакт"
    tech[fields["terArticle"]] = {key: fields.pop(key) for key in TECH_KEYS if key in fields}
    rows.append(ImportRowIn(fields=fields, old={}))


def call(dry_run):
    return import_rows("ter", factory(), rows, ACTOR, now, dry_run=dry_run, source="erp").model_dump()


data = call(mode != "apply")
print(f"== новые: {len(rows)} → создано {len(data['created'])}, обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"] + data["updated"]:
    print("   ", item)

print("== поля технолога у новых записей")
with factory() as uow:
    by_article = {str(r.fields.get("terArticle", "")).strip(): r.id for r in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)}
for article, values in tech.items():
    if not values:
        continue
    record_id = by_article.get(article)
    if record_id is None:
        print(f"   записи ещё нет (появится при записи): {article}")
        continue
    with factory() as uow:
        record = uow.tech_operations.get(record_id)
        edits = tuple(FieldEdit(key, "", value) for key, value in values.items() if str(record.fields.get(key, "")).strip() != value)
        if not edits:
            print(f"   уже так: {article}")
            continue
        try:
            before, _ = _edit_in(record_id, edits, now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            print(f"   отказ: {article} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
               "fields": [edit.field for edit in edits], "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {article} {values}")

print("== правки")
for record_id, (article, changes) in EDITS.items():
    with factory() as uow:
        record = uow.tech_operations.get(UUID(record_id))
        if all(str(record.fields.get(field, "")).strip() == new for field, _, new in changes):
            print(f"   уже так: {article}")
            continue
        try:
            before, _ = _edit_in(record.id, tuple(FieldEdit(field, old, new) for field, old, new in changes), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            print(f"   отказ: {article} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
               "fields": [field for field, _, _ in changes], "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {article} {[(f, o, n) for f, o, n in changes]}")
