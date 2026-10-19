"""Запись в прод 19.10.2026 — БД.КОАКС, «Тип экрана» микро-разъёмов MHF1 (в т.ч. Lock) и U.FL: обжим → корпус (владелец 19.10:
«MHF1, U.FL — там не может быть HEX, там монтаж экрана не гильзой»). Экран зажимается корпусом разъёма, гильзы и матрицы HEX нет.
На проде таких строк 6 (MHF1 ×4 с Lock, U.FL ×2; IPEX / W.FL в базе нет); HEX у всех пуст — чистить нечего (проверено списком до записи).
Каждое изменение дописывается в «Источник данных» («19.10.2026: Тип экрана обжим → корпус — …»).

Загрузка import_rows("coax") по паре артикул + провод; old={поле: было} — изменённая кем-то ячейка вернётся «отказом».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry      < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply    < этот файл
  ... python - dry      < этот файл   → все «без изменений»
  ... python - rollback < этот файл   → вернуть «обжим» (только ячейки, где сейчас «корпус»), убрать приписку из источника
Журнал ячеек — «Коакс — MHF1 и U.FL экран корпусом 19.10.csv»."""
import csv
import io
import os
import sys
from collections import defaultdict
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Экран корпусом MHF1/U.FL 19.10.2026", department="System")
SOURCE = "Источник данных"
NOTE_FIELD = "Источник данных (дописано)"
LABEL = {"coaxType": "Тип/Серия"}
CSV_TEXT = r'''Артикул,Провод,Поле,Было,Стало,Основание
MHF1 for 1.13,RF 1.13,Тип экрана,обжим,корпус,"экран зажимается корпусом разъёма, без гильзы (владелец)"
MHF1-plug RF 1.37,RF 1.37,Тип экрана,обжим,корпус,"экран зажимается корпусом разъёма, без гильзы (владелец)"
MHF1-plug RG-178,RG-178,Тип экрана,обжим,корпус,"экран зажимается корпусом разъёма, без гильзы (владелец)"
20278-112R-18,RG-178,Тип экрана,обжим,корпус,"экран зажимается корпусом разъёма, без гильзы (владелец)"
U.FL-female (IPX-female) угловой для RG-178,RG-178,Тип экрана,обжим,корпус,"экран зажимается корпусом разъёма, без гильзы (владелец)"
U.FL,RG-178,Тип экрана,обжим,корпус,"экран зажимается корпусом разъёма, без гильзы (владелец)"
'''
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply", "rollback"):
    raise SystemExit("режим: dry | apply | rollback")
cells = list(csv.DictReader(io.StringIO(CSV_TEXT.lstrip("﻿"))))
groups: dict = defaultdict(list)
for cell in cells:
    groups[(cell["Артикул"], cell["Провод"])].append(cell)


def note_of(items) -> str:
    parts = []
    for item in items:
        if item["Поле"] == NOTE_FIELD:
            parts.append(item["Стало"])
        else:
            parts.append(f"{LABEL.get(item['Поле'], item['Поле'])} {item['Было'] or '—'} → {item['Стало']} — {item['Основание']}")
    return "19.10.2026: " + "; ".join(parts)


factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
with factory() as uow:
    live = {}
    for record in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000):
        live[(str(record.fields.get("coaxArticle", "")).strip().casefold(), cable_key(record.fields.get("coaxWire")))] = dict(record.fields)

rows, missing = [], []
for (article, wire), items in groups.items():
    current = live.get((article.casefold(), cable_key(wire)))
    if current is None:
        missing.append((article, wire))
        continue
    note = note_of(items)
    source = str(current.get(SOURCE, ""))
    fields = {"coaxArticle": article, "coaxWire": wire}
    old = {}
    for item in items:
        if item["Поле"] == NOTE_FIELD:
            continue
        if mode == "rollback":
            fields[item["Поле"]], old[item["Поле"]] = item["Было"], item["Стало"]
        else:
            fields[item["Поле"]], old[item["Поле"]] = item["Стало"], item["Было"]
    if mode == "rollback":
        if source == note:
            fields[SOURCE], old[SOURCE] = "", source
        elif source.endswith("; " + note):
            fields[SOURCE], old[SOURCE] = source[: -len("; " + note)], source
    elif note not in source:
        fields[SOURCE], old[SOURCE] = (source + "; " + note) if source else note, source
    rows.append(ImportRowIn(fields=fields, old=old))
if missing:
    raise SystemExit(f"== СТОП — пары не найдены на проде: {missing}")


def call(dry_run):
    return import_rows("coax", factory(), rows, ACTOR, now, dry_run=dry_run, source="erp").model_dump()


data = call(True)
print(f"ячеек {len(cells)}, строк {len(rows)}: создаётся {len(data['created'])}, обновится {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"][:20]:
    print("   отказ:", item)
for item in data["updated"][:6]:
    print("   пример:", item)
if data["created"]:
    raise SystemExit("== СТОП — есть новые строки (ключ не найден)")
if mode in ("apply", "rollback"):
    if data["refused"]:
        raise SystemExit("== СТОП — есть отказы, ничего не записано")
    data = call(False)
    print(f"== записано: обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
