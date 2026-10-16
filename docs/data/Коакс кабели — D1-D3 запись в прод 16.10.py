"""Запись в прод 16.10.2026 — D1–D3 коаксиальных кабелей, у которых их не было (сверка «Коакс — сверка D, Ø и разъёмов
16.10.md», раздел B: 32 пары разъём–кабель не проверить, потому что у кабеля нет D1–D3). Значения — из даташитов,
журнал с URL — «Коакс кабели — D1-D3 по даташитам 16.10.csv».

Путь записи — тот же, что у формы «Коаксиальные кабели»: `set_coax_diameters_in`; пишется только кабель, у которого
D1–D3 сейчас пусты (old = пусто, иначе — отказ «уже поменяли»). Строки нет — форма заводит её под именем кабеля.
Наружный Ø — `import_rows("prov")` по «Марке», old="" (заполненная ячейка — отказ).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - probe < этот файл
  ... python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → все «уже так» / «отказ (заполнено)»
"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import SetCoaxDiametersCommand, set_coax_diameters_in
from app.modules.references.cables import diameter_index
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 16.10.2026", department="System")
REASON = "D1–D3 по даташиту (сверка коакса 16.10, раздел B)"
FIELD_OD = "Наружный диаметр, мм"
MARKS = ["DX-1000", "DX-10A", "H-1000", "RG-8", "3C-2V", "SAT-50", "РК50-4-11", "DG-113", "LMR-200", "RG-8X", "RP-913B",
         "SAT-752F", "RG-141", "nu-Trac TRC-1250", "РК50-1,5-21", "РК50-2-11"]
# [марка, {"D1","D2","D3"}, наружный Ø или ""]. Первые 5 — значения цеха из зеркала СПР.КАБ (совпали с даташитами),
# остальные — даташит / страница магазина (журнал CSV). Не записаны (нет надёжного источника): RG-8, 3C-2V, SAT-50,
# SAT-752F, nu-Trac TRC-1250. Наружного Ø нет: этих марок нет в «Коаксиальных кабелях» (удалены 15.10 как без поставщика).
CELLS = json.loads(r'''[
 ["РК50-4-11", {"D1": "1,37", "D2": "4,6", "D3": ""}, ""],
 ["LMR-200", {"D1": "1,12", "D2": "2,95", "D3": "3,66"}, ""],
 ["RG-141", {"D1": "0,94", "D2": "3", "D3": "3,45"}, ""],
 ["РК50-1,5-21", {"D1": "0,54", "D2": "1,5", "D3": ""}, ""],
 ["РК50-2-11", {"D1": "0,67", "D2": "2,2", "D3": ""}, ""],
 ["H-1000", {"D1": "2,62", "D2": "7,15", "D3": ""}, ""],
 ["DG-113", {"D1": "1,13", "D2": "4,8", "D3": ""}, ""],
 ["DX-1000", {"D1": "2,6", "D2": "7,11", "D3": ""}, ""],
 ["DX-10A", {"D1": "2,6", "D2": "7,24", "D3": ""}, ""],
 ["RG-8X", {"D1": "1,42", "D2": "3,94", "D3": ""}, ""],
 ["RP-913B", {"D1": "1,13", "D2": "4,75", "D3": ""}, ""]
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "probe").strip()
if mode not in ("probe", "dry", "apply"):
    raise SystemExit("режим: probe | dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def d_rows(uow):
    return diameter_index(uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=100_000))


if mode == "probe":
    with factory() as uow:
        dia = d_rows(uow)
        prov = {cable_key(r.fields.get("Марка")): r for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)}
        for mark in MARKS:
            d, p = dia.get(cable_key(mark)), prov.get(cable_key(mark))
            print(f"{mark}\tD: {({n: d.fields.get(n, '') for n in ('Кабель', 'D1', 'D2', 'D3')} if d else 'нет строки')}\t"
                  f"ПРОВ: {({n: p.fields.get(n, '') for n in ('Марка', FIELD_OD, 'Волновое сопротивление, Ом', 'Ссылки на datasheet')} if p else 'нет строки')}")
    raise SystemExit(0)

print("== СПР.КАБ D1–D3")
for mark, value, _ in CELLS:
    with factory() as uow:
        current = d_rows(uow).get(cable_key(mark))
        have = {n: str(current.fields.get(n, "")).strip() for n in ("D1", "D2", "D3")} if current else {"D1": "", "D2": "", "D3": ""}
        if have == value:
            print(f"   уже так: {mark} {value}")
            continue
        try:
            set_coax_diameters_in(SetCoaxDiametersCommand(cable=mark, dia_id=current.id if current else None,
                                                          old={"D1": "", "D2": "", "D3": ""}, value=value, actor=ACTOR, now=now),
                                  uow, {"reason": REASON})
        except (StaleRecordError, ReferenceValidationError) as error:
            print(f"   отказ: {mark} было {have} → {value} ({error})")
            continue
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {mark} {have} → {value}{' (новая строка)' if current is None else ''}")
        if mode == "apply":
            uow.commit()

rows = [ImportRowIn(fields={"Марка": mark, FIELD_OD: od}, old={FIELD_OD: ""}) for mark, _, od in CELLS if od]
if rows:
    def call(dry_run):
        return import_rows("prov", factory(), rows, ACTOR, now, dry_run=dry_run, source="erp").model_dump()
    data = call(True)
    if data["created"]:
        print("== СТОП — марки не найдены в БД.ПРОВ, Ø не записан:", data["created"])
        raise SystemExit(1)
    if mode == "apply":
        data = call(False)
    print(f"== БД.ПРОВ «{FIELD_OD}»: {len(rows)} → обновлено {len(data['updated'])}, без изменений {data['unchanged']}, "
          f"отказ {len(data['refused'])}")
    for item in data["refused"]:
        print("   отказ:", item)
print("ИТОГО", "(проверка, ничего не записано)" if mode == "dry" else "(записано)")
