"""Запись в прод 17.10.2026 — БД.КОАКС: пол разъёма в «Тип/Серия» по карточкам amel.ru (расхождения из
«Коакс — ссылки и цены 17.10.md»; владелец 17.10: «закрывай все открытые задачи»).

- QMA-J240W / LMR-240: amel.ru «вилки кабельные» → QMA-male (было QMA-female).
- SMB-KW3 / RG-174 и RG-316: amel.ru «вилка кабельная угловая» → SMB-male R/A (было SMB-female R/A); в той же
  базе SMB-JB2 АМЭЛ («розетки кабельные») стоит SMB-female — K = вилка, J = розетка, как у АМЭЛ.
- «SMA-Male RG402 18GHz»: название и ссылка (АМЭЛ SMA-JB3, вилка) — male, тип стоял SMA-female → SMA-male.
- QMA-KPD7: amel.ru «розетка приборная прямая», «Применяемый кабель: -» → тип QMA → QMA-female. Пара с 5D-FB
  не тронута (кабель у АМЭЛ не указан) — вопрос владельцу.

Правка — `_edit_in` со сверкой старого значения: колонка «Тип/Серия», схема (coaxType) идёт следом.

  ... python - dry < этот файл ; erp.sh backup-now ; ... python - apply < этот файл ; ... python - dry

Записано 17.10.2026 14:26, бэкап перед записью erp_2026-10-17_142542.dump; повторный dry — «уже так» у всех 5.
"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "пол коаксиального разъёма по карточке amel.ru (17.10.2026)"
FIELD = "Тип/Серия"
EDITS = {  # id: (артикул / кабель, было, стало)
    "6be41117-6bc0-4bdb-9ef7-18b71ad90fec": ("QMA-J240W / LMR-240", "QMA-female", "QMA-male"),
    "4438d4ff-ac8b-47af-a3a6-1538b8d21fba": ("SMB-KW3 / RG-174", "SMB-female R/A", "SMB-male R/A"),
    "4387b277-ffe5-4556-a884-08faa3176d27": ("SMB-KW3 / RG-316", "SMB-female R/A", "SMB-male R/A"),
    "10b1ab10-f3c3-424a-8eb0-d5d1ab62fb1a": ("SMA-Male RG402 18GHz / RG-402", "SMA-female", "SMA-male"),
    "d606c2d9-658e-4e72-9ff3-e8c4704f46b0": ("QMA-KPD7 / 5D-FB", "QMA", "QMA-female"),
}

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()

for record_id, (label, old, new) in EDITS.items():
    with factory() as uow:
        record = uow.tech_operations.get(UUID(record_id))
        if str(record.fields.get(FIELD, "")).strip() == new:
            print(f"   уже так: {label}")
            continue
        try:
            before, after = _edit_in(record.id, (FieldEdit(FIELD, old, new),), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            print(f"   отказ: {label} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
               "fields": [FIELD], "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {label} {old} → {new}"
              f" (coaxType {before.fields.get('coaxType', '—')} → {after.fields.get('coaxType', '—')})")
