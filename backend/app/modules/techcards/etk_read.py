"""Public read port of the techcards module for other modules: the issued ЭТК of a project item.

Production shows the fitter the ЭТК of his operation without giving him the techcards section, so it reads
the card through this function and never touches the techcards infrastructure."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

from .application import UnitOfWork
from .connector_housings import attach_housings
from .etk_builder import attach_route, fill_generator_chain
from .etk_live import live_document, load_templates
from .etk_parts import EtkLot, EtkLots, attach_parts
from .harness import application as harness
from .harness.application import HarnessNotFound
from .kitsets import Kitset, project_key
from .operation_cards import OperationCardTemplate
from .project_cards import ProjectTechcard, TechcardStatus

# The lots handed to the shop per (project, item index) as the ETK names them — the fulfillment read port
# (`app.workflows.etk_lots.IssuedKitLots`), one batched query for all the cards asked.
IssuedLots = Callable[[Sequence[tuple[str, str]]], Mapping[tuple[str, str], Sequence[EtkLot]]]


def _kb_key(value: object) -> str:
    return re.sub(r"[\s\-]+", "", str(value or "")).upper()


def _kitset_lots(kitset: Kitset | None, item_index: str) -> tuple[EtkLot, ...]:
    wanted = _kb_key(item_index)
    assembly = next((item for item in kitset.assemblies if _kb_key(item.index) == wanted), None) if kitset else None
    if assembly is None:
        return ()
    return tuple(
        EtkLot(article=component.article, name=component.name, grn=component.lot.grn, package=component.lot.package)
        for component in assembly.components if component.lot is not None and component.lot.grn.strip()
    )


def card_lots(uow: Any, cards: Sequence[ProjectTechcard], issued: IssuedLots | None = None) -> dict[UUID, EtkLots]:
    """Current ГРН bindings of each card's project + КБ: the «Состав» lots (one query for all the projects) and the
    lots issued to the shop (one query through `issued`). A card without a project or index gets none."""
    keys = list(dict.fromkeys(
        (card.project, card.item_index) for card in cards if card.project.strip() and card.item_index.strip()
    ))
    if not keys:
        return {}
    repository = getattr(uow, "kitsets", None)
    kitsets: Mapping[str, Kitset] = repository.for_projects([project for project, _ in keys]) if repository else {}
    handed = issued(keys) if issued is not None else {}
    asked = set(keys)
    return {
        card.id: EtkLots(
            issued=tuple(handed.get((card.project, card.item_index), ())),
            kitset=_kitset_lots(kitsets.get(project_key(card.project)), card.item_index),
        )
        for card in cards if (card.project, card.item_index) in asked
    }


def card_view(
    card: ProjectTechcard, identify: Callable[[str], str] | None = None, lots: EtkLots | None = None,
    templates: Sequence[OperationCardTemplate] = (), frozen: bool = False,
) -> dict[str, Any]:
    """The card as the screens read it: the document enriched with parts (ГРН by the current `lots` bindings),
    housings and the route scheme; operation cards nobody edited by hand come from their `templates` as they are now
    (etk_live), each operation says where its card came from. `frozen` — the document is a stored revision: shown as
    it was issued, without the live templates."""
    document = card.document if frozen else live_document(
        card.document, manual_keys=card.manual_operation_keys, templates=templates, built_at=card.updated_at)
    return {
        "id": str(card.id),
        "version": card.version,
        "project": card.project,
        "item_index": card.item_index,
        "product_name": card.product_name,
        "source_calculation": card.source_calculation,
        "deadline": card.deadline.isoformat() if card.deadline else None,
        "status": card.status.value,
        "revision": card.revision,
        "document": attach_route(attach_housings(attach_parts(fill_generator_chain(document), identify, lots))),
        "manual_operation_keys": list(card.manual_operation_keys),
        "created_at": card.created_at.isoformat(),
        "updated_at": card.updated_at.isoformat(),
        "issued_at": card.issued_at.isoformat() if card.issued_at else None,
    }


def card_views(
    uow: Any, cards: Sequence[ProjectTechcard], identify: Callable[[str], str] | None = None,
    issued: IssuedLots | None = None, frozen: bool = False,
) -> list[dict[str, Any]]:
    """`card_view` of several cards with their current ГРН bindings (`card_lots`) and the library templates their
    operations name read in one go."""
    lots = card_lots(uow, cards, issued) if cards else {}
    templates = load_templates(getattr(uow, "operation_cards", None), [card.document for card in cards])
    return [card_view(card, identify, lots.get(card.id), templates, frozen) for card in cards]


def _item_index(project: str, item: str) -> str:
    rest = str(item or "").strip()
    if project and rest.lower().startswith(project.lower()):
        rest = rest[len(project):]
    return rest.lstrip(". ").strip()


def saved_harness(uow: UnitOfWork, project: str, item: str) -> dict[str, Any]:
    """The saved harness drawing of the item, read-only and without the revision history, in the shape the editor
    reads it (`document`, `design`, `revision`, `version`, `warnings`; no drawing — `design: None`). Production shows it
    to the fitter, who has no «Проекты» right."""
    project = str(project or "").strip()
    index = _item_index(project, item)
    try:
        if not project or not index:
            raise HarnessNotFound(index)
        kitset, index = harness.position(uow, project, index)
        return harness.view(harness.current(uow, kitset.project, index))
    except HarnessNotFound:
        return harness.view(None)


def issued_card_for_item(uow: UnitOfWork, project: str, item: str) -> ProjectTechcard | None:
    """The issued card of the project and the КБ index taken from the item («591K.3КБ1» or «591K.3.КБ1»)."""
    project = str(project or "").strip()
    index = _item_index(project, item)
    if not project or not index:
        return None
    for candidate in dict.fromkeys((index, index.upper())):
        card = uow.techcards.by_key(project, candidate)
        if card is not None:
            return card if card.status is TechcardStatus.ISSUED else None
    return None


def pinned_card(uow: Any, card: ProjectTechcard, revision: int | None) -> tuple[ProjectTechcard, dict[str, Any]]:
    """The card as a task launched with `revision` sees it: the stored document of that revision, plus the notice for
    the screen — `pinned` (the revision shown), `current`, `newer` (a later revision exists), `fallback` (why the
    current document is shown instead: the task predates pinning or the revision row is missing; else None)."""
    current = card.revision
    info: dict[str, Any] = {"pinned": revision, "current": current, "newer": False, "fallback": None}
    if revision is None:
        info["fallback"] = "Задание создано до закрепления ревизии — показана текущая ЭТК"
        return card, info
    if revision == current:
        return card, info
    row = next((item for item in uow.techcards.revisions(card.id) if item.revision == revision), None)
    if row is None or not row.document:
        info["fallback"] = f"Ревизия {revision} не найдена — показана текущая ЭТК"
        return card, info
    info["newer"] = current > revision
    return replace(card, document=row.document), info
