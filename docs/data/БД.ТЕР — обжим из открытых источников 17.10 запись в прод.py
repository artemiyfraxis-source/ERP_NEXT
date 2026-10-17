"""Запись в прод 17.10.2026 — БД.ТЕР «Высота обжима проводника , мм»: 91 пустое сечение у 29 контактов по документам
производителей (TE 114-16017, 114-1012, 114-1021, 114-5245, 114-10000, чертёж 1355064; справочник JST по инструменту).
Данные и ссылки — «БД.ТЕР — обжим из открытых источников 17.10.csv»; как документы достаются без регистрации —
«БД.ТЕР — где брать спецификации без регистрации 17.10.md».

Сечение дописывается в ячейку к уже стоящим («28 AWG: …; 26 AWG: …»), порядок — от тонкого к толстому, как в формате 16.10.
Путь — `_edit_in`: старое значение ячейки сверяется (поменяли — отказ); уже стоящее сечение не перезаписывается.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → 0 к записи
"""
import csv
import io
import os
import re
import sys
from collections import defaultdict
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "высота обжима по жиле по документу производителя (открытые источники 17.10.2026)"
FIELD = "Высота обжима проводника , мм"
CSV_TEXT = r'''@@CSV@@'''


def parse(cell: str) -> dict[int, str]:
    out = {}
    for part in filter(None, (piece.strip() for piece in cell.split(";"))):
        match = re.match(r"(\d+)\s*AWG:\s*(.+)$", part)
        if not match:
            raise ValueError(f"не формат «N AWG: значение»: «{part}»")
        out[int(match.group(1))] = match.group(2).strip()
    return out


def render(values: dict[int, str]) -> str:
    return "; ".join(f"{awg} AWG: {values[awg]}" for awg in sorted(values, reverse=True))


mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
found: dict[str, dict[int, str]] = defaultdict(dict)
names: dict[str, str] = {}
for row in csv.DictReader(io.StringIO(CSV_TEXT.lstrip("﻿"))):
    found[row["id"]][int(row["AWG"].split()[0])] = row["Значение"].strip()
    names[row["id"]] = row["Артикул"]
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
written = same = refused = 0
for record_id, add in found.items():
    with factory() as uow:
        record = uow.tech_operations.get(UUID(record_id))
        if record is None:
            refused += 1
            print(f"   нет записи: {names[record_id]}")
            continue
        old = str(record.fields.get(FIELD, "")).strip()
        try:
            have = parse(old)
        except ValueError as error:
            refused += 1
            print(f"   отказ: {names[record_id]} ({error})")
            continue
        new_values = {**have, **{awg: value for awg, value in add.items() if awg not in have}}
        clash = {awg: (have[awg], value) for awg, value in add.items() if awg in have and have[awg] != value}
        if clash:
            print(f"   уже стоит другое (не трогаю): {names[record_id]} {clash}")
        new = render(new_values)
        if new == old:
            same += 1
            continue
        try:
            before, _ = _edit_in(record.id, (FieldEdit(FIELD, old, new),), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: {names[record_id]} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [FIELD], "reason": REASON,
        }, uow)
        if mode == "apply":
            uow.commit()
        written += 1
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {names[record_id]}: «{old}» → «{new}»")
print(f"ИТОГО контактов {len(found)}: {'проверено' if mode == 'dry' else 'записано'} {written}, уже так {same}, отказ {refused}")
