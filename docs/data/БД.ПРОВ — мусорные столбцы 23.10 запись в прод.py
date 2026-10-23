"""Запись в прод 23.10.2026 — БД.ПРОВ: перед чисткой столбцов списков (владелец 23.10: «почистил мусорные столбцы?»,
«если что-то заполняется на 3 % — это мусор для БД») сведения из старых текстовых столбцов переносятся в
структурированные, где те пусты. Чистка убирает из списков: коаксиальные — «Изоляция», «Экран», «Сечение, мм2», «Число
жил»; многожильные — «Изоляция», «Категория», «Волновое сопротивление, Ом»; монтажные — «Изоляция», «Экран», «Число
жил», «Ток, А». Значения в записях остаются — скрипт ничего не удаляет, только заполняет пустые ячейки.

Правила (пустая структурированная ячейка ← старый текст): коаксиальные «Изоляция» → «Материал диэлектрика» /
«Материал оболочки», «Экран» → «Материал экрана»; многожильные «Изоляция» → «Материал изоляции жилы» / «Материал
оболочки»; монтажные «Изоляция» → «Материал оболочки». Разбор — app.modules.references.coax_materials (словарь
материалов); оболочка-аббревиатура вне словаря («ПФА») берётся как есть; экран без названного металла — виды слоёв
(«фольга и проволочная оплетка» → «фольга + оплётка»). Данные прода 23.10 (1794 строки): заполняется 3 ячейки — у
остальных строк структурированные столбцы уже заполнены или старый текст не говорит о материале («шаг 1,27мм»,
«оболочка — с металлическим тросом»). Журнал — «БД.ПРОВ — мусорные столбцы 23.10.csv».

import_rows("prov") по марке (и цвету у монтажного), old = {столбец: "", старый текст: как был} — ячейку, которую
заполнили или поменяли с тех пор, сервер вернёт отказом. Новых строк не создаёт: если проверка найдёт создание, ничего
не пишет.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → обновлено 0, отказ 3 (ячейки уже заполнены)
"""
import json
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
PAYLOAD = json.loads(r'''{"fill": [{"fields": {"Марка": "GNS-360B 40 ГГц", "Материал оболочки": "ПФА"}, "old": {"Материал оболочки": "", "Изоляция": "ПТФЭ низкой плотности (оболочка ПФА)"}}, {"fields": {"Марка": "RG-58 A/U Cu ГОСТ PE", "Материал экрана": "фольга + оплётка"}, "old": {"Материал экрана": "", "Экран": "фольга и проволочная оплетка"}}, {"fields": {"Марка": "RG-58 A/U Cu ГОСТ многожильный", "Материал экрана": "фольга + оплётка"}, "old": {"Материал экрана": "", "Экран": "фольга и проволочная оплетка"}}]}''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
rows = PAYLOAD["fill"]


def call(dry_run):
    return import_rows("prov", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in rows], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
if data["created"]:  # a fill must find its row: a created row means the key went wrong — nothing is written
    print(f"== СТОП — создано бы {len(data['created'])}: {data['created'][:5]}; ничего не записано")
    raise SystemExit(1)
if not dry:
    data = call(False)
print(f"== структурированные столбцы из старого текста: строк {len(rows)} → обновлено {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["updated"]:
    print("   ", item.get("key"), item.get("changes"))
for item in data["refused"][:20]:
    print("   отказ:", item)
print("ИТОГО", "(проверка, ничего не записано)" if dry else "(записано)")
