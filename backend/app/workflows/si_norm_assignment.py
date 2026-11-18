"""Propose (default) or apply the assigned norm of every coax adapter / test cable СИ (owner 18.11.2026).

    python -m app.workflows.si_norm_assignment --csv proposals.csv              # ERP_DATABASE_URL, read only
    python -m app.workflows.si_norm_assignment --rows export.json --csv out.csv  # a read-only export (EXPORT_SQL)
    python -m app.workflows.si_norm_assignment --apply --csv reviewed.csv        # writes; quality applies after review

The proposal follows `instrument_norm_assignment.propose`. Apply takes the REVIEWED file, keyed by inventory number:
a row is written only when the card still holds `current_norm_id` (else refused — someone changed it meanwhile), each
change passes the catalog check of RfRepository.save and lands in the audit as `production.rf_norm_assigned`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.modules.production.instrument_norm_assignment import limits_text, pair_text, propose
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from app.shared.identity import Actor

COLUMNS = ("inventory_no", "name", "pair", "norm_id", "norm_reference", "limits", "reason", "current_norm_id")
ACTOR = Actor(uuid5(NAMESPACE_URL, "erp-next:si-norm-assignment"), "Назначение норм СИ", "Служба качества")
# Read-only export for --rows (psql as the read-only role, before the norm_id column exists on the server).
EXPORT_SQL = """
SELECT json_agg(json_build_object(
  'inventory_number', i.inventory_number, 'name', i.name, 'doc_designation', coalesce(i.doc_designation, ''),
  'manufacturer', coalesce(i.manufacturer, ''), 'role', d.role,
  'interfaces', (SELECT coalesce(json_agg(json_build_object('series', p.series, 'body', p.body, 'center', p.center,
                 'impedance_ohm', p.impedance_ohm) ORDER BY p.position), '[]') FROM production_rf_interfaces p
                 WHERE p.instrument_id = i.id)) ORDER BY i.inventory_number)
FROM production_instruments i JOIN production_rf_details d ON d.instrument_id = i.id
"""


def _row_details(row: dict) -> RfDetails:
    ports = tuple(RfInterface(p["series"], p["body"], p["center"], p.get("impedance_ohm")) for p in row.get("interfaces") or ())
    return RfDetails(ports, row.get("role") or "unknown", norm_id=row.get("norm_id") or "")


def rows_from_json(path: Path):
    for row in json.loads(path.read_text(encoding="utf-8")) or ():
        yield row["inventory_number"], row["name"], row.get("doc_designation", ""), row.get("manufacturer", ""), _row_details(row)


def rows_from_session(session):
    from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
    from app.modules.production.rf_infrastructure import RfRepository

    details = RfRepository(session).list()
    for item in sorted(SqlAlchemyInstrumentRepository(session).list(), key=lambda item: item.inventory_number):
        if item.id in details:
            yield item.inventory_number, item.name, item.doc_designation or "", item.manufacturer or "", details[item.id]


def proposals(rows) -> list[dict]:
    result = []
    for inventory, name, model, manufacturer, rf in rows:
        proposal = propose(name, model, manufacturer, rf)
        if proposal is None:
            continue
        entry = proposal.entry
        result.append(dict(inventory_no=inventory, name=name, pair=pair_text(rf), norm_id=proposal.norm_id,
                           norm_reference=entry.norm.reference if entry else "", limits=limits_text(entry) if entry else "",
                           reason=proposal.reason, current_norm_id=rf.norm_id))
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def apply(uow, rows: list[dict], now) -> dict[str, list[str]]:
    """Write the reviewed assignments in the caller's unit of work (the caller commits)."""
    from dataclasses import replace

    from app.modules.production.rf_infrastructure import RfRepository
    from app.shared.events import AuditEvent

    uow.instruments.lock_tree()
    items = {item.inventory_number: item for item in uow.instruments.list()}
    repository, outcome = RfRepository(uow.session), {"written": [], "unchanged": [], "refused": []}
    for row in rows:
        item, target = items.get(row["inventory_no"]), row["norm_id"].strip()
        if item is None:
            outcome["refused"].append(f"{row['inventory_no']}: нет в реестре")
            continue
        current = repository.get(item.id)
        if current.norm_id == target:
            outcome["unchanged"].append(row["inventory_no"])
        elif current.norm_id != row.get("current_norm_id", "").strip():
            outcome["refused"].append(f"{row['inventory_no']}: в карточке уже {current.norm_id or 'пусто'}, "
                                      f"в файле ожидалось {row.get('current_norm_id') or 'пусто'}")
        else:
            saved = repository.save(item.id, replace(current, norm_id=target))  # catalog check inside
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=item.id, event_type="production.rf_norm_assigned",
                                     actor=ACTOR, occurred_at=now, details={
                                         "before": current.norm_id, "after": saved.norm_id, "reason": row.get("reason", "")}))
            outcome["written"].append(f"{row['inventory_no']}: {current.norm_id or '—'} → {target or '—'}")
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Назначенная норма СИ: предложение (по умолчанию) или запись по проверенному CSV")
    parser.add_argument("--csv", type=Path, required=True, help="куда записать предложение; с --apply — проверенный файл")
    parser.add_argument("--rows", type=Path, help="выгрузка EXPORT_SQL вместо базы (только предложение)")
    parser.add_argument("--apply", action="store_true", help="записать нормы из проверенного --csv в ERP_DATABASE_URL")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.rows and not args.apply:
        rows = proposals(rows_from_json(args.rows))
        write_csv(args.csv, rows)
        print(f"Предложено: {sum(1 for r in rows if r['norm_id'])} из {len(rows)}; файл {args.csv}")
        return 0
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url or args.rows:
        print("Задайте ERP_DATABASE_URL; --rows только для предложения", file=sys.stderr)
        return 2
    from app.database import build_engine, build_production_uow_factory
    from app.shared.clock import moscow_now

    engine = build_engine(url)
    try:
        factory = build_production_uow_factory(engine)
        with factory() as uow:
            if not args.apply:
                rows = proposals(rows_from_session(uow.session))
                write_csv(args.csv, rows)
                print(f"Предложено: {sum(1 for r in rows if r['norm_id'])} из {len(rows)}; файл {args.csv}; без записи")
                return 0
            with args.csv.open(encoding="utf-8", newline="") as handle:
                outcome = apply(uow, list(csv.DictReader(handle)), moscow_now())
            uow.commit()
    finally:
        engine.dispose()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
