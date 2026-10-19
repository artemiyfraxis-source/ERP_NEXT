"""Запись в прод 20.10.2026 — «Заглубление контакта, мм» там, где документы дают только одну сторону (владелец 20.10: «MMCX вилка
— 0,00…0,25; односторонние пределы писать как есть; UHF — номинал»). Расстояние — от торца корпуса до контакта, мм.

  MMCX вилка   «0,00…0,25»      Huber+Suhner, лист MMCX: C = 0,00…0,25 — кончик штыря утоплен от переднего торца экрана.
  MMCX розетка «не более 1,75»  H+S: торец → REF  B ≤ 2,65; контакт → REF  C 0,90…1,20; контакт между торцом и REF, глубина
                                = B − C ≤ 2,65 − 0,90 = 1,75 (минимума у B в листе нет).
  FME вилка    «не менее 2,50»  radiolab FME, вилка: C ≥ 2,50 (максимум не задан); Powertec: f ≥ 2,50.
  SMB вилка    «не более 2,57»  MIL-STD-348B рис. 311-2 (гнездовой контакт): торец → REF  F ≤ 1,63; REF → контакт назад
                                D 0,18…0,94; глубина = F + D ≤ 1,63 + 0,94 = 2,57 (минимума у F нет).
  UHF вилка    «9,14 (номинал)» radiolab UHF, вилка: размер 9,14 мм (.36″) от уступа до кончика штыря, допуск не дан.
Не пишутся (документы не позволяют вывести): FME розетка, UHF розетка, угловые, mini-UHF.

Берутся прямые строки по типу: MMCX-male / MMCX-female / FME-male / SMB-male / UHF-male (угловые «R/A» и строки с
«Исполнение» = «угловой» пропускаются). Заполненная ячейка возвращается отказом (old = «»).
Режимы: dry | apply | csv (печатает журнал для отката).
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → всё «без изменений»
Откат: очистить две ячейки у строк из журнала (ключ — артикул × провод) — docs/data/«Заглубление контакта MMCX, FME, SMB, UHF 20.10.csv»."""
import csv
import os
import re
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Заглубление контакта MMCX FME SMB UHF 20.10.2026", department="System")
DEPTH, SOURCE = "Заглубление контакта, мм", "Заглубление контакта, источник"
HS = "https://static.mercateo.com/56/0ba581d7d9db4b0489666a9ef094c411/pdf/106768.pdf"
STD = ("https://web.archive.org/web/2026id_/https://everyspec.com/MIL-STD/MIL-STD-0300-0499/download.php?"
       "spec=MIL-STD-348B.051541.pdf")
VALUES = {
    "MMCX-male": ("0,00…0,25",
                  "Huber+Suhner, лист MMCX (интерфейсная таблица), вилка: C = 0,00…0,25 мм — кончик штыря утоплен от переднего "
                  "торца экрана (корпуса); по решению владельца 20.10 это и есть «от торца корпуса до контакта»; "
                  f"{HS}; 20.10.2026"),
    "MMCX-female": ("не более 1,75",
                    "Huber+Suhner, лист MMCX, розетка: торец корпуса → REF  B ≤ 2,65 мм, контакт → REF  C = 0,90…1,20 мм, "
                    "контакт лежит между торцом и REF, поэтому глубина = B − C ≤ 2,65 − 0,90 = 1,75 мм (минимума у B в листе "
                    "нет — только предел); Amphenol Connex и TE 1307191 пишут для B «2,6 min» — расхождение, принят лист H+S; "
                    f"{HS}; 20.10.2026"),
    "FME-male": ("не менее 2,50",
                 "radiolab.ru, FME, вилка: C ≥ 2,50 мм (мин. 2.50/.098, максимум не задан); по решению владельца 20.10 C = "
                 "расстояние от торца корпуса до контакта; Powertec FME plug f ≥ 2,50 совпадает; "
                 "https://www.radiolab.ru/ru/attach_file/fmeradiolab.pdf; 20.10.2026"),
    "SMB-male": ("не более 2,57",
                 "MIL-STD-348B рис. 311-2 (SMB, гнездовой контакт — «вилка» ERP): торец корпуса → REF  F ≤ 1,63 мм; REF → "
                 "передний край контакта (назад)  D = 0,18…0,94 мм; глубина = F + D ≤ 1,63 + 0,94 = 2,57 мм (минимума у F "
                 f"нет — только предел); {STD}; 20.10.2026"),
    "UHF-male": ("9,14 (номинал)",
                 "radiolab.ru, UHF, вилка (PL-259): размер 9,14 мм (.36″) от уступа до кончика штыря, допуск в чертеже не дан — "
                 "только номинал; https://www.radiolab.ru/ru/attach_file/uhfradiolab.pdf; 20.10.2026"),
}

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply", "csv"):
    raise SystemExit("режим: dry | apply | csv")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
with factory() as uow:
    records = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)

rows, skipped = [], 0
for record in records:
    fields = record.fields
    kind = str(fields.get("Тип/Серия") or fields.get("coaxType") or "").strip()
    if kind not in VALUES or "угл" in str(fields.get("Исполнение") or "").casefold():
        skipped += 1
        continue
    value, source = VALUES[kind]
    rows.append({"fields": {"coaxArticle": str(fields.get("Артикул") or fields.get("coaxArticle") or "").strip(),
                            "coaxWire": str(fields.get("Провод") or fields.get("coaxWire") or "").strip(),
                            DEPTH: value, SOURCE: source},
                 "old": {DEPTH: "", SOURCE: ""}, "kind": kind,
                 "had": str(fields.get(DEPTH) or "")})

if mode == "csv":
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(["артикул", "провод", "тип", "поле", "было", "стало"])
    for row in rows:
        f = row["fields"]
        for name in (DEPTH, SOURCE):
            writer.writerow([f["coaxArticle"], f["coaxWire"], row["kind"], name, "", f[name]])
    raise SystemExit(0)

print(f"БД.КОАКС: строк {len(records)}, к записи {len(rows)}, пропущено {skipped}")
for row in rows:
    f = row["fields"]
    print(f"   {f['coaxArticle']} × {f['coaxWire']} | {row['kind']} | {f[DEPTH]}" + (f" | уже: {row['had']}" if row["had"] else ""))


def call(dry_run: bool):
    return import_rows("coax", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in rows], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
if data["created"]:
    raise SystemExit(f"СТОП — строки не найдены, ничего не записано: {data['created']}")
if mode == "apply":
    data = call(False)
print(f"== import_rows(coax): строк {len(rows)} → создано {len(data['created'])}, обновлено {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"]:
    print("   отказ:", item)
