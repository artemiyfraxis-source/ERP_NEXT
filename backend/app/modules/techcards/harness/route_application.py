"""Use cases of the «Маршрут v3» of a position (application layer): read, save, «Построить по чертежу». Each runs in one
techcards unit of work and commits it; the rules of the document are `route_v3`, the table —
`harness_infrastructure.HarnessRouteRow` (migration 0111).

A route belongs to a КБ position that has a harness drawing and is built on one revision of it:
- a save names the route revision it was made on (None — the first route of the position) and the drawing revision
  the browser built it on; someone else's save since → HarnessConflict (409); a drawing revision the server has not
  reached → HarnessConflict; a route that must not be stored → HarnessInvalid (422, every reason);
- every part the route names must exist in the position's CURRENT drawing (the history keeps thinned rows, so an
  older revision cannot always be read back) — a route of a changed drawing is saved once the parts it names are
  still there, and stays marked as built on the older revision;
- «Построить по чертежу» runs on the server (`route_v3.generate_route`) over the saved drawing: the cut blanks are
  taken anew from the drawing, the snapshots of the stored route are kept (none yet — one snapshot of the whole
  drawing), the cards and links are rebuilt (manual card edits are dropped, as in the author's «Пересобрать»), and the
  route is stored as built on the current drawing revision;
- a route is never deleted; reading marks it `stale` when the position's drawing is another КД row or has moved past
  the revision the route was built on, and lists the parts the route names that the drawing no longer has. A remap of
  the snapshots onto the changed drawing (the author's ManufacturingRouteRemapper) is a later step.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.shared.identity import Actor

from .application import (
    Drawing, Factory, HarnessConflict, HarnessInvalid, HarnessNotFound, _audit, _when, current, position,
)
from .route_v3 import (
    RouteError, StoredRoute, generate_route, initial_route, is_complete, missing_refs, parse_route_v3,
    source_refs, starter_fragment,
)
from .schema import HarnessWriteConflict, decode

JUST_SAVED = "Маршрут только что сохранили — обновите страницу и повторите правку"


def view(route: StoredRoute | None, drawing: Drawing | None, design: Mapping[str, Any] | None) -> dict[str, Any]:
    """The route as the canvas reads it, with the drawing it is checked against: `stale` — built on another drawing
    or an older revision of it; `missing_refs` — parts the route names that the drawing no longer has; `complete` —
    exactly one «Готовый жгут» holding every part of the drawing (what a techcard may be fed from)."""
    current_drawing = None if drawing is None else {
        "document_id": str(drawing.document.id), "revision": drawing.revision,
    }
    if route is None:
        return {"route": None, "drawing": current_drawing, "stale": False, "missing_refs": [], "complete": False}
    stale = drawing is None or (route.drawing_document_id != drawing.document.id
                                or route.drawing_revision != drawing.revision)
    return {
        "route": {
            "revision": route.revision, "document": route.document,
            "drawing_document_id": str(route.drawing_document_id), "drawing_revision": route.drawing_revision,
            "updated_at": route.updated_at.isoformat(), "updated_by": route.updated_by,
        },
        "drawing": current_drawing,
        "stale": stale,
        "missing_refs": missing_refs(route.document, design),
        "complete": not stale and is_complete(route.document, design),
    }


def _drawing(uow: Any, project: str, index: str) -> tuple[str, str, Drawing]:
    kitset, index = position(uow, project, index)
    drawing = current(uow, kitset.project, index)
    if drawing is None:
        raise HarnessNotFound(f"У сборки {index} нет чертежа жгута — маршрут строится по чертежу")
    return kitset.project, index, drawing


def _expect(route: StoredRoute | None, revision: int | None) -> None:
    """The route revision the write was made on must be the stored one (None / 0 — there was no route)."""
    if route is None:
        if revision:
            raise HarnessConflict("Маршрута этой сборки на сервере нет — обновите страницу")
        return
    if revision != route.revision:
        raise HarnessConflict(f"Маршрут уже сохранил {route.updated_by} {_when(route.updated_at)} — обновите страницу "
                              "и повторите правку")


def _write(uow: Any, stored: StoredRoute | None, route: StoredRoute) -> None:
    try:
        if stored is None:
            uow.harness_routes.add(route)
        elif not uow.harness_routes.replace(route, stored.revision):
            raise HarnessConflict(JUST_SAVED)
    except HarnessWriteConflict as error:  # a concurrent first route of the position landed first
        raise HarnessConflict(JUST_SAVED) from error


def _stored(project: str, index: str, stored: StoredRoute | None, drawing: Drawing, drawing_revision: int,
            document: dict[str, Any], *, actor: Actor, now: datetime) -> StoredRoute:
    return StoredRoute(
        project=project, item_index=index, revision=(stored.revision if stored else 0) + 1,
        drawing_document_id=drawing.document.id, drawing_revision=drawing_revision, document=document,
        updated_at=now, updated_by=actor.name,
    )


def read(factory: Factory, project: str, index: str) -> dict[str, Any]:
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        route = uow.harness_routes.get(kitset.project, index)
    return view(route, drawing, decode(drawing.content) if drawing is not None else None)


def save(factory: Factory, project: str, index: str, document: Mapping[str, Any], *, revision: int | None,
         drawing_revision: int, actor: Actor, now: datetime) -> dict[str, Any]:
    with factory() as uow:
        project, index, drawing = _drawing(uow, project, index)
        stored = uow.harness_routes.get(project, index)
        _expect(stored, revision)
        if drawing_revision > drawing.revision:
            raise HarnessConflict(f"Маршрут построен на версии чертежа {drawing_revision}, а на сервере версия "
                                  f"{drawing.revision} — обновите страницу")
        design = decode(drawing.content)
        errors = parse_route_v3(document, design)
        if errors:
            raise HarnessInvalid(errors, prefix="Маршрут не сохранён")
        route = _stored(project, index, stored, drawing, drawing_revision, dict(document), actor=actor, now=now)
        _write(uow, stored, route)
        _audit(uow, actor, now, "techcards.harness_route_saved", {
            "project": project, "item_index": index, "revision": route.revision,
            "drawing_revision": drawing_revision, "cards": len(route.document.get("nodes") or []),
        })
        uow.commit()
    return view(route, drawing, design)


def generate(factory: Factory, project: str, index: str, *, revision: int | None, actor: Actor,
             now: datetime) -> dict[str, Any]:
    """«Построить по чертежу» (see the module docstring)."""
    with factory() as uow:
        project, index, drawing = _drawing(uow, project, index)
        stored = uow.harness_routes.get(project, index)
        _expect(stored, revision)
        design = decode(drawing.content)
        fresh = initial_route(design)
        base = {**(stored.document if stored is not None else fresh), "preparedRefs": fresh["preparedRefs"]}
        if base.get("wireArtwork"):  # sparse looks of wires: those of wires gone from the drawing just go
            wires = {ref["id"] for ref in source_refs(design) if ref["kind"] == "wire"}
            base["wireArtwork"] = [item for item in base["wireArtwork"] if item.get("wireId") in wires]
        if not base.get("fragments"):
            base["fragments"] = [starter_fragment(design, now.isoformat())]
        try:
            document = generate_route(base, design)
        except RouteError as error:
            raise HarnessInvalid([str(error)], prefix="Маршрут не построен") from error
        errors = parse_route_v3(document, design)
        if errors:
            raise HarnessInvalid(errors, prefix="Маршрут не построен")
        route = _stored(project, index, stored, drawing, drawing.revision, document, actor=actor, now=now)
        _write(uow, stored, route)
        _audit(uow, actor, now, "techcards.harness_route_generated", {
            "project": project, "item_index": index, "revision": route.revision,
            "drawing_revision": drawing.revision, "cards": len(document["nodes"]),
        })
        uow.commit()
    return view(route, drawing, design)
