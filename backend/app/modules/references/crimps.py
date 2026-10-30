"""«Что обжимает» for a crimp tool or a matrix of БД.ОБ: terminals (and lugs — they are БД.ТЕР rows too) by the code in
БД.ТЕР «Аппликатор», connector series by a list kept on the tool record. Writes follow the edit path: audit event,
`edited_in_erp`, so a mirror reload keeps them."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.shared.identity import Actor

from .application import UnitOfWork, _audit, _equipment_rows, _search_text
from .domain import TERMINAL_TABS, ReferenceValidationError, TechOperationRecord, TechOperationTab
from .equipment import (
    CODE_KEY, TARGETS_KEY, add_code, applicator_keys, applicator_value, dump_targets, field, has_code, name_of,
    parse_targets, remove_code, tokens,
)


def _tool(uow: UnitOfWork, tool_id: UUID) -> TechOperationRecord:
    tool = uow.tech_operations.get(tool_id)
    if tool is None or tool.tab != TechOperationTab.EQUIPMENT:
        raise LookupError("Инструмент не найден")
    return tool


def _terminals(uow: UnitOfWork) -> list[TechOperationRecord]:
    return list(uow.tech_operations.list(tab=TERMINAL_TABS, query="", limit=40_000))  # contacts and lugs


def _terminal_json(record: TechOperationRecord, code: str) -> dict:
    fields = record.fields
    value = applicator_value(fields)
    return {
        "id": str(record.id), "display_text": record.display_text,
        "article": field(fields, "terArticle", "Артикул контакта (REEL)", "Артикул\nконтакта (REEL)"),
        "series": field(fields, "terSeries", "Series"), "maker": field(fields, "terManufacturer", "Производитель"),
        "kind": field(fields, "Тип разъема", "Тип\nразъема", "connType"),
        "applicator": value, "doubtful": not has_code(value, code) and has_code(value, code, sure_only=False),
    }


def crimp_overview(tool_id: UUID, uow: UnitOfWork) -> dict:
    with uow:
        tool = _tool(uow, tool_id)
        code = field(tool.fields, CODE_KEY).upper()
        terminals = [_terminal_json(item, code) for item in _terminals(uow)
                     if code and has_code(applicator_value(item.fields), code, sure_only=False)]
        return {"code": code, "terminals": terminals, "targets": parse_targets(tool.fields.get(TARGETS_KEY, ""))}


def crimp_codes(uow: UnitOfWork) -> dict:
    """Equipment record by tool code, and the codes БД.ТЕР uses that no equipment record owns."""
    with uow:
        owners = {field(fields, CODE_KEY).upper(): str(record_id) for record_id, fields in _equipment_rows(uow)
                  if field(fields, CODE_KEY)}
        used: dict[str, int] = {}
        for item in _terminals(uow):
            for code in {token.code for token in tokens(applicator_value(item.fields))}:
                used[code] = used.get(code, 0) + 1
    missing = [{"code": code, "count": count}
               for code, count in sorted(used.items(), key=lambda pair: (pair[0][0], int(pair[0][1:]))) if code not in owners]
    return {"owners": owners, "missing": missing}


def _save(uow: UnitOfWork, record: TechOperationRecord, fields: dict[str, str], event: str, actor: Actor,
          now: datetime, details: dict) -> TechOperationRecord:
    updated = replace(record, fields=fields, edited_in_erp=True)
    updated = replace(updated, normalized_search=_search_text(updated))
    uow.tech_operations.save(updated)
    _audit(event, actor, now, {"record_id": str(record.id), "tab": record.tab.value, **details}, uow)
    return updated


def change_terminal(tool_id: UUID, terminal_id: UUID, *, add: bool, actor: Actor, now: datetime, uow: UnitOfWork) -> dict:
    with uow:
        code = field(_tool(uow, tool_id).fields, CODE_KEY).upper()
        if not code:
            raise ReferenceValidationError("У инструмента нет кода в базе терминалов — задайте его в записи")
        terminal = uow.tech_operations.get(terminal_id)
        if terminal is None or terminal.tab not in TERMINAL_TABS:
            raise LookupError("Терминал не найден")
        current = applicator_value(terminal.fields)
        value = add_code(current, code) if add else remove_code(current, code)
        fields = dict(terminal.fields)
        for key in applicator_keys(fields) or ["terApplicator"]:
            fields[key] = value
        updated = _save(uow, terminal, fields, "references.crimp_link_changed", actor, now,
                        {"tool_id": str(tool_id), "code": code, "action": "add" if add else "remove"})
        uow.commit()
    return _terminal_json(updated, code)


def change_target(tool_id: UUID, target: dict[str, str], *, add: bool, actor: Actor, now: datetime,
                  uow: UnitOfWork) -> list[dict[str, str]]:
    if not target.get("key") or not target.get("kind"):
        raise ReferenceValidationError("Не указано, что обжимает инструмент")
    with uow:
        tool = _tool(uow, tool_id)
        items = [item for item in parse_targets(tool.fields.get(TARGETS_KEY, ""))
                 if (item["kind"], item["key"]) != (target["kind"], target["key"])]
        if add:
            items.append({"kind": target["kind"], "key": target["key"], "label": target.get("label", "") or target["key"]})
        fields = dict(tool.fields)
        fields[TARGETS_KEY] = dump_targets(items)
        _save(uow, tool, fields, "references.crimp_target_changed", actor, now,
              {"kind": target["kind"], "key": target["key"], "action": "add" if add else "remove",
               "name": name_of(tool.fields)})
        uow.commit()
    return items
