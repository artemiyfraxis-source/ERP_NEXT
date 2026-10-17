"""Запись в прод 16.10.2026 — D1–D3 убираются из строк коаксиальных разъёмов (БД.КОАКС).

Владелец 16.10: «надо менять концепт: разъём тянет L1–L3, а провод — свои данные, так что показатель по D1 из разъёмов
можно убирать, оставить только подходящий тип». D1–D3 — у кабеля (СПР.КАБ / «Коаксиальные кабели»); ЭТК, генератор
и каталог жгута берут их только оттуда. Строка разъёма хранит свои данные: «Провод» (подходящий кабель), L1–L3, L±,
пин, экран, HEX…

Что делает: у каждой строки БД.КОАКС ячейки D1 / D2 / D3 (и ключи-двойники d1 / d2 / d3, если записаны) становятся
пустыми — тем же путём, что «Правка записи» на экране (`_edit_in`), со старым значением у каждой ячейки: ячейку,
которую поменяли после чтения, путь отклоняет («запись уже поменяли»), и строка не пишется. Событие журнала
`references.tech_record_edited` с причиной на каждую строку. Код с 16.10 убирает опустевший D из строки совсем;
код до него оставляет пустую ячейку — данных D в строке не остаётся в обоих случаях.

Строка ищется по естественному ключу «Артикул × Провод» (coaxArticle × ключ кабеля), не по ID; пара, у которой строк
больше одной, — отказ, ни одна из них не пишется.

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Журнал для отката (ключ, ID строки, поле, было, стало, статус) — строки «CSV» вывода, сохранённые в
docs/data/«КОАКС — D1-D3 убраны из разъёмов 16.10.csv». Откат строки: `_edit_in` с old = "" и value = «было»
(на коде с 16.10 D в строку разъёма не пишется — откат только кодом до него)."""
import csv
import io
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="D1–D3 у кабеля 16.10.2026", department="System")
REASON = "D1–D3 — данные кабеля (СПР.КАБ), не разъёма (владелец 16.10.2026: «показатель по D1 из разъёмов можно убирать»)"
D_FIELDS = ("D1", "D2", "D3", "d1", "d2", "d3")  # sheet columns and their twin keys, as stored

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
LOG = []


def log(key, record_id, field, old, new, status):
    LOG.append({"key": key, "record_id": record_id, "field": field, "old": old, "new": new, "status": status})
    print(f"   {status}: «{key}» {field}: «{old}» → «{new}»")


def folded(value):
    return " ".join(str(value or "").casefold().split())


def pair_of(fields):
    article = str(fields.get("coaxArticle") or fields.get("Артикул") or "").strip()
    cable = str(fields.get("coaxWire") or fields.get("Провод") or "").strip()
    return (folded(article), cable_key(cable)), f"{article} × {cable}"


with factory() as uow:
    coax = list(uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000))
by_pair: dict[tuple[str, str], list] = {}
for record in coax:
    by_pair.setdefault(pair_of(record.fields)[0], []).append(record)

print(f"== БД.КОАКС: строк {len(coax)}, пар «Артикул × Провод» {len(by_pair)}")
rows_written = rows_same = rows_refused = 0
for pair, records in by_pair.items():
    for record in records:
        key = pair_of(record.fields)[1]
        cells = [(name, str(record.fields[name])) for name in D_FIELDS if name in record.fields]
        filled = [(name, old) for name, old in cells if old.strip()]
        if not filled:
            rows_same += 1
            continue
        if len(records) > 1:
            for name, old in filled:
                log(key, str(record.id), name, old, "", f"отказ (пара не единственна: строк {len(records)})")
            rows_refused += 1
            continue
        with factory() as uow:
            try:
                before, _after = _edit_in(record.id, tuple(FieldEdit(name, old, "") for name, old in filled), now, uow)
            except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                for name, old in filled:
                    log(key, str(record.id), name, old, "", f"отказ ({error})")
                rows_refused += 1
                continue
            _audit("references.tech_record_edited", ACTOR, now, {
                "record_id": str(before.id), "tab": before.tab.value, "fields": [name for name, _old in filled],
                "reason": REASON}, uow)
            for name, old in filled:
                log(key, str(record.id), name, old, "", "проверено" if dry else "записано")
            if not dry:
                uow.commit()
            rows_written += 1

cells_by_status: dict[str, int] = {}
for item in LOG:
    cells_by_status[item["status"]] = cells_by_status.get(item["status"], 0) + 1
print("ИТОГО", json.dumps({"строк с D": rows_written + rows_refused, "строк " + ("проверено" if dry else "записано"): rows_written,
                           "строк отказ": rows_refused, "строк уже без D": rows_same, "ячеек": cells_by_status},
                          ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
buffer = io.StringIO()
writer = csv.DictWriter(buffer, fieldnames=["key", "record_id", "field", "old", "new", "status"], lineterminator="\n")
writer.writeheader()
writer.writerows(LOG)
for line in buffer.getvalue().splitlines():
    print("CSV " + line)
