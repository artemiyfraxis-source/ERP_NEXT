"""Запись в прод 17.10.2026 — БД.ТЕР, TE Superseal 183024-1 / 183025-1 / 183035-1 / 183036-1: высота обжима по жиле по
инструкции TE 411-20000 (ручной инструмент 785353-1) вместо значений «по аналогу». Документ:
https://web.archive.org/web/20180313110625id_/http://www.te.com:80/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm=411-20000&DocType=Specification+Or+Standard&DocLang=English&DocFormat=pdf&PartCntxt=282110-1
Высоты в документе — по сечению, допуск ±0,05: 0,35 мм² — 1,09; 0,50 — 1,12 (183035-1 / 183036-1); 1,0 — 1,35;
1,5 — 1,37 (183024-1 / 183025-1). Сечение → AWG — по таблице «ГОСТ → AWG» ERP (владелец 17.10: «у нас есть таблица»):
0,35 → 22, 0,5 → 20, 1,0 → 17, 1,5 → 16. 0,75 мм² (18 AWG) в документе нет — у 18 AWG стоящее значение не трогаю.

Путь — `_edit_in` со сверкой старого значения (поменяли — отказ).
  ... python - dry < этот файл ; erp.sh backup-now ; ... python - apply < этот файл ; ... python - dry → «уже так»
"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "высота обжима Superseal по TE 411-20000, сечение → AWG по таблице ГОСТ → AWG (владелец 17.10)"
H = "Высота обжима проводника , мм"
SRC = "Источник высоты обжима"
DOC = ("TE 411-20000 (инструмент 785353-1), ±0,05: 0,35 мм² — 1,09; 0,5 мм² — 1,12; 1,0 мм² — 1,35; 1,5 мм² — 1,37; "
       "мм² → AWG по таблице ГОСТ → AWG ERP. https://web.archive.org/web/20180313110625id_/http://www.te.com:80/commerce/"
       "DocumentDelivery/DDEController?Action=srchrtrv&DocNm=411-20000&DocType=Specification+Or+Standard&DocLang=English&DocFormat=pdf&PartCntxt=282110-1")
SMALL = "22 AWG: 1,04–1,14; 20 AWG: 1,07–1,17"
ROWS = {  # id: (артикул, было, стало, источник)
    "4345dbc1-1d60-414e-9694-047bf9e43ea8": ("183035-1", "22 AWG: 0,94–1,04; 20 AWG: 0,99–1,09", SMALL, DOC),
    "9e426687-b151-4010-ae7f-c422fde25722": ("183036-1", "22 AWG: 0,94–1,04; 20 AWG: 0,99–1,09", SMALL, DOC),
    "e6c0ae20-da97-41c9-9d15-2e1f6e51cdf0": ("183024-1", "18 AWG: 1,29–1,39; 17 AWG: 1,40–1,50; 16 AWG: 1,47–1,57",
                                             "18 AWG: 1,29–1,39; 17 AWG: 1,30–1,40; 16 AWG: 1,32–1,42",
                                             DOC + " 18 AWG (0,75 мм²) в документе нет — значение прежнее."),
    "57ecec57-f9fb-4566-bb6b-f812152a3723": ("183025-1", "18 AWG: 1,29–1,39; 16 AWG: 1,47–1,57",
                                             "18 AWG: 1,29–1,39; 17 AWG: 1,30–1,40; 16 AWG: 1,32–1,42",
                                             DOC + " 18 AWG (0,75 мм²) в документе нет — значение прежнее."),
}

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
done = same = refused = 0
for record_id, (article, old, new, source) in ROWS.items():
    with factory() as uow:
        record = uow.tech_operations.get(UUID(record_id))
        have, have_src = str(record.fields.get(H, "")).strip(), str(record.fields.get(SRC, "")).strip()
        if have == new and have_src == source:
            same += 1
            print(f"   уже так: {article}")
            continue
        try:
            before, _ = _edit_in(record.id, (FieldEdit(H, old, new), FieldEdit(SRC, have_src if have == old else "—", source)), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: {article} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [H, SRC], "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        done += 1
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {article}: «{old}» → «{new}»")
print(f"ИТОГО {len(ROWS)}: {'проверено' if mode == 'dry' else 'записано'} {done}, уже так {same}, отказ {refused}")
