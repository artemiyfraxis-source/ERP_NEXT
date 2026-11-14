"""Reclassify БД.ОБ and its live references using an explicit, owner-approved mapping.

    python -m app.workflows.equipment_reclassify --mapping mapping.json [--apply]

Each physical unit must be listed separately. A registry_existing flag asserts that an already
numbered registry instrument is the same physical unit; it never authorizes merging two UUIDs.
Dry-run builds the complete plan without writes. Apply locks and commits all modules together.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field as dc_field, replace
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.modules.production.infrastructure import ProductionInstrumentRow, SqlAlchemyInstrumentRepository
from app.modules.production.traceability import MeasurementInstrument
from app.modules.references.equipment import (
    INV_KEY, MEASURING_TYPE, SUPPORTING_TYPE, TYPE_KEY, field, inv_key, name_of, parse_inventory, type_of,
)
from app.modules.references.infrastructure import EquipmentModelRow, EquipmentRecordRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, SqlAlchemyAuditRepository
from app.workflows.equipment_inventory_rewrite import (
    InventoryChange, Report, Replacements, TableCount, _TOKEN, apply_patches, begin_transaction,
    format_report as format_inventory_report, plan_rewrites,
)

AUDIT_EVENT = "references.equipment_reclassified"


@dataclass(frozen=True)
class MappingEntry:
    before: str
    after: str | None
    type: str | None
    registry_existing: bool = False


@dataclass(frozen=True)
class TypeChange:
    row_id: UUID
    inventory: str
    before: str
    after: str


@dataclass
class ReclassificationReport(Report):
    type_changes: list[TypeChange] = dc_field(default_factory=list)
    registry_changes: list[InventoryChange] = dc_field(default_factory=list)
    already_applied: list[str] = dc_field(default_factory=list)


def _number(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Инв. номер в сопоставлении должен быть строкой")
    number = value.strip().upper()
    parsed = parse_inventory(number)
    if not parsed or parsed.base <= 0 or parsed.instance <= 0 or any(c.isspace() for c in number):
        raise ValueError(f"Неверный номер физического экземпляра: {value}")
    return number


def validate_mapping(mapping: object) -> tuple[list[MappingEntry], bool]:
    if not isinstance(mapping, list):
        raise ValueError("Файл сопоставления должен содержать список записей")
    entries, supporting = [], False
    sources, targets = set(), set()
    for item in mapping:
        if not isinstance(item, dict):
            raise ValueError("Каждая запись сопоставления должна быть объектом")
        if "role" in item:
            if item != {"role": "supporting", "type": SUPPORTING_TYPE} or supporting:
                raise ValueError("Допускается одно правило supporting → Обеспечивающее оборудование")
            supporting = True
            continue
        if "from" not in item or set(item) - {"from", "to", "type", "registry_existing"}:
            raise ValueError("Запись должна содержать from и только to, type, registry_existing")
        before = _number(item["from"])
        after = None if item.get("to") is None else _number(item["to"])
        kind = item.get("type")
        if kind is not None and kind not in (MEASURING_TYPE, SUPPORTING_TYPE):
            raise ValueError(f"Неизвестный тип оборудования: {kind}")
        existing = item.get("registry_existing", False)
        if not isinstance(existing, bool) or (existing and after is None):
            raise ValueError("registry_existing должен быть логическим признаком для перенумерации")
        if after is None and kind is None:
            raise ValueError(f"Для {before} не задано ни нового номера, ни типа")
        if inv_key(before) in sources:
            raise ValueError(f"Повторный исходный номер: {before}")
        sources.add(inv_key(before))
        if after is not None:
            if inv_key(after) in targets:
                raise ValueError(f"Повторный целевой номер: {after}")
            targets.add(inv_key(after))
        entries.append(MappingEntry(before, after, kind, existing))
    if sources & targets:
        raise ValueError("Цепочки, циклы и замена номера на себя запрещены")
    return entries, supporting


def _unit_plan(session: Session, entries: list[MappingEntry], supporting: bool, report: ReclassificationReport):
    units = list(session.scalars(select(EquipmentRecordRow)))
    inventories = defaultdict(list)
    for unit in units:
        inventories[inv_key(field(unit.fields, INV_KEY, "inventoryNumber"))].append(unit)
    field_updates: dict[UUID, dict[str, str]] = {}
    explicit_ids = set()
    history = list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == AUDIT_EVENT)))
    audit_counts = report.tables[AuditEventRow.__tablename__] = TableCount(scanned=len(history))
    completed = {(item.aggregate_id, item.details.get("from"), item.details.get("to"), item.details.get("type")) for item in history}
    audit_events = []

    def change_type(unit: EquipmentRecordRow, kind: str | None) -> None:
        if kind is None or type_of(unit.fields) == kind:
            return
        values = field_updates.setdefault(unit.id, {})
        values[TYPE_KEY] = kind
        if "obType" in unit.fields:
            values["obType"] = kind
        report.type_changes.append(TypeChange(unit.id, field(unit.fields, INV_KEY, "inventoryNumber"), type_of(unit.fields), kind))

    for entry in entries:
        source = inventories[inv_key(entry.before)]
        target = inventories[inv_key(entry.after)] if entry.after is not None else []
        if not source:
            # Only this workflow's committed audit proves the target UUID belonged to this source.
            if (len(target) == 1
                    and (target[0].id, inv_key(entry.before), inv_key(entry.after), entry.type or "") in completed
                    and (entry.type is None or type_of(target[0].fields) == entry.type)):
                explicit_ids.add(target[0].id)
                report.already_applied.append(entry.before)
                continue
            raise ValueError(f"Исходный номер {entry.before} не найден в БД.ОБ")
        if len(source) != 1:
            raise ValueError(f"Исходный номер {entry.before} неоднозначен в БД.ОБ")
        if target:
            raise ValueError(f"Целевой номер {entry.after} уже занят в БД.ОБ")
        unit = source[0]
        explicit_ids.add(unit.id)
        if entry.after is not None:
            if not _TOKEN.fullmatch(field(unit.fields, INV_KEY, "inventoryNumber")):
                raise ValueError(f"Номер {entry.before} в БД.ОБ записан нестандартно; исправьте написание перед заменой")
            values = field_updates.setdefault(unit.id, {})
            values[INV_KEY] = entry.after
            if "inventoryNumber" in unit.fields:
                values["inventoryNumber"] = entry.after
            report.changes.append(InventoryChange(unit.id, field(unit.fields, INV_KEY, "inventoryNumber"), entry.after, name_of(unit.fields)))
            audit_events.append(AuditEvent(
                id=uuid4(), aggregate_id=unit.id, event_type=AUDIT_EVENT,
                actor=Actor(id=uuid5(NAMESPACE_URL, "erp-next:equipment-reclassify"),
                            name="Переклассификация оборудования", department="ERP"),
                occurred_at=moscow_now(),
                details={"from": inv_key(entry.before), "to": inv_key(entry.after), "type": entry.type or ""},
            ))
        change_type(unit, entry.type)
    if supporting:
        supporting_ids = set(session.scalars(select(EquipmentModelRow.id).where(EquipmentModelRow.role == "supporting")))
        for unit in units:
            if unit.id not in explicit_ids and unit.equipment_model_id in supporting_ids and type_of(unit.fields) == "Оборудование":
                change_type(unit, SUPPORTING_TYPE)
    audit_counts.changed = len(audit_events)
    return units, field_updates, audit_events


def _registry_plan(repo: SqlAlchemyInstrumentRepository, entries: list[MappingEntry], report: ReclassificationReport) -> list[MeasurementInstrument]:
    instruments = repo.list()
    inventories = defaultdict(list)
    for instrument in instruments:
        inventories[inv_key(instrument.inventory_number)].append(instrument)
    counts = report.tables[ProductionInstrumentRow.__tablename__] = TableCount(scanned=len(instruments))
    updates = []
    for entry in entries:
        if entry.after is None:
            continue
        source, target = inventories[inv_key(entry.before)], inventories[inv_key(entry.after)]
        if len(source) > 1 or len(target) > 1:
            raise ValueError(f"Неоднозначный номер в реестре СИ: {entry.before} → {entry.after}")
        if source and target:
            raise ValueError(f"В реестре СИ существуют оба номера: {entry.before} и {entry.after}; объединение запрещено")
        if source and entry.before in report.already_applied:
            raise ValueError(f"Реестр СИ изменён после переклассификации {entry.before}; проверьте прибор вручную")
        if target and not entry.registry_existing and entry.before not in report.already_applied:
            raise ValueError(f"Номер {entry.after} занят в реестре СИ; требуется registry_existing для того же прибора")
        if source:
            instrument = source[0]
            updates.append(replace(instrument, inventory_number=entry.after, version=instrument.version + 1))
            report.registry_changes.append(InventoryChange(instrument.id, instrument.inventory_number, entry.after, instrument.name))
            counts.changed += 1
            counts.occurrences += 1
    return updates


def run(engine: Engine, mapping: object, *, apply: bool = False) -> ReclassificationReport:
    entries, supporting = validate_mapping(mapping)
    with Session(engine, autoflush=False) as session:
        begin_transaction(session, apply=apply, extra_tables=(ProductionInstrumentRow.__tablename__,))
        report = ReclassificationReport()
        units, field_updates, audit_events = _unit_plan(session, entries, supporting, report)
        repo = SqlAlchemyInstrumentRepository(session)
        registry_updates = _registry_plan(repo, entries, report)
        patches = plan_rewrites(session, Replacements(report.changes, units), report, field_updates=field_updates)
        if apply:
            apply_patches(patches)
            for instrument in registry_updates:
                repo.save(instrument)
            audit = SqlAlchemyAuditRepository(session)
            for event in audit_events:
                audit.add(event)
            session.commit()
        else:
            session.rollback()
        return report


def format_report(report: ReclassificationReport, *, applied: bool = False) -> str:
    lines = [format_inventory_report(report, applied=applied), f"Изменить тип: {len(report.type_changes)}"]
    lines.extend(f"{item.row_id} | {item.inventory} | Тип: {item.before} → {item.after}" for item in report.type_changes)
    lines.append(f"Перенумеровать в реестре СИ: {len(report.registry_changes)}")
    lines.extend(f"{item.row_id} | Реестр СИ: {item.before} → {item.after} | {item.name}" for item in report.registry_changes)
    lines.append(f"Уже применено: {len(report.already_applied)}")
    lines.append(f"Добавить событий аудита перенумерации: {report.tables['audit_events'].changed}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Переклассификация БД.ОБ; по умолчанию без записи")
    parser.add_argument("--mapping", required=True, type=Path, help="JSON со списком сопоставлений и правил")
    parser.add_argument("--apply", action="store_true", help="применить все изменения одной транзакцией")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    try:
        mapping = json.loads(args.mapping.read_text(encoding="utf-8-sig"))
        validate_mapping(mapping)
        from app.database import build_engine
        engine = build_engine(database_url)
        try:
            report = run(engine, mapping, apply=args.apply)
        finally:
            engine.dispose()
    except (OSError, ValueError) as error:
        print(f"Не выполнено: {error}", file=sys.stderr)
        return 2
    print(format_report(report, applied=args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
