"""Coax VSWR control by СТО ФРАК-002-2026 (сверка ВАЦ 18.11, В1–В2), one guarded transaction.

    # 1. dry run (default): the template state and every ETK whose document changes, with versions and hashes
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_vswr_ctrl.py --plan-out plan.json
    # 2. apply exactly that plan; the rollback file is written before the commit; --reissue issues the revisions here
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_vswr_ctrl.py --expect plan.json --rollback-out vswr-rollback.json --reissue --apply
    # rollback of the template (dry run without --apply)
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_vswr_ctrl.py --rollback vswr-rollback.json [--reissue] [--apply]

What changes: the ERP template CTRL_VSWR_coax (`payload.json`: two-port measurement from both ends — S11 side А,
S22 side Б — per СТО ФРАК-001-2026 4.8.16; warm-up 40 min; torques N 1,1–1,5 Н·м, 3,5 мм/SMA 0,8–1,0 Н·м; one-port
with a load only as the agreed fallback), and through the builder of this release the control step of every coax VSWR
operation, short cards (`coax.shortCards`) included. The template is replaced only when it is still exactly the
earlier delivery (`before`) and has no record edit; anything else is reported and refused under --apply.

Run it with the code of this release (the builder text is part of the document). On a release the deploy step already
seeds the template (when production still holds the ERP delivery) and `etk_refresh --all` re-issues the cards; then
this dry run shows the template «unchanged» and no cards — nothing to apply. Run before that refresh (from a checkout of
this release against the production database) it makes it one controlled wave: the deploy refresh then finds the
documents equal. Inputs, overrides, quantities, other templates and launched tasks (pinned revisions) are untouched.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
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
from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR
from app.modules.techcards.infrastructure import (
    OperationCardTemplateRow, ProjectTechcardRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork,
)
from app.modules.techcards.operation_cards import parse_operation_template
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows import etk_refresh
from app.workflows.new_operation import LIBRARY_AGGREGATE, NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs

AUTHOR = "КСВН сборок по СТО ФРАК-002-2026 — 18.11.2026"
REASON = "Контроль КСВН сборок по СТО ФРАК-002-2026: два конца, прогрев 40 мин, моменты затяжки"
PAYLOAD = Path(__file__).with_name("payload.json")
OUTCOMES = ("template", "revised", "kept", "refused")
# The template columns this script writes; a rollback restores exactly these.
COLUMNS = ("title", "name", "category", "description", "raw_rows", "document", "saved_at", "saved_by")


def actor() -> Actor:
    return Actor(id=UUID(int=0), name=AUTHOR, department="Технолог")


def rows_hash(rows) -> str:
    text = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_hash(document) -> str:
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_payload(path: Path = PAYLOAD) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if rows_hash(payload["rows"]) != payload["after"] or rows_hash(payload["before_rows"]) != payload["before"]:
        raise ValueError("payload.json: хеши строк шаблона не сходятся")
    return payload


def _event(uow, who, now, event_type, details, aggregate=LIBRARY_AGGREGATE):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=aggregate, event_type=event_type, actor=who, occurred_at=now,
                             details=details))


def _template_row(uow, payload) -> OperationCardTemplateRow:
    rows = list(uow.session.scalars(select(OperationCardTemplateRow).where(
        OperationCardTemplateRow.code == payload["code"]).with_for_update()))
    if len(rows) != 1 or rows[0].id != payload["id"]:
        raise ValueError(f"{payload['code']}: ожидался один шаблон {payload['id']}, найдено {[row.id for row in rows]}")
    return rows[0]


def _columns(row) -> dict:
    """JSON-ready: `saved_at` as ISO text (the rollback file is JSON)."""
    columns = {key: deepcopy(getattr(row, key)) for key in COLUMNS}
    columns["saved_at"] = columns["saved_at"].isoformat() if columns["saved_at"] is not None else None
    return columns


def _restore(row, columns) -> None:
    for key, value in columns.items():
        setattr(row, key, deepcopy(value))
    row.saved_at = datetime.fromisoformat(columns["saved_at"]) if columns["saved_at"] else None


def _write_template(uow, payload, rows, who, now) -> None:
    card = parse_operation_template(source_id=payload["id"], title=payload["title"], category=payload["category"],
                                    rows=rows, images=None)
    uow.operation_cards.save_sheet(card, description="Шаблон ERP (в БД.ТК SUPPLYMAP его нет)", saved_at=now,
                                   saved_by=ERP_TEMPLATE_AUTHOR)  # the seed keeps treating it as its own delivery


def _vswr_cards(uow) -> list[ProjectTechcardRow]:
    """Every stored ETK with a coax VSWR operation, locked; other cards are not even read for the plan."""
    rows = uow.session.scalars(select(ProjectTechcardRow).order_by(
        ProjectTechcardRow.project, ProjectTechcardRow.item_index).with_for_update())
    return [row for row in rows if isinstance(row.document, dict) and any(
        isinstance(op, dict) and (op.get("type") == "coaxVswr" or op.get("code") == "CTRL_VSWR_coax")
        for op in row.document.get("operations") or [])]


def _context(uow, refs, now) -> etk_refresh.Context:
    metadata = uow.operation_cards.metadata() or {}
    return etk_refresh.Context(
        refs=refs, templates=tuple(uow.operation_cards.list()), commit=str(metadata.get("source_commit") or ""),
        snapshot=dict(refs.snapshot) if refs.snapshot is not None else None, builder=etk_derive.builder_hash(),
        reason=etk_refresh.REASON_TEMPLATE, now=now)


def _cards(uow, refs, now, reissue, result) -> list[dict]:
    """Evaluate each VSWR card against the templates as they stand in this transaction; `reissue` saves the revisions
    here through the refresh's own path (journal, automatic ТД issue). → plan entries of the cards that change."""
    uow.session.flush()
    ctx = _context(uow, refs, now)
    inner = getattr(uow, "inner", uow)
    plan = []
    for row in _vswr_cards(uow):
        label = f"{row.project} {row.item_index} [{row.id}]"
        before = {"id": str(row.id), "project": row.project, "item_index": row.item_index, "version": row.version,
                  "revision": row.revision, "document": document_hash(row.document)}
        updated, revision, outcome = etk_refresh.evaluate(inner.techcards.get(row.id), ctx)
        if outcome.status not in {"revised", "refreshed"}:
            result["kept"].append(f"{label}: {outcome.status}")
            continue
        plan.append(before)
        fields = ", ".join(dict.fromkeys(item["field"] for item in outcome.changes))
        if reissue:
            etk_refresh.save_result(inner, updated, revision, outcome, now)
        result["revised"].append(f"{label}: ревизия {row.revision} → {row.revision + 1}" + (f" — {fields}" if fields else ""))
    uow.session.flush()
    return plan


def _check_expect(expect, template_plan, cards) -> None:
    if expect.get("template") != template_plan:
        raise ValueError(f"Шаблон изменился после пробного прогона: было {expect.get('template')}, сейчас {template_plan}")
    expected = {item["id"]: item for item in expect.get("cards", [])}
    actual = {item["id"]: item for item in cards}
    if set(expected) != set(actual):
        raise ValueError(f"Состав карт изменился после пробного прогона: лишние {sorted(set(actual) - set(expected))}, "
                         f"нет {sorted(set(expected) - set(actual))}")
    for card_id, item in actual.items():
        if any(item[key] != expected[card_id][key] for key in ("version", "revision", "document")):
            raise ValueError(f"Карта {item['project']} {item['item_index']} [{card_id}] изменилась после пробного прогона")


def _enqueue_builder(uow, now, plan) -> None:
    """Template already in place, documents still old (the builder of this release, no deploy refresh yet): the worker
    re-derives them after the commit."""
    uow.session.flush()
    pending = uow.session.scalar(select(RefreshRequestRow.id).where(
        RefreshRequestRow.reason == etk_refresh.REASON_BUILDER, RefreshRequestRow.processed_at.is_(None)).limit(1))
    if pending is None and plan:
        uow.session.add(RefreshRequestRow(id=uuid4(), reason=etk_refresh.REASON_BUILDER,
                                          payload={"reason": REASON, "builder_hash": etk_derive.builder_hash()},
                                          created_at=now))


def apply_vswr_ctrl(uow, *, who, now, payload=None, refs=None, expect=None, reissue=False):
    """Open UoW only; never commits. → (result, plan, rollback). `expect` — the dry-run plan: exactly this template
    state and these cards, versions and documents, or nothing."""
    payload = payload or load_payload()
    result = {key: [] for key in OUTCOMES}
    row = _template_row(uow, payload)
    current = rows_hash(row.raw_rows)
    template_plan = {"id": row.id, "rows": current, "edited": bool(row.edited)}
    rollback = {"template": None}
    if row.edited or row.edited_at:
        result["refused"].append(f"{row.code}: есть правка записи ({row.edited_by or '?'}) — шаблон не заменяю")
    elif current == payload["after"]:
        result["template"].append(f"{row.code}: уже по СТО ФРАК-002-2026 — без изменений")
    elif current != payload["before"]:
        result["refused"].append(f"{row.code}: содержимое не прежняя поставка ERP (хеш {current[:12]}) — не заменяю")
    else:
        rollback["template"] = {"id": row.id, "after": payload["after"], "columns": _columns(row)}
        _write_template(uow, payload, payload["rows"], who, now)
        _event(uow, who, now, "techcards.vswr_template_applied", {"code": row.code, "reason": REASON})
        result["template"].append(f"{row.code}: заменён (два конца, прогрев 40 мин, моменты затяжки)")
    cards = _cards(uow, refs, now, reissue, result) if refs is not None else []
    plan = {"template": template_plan, "cards": cards}
    if expect is not None:
        if result["refused"]:
            raise ValueError("; ".join(result["refused"]))
        _check_expect(expect, template_plan, cards)
    if not reissue and rollback["template"] is None:
        _enqueue_builder(uow, now, cards)
    uow.session.flush()
    return result, plan, rollback


def rollback_vswr_ctrl(data, uow, *, who, now, refs=None, reissue=False):
    """Restore the template columns only while the template is still exactly what this script wrote. The builder text
    of the release stays (a code revert is a release of its own)."""
    result = {key: [] for key in OUTCOMES}
    saved = data.get("template")
    if saved is None:
        raise ValueError("В файле отката шаблона нет — скрипт его не менял")
    row = uow.session.get(OperationCardTemplateRow, saved["id"], with_for_update=True)
    if row is None or row.edited or rows_hash(row.raw_rows) != saved["after"]:
        raise ValueError(f"Шаблон {saved['id']} изменился после записи — откат не выполнен")
    _restore(row, saved["columns"])
    _event(uow, who, now, "techcards.vswr_template_rolled_back", {"code": row.code, "reason": REASON})
    result["template"].append(f"{row.code}: прежний шаблон восстановлен")
    if refs is not None:
        _cards(uow, refs, now, reissue, result)
    uow.session.flush()
    return result


def _print(title, result):
    print(title)
    for key, values in result.items():
        print(f"  {key}: {len(values)}")
        for value in values:
            print(f"    {value}")


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон, всё откатывается)")
    parser.add_argument("--plan-out", type=Path, help="записать план пробного прогона (шаблон, карты, версии, хеши)")
    parser.add_argument("--expect", type=Path, help="план пробного прогона: применить ровно его")
    parser.add_argument("--rollback-out", type=Path, help="куда записать прежний шаблон до записи")
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
            _print("Контроль КСВН (откат):", rollback_vswr_ctrl(
                json.loads(args.rollback.read_text(encoding="utf-8")), uow, who=actor(), now=now, refs=refs,
                reissue=args.reissue))
        else:
            expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
            result, plan, rollback = apply_vswr_ctrl(uow, who=actor(), now=now, refs=refs, expect=expect,
                                                     reissue=args.reissue)
            _print("Контроль КСВН по СТО ФРАК-002-2026:", result)
            if args.plan_out:
                args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if args.apply:
                with args.rollback_out.open("x", encoding="utf-8", newline="\n") as handle:  # never overwrite
                    handle.write(json.dumps(_jsonable(rollback), ensure_ascii=False, indent=1))
        if args.apply:
            uow.commit()
            print("COMMITTED" + (" — ревизии выпущены" if args.reissue else " — ревизии выпустит обновление ЭТК"))
        else:
            print("dry run — rolled back (включая журнал, ревизии и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
