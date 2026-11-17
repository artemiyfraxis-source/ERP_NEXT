"""Group existing unstarted preparation PFs; default CLI mode only previews."""

import argparse
import json
import os
import sys
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.resource_claims import ResourceConflict
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Группировка запущенных, не начатых ПФ подготовки")
    parser.add_argument("--project", required=True)
    parser.add_argument("--task-id", action="append", type=UUID, dest="task_ids")
    parser.add_argument("--refresh-etk", action="store_true",
                        help="обновить из ЭТК все не начатые группы проекта (с --apply; по умолчанию только показать)")
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
        result = (refresh_etk if args.refresh_etk else run)(args.project, factory=build_production_uow_factory(engine),
            techcards_factory=build_techcards_uow_factory(engine),
            actor=Actor(uuid5(NAMESPACE_URL, "erp-next:cutting-group-existing"), "Группировка запущенных ПФ", "ERP"),
            now=moscow_now(), dry_run=not args.apply, **extra)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
