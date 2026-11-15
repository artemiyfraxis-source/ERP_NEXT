"""Dedicated FAKRA/HFM cards and saved 708К. generator bindings, in one transaction.

    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_templates.py payload.json [--apply]

Without --apply ALL writes, including audit, snapshots and refresh requests, roll back.
Human-edited cards and ETKs are reported and kept. The existing ETK refresh worker
issues revisions from the migrated inputs after commit; this script never edits a
published document or a revision. No equipment is inferred from an analogue row.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.application import create_operation_row_in, operation_code
from app.modules.references.domain import TechOperationTab
from app.modules.references.equipment import INV_KEY, field, inv_key
from app.modules.references.equipment_identity import unit_base
from app.modules.references.infrastructure import (
    EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow, TechOperationRecordRow,
)
from app.modules.references.technology import schema_columns, sync_schema_fields
from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR, create_operation_templates_in, new_operation
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_derive import route_override
from app.modules.techcards.infrastructure import (
    OperationCardSnapshotRow, OperationCardTemplateRow, ProjectTechcardRow, RefreshRequestRow,
    SqlAlchemyTechcardsUnitOfWork,
)
from app.modules.techcards.operation_cards import normalize_record_edit
from app.modules.techcards.project_cards import operation_key
from app.modules.techcards.template_sheets import make_template_id
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number
from app.workflows import etk_refresh  # noqa: F401 - enqueue inside this transaction
from app.workflows.new_operation import LIBRARY_AGGREGATE, NewOperationUnitOfWork

AUTHOR = "FAKRA 708К. — карты 14.11.2026"
REASON = "Специализированные карты FAKRA/HFM; нормы времени не заданы"
PROJECT = normalize_project_number("708К.")
TRUSTED_AUTHORS = {AUTHOR, ERP_TEMPLATE_AUTHOR}
OUTCOMES = ("created", "changed", "unchanged", "protected", "rows_created", "equipment_linked",
            "etk_changed", "etk_kept", "etk_protected")

# Exact part identity and RTK 031 are required, never just the word FAKRA.
ENDS = {"straight": "59K13O-1M4A4", "angled": "59K25H-1M4A4",
        "hfm": "AMK11D-1M4Z5-Z", "bulkhead": "2FA1-NZSP-C30E2"}
STAGES = {
    "straight": {"coaxInsSleeve": "INS_FAKRA_ST_PREP", "coaxSideStrip": "STRIP_FAKRA_ST",
                 "coaxPinCrimp": "PRS_FAKRA_ST_PIN", "coaxInsert": "INS_FAKRA_ST_CONTACT",
                 "coaxShield": "PRS_FAKRA_ST_SHIELD", "coaxBody": "INS_FAKRA_ST_HOUSING",
                 "coaxSealCap": "INS_FAKRA_ST_SEAL"},
    "angled": {"coaxInsSleeve": "INS_FAKRA_RA_PREP", "coaxSideStrip": "STRIP_FAKRA_RA",
               "coaxPinCrimp": "PRS_FAKRA_RA_PIN", "coaxInsert": "INS_FAKRA_RA_CONTACT",
               "coaxShield": "PRS_FAKRA_RA_SHIELD", "coaxCapCrimp": "PRS_FAKRA_RA_CAP",
               "coaxBody": "INS_FAKRA_RA_HOUSING"},
    "hfm": {"coaxInsSleeve": "INS_HFM_PREP", "coaxSideStrip": "STRIP_HFM", "coaxPinCrimp": "PRS_HFM_PIN",
            "coaxInsert": "INS_HFM_CONTACT", "coaxShield": "PRS_HFM_SHIELD", "coaxBody": "INS_HFM_HOUSING"},
    "bulkhead": {"coaxInsSleeve": "INS_FAKRA_BH_PREP", "coaxSideStrip": "STRIP_FAKRA_BH",
                 "coaxPin": "SDR_FAKRA_BH_PIN", "coaxInsert": "INS_FAKRA_BH_CONTACT",
                 "coaxShield": "PRS_FAKRA_BH_SHIELD", "coaxHeatTtk": "HEAT_FAKRA_BH_TTK"},
}
LEGACY = {
    "coaxInsSleeve": ("coaxInsSleeve", {"INS_SLEEVE_coax"}),
    "coaxSideStrip": ("coaxStrip", {"STRIP_COAX_auto", "STRIP_COAX_hand"}),
    "coaxPinCrimp": ("coaxPinCrimp", {"PRS_SLEEVE_coax", "PRS_PIN_coax"}),
    "coaxPin": ("coaxPin", {"SDR_COAX_pin"}),
    "coaxInsert": ("coaxHousing", {"INS_housing_COAX"}),
    "coaxShield": ("coaxShield", {"PRS_SLEEVE_coax"}),
    "coaxBody": ("coaxHousing", {"INS_housing_COAX"}),
    "coaxCapCrimp": ("coaxCapCrimp", {"PRS_CAP", "PRS_CAP_COAX"}),
    "coaxSealCap": ("coaxCapScrew", {"INS_CAP"}),
    "coaxHeatTtk": ("coaxHeat", {"HEAT_TTK_short"}),
}
STALE_EQUIPMENT = ("machine", "equipment", "equipmentTools", "equipment_model_id", "applicator")
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
    units = [row for row in uow.session.scalars(select(TechOperationRecordRow).where(
        TechOperationRecordRow.tab == "ob")) if inv_key(field(row.fields, INV_KEY, "inventoryNumber")) == inv_key(number)]
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


def _normal(value):
    return re.sub(r"[\s._–—-]+", "", str(value or "")).casefold()


def migrate_config(config, templates, allowed):
    """Return a copy and changed opKeys. Unknown/custom bindings keep the complete ETK unchanged."""
    candidate = deepcopy(config)
    coax = candidate.get("coax") or {}
    if _normal(coax.get("cableArt")) != "rtk031":
        return candidate, [], "другой кабель или нет артикула"
    by_id = {card.source_id: card for card in templates}
    by_code = {}
    for card in templates:
        by_code.setdefault(card.code, card)
    changed = []
    for op in candidate.get("ops") or []:
        side = op.get("side")
        if side not in {"A", "B"}:
            continue
        end = coax.get("side" + side) or {}
        kind = end.get("kind")
        if kind not in ENDS or _normal(end.get("article")) != _normal(ENDS[kind]):
            continue
        op_key = str(op.get("opKey") or "")
        stage = op_key[:-1] if op_key.endswith(side) else ""
        target = STAGES[kind].get(stage)
        if not target:
            continue
        if target not in allowed:
            return deepcopy(config), [], f"карта защищена или отсутствует: {target}"
        expected_type, generic = LEGACY[stage]
        source = by_id.get(str(op.get("templateId") or ""))
        current = source.code if source else ""
        if op.get("type") != expected_type or current not in generic | {target} \
                or str(op.get("opNum") or "") not in generic | {target, ""}:
            return deepcopy(config), [], f"своя привязка: {op_key}"
        desired = by_code[target]
        before = deepcopy(op)
        op.update(opNum=target, templateId=desired.source_id)
        if target == "INS_HFM_PREP" and str(op.get("label") or "").startswith("Надеть опорные гильзы"):
            op["label"] = op["label"].replace("Надеть опорные гильзы", "Подготовить опорные гильзы", 1)
        if target == "INS_FAKRA_ST_PREP" and str(op.get("label") or "").startswith("Надеть крышку, уплотнитель и гильзу"):
            op["label"] = op["label"].replace("Надеть крышку, уплотнитель и гильзу",
                                             "Подготовить гильзу, уплотнитель и крышку", 1)
        if current != target:
            # The dedicated process has no measured time norm; generic durations are not evidence.
            op.update(tOp="", tPrep="", tMachine="")
            op.pop("linearTiming", None)
        if target == "INS_FAKRA_ST_SEAL":
            op["noTooling"] = True
        else:
            op.pop("noTooling", None)
        for key in STALE_EQUIPMENT:
            op.pop(key, None)
        if op != before:
            changed.append(op_key)
    return candidate, changed, "без специализированных изменений"


def _migrate_etks(uow, allowed, result, who, now):
    templates = tuple(uow.operation_cards.list())
    # Legacy rows may lack project_key. Read their small identity projection, then
    # lock only this project's cards, never the entire ERP techcard table.
    ids = [row.id for row in uow.session.execute(select(ProjectTechcardRow.id, ProjectTechcardRow.project))
           if normalize_project_number(row.project) == PROJECT]
    rows = uow.session.scalars(select(ProjectTechcardRow).where(ProjectTechcardRow.id.in_(ids))
                              .order_by(ProjectTechcardRow.item_index).with_for_update())
    for row in rows:
        if normalize_project_number(row.project) != PROJECT:
            continue
        label = f"{row.item_index} [{row.id}]"
        if not isinstance(row.inputs, dict):
            result["etk_kept"].append(f"{label}: нет inputs")
            continue
        inputs, changed, reason = migrate_config(row.inputs, templates, allowed)
        if not changed:
            result["etk_kept"].append(f"{label}: {reason}")
            continue
        # A hand edit may rely on a legacy code or a template version. Leave it for the technologist.
        if row.manual_operation_keys or (row.overrides or {}).get("operations"):
            result["etk_protected"].append(f"{label}: ручные правки операций")
            continue
        overrides = deepcopy(row.overrides)
        route = (overrides or {}).get("route") or route_override(row.document)
        if route:
            stored_ops = (row.document or {}).get("operations") or []
            old_ops = build_etk(row.inputs, templates)["operations"]
            new_ops = build_etk(inputs, templates)["operations"]
            old_keys = [operation_key(op) for op in old_ops]
            new_keys = [operation_key(op) for op in new_ops]
            input_keys = [op.get("opKey") for op in inputs.get("ops", [])]
            stored = {operation_key(op): (op.get("type"), op.get("side")) for op in stored_ops}
            expected = {operation_key(op): (op.get("type"), op.get("side")) for op in old_ops}
            if len(old_ops) != len(new_ops) or len(old_ops) != len(input_keys) or len(stored_ops) != len(old_ops) \
                    or len(set(input_keys)) != len(input_keys) or len(set(old_keys)) != len(old_keys) \
                    or len(set(new_keys)) != len(new_keys) or stored != expected:
                result["etk_protected"].append(f"{label}: ручной маршрут не сопоставлен")
                continue
            keys = dict(zip(old_keys, new_keys))
            if any(key not in keys for block in route.get("blocks", []) for key in block.get("ops", [])):
                result["etk_protected"].append(f"{label}: неизвестные операции ручного маршрута")
                continue
            mapped = deepcopy(route)
            for block in mapped.get("blocks", []):
                block["ops"] = [keys[key] for key in block.get("ops", [])]
            overrides = {**(overrides or {}), "route": mapped}
        row.inputs, row.overrides = inputs, overrides
        row.version += 1
        row.updated_at = now
        result["etk_changed"].append(f"{label}: {', '.join(changed)}")
        _event(uow, who, now, "techcards.fakra_inputs_migrated", {"operations": changed, "reason": REASON}, row.id)
    if result["etk_changed"]:
        _enqueue(uow, now, etk_refresh.REASON_TEMPLATE, {"reason": REASON, "cards": result["etk_changed"]})


def apply_payload(payload, uow, *, who, now, migrate_inputs=True):
    """Open UoW only; never commits. A caller may inspect the report and roll everything back."""
    result = {key: [] for key in OUTCOMES}
    if len({item["code"] for item in payload}) != len(payload):
        raise ValueError("Повтор кода в payload")
    allowed = set()
    for item in payload:
        code = item["code"]
        if code not in {value for mapping in STAGES.values() for value in mapping.values()}:
            raise ValueError(f"Код вне FAKRA/HFM: {code}")
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
            # formulas, machine, die and other process settings are not FAKRA data.
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
        links_before = len(result["equipment_linked"])
        _equipment(uow, code, edit, found, records[code], result)
        outcome = "created" if created else "unchanged" if same else "changed"
        result[outcome].append(code)
        allowed.add(code)
        if outcome != "unchanged" or code in result["rows_created"] or len(result["equipment_linked"]) != links_before:
            _event(uow, who, now, "techcards.fakra_template_applied", {"code": code, "outcome": outcome, "reason": REASON})
    if result["created"]:
        count = len(uow.operation_cards.sheets())
        snapshot = uow.session.get(OperationCardSnapshotRow, "current")
        if snapshot is None:
            uow.session.add(OperationCardSnapshotRow(name="current", source_commit="", imported_at=now, template_count=count))
        else:
            snapshot.template_count = count
    if migrate_inputs:
        _migrate_etks(uow, allowed, result, who, now)
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
            changed = any(result[key] for key in ("created", "changed", "rows_created", "equipment_linked", "etk_changed"))
            print("COMMITTED — обновление ревизий поставлено в существующую очередь ЭТК" if changed
                  else "COMMITTED — изменений нет")
        else:
            print("dry run — rolled back (включая журнал и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
