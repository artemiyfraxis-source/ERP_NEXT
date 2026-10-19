"""Запись в прод 19.10.2026 — БД.ПРОВ: «Артикул поставщика» без имени продавца, как в БД.КОАКС (владелец: «заносим то же
самое как и в разъёмах»). «gsm-repiteri.ru: 0090» → «0090», когда в «Поставщик» стоит тот же продавец (один). Ячейка со
списком через «;» или с другим продавцом не трогается — печатается. Строка — по ID только внутри одного прогона: правка
идёт со старым значением, изменённая с тех пор ячейка вернётся отказом.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → «к записи 0»
"""
import os
import re
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.domain import TechOperationTab
from app.modules.references.load_mirror import ACTOR
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
FIELD = "Артикул поставщика"
PREFIXED = re.compile(r"^\s*([\w.-]+\.[a-zа-я]{2,})\s*:\s*(.+?)\s*$", re.IGNORECASE)

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry или apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()

with factory() as uow:
    records = uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)

total = {"к записи": 0, "записано": 0, "оставлено": 0, "отказ": 0}
for record in records:
    current = str(record.fields.get(FIELD) or "")
    match = PREFIXED.match(current)
    if not match:
        continue
    mark = record.fields.get("Марка") or record.display_text
    seller, article = match.group(1), match.group(2)
    if ";" in current or seller.casefold() != str(record.fields.get("Поставщик") or "").strip().casefold():
        total["оставлено"] += 1
        print(f"   оставлено «{mark}»: «{current}», поставщик «{record.fields.get('Поставщик') or ''}»")
        continue
    total["к записи"] += 1
    print(f"   «{mark}»: «{current}» → «{article}»")
    if dry:
        continue
    try:
        edit_record(EditRecordCommand(record.id, (FieldEdit(FIELD, current, article),), ACTOR, now), factory())
        total["записано"] += 1
    except Exception as error:  # noqa: BLE001 — one record refused, the rest go on
        total["отказ"] += 1
        print(f"   отказ «{mark}»: {error}")
print("ИТОГО", total, "(проверка, ничего не записано)" if dry else "(записано)")
