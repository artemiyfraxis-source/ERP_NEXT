"""Запись в прод 17.10.2026 — БД.ТЕР, JST SSQSK-A03GJ-P-L / -R: диапазон провода и обжим по JST (владелец 17.10: «так правь»).
В базе стояло 28–26 AWG (0,08–0,12 мм²) — по JST Application Tooling Guide контакт под 22–20 AWG:
SSQSK-A03GJ-P-L — аппликатор APLMK SSQSK-A03-P, 22 AWG: высота 0,98, отрыв 69,0 Н; 20 AWG: 1,08 и 88,3 Н; зачистка 3,70.
-R — тот же контакт другой намотки ленты, тот же аппликатор APLMK SSQSK-A03-P (справочник для -R пишет «CALL»).
Данные: POST https://www.jst.com/wp-content/themes/jst/filter.php (toolInput=<PN>&URL=crimp-style), страница
https://www.jst.com/resources/application-tooling-guide/crimp-style/ (только из браузера). мм² — по таблице ГОСТ → AWG ERP
(22 → 0,35, 20 → 0,5). Высота по изоляции — как у SQS-A03GF-P-P той же серии (скан таблицы JST от владельца 21.09).
Путь — `_edit_in` со сверкой старых значений.
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
REASON = "JST SQS: диапазон 22–20 AWG и обжим по JST Application Tooling Guide (владелец 17.10)"
SRC = ("JST Application Tooling Guide (crimp style), SSQSK-A03GJ-P-L: аппликатор APLMK SSQSK-A03-P, 22 AWG 0,98 / 69,0 Н, "
       "20 AWG 1,08 / 88,3 Н, зачистка 3,70 мм; -R — тот же контакт и аппликатор. https://www.jst.com/resources/application-tooling-guide/crimp-style/")
SET = {
    "От AWG": ("28", "22"), "До AWG": ("26", "20"), "От мм2": ("0,08", "0,35"), "До мм2": ("0,12", "0,5"),
    "terCrimpHeight": ("", "22 AWG: 0,98; 20 AWG: 1,08"),
    "Высота обжима изоляции, мм": ("28 AWG: 1,20; 26 AWG: 1,55", "22 AWG: 1,20; 20 AWG: 1,55"),
    "Усилие обрыва контакта, N": ("28 AWG: 11; 26 AWG: 15", "22 AWG: 69; 20 AWG: 88,3"),
}
ROWS = {"4fe00b08-442a-4ff6-8a72-2d289df85fe5": "SSQSK-A03GJ-P-L", "296bdafe-cb82-478a-9f91-5a2184150cc9": "SSQSK-A03GJ-P-R"}
CHECK = ("От AWG", "До AWG", "awgFrom", "awgTo", "mm2From", "mm2To", "terCrimpHeight", "Высота обжима проводника , мм",
         "terInsCrimpHeight", "terPullForceMin", "Источник высоты обжима")

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
for record_id, article in ROWS.items():
    with factory() as uow:
        record = uow.tech_operations.get(UUID(record_id))
        fields = record.fields
        if all(str(fields.get(name, "")).strip() == new for name, (_, new) in SET.items()) and str(fields.get("Источник высоты обжима", "")) == SRC:
            print(f"   уже так: {article}", {name: fields.get(name, "") for name in CHECK})
            continue
        edits = tuple(FieldEdit(name, old, new) for name, (old, new) in SET.items())
        edits += (FieldEdit("Источник высоты обжима", str(fields.get("Источник высоты обжима", "")), SRC),)
        try:
            before, after = _edit_in(record.id, edits, now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            print(f"   отказ: {article} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [edit.field for edit in edits], "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {article}", {name: after.fields.get(name, "") for name in CHECK})
