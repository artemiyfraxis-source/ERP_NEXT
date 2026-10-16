"""Запись в прод 16.10.2026 — БД.ТЕР, высоты обжима трёх контактов TE по спецификациям применения TE (допуск ±0,05,
у MQS ±0,03 — записан диапазоном):
  1-962916-1 — 114-18051-1 Rev G, табл. 2 (962916: 1,5 / 2,0 / 2,5 мм² → 1,58 / 1,73 / 1,88; 1,0 мм² — 962915: 1,36);
  1-968857-1 — 114-18148 Rev D1, табл. 5 (2,0 мм² → 1,66);
  5-1534116-1 — 114-18021 Rev V, табл. 2 (0,25 / 0,35 мм² → по жиле 0,72 / 0,76 ±0,03, по изоляции 1,40–1,50).
Копии документов — corsa-technic.com/item_docs (U114-18051-1_G.pdf, U114-18148_D1.pdf, U114-18021_V1.pdf).

Путь — `_edit_in` (правка строки «Базы данных»), старое значение ячейки сверяется. Режимы dry | apply.
"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 16.10.2026", department="System")
REASON = "высоты обжима по спецификациям применения TE (114-18051-1, 114-18148, 114-18021)"
H, INS = "Высота обжима проводника , мм", "Высота обжима изоляции, мм"
CELLS = [
    ("1-962916-1", H, "", "17 AWG: 1,31–1,41; 16 AWG: 1,53–1,63; 14 AWG: 1,68–1,78; 13 AWG: 1,83–1,93"),
    ("1-968857-1", H, "16 AWG: 1,47; 13 AWG: 1,76", "16 AWG: 1,47; 14 AWG: 1,66; 13 AWG: 1,76"),
    ("5-1534116-1", H, "22 AWG: 0,80; 20 AWG: 0,80", "24 AWG: 0,69–0,75; 22 AWG: 0,73–0,79"),
    ("5-1534116-1", INS, "24 AWG: 1,30; 22 AWG: 1,40; 20 AWG: 1,40", "24 AWG: 1,40–1,50; 22 AWG: 1,40–1,50"),
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
with factory() as uow:
    ids = {}
    for r in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000):
        arts = {str(v).strip() for k, v in r.fields.items() if ("Артикул" in k or k == "terArticle") and str(v).strip()}
        for art in arts:
            ids.setdefault(art, []).append(r.id)
done = refused = 0
for article, field, old, new in CELLS:
    found = ids.get(article, [])
    if len(found) != 1:
        refused += 1
        print(f"   отказ: «{article}» — строк {len(found)}")
        continue
    with factory() as uow:
        try:
            before, _ = _edit_in(found[0], (FieldEdit(field, old, new),), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: «{article}» {field}: {error}")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [field], "reason": REASON,
        }, uow)
        if mode == "apply":
            uow.commit()
        done += 1
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: «{article}» {field}: «{old}» → «{new}»")
print(f"ИТОГО ячеек {len(CELLS)}: {'проверено' if mode == 'dry' else 'записано'} {done}, отказ {refused}")
