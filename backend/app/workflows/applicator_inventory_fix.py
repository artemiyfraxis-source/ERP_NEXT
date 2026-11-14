"""Replace legacy applicator inventory numbers with their existing M/S/A codes.

    python -m app.workflows.applicator_inventory_fix [--apply]

Dry-run is the default. The complete plan is built without mutating ORM rows; apply commits all live
copies in one transaction. PostgreSQL table locks (SQLite BEGIN IMMEDIATE) exclude concurrent writers
while the collision check and rewrite run. No schema changes, UUID changes, or manual document issues.

Inventory-reference audit (0171 / 0172):
- tech_operation_records: fields (including «Для инструмента» and БД.ОП cells), display, sort, search.
  Keep id, equipment_model_id and mirror_key; edited_in_erp protects corrections on normal mirror reload.
- equipment_models: display attributes only. Operation/unit links and reference_stock_links use UUID FKs.
- equipment_template_tools: inventory_number AND tool_name; _linked_tool compares both against the
  template parsed from raw_rows + edited. Rewrite those plus the cached document and live drafts.
- techcard_projects: inputs, overrides and draft documents; issued documents stay unchanged until normal
  ETK auto-revision. Count affected issued documents separately. Keep revision/issue identity.
- Generator drafts and unfinished assembly drafts carry build configs; rewrite them as live inputs too.
- td_issues: draft snapshots only, increment their edit version. Signing/active/annulled issues are frozen.
- techcard_revisions, audit/outbox history, source mirrors and template provenance remain untouched.
- equipment_setup_recipe_versions/tasks/commands and production_resource_reservations/active_resources/
  resource_allocation_machines use unit UUIDs after 0172, including setup snapshots and command results.
  production_instruments is a separate measurement-instrument registry, not an applicator reference.
- ETK builders, equipment catalogs, planning and tdPrintSheets consume the above data; they store no
  extra inventory identities. Normal ETK refresh hooks enqueue checks; this command never reissues history.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from uuid import UUID

from app.modules.references.equipment import (
    APPLICATOR_TYPE, CODE_KEY, DEFAULT_PREFIX, INV_KEY, field, inv_key, name_of, parse_inventory, type_of,
)
from app.modules.references.infrastructure import EquipmentRecordRow
from app.workflows.equipment_inventory_rewrite import (
    InventoryChange, Report, Replacements, SkippedRow, _TOKEN,
    apply_patches, begin_transaction, format_report, plan_rewrites,
)

_CODE = re.compile(r"[AMS]\d{1,3}")


def _inventory_plan(units: list[EquipmentRecordRow], report: Report) -> None:
    inventories: dict[str, set[UUID]] = defaultdict(set)
    codes: dict[str, set[UUID]] = defaultdict(set)
    for unit in units:
        if number := inv_key(field(unit.fields, INV_KEY)):
            inventories[number].add(unit.id)
        if code := inv_key(field(unit.fields, CODE_KEY)):
            codes[code].add(unit.id)
    for unit in sorted(units, key=lambda row: (field(row.fields, INV_KEY), str(row.id))):
        before = field(unit.fields, INV_KEY)
        parsed = parse_inventory(before)
        if type_of(unit.fields) != APPLICATOR_TYPE or parsed is None or parsed.prefix != DEFAULT_PREFIX:
            continue
        code = re.sub(r"\s+", "", field(unit.fields, CODE_KEY)).upper()
        reason = ""
        if not code:
            reason = "нет кода"
        elif not _CODE.fullmatch(code):
            reason = "неверный код: ожидается M, S или A и номер"
        elif (inventories.get(inv_key(code), set()) | codes.get(inv_key(code), set())) - {unit.id}:
            reason = "коллизия: код или инв. номер уже у другой записи"
        elif inventories[inv_key(before)] - {unit.id}:
            reason = "коллизия: старый инв. номер у нескольких записей"
        elif not _TOKEN.fullmatch(before):
            reason = "старый номер не обозначает физический экземпляр"
        if reason:
            report.skipped.append(SkippedRow(unit.id, before, code, reason))
        else:
            report.changes.append(InventoryChange(unit.id, before, code, name_of(unit.fields)))


def run(engine: Engine, *, apply: bool = False) -> Report:
    """Own the transaction; dry-run issues no INSERT/UPDATE/DELETE, even temporarily."""
    with Session(engine, autoflush=False) as session:
        begin_transaction(session, apply=apply)
        report = Report()
        units = list(session.scalars(select(EquipmentRecordRow)))
        _inventory_plan(units, report)
        patches = plan_rewrites(
            session, Replacements(report.changes, units, family_suffixes=True), report,
            field_updates={item.row_id: {INV_KEY: item.after, CODE_KEY: item.after} for item in report.changes},
            skipped_ids={item.row_id for item in report.skipped},
        )
        if apply:
            apply_patches(patches)
            session.commit()
        else:
            session.rollback()
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Инв. номера аппликаторов → коды M/S/A; по умолчанию без записи")
    parser.add_argument("--apply", action="store_true", help="применить все изменения одной транзакцией")
    args = parser.parse_args(argv)
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine
    engine = build_engine(database_url)
    try:
        report = run(engine, apply=args.apply)
    finally:
        engine.dispose()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(format_report(report, applied=args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
