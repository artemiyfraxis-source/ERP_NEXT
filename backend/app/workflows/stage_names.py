"""Названия карт операций для этапов заданий: один установщик для приложения и воркера исходящих событий."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.production.launchable import set_stage_name_loader
from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork


def install_stage_name_loader(factory: Callable[[], TechcardsUnitOfWork]) -> None:
    """Код карты → название из библиотеки карточек, вся карта одним чтением (кэш и сбои — в `launchable`)."""
    def load() -> dict[str, str]:
        with factory() as techcards:
            return techcards.operation_cards.names()
    set_stage_name_loader(load)
