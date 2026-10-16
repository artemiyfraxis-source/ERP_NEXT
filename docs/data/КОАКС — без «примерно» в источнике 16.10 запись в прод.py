"""Запись в прод 16.10.2026 — БД.КОАКС «Источник данных»: убрать слово «примерно» (владелец 16.10: «"примерно" не надо,
будем на деле проверять»). Источник остаётся: «L1–L3 примерно: по X» → «L1–L3: по X». Значения L1–L3 не меняются.

Путь — `_edit_in`, как правка строки в «Базе данных»: старое значение сверяется (изменилось — отказ). Журнал «было → стало»
печатается целиком (откат — обратной заменой по нему).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → 0 к правке
"""
import os
import re
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 16.10.2026", department="System")
REASON = "убрано «примерно» из источника (владелец 16.10: «будем на деле проверять»)"
FIELD = "Источник данных"


def clean(text: str) -> str:
    out = re.sub(r"\s*\(?примерно\)?\s*:", ":", text, flags=re.I)
    out = re.sub(r"\s*,?\s*примерно\b", "", out, flags=re.I)
    return re.sub(r"\s{2,}", " ", out).strip()


mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
with factory() as uow:
    todo = [(r.id, r.display_text, str(r.fields.get(FIELD, ""))) for r in
            uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
            if re.search(r"примерн", str(r.fields.get(FIELD, "")), re.I)]
done = refused = 0
for record_id, name, old in todo:
    new = clean(old)
    with factory() as uow:
        try:
            before, _ = _edit_in(record_id, (FieldEdit(FIELD, old, new),), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: «{name}» ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [FIELD], "reason": REASON,
        }, uow)
        if mode == "apply":
            uow.commit()
        done += 1
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: «{name}»: «{old}» → «{new}»")
print(f"ИТОГО к правке {len(todo)}: {'проверено' if mode == 'dry' else 'записано'} {done}, отказ {refused}")
