"""Reviewed UUID bindings between the equipment catalog and the existing instrument registry.

Inventory matches are suggestions only. This workflow never creates verification evidence, instruments,
accessories or files and never modifies registry-owned passport fields.
"""

from uuid import UUID, uuid4

from sqlalchemy import select

from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.modules.references.equipment import inv_key, field
from app.modules.references.infrastructure import EquipmentInstrumentBindingRow, SqlAlchemyTechOperationRepository
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.persistence import SqlAlchemyAuditRepository


def instrument_passports(session, unit_ids):
    if not unit_ids:
        return {}
    statement = select(EquipmentInstrumentBindingRow.unit_id, ProductionInstrumentRow).join(
        ProductionInstrumentRow, ProductionInstrumentRow.id == EquipmentInstrumentBindingRow.instrument_id
    ).where(EquipmentInstrumentBindingRow.unit_id.in_(unit_ids), ProductionInstrumentRow.kind == "Средство измерений")
    return {unit_id: {"serial_number": item.serial_number, "status": item.status,
                     "verification_number": item.verification_number,
                     "verified_at": item.verified_at.isoformat() if item.verified_at else None,
                     "verification_due": item.verification_due.isoformat() if item.verification_due else None}
            for unit_id, item in session.execute(statement)}


def binding_report(session):
    instruments = list(session.scalars(select(ProductionInstrumentRow)))
    bindings = {row.unit_id: row.instrument_id for row in session.scalars(select(EquipmentInstrumentBindingRow))}
    rows = SqlAlchemyTechOperationRepository(session).list(tab=TechOperationTab.MEASURING, query="", limit=100_000)
    result = []
    for row in rows:
        number = inv_key(field(row.fields, "Инв. номер", "inventoryNumber"))
        candidates = [str(item.id) for item in instruments if item.kind == "Средство измерений"
                      and number and inv_key(item.inventory_number) == number]
        bound = next((item for item in instruments if item.id == bindings.get(row.id)), None)
        valid = bound is not None and bound.kind == "Средство измерений"
        result.append({"unit_id": str(row.id), "instrument_id": str(bindings[row.id]) if row.id in bindings else None,
                       "candidates": candidates, "resolved": valid,
                       "issue": "Связанная запись больше не является средством измерений" if bound and not valid else ""})
    return result


def bind_reviewed(session, pairs, actor):
    """Validate all explicit UUID pairs before adding anything; caller owns the transaction."""
    repository = SqlAlchemyTechOperationRepository(session)
    stored = list(session.scalars(select(EquipmentInstrumentBindingRow)))
    by_unit = {row.unit_id: row.instrument_id for row in stored}
    by_instrument = {row.instrument_id: row.unit_id for row in stored}
    pending = []
    for pair in pairs:
        unit_id, instrument_id = UUID(pair["unit_id"]), UUID(pair["instrument_id"])
        unit = repository.get(unit_id)
        instrument = session.get(ProductionInstrumentRow, instrument_id)
        if unit is None or unit.tab != TechOperationTab.MEASURING or instrument is None or instrument.kind != "Средство измерений":
            raise ReferenceValidationError("Связь требует UUID средства измерений из каталога и реестра")
        if unit_id in by_unit and by_unit[unit_id] == instrument_id:
            continue
        if unit_id in by_unit or instrument_id in by_instrument:
            raise ReferenceValidationError("У экземпляра или записи реестра уже есть другая связь")
        by_unit[unit_id], by_instrument[instrument_id] = instrument_id, unit_id
        pending.append((unit_id, instrument_id))
    audit = SqlAlchemyAuditRepository(session)
    for unit_id, instrument_id in pending:
        session.add(EquipmentInstrumentBindingRow(unit_id=unit_id, instrument_id=instrument_id))
        audit.add(AuditEvent(uuid4(), unit_id, "references.equipment_instrument_bound", actor, moscow_now(),
                            {"instrument_id": str(instrument_id)}))
    return len(pending)


def main(argv=None):
    import argparse
    import json
    import os
    from pathlib import Path
    from uuid import NAMESPACE_URL, uuid5
    from sqlalchemy.orm import Session
    from app.database import build_engine
    from app.shared.identity import Actor

    parser = argparse.ArgumentParser(description="Связи каталога СИ с реестром по UUID")
    parser.add_argument("--mapping", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.mapping:
        parser.error("Для применения нужен проверенный --mapping с unit_id и instrument_id")
    url = os.getenv("ERP_DATABASE_URL", "")
    if not url:
        parser.error("Задайте ERP_DATABASE_URL")
    engine = build_engine(url)
    try:
        with Session(engine) as session:
            if args.mapping:
                pairs = json.loads(args.mapping.read_text(encoding="utf-8-sig"))
                actor = Actor(uuid5(NAMESPACE_URL, "erp-next:equipment-instruments"), "Связь средств измерений", "ERP")
                count = bind_reviewed(session, pairs, actor)
                if args.apply:
                    session.commit()
                else:
                    session.rollback()
                print(f"Связей: {count}; записано: {args.apply}")
            else:
                print(json.dumps(binding_report(session), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
