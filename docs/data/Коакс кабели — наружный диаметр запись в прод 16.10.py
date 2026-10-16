"""Запись в прод 16.10.2026 — «Коаксиальные кабели»: пустой «Наружный диаметр, мм» у 8 марок по даташиту изготовителя
или странице поставщика (журнал ячеек с URL — docs/data/«Коакс кабели — D и Ø сверка 16.10.csv», раздел «записано»).

import_rows("prov") по «Марке», old="" у ячейки — заполненная ячейка возвращается отказом; марки не меняются, строки не
создаются (если хоть одна марка не найдена — СТОП, ничего не пишется). Порядок (live-data protocol, prod-reference-write):

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → все 8 «отказ» (ячейка уже заполнена)
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
FIELD = "Наружный диаметр, мм"
CELLS = json.loads(r'''[
 ["HPL 50-1/2F", "16"],
 ["LCF12-50J", "15,8"],
 ["LCF78-50J", "27,8"],
 ["РК50-23-391-Снг(С)", "27,8"],
 ["RF 1.37", "1,37"],
 ["1/2″", "15,8"],
 ["1/2″ SF", "13,75"],
 ["7/8″", "27,8"]
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
ROWS = [ImportRowIn(fields={"Марка": mark, FIELD: value}, old={FIELD: ""}) for mark, value in CELLS]


def call(dry_run):
    return import_rows("prov", factory(), ROWS, ACTOR, now, dry_run=dry_run, source="erp").model_dump()


data = call(True)
if data["created"]:
    print("== СТОП — марки не найдены, ничего не записано:", data["created"])
    raise SystemExit(1)
if mode == "apply":
    data = call(False)
print(f"== БД.ПРОВ «{FIELD}»: ячеек {len(ROWS)} → обновлено {len(data['updated'])}, без изменений {data['unchanged']}, "
      f"отказ {len(data['refused'])}")
for item in data["updated"]:
    print("   обновлено:", item)
for item in data["refused"]:
    print("   отказ:", item)
print("ИТОГО", "(проверка, ничего не записано)" if mode == "dry" else "(записано)")
