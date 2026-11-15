"""Reviewable equipment naming delta. Dry-run never changes data; apply requires the reviewed JSON.

python -m app.workflows.equipment_names --dry-run --out names.json
python -m app.workflows.equipment_names --apply --reviewed names.json

Only structured, already stored facts are used. No range, function or manufacturer specification is inferred.
"""

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.references.domain import EQUIPMENT_TABS, ReferenceValidationError
from app.modules.references.equipment import CODE_KEY, NAME_KEYS, base_value, display_name, field, name_of
from app.modules.references.equipment_catalogs import CATALOG_TABLES, CATALOG_TYPES
from app.modules.references.infrastructure import (EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow,
                                                   SqlAlchemyTechOperationRepository)
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import SqlAlchemyAuditRepository

ACTOR = Actor(id=uuid5(NAMESPACE_URL, "erp-next:equipment-names"), name="Нормализация названий оборудования", department="ERP")


def fingerprint(record):
    payload = {"fields": record.fields, "model": str(record.equipment_model_id), "tab": record.tab.value,
               "display": record.display_text}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def proposed_name(record):
    """Conservative suggestions: formatting, duplicated model suffix, or structured applicator code."""
    fields = record.fields
    name = " ".join(name_of(fields).split())
    model = field(fields, "Модель") or field(fields, "Артикул")
    if model:
        while name.casefold().endswith(f"{model} {model}".casefold()):
            name = name[:-(len(model) + 1)].rstrip()
    if record.tab.value == "ob_applicators" and (code := field(fields, CODE_KEY)):
        kind = {"M": "ОТП", "A": "для автомата", "S": "для незачищенных проводов"}.get(code[:1].upper(), "")
        series = field(fields, "Серия", "Совместимая серия")
        return f"Аппликатор {kind} {code}".replace("  ", " ").strip() + (f" · {series}" if series else "")
    return name


def plan(session):
    repository = SqlAlchemyTechOperationRepository(session)
    models = {row.id: row for row in session.scalars(select(EquipmentModelRow))}
    operations, templates = {}, {}
    for link in session.scalars(select(EquipmentOperationLinkRow)):
        operations.setdefault(link.model_id, []).append(str(link.operation_id))
    for link in session.scalars(select(EquipmentTemplateToolRow)):
        templates.setdefault(link.model_id, []).append(link.template_id)
    result = []
    for record in repository.list(tab=EQUIPMENT_TABS, query="", limit=100_000):
        name = proposed_name(record)
        display = display_name(record.fields, name)
        if not name or (name == name_of(record.fields) and display == record.display_text):
            continue
        result.append({"unit_id": str(record.id), "model_id": str(record.equipment_model_id),
            "catalog": record.tab.value, "catalog_label": CATALOG_TYPES[record.tab.value],
            "role": models[record.equipment_model_id].role, "current": name_of(record.fields), "proposed": name,
            "current_display": record.display_text, "proposed_display": display,
            "fingerprint": fingerprint(record), "evidence": "Stored passport fields only; no new specifications",
            "confidence": "normalization_only", "operation_ids": sorted(set(operations.get(record.equipment_model_id, []))),
            "template_ids": sorted(set(templates.get(record.equipment_model_id, []))), "approved": False})
    return result


def apply_reviewed(session, entries):
    repository = SqlAlchemyTechOperationRepository(session)
    accepted, seen = [], set()
    for entry in entries:
        if entry.get("approved") is not True:
            continue
        unit_id = UUID(entry["unit_id"])
        if unit_id in seen:
            raise ReferenceValidationError(f"Повтор UUID: {unit_id}")
        seen.add(unit_id)
        record = repository.get(unit_id)
        if record is None or fingerprint(record) != entry["fingerprint"]:
            raise ReferenceValidationError(f"Запись {unit_id} изменилась после проверки списка")
        name = str(entry.get("proposed") or "").strip()
        if not name or len(name) > 512:
            raise ReferenceValidationError(f"Некорректное наименование: {unit_id}")
        aliases = {field(record.fields, key) for key in NAME_KEYS if field(record.fields, key)}
        if len(aliases) > 1:
            raise ReferenceValidationError(f"Разные поля наименования: {unit_id}")
        accepted.append((record, name, entry))
    audit = SqlAlchemyAuditRepository(session)
    for record, name, entry in accepted:
        fields = dict(record.fields)
        for key in NAME_KEYS:
            if key in fields:
                fields[key] = name
        if not any(key in fields for key in NAME_KEYS):
            fields["Наименование"] = name
        fields["Для базы"] = base_value(fields)
        if "baseValue" in fields:
            fields["baseValue"] = fields["Для базы"]
        display = display_name(record.fields, name)
        repository.save(replace(record, fields=fields, display_text=display, edited_in_erp=True,
                                normalized_search=" ".join(f"{display} {' '.join(fields.values())}".casefold().split())))
        audit.add(AuditEvent(uuid4(), record.id, "references.equipment_name_changed", ACTOR, moscow_now(),
                            {"before": name_of(record.fields), "after": name, "before_display": record.display_text,
                             "after_display": display, "model_id": str(record.equipment_model_id),
                             "catalog": record.tab.value, "evidence": str(entry.get("evidence") or ""),
                             "before_fields": dict(record.fields)}))
    return len(accepted)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Названия оборудования: список для проверки и журналируемое применение")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.apply and not args.reviewed:
        parser.error("--apply требует --reviewed со списком, где принятые строки помечены approved=true")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        parser.error("Задайте ERP_DATABASE_URL")
    from app.database import build_engine
    # build_engine imports the persistence mappings without booting the HTTP application.
    engine = build_engine(url)
    try:
        with Session(engine) as session:
            if args.apply:
                if engine.dialect.name == "postgresql":
                    session.execute(text("LOCK TABLE " + ", ".join((*CATALOG_TABLES.values(), "equipment_units",
                        "equipment_models", "equipment_operation_links", "tech_operation_records")) + " IN SHARE ROW EXCLUSIVE MODE"))
                else:
                    session.execute(text("BEGIN IMMEDIATE"))
                count = apply_reviewed(session, json.loads(args.reviewed.read_text(encoding="utf-8-sig")))
                session.commit()
                print(f"Названий записано: {count}")
            else:
                output = json.dumps(plan(session), ensure_ascii=False, indent=2)
                if args.out:
                    args.out.write_text(output + "\n", encoding="utf-8", newline="\n")
                print(output)
    except (ValueError, OSError) as error:
        print(f"Не выполнено: {error}")
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
