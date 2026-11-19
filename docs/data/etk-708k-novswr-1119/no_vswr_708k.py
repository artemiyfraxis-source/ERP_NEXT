"""708К.: «Контроль КСВН — не проверять» in the saved generator inputs and a new ETK revision, one guarded transaction.

Owner 19.11.2026 (ОТК «Замеры КСВН», 40 tasks 708К.): «а зачем ты добавил это сюда мы не будем это мерить у нас нет
переходников» — the shop has no FAKRA/HFM/Amphenol adapters for the VNA, so these assemblies are never VSWR-measured.

    # 1. dry run (default): what changes and what the refresh would issue, card by card; nothing is written
    ERP_DATABASE_URL=... PYTHONPATH=backend python no_vswr_708k.py --plan-out plan.json
    # 2. apply exactly that plan and issue the revisions in the same transaction; rollback file written first
    ERP_DATABASE_URL=... PYTHONPATH=backend python no_vswr_708k.py --expect plan.json --rollback-out rollback.json --apply
    # rollback (dry run without --apply): inputs back, a new revision with the check again
    ERP_DATABASE_URL=... PYTHONPATH=backend python no_vswr_708k.py --rollback rollback.json [--apply]

Only `techcard_projects.inputs` (+ version, updated_at) of the «Коакс» cards of project 708К. change, exactly as the
generator saves «Контроль КСВН: не проверять»: `coax.vswrControl` = {mode: none} (with the reason), the form state
`gen_state.S.coax.vswrMode` = none, and the coaxVswr operation leaves `ops` (the builder drops it for «none» anyway).
The ETK revision is issued here through the refresh's own evaluate/save path (journal, automatic ТД issue), with the
owner's reason instead of «обновлено по справочнику»; after it a second evaluate of every changed card must be
«unchanged», otherwise the whole run rolls back. Every change the refresh reports is printed per card (dry run too), and
the run refuses when anything but the «Контроль КСВН» operation itself would change — allowed besides it: the ПФ
scheme losing exactly that operation and the operation before it taking over the finished-product name in its
«Результат» / «Норма времени» rows (same quantities, units, times) — a dry run with such a card ends with an error, not «dry run». Cards with hand-edited operations keep them (the refresh path keeps
overrides); a refresh conflict refuses --apply. Non-coax cards (the Orange Pi harness КБ1) and cards without inputs
(КБ41) are never touched. Production tasks, serials, labels, measurements, cutting groups and stock are not written:
launched tasks keep their pinned revision, and «Замеры КСВН» follows the current revision's «Контроль КСВН»
(`coax_vswr_api.task_norm`, PR 708k-no-vswr-1119).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards import etk_derive
from app.modules.techcards.etk_derive import route_override
from app.modules.techcards.infrastructure import ProjectTechcardRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import TechcardValidationError, operation_key
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number
from app.workflows import etk_refresh
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs

PROJECT = normalize_project_number("708К.")
AUTHOR = "Контроль КСВН 708К. — 19.11.2026"
REASON = "Контроль КСВН снят: нет переходников к ВАЦ — КСВН не измеряется (владелец 19.11.2026)"
CONTROL = {"mode": "none", "every": None, "reason": REASON}
VISIBLE = {"revised", "refreshed"}


def actor() -> Actor:
    return Actor(id=UUID(int=0), name=AUTHOR, department="Технолог")


def inputs_hash(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def without_vswr(inputs):
    """→ (new inputs, changes). Pure. The generator's own save of «Контроль КСВН: не проверять»."""
    inputs = deepcopy(inputs)
    changes = []
    coax = inputs.get("coax") if isinstance(inputs.get("coax"), dict) else None
    if coax is None:
        return inputs, changes
    if (coax.get("vswrControl") or {}).get("mode") != "none":
        changes.append(f"Контроль КСВН: {(coax.get('vswrControl') or {}).get('mode') or 'не задан'} → не проверять")
        coax["vswrControl"] = deepcopy(CONTROL)
    ops = inputs.get("ops") if isinstance(inputs.get("ops"), list) else []
    kept = [op for op in ops if not (isinstance(op, dict) and op.get("type") == "coaxVswr")]
    if len(kept) != len(ops):
        dropped = {str(op.get("opKey") or "") for op in ops if isinstance(op, dict) and op.get("type") == "coaxVswr"}
        inputs["ops"] = kept
        if isinstance(inputs.get("opOrder"), list):
            inputs["opOrder"] = [key for key in inputs["opOrder"] if key not in dropped]
        changes.append("операция «Контроль КСВН» убрана")
    form = ((inputs.get("gen_state") or {}).get("S") or {}).get("coax") if isinstance(inputs.get("gen_state"), dict) else None
    if isinstance(form, dict) and form.get("vswrMode") != "none":
        changes.append(f"форма генератора: {form.get('vswrMode') or 'не задано'} → не проверять")
        form["vswrMode"] = "none"
    return inputs, changes


def _key(op):
    try:
        return operation_key(dict(op))
    except TechcardValidationError:
        return None


def _vswr_keys(*documents) -> set[str]:
    """Keys of the «Контроль КСВН» operations in any of the documents."""
    return {_key(op) for document in documents for op in (document or {}).get("operations") or ()
            if isinstance(op, Mapping) and (str(op.get("code") or "").upper().startswith("CTRL_VSWR")
                                            or op.get("type") == "coaxVswr")}


def _route(document, drop):
    """The ПФ scheme by operation keys, without the `drop` operations."""
    keys = {op.get("position"): _key(op) for op in (document or {}).get("operations") or () if isinstance(op, Mapping)}
    route = (document or {}).get("route")
    blocks = route.get("blocks") if isinstance(route, Mapping) else None
    return [(block.get("key"), block.get("label"), block.get("name"),
             [key for key in (keys.get(position) for position in block.get("ops") or ()) if key not in drop])
            for block in blocks or () if isinstance(block, Mapping)]


_ROWS = {"Результат": "result", "Норма времени": "time"}


def _ops(document) -> dict:
    return {_key(op): op for op in (document or {}).get("operations") or () if isinstance(op, Mapping)}


def unexpected_changes(old, new, changes) -> list[dict]:
    """The refresh changes that are NOT the «Контроль КСВН» operation leaving (or, on rollback, coming back), the ПФ
    scheme losing (gaining) exactly that operation, or the operation before it taking over (handing back) the finished
    product as its «Результат» / «Норма времени» row name — quantities, units and times unchanged. Pure."""
    drop = _vswr_keys(old, new)
    same_route = _route(old, drop) == _route(new, drop)
    before, after = _ops(old), _ops(new)
    products = {row.get("name") for ops in (before, after) for key, op in ops.items() if key in drop
                for row in op.get("result") or () if isinstance(row, Mapping)}

    def handed_over(item) -> bool:
        part = _ROWS.get(item.get("field"))
        op_old, op_new = before.get(item.get("operation_key")), after.get(item.get("operation_key"))
        if part is None or op_old is None or op_new is None:
            return False
        rows_old, rows_new = list(op_old.get(part) or ()), list(op_new.get(part) or ())
        return len(rows_old) == len(rows_new) > 0 and all(
            isinstance(x, Mapping) and isinstance(y, Mapping) and (x.get("name") in products or y.get("name") in products)
            and {**x, "name": y.get("name")} == dict(y) for x, y in zip(rows_old, rows_new))

    return [item for item in changes
            if not (item.get("field") == "Операция" and item.get("operation_key") in drop)
            and not (item.get("field") == "Схема ПФ" and same_route)
            and not handed_over(item)]


def _change_line(item) -> str:
    return f"{item.get('label') or '—'}: {item.get('field')}: «{item.get('old') or ''}» → «{item.get('new') or ''}»"


def _manual(row) -> bool:
    overrides = row.overrides if isinstance(row.overrides, dict) else {}
    return bool(row.manual_operation_keys or overrides.get("operations") or overrides.get("route")
                or route_override(row.document))


def _rows(uow):
    # Legacy rows may lack project_key: read the small identity projection, lock only this project's cards.
    ids = [row.id for row in uow.session.execute(select(ProjectTechcardRow.id, ProjectTechcardRow.project))
           if normalize_project_number(row.project) == PROJECT]
    return list(uow.session.scalars(select(ProjectTechcardRow).where(ProjectTechcardRow.id.in_(ids))
                                    .order_by(ProjectTechcardRow.item_index).with_for_update()))


def _context(uow, refs, now):
    metadata = uow.operation_cards.metadata() or {}
    return etk_refresh.Context(
        refs=refs, templates=tuple(uow.operation_cards.list()), commit=str(metadata.get("source_commit") or ""),
        snapshot=dict(refs.snapshot) if refs.snapshot is not None else None, builder=etk_derive.builder_hash(),
        reason=etk_refresh.REASON_REFERENCE, now=now)


def _issue(uow, refs, now, rows, reason):
    """The refresh's evaluate/save on the new inputs, journaled with `reason`. → {card id: status}; any conflict or a
    card that does not come out as a new revision (or a refreshed draft) — ValueError, nothing is kept."""
    inner = getattr(uow, "inner", uow)
    uow.session.flush()
    ctx = _context(uow, refs, now)
    statuses, refused, details = {}, [], {}
    for row in rows:
        card = inner.techcards.get(row.id)
        updated, revision, result = etk_refresh.evaluate(card, ctx)
        statuses[row.id] = result.status
        details[row.id] = [_change_line(item) for item in result.changes]
        if result.status not in VISIBLE or result.conflicts:
            refused.append(f"{row.item_index} [{row.id}]: {result.status}"
                           + "".join(f"; {item['message']}" for item in result.conflicts))
            continue
        extra = unexpected_changes(card.document, updated.document, result.changes)
        if extra:
            refused.append(f"{row.item_index} [{row.id}]: меняется не только «Контроль КСВН» — "
                           + "; ".join(_change_line(item) for item in extra))
            continue
        if revision is not None:
            text = f"{reason} ({now.strftime('%d.%m.%Y')})"
            revision = replace(revision, reason=text, diff={**(revision.diff or {}), "reason": text})
        etk_refresh.save_result(inner, updated, revision, result, now)
    for row in rows:
        print(f"  {row.item_index} [{row.id}]: {statuses[row.id]}, изменения ЭТК:")
        for line in details[row.id] or ["—"]:
            print(f"    {line}")
    if refused:
        raise ValueError("ЭТК не перевыпущены — выпуск отменён: " + "; ".join(refused))
    uow.session.flush()
    again = [etk_refresh.evaluate(inner.techcards.get(row.id), ctx)[2] for row in rows]
    restless = [f"{item.label}: {item.status}" for item in again if item.status != "unchanged"]
    if restless:
        raise ValueError(f"Повторная проверка не пуста — выпуск отменён: {', '.join(restless)}")
    return statuses


def _event(uow, who, now, row, event_type, details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=event_type, actor=who, occurred_at=now,
                             details=details))


def no_vswr(uow, *, who, now, refs, expect=None):
    """Open UoW only; never commits. → (report, plan, rollback). `expect` — the dry-run plan: exactly these cards,
    versions and input hashes, or nothing. The revisions are issued here either way (a dry run rolls them back)."""
    report = {"changed": [], "kept": [], "issued": []}
    plan, rollback, changed = [], [], []
    for row in _rows(uow):
        label = f"{row.item_index} [{row.id}]"
        if not isinstance(row.inputs, dict) or row.inputs.get("mode") != "coax":
            report["kept"].append(f"{label}: не «Коакс» ({(row.inputs or {}).get('mode') or 'нет входных данных'})")
            continue
        inputs, changes = without_vswr(row.inputs)
        if not changes:
            report["kept"].append(f"{label}: контроль КСВН уже снят")
            continue
        before, after = inputs_hash(row.inputs), inputs_hash(inputs)
        plan.append({"id": str(row.id), "item_index": row.item_index, "version": row.version, "revision": row.revision,
                     "before": before, "after": after, "changes": changes})
        rollback.append({"id": str(row.id), "item_index": row.item_index, "after": after, "inputs": deepcopy(row.inputs)})
        row.inputs = inputs
        row.version += 1
        row.updated_at = now
        changed.append(row)
        report["changed"].append(f"{label} рев. {row.revision}: {'; '.join(changes)}"
                                 + (" (ручные правки операций сохраняются)" if _manual(row) else ""))
        _event(uow, who, now, row, "techcards.vswr_control_removed", {"changes": changes, "reason": REASON})
    if expect is not None:
        expected = {item["id"]: item for item in expect.get("cards", [])}
        actual = {item["id"]: item for item in plan}
        if set(expected) != set(actual):
            raise ValueError(f"Состав карт изменился после пробного прогона: лишние {sorted(set(actual) - set(expected))}, "
                             f"нет {sorted(set(expected) - set(actual))}")
        for card_id, item in actual.items():
            if any(item[key] != expected[card_id][key] for key in ("version", "revision", "before", "after")):
                raise ValueError(f"Карта {item['item_index']} [{card_id}] изменилась после пробного прогона")
    if changed:
        statuses = _issue(uow, refs, now, changed, REASON)
        report["issued"] = [f"{row.item_index} [{row.id}]: {statuses[row.id]}, ревизия {row.revision}" for row in changed]
    return report, {"project": PROJECT, "cards": plan}, {"project": PROJECT, "cards": rollback}


def rollback_inputs(data, uow, *, who, now, refs):
    """Restore saved inputs only where they are still exactly what this script wrote, and issue the revision back."""
    rows = {str(row.id): row for row in _rows(uow)}
    restored, report = [], {"changed": [], "kept": [], "issued": []}
    for item in data.get("cards", []):
        row = rows.get(item["id"])
        if row is None or inputs_hash(row.inputs) != item["after"] or inputs_hash(item["inputs"]) == item["after"]:
            raise ValueError(f"Карта {item.get('item_index')} [{item['id']}] изменилась после записи — откат не выполнен")
        row.inputs = deepcopy(item["inputs"])
        row.version += 1
        row.updated_at = now
        restored.append(row)
        report["changed"].append(f"{row.item_index} [{row.id}]: входные данные восстановлены")
        _event(uow, who, now, row, "techcards.vswr_control_restored", {"reason": f"Откат: {REASON}"})
    if restored:
        statuses = _issue(uow, refs, now, restored, f"Откат: {REASON}")
        report["issued"] = [f"{row.item_index} [{row.id}]: {statuses[row.id]}, ревизия {row.revision}" for row in restored]
    return report


def _print(title, report):
    print(title)
    for key, name in (("changed", "Изменены"), ("issued", "Выпущено"), ("kept", "Без изменений")):
        print(f"  {name}: {len(report[key])}")
        for line in report[key]:
            print(f"    {line}")


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон, всё откатывается)")
    parser.add_argument("--plan-out", type=Path, help="записать план пробного прогона (карты, версии, хеши)")
    parser.add_argument("--expect", type=Path, help="план пробного прогона: применить ровно его")
    parser.add_argument("--rollback-out", type=Path, help="куда записать исходные inputs до записи")
    parser.add_argument("--rollback", type=Path, help="файл отката, записанный --rollback-out")
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
            _print("Откат, ЭТК 708К.:", rollback_inputs(json.loads(args.rollback.read_text(encoding="utf-8")), uow,
                                                         who=actor(), now=now, refs=refs))
        else:
            expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
            report, plan, rollback = no_vswr(uow, who=actor(), now=now, refs=refs, expect=expect)
            _print("Контроль КСВН, ЭТК 708К.:", report)
            if args.plan_out:
                args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if args.apply:
                # Written before the commit: a committed change always has its way back.
                with args.rollback_out.open("x", encoding="utf-8", newline="\n") as handle:  # never overwrite
                    handle.write(json.dumps(rollback, ensure_ascii=False, indent=1))
        if args.apply:
            uow.commit()
            print("COMMITTED — входные данные записаны, ревизии ЭТК выпущены")
        else:
            print("dry run — rolled back (входные данные, ревизии, журнал и ТД не записаны)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
