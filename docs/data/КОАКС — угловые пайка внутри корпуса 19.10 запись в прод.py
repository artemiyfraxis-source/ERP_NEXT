"""Запись в прод 19.10.2026 — БД.КОАКС: у угловых разъёмов (R/A) «Тип пина» «пайка» → «пайка внутри корпуса» (владелец 19.10:
«в базе нет пайка внутри корпуса у угловых, хотя там пин в корпусе и это другая операция»). Пустые и «обжим» не трогаются.
Ключ — Артикул + Кабель, old = «пайка». Откатный журнал — «КОАКС — угловые пайка внутри корпуса 19.10.csv».

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
   "coaxArticle": "N-121/5D",
   "coaxWire": "5D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-122/5D",
   "coaxWire": "5D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-121/5D",
   "coaxWire": "5D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-122/5D",
   "coaxWire": "5D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-122/5D",
   "coaxWire": "5D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-121/8D",
   "coaxWire": "8D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-122/8D",
   "coaxWire": "8D-FB",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male R/A RG-174",
   "coaxWire": "RG-174",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-A221L",
   "coaxWire": "RG-174",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-174",
   "coaxWire": "RG-174",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-121L",
   "coaxWire": "RG-174",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "TS9 R/A",
   "coaxWire": "RG-174",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-121B",
   "coaxWire": "RG-213",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-122B",
   "coaxWire": "RG-213",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-223",
   "coaxWire": "RG-223",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male R/A RG-316",
   "coaxWire": "RG-316",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-A221L",
   "coaxWire": "RG-316",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-316",
   "coaxWire": "RG-316",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-121L",
   "coaxWire": "RG-316",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "TS9 R/A",
   "coaxWire": "RG-316",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-A125-141",
   "coaxWire": "RG-402",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-50-3-3/111 NH",
   "coaxWire": "RG-402",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-402",
   "coaxWire": "RG-402",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-125-141",
   "coaxWire": "RG-402",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male R/A RG-405",
   "coaxWire": "RG-405",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-125-085",
   "coaxWire": "RG-405",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-A125-085",
   "coaxWire": "RG-405",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-50-2-103/111 NH",
   "coaxWire": "RG-405",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-125-085",
   "coaxWire": "RG-405",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "B-121F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-253F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-122F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-A121F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG58",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "S-121F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "T-122F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
  }
 },
 {
  "fields": {
   "coaxArticle": "U-122F",
   "coaxWire": "RG-58",
   "Тип пина": "пайка внутри корпуса"
  },
  "old": {
   "Тип пина": "пайка"
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
