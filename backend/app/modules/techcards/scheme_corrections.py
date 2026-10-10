"""Журнал правок «Схемы полуфабрикатов» (owner, 10.10.2026): every time a techcard is built with a block scheme that
differs from the generator's automatic one, the build writes `techcards.scheme_corrected` into the audit journal (same
transaction, no table of its own). The agent pulls them all later to find what the generator gets wrong systematically.

Two ways a scheme gets corrected:
- «типовая»: the generator tab sends its hand-edited `config.route`; the automatic scheme is `derive_route` over the
  operations the build made (in the order the config gives them).
- «ручная из генератора»: «Поправить вручную» turned the generator config into a «Ручная сборка» one; the client sends
  that generator config as `generator_origin`, and the automatic scheme is rebuilt from it the way
  `/assembly/from-generator` converts it (without the generator's own hand-drawn route — the automatic one is wanted).
  The origin is only an input to that rebuild, never taken as a scheme.

Operations are matched by their place in the generator config: the position in «типовая», `seq` in the manual config
(the conversion sets `seq` = the generator position, new operations get new numbers). Block names are not compared —
only what blocks hold, the arrows between them and their «шт на 1».

    ERP_DATABASE_URL=... python -m app.modules.techcards.scheme_corrections --since 2026-10-01 [--project 708К.4] [--json]
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import os
import re
import sys
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.clock import MOSCOW
from app.shared.persistence import AuditEventRow

from .assembly_schema import generator_to_assembly
from .domain.route import derive_route
from .etk_builder import MANUAL_MODE, _route_ops
from .kitsets import project_key

EVENT = "techcards.scheme_corrected"
TYPICAL = "типовая"
FROM_GENERATOR = "ручная из генератора"
LIST_LIMIT = 1000

_SEQ = re.compile(r"[1-9]\d{0,5}")


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _number(value: object) -> int | float:
    try:
        number = Decimal(str(value)) if value not in (None, "") else Decimal(1)
    except InvalidOperation:
        number = Decimal(1)
    if not number.is_finite():
        number = Decimal(1)
    number = number.quantize(Decimal("0.0001"))
    return int(number) if number == number.to_integral_value() else float(number)


def _shown(value: int | float) -> str:
    return str(value).replace(".", ",")


def light_route(route: Mapping[str, Any] | None) -> dict[str, Any]:
    """A scheme as it is compared and logged: blocks (key, name, operation numbers, final) and arrows with «шт на 1»."""
    route = route if isinstance(route, Mapping) else {}
    return {
        "blocks": [{
            "key": _text(block.get("key")), "name": _text(block.get("name")),
            "ops": [int(item) for item in _list(block.get("ops"))], "final": bool(block.get("final")),
        } for block in _list(route.get("blocks")) if isinstance(block, Mapping)],
        "edges": [{
            "from": _text(edge.get("from")), "to": _text(edge.get("to")), "per_unit": _number(edge.get("per_unit")),
        } for edge in _list(route.get("edges")) if isinstance(edge, Mapping)],
    }


def _operation_label(operation: Mapping[str, Any], source: Mapping[str, Any] | None = None) -> str:
    card = operation.get("card") if isinstance(operation.get("card"), Mapping) else {}
    source = source or {}
    return (_text(source.get("label")) or _text(card.get("name")) or _text(operation.get("title"))
            or _text(operation.get("code")) or _text(operation.get("opNum")) or _text(operation.get("type")))


def _document_ops(document: Mapping[str, Any], config_ops: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    operations = [item for item in _list(document.get("operations")) if isinstance(item, Mapping)]
    aligned = len(config_ops) == len(operations)
    return [{
        "position": position, "code": _text(operation.get("code")), "type": _text(operation.get("type")),
        "label": _operation_label(operation, config_ops[position - 1] if aligned else None),
    } for position, operation in enumerate(operations, start=1)]


def _seqs(config_ops: Sequence[Mapping[str, Any]]) -> list[int]:
    """`seq` of every manual operation, as `_build_manual` takes it: all valid and distinct, else the positions."""
    seqs = [_text(item.get("seq")) for item in config_ops]
    if all(_SEQ.fullmatch(seq) for seq in seqs) and len(set(seqs)) == len(seqs):
        return [int(seq) for seq in seqs]
    return list(range(1, len(config_ops) + 1))


class _Scheme:
    """A light route with its operations renumbered into ids shared by the automatic and the final scheme."""

    def __init__(self, route: Mapping[str, Any], ids: Mapping[int, int], labels: Mapping[int, str]):
        self.blocks = [{**block, "ops": [ids.get(item, -item) for item in block["ops"]]} for block in route["blocks"]]
        self.edges = {(edge["from"], edge["to"]): edge["per_unit"] for edge in route["edges"]}
        self.by_key = {block["key"]: block for block in self.blocks}
        self.owner = {item: block["key"] for block in self.blocks for item in block["ops"]}
        # Operations in the scheme's own numbering (1..N) — the order they are done in.
        self.order = [ids[position] for position in sorted(ids)]
        self.labels = dict(labels)

    def name(self, key: str) -> str:
        block = self.by_key.get(key)
        return f"«{(block and block['name']) or key}»"

    def final(self) -> str:
        return next((block["key"] for block in self.blocks if block["final"]), "")


def _match(auto: _Scheme, final: _Scheme) -> dict[str, str]:
    """Automatic block → final block, greedily by the most operations in common (the same key wins a tie)."""
    pairs = []
    for a_index, a in enumerate(auto.blocks):
        for f_index, f in enumerate(final.blocks):
            common = len(set(a["ops"]) & set(f["ops"]))
            if common:
                pairs.append((-common, a["key"] != f["key"], a_index, f_index, a["key"], f["key"]))
    result: dict[str, str] = {}
    taken: set[str] = set()
    for *_, a_key, f_key in sorted(pairs):
        if a_key not in result and f_key not in taken:
            result[a_key] = f_key
            taken.add(f_key)
    return result


def scheme_changes(auto: _Scheme, final: _Scheme) -> list[str]:
    """What the person changed in the automatic scheme, one Russian line per change; empty — nothing."""
    lines: list[str] = []
    match = _match(auto, final)
    matched = set(match.values())

    def label(item: int) -> str:
        return f"«{final.labels.get(item) or auto.labels.get(item) or item}»"

    def ops_of(scheme: _Scheme, block: Mapping[str, Any]) -> str:
        return ", ".join(f"«{scheme.labels.get(item) or item}»" for item in block["ops"])

    for block in auto.blocks:
        if block["key"] not in match:
            lines.append(f"блок убран: {auto.name(block['key'])} (операции: {ops_of(auto, block)})")
    for block in final.blocks:
        if block["key"] not in matched:
            lines.append(f"блок добавлен: {final.name(block['key'])} (операции: {ops_of(final, block)})")
    auto_final, final_final = auto.final(), final.final()
    if match.get(auto_final) != final_final:
        lines.append(f"итоговый блок изменён: было {auto.name(auto_final)} → стало {final.name(final_final)}")

    for item in final.order:
        if item not in auto.owner:
            lines.append(f"операция добавлена: {label(item)} — в блок {final.name(final.owner[item])}")
    for item in auto.order:
        if item not in final.owner:
            lines.append(f"операция убрана: {label(item)} — была в блоке {auto.name(auto.owner[item])}")
    for item in final.order:
        if item in auto.owner and match.get(auto.owner[item]) != final.owner[item]:
            lines.append(f"операция перенесена: {label(item)} — из {auto.name(auto.owner[item])} "
                         f"в {final.name(final.owner[item])}")
    common = set(auto.order) & set(final.order)
    before = [item for item in auto.order if item in common]
    after = [item for item in final.order if item in common]
    if before != after:
        lines.append("порядок операций изменён: было " + ", ".join(str(item) for item in before)
                     + " → стало " + ", ".join(str(item) for item in after))

    mapped = {(match.get(source, f"~{source}"), match.get(target, f"~{target}")): (source, target, per_unit)
              for (source, target), per_unit in auto.edges.items()}
    for (source, target), per_unit in final.edges.items():
        if (source, target) not in mapped:
            lines.append(f"вход добавлен: {final.name(source)} → {final.name(target)}")
        elif mapped[(source, target)][2] != per_unit:
            lines.append(f"шт на 1: {final.name(source)} → {final.name(target)} "
                         f"было {_shown(mapped[(source, target)][2])} → стало {_shown(per_unit)}")
    for pair, (source, target, _per_unit) in mapped.items():
        if pair not in final.edges:
            lines.append(f"вход убран: {auto.name(source)} → {auto.name(target)}")
    return lines


def _typical(document: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any] | None:
    if not config.get("route"):
        return None  # no hand-edited scheme: the build derived it, nothing was corrected
    route_ops, final_name, _ = _route_ops(document, strict=True)
    derived = derive_route(route_ops, final_name)
    auto = light_route({
        "blocks": [{"key": block.key, "name": block.name, "ops": list(block.ops), "final": block.final}
                   for block in derived.blocks],
        "edges": [{"from": edge.source, "to": edge.target, "per_unit": edge.per_unit} for edge in derived.edges],
    })
    final = light_route(document.get("route"))
    ops = _document_ops(document, [item for item in _list(config.get("ops")) if isinstance(item, Mapping)])
    labels = {op["position"]: op["label"] for op in ops}
    ids = {op["position"]: op["position"] for op in ops}
    changes = scheme_changes(_Scheme(auto, ids, labels), _Scheme(final, ids, labels))
    return {"mode": TYPICAL, "ops": ops, "auto_route": auto, "final_route": final, "changes": changes}


def _from_generator(document: Mapping[str, Any], config: Mapping[str, Any], origin: Mapping[str, Any]) -> dict[str, Any]:
    source = deepcopy(dict(origin))
    source.pop("route", None)  # the generator's own hand-drawn scheme is a correction too, not the automatic one
    converted = generator_to_assembly(source)
    origin_ops = [item for item in _list(converted.get("ops")) if isinstance(item, Mapping)]
    auto_ops = [{
        "position": position, "code": _text(op.get("opNum")), "type": _text(op.get("type")),
        "label": _text(op.get("label")) or _text(op.get("opNum")) or _text(op.get("type")),
    } for position, op in enumerate(origin_ops, start=1)]
    auto = light_route(converted.get("route"))

    config_ops = [item for item in _list(config.get("ops")) if isinstance(item, Mapping)]
    ops = _document_ops(document, config_ops)
    seqs = _seqs(config_ops) if len(config_ops) == len(ops) else [op["position"] for op in ops]
    for op, seq in zip(ops, seqs):
        op["seq"] = seq
    final = light_route(document.get("route"))
    changes = scheme_changes(
        _Scheme(auto, {op["position"]: op["position"] for op in auto_ops}, {op["position"]: op["label"] for op in auto_ops}),
        _Scheme(final, {op["position"]: op["seq"] for op in ops}, {op["seq"]: op["label"] for op in ops}),
    )
    return {"mode": FROM_GENERATOR, "ops": ops, "auto_ops": auto_ops, "auto_route": auto, "final_route": final,
            "changes": changes}


def _same_scheme(previous: Mapping[str, Any] | None, document: Mapping[str, Any]) -> bool:
    """The card's last build already had this very scheme over the same operations — a re-save, not a new correction."""
    if not isinstance(previous, Mapping):
        return False

    def shape(item: Mapping[str, Any]) -> tuple:
        route = light_route(item.get("route"))
        ops = tuple((_text(op.get("code")), _text(op.get("type")))
                    for op in _list(item.get("operations")) if isinstance(op, Mapping))
        blocks = tuple((block["key"], tuple(block["ops"]), block["final"]) for block in route["blocks"])
        edges = tuple((edge["from"], edge["to"], edge["per_unit"]) for edge in route["edges"])
        return ops, blocks, edges

    return shape(previous) == shape(document)


def scheme_correction(
    document: Mapping[str, Any], generator_origin: Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The `techcards.scheme_corrected` details (without the card's own fields) of a just built document, or None when
    its scheme is the automatic one, the scheme is the one the card's previous build already had, or the automatic one
    cannot be rebuilt (a malformed `generator_origin` never stops a build)."""
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    try:
        if config.get("mode") == MANUAL_MODE:
            if not isinstance(generator_origin, Mapping) or generator_origin.get("mode") == MANUAL_MODE:
                return None
            details = _from_generator(document, config, generator_origin)
        else:
            details = _typical(document, config)
        if details is None or not details["changes"] or _same_scheme(previous, document):
            return None
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return None
    return details


# ── Чтение журнала: GET /api/techcards/scheme-corrections и CLI ──

def list_scheme_corrections(
    session: Session, *, since: date | None = None, project: str = "", limit: int = LIST_LIMIT,
) -> list[dict[str, Any]]:
    """`techcards.scheme_corrected` events newest first; `since` — from that day (Moscow), `project` by the project
    number rule (spaces, case, homoglyphs)."""
    query = select(AuditEventRow).where(AuditEventRow.event_type == EVENT)
    if since is not None:
        query = query.where(AuditEventRow.occurred_at >= datetime.combine(since, time.min, MOSCOW))
    rows = list(session.scalars(query.order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())))
    if project:
        wanted = project_key(project)
        rows = [row for row in rows if project_key((row.details or {}).get("project")) == wanted]
    return [{
        "id": str(row.id), "card_id": str(row.aggregate_id), "occurred_at": row.occurred_at.isoformat(),
        "actor": row.actor_name, **deepcopy(dict(row.details or {})),
    } for row in rows[:limit]]


def _kind(line: str) -> str:
    return line.split(":", 1)[0]


def summary_text(events: Sequence[Mapping[str, Any]], since: date | None = None, project: str = "") -> str:
    """The human-readable report: each correction with its changes, then how often each kind of change happened."""
    scope = (f" с {since.isoformat()}" if since else "") + (f", проект {project}" if project else "")
    if not events:
        return f"Правок схемы полуфабрикатов{scope} нет."
    lines = [f"Правки схемы полуфабрикатов{scope}: {len(events)}", ""]
    kinds: Counter[str] = Counter()
    for event in events:
        when = str(event.get("occurred_at") or "")[:16].replace("T", " ")
        lines.append(f"{when} · {event.get('project', '')} / {event.get('item_index', '')} · ред. "
                     f"{event.get('revision', '')} · {event.get('mode', '')} · {event.get('actor', '')}")
        for change in _list(event.get("changes")):
            lines.append(f"  - {change}")
            kinds[_kind(str(change))] += 1
        lines.append("")
    lines.append("Чаще всего правят:")
    lines.extend(f"  {kind} — {count}" for kind, count in kinds.most_common())
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Журнал правок схемы полуфабрикатов (techcards.scheme_corrected)")
    parser.add_argument("--since", required=True, type=date.fromisoformat, help="с какого дня, ГГГГ-ММ-ДД")
    parser.add_argument("--project", default="", help="номер проекта")
    parser.add_argument("--json", action="store_true", help="события целиком, JSON")
    args = parser.parse_args(argv)
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    from app.database import build_engine  # the app wiring imports this module's package: only when run

    engine = build_engine(database_url)
    try:
        with Session(engine) as session:
            events = list_scheme_corrections(session, since=args.since, project=args.project)
    finally:
        engine.dispose()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(events, ensure_ascii=False, indent=2) if args.json
          else summary_text(events, args.since, args.project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
