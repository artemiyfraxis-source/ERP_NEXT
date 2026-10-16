"""Запись в прод 16.10.2026 — L+ / L− 20 терминалов БД.ТЕР: Molex C-Grid III (6) и KK 396 (14), по глубине конца
жилы (владелец 16.10: «закрыть всё»; «"примерно" не надо, будем на деле проверять»). Продолжение записи
«БД.ТЕР — L+ по глубине жилы, все серии 16.10» (81 строка, применена, бэкап 191250).

Смысл (PR #476): L+ — глубина конца жилы от торца корпуса со стороны провода, L− — от конца жилы до стыковочного
торца, L+ + L− = длина корпуса. Правило L+ = 0,5 × корпус (как для серий без чертежа контакта в записи на 81 строку):
- C-Grid III 90123 (корпус 15,24, SD-90123): L+ 5 → 7,6, L− 10,24 → 7,64; «Источник L-» — ссылка на SD-90123;
- KK 396, корпус 41695 (15,88 вдоль провода): L+ / L− пусты → 7,95 / 7,93; «Источник L-» остаётся пустым.
Как получено — в «БД.ТЕР — L+ по глубине жилы, C-Grid и KK 396 16.10.csv» и .md рядом. В базу идут только числа.

Путь приложения: `import_rows("ter")` по натуральному ключу «terArticle»; у каждой строки `old` — значения прода на
16.10: ячейка, изменённая после снимка, придёт отказом и не перепишется; строк не создаётся.

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «отказ» (уже записано)"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="L+ по глубине жилы, C-Grid и KK 396 16.10.2026", department="System")
FIELDS = ("terLPlus", "terLMinus", "L+", "L-", "Источник L-")
ROWS = json.loads(r'''
[
 {
  "fields": {
   "terArticle": "901190109",
   "terLPlus": "7,6",
   "terLMinus": "7,64",
   "Источник L-": "чертёж корпуса: Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf"
  },
  "old": {
   "terArticle": "901190109",
   "terLPlus": "5",
   "terLMinus": "10,24",
   "Источник L-": "оценка по чертежу корпуса: L− = C-Grid III 90123 15,24 мм − L+ 5 мм; Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf; 90142, 90143, 90156, 90160 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "901190110",
   "terLPlus": "7,6",
   "terLMinus": "7,64",
   "Источник L-": "чертёж корпуса: Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf"
  },
  "old": {
   "terArticle": "901190110",
   "terLPlus": "5",
   "terLMinus": "10,24",
   "Источник L-": "оценка по чертежу корпуса: L− = C-Grid III 90123 15,24 мм − L+ 5 мм; Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf; 90142, 90143, 90156, 90160 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "901190111",
   "terLPlus": "7,6",
   "terLMinus": "7,64",
   "Источник L-": "чертёж корпуса: Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf"
  },
  "old": {
   "terArticle": "901190111",
   "terLPlus": "5",
   "terLMinus": "10,24",
   "Источник L-": "оценка по чертежу корпуса: L− = C-Grid III 90123 15,24 мм − L+ 5 мм; Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf; 90142, 90143, 90156, 90160 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "901190120",
   "terLPlus": "7,6",
   "terLMinus": "7,64",
   "Источник L-": "чертёж корпуса: Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf"
  },
  "old": {
   "terArticle": "901190120",
   "terLPlus": "5",
   "terLMinus": "10,24",
   "Источник L-": "оценка по чертежу корпуса: L− = C-Grid III 90123 15,24 мм − L+ 5 мм; Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf; 90142, 90143, 90156, 90160 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "901190121",
   "terLPlus": "7,6",
   "terLMinus": "7,64",
   "Источник L-": "чертёж корпуса: Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf"
  },
  "old": {
   "terArticle": "901190121",
   "terLPlus": "5",
   "terLMinus": "10,24",
   "Источник L-": "оценка по чертежу корпуса: L− = C-Grid III 90123 15,24 мм − L+ 5 мм; Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf; 90142, 90143, 90156, 90160 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "901190122",
   "terLPlus": "7,6",
   "terLMinus": "7,64",
   "Источник L-": "чертёж корпуса: Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf"
  },
  "old": {
   "terArticle": "901190122",
   "terLPlus": "5",
   "terLMinus": "10,24",
   "Источник L-": "оценка по чертежу корпуса: L− = C-Grid III 90123 15,24 мм − L+ 5 мм; Molex SD-90123, https://static.chipdip.ru/lib/237/DOC035237016.pdf; 90142, 90143, 90156, 90160 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "08-52-0071",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "08-52-0071",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8500063",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8500063",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8550103",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8550103",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8560105",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8560105",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8550133",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8550133",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8500105",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8500105",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8500119",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8500119",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "39000286",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "39000286",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8520811",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8520811",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8650114",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8650114",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8580121",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8580121",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8580114",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8580114",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8500281",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8500281",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "8520071",
   "terLPlus": "7,95",
   "terLMinus": "7,93"
  },
  "old": {
   "terArticle": "8520071",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 }
]
''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))


def current() -> dict[str, dict]:
    with factory() as uow:
        records = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)
    return {str(r.fields.get("terArticle") or ""): dict(r.fields) for r in records}


before = current()
missing = [row["fields"]["terArticle"] for row in ROWS if row["fields"]["terArticle"] not in before]
if missing:
    raise SystemExit(f"ОШИБКА: нет в БД.ТЕР: {missing}")
with factory() as uow:
    report = import_rows("ter", uow, [ImportRowIn(fields=row["fields"], old=row["old"]) for row in ROWS], ACTOR,
                         moscow_now(), dry_run=dry, source="erp").model_dump()
for item in report["refused"]:
    print("отказ:", json.dumps(item, ensure_ascii=False, default=str))
if report["created"]:
    raise SystemExit("ОШИБКА: импорт создал бы строки — ключ не найден")
if not dry:
    after = current()
    for row in ROWS:
        have = after[row["fields"]["terArticle"]]
        print("стало:", row["fields"]["terArticle"], {name: have.get(name, "") for name in FIELDS})
print("ИТОГО", json.dumps({"строк": len(ROWS), "обновится" if dry else "обновлено": len(report["updated"]),
                           "отказ": len(report["refused"]), "создано": len(report["created"])}, ensure_ascii=False),
      "(проверка, ничего не записано)" if dry else "(записано)")
