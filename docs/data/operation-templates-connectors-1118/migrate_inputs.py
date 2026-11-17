"""Saved 708К. generator inputs follow the short connector cards (18.11), in one guarded transaction.

    # 1. dry run (default): report + plan of exactly which cards change, with their input hashes
    ERP_DATABASE_URL=... PYTHONPATH=backend python migrate_inputs.py --plan-out plan.json
    # 2. apply exactly that plan; the rollback file is written before the commit
    ERP_DATABASE_URL=... PYTHONPATH=backend python migrate_inputs.py --expect plan.json --rollback-out inputs-rollback.json --apply
    # one refresh wave: the short cards (apply_templates.py payload) in the SAME transaction
    ... migrate_inputs.py --templates payload.json --expect plan.json --rollback-out inputs-rollback.json --apply
    # rollback (dry run without --apply); --templates rollback.json restores the cards in the same transaction
    ERP_DATABASE_URL=... PYTHONPATH=backend python migrate_inputs.py --rollback inputs-rollback.json [--templates rollback.json] [--apply]

Only `techcard_projects.inputs` (+ version, updated_at) of project 708К. changes, and only on RTK 031 cards whose
ends are the exact FAKRA/HFM/Amphenol articles:
  * generated titles of the 18.11 cards: «…заглубления контакта 0,18 мм», «…до щелчка», «…и CPA» → the generator's
    current titles (only when the saved title is the old generated one and the op is bound to its dedicated card);
  * an Amphenol 2FA1 end with a separate STRIP_FAKRA_BH in a cut-and-strip recipe: the side joins the cut's
    stripSides and the separate strip goes (owner 16.11: «везде полная разделка» at the cut). HFM routes with a
    separate STRIP_HFM, and any recipe without the cut-and-strip, need a generator rebuild and are only reported.
Quantities, parts, marks (FRX serials, label texts), times, equipment, documents, revisions and production tasks are
never touched; launched tasks keep their pinned revision. Cards with hand-edited operations or a hand-cut ПФ scheme
get titles only, never a structural change. The ETK refresh worker issues the revisions after commit.

The refresh diff does not compare operation titles: a card whose only change is a title is NOT re-issued. Each run
previews `etk_refresh.evaluate` per changed card («revised» / «inputs_only»); --apply refuses any «inputs_only» card.
Run it with --templates payload.json (the short cards) so titles and card text land in one revision — one refresh
wave instead of two, whatever the worker does between two separate commits.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards import etk_derive
from app.modules.techcards.etk_derive import route_override
from app.modules.techcards.infrastructure import ProjectTechcardRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number
from app.workflows import etk_refresh
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs

AUTHOR = "Разъёмы — входные данные ЭТК 18.11.2026"
REASON = "Короткие карты разъёмов 708К. (свод 17.11.2026): названия операций и полная разделка при резке"
PROJECT = normalize_project_number("708К.")
OUTCOMES = ("changed", "revised", "inputs_only", "rebuild", "protected", "kept")
# Exact part identity and RTK 031 are required, never just the word FAKRA.
ENDS = {"straight": "59K13O-1M4A4", "angled": "59K25H-1M4A4", "hfm": "AMK11D-1M4Z5-Z", "bulkhead": "2FA1-NZSP-C30E2"}
# (end kind, opKey stage) → (dedicated card, old generated title, current generator title; tcGenLogic.getActiveOpsFakra_).
TITLES = {
    ("straight", "coaxInsert"): ("INS_FAKRA_ST_CONTACT", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм",
                                 "Вставка контакта в металлический корпус, утопание 0,18…0,68 мм"),
    ("straight", "coaxBody"): ("INS_FAKRA_ST_HOUSING", "Установка в корпус до щелчка", "Установка в пластмассовый корпус"),
    ("hfm", "coaxInsert"): ("INS_HFM_CONTACT", "Вставка контактов в корпус до щелчка", "Вставка контактов в металлические корпуса"),
    ("hfm", "coaxBody"): ("INS_HFM_HOUSING", "Вторичная фиксация и CPA", "Установка в корпус QUAD и вторичная фиксация"),
}
STRIP_CODES = {"hfm": "STRIP_HFM", "bulkhead": "STRIP_FAKRA_BH"}
CUT_CODE = "CUT_COAX+2strip"
SIDE = {"A": "А", "B": "В"}
_CUT_TITLE = re.compile(r"Резка с разделкой \([АВ](?: и [АВ])?\)")


def actor() -> Actor:
    return Actor(id=UUID(int=0), name=AUTHOR, department="Технолог")


def inputs_hash(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normal(value) -> str:
    return re.sub(r"[\s._–—-]+", "", str(value or "")).casefold()


def code_resolver(templates):
    """Saved ops name their card by template id (a DB source id; old fixtures — the code), else by opNum."""
    by_id = {card.source_id: card.code for card in templates}
    codes = set(by_id.values())

    def code_of(op) -> str:
        template_id = str(op.get("templateId") or "")
        if template_id in by_id:
            return by_id[template_id]
        return template_id if template_id in codes else str(op.get("opNum") or "")
    return code_of


def _title(op, side, kind, code_of):
    """(new title or None, note or None) for one connector-end operation."""
    key = str(op.get("opKey") or "")
    rule = TITLES.get((kind, key[:-1])) if key.endswith(side) else None
    if not rule:
        return None, None
    code, old, new = rule
    label = str(op.get("label") or "")
    pattern = r"(?P<mult> ×\d+)? \((?P<side>[АВ])\)"
    if re.fullmatch(re.escape(new) + pattern, label):
        return None, None
    found = re.fullmatch(re.escape(old) + pattern, label)
    if op.get("type") != "coaxHousing" or code_of(op) != code or not found or found["side"] != SIDE[side]:
        return None, f"{key}: своё название или карта — оставлено «{label}»"
    return f"{new}{found['mult'] or ''} ({found['side']})", None


def _fold_strip(ops, coax, side, code_of):
    """Amphenol end: drop the separate strip, strip this side at the cut. → None on success, else the reason."""
    strips = [op for op in ops if op.get("side") == side and op.get("type") == "coaxStrip"]
    cuts = [op for op in ops if op.get("type") == "coaxCut"]
    if not coax.get("fakraCutStrip"):
        return "рецепт без резки с разделкой"
    if len(strips) != 1 or strips[0].get("opKey") != f"coaxSideStrip{side}" or code_of(strips[0]) != STRIP_CODES["bulkhead"]:
        return "своя разделка"
    if len(cuts) != 1 or code_of(cuts[0]) != CUT_CODE or not isinstance(cuts[0].get("stripSides"), list) \
            or side in cuts[0]["stripSides"] or not _CUT_TITLE.fullmatch(str(cuts[0].get("label") or "")):
        return "резка без разделки или своя резка"
    cut = cuts[0]
    ops.remove(strips[0])
    sides = [item for item in ("A", "B") if item in cut["stripSides"] or item == side]
    ends = [item for item in ("A", "B") if (coax.get("side" + item) or {}).get("kind")]
    flow = sides == ends
    cut.update(stripSides=sides, opKey="coaxFakraCutStrip" if flow else "coaxCutStrip",
               label=f"Резка с разделкой ({' и '.join(SIDE[item] for item in sides)})")
    if flow:
        # Generator flow: every end's preparation follows the cut-cable block, before the labels and the end stages.
        prep = [op for op in ops if op.get("type") == "coaxInsSleeve"]
        rest = [op for op in ops if op.get("type") != "coaxInsSleeve"]
        at = next((index for index, op in enumerate(rest) if op.get("side") or op.get("opKey") == "coaxInsLabel"), len(rest))
        ops[:] = rest[:at] + prep + rest[at:]
    return None


def migrate_config(config, code_of, *, structural=True):
    """(copy, changes, notes, rebuild, skipped). Unknown ends, custom titles and custom bindings stay as they are;
    `notes` — kept custom titles/bindings, `rebuild` — separate strips left for a generator rebuild, `skipped` — why
    the whole config is outside this migration."""
    new = deepcopy(config)
    if not isinstance(new, dict) or new.get("mode") != "coax" or not isinstance(new.get("ops"), list):
        return new, [], [], [], "не коакс-сборка"
    coax = new.get("coax") if isinstance(new.get("coax"), dict) else {}
    if _normal(coax.get("cableArt")) != "rtk031":
        return new, [], [], [], "другой кабель или нет артикула"
    ends = {}
    for side in ("A", "B"):
        end = coax.get("side" + side)
        if isinstance(end, dict) and end.get("kind") in ENDS and _normal(end.get("article")) == _normal(ENDS[end["kind"]]):
            ends[side] = end["kind"]
    if not ends:
        return new, [], [], [], "нет концов FAKRA/HFM/Amphenol 708К."
    changes, notes, rebuild = [], [], []
    for op in new["ops"]:
        side = op.get("side") if isinstance(op, dict) else None
        if side not in ends:
            continue
        title, note = _title(op, side, ends[side], code_of)
        if note:
            notes.append(note)
        if title:
            changes.append(f"{op['opKey']}: «{op['label']}» → «{title}»")
            op["label"] = title
    for side, kind in ends.items():
        if kind not in STRIP_CODES or not any(op.get("side") == side and op.get("type") == "coaxStrip" for op in new["ops"]):
            continue
        reason = "HFM: опорная гильза и резка с разделкой" if kind == "hfm" else (
            "ручные правки операций или схемы ПФ" if not structural else _fold_strip(new["ops"], coax, side, code_of))
        if reason:
            rebuild.append(f"сторона {SIDE[side]}: отдельная разделка остаётся ({reason}) — пересобрать в генераторе")
        else:
            changes.append(f"coaxSideStrip{side}: отдельная разделка убрана — сторона {SIDE[side]} разделывается при резке")
    return new, changes, notes, rebuild, ""


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


def _event(uow, who, now, row, event_type, details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=event_type, actor=who, occurred_at=now,
                             details=details))


def _enqueue(uow, now, payload):
    # One pending template request is enough: the refresh job takes every pending request as one batch.
    uow.session.flush()
    pending = uow.session.scalar(select(RefreshRequestRow.id).where(
        RefreshRequestRow.reason == etk_refresh.REASON_TEMPLATE, RefreshRequestRow.processed_at.is_(None)).limit(1))
    if pending is None:
        uow.session.add(RefreshRequestRow(id=uuid4(), reason=etk_refresh.REASON_TEMPLATE, payload=payload, created_at=now))


def _preview(uow, refs, now, rows):
    """What the refresh worker will make of each changed card once this transaction commits: `evaluate` on the new
    inputs and the templates as they stand in this transaction. Its document diff does not compare operation titles,
    so a title-only change alone is never re-issued — it must ride with the card content of apply_templates.py."""
    metadata = uow.operation_cards.metadata() or {}
    ctx = etk_refresh.Context(
        refs=refs, templates=tuple(uow.operation_cards.list()), commit=str(metadata.get("source_commit") or ""),
        snapshot=dict(refs.snapshot) if refs.snapshot is not None else None, builder=etk_derive.builder_hash(),
        reason=etk_refresh.REASON_REFERENCE, now=now)
    cards = getattr(uow, "inner", uow).techcards
    uow.session.flush()
    return {row.id: etk_refresh.evaluate(cards.get(row.id), ctx)[2].status for row in rows}


def migrate_inputs(uow, *, who, now, expect=None, refs=None):
    """Open UoW only; never commits. `expect` — the dry-run plan: exactly these cards, versions and hashes, or nothing;
    with `refs` every changed card must also come out as a new revision (or a refreshed draft)."""
    result = {key: [] for key in OUTCOMES}
    plan, rollback, changed_rows = [], [], []
    code_of = code_resolver(tuple(uow.operation_cards.list()))
    for row in _rows(uow):
        label = f"{row.item_index} [{row.id}]"
        if not isinstance(row.inputs, dict) or not row.inputs:
            result["kept"].append(f"{label}: нет inputs")
            continue
        manual = _manual(row)
        inputs, changes, notes, rebuild, skipped = migrate_config(row.inputs, code_of, structural=not manual)
        suffix = f" ({'; '.join(notes)})" if notes else ""
        if rebuild:
            result["rebuild"].append(f"{label}: {'; '.join(rebuild)}")
        if not changes:
            (result["protected"] if notes else result["kept"]).append(f"{label}: {skipped or 'без изменений'}{suffix}")
            continue
        before, after = inputs_hash(row.inputs), inputs_hash(inputs)
        plan.append({"id": str(row.id), "item_index": row.item_index, "version": row.version,
                     "before": before, "after": after, "changes": changes})
        rollback.append({"id": str(row.id), "item_index": row.item_index, "after": after, "inputs": deepcopy(row.inputs)})
        row.inputs = inputs
        row.version += 1
        row.updated_at = now
        changed_rows.append(row)
        result["changed"].append(f"{label}: {'; '.join(changes)}{suffix}"
                                 + ("; есть ручные правки операций — их названия остаются ручными" if manual else ""))
        _event(uow, who, now, row, "techcards.connector_inputs_migrated", {"changes": changes, "reason": REASON})
    if refs is not None and changed_rows:
        statuses = _preview(uow, refs, now, changed_rows)
        for row in changed_rows:
            visible = statuses[row.id] in {"revised", "refreshed"}
            result["revised" if visible else "inputs_only"].append(f"{row.item_index} [{row.id}]: {statuses[row.id]}")
        if expect is not None and result["inputs_only"]:
            raise ValueError("ЭТК не перевыпустятся, изменятся только входные данные: "
                             f"{', '.join(result['inputs_only'])} — запустите вместе с --templates payload.json")
    if expect is not None:
        expected = {item["id"]: item for item in expect.get("cards", [])}
        actual = {item["id"]: item for item in plan}
        if set(expected) != set(actual):
            raise ValueError(f"Состав карт изменился после пробного прогона: лишние {sorted(set(actual) - set(expected))}, "
                             f"нет {sorted(set(expected) - set(actual))}")
        for card_id, item in actual.items():
            if any(item[key] != expected[card_id][key] for key in ("version", "before", "after")):
                raise ValueError(f"Карта {item['item_index']} [{card_id}] изменилась после пробного прогона")
    if plan:
        _enqueue(uow, now, {"reason": REASON, "cards": [item["item_index"] for item in plan]})
    uow.session.flush()
    return result, {"project": PROJECT, "cards": plan}, {"project": PROJECT, "cards": rollback}


def rollback_inputs(data, uow, *, who, now, refs=None):
    """Restore saved inputs only where they are still exactly what this migration wrote; otherwise nothing."""
    result = {key: [] for key in OUTCOMES}
    rows = {str(row.id): row for row in _rows(uow)}
    restored = []
    for item in data.get("cards", []):
        row = rows.get(item["id"])
        if row is None or inputs_hash(row.inputs) != item["after"] or inputs_hash(item["inputs"]) == item["after"]:
            raise ValueError(f"Карта {item.get('item_index')} [{item['id']}] изменилась после миграции — откат не выполнен")
        row.inputs = deepcopy(item["inputs"])
        row.version += 1
        row.updated_at = now
        restored.append(row)
        result["changed"].append(f"{row.item_index} [{row.id}]: входные данные восстановлены")
        _event(uow, who, now, row, "techcards.connector_inputs_rolled_back", {"reason": REASON})
    if refs is not None and restored:
        statuses = _preview(uow, refs, now, restored)
        for row in restored:
            visible = statuses[row.id] in {"revised", "refreshed"}
            result["revised" if visible else "inputs_only"].append(f"{row.item_index} [{row.id}]: {statuses[row.id]}")
    if result["changed"]:
        _enqueue(uow, now, {"reason": f"Откат: {REASON}", "cards": [item["item_index"] for item in data["cards"]]})
    uow.session.flush()
    return result


def _templates_script():
    path = Path(__file__).with_name("apply_templates.py")
    spec = importlib.util.spec_from_file_location("connector_apply_templates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _print(title, result):
    print(title)
    for key, values in result.items():
        print(f"  {key}: {len(values)}")
        if key != "unchanged":
            for value in values:
                print(f"    {value}")


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон, всё откатывается)")
    parser.add_argument("--plan-out", type=Path, help="записать план пробного прогона (карты, версии, хеши)")
    parser.add_argument("--expect", type=Path, help="план пробного прогона: применить ровно его")
    parser.add_argument("--rollback-out", type=Path, help="куда записать исходные inputs до записи")
    parser.add_argument("--rollback", type=Path, help="файл отката, записанный --rollback-out")
    parser.add_argument("--templates", type=Path, help="payload.json / rollback.json карт apply_templates.py — в той же транзакции")
    args = parser.parse_args(argv)
    if args.apply and not args.rollback and not (args.expect and args.rollback_out):
        parser.error("--apply требует --expect (план пробного прогона) и --rollback-out")
    if args.rollback_out and args.rollback_out.exists():
        parser.error(f"{args.rollback_out} уже есть — не перезаписываю")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sessions = sessionmaker(bind=build_engine(os.environ["ERP_DATABASE_URL"]), expire_on_commit=False, class_=Session)
    now = moscow_now()
    # References first, on their own connection: the preview reads them; this transaction does not write them.
    refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        if args.templates:
            script = _templates_script()
            payload = json.loads(args.templates.read_text(encoding="utf-8"))
            _print("Карты операций:", script.apply_payload(payload, uow, who=script.actor(), now=now))
        if args.rollback:
            _print("Входные данные ЭТК (откат):", rollback_inputs(
                json.loads(args.rollback.read_text(encoding="utf-8")), uow, who=actor(), now=now, refs=refs))
        else:
            expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
            result, plan, rollback = migrate_inputs(uow, who=actor(), now=now, expect=expect, refs=refs)
            _print("Входные данные ЭТК 708К.:", result)
            if args.plan_out:
                args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if args.apply:
                # Written before the commit: a committed change always has its way back.
                args.rollback_out.write_text(json.dumps(rollback, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        if args.apply:
            uow.commit()
            print("COMMITTED — обновление ЭТК поставлено в очередь (одна волна ревизий)")
        else:
            print("dry run — rolled back (включая журнал и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
