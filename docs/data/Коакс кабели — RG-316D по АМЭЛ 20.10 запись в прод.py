"""Запись в прод 20.10.2026 — БД.ПРОВ «RG-316D»: строка приведена к своему артикулу АМЭЛ RG-316D
(https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-gibkiy/rg-316d/: SPCW 7×0,17 ⌀0,51, PTFE ⌀1,52, две оплётки
SPC ⌀2,0 / ⌀2,4, FEP ⌀2,90). Владелец 15.10: коакс только от АМЭЛ, Крокс, gsm; 16.10 Ø уже был выбран по АМЭЛ (2,9).
Агент 20.10 по ошибке записал сюда даташит Huber+Suhner ENVIROFLEX_316_D (другой кабель) — откат; заодно сняты
значения H+S, оставшиеся от раньше (производитель, напряжение 1300, температура до 105, масса 21, ссылка chipdip).
Пустые параметры таблица показывает по карточке RG-316D (M17/152). Каждая правка — со старым значением: изменённая с
чтения ячейка вернётся отказом.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл
"""
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.references.load_mirror import ACTOR
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
AMEL = "https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-gibkiy/rg-316d/"
CELLS = [  # field, old (prod 20.10), new
    ("Материал диэлектрика", "ПЭ вспененный сшитый", "ПТФЭ"),
    ("Материал оболочки", "RADOX", "ФЭП"),
    ("Наружный диаметр, мм", "3,16", "2,9"),
    ("Погонная ёмкость, пФ/м", "94,5", ""),
    ("Радиус изгиба неподвижно, мм", "5", ""),
    ("Радиус изгиба многократно, мм", "30", ""),
    ("Напряжение, В", "1300", ""),
    ("Темп. до, °C", "105", ""),
    ("Масса, г/м", "21", ""),
    ("Производители", "Huber+Suhner ENVIROFLEX-316-D", "АМЭЛ"),
    ("Ссылка на datasheet", "https://www.chipdip.ru/product/enviroflex-316-d-100-m-rg316-d-coaxial-huber-suhner-8014772762", AMEL),
    ("Источник данных", None, "АМЭЛ, каталог amel.ru «Гибкие кабели»: " + AMEL + " — SPCW 7×0,17 ⌀0,51, PTFE ⌀1,52, оплётки SPC ⌀2,0 (93 %) и ⌀2,4 (95 %), FEP ⌀2,90; до 12,4 ГГц, 6,53 дБ/м при 12,4 ГГц. 20.10.2026 сняты значения Huber+Suhner ENVIROFLEX-316-D (другой кабель)"),
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
with factory() as uow:
    record = next(r for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)
                  if cable_key(r.fields.get("Марка") or "") == cable_key("RG-316D"))
total = {"к записи": 0, "уже так": 0, "отказ": 0}
edits = []
for field, old, new in CELLS:
    current = str(record.fields.get(field) or "")
    if current == new:
        total["уже так"] += 1
    elif old is None or current == old:
        total["к записи"] += 1
        edits.append(FieldEdit(field, current, new))
        print(f"   «RG-316D» {field}: «{current[:80]}» → «{new[:80]}»")
    else:
        total["отказ"] += 1
        print(f"   отказ {field}: в ячейке «{current[:80]}», ждали «{old[:80]}»")
if edits and mode == "apply":
    edit_record(EditRecordCommand(record.id, tuple(edits), ACTOR, moscow_now()), factory())
print("ИТОГО", total, "(записано)" if mode == "apply" else "(проверка, ничего не записано)")
