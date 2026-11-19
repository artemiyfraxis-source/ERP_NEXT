"""Group existing unstarted preparation PFs; default CLI mode only previews."""

import argparse
from collections import Counter
import json
import os
import sys
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.resource_claims import ResourceConflict
from app.modules.production.cutting import recipe_kind
from app.modules.production.cutting_application import describe_group, group_existing, plan_etk_refresh, refresh_group_etk
from app.modules.production.domain import ProductionValidationError
from app.modules.production.plan import normalize_project_number
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import moscow_now
from app.shared.identity import Actor


def run(project, *, factory, techcards_factory, actor, now, task_ids=None, dry_run=True):
    def load_card(task):
        with techcards_factory() as uow:
            card = uow.techcards.get(task.techcard_id) if task.techcard_id else None
            if card is None or card.status is not TechcardStatus.ISSUED or not card.document:
                raise ProductionValidationError("Нет выданной ЭТК задания")
            if normalize_project_number(card.project) != normalize_project_number(task.project) or card.item_index != task.item:
                raise ProductionValidationError("ЭТК не соответствует проекту и КБ задания")
            if card.revision != task.techcard_revision:
                raise ProductionValidationError("Ревизия ЭТК изменилась после запуска: сначала согласуйте маршрут задания")
            return card.document, card.version

    return group_existing(project, task_ids, dry_run, load_card, actor, now, factory)


def refresh_etk(project, *, factory, techcards_factory, actor, now, dry_run=True, task_ids=None):
    """Re-pin all unstarted groups of the project to their current ETK (one transaction per group)."""
    from app.modules.production.api import cutting_refresh_cards

    key = normalize_project_number(project)
    if not key:
        raise ProductionValidationError("Укажите проект")
    result = {"project": key, "dry_run": dry_run, "refreshed": [], "current": [], "refused": [], "skipped": []}
    with factory() as uow:
        groups = [(g.id, g.number, g.state) for g in uow.cutting.list() if normalize_project_number(g.project_key) == key]
        if task_ids is not None:
            wanted = set(task_ids)
            groups = [item for item in groups if wanted & {m.task_id for m in uow.cutting.members(item[0], active=False)}]
    for group_id, number, state in groups:
        identity = {"group_id": str(group_id), "number": number}
        if state != "open":
            result["skipped"].append({**identity, "reason": "Группа начата или отменена"})
            continue
        try:
            cards = cutting_refresh_cards(group_id, factory, techcards_factory)
            with factory() as uow:
                group = uow.cutting.get(group_id)
                steps, problems = plan_etk_refresh(group, uow.cutting.members(group_id), cards, uow)
                stale = [task.item for member, task, entry, _r in steps if task.techcard_revision != entry[1]
                         or member.row_snapshot.get("validated_card_version") != entry[3]]
                # A key computed by older code (e.g. equipment names in the key) is stale too: refresh adopts the new key.
                if not stale and steps and any(recipe.key != group.recipe_key for _m, _t, _e, recipe in steps):
                    stale = ["ключ рецепта"]
                version = group.version
            if problems:
                raise ProductionValidationError("; ".join(problems))
            if not stale:
                result["current"].append(identity)
            elif dry_run:
                result["refreshed"].append({**identity, "items": stale})
            else:
                refresh_group_etk(group_id, version, cards, actor, now, factory())
                result["refreshed"].append({**identity, "items": stale})
        except (ProductionValidationError, ConcurrentProductionUpdate, ResourceConflict, PermissionError,
                KeyError, StaleDataError, IntegrityError, HTTPException) as error:
            reason = getattr(error, "detail", None) or str(error) or type(error).__name__
            result["refused"].append({**identity, "reason": str(reason)})
    return result


def _counts(uow, key, task_ids):
    """Row counts the restructure must keep (batches, jobs, mark events, serials) or may change (PFs, groups)."""
    from sqlalchemy import func, select

    from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
    from app.modules.production.domain import MARKS_ISSUED_EVENT, MARKS_RELEASED_EVENT, RESTORED_EVENT
    from app.modules.production.infrastructure import ProductionOperationRow, ProductionSerialRow
    from app.modules.warehouse.label_jobs import LabelJobRow
    from app.shared.persistence import AuditEventRow
    from app.workflows.marking_progress import task_batches

    session = uow.session
    ids = list(task_ids)
    groups = [g for g in session.scalars(select(CuttingGroupRow)) if normalize_project_number(g.project_key) == key]
    group_ids = [g.id for g in groups]
    members = list(session.scalars(select(CuttingMemberRow).where(CuttingMemberRow.group_id.in_(group_ids)))) if group_ids else []
    batches = task_batches(session, ids) if ids else []

    def count(statement):
        return session.scalar(statement) or 0

    by = lambda values: dict(sorted(Counter(values).items()))  # noqa: E731
    return {
        "tasks": len(ids),
        "operations": count(select(func.count()).select_from(ProductionOperationRow).where(
            ProductionOperationRow.task_id.in_(ids))) if ids else 0,
        "serials": count(select(func.count()).select_from(ProductionSerialRow).where(
            ProductionSerialRow.task_id.in_(ids))) if ids else 0,
        "groups": by(g.state for g in groups),
        "groups_by_kind": by(recipe_kind(g.recipe) for g in groups if g.state != "cancelled"),
        "members": by(m.state for m in members),
        "marking_batches": len(batches),
        "label_jobs": count(select(func.count()).select_from(LabelJobRow).where(
            LabelJobRow.batch_id.in_([b.id for b in batches]))) if batches else 0,
        "mark_events": count(select(func.count()).select_from(AuditEventRow).where(
            AuditEventRow.aggregate_id.in_(ids),
            AuditEventRow.event_type.in_((MARKS_ISSUED_EVENT, MARKS_RELEASED_EVENT, RESTORED_EVENT)))) if ids else 0,
    }


def _label_check(uow, tasks, documents):
    from app.workflows.marking_progress import printed_counts, serial_range_of, task_progress

    counts = printed_counts(uow.session, [task.id for task in tasks])
    return [{key: row[key] for key in ("task_number", "item", "quantity", "serial_first", "serial_last", "printed",
                                       "remaining", "printed_serials")}
            for row in (task_progress(task, documents.get(task.id), serial_range_of(task, uow), counts) for task in tasks)]


def restructure(project, *, factory, techcards_factory, actor, now, dry_run=True, task_ids=None):
    """Rebuild unstarted tasks on their reissued ETK and regroup PFs by kind (one transaction; dry run rolls back)."""
    from app.modules.production.cutting_application import restructure_project
    from app.modules.production.domain import ProductionStatus
    from app.modules.techcards.etk_read import issued_card_for_item

    key = normalize_project_number(project)
    if not key:
        raise ProductionValidationError("Укажите проект")
    with factory() as uow:
        tasks = [t for t in uow.production.for_project(key) if t.status is ProductionStatus.RUNNING
                 and (task_ids is None or t.id in set(task_ids))]
    cards = {}
    with techcards_factory() as techcards:
        for task in tasks:
            card = issued_card_for_item(techcards, task.project, task.item)
            if card is None or card.status is not TechcardStatus.ISSUED or not card.document:
                cards[task.id] = "нет выданной ЭТК"
            else:
                cards[task.id] = (card.id, card.revision, card.document, card.version)
    documents = {identifier: entry[2] for identifier, entry in cards.items() if not isinstance(entry, str)}
    ids = [task.id for task in tasks]
    with factory() as uow:
        before = _counts(uow, key, ids)
        labels_before = _label_check(uow, [uow.production.get(i) for i in ids], documents)
        result = restructure_project(key, cards, actor, now, uow, task_ids=task_ids)
        uow.session.flush()
        after = _counts(uow, key, ids)
        labels_after = _label_check(uow, [uow.production.get(i) for i in ids], documents)
        if not dry_run:
            uow.commit()
    kept = ("tasks", "serials", "marking_batches", "label_jobs", "mark_events")
    result.update(dry_run=dry_run, counts={"before": before, "after": after},
                  invariants={name: before[name] == after[name] for name in kept},
                  labels=labels_after, labels_unchanged=[
                      (row["printed"], row["remaining"]) for row in labels_before] == [
                      (row["printed"], row["remaining"]) for row in labels_after],
                  printed_one_per_kb=sum(1 for row in labels_after if row["printed"] == 1))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Группировка запущенных, не начатых ПФ подготовки")
    parser.add_argument("--project", required=True)
    parser.add_argument("--task-id", action="append", type=UUID, dest="task_ids")
    parser.add_argument("--refresh-etk", action="store_true",
                        help="обновить из ЭТК все не начатые группы проекта (с --apply; по умолчанию только показать)")
    parser.add_argument("--restructure", action="store_true",
                        help="перестроить не начатые задания по перевыпущенной ЭТК на месте и разложить ПФ по группам "
                             "резки/маркировки/трубок (с --apply; по умолчанию — пробный прогон с откатом)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="создать группы (одна транзакция на группу)")
    mode.add_argument("--dry-run", action="store_true", help="только показать план (по умолчанию)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_production_uow_factory, build_techcards_uow_factory

    engine = build_engine(url)
    try:
        extra = {"task_ids": args.task_ids}
        if args.restructure and args.refresh_etk:
            parser.error("--restructure и --refresh-etk — разные режимы")
        result = (restructure if args.restructure else refresh_etk if args.refresh_etk else run)(args.project, factory=build_production_uow_factory(engine),
            techcards_factory=build_techcards_uow_factory(engine),
            actor=Actor(uuid5(NAMESPACE_URL, "erp-next:cutting-group-existing"), "Группировка запущенных ПФ", "ERP"),
            now=moscow_now(), dry_run=not args.apply, **extra)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
