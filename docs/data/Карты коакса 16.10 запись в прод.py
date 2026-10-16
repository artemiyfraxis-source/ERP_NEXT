"""Запись в прод 16.10.2026 — карты операций коаксиальной сборки (владелец 16.10: «сделай все сам»).

Путями самого приложения, со старым значением у каждой ячейки и событием журнала:
  1. БД.ОП — строка CUT_COAX_auto «Резка» (резка коаксиала без разделки; карта операции — шаблон ERP
     `backend/data/techcard-library-erp.json`, его кладёт `seed_library --erp-only` при обновлении сервера).
     Строка — копия CUT_WIRE_auto (`create_operation_row_in`, как «Новая операция → копия»): тот же станок
     EW-05F+M, те же нормы и узлы станка, по которым коакс режется сегодня. Других замеров нет — нормы уточняет
     владелец («База данных → Операции → CUT_COAX_auto»). Пишется, только если CUT_WIRE_auto ровно такая, как в
     снимке ниже, а CUT_COAX_auto ещё нет.
  2. БД.КОАКС — свой столбец «HEX обжима, мм» (`add_column`, как «+ Столбец»: виден и правится в таблице
     «Коаксиальные разъёмы»), ЭТК опрессовки втулки экрана (PRS_SLEEVE_coax) берёт из него HEX пары «разъём × кабель».
  3. БД.КОАКС — HEX там, где его называет источник (страница поставщика), old = "" у HEX и полный старый
     «Источник данных». Остальные пары — пусто: ЭТК пишет «HEX не задан — по чертежу разъёма».

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Журнал ячеек (таблица, ключ, поле, было, стало, источник) — docs/data/«Карты коакса 16.10.csv»."""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import (ColumnCommand, FieldEdit, _audit, _edit_in, add_column, custom_columns,
                                                 create_operation_row_in, operation_code)
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Карты коакса 16.10.2026", department="System")
REASON = "карты операций коаксиальной сборки, 16.10.2026 (владелец: «сделай все сам»)"

NEW_CODE, NEW_NAME, COPY_OF = "CUT_COAX_auto", "Резка", "CUT_WIRE_auto"
# CUT_WIRE_auto на проде 16.10 (снимок перед записью): копия берёт эти нормы.
COPY_SNAPSHOT = {
    "Машина": "EW-05F+M/Автомат мерной резки проводов", "Время Операции": "3,50", "Время подготовки, сек": "180",
    "Время машины, сек/оп; сек/м": "3,50", "Тип операции": "Погонный", "Скорость проката, сек/м": "3",
    "Скорость работы инструмента": "0,5", "Кол-во операций инструмента": "1",
}

HEX_COLUMN = "HEX обжима, мм"
# (Артикул, Провод, HEX, приписка к «Источник данных») — только то, что назвал источник.
HEX = [
    ("F-male для RG-11", "RG-11", "12,07",
     "; 16.10.2026: «HEX обжима, мм» 12,07 = 0,475″ по странице gsm-repiteri.ru 2102 "
     "(https://gsm-repiteri.ru/prodazha/razyomi-75-om/razyom-f-male-obzhimnoy-na-cable-rg-11): "
     "«обжим (под клещи 0,475″)», «обжимных клещей с диаметром 0,475″»"),
]

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


def records(uow, tab):
    return uow.tech_operations.list(tab=tab, query="", limit=100_000)


print("== 1. БД.ОП CUT_COAX_auto")
with factory() as uow:
    rows = records(uow, TechOperationTab.OPERATION)
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
        except ReferenceValidationError as error:
            log("БД.ОП", NEW_CODE, "строка", "", f"копия {COPY_OF}", f"отказ ({error})")
        else:
            _audit("references.tech_record_created", ACTOR, now, {
                "record_id": str(record.id), "tab": record.tab.value, "copy_of": COPY_OF, "reason": REASON}, uow)
            for key, value in COPY_SNAPSHOT.items():
                log("БД.ОП", NEW_CODE, key, "", value, "проверено" if dry else "записано")
            if not dry:
                uow.commit()

print(f"== 2. БД.КОАКС столбец «{HEX_COLUMN}»")
with factory() as uow:
    has_column = HEX_COLUMN in custom_columns(TechOperationTab.COAX, uow)
    count = len(records(uow, TechOperationTab.COAX))
if has_column:
    log("БД.КОАКС", "*", "столбец", "", HEX_COLUMN, "уже так")
elif dry:
    log("БД.КОАКС", f"* ({count} строк)", "столбец", "", HEX_COLUMN, "проверено")
else:
    try:
        written = add_column(ColumnCommand(TechOperationTab.COAX, HEX_COLUMN, ACTOR, now), factory())
    except ReferenceValidationError as error:
        log("БД.КОАКС", "*", "столбец", "", HEX_COLUMN, f"отказ ({error})")
    else:
        log("БД.КОАКС", f"* ({written} строк)", "столбец", "", HEX_COLUMN, "записано")
        has_column = True

print("== 3. БД.КОАКС HEX")
for article, wire, value, note in HEX:
    key = f"{article} × {wire}"
    with factory() as uow:
        pair = [r for r in records(uow, TechOperationTab.COAX) if str(r.fields.get("coaxArticle", "")).strip() == article
                and cable_key(r.fields.get("coaxWire")) == cable_key(wire)]
        if len(pair) != 1:
            log("БД.КОАКС", key, HEX_COLUMN, "", value, f"отказ (строк {len(pair)})")
            continue
        record = pair[0]
        current = str(record.fields.get(HEX_COLUMN, "")).strip()
        if current == value:
            log("БД.КОАКС", key, HEX_COLUMN, current, value, "уже так")
            continue
        if current:
            log("БД.КОАКС", key, HEX_COLUMN, current, value, "отказ (ячейка уже заполнена)")
            continue
        if not has_column:  # dry before the column exists: the row and the empty cell are what apply will find
            log("БД.КОАКС", key, HEX_COLUMN, "", value, "проверено")
            continue
        source = str(record.fields.get("Источник данных", ""))
        try:
            _edit_in(record.id, (FieldEdit(HEX_COLUMN, "", value), FieldEdit("Источник данных", source, source + note)), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            log("БД.КОАКС", key, HEX_COLUMN, "", value, f"отказ ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(record.id), "tab": record.tab.value, "fields": [HEX_COLUMN, "Источник данных"],
            "reason": REASON}, uow)
        log("БД.КОАКС", key, HEX_COLUMN, "", value, "проверено" if dry else "записано")
        if not dry:
            uow.commit()

print("Итого:", ", ".join(f"{name} {count}" for name, count in sorted(totals.items())))
