"""Group existing unstarted preparation PFs; default CLI mode only previews."""

import argparse
import json
import os
import sys
from uuid import UUID, NAMESPACE_URL, uuid5

from app.modules.production.cutting_application import group_existing
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Группировка запущенных, не начатых ПФ подготовки")
    parser.add_argument("--project", required=True)
    parser.add_argument("--task-id", action="append", type=UUID, dest="task_ids")
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
        result = run(args.project, factory=build_production_uow_factory(engine),
            techcards_factory=build_techcards_uow_factory(engine),
            actor=Actor(uuid5(NAMESPACE_URL, "erp-next:cutting-group-existing"), "Группировка запущенных ПФ", "ERP"),
            now=moscow_now(), task_ids=args.task_ids, dry_run=not args.apply)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
