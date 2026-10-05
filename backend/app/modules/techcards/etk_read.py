"""Public read port of the techcards module for other modules: the issued ЭТК of a project item.

Production shows the fitter the ЭТК of his operation without giving him the techcards section, so it reads
the card through this function and never touches the techcards infrastructure."""

from collections.abc import Callable
from typing import Any

from .application import UnitOfWork
from .connector_housings import attach_housings
from .etk_builder import attach_route
from .etk_parts import attach_parts
from .project_cards import ProjectTechcard, TechcardStatus


def card_view(card: ProjectTechcard, identify: Callable[[str], str] | None = None) -> dict[str, Any]:
    """The card as the screens read it: the document enriched with parts, housings and the route scheme."""
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
        "document": attach_route(attach_housings(attach_parts(card.document, identify))),
        "manual_operation_keys": list(card.manual_operation_keys),
        "created_at": card.created_at.isoformat(),
        "updated_at": card.updated_at.isoformat(),
        "issued_at": card.issued_at.isoformat() if card.issued_at else None,
    }


def issued_card_for_item(uow: UnitOfWork, project: str, item: str) -> ProjectTechcard | None:
    """The issued card of the project and the КБ index taken from the item («591K.3КБ1» or «591K.3.КБ1»)."""
    project = str(project or "").strip()
    rest = str(item or "").strip()
    if project and rest.lower().startswith(project.lower()):
        rest = rest[len(project):]
    index = rest.lstrip(". ").strip()
    if not project or not index:
        return None
    for candidate in dict.fromkeys((index, index.upper())):
        card = uow.techcards.by_key(project, candidate)
        if card is not None:
            return card if card.status is TechcardStatus.ISSUED else None
    return None
