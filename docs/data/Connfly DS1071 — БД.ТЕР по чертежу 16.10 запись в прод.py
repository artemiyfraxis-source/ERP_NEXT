"""Запись в прод 16.10.2026 — контакты Connfly DS1071 (шаг 2,54) в БД.ТЕР по чертежу производителя.

Чертёж Connfly C-DS1071-XXXXX-B rev. B (https://www.connfly.com/static/upload/file/pdf/DS1071.pdf), блок «FEMALE
TERMINAL», таблица «Wire Range»: Section Area 0.09~0.3 мм², AWG#28~22, O.D of Wire 1.2–1.6 мм; в NOTES «Wire range:
AWG#28~#22»; номер контакта T-DS1071-SCX00 (6 — олово, 8 — золото). В строках T-DS1071-SC600 и T-DS1071-SC800 стояло
AWG 28–24, 0,08–0,2 мм², Ø изоляции 1–1,5 и ссылка на чертёж DS1071-01 — это другая серия (шаг 2,0, контакт
T-DS1071-01-SCX00, на её чертеже диапазона провода нет). Строку T-DS1071-01-SC600 скрипт не трогает.

Правка — путём приложения (`_edit_in`, как «Изменить» в «Базе данных»): у каждой ячейки старое значение, ячейка,
которую поменяли после снимка, приходит отказом и не пишется; ключи карточки (awgTo, mm2From…) выводятся из столбцов.
Строка ищется по «Артикулу контакта (REEL)», не по ID.

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Журнал ячеек (артикул, поле, было, стало) — CELLS ниже; он же запись для отката."""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Сверка Connfly DS1071 16.10.2026", department="System")
REASON = "по чертежу Connfly C-DS1071-XXXXX-B, таблица Wire Range контакта T-DS1071-SCX00 (сверка 16.10.2026)"
DS1071_PDF = "https://www.connfly.com/static/upload/file/pdf/DS1071.pdf"
DS1071_01_PDF = "https://en.connfly.com/static/upload/file/DS1071-01.pdf"

# (артикул, столбец, было, стало)
CELLS = [
    (article, field, old, new)
    for article in ("T-DS1071-SC600", "T-DS1071-SC800")
    for field, old, new in (
        ("До AWG", "24", "22"),
        ("От мм2", "0,08", "0,09"),
        ("До мм2", "0,2", "0,3"),
        ("Диаметр изоляции от, мм", "1", "1,2"),
        ("Диаметр изоляции до, мм", "1,5", "1,6"),
        ("Ссылка на DATASHEET на сайте производителя", DS1071_01_PDF, DS1071_PDF),
    )
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
LOG = []


def log(key, field, old, new, status):
    LOG.append({"table": "БД.ТЕР", "key": key, "field": field, "old": old, "new": new, "status": status})
    print(f"   {status}: БД.ТЕР «{key}» {field}: «{old}» → «{new}»")


with factory() as uow:
    rows = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)
by_article: dict[str, list] = {}
for record in rows:
    by_article.setdefault(str(record.fields.get("terArticle") or "").strip(), []).append(record)

print("== БД.ТЕР — Connfly DS1071 (2,54) по чертежу C-DS1071-B")
for article in dict.fromkeys(item[0] for item in CELLS):
    found = by_article.get(article, [])
    cells = [item for item in CELLS if item[0] == article]
    if len(found) != 1:
        for _, field, old, new in cells:
            log(article, field, old, new, f"отказ (строк {len(found)})")
        continue
    record = found[0]
    edits = []
    for _, field, old, new in cells:
        have = str(record.fields.get(field, "")).strip()
        if have == new:
            log(article, field, have, new, "уже так")
        else:
            edits.append(FieldEdit(field, old, new))
    if not edits:
        continue
    with factory() as uow:
        try:
            before, after = _edit_in(record.id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            for item in edits:
                log(article, item.field, item.old, item.value, f"отказ ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [item.field for item in edits], "reason": REASON,
        }, uow)
        keys = {name: after.fields.get(name) for name in ("awgFrom", "awgTo", "mm2From", "mm2To", "terInsOdFrom", "terInsOdTo",
                                                          "terDatasheet")}
        print("   ключи карточки после правки:", json.dumps(keys, ensure_ascii=False))
        for item in edits:
            log(article, item.field, item.old, item.value, "проверено" if dry else "записано")
        if not dry:
            uow.commit()

print("ИТОГО", json.dumps({status: sum(1 for item in LOG if item["status"] == status) for status in {i["status"] for i in LOG}},
                          ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
print("LOG " + json.dumps(LOG, ensure_ascii=False))
