"""Write audited operation-card templates in ONE transaction (one ETK refresh request → one auto revision per card).

usage: ERP_DATABASE_URL=... python apply_templates.py <payload.json> [--apply]
Without --apply: validates every record and rolls back. Prints per code: changed / unchanged / missing.
"""
import json
import os
import sys
from uuid import UUID, uuid4

from app.database import build_engine, build_techcards_uow_factory
from app.modules.techcards.operation_cards import normalize_record_edit
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor

payload = json.load(open(sys.argv[1], encoding="utf-8"))
apply = "--apply" in sys.argv
engine = build_engine(os.environ["ERP_DATABASE_URL"])
uow = build_techcards_uow_factory(engine)()
now = moscow_now()
actor = Actor(id=UUID(int=0), name="Аудит ГОСТ Р 22.10.2026", department="Технолог")
stats = {"changed": 0, "unchanged": 0, "missing": 0}
with uow:
    for item in payload:
        edit = normalize_record_edit(item["record"])
        current = next((card for card in uow.operation_cards.list() if card.code == item["code"]), None)
        if current is None:
            stats["missing"] += 1
            print("missing", item["code"])
            continue
        same = (current.name, current.safety, current.defects, current.steps, current.tooling) == (
            edit.name, edit.safety, edit.defects, edit.steps, edit.tooling)
        if same:
            stats["unchanged"] += 1
            continue
        uow.operation_cards.edit(item["code"], edit, changed_at=now, changed_by="Аудит ГОСТ Р 22.10.2026")
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=UUID("00000000-0000-0000-0000-000000000018"),
            event_type="techcards.operation_record_edited", actor=actor, occurred_at=now,
            details={"code": item["code"], "steps": str(len(edit.steps)), "tooling": str(len(edit.tooling)),
                     "reason": "аудит шаблонов по ГОСТ Р, ссылки на пункты стандартов"},
        ))
        stats["changed"] += 1
    print(stats)
    if apply:
        uow.commit()
        print("COMMITTED")
    else:
        print("dry run — rolled back")
