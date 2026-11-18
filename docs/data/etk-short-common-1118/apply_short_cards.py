"""Saved 708К. coax inputs switch to the short common cards (свод инструкций 3/5, 18.11), one guarded transaction.

    # 1. dry run (default): which cards change, their input hashes and what the refresh makes of each
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_short_cards.py --plan-out plan.json
    # 2. apply exactly that plan and issue the revisions in the same transaction; rollback file written before commit
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_short_cards.py --expect plan.json --rollback-out short-rollback.json --reissue --apply
    # rollback (dry run without --apply)
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_short_cards.py --rollback short-rollback.json [--reissue] [--apply]

Only `techcard_projects.inputs` (+ version, updated_at) of project 708К. changes: `coax.shortCards = true` on every
saved coax configuration («Коакс», mode coax) that lacks it. The builder (etk_builder.coax_short_steps) then renders the
common operations — cut with strip, label print, TUT cut/put-on, label install, TUT shrink, VSWR — as short resolved
cards; connector-end operations, quantities, parts, marks, times, equipment, documents of other projects and launched
tasks are untouched (launched tasks keep their pinned revision). Harness cards of the project (mode ≠ coax) are kept.
Hand-edited operations stay hand-edited: the refresh keeps overrides, a conflicting one is reported, never overwritten.

Without --reissue the refresh worker issues the revisions after commit (the document changes, so it does);
--reissue issues them here through the refresh's own evaluate/save path. --apply refuses a card whose document would
not change («inputs_only»): the flag must never sit silently on a card the reviewer did not see revised.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.etk_builder import SHORT_CARDS
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs


def _sibling(folder: str, name: str):
    path = Path(__file__).resolve().parents[1] / folder / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_{folder.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Project scope, row locking, hashes, preview and in-transaction reissue: the reviewed 18.11 input migration.
INPUTS = _sibling("operation-templates-connectors-1118", "migrate_inputs")
AUTHOR = "Свод инструкций — общие карты ЭТК 18.11.2026"
REASON = "Короткие общие карты 708К. (свод инструкций 17.11.2026): резка, этикетки, ТУТ, усадка, КСВН"
PROJECT = INPUTS.PROJECT
OUTCOMES = ("changed", "revised", "inputs_only", "kept")


def actor() -> Actor:
    return Actor(id=UUID(int=0), name=AUTHOR, department="Технолог")


def migrate_config(config):
    """(copy, reason-if-skipped). Only a coax configuration gets the flag; everything else stays byte-identical."""
    new = deepcopy(config)
    if not isinstance(new, dict) or new.get("mode") != "coax" or not isinstance(new.get("coax"), dict):
        return new, "не коакс-сборка"
    if not isinstance(new.get("ops"), list) or not new["ops"]:
        return new, "нет операций"
    if new["coax"].get(SHORT_CARDS) is True:
        return new, "уже короткие карты"
    new["coax"][SHORT_CARDS] = True
    return new, ""


def _event(uow, who, now, row, event_type):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=event_type, actor=who, occurred_at=now,
                             details={"reason": REASON}))


def _statuses(uow, refs, now, rows, reissue):
    return (INPUTS._reissue if reissue else INPUTS._preview)(uow, refs, now, rows)


def apply_short_cards(uow, *, who, now, expect=None, refs=None, reissue=False):
    """Open UoW only; never commits. `expect` — the dry-run plan: exactly these cards, versions and hashes, or nothing."""
    result = {key: [] for key in OUTCOMES}
    plan, rollback, changed_rows = [], [], []
    for row in INPUTS._rows(uow):
        label = f"{row.item_index} [{row.id}]"
        inputs, skipped = migrate_config(row.inputs if isinstance(row.inputs, dict) else {})
        if skipped:
            result["kept"].append(f"{label}: {skipped}")
            continue
        before, after = INPUTS.inputs_hash(row.inputs), INPUTS.inputs_hash(inputs)
        plan.append({"id": str(row.id), "item_index": row.item_index, "version": row.version,
                     "before": before, "after": after})
        rollback.append({"id": str(row.id), "item_index": row.item_index, "after": after, "inputs": deepcopy(row.inputs)})
        row.inputs, row.version, row.updated_at = inputs, row.version + 1, now
        changed_rows.append(row)
        result["changed"].append(label + ("; есть ручные правки операций — остаются ручными" if INPUTS._manual(row) else ""))
        _event(uow, who, now, row, "techcards.short_common_cards")
    if refs is not None and changed_rows:
        statuses = _statuses(uow, refs, now, changed_rows, reissue)
        for row in changed_rows:
            visible = statuses[row.id] in {"revised", "refreshed"}
            result["revised" if visible else "inputs_only"].append(f"{row.item_index} [{row.id}]: {statuses[row.id]}")
        if expect is not None and result["inputs_only"]:
            raise ValueError(f"ЭТК не изменятся, только входные данные: {', '.join(result['inputs_only'])} — разобрать до записи")
    if expect is not None:
        expected = {item["id"]: item for item in expect.get("cards", [])}
        actual = {item["id"]: item for item in plan}
        if set(expected) != set(actual):
            raise ValueError(f"Состав карт изменился после пробного прогона: лишние {sorted(set(actual) - set(expected))}, "
                             f"нет {sorted(set(expected) - set(actual))}")
        for card_id, item in actual.items():
            if any(item[key] != expected[card_id][key] for key in ("version", "before", "after")):
                raise ValueError(f"Карта {item['item_index']} [{card_id}] изменилась после пробного прогона")
    if plan and not reissue:
        INPUTS._enqueue(uow, now, {"reason": REASON, "cards": [item["item_index"] for item in plan]})
    uow.session.flush()
    return result, {"project": PROJECT, "cards": plan}, {"project": PROJECT, "cards": rollback}


def rollback_short_cards(data, uow, *, who, now, refs=None, reissue=False):
    """Restore saved inputs only where they are still exactly what this script wrote; otherwise nothing."""
    result = {key: [] for key in OUTCOMES}
    rows = {str(row.id): row for row in INPUTS._rows(uow)}
    restored = []
    for item in data.get("cards", []):
        row = rows.get(item["id"])
        if row is None or INPUTS.inputs_hash(row.inputs) != item["after"] or INPUTS.inputs_hash(item["inputs"]) == item["after"]:
            raise ValueError(f"Карта {item.get('item_index')} [{item['id']}] изменилась после записи — откат не выполнен")
        row.inputs, row.version, row.updated_at = deepcopy(item["inputs"]), row.version + 1, now
        restored.append(row)
        result["changed"].append(f"{row.item_index} [{row.id}]: входные данные восстановлены")
        _event(uow, who, now, row, "techcards.short_common_cards_rolled_back")
    if refs is not None and restored:
        statuses = _statuses(uow, refs, now, restored, reissue)
        for row in restored:
            visible = statuses[row.id] in {"revised", "refreshed"}
            result["revised" if visible else "inputs_only"].append(f"{row.item_index} [{row.id}]: {statuses[row.id]}")
    if restored and not reissue:
        INPUTS._enqueue(uow, now, {"reason": f"Откат: {REASON}", "cards": [row.item_index for row in restored]})
    uow.session.flush()
    return result


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон, всё откатывается)")
    parser.add_argument("--plan-out", type=Path, help="записать план пробного прогона (карты, версии, хеши)")
    parser.add_argument("--expect", type=Path, help="план пробного прогона: применить ровно его")
    parser.add_argument("--rollback-out", type=Path, help="куда записать исходные inputs до записи")
    parser.add_argument("--rollback", type=Path, help="файл отката, записанный --rollback-out")
    parser.add_argument("--reissue", action="store_true", help="выпустить ревизии ЭТК в этой же транзакции")
    args = parser.parse_args(argv)
    if args.apply and not args.rollback and not (args.expect and args.rollback_out):
        parser.error("--apply требует --expect (план пробного прогона) и --rollback-out")
    if args.rollback_out and args.rollback_out.exists():
        parser.error(f"{args.rollback_out} уже есть — не перезаписываю")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sessions = sessionmaker(bind=build_engine(os.environ["ERP_DATABASE_URL"]), expire_on_commit=False, class_=Session)
    now = moscow_now()
    refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))  # own connection, read before the transaction
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        if args.rollback:
            INPUTS._print("Короткие общие карты (откат):", rollback_short_cards(
                json.loads(args.rollback.read_text(encoding="utf-8")), uow, who=actor(), now=now, refs=refs,
                reissue=args.reissue))
        else:
            expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
            result, plan, rollback = apply_short_cards(uow, who=actor(), now=now, expect=expect, refs=refs,
                                                       reissue=args.reissue)
            INPUTS._print("Короткие общие карты 708К.:", result)
            if args.plan_out:
                args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if args.apply:
                with args.rollback_out.open("x", encoding="utf-8", newline="\n") as handle:  # never overwrite
                    handle.write(json.dumps(rollback, ensure_ascii=False, indent=1))
        if args.apply:
            uow.commit()
            print("COMMITTED" + (" — ревизии выпущены" if args.reissue else " — ревизии выпустит обновление ЭТК"))
        else:
            print("dry run — rolled back (включая журнал, ревизии и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
