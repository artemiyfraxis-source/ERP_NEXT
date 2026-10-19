"""A stored ETK card rebuilt from its inputs (owner, 19.10.2026: a reference or rule change issues a new revision of
every affected card by itself, keeping the technologist's hand edits).

`derive` is a pure function: the raw generator config (`inputs`), the technologist's overrides and the library
templates give the document. It touches no database — the references enrichment comes in as a callable, the templates
as a list. `builder_hash` and `derived_hash` tell whether a stored card is still what the builder gives today;
`document_diff` says, in the owner's words, what a new derivation changed.

Overrides (what a rebuild must keep), stored by the card:
    {"operations": {operation_key: {"base_template_version": str, "operation": {the operation as edited}}},
     "route": {"blocks": [{"key", "name", "final", "kit"?, "ops": [operation_key, …]}],
               "edges": [{"from", "to", "per_unit", "item"?}]}}
`operation_key` is `code::connector::side` (project_cards). The route is the ПФ scheme cut by hand, its blocks naming
operations by key instead of by place, so it survives a rebuild that moves the operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import hashlib
import inspect
import json
from typing import Any

from . import etk_builder, etk_live, etk_marking, operation_cards, project_cards
from .domain import pf_split, route as route_domain
from .etk_builder import _route_ops, build_etk
from .etk_live import VIEW_KEYS, stamp_templates
from .operation_cards import OperationCardTemplate
from .project_cards import TechcardValidationError, operation_key

NO_INPUTS = "no_inputs"
BUILD_FAILED = "build_failed"
MISSING_OPERATION = "missing_operation"
TEMPLATE_CHANGED = "template_changed"
ROUTE_NOT_APPLIED = "route_not_applied"
NO_INPUTS_MESSAGE = "нет входных данных для пересборки"

# The modules whose code decides what `build_etk` and `stamp_templates` give.
_BUILDER_MODULES = (etk_builder, etk_live, etk_marking, operation_cards, project_cards, pf_split, route_domain)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@lru_cache(maxsize=1)
def builder_hash() -> str:
    """A stable hash of the builder's source (line endings normalised, so Windows and the server agree): a deploy that
    changes the builder changes it, and every stored card is then due for a check."""
    digest = hashlib.sha256()
    for module in _BUILDER_MODULES:
        digest.update(module.__name__.encode("utf-8"))
        digest.update(inspect.getsource(module).replace("\r\n", "\n").encode("utf-8"))
    return digest.hexdigest()[:16]


def derived_hash(
    inputs: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None, refs_stamp: object,
    template_versions: Mapping[str, str] | None, builder: str,
) -> str:
    """What the derived document depends on: the inputs, the overrides, the references stamp, the versions of the
    templates and the builder. Equal hash — equal document, nothing to reissue."""
    payload = _canonical([inputs or {}, overrides or {}, refs_stamp, dict(template_versions or {}), builder])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _conflict(kind: str, message: str, key: str = "") -> dict[str, str]:
    return {"kind": kind, "operation_key": key, "message": message}


def _key(op: Mapping[str, Any]) -> str | None:
    try:
        return operation_key(dict(op))
    except TechcardValidationError:
        return None


def _label(op: Mapping[str, Any]) -> str:
    title = str(op.get("title") or op.get("code") or "").strip()
    extra = " ".join(str(op.get(name) or "").strip() for name in ("connector", "side")).strip()
    return f"{title} ({extra})" if extra and extra not in title else title


def derive(
    inputs: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None,
    enrich: Callable[[Mapping[str, Any]], Mapping[str, Any]], templates: Sequence[OperationCardTemplate],
    *, operation_library_commit: str = "", technology_source: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """(document, conflicts). `inputs` — the raw generator config, not enriched; `enrich` — config → config (the
    references norms); the builder and the template stamp follow, then the overrides. An override whose operation is
    gone is returned in `conflicts`, never dropped. A card without inputs cannot be derived: (None, [no_inputs])."""
    if not isinstance(inputs, Mapping) or not inputs:
        return None, [_conflict(NO_INPUTS, NO_INPUTS_MESSAGE)]
    try:
        document = stamp_templates(build_etk(
            enrich(deepcopy(dict(inputs))), templates, operation_library_commit=operation_library_commit,
            technology_source=technology_source,
        ), templates)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, TechcardValidationError) as error:
        return None, [_conflict(BUILD_FAILED, f"сборка не удалась: {error}")]
    overrides = overrides if isinstance(overrides, Mapping) else {}
    conflicts: list[dict[str, str]] = []
    document = _apply_operations(document, overrides.get("operations"), conflicts)
    document = _apply_route(document, overrides.get("route"), conflicts)
    return document, conflicts


def _apply_operations(document: dict[str, Any], edits: object, conflicts: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(edits, Mapping) or not edits:
        return document
    operations = list(document["operations"])
    index_by_key = {_key(op): index for index, op in enumerate(operations)}
    versions = (document.get("source") or {}).get("templates") or {}
    for key, edit in edits.items():
        edited = edit.get("operation") if isinstance(edit, Mapping) else None
        index = index_by_key.get(key)
        if index is None:
            conflicts.append(_conflict(
                MISSING_OPERATION, "операции, которую правил технолог, в пересобранной карте больше нет", key))
            continue
        if not isinstance(edited, Mapping):
            continue
        base = operations[index]
        template_id = str(base.get("template_id") or "").strip()
        base_version = str(edit.get("base_template_version") or "")
        if base_version and template_id in versions and versions[template_id] != base_version:
            conflicts.append(_conflict(
                TEMPLATE_CHANGED, "шаблон операции изменился после правки технолога — правка оставлена как была", key))
        operations[index] = {
            **{name: value for name, value in deepcopy(dict(edited)).items() if name not in VIEW_KEYS},
            "position": base.get("position"),
        }
    return {**document, "operations": operations}


def _apply_route(document: dict[str, Any], stored: object, conflicts: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(stored, Mapping) or not stored.get("blocks"):
        return document
    positions = {_key(op): index for index, op in enumerate(document["operations"], start=1)}
    raw_blocks = []
    missing = False
    for block in stored["blocks"]:
        if not isinstance(block, Mapping):
            continue
        ops = [positions.get(key) for key in block.get("ops") or []]
        for key, position in zip(block.get("ops") or [], ops):
            if position is None:
                missing = True
                conflicts.append(_conflict(MISSING_OPERATION, "операции, на которой построен ПФ, в карте больше нет", str(key)))
        raw_blocks.append({**block, "ops": [position for position in ops if position is not None]})
    if missing:
        return document
    try:
        ops, final_name, quantity = _route_ops(document, strict=False)
        route = route_domain.parse_route({**stored, "blocks": raw_blocks}, ops, final_name, manual=True)
        route = replace(route, generated=stored.get("source") == "generated")
        totals = route_domain.route_totals(route, {op.position: op.minutes for op in ops}, quantity)
        return {**document, "route": route_domain.route_json(route, totals)}
    except (route_domain.RouteError, AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        conflicts.append(_conflict(ROUTE_NOT_APPLIED, f"схема ПФ, нарезанная вручную, не подошла: {error}"))
        return document


def route_override(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The hand-cut ПФ scheme of a stored document in the override form (blocks name operations by key); None — no
    manual scheme, or its blocks name places the operations do not have."""
    route = document.get("route") if isinstance(document, Mapping) else None
    operations = document.get("operations") if isinstance(document, Mapping) else None
    if not isinstance(route, Mapping) or route.get("source") != "manual" or not isinstance(operations, list):
        return None
    keys = [_key(op) if isinstance(op, Mapping) else None for op in operations]
    blocks = []
    for block in route.get("blocks") or []:
        try:
            names = [keys[int(position) - 1] for position in block["ops"]]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if None in names:
            return None
        blocks.append({name: block[name] for name in ("key", "name", "final", "kit", "components") if name in block}
                      | {"ops": names})
    return {"source": "manual", "blocks": blocks, "edges": deepcopy(list(route.get("edges") or []))}


# ── The diff: what a new derivation changed, for the owner's journal ──

def _text(value: object) -> str:
    if isinstance(value, Mapping):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items() if _text(item))
    if isinstance(value, (list, tuple)):
        return "\n".join(part for part in (_text(item) for item in value) if part)
    return "" if value is None else str(value).strip()


def _row(row: object) -> str:
    return " — ".join(str(value).strip() for value in row.values() if str(value or "").strip()) \
        if isinstance(row, Mapping) else _text(row)


def _rows(rows: object) -> str:
    return "\n".join(part for part in (_row(row) for row in rows or []) if part) if isinstance(rows, list) else ""


def _steps(card: object) -> str:
    steps = card.get("steps") if isinstance(card, Mapping) else None
    return "\n".join(
        f"{number}. {_text(step.get('text'))}" + (f" ({_text(step['tolerance'])})" if _text(step.get("tolerance")) else "")
        for number, step in enumerate(steps or [], start=1) if isinstance(step, Mapping))


def _tooling(card: object) -> str:
    tooling = card.get("tooling") if isinstance(card, Mapping) else None
    return "\n".join(
        " — ".join(part for part in (_text(tool.get("section")), _text(tool.get("name")), _text(tool.get("parameters")))
                   if part) for tool in tooling or [] if isinstance(tool, Mapping))


def _field_depth(op: Mapping[str, Any]) -> str:
    fields = op.get("fields") if isinstance(op.get("fields"), Mapping) else {}
    end = fields.get("coaxEnd") if isinstance(fields.get("coaxEnd"), Mapping) else {}
    return _text(fields.get("contactDepth")) or _text(end.get("contactDepth"))


def _other_fields(op: Mapping[str, Any]) -> dict[str, Any]:
    fields = op.get("fields") if isinstance(op.get("fields"), Mapping) else {}
    rest = {key: value for key, value in fields.items() if key != "contactDepth"}
    end = rest.get("coaxEnd")
    if isinstance(end, Mapping):
        rest["coaxEnd"] = {key: value for key, value in end.items() if key != "contactDepth"}
    return rest


# (Russian label, how the text is read from an operation); the order is the order of the journal.
_FIELDS: tuple[tuple[str, Callable[[Mapping[str, Any]], str]], ...] = (
    ("Результат", lambda op: _rows(op.get("result"))),
    ("Принимает", lambda op: _rows(op.get("semis"))),
    ("Комплектующие", lambda op: _rows(op.get("components"))),
    ("Норма времени", lambda op: _rows(op.get("time"))),
    ("Ход операции", lambda op: _steps(op.get("card"))),
    ("Инструмент", lambda op: _tooling(op.get("card"))),
    ("Безопасность", lambda op: _text((op.get("card") or {}).get("safety") if isinstance(op.get("card"), Mapping) else "")),
    ("Брак", lambda op: _text((op.get("card") or {}).get("defects") if isinstance(op.get("card"), Mapping) else "")),
    ("Заглубление контакта", _field_depth),
    ("Параметры операции", lambda op: _text(_other_fields(op))),
)


def _route_text(document: Mapping[str, Any]) -> str:
    route = document.get("route")
    blocks = route.get("blocks") if isinstance(route, Mapping) else None
    return "\n".join(f"{block.get('label') or block.get('key')}: {block.get('name')}, операции {block.get('ops')}"
                     for block in blocks or [] if isinstance(block, Mapping))


def _by_key(document: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    operations = document.get("operations") if isinstance(document, Mapping) else None
    return {key: op for op in operations or [] if isinstance(op, Mapping) and (key := _key(op)) is not None}


def document_diff(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """What differs between two documents of one card: [{operation_key, label, field, old, new}], `field` in Russian.
    The volatile stamps (`source`, `edited`, view keys, the schema version, operation places) are ignored."""
    before, after = _by_key(old), _by_key(new)
    changes: list[dict[str, str]] = []
    for key in dict.fromkeys([*before, *after]):
        op_old, op_new = before.get(key), after.get(key)
        label = _label(op_new or op_old or {})
        if op_old is None or op_new is None:
            changes.append({"operation_key": key, "label": label, "field": "Операция",
                            "old": "" if op_old is None else "есть", "new": "" if op_new is None else "есть"})
            continue
        for field, read in _FIELDS:
            left, right = read(op_old), read(op_new)
            if left != right:
                changes.append({"operation_key": key, "label": label, "field": field, "old": left, "new": right})
    left, right = _route_text(old or {}), _route_text(new or {})
    if left != right:
        changes.append({"operation_key": "", "label": "Схема ПФ", "field": "Схема ПФ", "old": left, "new": right})
    warnings_old = _text((old or {}).get("warnings"))
    warnings_new = _text((new or {}).get("warnings"))
    if warnings_old != warnings_new:
        changes.append({"operation_key": "", "label": "Карта", "field": "Предупреждения сборки",
                        "old": warnings_old, "new": warnings_new})
    return changes
