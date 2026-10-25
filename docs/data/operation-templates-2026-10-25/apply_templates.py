"""Шаблоны карт операций 25.10.2026 — три новых кода (SDR_QS8, SDR_SA, INS_housing_SA) со строками БД.ОП и
переименование INS_MARK_TUBE_AUTO → PRINT_MARK_TUBE — ОДНОЙ транзакцией.

Одна транзакция — один запрос пересчёта ЭТК (триггер app.workflows.etk_refresh).

    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_templates.py payload.json [--apply]
    ERP_DATABASE_URL=... PYTHONPATH=backend python apply_templates.py payload.json --rollback [--apply]

Без --apply — репетиция: каждая запись проверяется (`normalize_record_edit`), всё откатывается.

Запись payload — {"code", "record"} плюс по случаю:
- `create: {"category", "row": {"copy_of", "name", "norms": {колонка: значение}}}` — кода нет в библиотеке: карта создаётся
  тем же путём, что «Новая операция» (`create_operation_templates_in`), на неё ложится запись; строки БД.ОП с этим «Номером»
  нет — она создаётся здесь же (`create_operation_row_in`: копия строки `copy_of`, в ней правятся нормы `norms`; в
  событии — «норма по аналогу — проверить»); строки нет и `row` не задан — `no_row`, ничего не пишется;
- `rename_from: "СТАРЫЙ_КОД"` — переименование: карта кода (все стороны) меняет код и заголовок на месте, `id` карты
  (templateId в ЭТК и черновиках «Ручной сборки») остаётся прежним; строка БД.ОП «Номер» — на новый код. Каждая половина
  делается, только если она ещё не сделана (обновление сервера уже переименовало карту из поставки ERP — остаётся строка);
- иначе — код есть в библиотеке: запись ложится на все его стороны (`operation_cards.edit`), `unchanged`, если не изменилась.

--rollback: удаляет карты и строки БД.ОП, созданные этим скриптом (только если карту после записи никто не правил:
`edited_by` и `saved_by` — подпись скрипта), и возвращает прежний код у переименованных. Прежних записей он не пишет:
у переименованной карты запись не менялась (текст тот же), новые коды — новые.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.application import FieldEdit, _edit_in, create_operation_row_in, operation_code
from app.modules.references.domain import TechOperationTab
from app.modules.references.technology import schema_columns
from app.modules.techcards.application import create_operation_templates_in, new_operation
from app.modules.techcards.infrastructure import OperationCardTemplateRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import normalize_record_edit
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows import etk_refresh  # noqa: F401 — регистрирует триггер пересчёта ЭТК на запись шаблонов
from app.workflows.new_operation import LIBRARY_AGGREGATE, NewOperationUnitOfWork

AUTHOR = "Аудит ГОСТ Р 25.10.2026"
ROLLBACK_AUTHOR = "Откат аудита ГОСТ Р 25.10.2026"
REASON = "шаблоны SDR_QS8, SDR_SA, INS_housing_SA (норма по аналогу — проверить), PRINT_MARK_TUBE (переименование)"
OUTCOMES = ("changed", "unchanged", "created", "renamed", "missing", "no_row")


def actor(name: str = AUTHOR) -> Actor:
    return Actor(id=UUID(int=0), name=name, department="Технолог")


def _event(event_type: str, who: Actor, now: datetime, details: dict[str, str]) -> AuditEvent:
    return AuditEvent(id=uuid4(), aggregate_id=LIBRARY_AGGREGATE, event_type=event_type, actor=who, occurred_at=now,
                      details=details)


def _rows(uow: NewOperationUnitOfWork) -> dict[str, object]:
    return {operation_code(record.fields): record
            for record in uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)}


def _edit_row(record, changes: dict[str, str], now: datetime, uow: NewOperationUnitOfWork) -> None:
    """Ячейки строки БД.ОП по тем же правилам, что правка на экране (`_edit_in`): колонка схемы или имя колонки."""
    columns = schema_columns(TechOperationTab.OPERATION.value, record.fields)
    edits = []
    for key, value in changes.items():
        column = columns.get(key, key)
        if column not in record.fields:
            raise ValueError(f"В строке БД.ОП «{operation_code(record.fields)}» нет колонки «{column}»")
        if record.fields[column] != value:
            edits.append(FieldEdit(field=column, old=record.fields[column], value=value))
    if edits:
        _edit_in(record.id, tuple(edits), now, uow)


def _rename_cards(old: str, new: str, uow: NewOperationUnitOfWork) -> list[str]:
    """Код и заголовок карты `old` (все стороны) — на `new`; `id` остаётся. Возвращает id переименованных."""
    session = uow.session
    found = list(session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == old)))
    for row in found:
        head = f"{old} |"
        if not row.title.startswith(head) or not row.raw_rows or not str(row.raw_rows[0][0]).startswith(head):
            raise ValueError(f"Заголовок карты {row.id} не «{head} …» — переименование отменено")
        row.code = new
        row.title = new + row.title[len(old):]
        raw = [list(item) for item in row.raw_rows]
        raw[0][0] = new + str(raw[0][0])[len(old):]
        row.raw_rows = raw
        row.document = uow.operation_cards._domain(row).document()
    session.flush()
    return sorted(row.id for row in found)


def _rename(old: str, new: str, uow: NewOperationUnitOfWork, who: Actor, now: datetime, reason: str) -> bool:
    """Обе половины переименования, каждая — если ещё не сделана. True — что-то изменилось."""
    codes = {card.code for card in uow.operation_cards.list()}
    done: list[str] = []
    if old in codes and new not in codes:
        done += ["карта: " + "; ".join(_rename_cards(old, new, uow))]
    rows = _rows(uow)
    if old in rows and new not in rows:
        record = rows[old]
        _edit_row(record, {"opNumber": new}, now, uow)
        done.append("строка БД.ОП")
    if done:
        uow.audit.add(_event("techcards.operation_template_renamed", who, now,
                             {"old": old, "new": new, "done": "; ".join(done), "reason": reason}))
    return bool(done)


def apply_payload(payload: list[dict], uow: NewOperationUnitOfWork, *, who: Actor, now: datetime,
                  reason: str = REASON) -> dict[str, list[str]]:
    """Payload в открытую единицу работы (ничего не фиксирует). Возвращает коды по исходам."""
    result: dict[str, list[str]] = {outcome: [] for outcome in OUTCOMES}
    for item in payload:
        code = item["code"]
        edit = normalize_record_edit(item["record"])
        if item.get("rename_from") and _rename(item["rename_from"], code, uow, who, now, reason):
            result["renamed"].append(f"{item['rename_from']} → {code}")
        library = {}
        for card in uow.operation_cards.list():
            library.setdefault(card.code, card)
        current = library.get(code)
        if current is None:
            create = item.get("create")
            if create is None:
                result["missing"].append(code)
                continue
            rows = _rows(uow)
            spec = create.get("row")
            if code not in rows and spec is None:
                result["no_row"].append(code)
                continue
            cards = create_operation_templates_in(
                new_operation(code, edit.name, "", create.get("category", "")), who, now, uow)
            uow.operation_cards.edit(code, edit, changed_at=now, changed_by=who.name)
            made = ""
            if code not in rows:
                record = create_operation_row_in(code, spec["name"], spec["copy_of"], uow)
                _edit_row(record, spec["norms"], now, uow)
                made = f"строка БД.ОП по аналогу {spec['copy_of']}: " + "; ".join(
                    f"{key} = {value}" for key, value in spec["norms"].items()) + " — норма по аналогу, проверить"
            uow.audit.add(_event("techcards.operation_template_created", who, now, {
                "code": code, "name": edit.name, "templates": "; ".join(card.source_id for card in cards),
                "steps": str(len(edit.steps)), "tooling": str(len(edit.tooling)), "reason": reason, "row": made,
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


def delete_created(payload: list[dict], uow: NewOperationUnitOfWork, *, who: Actor, now: datetime,
                   author: str = AUTHOR) -> list[str]:
    """Удаляет карты и строки БД.ОП, созданные скриптом (подпись `author` в edited_by и saved_by). Карту, которую правили
    после записи, не трогает — ValueError, откат целиком."""
    codes = [item["code"] for item in payload if "create" in item]
    session = uow.session
    found = list(session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code.in_(codes))))
    foreign = sorted({row.code for row in found if row.edited_by != author or row.saved_by != author})
    if foreign:
        raise ValueError(f"Карты правили после записи, удаление отменено: {', '.join(foreign)}")
    for row in found:
        session.delete(row)
    deleted = sorted({row.code for row in found})
    rows = _rows(uow)
    for code in deleted:
        if code in rows and payload_row(payload, code):
            uow.tech_operations.delete(rows[code].id)
    if deleted:
        uow.audit.add(_event("techcards.operation_template_deleted", who, now, {
            "codes": "; ".join(deleted), "templates": "; ".join(sorted(row.id for row in found)),
            "reason": "откат шаблонов 25.10.2026",
        }))
    return deleted


def payload_row(payload: list[dict], code: str) -> bool:
    return any(item["code"] == code and (item.get("create") or {}).get("row") for item in payload)


def rollback_payload(payload: list[dict], uow: NewOperationUnitOfWork, *, who: Actor, now: datetime) -> dict[str, list]:
    deleted = delete_created(payload, uow, who=who, now=now)
    renamed = [f"{item['code']} → {item['rename_from']}" for item in payload
               if item.get("rename_from") and _rename(item["code"], item["rename_from"], uow, who, now,
                                                      "откат шаблонов 25.10.2026")]
    return {"deleted": deleted, "renamed": renamed}


def main(argv: list[str]) -> int:
    from app.database import build_engine
    from app.shared.clock import moscow_now

    args = [arg for arg in argv if not arg.startswith("--")]
    apply = "--apply" in argv
    payload = json.load(open(args[0], encoding="utf-8"))
    engine = build_engine(os.environ["ERP_DATABASE_URL"])
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    now = moscow_now()
    with NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)) as uow:
        uow.serialise()
        if "--rollback" in argv:
            result = rollback_payload(payload, uow, who=actor(ROLLBACK_AUTHOR), now=now)
            bad = []
        else:
            result = apply_payload(payload, uow, who=actor(), now=now)
            bad = result["missing"] or result["no_row"]
        for outcome, codes in result.items():
            print(f"{outcome}: {len(codes)}" + (f" — {', '.join(codes)}" if codes and outcome != "unchanged" else ""))
        if apply and not bad:
            uow.commit()
            print("COMMITTED")
        else:
            print("dry run — rolled back" if not bad else "ошибки — rolled back")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
