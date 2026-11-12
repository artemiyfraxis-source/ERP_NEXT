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
from dataclasses import dataclass, field as dc_field
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.modules.references.equipment import (
    APPLICATOR_TYPE, CODE_KEY, DEFAULT_PREFIX, INV_KEY, field, inv_key, name_of, parse_inventory, type_of,
)
from app.modules.references.infrastructure import EquipmentModelRow, EquipmentTemplateToolRow, TechOperationRecordRow
from app.modules.techcards.assembly_drafts_infrastructure import AssemblyDraftRow
from app.modules.techcards.generator_drafts_infrastructure import GeneratorDraftRow
from app.modules.techcards.infrastructure import (
    OperationCardDraftRow, OperationCardTemplateRow, ProjectTechcardRow, SqlAlchemyTechcardRepository,
)
from app.modules.techcards.project_cards import TechcardStatus
from app.modules.techcards.td_issues_infrastructure import TdIssueRow
from app.shared.clock import moscow_now
from app.workflows import etk_refresh  # also registers the normal transactional refresh hook


@dataclass(frozen=True)
class InventoryChange:
    row_id: UUID
    before: str
    after: str
    name: str


@dataclass(frozen=True)
class SkippedRow:
    row_id: UUID
    inventory: str
    code: str
    reason: str


@dataclass
class TableCount:
    scanned: int = 0
    changed: int = 0
    occurrences: int = 0


@dataclass
class Report:
    changes: list[InventoryChange] = dc_field(default_factory=list)
    skipped: list[SkippedRow] = dc_field(default_factory=list)
    tables: dict[str, TableCount] = dc_field(default_factory=dict)
    issued_documents_to_refresh: int = 0
    issued_documents_without_inputs: list[UUID] = dc_field(default_factory=list)


# A trailing sentence dot is punctuation; .X, .1.2, embedded identifiers and larger numbers are not matches.
_TOKEN = re.compile(r"(?<![\w.])ОБ\.\d+(?:\.\d+)?(?!\w|\.\w)", re.IGNORECASE)
_CODE = re.compile(r"[AMS]\d{1,3}")
_LIVE_COLUMNS = (
    (TechOperationRecordRow, ("fields", "display_text", "sort_key", "normalized_search")),
    (EquipmentModelRow, ("name", "article", "maker")),
    (EquipmentTemplateToolRow, ("tool_name", "inventory_number")),
    (OperationCardTemplateRow, ("document", "edited", "raw_rows", "title", "name", "description")),
    (OperationCardDraftRow, ("rows", "name", "title", "description")),
    (GeneratorDraftRow, ("payload",)),
    (AssemblyDraftRow, ("draft_config",)),
    (ProjectTechcardRow, ("document", "inputs", "overrides")),
    (TdIssueRow, ("snapshot",)),
)


def _inventory_plan(units: list[TechOperationRecordRow], report: Report) -> None:
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


class _Replacements:
    def __init__(self, changes: list[InventoryChange], units: list[TechOperationRecordRow]):
        self.numbers = {inv_key(item.before): item.after for item in changes}
        changed_ids = {item.row_id for item in changes}
        # An actual .k row overrides the family fallback, including non-applicators and skipped applicators.
        self.protected = {inv_key(field(unit.fields, INV_KEY)) for unit in units if unit.id not in changed_ids}

    def _token(self, match: re.Match) -> str:
        original = match[0]
        key = inv_key(original)
        if key in self.protected:
            return original
        if key in self.numbers:
            return self.numbers[key]
        parent, dot, suffix = key.rpartition(".")
        if dot and suffix.isdigit() and parent in self.numbers:
            return f"{self.numbers[parent]}.{suffix}"
        return original

    def rewrite(self, value: Any) -> tuple[Any, int]:
        if isinstance(value, str):
            occurrences = 0

            def replace(match: re.Match) -> str:
                nonlocal occurrences
                result = self._token(match)
                occurrences += result != match[0]
                return result

            return _TOKEN.sub(replace, value), occurrences
        if isinstance(value, list):
            items = [self.rewrite(item) for item in value]
            return [item for item, _ in items], sum(count for _, count in items)
        if isinstance(value, dict):
            result, occurrences = {}, 0
            for key, item in value.items():
                new_key, key_count = self.rewrite(key)
                new_item, count = self.rewrite(item)
                if new_key in result:
                    raise ValueError(f"Коллизия ключей JSON после замены: {new_key}")
                result[new_key] = new_item
                occurrences += key_count + count
            return result, occurrences
        return value, 0

    def count(self, value: Any) -> int:
        """Count references in frozen content without constructing a rewrite or colliding JSON keys."""
        if isinstance(value, dict):
            return sum(self.count(key) + self.count(item) for key, item in value.items())
        if isinstance(value, list):
            return sum(self.count(item) for item in value)
        return self.rewrite(value)[1]


def _plan(session: Session) -> tuple[Report, list[tuple[Any, dict[str, Any]]]]:
    report = Report()
    units = list(session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "ob")))
    _inventory_plan(units, report)
    replacements = _Replacements(report.changes, units)
    skipped_ids = {item.row_id for item in report.skipped}
    changed_codes = {item.row_id: item.after for item in report.changes}
    patches: list[tuple[Any, dict[str, Any]]] = []
    now = moscow_now()
    for model, columns in _LIVE_COLUMNS:
        counts = report.tables[model.__tablename__] = TableCount()
        for row in session.scalars(select(model)):
            counts.scanned += 1
            if isinstance(row, TdIssueRow) and row.status != "draft":
                continue
            if isinstance(row, AssemblyDraftRow) and row.status != "draft":
                continue
            if isinstance(row, TechOperationRecordRow) and row.id in skipped_ids:
                continue
            issued = isinstance(row, ProjectTechcardRow) and row.status == TechcardStatus.ISSUED.value
            values, occurrences, issued_document_changed = {}, 0, False
            for column in columns:
                old = getattr(row, column)
                if issued and column == "document":
                    issued_document_changed = bool(replacements.count(old))
                    continue
                if isinstance(row, ProjectTechcardRow) and column == "overrides":
                    # Legacy manual operations/route may live only in the frozen document. The refresh
                    # would copy them back verbatim; persist their corrected override copy instead.
                    old = etk_refresh._effective_overrides(SqlAlchemyTechcardRepository._domain(row))
                new, count = replacements.rewrite(old)
                if new != old:
                    values[column] = new
                    occurrences += count
            if issued and (issued_document_changed or values):
                if row.inputs:
                    report.issued_documents_to_refresh += 1
                else:
                    report.issued_documents_without_inputs.append(row.id)
            if not values:
                continue
            if isinstance(row, TechOperationRecordRow):
                fields = values.get("fields", row.fields)
                if row.id in changed_codes:
                    fields = {**fields, INV_KEY: changed_codes[row.id], CODE_KEY: changed_codes[row.id]}
                    values["fields"] = fields
                display = values.get("display_text", row.display_text)
                values["normalized_search"] = " ".join(f"{display} {' '.join(str(v) for v in fields.values())}".casefold().split())
                values["edited_in_erp"] = True
            if isinstance(row, (ProjectTechcardRow, TdIssueRow, GeneratorDraftRow)):
                values["version"] = row.version + 1
            if isinstance(row, (ProjectTechcardRow, OperationCardDraftRow, GeneratorDraftRow, AssemblyDraftRow)):
                values["updated_at"] = now
            patches.append((row, values))
            counts.changed += 1
            counts.occurrences += occurrences
    return report, patches


def run(engine: Engine, *, apply: bool = False) -> Report:
    """Own the transaction; dry-run issues no INSERT/UPDATE/DELETE, even temporarily."""
    with Session(engine, autoflush=False) as session:
        if engine.dialect.name == "postgresql":
            if apply:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                tables = ", ".join(sorted(model.__tablename__ for model, _ in _LIVE_COLUMNS))
                session.execute(text(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE"))
            else:
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        elif engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE" if apply else "BEGIN"))
        else:
            raise ValueError("Поддерживаются только PostgreSQL и SQLite")
        report, patches = _plan(session)
        if apply:
            for row, values in patches:
                for column, value in values.items():
                    setattr(row, column, value)
            session.commit()
        else:
            session.rollback()
        return report


def format_report(report: Report, *, applied: bool = False) -> str:
    lines = ["Изменения записаны." if applied else "Проверка без записи. Для применения добавьте --apply.",
             "ID записи | Было → Стало | Наименование"]
    lines.extend(f"{item.row_id} | {item.before} → {item.after} | {item.name}" for item in report.changes)
    lines.append(f"Перенумеровать: {len(report.changes)}; пропущено: {len(report.skipped)}")
    lines.append(f"Выданных ЭТК для автоматического обновления: {report.issued_documents_to_refresh}")
    lines.append(f"Выданных ЭТК без входов генератора (автообновление невозможно): {len(report.issued_documents_without_inputs)}")
    lines.extend(f"Нужна проверка технолога: ЭТК {card_id} — нет входов генератора"
                 for card_id in report.issued_documents_without_inputs)
    lines.extend(f"Пропущено {item.row_id} | {item.inventory} | {item.code or '—'} | {item.reason}" for item in report.skipped)
    lines.append("Таблица | Просмотрено строк | Изменено строк" + ("" if applied else " (будет)") + " | Замен вхождений")
    lines.extend(f"{name} | {counts.scanned} | {counts.changed} | {counts.occurrences}" for name, counts in report.tables.items())
    return "\n".join(lines)


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
