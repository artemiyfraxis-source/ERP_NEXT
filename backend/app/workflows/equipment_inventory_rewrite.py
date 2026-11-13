"""Shared atomic rewrite of live equipment references; issued snapshots remain frozen.

The inventory-reference audit is documented in applicator_inventory_fix. Callers build explicit
unit field patches and choose whether legacy applicator family suffixes may be inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.references.equipment import INV_KEY, field, inv_key
from app.modules.references.infrastructure import EquipmentModelRow, EquipmentTemplateToolRow, TechOperationRecordRow
from app.modules.techcards.assembly_drafts_infrastructure import AssemblyDraftRow
from app.modules.techcards.generator_drafts_infrastructure import GeneratorDraftRow
from app.modules.techcards.infrastructure import (
    OperationCardDraftRow, OperationCardTemplateRow, ProjectTechcardRow, SqlAlchemyTechcardRepository,
)
from app.modules.techcards.project_cards import TechcardStatus
from app.modules.techcards.td_issues_infrastructure import TdIssueRow
from app.shared.clock import moscow_now
from app.workflows import etk_refresh  # registers the normal transactional refresh hook


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
_TOKEN = re.compile(r"(?<![\w.])(?:[A-ZА-ЯЁ]+\.\d+(?:\.\d+)?|[AMS]\d{1,3}(?:\.\d+)?)(?!\w|\.\w)", re.IGNORECASE)
LIVE_COLUMNS = (
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


class Replacements:
    def __init__(self, changes: list[InventoryChange], units: list[TechOperationRecordRow], *, family_suffixes: bool = False):
        self.family_suffixes = family_suffixes
        self.numbers = {inv_key(item.before): item.after for item in changes}
        changed_ids = {item.row_id for item in changes}
        # An actual .k row overrides the family fallback, including non-applicators and skipped applicators.
        self.protected = {inv_key(field(unit.fields, INV_KEY, "inventoryNumber")) for unit in units if unit.id not in changed_ids}

    def _token(self, match: re.Match) -> str:
        original = match[0]
        key = inv_key(original)
        if key in self.protected:
            return original
        if key in self.numbers:
            return self.numbers[key]
        parent, dot, suffix = key.rpartition(".")
        if self.family_suffixes and dot and suffix.isdigit() and parent in self.numbers:
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


def plan_rewrites(
    session: Session, replacements: Replacements, report: Report, *,
    field_updates: dict[UUID, dict[str, str]], skipped_ids: set[UUID] | None = None,
) -> list[tuple[Any, dict[str, Any]]]:
    skipped_ids = skipped_ids or set()
    patches: list[tuple[Any, dict[str, Any]]] = []
    now = moscow_now()
    for model, columns in LIVE_COLUMNS:
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
            if isinstance(row, TechOperationRecordRow) and row.id in field_updates:
                values["fields"] = {**values.get("fields", row.fields), **field_updates[row.id]}
            if not values:
                continue
            if isinstance(row, TechOperationRecordRow):
                fields = values.get("fields", row.fields)
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
    return patches


def begin_transaction(session: Session, *, apply: bool, extra_tables: tuple[str, ...] = ()) -> None:
    """Lock the full write set before validation; table names must be code-owned constants."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        if apply:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            tables = ", ".join(sorted({model.__tablename__ for model, _ in LIVE_COLUMNS} | set(extra_tables)))
            session.execute(text(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE"))
        else:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    elif dialect == "sqlite":
        session.execute(text("BEGIN IMMEDIATE" if apply else "BEGIN"))
    else:
        raise ValueError("Поддерживаются только PostgreSQL и SQLite")


def apply_patches(patches: list[tuple[Any, dict[str, Any]]]) -> None:
    for row, values in patches:
        for column, value in values.items():
            setattr(row, column, value)


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
