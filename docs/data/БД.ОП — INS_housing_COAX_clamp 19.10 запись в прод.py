"""Запись в прод 19.10.2026 — БД.ОП, новая строка INS_housing_COAX_clamp («Монтаж корпуса с прижимом», ЭТК, #568).
Норма по аналогии (владелец: «решай сам»): tOp = INS_housing_COAX 10,00 + PRS_SLEEVE_coax 8,00 = 18,00 с
(прижим заменяет монтаж корпуса и опрессовку втулки; затяжка гайки вместо опрессовки). Расчёс/обрезка оплётки: в БД.ОП нет
операции с выделенной частью «сложить оплётку» (INS_BRAID 20 с — надеть оплётку на жгут, PREP_FOLD 10 с — сложить жилу), добавка 0.
tPrep = 120 (как INS_housing_COAX), станка нет (tMachine 0). Взять 2 + работа 14 (6 + 8) + снять 2 = 18.
Путь — `_create_in` (БД.ОП нет в import_rows): строка копирует INS_housing_COAX; «Номер» уникален — повторная запись
отказывает (guard вместо перезаписи). Режимы: dry | apply.
"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import _audit, _clean_new_fields, _create_in, operation_code
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Нормы 19.10.2026", department="System")
CODE, BASE = "INS_housing_COAX_clamp", "INS_housing_COAX"
NOTE = "по аналогии с INS_housing_COAX + PRS_SLEEVE_coax, 19.10 — уточнить хронометражем"
NEW = {
    "opNumber": CODE, "Номер": CODE, "tOp": "18,00", "Время Операции": "18,00", "tPrep": "120", "Время подготовки, сек": "120",
    "tMachine": "0,00", "Время машины, сек/оп; сек/м": "0,00", "Время Чел, сек/оп; сек/м": "18,00",
    "Время ручных работ для взятия полуфабриката": "2", "Время ручных работ": "14",
    "Время ручных работ для снятия полуфабриката": "2", "Инструкция": NOTE,
}
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
with factory() as uow:
    rows = list(uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000))
    if any(operation_code(r.fields) == CODE for r in rows):
        print("   уже есть:", CODE)
        raise SystemExit(0)
    base = next(r for r in rows if operation_code(r.fields) == BASE)
    record = _create_in(TechOperationTab.OPERATION, _clean_new_fields(TechOperationTab.OPERATION, {**base.fields, **NEW}), uow)
    _audit("references.tech_record_created", ACTOR, now, {"record_id": str(record.id), "tab": record.tab.value,
                                                          "reason": NOTE}, uow)
    if mode == "apply":
        uow.commit()
    print("   создано" if mode == "apply" else "   проверено (создалась бы)", {k: record.fields.get(k, "") for k in NEW})
