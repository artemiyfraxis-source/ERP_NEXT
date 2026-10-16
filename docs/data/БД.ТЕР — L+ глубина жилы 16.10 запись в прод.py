"""Запись в прод 16.10.2026 — L+ / L− терминалов БД.ТЕР по смыслу генератора (владелец 16.10: «сделай все сам»).

L+ — глубина конца жилы от торца корпуса со стороны провода (генератор прибавляет её к длине «между разъёмами»),
L− — от конца жилы до стыковочного торца (вычитается из длины «от краёв разъёмов»); L+ + L− = длина корпуса.
Длина контакта — не L+. Из 184 строк БД.ТЕР с заполненным L+ ни у одной нет источника глубины жилы; источник нашёлся
только у Molex KK 254 (корпус 2695, контакт 08-50-0113 серии 2759): на чертеже 26950000-SD в разрезе с контактом
«WIRE INTO HOUSING 6.73 REF» от заднего торца корпуса 12,7 мм → L+ 6,73, L− 5,97 (у строки оба пусты).
Остальные строки не трогаются — список в «БД.ТЕР — L+ глубина жилы 16.10.md».

Путь приложения: `import_rows("ter")` по натуральному ключу «terArticle», у каждой ячейки старое значение "" — ячейка,
которую заполнили после снимка, приходит отказом и не пишется; строк не создаётся. Тело — то же, что строка 8500113 в
backend/data/terminals/ter-lengths-estimates.json.

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «отказ» (уже записано)
Журнал ячеек (ключ, поле, было, стало, источник) — docs/data/«БД.ТЕР — L+ глубина жилы 16.10.csv»."""
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
ACTOR = Actor(id=UUID(int=0), name="L+ по глубине жилы 16.10.2026", department="System")
ROWS = json.loads(r'''[
 {
  "fields": {
   "terArticle": "8500113",
   "terLMinus": "5,97",
   "Источник L-": "по чертежу: L+ = глубина конца жилы от торца корпуса со стороны провода 6,73 мм; L− = KK 254, 2695 12,7 мм − L+ 6,73 мм; Molex 26950000-SD (SD-2695), разрез с контактом: «WIRE INTO HOUSING 6.73 REF» от заднего торца корпуса, https://static.chipdip.ru/lib/879/DOC011879936.pdf",
   "terLPlus": "6,73"
  },
  "old": {
   "terArticle": "8500113",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 }
]''')

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
for row in ROWS:
    have = before.get(row["fields"]["terArticle"])
    print("было:", row["fields"]["terArticle"], None if have is None else
          {name: have.get(name, "") for name in ("terLPlus", "terLMinus", "L+", "L-", "Источник L-")})
with factory() as uow:
    report = import_rows("ter", uow, [ImportRowIn(fields=row["fields"], old=row["old"]) for row in ROWS], ACTOR,
                         moscow_now(), dry_run=dry, source="erp").model_dump()
print("отчёт:", json.dumps(report, ensure_ascii=False, default=str))
if report["created"]:
    raise SystemExit("ОШИБКА: импорт создал бы строки — ключ не найден")
if not dry:
    after = current()
    for row in ROWS:
        have = after[row["fields"]["terArticle"]]
        print("стало:", row["fields"]["terArticle"],
              {name: have.get(name, "") for name in ("terLPlus", "terLMinus", "L+", "L-", "Источник L-")})
print("ИТОГО", json.dumps({"строк": len(ROWS), "обновится" if dry else "обновлено": len(report["updated"]),
                           "отказ": len(report["refused"]), "создано": len(report["created"])}, ensure_ascii=False),
      "(проверка, ничего не записано)" if dry else "(записано)")
