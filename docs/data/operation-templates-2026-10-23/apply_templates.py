"""Шаблоны карт операций 23.10.2026 — правка по коду и создание недостающих — ОДНОЙ транзакцией.

Одна транзакция — один запрос пересчёта ЭТК (триггер app.workflows.etk_refresh) — одна автоматическая ревизия на карту.

    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_templates.py payload.json [--apply]
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_templates.py payload.json \
        --rollback ../operation-templates-2026-10-22/templates-after.json [--apply]

Без --apply — репетиция: каждая запись проверяется (`normalize_record_edit`), всё откатывается.

Запись payload — {"code", "record"} или {"code", "create": {"category"}, "record"}:
- код есть в библиотеке — запись ложится на все его стороны A/B (`operation_cards.edit`), событие
  `techcards.operation_record_edited`; запись не изменилась — `unchanged`;
- кода нет, есть `create` и строка БД.ОП с этим «Номером» — карта создаётся тем же путём, что «Новая операция»
  (`create_operation_templates_in`: пустой лист ERP), на неё ложится запись, событие `techcards.operation_template_created`;
  строки БД.ОП нет — `no_row`, шаблон без строки не создаётся;
- кода нет и `create` нет — `missing`.

--rollback: в той же транзакции удаляет карты, созданные этим скриптом (коды с `create`, только если их никто не правил
после записи: `edited_by` и `saved_by` — подпись скрипта), и пишет прежние записи из файла отката.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.application import operation_code
from app.modules.references.domain import TechOperationTab
from app.modules.techcards.application import create_operation_templates_in, new_operation
from app.modules.techcards.infrastructure import OperationCardTemplateRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import normalize_record_edit
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows import etk_refresh  # noqa: F401 — регистрирует триггер пересчёта ЭТК на запись шаблонов
from app.workflows.new_operation import LIBRARY_AGGREGATE, NewOperationUnitOfWork

AUTHOR = "Аудит ГОСТ Р 23.10.2026"
ROLLBACK_AUTHOR = "Откат аудита ГОСТ Р 23.10.2026"
REASON = "шаблоны по ГОСТ Р 72182.2/.3/.4/.9, ГОСТ 23586-96, 23594-79, 13317-89; СТО ФРАК-001-2026 ред. 3"


def actor(name: str = AUTHOR) -> Actor:
    return Actor(id=UUID(int=0), name=name, department="Технолог")


def _event(event_type: str, who: Actor, now: datetime, details: dict[str, str]) -> AuditEvent:
    return AuditEvent(id=uuid4(), aggregate_id=LIBRARY_AGGREGATE, event_type=event_type, actor=who, occurred_at=now,
                      details=details)


def apply_payload(payload: list[dict], uow: NewOperationUnitOfWork, *, who: Actor, now: datetime,
                  reason: str = REASON) -> dict[str, list[str]]:
    """Payload в открытую единицу работы (ничего не фиксирует). Возвращает коды по исходам."""
    result: dict[str, list[str]] = {"changed": [], "unchanged": [], "created": [], "missing": [], "no_row": []}
    library: dict[str, object] = {}
    for card in uow.operation_cards.list():
        library.setdefault(card.code, card)
    rows = {operation_code(record.fields)
            for record in uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)}
    for item in payload:
        code = item["code"]
        edit = normalize_record_edit(item["record"])
        current = library.get(code)
        if current is None:
            create = item.get("create")
            if create is None:
                result["missing"].append(code)
                continue
            if code not in rows:
                result["no_row"].append(code)
                continue
            cards = create_operation_templates_in(
                new_operation(code, edit.name, "", create.get("category", "")), who, now, uow)
            uow.operation_cards.edit(code, edit, changed_at=now, changed_by=who.name)
            uow.audit.add(_event("techcards.operation_template_created", who, now, {
                "code": code, "name": edit.name, "templates": "; ".join(card.source_id for card in cards),
                "steps": str(len(edit.steps)), "tooling": str(len(edit.tooling)), "reason": reason,
            }))
            result["created"].append(code)
            continue
        same = (current.name, current.safety, current.defects, current.steps, current.tooling) == (
            edit.name, edit.safety, edit.defects, edit.steps, edit.tooling)
        if same:
            result["unchanged"].append(code)
            continue
        uow.operation_cards.edit(code, edit, changed_at=now, changed_by=who.name)
        uow.audit.add(_event("techcards.operation_record_edited", who, now, {
            "code": code, "steps": str(len(edit.steps)), "tooling": str(len(edit.tooling)), "reason": reason,
        }))
        result["changed"].append(code)
    return result


def delete_created(codes: list[str], uow: NewOperationUnitOfWork, *, who: Actor, now: datetime,
                   author: str = AUTHOR) -> list[str]:
    """Удаляет карты, созданные скриптом (подпись `author` в edited_by и saved_by). Карту, которую правили после
    записи, не трогает — ValueError, откат целиком."""
    session = uow.session
    found = list(session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code.in_(codes))))
    foreign = sorted({row.code for row in found if row.edited_by != author or row.saved_by != author})
    if foreign:
        raise ValueError(f"Карты правили после записи, удаление отменено: {', '.join(foreign)}")
    for row in found:
        session.delete(row)
    deleted = sorted({row.code for row in found})
    if deleted:
        uow.audit.add(_event("techcards.operation_template_deleted", who, now, {
            "codes": "; ".join(deleted), "templates": "; ".join(sorted(row.id for row in found)),
            "reason": "откат шаблонов 23.10.2026",
        }))
    return deleted


def main(argv: list[str]) -> int:
    from app.database import build_engine
    from app.shared.clock import moscow_now

    args = [arg for arg in argv if not arg.startswith("--")]
    apply = "--apply" in argv
    payload = json.load(open(args[0], encoding="utf-8"))
    rollback = argv[argv.index("--rollback") + 1] if "--rollback" in argv else None
    engine = build_engine(os.environ["ERP_DATABASE_URL"])
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    now = moscow_now()
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        if rollback:
            who = actor(ROLLBACK_AUTHOR)
            deleted = delete_created([item["code"] for item in payload if "create" in item], uow, who=who, now=now)
            print("deleted", deleted)
            result = apply_payload(json.load(open(rollback, encoding="utf-8")), uow, who=who, now=now,
                                   reason="откат шаблонов 23.10.2026")
        else:
            result = apply_payload(payload, uow, who=actor(), now=now)
        for outcome, codes in result.items():
            print(f"{outcome}: {len(codes)}" + (f" — {', '.join(codes)}" if codes and outcome != "unchanged" else ""))
        bad = result["missing"] or result["no_row"]
        if apply and not bad:
            uow.commit()
            print("COMMITTED")
        else:
            print("dry run — rolled back" if not bad else "ошибки — rolled back")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
