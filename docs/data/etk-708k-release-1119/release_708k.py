"""Scoped preview and reissue of the 708К. ETK after the свод инструкций deliveries (5/5), one guarded transaction.

    # 1. preview (default, read-only): every card of 708К. by stable id, checked against manifest.json
    ERP_DATABASE_URL=... PYTHONPATH=backend python release_708k.py --plan-out release-plan.json
    # 2. issue exactly that plan, in one transaction, then prove a second pass changes nothing
    ERP_DATABASE_URL=... PYTHONPATH=backend python release_708k.py --expect release-plan.json --reissue --apply

`python -m app.workflows.etk_refresh --all --dry-run` previews every project; this one previews and issues only the
reviewed 708К. coax cards. Identity is the card id; `manifest.json` names, per КБ, the one «Коакс» card and its end
articles. Two «Коакс» cards with one КБ, another end article, or no card — «identity» problem: preview reports it,
--apply refuses (never «the latest one»). Non-coax cards with a manifest КБ (the Orange Pi harness КБ1) are excluded;
cards whose КБ is not in the manifest (КБ41) are outside; neither is ever written.

A card is revised through the refresh's own evaluate/save path (journal, automatic ТД issue, revision N+1; a draft is
refreshed in place); manual operation edits and hand-cut ПФ schemes are kept by that path, a conflict blocks --apply.
Launched production tasks keep their pinned revision. After the reissue the same transaction evaluates every manifest
card again: anything but «unchanged» rolls the whole run back. Templates, references and saved inputs are not
changed here — run the delivery scripts (etk-pin-coax-1119 etc.) first, without their own --reissue if one wave is
wanted. There is no rollback file: a later correction is a new revision, issued revisions are never edited.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards import etk_derive
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.workflows import etk_refresh
from app.workflows.new_operation import NewOperationUnitOfWork
from app.workflows.techcard_build import load_refs

MANIFEST = Path(__file__).with_name("manifest.json")
RENAME_REASON = "Свод инструкций 708К."
VISIBLE = {"revised", "refreshed"}


def _sibling(folder: str, name: str):
    path = Path(__file__).resolve().parents[1] / folder / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_{folder.replace('-', '_')}_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Project scope and row locks, hashes and article normalisation: the reviewed 18.11 input migration.
INPUTS = _sibling("operation-templates-connectors-1118", "migrate_inputs")


def manifest_hash(manifest) -> str:
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _ends(inputs) -> dict[str, str]:
    coax = inputs.get("coax") if isinstance(inputs, dict) and isinstance(inputs.get("coax"), dict) else {}
    return {side: str((coax.get("side" + side) or {}).get("article") or "") for side in ("A", "B")}


def identify(rows, manifest):
    """→ (cards: kb → row, excluded, outside, problems). Pure: classifies the project's cards by the manifest."""
    if INPUTS.normalize_project_number(manifest["project"]) != INPUTS.PROJECT:
        raise ValueError(f"Манифест другого проекта: {manifest['project']}")
    expected = {item["kb"]: item for item in manifest["expected"]}
    coax, excluded, outside, problems = {}, [], [], []
    for row in rows:
        label = f"{row.item_index} [{row.id}]"
        mode = row.inputs.get("mode") if isinstance(row.inputs, dict) else None
        if row.item_index not in expected:
            outside.append(f"{label}: КБ не в манифесте")
        elif mode != "coax":
            excluded.append(f"{label}: не «Коакс» ({mode or 'нет входных данных'})")
        else:
            coax.setdefault(row.item_index, []).append(row)
    cards = {}
    for kb, item in expected.items():
        found = coax.get(kb, [])
        if len(found) != 1:
            problems.append(f"{kb}: ЭТК «Коакс» {len(found)} — нужна ровно одна"
                            + (f" ({', '.join(str(row.id) for row in found)})" if found else ""))
            continue
        ends = _ends(found[0].inputs)
        if any(INPUTS._normal(ends[side]) != INPUTS._normal(item["ends"][side]) for side in ("A", "B")):
            problems.append(f"{kb} [{found[0].id}]: концы А {ends['A'] or '—'} / В {ends['B'] or '—'}, "
                            f"в манифесте {item['ends']['A']} / {item['ends']['B']}")
            continue
        cards[kb] = found[0]
    return cards, excluded, outside, problems


def _context(uow, refs, now):
    metadata = uow.operation_cards.metadata() or {}
    return etk_refresh.Context(
        refs=refs, templates=tuple(uow.operation_cards.list()), commit=str(metadata.get("source_commit") or ""),
        snapshot=dict(refs.snapshot) if refs.snapshot is not None else None, builder=etk_derive.builder_hash(),
        reason=etk_refresh.REASON_REFERENCE, now=now)


def _entry(row, result):
    return {"id": str(row.id), "item_index": row.item_index, "product": row.product_name, "status": result.status,
            "card_status": str(getattr(row.status, "value", row.status)), "revision": row.revision,
            "version": row.version, "inputs": INPUTS.inputs_hash(row.inputs), "document": INPUTS.inputs_hash(row.document),
            "manual": INPUTS._manual(row),
            "fields": list(dict.fromkeys(item["field"] for item in result.changes)),
            "conflicts": [item["message"] for item in result.conflicts]}


def release(uow, *, refs, now, manifest, expect=None, reissue=False):
    """Open UoW only; never commits. → report. Preview: what the refresh would do to each manifest card. `reissue` with
    `expect` (the preview's plan): issue exactly those cards, then require a second pass to change nothing."""
    cards, excluded, outside, problems = identify(INPUTS._rows(uow), manifest)
    inner = getattr(uow, "inner", uow)
    uow.session.flush()
    ctx = _context(uow, refs, now)
    entries = []
    for kb, row in cards.items():
        result = etk_refresh.evaluate(inner.techcards.get(row.id), ctx, rename_reason=RENAME_REASON)[2]
        entries.append(_entry(row, result))
    plan = [entry for entry in entries if entry["status"] in VISIBLE]
    conflicts = [entry for entry in entries if entry["status"] == "conflict" or entry["conflicts"]]
    report = {"project": INPUTS.PROJECT, "manifest": manifest_hash(manifest), "cards": entries, "plan": plan,
              "excluded": excluded, "outside": outside, "problems": problems,
              "counts": {status: sum(1 for entry in entries if entry["status"] == status)
                         for status in ("revised", "refreshed", "unchanged", "conflict")}}
    if not reissue:
        return report
    if expect is None:
        raise ValueError("--reissue требует --expect (план пробного прогона)")
    if problems or conflicts:
        raise ValueError("Выпуск остановлен: " + "; ".join(problems + [f"{e['item_index']}: конфликт" for e in conflicts]))
    if expect.get("manifest") != report["manifest"]:
        raise ValueError("Манифест изменился после пробного прогона")
    keys = ("id", "version", "revision", "inputs", "document", "status")
    expected = {item["id"]: item for item in expect.get("plan", [])}
    actual = {item["id"]: item for item in plan}
    if set(expected) != set(actual):
        raise ValueError(f"Состав карт изменился после пробного прогона: лишние {sorted(set(actual) - set(expected))}, "
                         f"нет {sorted(set(expected) - set(actual))}")
    for card_id, item in actual.items():
        if any(item[key] != expected[card_id][key] for key in keys):
            raise ValueError(f"Карта {item['item_index']} [{card_id}] изменилась после пробного прогона")
    issued, rows = [], {str(row.id): row for row in cards.values()}
    for card_id in actual:
        updated, revision, result = etk_refresh.evaluate(inner.techcards.get(rows[card_id].id), ctx,
                                                         rename_reason=RENAME_REASON)
        if updated is None or result.status not in VISIBLE:
            raise ValueError(f"Карта {card_id}: при выпуске {result.status}, в плане {actual[card_id]['status']}")
        etk_refresh.save_result(inner, updated, revision, result, now)
        issued.append(f"{result.label}: {result.status}" + (f", ревизия {result.revision}" if result.revision else ""))
    uow.session.flush()
    # Idempotency: the same inputs and templates must now give no further change on any manifest card.
    again = [etk_refresh.evaluate(inner.techcards.get(row.id), ctx, rename_reason=RENAME_REASON)[2] for row in cards.values()]
    restless = [f"{item.label}: {item.status}" for item in again if item.status != "unchanged"]
    if restless:
        raise ValueError(f"Повторная проверка не пуста — выпуск отменён: {', '.join(restless)}")
    report["issued"] = issued
    return report


def _print(report):
    print(f"ЭТК {report['project']}: манифест {report['manifest'][:12]}")
    for entry in report["cards"]:
        line = (f"  {entry['item_index']} [{entry['id']}] {entry['card_status']} рев. {entry['revision']} v{entry['version']}: "
                f"{entry['status']}" + (f" — {', '.join(entry['fields'][:6])}" if entry["fields"] else "")
                + (" (ручные правки)" if entry["manual"] else ""))
        print(line)
        for message in entry["conflicts"]:
            print(f"    ! {message}")
    for key, title in (("problems", "Стоп — идентичность"), ("excluded", "Исключены"), ("outside", "Вне манифеста"),
                       ("issued", "Выпущено")):
        if report.get(key):
            print(f"{title}: {len(report[key])}")
            for line in report[key]:
                print(f"    {line}")
    print("Итого: " + ", ".join(f"{key} {value}" for key, value in report["counts"].items()))


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон, всё откатывается)")
    parser.add_argument("--plan-out", type=Path, help="записать план пробного прогона (id, версии, хеши, статусы)")
    parser.add_argument("--expect", type=Path, help="план пробного прогона: выпустить ровно его")
    parser.add_argument("--reissue", action="store_true", help="выпустить ревизии по плану в этой транзакции")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)
    if args.apply and not (args.reissue and args.expect):
        parser.error("--apply требует --reissue и --expect")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    sessions = sessionmaker(bind=build_engine(os.environ["ERP_DATABASE_URL"]), expire_on_commit=False, class_=Session)
    now = moscow_now()
    refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))  # own connection, read before the transaction
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
        report = release(uow, refs=refs, now=now, manifest=manifest, expect=expect, reissue=args.reissue)
        _print(report)
        if args.plan_out:
            args.plan_out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        if args.apply:
            uow.commit()
            print("COMMITTED — ревизии выпущены, повторная проверка пуста")
        else:
            print("dry run — rolled back (ревизии, журнал и ТД не записаны)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
