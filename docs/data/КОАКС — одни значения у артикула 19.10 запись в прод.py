"""Запись в прод 19.10.2026 — БД.КОАКС: у одного артикула одни значения (владелец 19.10: «у одного артикула не могут быть
разные значения»). Пустые свойства разъёма дописаны из другой строки того же артикула, «Артикул поставщика» объединён,
N-111B × LMR-400: L1–L3 по документу изготовителя (Балтик Сигнал, как у N-111B × RG-213 с 17.10). Ключ — Артикул + Кабель,
old — текущее значение. Откатный журнал — «КОАКС — одни значения у артикула 19.10.csv».

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
   "coaxArticle": "N1-111/5D",
   "coaxWire": "RG-59",
   "Артикул поставщика": "gsm-repiteri.ru: 1490; kroks.ru: 769"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1490"
  }
 },
 {
  "fields": {
   "coaxArticle": "N1-111/5D",
   "coaxWire": "RG-6",
   "Артикул поставщика": "gsm-repiteri.ru: 1490; kroks.ru: 769"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1490"
  }
 },
 {
  "fields": {
   "coaxArticle": "N1-111/5D",
   "coaxWire": "SAT-703",
   "Артикул поставщика": "gsm-repiteri.ru: 1490; kroks.ru: 769"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1490"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111/5D",
   "coaxWire": "CNT-300",
   "Артикул поставщика": "gsm-repiteri.ru: 0111; kroks.ru: 304; vegatel.ru: R01432"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0111"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111/5D",
   "coaxWire": "LMR-300",
   "Артикул поставщика": "gsm-repiteri.ru: 0111; kroks.ru: 304; vegatel.ru: R01432"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0111; kroks.ru: 304"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111/5D",
   "coaxWire": "RG-59",
   "Артикул поставщика": "gsm-repiteri.ru: 0111; kroks.ru: 304; vegatel.ru: R01432"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0111"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111/5D",
   "coaxWire": "RG-6",
   "Артикул поставщика": "gsm-repiteri.ru: 0111; kroks.ru: 304; vegatel.ru: R01432"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0111"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111/5D",
   "coaxWire": "SAT-703",
   "Артикул поставщика": "gsm-repiteri.ru: 0111; kroks.ru: 304; vegatel.ru: R01432"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0111"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-112/5D",
   "coaxWire": "RG-59",
   "Артикул поставщика": "gsm-repiteri.ru: 0294; kroks.ru: 576"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0294"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-112/5D",
   "coaxWire": "RG-6",
   "Артикул поставщика": "gsm-repiteri.ru: 0294; kroks.ru: 576"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0294"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-112/5D",
   "coaxWire": "SAT-703",
   "Артикул поставщика": "gsm-repiteri.ru: 0294; kroks.ru: 576"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 0294"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-111B",
   "coaxWire": "8D-FB",
   "Артикул поставщика": "gsm-repiteri.ru: 1477; kroks.ru: 806"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1477"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-111B",
   "coaxWire": "DX-1000",
   "Артикул поставщика": "gsm-repiteri.ru: 1477; kroks.ru: 806"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1477"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-111B",
   "coaxWire": "DX-10A",
   "Артикул поставщика": "gsm-repiteri.ru: 1477; kroks.ru: 806"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1477"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-111B",
   "coaxWire": "H-1000",
   "Артикул поставщика": "gsm-repiteri.ru: 1477; kroks.ru: 806"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1477"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-111B",
   "coaxWire": "RG-8",
   "Артикул поставщика": "gsm-repiteri.ru: 1477; kroks.ru: 806"
  },
  "old": {
   "Артикул поставщика": "gsm-repiteri.ru: 1477"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111B",
   "coaxWire": "LMR-400",
   "L1": "5",
   "L2": "7",
   "L3": "15",
   "Артикул поставщика": "gsm-repiteri.ru: 3431; kroks.ru: 759",
   "Исполнение": "прямой",
   "Монтаж": "обжимной",
   "Пол": "штекер",
   "Полярность": "обычная",
   "Серия": "N"
  },
  "old": {
   "L1": "3,6",
   "L2": "6",
   "L3": "13,5",
   "Артикул поставщика": "kroks.ru: 759",
   "Исполнение": "",
   "Монтаж": "",
   "Пол": "",
   "Полярность": "",
   "Серия": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111B",
   "coaxWire": "RG-213",
   "КСВ": "<2 до 6 ГГц",
   "Тип пина": "пайка",
   "Тип экрана": "обжим"
  },
  "old": {
   "КСВ": "",
   "Тип пина": "",
   "Тип экрана": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "T-111L",
   "coaxWire": "RG-174",
   "Тип пина": "пайка",
   "Тип экрана": "обжим"
  },
  "old": {
   "Тип пина": "",
   "Тип экрана": ""
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
