"""Запись в прод 16.10.2026 — карты операций сложной сборки (владелец 16.10, жгут 713К.4 «Жгут НВ4»).

Карты операций — шаблоны ERP `backend/data/techcard-library-erp.json` (их кладёт `seed_library --erp-only` при
обновлении сервера):
  - INS_MARK_TUBE_AUTO «Печать и резка маркировочной трубки» — ОБ.102 ZCUT-RT660. Строка БД.ОП INS_MARK_TUBE_AUTO
    на проде уже есть (4 с/шт, подготовка 600 с) — её этот скрипт не трогает, только проверяет, что она на месте.
  - CUT_WIRE+2strip+tin «Резка с зачисткой двух концов и лужением» — ОБ.098 BZW-5.0+Z. Строки БД.ОП не было.
Скрипт пишет единственное: строку БД.ОП CUT_WIRE+2strip+tin — копией CUT_WIRE+1crimp+tin (тот же станок BZW-5.0+Z,
время машины, цена машины, скорость проката, узлы станка) с двумя своими нормами: «Время Операции» 6,50 с и «Время
подготовки» 600 с — как у CUT_WIRE+2strip (резка и зачистка двух концов). Пишется, только если CUT_WIRE+1crimp+tin
ровно такая, как в снимке ниже, а CUT_WIRE+2strip+tin ещё нет. Норму уточняет владелец («База данных → Операции»).

Порядок (live-data protocol, prod-reference-write; по умолчанию — dry, ничего не пишет):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in, create_operation_row_in, operation_code
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Карты сложной сборки 16.10.2026", department="System")
REASON = "карты операций сложной сборки (жгут 713К.4), 16.10.2026, владелец"

NEW_CODE, NEW_NAME, COPY_OF = "CUT_WIRE+2strip+tin", "Резка с зачисткой двух концов и лужением", "CUT_WIRE+1crimp+tin"
MARK_CODE = "INS_MARK_TUBE_AUTO"
# CUT_WIRE+1crimp+tin (снимок БД.ОП из зеркала 16.10): копия берёт эти нормы.
COPY_SNAPSHOT = {
    "Машина": "BZW-5.0+Z/Станок резки/ обжим+лужение", "Время Операции": "4,00", "Время подготовки, сек": "3600",
    "Время машины, сек/оп; сек/м": "4,00", "Уд.Цена МШ, сек": "0,29", "Тип операции": "Погонный",
    "Скорость проката, сек/м": "3", "Скорость работы инструмента": "0,2", "Кол-во операций инструмента": "5",
}
# Свои нормы новой строки: (поле, было у копии, стало) — как у CUT_WIRE+2strip.
OWN = (("Время Операции", "4,00", "6,50"), ("Время подготовки, сек", "3600", "600"))

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
totals: dict[str, int] = {}


def log(table, key, field, old, new, status):
    totals[status.split(" ")[0]] = totals.get(status.split(" ")[0], 0) + 1
    print(f"   {status}: {table} «{key}» {field}: «{old}» → «{new}»")


print(f"== БД.ОП {MARK_CODE} (должна быть на месте)")
with factory() as uow:
    rows = uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
    mark = [r for r in rows if operation_code(r.fields) == MARK_CODE]
    log("БД.ОП", MARK_CODE, "строка", "", "есть",
        "уже так" if len(mark) == 1 else f"отказ (строк {len(mark)}: карту печати трубки без строки нормы не планировать)")

print(f"== БД.ОП {NEW_CODE}")
with factory() as uow:
    rows = uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
    mine = [r for r in rows if operation_code(r.fields).casefold() == NEW_CODE.casefold()]
    source = [r for r in rows if operation_code(r.fields) == COPY_OF]
    if mine:
        log("БД.ОП", NEW_CODE, "строка", "", f"копия {COPY_OF}", "уже так" if len(mine) == 1 else f"отказ (строк {len(mine)})")
    elif len(source) != 1:
        log("БД.ОП", NEW_CODE, "строка", "", f"копия {COPY_OF}", f"отказ (строк {COPY_OF}: {len(source)})")
    elif drift := {k: (str(source[0].fields.get(k, "")).strip(), v) for k, v in COPY_SNAPSHOT.items()
                   if str(source[0].fields.get(k, "")).strip() != v}:
        log("БД.ОП", NEW_CODE, "строка", "", f"копия {COPY_OF}", f"отказ ({COPY_OF} поменяли после снимка: {drift})")
    else:
        try:
            record = create_operation_row_in(NEW_CODE, NEW_NAME, COPY_OF, uow)
            _edit_in(record.id, tuple(FieldEdit(field, old, new) for field, old, new in OWN), now, uow)
        except (ReferenceValidationError, StaleRecordError, LookupError) as error:
            log("БД.ОП", NEW_CODE, "строка", "", f"копия {COPY_OF}", f"отказ ({error})")
        else:
            _audit("references.tech_record_created", ACTOR, now, {
                "record_id": str(record.id), "tab": record.tab.value, "copy_of": COPY_OF, "reason": REASON}, uow)
            own = {field: new for field, _, new in OWN}
            for key, value in COPY_SNAPSHOT.items():
                log("БД.ОП", NEW_CODE, key, "", own.get(key, value), "проверено" if dry else "записано")
            if not dry:
                uow.commit()

print("Итого:", ", ".join(f"{name} {count}" for name, count in sorted(totals.items())))
