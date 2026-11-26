"""Readable operation cards (ETK generator review 23.11.2026, slice 2), one guarded transaction.

    # 1. dry run (default): per code — template, current content hash, signature, outcome; nothing is written
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_readability.py --plan-out plan.json
    # 2. apply exactly that plan; the rollback file is written before the commit
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_readability.py --expect plan.json --rollback-out readability-rollback.json --apply
    # rollback (dry run without --apply)
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_readability.py --rollback readability-rollback.json [--apply]

What changes: the steps of 24 operation templates (payload.json, built from the production ETK export of 23.11.2026).
Paragraph steps over 300 characters become one action per step plus separate check rows; every numeric limit, unit,
standard clause, coverage, tool and safety clause is kept (tests: test_operation_templates_readability.py). In
MARK_CONN_PAINT the «Вход:» / «Результат:» pseudo-steps and «BOM» go; drying stays visibly «не задано». Name, safety,
defects, revision and tooling of every card are unchanged.

Guards: a code is written only when its one template has the payload's id, its effective content hashes to the
payload's `expected_before_hash` (the production content the ETKs were built from) and every signature on it is a
known delivery author. Anything else is reported — `drifted` (unknown content), `protected` (a person saved or edited
it), `missing` — and left untouched; `unchanged` — already the new content (e.g. the release's ERP seed got there).
An ERP-delivered card without a record edit (CTRL_VSWR_coax / MARK_CONN_PAINT as seeded) is rewritten as the seed sheet
of this release (`techcard-library-erp.json`, signed as the ERP delivery, so later seeds keep owning it); every other
card gets a record edit signed by this script, as the 22.10/23.10 audits wrote them.

Not passive: writing a template queues the standard ETK refresh request (app.workflows.etk_refresh listener); issued
cards get revisions only through that job and the deployment's ETK_AUTO_REVISION policy. Run it in the coordinated
rebuild window (after the slice-1 builder release), not as an isolated text fix. Saved inputs, documents, overrides and
launched tasks (pinned revisions) are not touched here.
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

from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR
from app.modules.techcards.infrastructure import OperationCardTemplateRow, ProjectTechcardRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import normalize_record_edit, parse_operation_template
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows import etk_refresh  # noqa: F401 - the template write queues the ETK refresh in this transaction
from app.workflows.new_operation import LIBRARY_AGGREGATE, NewOperationUnitOfWork

AUTHOR = "Читаемые карты операций — 23.11.2026"
ROLLBACK_AUTHOR = "Откат читаемых карт операций — 23.11.2026"
REASON = ("Ревью генератора ЭТК 23.11.2026, срез 2: шаги — короткие действия и отдельные строки контроля; "
          "все нормы, ссылки, объём контроля и требования безопасности сохранены")
FOLDER = Path(__file__).resolve().parent
PAYLOAD = FOLDER / "payload.json"
ERP_SEED = FOLDER.parents[2] / "backend" / "data" / "techcard-library-erp.json"
# Signatures of the deliveries whose content this payload replaces; any other name is a person's edit.
TRUSTED_AUTHORS = frozenset({
    ERP_TEMPLATE_AUTHOR, "Аудит ГОСТ Р 22.10.2026", "Аудит ГОСТ Р 23.10.2026", "Аудит ГОСТ Р 25.10.2026",
    "Маркировка — карты 16.11.2026", "КСВН сборок по СТО ФРАК-002-2026 — 18.11.2026", AUTHOR, ROLLBACK_AUTHOR,
})
OUTCOMES = ("changed", "unchanged", "drifted", "protected", "missing")
# The template columns a write touches; a rollback restores exactly these.
COLUMNS = ("title", "name", "category", "description", "raw_rows", "images", "document", "edited", "edited_at",
           "edited_by", "saved_at", "saved_by")


def actor(name: str = AUTHOR) -> Actor:
    return Actor(id=UUID(int=0), name=name, department="Технолог")


def record_hash(record) -> str:
    """Hash of a card's record content (name, safety, defects, revision, steps, tooling) in the normalised form a record
    edit stores — the same for a seeded sheet and for an edit overlay with that content."""
    value = normalize_record_edit(record).as_json()
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def card_hash(card) -> str:
    return record_hash({"name": card.name, "safety": card.safety, "defects": card.defects, "revision": card.revision,
                        "steps": [step.__dict__ for step in card.steps], "tooling": [tool.__dict__ for tool in card.tooling]})


def load_payload(path: Path = PAYLOAD) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if len({item["code"] for item in payload}) != len(payload):
        raise ValueError("payload.json: повтор кода")
    for item in payload:
        if record_hash(item["record"]) != item["after_hash"]:
            raise ValueError(f"payload.json: {item['code']} — хеш новой записи не сходится")
    return payload


def erp_sheets(path: Path = ERP_SEED) -> dict[str, dict]:
    return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["templates"]}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _protected(row) -> bool:
    return any((
        row.saved_by and row.saved_by not in TRUSTED_AUTHORS, row.edited_by and row.edited_by not in TRUSTED_AUTHORS,
        row.saved_at and not row.saved_by, row.edited_at and not row.edited_by,
    ))


def _sheet_mode(row, sheet) -> bool:
    """The ERP delivery as seeded (no record edit over it): rewrite the sheet itself, so the seed keeps owning it."""
    return sheet is not None and row.saved_by == ERP_TEMPLATE_AUTHOR and not (row.edited and row.edited_at)


def _documents(uow, code) -> int:
    """Read-only: stored ETK documents with an operation of this code (what the refresh job will re-derive)."""
    count = 0
    for document in uow.session.scalars(select(ProjectTechcardRow.document)):
        if isinstance(document, dict) and any(isinstance(op, dict) and op.get("code") == code
                                              for op in document.get("operations") or []):
            count += 1
    return count


def _columns(row) -> dict:
    columns = {key: deepcopy(getattr(row, key)) for key in COLUMNS}
    for key in ("edited_at", "saved_at"):
        columns[key] = _iso(columns[key])
    return columns


def _restore(row, columns) -> None:
    for key, value in columns.items():
        setattr(row, key, deepcopy(value))
    for key in ("edited_at", "saved_at"):
        setattr(row, key, datetime.fromisoformat(columns[key]) if columns[key] else None)


def _event(uow, who, now, event_type, details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=LIBRARY_AGGREGATE, event_type=event_type, actor=who,
                             occurred_at=now, details=details))


def apply_readability(uow, *, who, now, payload=None, sheets=None, expect=None):
    """Open UoW only; never commits. → (result, plan, rollback). `expect` — the dry-run plan: exactly these templates,
    signatures, timestamps and content hashes, or nothing is written."""
    payload = load_payload() if payload is None else payload
    sheets = erp_sheets() if sheets is None else sheets
    result = {key: [] for key in OUTCOMES}
    plan, rollback, writes = [], [], []
    for item in payload:
        code = item["code"]
        rows = list(uow.session.scalars(select(OperationCardTemplateRow).where(
            OperationCardTemplateRow.code == code).with_for_update()))
        if len(rows) != 1 or rows[0].id != item["template_id"]:
            result["missing"].append(f"{code}: ожидался шаблон {item['template_id']}, найдено {[row.id for row in rows]}")
            plan.append({"code": code, "outcome": "missing", "templates": sorted(row.id for row in rows)})
            continue
        row = rows[0]
        current = card_hash(uow.operation_cards.get(row.id))
        sheet = sheets.get(row.id)
        mode = "sheet" if _sheet_mode(row, sheet) else "record"
        if current == item["after_hash"]:
            outcome = "unchanged"
        elif _protected(row):
            outcome = "protected"
        elif current != item["expected_before_hash"]:
            outcome = "drifted"
        else:
            outcome = "changed"
        plan.append({"code": code, "template": row.id, "outcome": outcome, "mode": mode, "hash": current,
                     "edited_by": row.edited_by, "edited_at": _iso(row.edited_at),
                     "saved_by": row.saved_by, "saved_at": _iso(row.saved_at)})
        label = f"{code} [{row.id}]"
        if outcome == "drifted":
            result[outcome].append(f"{label}: содержимое {current[:12]} — не то, из которого собраны ЭТК; не заменяю")
        elif outcome == "protected":
            result[outcome].append(f"{label}: правка человека ({row.edited_by or row.saved_by or '?'}) — не заменяю")
        elif outcome == "unchanged":
            result[outcome].append(label)
        else:
            writes.append((item, row, mode, sheet))
    if expect is not None and expect.get("templates") != plan:
        raise ValueError("Шаблоны изменились после пробного прогона — сравните план и запустите пробный прогон заново")
    for item, row, mode, sheet in writes:
        code = item["code"]
        rollback.append({"code": code, "template": row.id, "after_hash": item["after_hash"], "columns": _columns(row)})
        if mode == "sheet":
            card = parse_operation_template(source_id=row.id, title=sheet["title"], category=sheet.get("category", ""),
                                            rows=sheet["rows"], images=sheet.get("images"))
            if card_hash(card) != item["after_hash"]:
                raise ValueError(f"{code}: лист поставки ERP этого релиза не совпадает с payload.json")
            uow.operation_cards.save_sheet(card, description=row.description, saved_at=now, saved_by=ERP_TEMPLATE_AUTHOR)
        else:
            uow.operation_cards.edit(code, normalize_record_edit(item["record"]), changed_at=now, changed_by=who.name)
        uow.session.flush()
        if card_hash(uow.operation_cards.get(row.id)) != item["after_hash"]:
            raise ValueError(f"{code}: после записи содержимое не равно payload.json")
        _event(uow, who, now, "techcards.operation_template_readability", {
            "code": code, "mode": mode, "before": item["expected_before_hash"], "after": item["after_hash"],
            "steps": str(len(item["record"]["steps"])), "reason": REASON})
        result["changed"].append(f"{code} [{row.id}]: {len(item['before_steps'])} → {len(item['record']['steps'])} шагов"
                                 f" ({'лист поставки ERP' if mode == 'sheet' else 'правка записи'}); "
                                 f"ЭТК с операцией: {_documents(uow, code)}")
    uow.session.flush()
    return result, {"templates": plan}, {"templates": rollback}


def rollback_readability(data, uow, *, who, now):
    """Restore the template columns only while every template is still exactly what this script wrote."""
    restored = []
    for item in data.get("templates", []):
        row = uow.session.get(OperationCardTemplateRow, item["template"], with_for_update=True)
        if row is None or card_hash(uow.operation_cards.get(row.id)) != item["after_hash"]:
            raise ValueError(f"{item['code']} [{item['template']}] изменился после записи — откат не выполнен")
        _restore(row, item["columns"])
        _event(uow, who, now, "techcards.operation_template_readability_rolled_back",
               {"code": item["code"], "reason": REASON})
        restored.append(item["code"])
    uow.session.flush()
    return restored


def _print(title, result):
    print(title)
    for key, values in result.items():
        print(f"  {key}: {len(values)}")
        for value in values:
            print(f"    {value}")


def main(argv=None):
    from app.database import build_engine
    from app.shared.clock import moscow_now

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон, всё откатывается)")
    parser.add_argument("--plan-out", type=Path, help="записать план пробного прогона (шаблоны, подписи, хеши)")
    parser.add_argument("--expect", type=Path, help="план пробного прогона: применить ровно его")
    parser.add_argument("--rollback-out", type=Path, help="куда записать прежние колонки шаблонов до записи")
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
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        if args.rollback:
            restored = rollback_readability(json.loads(args.rollback.read_text(encoding="utf-8")), uow,
                                            who=actor(ROLLBACK_AUTHOR), now=now)
            print(f"Откат: {len(restored)} шаблонов — {', '.join(restored)}")
        else:
            expect = json.loads(args.expect.read_text(encoding="utf-8")) if args.expect else None
            result, plan, rollback = apply_readability(uow, who=actor(), now=now, expect=expect)
            _print("Читаемые карты операций:", result)
            if args.plan_out:
                args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            if args.apply:
                with args.rollback_out.open("x", encoding="utf-8", newline="\n") as handle:  # never overwrite
                    handle.write(json.dumps(rollback, ensure_ascii=False, indent=1))
        if args.apply:
            uow.commit()
            print("COMMITTED — шаблоны записаны; обновление ЭТК поставлено в очередь шаблонов")
        else:
            print("dry run — rolled back (включая шаблоны, журнал и очередь ЭТК)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
