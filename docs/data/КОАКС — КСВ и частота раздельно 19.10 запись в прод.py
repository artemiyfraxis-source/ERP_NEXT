"""Запись в прод 19.10.2026 — БД.КОАКС: КСВ отдельно, частота отдельно (владелец 19.10: «частотный диапазон отдельно, КСВ
отдельно»). Из «КСВ» убрана приписка диапазона («до 6 ГГц», «(0–3 ГГц)», «(возвратные потери …)»); пустая «Частота до, ГГц»
получила верхнюю границу этого диапазона. Формула «≤1,1 + 0,002·f» не тронута. old — текущее значение. Откатный журнал —
«КОАКС — КСВ и частота раздельно 19.10.csv».

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
   "КСВ": "≤1,11"
  },
  "old": {
   "КСВ": "≤1,11 до 6 ГГц"
  }
 },
 {
  "fields": {
   "coaxArticle": "N-112C/FC-1/2",
   "coaxWire": "1/2″",
   "КСВ": "≤1,1"
  },
  "old": {
   "КСВ": "≤1,1 (0–3 ГГц)"
  }
 },
 {
  "fields": {
   "coaxArticle": "NM-1/2",
   "coaxWire": "1/2″",
   "КСВ": "≤1,1"
  },
  "old": {
   "КСВ": "≤1,1 (0–3 ГГц)"
  }
 },
 {
  "fields": {
   "coaxArticle": "RFS NM-LCF12-C02",
   "coaxWire": "1/2″",
   "КСВ": "≤1,08"
  },
  "old": {
   "КСВ": "≤1,08 до 3,7 ГГц"
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-2950",
   "coaxWire": "5D-FB",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-male 5D-FB",
   "coaxWire": "5D-FB",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female 5D-FB",
   "coaxWire": "5D-FB",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "S-M111C",
   "coaxWire": "5D-FB",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "UHF PL259",
   "coaxWire": "5D-FB",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111B",
   "coaxWire": "LMR-400",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "F-male RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-female RG-174",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male RG-174",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-1440",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male RG-174",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-412",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-male R/A RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female R/A RG316",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-male RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Female RG316",
   "coaxWire": "RG-174",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male R/A RG178",
   "coaxWire": "RG-178",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111B",
   "coaxWire": "RG-213",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "11_SMA-50-2-112/133_NE",
   "coaxWire": "RG-316D",
   "КСВ": "≤1,11"
  },
  "old": {
   "КСВ": "≤1,11 до 5 ГГц"
  }
 },
 {
  "fields": {
   "coaxArticle": "F-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-female RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-1440",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-412",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-male R/A RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female R/A RG316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Female RG316",
   "coaxWire": "RG-316",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-50-3-3/111 NH",
   "coaxWire": "RG-402",
   "КСВ": "≤1,92"
  },
  "old": {
   "КСВ": "≤1,92 до 18 ГГц (возвратные потери ≥10 дБ)"
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male R/A RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male flanged",
   "coaxWire": "RG-405",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "BNC-C58P",
   "coaxWire": "RG-58",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female R/A RG58",
   "coaxWire": "RG-58",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-6",
   "coaxWire": "RG-6",
   "КСВ": "<2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "<2 до 6 ГГц",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "11_SMA-50-2-65/119_NE",
   "coaxWire": "RG-405",
   "КСВ": "≤1,14",
   "Частота до, ГГц": "18"
  },
  "old": {
   "КСВ": "≤1,14 до 18 ГГц",
   "Частота до, ГГц": ""
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
