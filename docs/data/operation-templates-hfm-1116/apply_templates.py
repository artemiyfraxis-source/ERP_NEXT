"""HFM support-sleeve crimp card after full machine stripping, in one transaction.

    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_templates.py payload.json [--apply]

Dry-run is the default: cards, reference rows, model links, audit and refresh requests
all roll back. Human edits are reported and kept. No saved ETK inputs, documents or
revisions are changed; regeneration is a separate operation.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.application import create_operation_row_in, operation_code
from app.modules.references.domain import TechOperationTab
from app.modules.references.equipment import INV_KEY, field, inv_key
from app.modules.references.equipment_identity import unit_base
from app.modules.references.infrastructure import (
    EquipmentModelRow, EquipmentOperationLinkRow, EquipmentRecordRow, EquipmentTemplateToolRow,
)
from app.modules.references.technology import schema_columns, sync_schema_fields
from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR, create_operation_templates_in, new_operation
from app.modules.techcards.infrastructure import (
    OperationCardSnapshotRow, OperationCardTemplateRow, RefreshRequestRow,
    SqlAlchemyTechcardsUnitOfWork,
)
from app.modules.techcards.operation_cards import normalize_record_edit
from app.modules.techcards.template_sheets import make_template_id
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows import etk_refresh  # noqa: F401 - enqueue inside this transaction
from app.workflows.new_operation import LIBRARY_AGGREGATE, NewOperationUnitOfWork

AUTHOR = "HFM — опорная гильза 16.11.2026"
REASON = "Обжим опорной гильзы HFM после полной разделки; нормы времени не заданы"
TRUSTED_AUTHORS = {AUTHOR, ERP_TEMPLATE_AUTHOR, "FAKRA 708К. — карты 14.11.2026"}
HFM_CODES = {"PRS_HFM_SUPPORT"}
OUTCOMES = ("created", "changed", "unchanged", "protected", "rows_created", "rows_kept", "equipment_linked")
EQUIPMENT_COLUMNS = ("Машина", "Инструмент", "Апликаторы / модули")


def actor() -> Actor:
    return Actor(id=UUID(int=0), name=AUTHOR, department="Технолог")


def _event(uow, who, now, event_type, details, aggregate=LIBRARY_AGGREGATE):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=aggregate, event_type=event_type,
                             actor=who, occurred_at=now, details=details))


def _enqueue(uow, now, reason, payload):
    # Flush first so the standard template/reference listener can supply the same request.
    uow.session.flush()
    pending = uow.session.scalar(select(RefreshRequestRow.id).where(
        RefreshRequestRow.reason == reason, RefreshRequestRow.processed_at.is_(None)).limit(1))
    if pending is None:
        uow.session.add(RefreshRequestRow(id=uuid4(), reason=reason, payload=payload, created_at=now))


def _protected(rows) -> bool:
    # A record edit applies to all A/B variants; even one human-edited sibling protects the code.
    return any((row.saved_by and row.saved_by not in TRUSTED_AUTHORS)
               or (row.edited_by and row.edited_by not in TRUSTED_AUTHORS)
               or (row.saved_at and not row.saved_by) or (row.edited_at and not row.edited_by)
               for row in rows)


def _rows(uow):
    records = uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
    result = {}
    for row in records:
        code = operation_code(row.fields)
        if code in result:
            raise ValueError(f"Неоднозначный код БД.ОП: {code}")
        result[code] = row
    return result


def _save_fields(uow, row, changes):
    fields = dict(row.fields)
    columns = schema_columns("op", fields)
    # Canonical identities/norms win even when an imported analogue stores the
    # canonical aliases before the display columns in its JSON object.
    for key in sorted(changes, key=lambda name: name in columns):
        value = changes[key]
        fields[columns.get(key, key)] = str(value)
        if key in columns:
            fields[key] = str(value)
    fields = sync_schema_fields("op", fields)
    if fields != row.fields:
        uow.tech_operations.save(replace(row, fields=fields, edited_in_erp=True))


def _inventory(uow, number):
    # Live units only: the six БД.ОБ catalogs (0180). Frozen tech_operation_records tab='ob' rows are history.
    units = [row for row in uow.session.scalars(select(EquipmentRecordRow))
             if inv_key(field(row.fields, INV_KEY, "inventoryNumber")) == inv_key(number)]
    if len(units) != 1:
        raise ValueError(f"Инв. номер {number}: нужно ровно одно оборудование, найдено {len(units)}")
    unit = units[0]
    model = uow.session.get(EquipmentModelRow, unit.equipment_model_id)
    if model is None or model.role != "operational":
        raise ValueError(f"Инв. номер {number}: нет операционной модели оборудования")
    return unit


def _equipment(uow, code, edit, templates, op_row, result):
    """Only explicit inventory numbers produce links; manufacturer requirements remain unlinked text."""
    resolved = [(index, tool, _inventory(uow, tool.inventory_number))
                for index, tool in enumerate(edit.tooling) if tool.inventory_number.strip()]
    desired = {column: [] for column in EQUIPMENT_COLUMNS}
    for index, tool, unit in resolved:
        column = {"Оборудование": "Машина", "Инструмент": "Инструмент", "Матрицы": "Апликаторы / модули"}.get(tool.section)
        if column and all(old.equipment_model_id != unit.equipment_model_id for old in desired[column]):
            desired[column].append(unit)
        if tool.section != "Оборудование":
            continue
        for template in templates:
            key = (template.id, index)
            row = uow.session.get(EquipmentTemplateToolRow, key)
            values = dict(tool_name=tool.name, inventory_number=tool.inventory_number,
                          model_id=unit.equipment_model_id, candidates=1)
            if row is None:
                uow.session.add(EquipmentTemplateToolRow(template_id=template.id, position=index, **values))
                result["equipment_linked"].append(f"{code}: {tool.inventory_number}")
            elif any(getattr(row, name) != value for name, value in values.items()):
                raise ValueError(f"Существующая привязка оснастки изменена: {code}, {index}")
    for column, units in desired.items():
        expected_text = "; ".join(unit_base(unit.fields, unit.display_text) for unit in units)
        if str(op_row.fields.get(column) or "") != expected_text:
            raise ValueError(f"Существующая ячейка БД.ОП изменена: {code}, {column}")
        for position, unit in enumerate(units):
            # Positions follow equipment_identity.pieces: semicolon separators occupy odd indexes.
            key = (op_row.id, column, position * 2)
            row = uow.session.get(EquipmentOperationLinkRow, key)
            if row is None:
                uow.session.add(EquipmentOperationLinkRow(operation_id=op_row.id, op_column=column,
                                                          position=position * 2, model_id=unit.equipment_model_id))
                result["equipment_linked"].append(f"{code}: БД.ОП {column}, {position * 2}")
            elif row.model_id != unit.equipment_model_id:
                raise ValueError(f"Существующая привязка БД.ОП изменена: {code}, {column}")
    expected_links = {(column, position * 2): unit.equipment_model_id
                      for column, units in desired.items() for position, unit in enumerate(units)}
    for link in uow.session.scalars(select(EquipmentOperationLinkRow).where(EquipmentOperationLinkRow.operation_id == op_row.id)):
        if expected_links.get((link.op_column, link.position)) != link.model_id:
            raise ValueError(f"Лишняя привязка БД.ОП: {code}, {link.op_column}")
    desired_positions = {index for index, tool, _ in resolved if tool.section == "Оборудование"}
    for link in uow.session.scalars(select(EquipmentTemplateToolRow).where(
            EquipmentTemplateToolRow.template_id.in_([row.id for row in templates]))):
        if link.position not in desired_positions:
            raise ValueError(f"Лишняя привязка оснастки: {code}, {link.position}")


def apply_payload(payload, uow, *, who, now):
    """Open UoW only; never commits. A caller may inspect the report and roll everything back."""
    result = {key: [] for key in OUTCOMES}
    if len({item["code"] for item in payload}) != len(payload):
        raise ValueError("Повтор кода в payload")
    for item in payload:
        code = item["code"]
        if code not in HFM_CODES:
            raise ValueError(f"Код вне поставки HFM: {code}")
        edit = normalize_record_edit(item["record"])
        found = list(uow.session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == code)))
        if _protected(found):
            result["protected"].append(code)
            continue
        records = _rows(uow)
        spec = item["create"]["row"]
        created = not found
        if created:
            expected_id = make_template_id(f"{code} | {edit.name}", ())
            if uow.session.get(OperationCardTemplateRow, expected_id) is not None:
                raise ValueError(f"ID занят другой картой: {expected_id}")
            create_operation_templates_in(new_operation(code, edit.name, "", item["create"].get("category", "")),
                                          who, now, uow)
            found = list(uow.session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == code)))
        current = [uow.operation_cards.get(row.id) for row in found]
        same = all((card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) ==
                   (edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling) for card in current)
        if not same:
            uow.operation_cards.edit(code, edit, changed_at=now, changed_by=who.name)
        if code not in records:
            copy_of = spec.get("copy_of", "")
            if copy_of and copy_of not in records:
                raise ValueError(f"Нет строки-аналога БД.ОП: {copy_of}")
            row = create_operation_row_in(code, spec["name"], copy_of, uow)
            # Only the analogue's column layout is reusable. Its programs, times,
            # formulas, machine, die and other process settings are not HFM support-crimp data.
            changes = {**dict.fromkeys(row.fields, ""), "opNumber": code, "opName": spec["name"],
                       **spec.get("norms", {}), **dict.fromkeys(EQUIPMENT_COLUMNS, "")}
            resolved = {}
            for tool in edit.tooling:
                if tool.inventory_number.strip():
                    unit = _inventory(uow, tool.inventory_number)
                    column = {"Оборудование": "Машина", "Инструмент": "Инструмент", "Матрицы": "Апликаторы / модули"}.get(tool.section)
                    if column:
                        resolved.setdefault(column, {})[unit.equipment_model_id] = unit_base(unit.fields, unit.display_text)
            changes.update({column: "; ".join(units.values()) for column, units in resolved.items()})
            _save_fields(uow, row, changes)
            result["rows_created"].append(code)
            records = _rows(uow)
        else:
            # Existing reference names, norms and programs may contain human edits.
            result["rows_kept"].append(code)
        links_before = len(result["equipment_linked"])
        _equipment(uow, code, edit, found, records[code], result)
        outcome = "created" if created else "unchanged" if same else "changed"
        result[outcome].append(code)
        if outcome != "unchanged" or code in result["rows_created"] or len(result["equipment_linked"]) != links_before:
            _event(uow, who, now, "techcards.hfm_template_applied", {"code": code, "outcome": outcome, "reason": REASON})
    if result["created"]:
        count = len(uow.operation_cards.sheets())
        snapshot = uow.session.get(OperationCardSnapshotRow, "current")
        if snapshot is None:
            uow.session.add(OperationCardSnapshotRow(name="current", source_commit="", imported_at=now, template_count=count))
        else:
            snapshot.template_count = count
    if result["equipment_linked"]:
        _enqueue(uow, now, etk_refresh.REASON_REFERENCE, {"reason": REASON, "equipment": result["equipment_linked"]})
    uow.session.flush()
    return result


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    sessions = sessionmaker(bind=build_engine(os.environ["ERP_DATABASE_URL"]), expire_on_commit=False, class_=Session)
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        result = apply_payload(payload, uow, who=actor(), now=moscow_now())
        for key, values in result.items():
            print(f"{key}: {len(values)}")
            if key != "unchanged":
                for value in values:
                    print(f"  {value}")
        if args.apply:
            uow.commit()
            changed = any(result[key] for key in ("created", "changed", "rows_created", "equipment_linked"))
            print("COMMITTED — карты и связи сохранены; перегенерация ЭТК выполняется отдельно" if changed
                  else "COMMITTED — изменений нет")
        else:
            print("dry run — rolled back (включая журнал и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
