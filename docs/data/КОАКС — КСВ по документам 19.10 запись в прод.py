"""Запись в прод 19.10.2026 — БД.КОАКС: «КСВ» по документам изготовителя, на которые ссылаются карточки поставщиков (второй
проход, PDF): Commscope L4HM-D, RFS NM-LCF12-C02, Huber+Suhner 11_SMA-50-2-112/133_NE и 11_SMA-50-2-65/119_NE — КСВ из документа
(худший диапазон); Huber+Suhner SMA-50-3-3/111 NH — из возвратных потерь 10 дБ (КСВ = (1+Г)/(1−Г), Г = 10^(−RL/20)). Только
пустые ячейки, все строки артикула. Откатный журнал — «КОАКС — КСВ по документам 19.10.csv».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  ... python - apply < этот файл ;  ... python - dry < этот файл  (→ всё «отказ»: уже записано)"""
import json, os, sys
from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import COAX_CARD_COLUMNS, import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
ROWS = json.loads(r'''[
 {
  "fields": {
   "coaxArticle": "Commscope L4HM-D",
   "coaxWire": "1/2″",
   "КСВ": "≤1,11 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RFS NM-LCF12-C02",
   "coaxWire": "1/2″",
   "КСВ": "≤1,08 до 3,7 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "11_SMA-50-2-112/133_NE",
   "coaxWire": "RG-316D",
   "КСВ": "≤1,11 до 5 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "11_SMA-50-2-65/119_NE",
   "coaxWire": "RG-405",
   "КСВ": "≤1,14 до 18 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-50-3-3/111 NH",
   "coaxWire": "RG-402",
   "КСВ": "≤1,92 до 18 ГГц (возвратные потери ≥10 дБ)"
  },
  "old": {
   "КСВ": ""
  }
 }
]''')
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
if "КСВ" not in COAX_CARD_COLUMNS:
    raise SystemExit("СТОП: сервер ещё без кода #552 — поле «КСВ» неизвестно")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
call = lambda dry_run: import_rows("coax", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in ROWS], ACTOR, now,
                                   dry_run=dry_run, source="erp").model_dump()
data = call(True)
if data["created"]:
    raise SystemExit(f"СТОП — строки не найдены, ничего не записано: {data['created'][:5]}")
if mode == "apply":
    data = call(False)
print(f"== {mode}: строк {len(ROWS)} → обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"][:10]:
    print("   отказ:", item)
