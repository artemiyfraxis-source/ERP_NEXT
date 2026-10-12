"""Techcards' read port over Fulfillment: the lots handed to the shop for the КБ of an ETK.

The ETK shows the ГРН of its components from the current bindings (owner, 12.10.2026: «после того как Алексей
привяжет к позиции ГРН, нужно чтобы это синкалось в техкарте»). Techcards never reads Fulfillment's tables: this is a
read-only port, one batched query for all the cards of a request, the same shape as RequestPurposeLookup.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modules.fulfillment.application import UnitOfWork as FulfillmentUnitOfWork
from app.modules.fulfillment.domain import item_index_key
from app.modules.procurement.domain import project_key
from app.modules.techcards.etk_parts import EtkLot


class IssuedKitLots:
    """`(project, item index)` as the ETK names them → the lots issued to the shop for that kit (main request and its
    «Довыдача», lines with issued > 0 on a lot), in the order they were requested."""

    def __init__(self, fulfillment_uow_factory: Callable[[], FulfillmentUnitOfWork]):
        self.fulfillment_uow_factory = fulfillment_uow_factory

    def __call__(self, kits: Sequence[tuple[str, str]]) -> dict[tuple[str, str], tuple[EtkLot, ...]]:
        asked: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for project, item_index in dict.fromkeys(kits):
            key = (project_key(project), item_index_key(item_index))
            if key[0] and key[1]:
                asked.setdefault(key, []).append((project, item_index))
        if not asked:
            return {}
        with self.fulfillment_uow_factory() as uow:
            rows = uow.fulfillment.issued_lots(list(asked))
        found: dict[tuple[str, str], list[EtkLot]] = {}
        for project, kb, article, name, grn, package in rows:
            found.setdefault((project, kb), []).append(EtkLot(article=article, name=name, grn=grn, package=package))
        return {
            kit: tuple(found.get(key, ()))
            for key, named in asked.items()
            for kit in named
            if key in found
        }
