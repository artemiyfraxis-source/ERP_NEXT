"""«Заполнить техкарту» from the «Маршрут v3» of a position (owner 13.10): the route cards become a «Ручная сборка»
draft — `build_config` with mode manual — stored through the existing `assembly_drafts` path. Only a draft: the
project techcard, its issued ETKs and production launches stay as they are until the technologist builds the card
naming the draft and issues it.

The mapping (pure, `assembly_from_route`):
- one card → one block of the semi-finished scheme; one link → one arrow; the «Готовый жгут» card → the final block
  (named after the position, so the ETK's «Изделие» is the harness). The scheme rides in `build_config.route` and the
  builder marks it `source: manual`, so production follows it once the card is issued;
- the operations follow the cards' order — inputs before the card they feed (a topological order, ties read like the
  canvas: top to bottom, then left to right) — and within a card its own list; `seq` = the ETK position 1..n;
- an operation taken from the ERP library (`templateId`) → `libraryOp` of that card, an own one → `customOp`;
  time per harness: «Штучная» — quantity × minutes each (`qty` × `tOp`), «Погонная» — length / speed / people (one
  piece of that time);
- a card without operations still is a step of the route: it becomes an own operation named after the card (no
  norm, no steps — the builder warns, the master fills it in «Ручная сборка»);
- the card's «Количество, шт» is pieces per harness: an arrow takes source pieces / target pieces per one piece of
  the target (`per_unit`, ≥ 4 decimals as the scheme stores them).

A route without a «Готовый жгут» card has no product to fill the techcard with and is refused in Russian."""

from __future__ import annotations

import heapq
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.shared.identity import Actor

from ..assembly_drafts import AssemblyDraft, AssemblyDraftError, CheckedConfig, SubmitDraftCommand, submit_draft
from ..domain.route import RouteError as SchemeError, RouteOp, parse_route
from .application import HarnessConflict, HarnessInvalid, HarnessNotFound, current, position
from .route_v3 import is_complete, missing_refs
from .schema import decode, plain

LIBRARY_OP = "libraryOp"
CUSTOM_OP = "customOp"
DRAFT_SOURCE = "manual"
NO_FINAL = ("В маршруте нет карточки «Готовый жгут» — отметьте итоговую карточку (вид «Готовый жгут»): из неё "
            "техкарта берёт изделие")
NO_CARDS = "В маршруте нет карточек — постройте его по чертежу"
_PLACES = Decimal("0.0001")


class RouteAssemblyError(ValueError):
    """The route cannot fill a techcard; the message is for the master."""


def _number(value: object, default: Decimal = Decimal(0)) -> Decimal:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return number if number.is_finite() else default


def _text(value: Decimal) -> str:
    """A decimal as the «Ручная сборка» fields hold it: «90», «1.5»."""
    rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP).normalize()
    return format(rounded, "f")


def _quantity(node: Mapping[str, Any]) -> Decimal:
    quantity = _number(node.get("quantity"), Decimal(1))
    return quantity if quantity > 0 else Decimal(1)


def card_order(nodes: list[Mapping[str, Any]], edges: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Inputs before the card they feed; among cards ready at once — top to bottom, then left to right, then the
    stored order. A card left over by a cycle (a stored route never has one) goes last in its place."""
    by_id = {str(node.get("id")): (index, node) for index, node in enumerate(nodes)}
    incoming = {key: 0 for key in by_id}
    following: dict[str, list[str]] = {key: [] for key in by_id}
    for edge in edges:
        source, target = str(edge.get("from")), str(edge.get("to"))
        if source in by_id and target in by_id:
            incoming[target] += 1
            following[source].append(target)

    def rank(key: str) -> tuple[Decimal, Decimal, int, str]:
        index, node = by_id[key]
        return _number(node.get("y")), _number(node.get("x")), index, key

    ready = [rank(key) for key, count in incoming.items() if not count]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        key = heapq.heappop(ready)[3]
        order.append(key)
        for target in following[key]:
            incoming[target] -= 1
            if not incoming[target]:
                heapq.heappush(ready, rank(target))
    order += sorted((key for key in by_id if key not in order), key=rank)
    return [by_id[key][1] for key in order]


def _operation(op: Mapping[str, Any], card: str) -> dict[str, Any]:
    """One operation of a card as a «Ручная сборка» operation, time per harness."""
    title = str(op.get("title") or "").strip()
    template = str(op.get("templateId") or "").strip()
    if op.get("kind") == "running":
        length, speed, people = (_number(op.get(name)) for name in ("lengthMm", "speedMmPerMinute", "employees"))
        minutes = length / speed / people if length > 0 and speed > 0 and people > 0 else Decimal(0)
        quantity = Decimal(1)
    else:
        quantity, minutes = _number(op.get("quantity")), _number(op.get("minutesEach"))
        if quantity <= 0:
            quantity, minutes = Decimal(1), Decimal(0)
    base: dict[str, Any] = {"label": title or f"Операция карточки «{card}»", "qty": _text(quantity),
                            "tPrep": "0", "tOp": _text(minutes * 60)}
    if template:
        return {"type": LIBRARY_OP, "templateId": template, "opNum": str(op.get("code") or "").strip(), **base}
    return {"type": CUSTOM_OP, **base}


def assembly_from_route(
    document: Mapping[str, Any], *, project: str, index: str, name: str, part_qty: object,
) -> tuple[dict[str, Any], list[str]]:
    """The «Ручная сборка» build_config of a stored route v3 and the Russian notes about it (see the module
    docstring). RouteAssemblyError — the route has no product or its scheme does not hold."""
    nodes = [node for node in document.get("nodes") or [] if isinstance(node, Mapping)]
    edges = [edge for edge in document.get("edges") or [] if isinstance(edge, Mapping)]
    if not nodes:
        raise RouteAssemblyError(NO_CARDS)
    finals = [node for node in nodes if node.get("kind") == "final"]
    if not finals:
        raise RouteAssemblyError(NO_FINAL)
    if len(finals) > 1:
        raise RouteAssemblyError("В маршруте несколько карточек «Готовый жгут» — оставьте одну")
    notes: list[str] = []
    ops: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    keys: dict[str, str] = {}
    quantities: dict[str, Decimal] = {}
    for number, node in enumerate(card_order(nodes, edges), start=1):
        title = str(node.get("title") or "").strip() or f"Карточка {number}"
        final = node.get("kind") == "final"
        positions: list[int] = []
        own = [op for op in node.get("operations") or [] if isinstance(op, Mapping)]
        if not own:
            notes.append(f"Карточка «{title}» без операций — в техкарте она операция «{title}» без нормы и хода; "
                         "дополните её в «Ручной сборке»")
        for op in own or [{"title": title}]:
            ops.append({**_operation(op, title), "seq": len(ops) + 1})
            positions.append(len(ops))
        key = f"b{number}"
        keys[str(node.get("id"))] = key
        quantities[key] = _quantity(node)
        blocks.append({"key": key, "name": (name.strip() or title) if final else title, "ops": positions,
                       "final": final})
    arrows: list[dict[str, Any]] = []
    for edge in edges:
        source, target = keys.get(str(edge.get("from"))), keys.get(str(edge.get("to")))
        if source is None or target is None:
            continue
        per_unit = (quantities[source] / quantities[target]).quantize(_PLACES, rounding=ROUND_HALF_UP)
        arrows.append({"from": source, "to": target, "per_unit": plain(per_unit if per_unit > 0 else _PLACES)})
    config = {
        "mode": "manual", "projectNo": project, "assemblyIndex": index, "assemblyName": name, "partQty": plain(part_qty),
        "ops": ops, "route": {"source": "manual", "blocks": blocks, "edges": arrows},
    }
    route_ops = [RouteOp(position=op["seq"], type=op["type"], code=str(op.get("opNum") or ""), name=op["label"])
                 for op in ops]
    try:
        parse_route(config["route"], route_ops, name, manual=True)
    except SchemeError as error:
        raise RouteAssemblyError(str(error)) from error
    return config, notes


# ── Use case: preview (dry run) and save as a draft ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouteDraft:
    """What «Заполнить техкарту» shows: the route revision it was made from, the config, the builder's check and the
    stored draft (None on a dry run)."""
    revision: int
    build_config: dict[str, Any]
    checked: CheckedConfig
    warnings: list[str]
    draft: AssemblyDraft | None


def draft_from_route(
    factory: Callable[[], Any], project: str, index: str, *, revision: int | None, dry_run: bool, actor: Actor,
    now: datetime,
) -> RouteDraft:
    """Turn the stored route into a «Ручная сборка» config and check it with the real ETK builder; unless `dry_run`,
    store it as a draft of the position (source «manual», linked to the drawing). `revision` — the route revision the
    master previewed: a save over a route changed since is refused, so what is stored is what was shown."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        assembly = kitset.assembly(index)
        drawing = current(uow, kitset.project, index)
        route = uow.harness_routes.get(kitset.project, index)
    if route is None:
        raise HarnessNotFound(f"У сборки {index} нет маршрута — постройте его по чертежу")
    if revision is not None and revision != route.revision:
        raise HarnessConflict(f"Маршрут изменил {route.updated_by} после просмотра — посмотрите результат заново")
    if revision is None and not dry_run:
        raise HarnessConflict("Сначала посмотрите результат — техкарта заполняется по просмотренному маршруту")
    try:
        config, notes = assembly_from_route(
            route.document, project=kitset.project, index=index, name=assembly.name if assembly else "",
            part_qty=assembly.run if assembly else 1,
        )
    except RouteAssemblyError as error:
        raise HarnessInvalid([str(error)], prefix="Техкарта не заполнена") from error
    design = decode(drawing.content) if drawing is not None else None
    if drawing is None or route.drawing_document_id != drawing.document.id or route.drawing_revision != drawing.revision:
        notes.insert(0, f"Маршрут построен на чертеже ред. {route.drawing_revision}"
                     + (f", чертёж уже ред. {drawing.revision}" if drawing is not None else ", чертежа у сборки нет")
                     + " — сверьте карточки")
    elif missing_refs(route.document, design) or not is_complete(route.document, design):
        notes.insert(0, "«Готовый жгут» держит не все части чертежа — техкарта заполнится только по карточкам маршрута")
    linked = drawing.document.id if drawing is not None and route.drawing_document_id == drawing.document.id else None
    try:
        draft, checked = submit_draft(SubmitDraftCommand(
            project=kitset.project, item_index=index, drawing_document_id=linked, source=DRAFT_SOURCE,
            build_config=config, note=f"Из маршрута {index}, ред. {route.revision} (чертёж ред. {route.drawing_revision})",
            dry_run=dry_run, actor=actor, now=now,
        ), factory)
    except AssemblyDraftError as error:
        raise HarnessInvalid([str(error)], prefix="Техкарта не заполнена") from error
    return RouteDraft(route.revision, config, checked, list(dict.fromkeys([*notes, *checked.warnings])), draft)


def draft_json(result: RouteDraft, *, dry_run: bool) -> dict[str, Any]:
    draft = result.draft
    return {
        "dry_run": dry_run, "revision": result.revision, "build_config": result.build_config,
        "operations": result.checked.operations, "route": result.checked.route, "warnings": result.warnings,
        "draft": None if draft is None else {
            "id": str(draft.id), "project": draft.project, "item_index": draft.item_index, "source": draft.source,
            "author": draft.author, "status": draft.status, "note": draft.note, "created_at": draft.created_at.isoformat(),
        },
    }

