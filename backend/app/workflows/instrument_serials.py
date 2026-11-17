"""Preview or atomically assign in-house serials; dry-run is the default."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import os
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.database import build_engine
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
from app.modules.production.instrument_serials import article_of, assign_in_house_serial, is_in_house_serial
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import SqlAlchemyAuditRepository


def assign_serials(sessions, *, apply: bool = False, actor: Actor, now: datetime) -> list[dict]:
    """Validate the complete projected registry before writing; one transaction owns all assignments."""
    with sessions.begin() as session:
        repository = SqlAlchemyInstrumentRepository(session)
        if apply:
            repository.lock_tree()
        items = sorted(repository.list(), key=lambda item: item.inventory_number)
        renumber = [item for item in items if not item.serial_number.strip() or is_in_house_serial(item.serial_number)]
        contexts = repository.serial_contexts([item.id for item in renumber])
        # «article.N» (owner 18.11): vendor serials stay; earlier in-house numbers are replaced, numbering per article
        # continues after the vendor/kept ones; an item without an article loses an earlier in-house number.
        taken = {item.serial_number.strip() for item in items if item not in renumber and item.serial_number.strip()}
        proposed = []
        for item in items:
            if item not in renumber:
                proposed.append(item)
                continue
            if article_of(item) is None:
                proposed.append(replace(item, serial_number="") if item.serial_number.strip() else item)
                continue
            context = replace(contexts[item.id], taken=frozenset(taken))
            assigned = assign_in_house_serial(item, now=now, context=context)
            taken.add(assigned.serial_number)
            proposed.append(assigned)
        serials: dict[str, list[str]] = {}
        for item in proposed:
            serials.setdefault(item.serial_number.strip(), []).append(item.inventory_number)
        duplicates = {serial: numbers for serial, numbers in serials.items() if serial and len(numbers) > 1}
        if duplicates:
            report = "; ".join(f"{serial}: {', '.join(numbers)}" for serial, numbers in sorted(duplicates.items()))
            raise ProductionValidationError(f"Повторяющиеся заводские / собственные номера: {report}")
        changes = [{"id": str(current.id), "inventory_number": current.inventory_number,
                    "serial_number": assigned.serial_number, "previous": current.serial_number}
                   for current, assigned in zip(items, proposed)
                   if assigned is not current and assigned.serial_number != current.serial_number]
        if apply:
            audit = SqlAlchemyAuditRepository(session)
            for current, assigned in zip(items, proposed):
                if assigned is current or assigned.serial_number == current.serial_number:
                    continue
                repository.save(replace(assigned, version=current.version + 1))
                audit.add(AuditEvent(
                    id=uuid4(), aggregate_id=current.id, event_type="production.instrument_serial_assigned",
                    actor=actor, occurred_at=now,
                    details={"inventory_number": current.inventory_number,
                             "serial_number": [current.serial_number, assigned.serial_number],
                             "version": [current.version, current.version + 1]},
                ))
        return changes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Собственные номера СИ: предварительный просмотр по умолчанию")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Показать назначения без записи (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="Назначить номера пустым записям")
    parser.add_argument("--actor-name", default="Назначение номеров СИ")
    args = parser.parse_args(argv)
    database_url = os.getenv("ERP_DATABASE_URL")
    if not database_url:
        parser.error("Укажите ERP_DATABASE_URL")
    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    try:
        changes = assign_serials(sessions, apply=args.apply,
                                actor=Actor(UUID(int=0), args.actor_name, "Администрирование"), now=moscow_now())
        for item in changes:
            print(f"{item['inventory_number']}: {item['previous'] or '—'} → {item['serial_number'] or '— (очищен)'}")
        print(f"{'Назначено' if args.apply else 'Будет назначено'}: {len(changes)}")
    except ProductionValidationError as error:
        raise SystemExit(str(error)) from error
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
