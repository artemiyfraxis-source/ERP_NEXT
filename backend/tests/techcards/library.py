"""The operation-card library straight into a test database — what `seed_library` does for a fresh one."""

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from app.modules.techcards.operation_cards import parse_operation_library


def load_library(
    factory: Callable[[], Any], templates: Iterable[Mapping[str, Any]], *, now: datetime = datetime(2026, 9, 23, 12, 0),
) -> None:
    """Adds the templates as library cards (not ERP-saved sheets); an id already in the library is an error."""
    cards = parse_operation_library(list(templates))
    with factory() as uow:
        uow.operation_cards.load(cards, imported_at=now)
        uow.commit()
