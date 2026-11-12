"""One-off administrative repair: python -m app.workflows.rename_project OLD NEW [--apply].

Inventory (column policies below are also the executable per-table inventory):

* techcard_kitsets: project, key and assemblies move; calc_source/folder_url stay as provenance.
* techcard_projects: project/key and document/inputs/overrides move; UUIDs, ETK revision numbers,
  status and source_calculation stay. derived_hash is invalidated, not forged.
* techcard_revisions: document/diff identity scalars move, including build_config.projectNo
  and itemId; revision number, source, reason, actor and date stay. This is an explicit identity correction
  to every printable snapshot, not a new engineering revision.
* td_issues: snapshot identity scalars move, version increments; designation sequence, signatures,
  status, notice/reason and approval dates stay. No table references td_issues.id in this schema.
* techcard_assembly_drafts, techcard_generator_drafts, techcard_harness_routes: project and JSON
  identity scalars move; generator build_fingerprint is cleared so stale forms cannot silently win.
* techcard_kitset_documents: project and generated leading item token in file_name move; bytes,
  URLs and external file IDs stay. techcard_harness_revisions: binary drawing revisions stay;
  the drawing is reached by its unchanged document UUID. operation sketches stay by card UUID.
* production_plan_projects, production_plan_baseline_lines, production_plan_events,
  production_tasks, production_downtimes, production_work_time_log: project keys and compound
  item fields move. Journal text/reason/from_value/value stay verbatim. Add one new note.
* production_boxes: compound item moves; physical box code stays (already printed/scannable).
  All other production child tables reference stable task/operation/box UUIDs and stay.
  Journal/work-log/baseline no-update triggers stay enabled: replace affected rows under the
  SAME primary keys in this transaction, preserving every non-identity field. These tables
  have no incoming FKs. Legacy rows that fail today's insert checks cause an atomic refusal;
  a production-copy rehearsal must establish whether such grandfathered rows exist.
* finished_goods, shipment_lines: project/key/article move. finished_goods_moves and
  shipping_pack_permissions: project_key moves; movement amounts and history text stay.
* shipping_project_addressees, shipping_files: project moves; file bytes/names stay as history.
  shipping_sequences' hashed document:<sha256(project)> counters stay in their old namespace;
  future PL/DN numbers use NEW and a new namespace, existing PL/DN numbers stay document history.
  shipments version increments when a child line changes; boxes/documents/acts/lots stay by UUID.
* fulfillment_requests: purpose leading token and project_key move; fulfillment_lines: generated
  articles move (their parent version also increments). fulfillment_kit_reserves: project/article
  move, preserving lot UUID, quantities and consumption/release history.
* requests, procurement_lines: project_reference, normalized keys, purpose and design_article
  move; designation moves ONLY for the generated project.NNN (at least three digits) form.
* request_designations: purpose and generated designation move (idempotency lookup).
* requests_calc_compositions: project and rows identity scalars move; source_id stays. Recompute
  content_hash with CompositionSeed (it includes project), preserving recorded request links.
* procurement_stock_covers, purchasing_upd_documents: project_reference moves. RFQ line articles
  in purchasing_rfq_lines move; all other purchase links use unchanged UUIDs/order numbers.
  purchasing_internal_projects.number is collision-checked only: it belongs to another registry.
* receiving_lines: purpose/goal/keys and generated article move. warehouse_lots: purpose/article
  move. warehouse_receipts and warehouse_writeoffs: purpose moves; quantities, reason and source
  documents stay. Other stock reserves link to unchanged lot/line UUIDs.
* operations_job_marks: only tc_name_queue JSON project tokens move. project_tasks.project moves;
  regenerate an open arbiter task's title only if it exactly matches its known kind's generated
  title. Closed/custom task titles stay as the original instruction.
  notifications.reference moves if it is an exact project/compound token; title/body stay history.
* outbox_messages: unpublished payload identity scalars move so pending delivery cannot resurrect
  OLD; published messages stay history. Audit events stay entirely unchanged; append one audit.
* warehouse_label_jobs: pending jobs linked to affected stock lots or finished-goods acts are
  rerendered with current templates/settings, same job ID/copies; printed/failed jobs stay history.
  Refuse if an affected job is taken: its raster may already be on the printer. Sample jobs stay.
* Idempotency keys and command fingerprints stay as receipts of the original commands, not new
  commands. URLs, free prose, supplier codes, external documents and binary drawings are not
  searched/replaced. A sibling OLD.1 or OLD1 never matches OLD. All UUID/FK relationships stay.

One Session/transaction owns every cross-module write. PostgreSQL project advisory locks plus
SHARE ROW EXCLUSIVE table locks exclude concurrent writers during inventory/preflight/apply;
SQLite uses BEGIN IMMEDIATE. Before --apply STOP AND DRAIN API/background writers: locks alone
cannot stop a request that cached OLD before this transaction from writing OLD after commit.
The locks cover the participating tables. Unknown
project/key columns fail closed. No schema creation, production connection default, commit or
write during dry run. Rehearse against a production copy before using --apply on production.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import Engine, MetaData, Table, and_, delete, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.techcards.kitsets import project_number_valid
from app.shared.advisory_lock import lock_project_run
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import Base, SqlAlchemyAuditRepository
from app.shared.project_number import normalize_project_number


class RenameRefused(ValueError):
    """A preflight failure: no data may be written."""


# p: exact project; k: canonical key; c: exact or leading compound token;
# d: c plus generated project.NNN designation; j: structured JSON identity scalars.
POLICIES: dict[str, dict[str, str]] = {
    "techcard_kitsets": {"project": "p", "project_key": "k", "assemblies": "j"},
    "techcard_projects": {"project": "p", "project_key": "k", "document": "j", "inputs": "j", "overrides": "j"},
    "techcard_revisions": {"document": "j", "diff": "j"},
    "td_issues": {"snapshot": "j"},
    "techcard_assembly_drafts": {"project": "p", "draft_config": "j", "final_config": "j"},
    "techcard_generator_drafts": {"project": "p", "payload": "j"},
    "techcard_harness_routes": {"project": "p", "document": "j"},
    "techcard_kitset_documents": {"project": "p", "file_name": "c"},
    "production_plan_projects": {"project": "p"},
    "production_plan_baseline_lines": {"project": "p", "item": "c"},
    "production_plan_events": {"project": "p"},
    "production_tasks": {"project": "p", "project_key": "k", "item": "c"},
    "production_downtimes": {"project": "p", "item": "c"},
    "production_work_time_log": {"project": "p", "item": "c"},
    "production_boxes": {"item": "c"},
    "finished_goods": {"project": "p", "project_key": "k", "article": "c"},
    "shipment_lines": {"project": "p", "project_key": "k", "article": "c"},
    "finished_goods_moves": {"project_key": "k"},
    "shipping_pack_permissions": {"project_key": "k"},
    "shipping_project_addressees": {"project": "p"},
    "shipping_files": {"project": "p"},
    "fulfillment_requests": {"project_key": "k", "purpose": "c"},
    "fulfillment_lines": {"article": "c"},
    "fulfillment_kit_reserves": {"project": "p", "article": "c"},
    "requests": {"project_reference": "p", "project_key": "k", "purpose_project_key": "k",
                 "designation_project_key": "k", "purpose": "d", "designation": "d", "design_article": "c"},
    "procurement_lines": {"project_reference": "p", "project_key": "k", "designation": "d", "design_article": "c"},
    "request_designations": {"purpose": "d", "designation": "d"},
    "requests_calc_compositions": {"project": "p", "rows": "j"},
    "procurement_stock_covers": {"project_reference": "p"},
    "purchasing_upd_documents": {"project_reference": "p"},
    "purchasing_rfq_lines": {"article": "c"},
    "receiving_lines": {"purpose": "d", "goal": "d", "purpose_project_key": "k", "goal_project_key": "k", "article": "c"},
    "warehouse_lots": {"purpose": "d", "article": "c"},
    "warehouse_receipts": {"purpose": "d"},
    "warehouse_writeoffs": {"purpose": "d"},
    "operations_job_marks": {"value": "j"},
    "project_tasks": {"project": "p"},
    "notifications": {"reference": "c"},
    "outbox_messages": {"payload": "j"},
}

_TABLES = frozenset(POLICIES) | {"audit_events", "shipments", "purchasing_internal_projects",
    "warehouse_label_jobs", "finished_goods_acts", "warehouse_label_settings", "warehouse_label_templates"}


def rename_token(value: str, old: str, new: str, *, designation: bool = False) -> str:
    """Exact normalized identity or leading project followed by КБ / ' · ', never a prefix match."""
    key = normalize_project_number(old)
    if normalize_project_number(value) == key:
        return new
    match = re.match(r"^(.*?)(КБ(?=\d)| · )", value, flags=re.I)
    if match and normalize_project_number(match[1]) == key:
        return new + value[len(match[1]):]
    if designation:
        match = re.fullmatch(r"(.*)(\.\d{3,})", value)
        if match and normalize_project_number(match[1]) == key:
            return new + match[2]
    return value


def rename_json(value: Any, old: str, new: str) -> Any:
    """Copy JSON; rewrite identity scalars/keys, never replace substrings inside prose or URLs."""
    if isinstance(value, str):
        return rename_token(value, old, new)
    if isinstance(value, list):
        return [rename_json(item, old, new) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            moved_key = rename_token(key, old, new)
            if moved_key in result:
                raise RenameRefused(f"JSON key collision: {moved_key}")
            result[moved_key] = (rename_token(item, old, new, designation=True)
                                if key in {"designation", "purpose", "goal"} and isinstance(item, str) else rename_json(item, old, new))
        return result
    return value


@dataclass
class RowChange:
    table: Table
    before: dict[str, Any]
    values: dict[str, Any]


@dataclass(frozen=True)
class RenameReport:
    old: str
    new: str
    applied: bool
    counts: dict[str, int]

    def format(self) -> str:
        mode = "APPLIED" if self.applied else "DRY RUN"
        return "\n".join([f"{mode}: {self.old} → {self.new}", "table | rows",
                          *(f"{name} | {count}" for name, count in sorted(self.counts.items()))])


def _changes(table: Table, row: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    if table.name == "outbox_messages" and row["published_at"] is not None:
        return {}
    if table.name == "operations_job_marks" and row["key"] != "tc_name_queue":
        return {}
    values = {}
    for column, policy in POLICIES[table.name].items():
        value = row[column]
        if value is None:
            continue
        if table.name == "operations_job_marks":
            try:
                value = json.loads(value)
            except (ValueError, TypeError) as error:
                raise RenameRefused("Invalid tc_name_queue JSON") from error
        if policy == "j":
            moved = rename_json(value, old, new)
        elif policy in {"p", "k"}:
            moved = (normalize_project_number(new) if policy == "k" else new) if normalize_project_number(value) == normalize_project_number(old) else value
        else:
            moved = rename_token(value, old, new, designation=policy == "d")
        if moved != value:
            values[column] = json.dumps(moved, ensure_ascii=False) if table.name == "operations_job_marks" else moved
    if table.name == "project_tasks" and "project" in values and row.get("status") == "open":
        from app.workflows.arbiter import KINDS

        kind = KINDS.get(row.get("kind"))
        if kind is not None and row.get("title") == kind.text(row["project"], row["item_index"]):
            values["title"] = kind.text(new, row["item_index"])
    return values


def _inventory(session: Session) -> MetaData:
    # Register owners for standalone callers too; importing does not create/alter the schema.
    from app import database as _database  # noqa: F401
    from app.modules.production import baseline_infrastructure as _baselines  # noqa: F401
    from app.modules.techcards import assembly_drafts_infrastructure as _assembly_drafts  # noqa: F401
    from app.modules.techcards import generator_drafts_infrastructure as _generator_drafts  # noqa: F401
    from app.modules.warehouse import label_jobs as _label_jobs, label_templates as _label_templates  # noqa: F401

    metadata = MetaData()
    metadata.reflect(bind=session.connection())
    # SQLite reflection loses SQLAlchemy's UUID type (it sees CHAR(32)); preserve owner adapters.
    for table in metadata.tables.values():
        owner = Base.metadata.tables.get(table.name)
        if owner is not None:
            for column in table.c:
                if column.name in owner.c:
                    column.type = owner.c[column.name].type
    missing = _TABLES - metadata.tables.keys()
    if missing:
        raise RenameRefused("Schema missing tables: " + ", ".join(sorted(missing)))
    for table in metadata.tables.values():
        for foreign_key in table.foreign_keys:
            if foreign_key.column.table.name in {"production_plan_events", "production_work_time_log", "production_plan_baseline_lines"}:
                raise RenameRefused(f"Cannot replace referenced history: {table.name}.{foreign_key.parent.name}")
        for column in table.c:
            if column.name in {"project", "project_reference", "project_key"} or column.name.endswith("_project_key"):
                if column.name not in POLICIES.get(table.name, {}):
                    raise RenameRefused(f"Uninventoried project column: {table.name}.{column.name}")
        if table.name in POLICIES and (set(POLICIES[table.name]) - set(table.c.keys())):
            raise RenameRefused(f"Schema mismatch: {table.name}")
    return metadata


def _lock(session: Session, old: str, new: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET LOCAL lock_timeout = '10s'"))
        for project in sorted({normalize_project_number(old), normalize_project_number(new)}):
            lock_project_run(session, project)
        quote = session.get_bind().dialect.identifier_preparer.quote
        session.execute(text("LOCK TABLE " + ", ".join(quote(name) for name in sorted(_TABLES)) + " IN SHARE ROW EXCLUSIVE MODE"))
    else:
        raise RenameRefused("Only PostgreSQL and SQLite are supported")


def _plan(session: Session, metadata: MetaData, old: str, new: str) -> list[RowChange]:
    changes: list[RowChange] = []
    found = False
    for name in sorted(POLICIES):
        table = metadata.tables[name]
        for row in session.execute(select(table)).mappings():
            row = dict(row)
            if _changes(table, row, new, old):
                raise RenameRefused(f"NEW already exists: {name}")
            if name == "techcard_kitsets" and normalize_project_number(row["project"]) == normalize_project_number(old):
                found = True
            values = _changes(table, row, old, new)
            if values:
                if "version" in table.c:
                    values["version"] = row["version"] + 1
                if name == "techcard_projects":
                    values["derived_hash"] = None
                if name == "techcard_generator_drafts":
                    values["build_fingerprint"] = ""
                if name == "requests_calc_compositions":
                    from app.modules.requests.composition import CompositionSeed

                    values["content_hash"] = CompositionSeed(
                        values.get("project", row["project"]), row["source_id"], values.get("rows", row["rows"]),
                    ).digest
                changes.append(RowChange(table, row, values))
    if not found:
        raise RenameRefused("OLD has no kitset card")
    internal = metadata.tables["purchasing_internal_projects"]
    if any(normalize_project_number(value) == normalize_project_number(new) for value in session.scalars(select(internal.c.number))):
        raise RenameRefused("NEW already exists: purchasing_internal_projects")
    # If only a child article changes, stale aggregate editors must still be refused.
    for child, parent, fk in (("fulfillment_lines", "fulfillment_requests", "request_id"),
                               ("shipment_lines", "shipments", "shipment_id")):
        ids = {change.before[fk] for change in changes if change.table.name == child}
        changed_ids = {change.before["id"] for change in changes if change.table.name == parent}
        table = metadata.tables[parent]
        for row in session.execute(select(table).where(table.c.id.in_(ids - changed_ids))).mappings():
            changes.append(RowChange(table, dict(row), {"version": row["version"] + 1}))
    return changes


def _labels(session: Session, metadata: MetaData, changes: list[RowChange], old: str, new: str) -> list[RowChange]:
    """Prepare rasters before writing, using the same transaction and a view of the new identity."""
    from app.modules.warehouse.infrastructure import SqlAlchemyLabelSettings, SqlAlchemyWarehouseRepository
    from app.modules.warehouse.labels_application import PrintSettings, lot_layout_in, render_builtin, tspl_job
    from app.modules.warehouse.label_templates import stored_template_in
    from app.modules.warehouse.labels.template import format_key
    from app.modules.warehouse.labels.render import render
    from app.workflows.finished_goods_label import act_label_data
    from contextlib import nullcontext

    lots = {change.before["id"] for change in changes if change.table.name == "warehouse_lots"}
    goods = {change.before["id"] for change in changes if change.table.name == "finished_goods"}
    acts = metadata.tables["finished_goods_acts"]
    act_grns = dict(session.execute(select(acts.c.id, acts.c.grn).where(acts.c.finished_good_id.in_(goods))).all())
    table = metadata.tables["warehouse_label_jobs"]
    jobs = list(session.execute(select(table).where(table.c.lot_id.in_(lots | act_grns.keys()),
                                                    table.c.status.in_(("pending", "taken")))).mappings())
    if any(row["status"] == "taken" for row in jobs):
        raise RenameRefused("Affected label job is taken; wait for the print agent to finish")
    if not jobs:
        return []
    settings = SqlAlchemyLabelSettings(session).load() or PrintSettings()
    result = []
    for row in jobs:
        lot_id = row["lot_id"]
        if lot_id in lots:
            repo = SqlAlchemyWarehouseRepository(session)
            lot = repo.get(lot_id)
            lot = replace(lot, article=rename_token(lot.article, old, new),
                          purpose=rename_token(lot.purpose, old, new, designation=True))
            layout = lot_layout_in(session, lot, repo.received_at(lot_id), settings)
        else:
            label = act_label_data(lambda: nullcontext(session), act_grns[lot_id])
            label = replace(label, project=new, data=replace(label.data, sku=f"{new} {label.item_index}"))
            stored = stored_template_in(session, label.content.kind, format_key(settings.width_mm, settings.height_mm))
            layout = render(stored.template, label.content, settings) if stored and stored.template else render_builtin(label.content, settings)
        if layout.skipped:
            raise RenameRefused("Cannot render an affected label: " + ", ".join(layout.skipped))
        result.append(RowChange(table, dict(row), {"tspl": tspl_job(layout, settings, row["copies"])}))
    return result


def rename_project(engine: Engine, old: str, new: str, *, apply: bool = False,
                   now: datetime | None = None) -> RenameReport:
    old, new = old.strip(), new.strip()
    if not project_number_valid(new):
        raise RenameRefused("NEW fails project_number_valid")
    if not old or normalize_project_number(old) == normalize_project_number(new):
        raise RenameRefused("OLD and NEW must be different projects")
    now = now or moscow_now()
    with Session(engine) as session:
        # SQLite must obtain its write reservation before schema/inventory reads.
        if engine.dialect.name == "sqlite":
            session.execute(text("PRAGMA foreign_keys=ON"))
            session.execute(text("BEGIN IMMEDIATE"))
        metadata = _inventory(session)
        if engine.dialect.name != "sqlite":
            _lock(session, old, new)
        changes = _plan(session, metadata, old, new)
        changes += _labels(session, metadata, changes, old, new)
        counts = {name: 0 for name in POLICIES}
        counts.update({"warehouse_label_jobs": 0, "shipments": 0})
        for change in changes:
            counts[change.table.name] += 1
        counts["audit_events (insert)"] = 1
        counts["production_plan_events (insert)"] = 1
        if apply:
            for change in changes:
                table, before = change.table, change.before
                where = [column == before[column.name] for column in table.primary_key.columns]
                if "version" in table.c:
                    where.append(table.c.version == before["version"])
                if table.name in {"production_plan_events", "production_work_time_log", "production_plan_baseline_lines"}:
                    affected = session.execute(delete(table).where(and_(*where))).rowcount
                    session.execute(insert(table).values(**{**before, **change.values}))
                else:
                    affected = session.execute(update(table).where(and_(*where)).values(**change.values)).rowcount
                if affected != 1:
                    raise RenameRefused(f"Concurrent edit: {table.name}")
            actor = Actor(id=uuid4(), name="администратор (CLI)", department="")
            message = f"проект переименован {old} → {new}"
            SqlAlchemyAuditRepository(session).add(AuditEvent(
                id=uuid4(), aggregate_id=uuid5(NAMESPACE_URL, f"kitset:{new}"),
                event_type="techcards.project_renamed", actor=actor, occurred_at=now,
                details={"project": new, "old_project": old, "message": message},
            ))
            from app.modules.production.infrastructure import SqlAlchemyPlanEventRepository
            from app.modules.production.plan import PlanEvent

            SqlAlchemyPlanEventRepository(session).add(PlanEvent(
                project=new, kind="Комментарий", who=actor.name, text=message, occurred_at=now,
            ))
            session.commit()
        return RenameReport(old, new, apply, counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Set ERP_DATABASE_URL explicitly", file=sys.stderr)
        return 2
    from app.database import build_engine

    engine = build_engine(url)
    try:
        print(rename_project(engine, args.old, args.new, apply=args.apply).format())
    except (RenameRefused, SQLAlchemyError) as error:
        # Avoid exposing credentials/SQL parameter payloads from database exceptions.
        print(str(error) if isinstance(error, RenameRefused) else f"Database failure: {type(error).__name__}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
