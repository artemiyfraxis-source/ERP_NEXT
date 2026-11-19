"""Own card for the crimped centre contact of ordinary coax (свод инструкций 4/5, 19.11), one guarded transaction.

    # 1. dry run (default): the card, its БД.ОП row, which 708К. cards move and what the refresh makes of each
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_pin_coax.py --plan-out plan.json
    # 2. apply exactly that plan and issue the revisions here; the rollback file is written before the commit
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_pin_coax.py --expect plan.json --rollback-out pin-rollback.json --reissue --apply
    # rollback of the saved routes (dry run without --apply); the card and its row stay — nothing names them then
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_pin_coax.py --rollback pin-rollback.json [--reissue] [--apply]

1. The card PRS_PIN_coax «Обжим центрального контакта» (payload.json, same id as the ERP library seed) and its БД.ОП
   row (layout of PRS_SLEEVE_coax, no times, programs or equipment: the pin tool is not set) — through the 18.11
   connector-card delivery (author/hash/human-edit guards, a seeded identical card is «unchanged»).
2. Saved inputs of project 708К. only (КБ11–14 — 73403-6270 crimped pin): every `coaxPinCrimp` operation still on the
   shield-crimp card PRS_SLEEVE_coax moves to PRS_PIN_coax — templateId, opNum and the card's own times (none yet);
   the stale equipment links of the shield press go (the refresh rebuilds links per card). Exactly what the generator
   now writes for the same product (tcGenFakra.test.ts «КБ11: the crimped centre contact…»).
   The shield crimp stays its own PRS_SLEEVE_coax operation with HEX per end; the order of put-on parts is kept: the
   TUT and the crimp tube pass over the attached pin (tube bore ≥ braid Ø 1,8 mm > dielectric Ø 1,52 mm ≥ pin) and
   are on the cable before either housing, so passability does not require moving them.
Cards with hand-edited operations or a hand-cut ПФ scheme are reported «protected» and not touched; other projects are
only counted. Quantities, parts, marks, documents of other projects and launched tasks are untouched (launched tasks
keep their pinned revision). --apply refuses a card that would not get a new revision («inputs_only»).
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

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.infrastructure import ProjectTechcardRow, SqlAlchemyTechcardsUnitOfWork
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs

AUTHOR = "Свод инструкций — обжим центрального контакта 19.11.2026"
REASON = "Своя карта обжима центрального контакта PRS_PIN_coax вместо карты обжима экрана (свод инструкций 708К., 4/5)"
PIN, SHIELD = "PRS_PIN_coax", "PRS_SLEEVE_coax"
TIMES = ("tPrep", "tOp", "tMachine")
OUTCOMES = ("changed", "revised", "inputs_only", "protected", "kept", "other_projects")
PAYLOAD = Path(__file__).with_name("payload.json")


def _sibling(folder: str, name: str):
    # A fresh module instance: the settings below never leak into the 18.11 script itself.
    path = Path(__file__).resolve().parents[1] / folder / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_{folder.replace('-', '_')}_pin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Card delivery guards (trusted authors, old-content hashes, БД.ОП row from an analogue) and the 708К. input migration
# machinery (project scope, row locks, hashes, preview and in-transaction reissue): the reviewed 18.11 scripts.
TEMPLATES = _sibling("operation-templates-connectors-1118", "apply_templates")
TEMPLATES.CONNECTOR_CODES = frozenset({PIN})
TEMPLATES.TRUSTED_AUTHORS = {*TEMPLATES.TRUSTED_AUTHORS, AUTHOR}
TEMPLATES.REASON = REASON
INPUTS = _sibling("operation-templates-connectors-1118", "migrate_inputs")
PROJECT = INPUTS.PROJECT


def actor() -> Actor:
    return Actor(id=UUID(int=0), name=AUTHOR, department="Технолог")


def migrate_config(config, code_of, pin_id: str, times: dict[str, str]):
    """(copy, changes, reason-if-skipped). Only crimped-pin operations bound to the shield card change."""
    new = deepcopy(config)
    if not isinstance(new, dict) or new.get("mode") != "coax" or not isinstance(new.get("ops"), list):
        return new, [], "не коакс-сборка"
    changes = []
    for op in new["ops"]:
        if not isinstance(op, dict) or op.get("type") != "coaxPinCrimp" or code_of(op) != SHIELD:
            continue
        op.update(templateId=pin_id, opNum=PIN, **times)
        op.pop("equipmentTools", None)
        changes.append(f"{op.get('opKey')}: {SHIELD} → {PIN}")
    return new, changes, "" if changes else "нет обжима пина картой экрана"


def _pin_binding(uow):
    """(template id, the БД.ОП row's times) of the delivered card — what the generator binds and copies."""
    cards = [card for card in uow.operation_cards.list() if card.code == PIN]
    if len(cards) != 1:
        raise ValueError(f"Карта {PIN}: нужна ровно одна, найдено {len(cards)}")
    row = TEMPLATES._rows(uow).get(PIN)
    if row is None:
        raise ValueError(f"Нет строки БД.ОП {PIN}")
    return cards[0].source_id, {key: str(row.fields.get(key) or "") for key in TIMES}


def _other_projects(uow, code_of) -> list[str]:
    """Read-only: cards of other projects whose crimped pin still uses the shield card (a generator rebuild fixes
    them; this script does not touch them)."""
    found = []
    for row in uow.session.execute(select(ProjectTechcardRow.project, ProjectTechcardRow.item_index, ProjectTechcardRow.inputs)):
        if normalize_project_number(row.project) == PROJECT or not isinstance(row.inputs, dict):
            continue
        if any(isinstance(op, dict) and op.get("type") == "coaxPinCrimp" and code_of(op) == SHIELD
               for op in row.inputs.get("ops") or []):
            found.append(f"{row.project} {row.item_index}")
    return found


def _event(uow, who, now, row, event_type, details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=event_type, actor=who, occurred_at=now,
                             details=details))


def _statuses(uow, refs, now, rows, reissue):
    return (INPUTS._reissue if reissue else INPUTS._preview)(uow, refs, now, rows)


def apply_pin_coax(uow, *, who, now, payload, expect=None, refs=None, reissue=False):
    """Open UoW only; never commits. → (card report, input report, plan, rollback). `expect` — the dry-run plan:
    exactly these cards, versions and hashes, or nothing."""
    cards_report = TEMPLATES.apply_payload(payload, uow, who=who, now=now)
    if cards_report["protected"] or cards_report["drifted"]:
        raise ValueError(f"Карта {PIN} правлена человеком или неизвестного содержания — разобрать до переноса маршрутов")
    pin_id, times = _pin_binding(uow)
    code_of = INPUTS.code_resolver(tuple(uow.operation_cards.list()))
    result = {key: [] for key in OUTCOMES}
    plan, rollback, changed_rows = [], [], []
    for row in INPUTS._rows(uow):
        label = f"{row.item_index} [{row.id}]"
        inputs, changes, skipped = migrate_config(row.inputs if isinstance(row.inputs, dict) else {}, code_of, pin_id, times)
        if skipped:
            result["kept"].append(f"{label}: {skipped}")
            continue
        if INPUTS._manual(row):
            result["protected"].append(f"{label}: ручные правки операций или схемы ПФ — пересобрать в генераторе")
            continue
        before, after = INPUTS.inputs_hash(row.inputs), INPUTS.inputs_hash(inputs)
        plan.append({"id": str(row.id), "item_index": row.item_index, "version": row.version,
                     "before": before, "after": after, "changes": changes})
        rollback.append({"id": str(row.id), "item_index": row.item_index, "after": after, "inputs": deepcopy(row.inputs)})
        row.inputs, row.version, row.updated_at = inputs, row.version + 1, now
        changed_rows.append(row)
        result["changed"].append(f"{label}: {'; '.join(changes)}")
        _event(uow, who, now, row, "techcards.pin_card_migrated", {"changes": changes, "reason": REASON})
    result["other_projects"] = _other_projects(uow, code_of)
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
    return cards_report, result, {"project": PROJECT, "cards": plan}, {"project": PROJECT, "cards": rollback}


def rollback_pin_coax(data, uow, *, who, now, refs=None, reissue=False):
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
        _event(uow, who, now, row, "techcards.pin_card_rolled_back", {"reason": REASON})
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
            INPUTS._print("Обжим центрального контакта (откат):", rollback_pin_coax(
                json.loads(args.rollback.read_text(encoding="utf-8")), uow, who=actor(), now=now, refs=refs,
                reissue=args.reissue))
        else:
            expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
            cards, result, plan, rollback = apply_pin_coax(
                uow, who=actor(), now=now, payload=json.loads(PAYLOAD.read_text(encoding="utf-8")), expect=expect,
                refs=refs, reissue=args.reissue)
            INPUTS._print("Карта PRS_PIN_coax и строка БД.ОП:", cards)
            INPUTS._print("Маршруты 708К.:", result)
            if args.plan_out:
                args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if args.apply:
                with args.rollback_out.open("x", encoding="utf-8", newline="\n") as handle:  # never overwrite
                    handle.write(json.dumps(rollback, ensure_ascii=False, indent=1))
        if args.apply:
            uow.commit()
            print("COMMITTED" + (" — ревизии выпущены" if args.reissue else " — ревизии выпустит обновление ЭТК"))
        else:
            print("dry run — rolled back (включая карту, журнал, ревизии и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
