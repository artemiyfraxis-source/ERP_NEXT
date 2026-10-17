"""Запись в прод 17.10.2026 — БД.КОАКС: L1–L3 цеха → размеры из документа изготовителя самого разъёма.

Владелец 17.10 на отчёт «11 строк с размерами цеха оставлены»: «нет, документация превыше». Это 11 строк «оставлено:
значения цеха» из журнала «КОАКС — L1-L3 по документам изготовителя 17.10.csv» (PR #522): документ — инструкция
Балтик Сигнал (карточка gsm-repiteri) или Huber+Suhner 16 SMA (chipdip). К «Источнику данных» дописывается сегмент
«L1–L3: по документу …; было по цеху a/b/c» — старые значения остаются в строке и в этом файле (откат).

Правка — `_edit_in`, у каждой ячейки сверяется старое значение; строка ищется по ключу «артикул + кабель» (cable_key).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»

Записано 17.10.2026 20:42: бэкап перед записью /backups/daily/erp_2026-10-17_204159.dump; dry — 11 «проверено», apply — 11
«записано», повторный dry — 11 «уже так». БД.КОАКС 591 строка.
"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "L1–L3 по документу изготовителя вместо значений цеха (владелец 17.10: «документация превыше»)"
BALTIC_N = "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
BALTIC_SMA = "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
S111 = "https://gsm-repiteri.ru/files/2026/09/razem-s-111-instrukciya-po-ustanovke.pdf"
S231 = "https://gsm-repiteri.ru/files/2026/09/razem-s-231-instrukciya-po-ustanovke.pdf"
HS16 = "https://static.chipdip.ru/lib2/a/940/DOC066940338.pdf"
ROWS = [  # артикул, кабель, было (L1, L2, L3), стало, документ
    ("N-111F", "RG-58", ("2", "7,5", "15,5"), ("5", "7", "15"), f"инструкция Балтик Сигнал, обжимной N — {BALTIC_N}"),
    ("S-111L NGT", "RG-174", ("2,5", "4,2", "10,7"), ("2,5", "5,5", "12"), f"инструкция по установке S-111 — {S111}"),
    ("S-111L NGT", "RG-316", ("2,5", "4,2", "10,7"), ("2,5", "5,5", "12"), f"инструкция по установке S-111 — {S111}"),
    ("S-111L", "RG-174", ("2,5", "4,2", "10,7"), ("2,5", "5,5", "12"), f"инструкция Балтик Сигнал, обжимной SMA — {BALTIC_SMA}"),
    ("S-111L", "RG-316", ("2,5", "4,2", "10,7"), ("2,5", "5,5", "12"), f"инструкция Балтик Сигнал, обжимной SMA — {BALTIC_SMA}"),
    ("S-211L", "RG-174", ("2,5", "5", "11,5"), ("2,5", "5,5", "12"), f"инструкция Балтик Сигнал, обжимной SMA — {BALTIC_SMA}"),
    ("S-211L", "RG-316", ("2,5", "5", "11,5"), ("2,5", "5,5", "12"), f"инструкция Балтик Сигнал, обжимной SMA — {BALTIC_SMA}"),
    ("S-231F", "RG-58", ("2,5", "5", "11,5"), ("2,5", "5,5", "12"), f"инструкция Балтик Сигнал, обжимной SMA — {BALTIC_SMA}"),
    ("S-231L NGT", "RG-174", ("2,5", "5", "11,5"), ("2,5", "5,5", "12"), f"инструкция по установке S-231 — {S231}"),
    ("S-231L NGT", "RG-316", ("2,5", "5", "11,5"), ("2,5", "5,5", "12"), f"инструкция по установке S-231 — {S231}"),
    ("16_SMA-50-2-106/111_NE", "RG-316D", ("3,5", "5,5", "10"), ("2", "6", "11"), f"Huber+Suhner 16_SMA-50-2-106/111_NE — {HS16}"),
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def key(article, cable):
    return str(article or "").strip().casefold(), cable_key(cable)


with factory() as uow:
    coax = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
pairs = {}
for record in coax:
    pairs.setdefault(key(record.fields.get("coaxArticle"), record.fields.get("coaxWire")), []).append(record)
print("строк БД.КОАКС:", len(coax))

counts = {}
for article, cable, old, new, doc in ROWS:
    label = f"«{article}» × «{cable}»"
    found = pairs.get(key(article, cable), [])
    if len(found) != 1:
        status = f"отказ (строк {len(found)})"
    else:
        record = found[0]
        have = tuple(str(record.fields.get(field) or "").strip() for field in ("L1", "L2", "L3"))
        if have == new:
            status = "уже так"
        else:
            source = str(record.fields.get("Источник данных") or "").strip()
            segment = f"L1–L3: по документу изготовителя — {doc}; было по цеху {'/'.join(old)} (17.10.2026)"
            edits = [FieldEdit(field, was, value) for field, was, value in zip(("L1", "L2", "L3"), old, new) if was != value]
            if "Источник данных" in record.fields:
                edits.append(FieldEdit("Источник данных", source, f"{source}; {segment}" if source else segment))
            with factory() as uow:
                try:
                    before, _ = _edit_in(record.id, tuple(edits), now, uow)
                    _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
                           "fields": [edit.field for edit in edits], "reason": REASON}, uow)
                    if not dry:
                        uow.commit()
                    status = "проверено" if dry else "записано"
                except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                    status = f"отказ ({error})"
    counts[status.split(" (")[0]] = counts.get(status.split(" (")[0], 0) + 1
    print(f"   {status}: {label} {'/'.join(old)} → {'/'.join(new)}")
print("ИТОГО", counts, "(проверка, ничего не записано)" if dry else "(записано)")
