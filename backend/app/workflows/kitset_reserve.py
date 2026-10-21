"""Резерв при выборе ГРН в спецификации проекта (T1, 06.10.2026).

A technologist picks a warehouse lot for a spec row (project + КБ + article): the lot is reserved for that КБ at once,
so other projects see less ДОСТУПНО. Techcards keep the binding in the kitset JSON, fulfillment keeps the reserve
(`KitReserve`); this workflow keeps the two in step across the modules:

- a row save holds the reserve first and saves the kitset after; a failed save gives the reserve back to the stored
  state of the rows (never an orphan reserve);
- «Обновить из расчёта», «Отправить на комплектацию» and the project's deletion re-check only rows that already hold
  a reserve: a dropped row or a deleted project releases it, a КБ with its kit request releases what the kit did not
  take. Rows bound before T1 get no reserve here (no backfill) — only on their next save or in their kit request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.modules.fulfillment.application import (
    HoldKitReserveCommand,
    active_kit_reserves,
    hold_kit_reserve,
    kit_requested_kbs,
)
from app.modules.fulfillment.domain import KitReserve, item_index_key
from app.modules.techcards.kitsets import Kitset, KitComponent, KitLot, KitsetNotFound
from app.shared.identity import Actor

_log = logging.getLogger(__name__)

RowKey = tuple[str, str]  # (item_index_key(КБ), артикул)


class ConcurrentKitsetUpdate(RuntimeError):
    """The kitset changed between reading the rows and saving them."""


@dataclass(frozen=True)
class RowWant:
    item_index: str
    article: str
    lot: KitLot | None
    batch: Decimal
    unit: str


def row_key(item_index: str, article: str) -> RowKey:
    return item_index_key(item_index), str(article or "").strip()


def row_wants(kitset: Kitset | None) -> dict[RowKey, RowWant]:
    """What every spec row asks for: its ГРН and «на партию» (`batch`) in the row's unit. The first row of a key wins."""
    wants: dict[RowKey, RowWant] = {}
    if kitset is None:
        return wants
    for assembly in kitset.assemblies:
        for component in assembly.components:
            key = row_key(assembly.index, component.article)
            if key[1] and key not in wants:
                wants[key] = RowWant(
                    item_index=assembly.index, article=key[1], lot=component.lot,
                    batch=component.batch(assembly.run), unit=component.unit,
                )
    return wants


def _resolve_lot(uow, lot: KitLot):
    """The stock lot of a binding: by id, else by ГРН + пакет (a binding saved before lot ids), as the kit request."""
    stock = uow.warehouse.get(lot.lot_id) if lot.lot_id else None
    if stock is None and lot.grn:
        stock = next((
            item for item in uow.warehouse.list(limit=50, query=lot.grn)
            if item.grn == lot.grn and (item.package or "") == (lot.package or "")
        ), None)
    return stock


def sync_rows(
    project: str,
    kitset: Kitset | None,
    keys: Iterable[RowKey] | None,
    actor: Actor,
    now: datetime,
    fulfillment_uow: Callable[[], Any],
    *,
    versions: Mapping[RowKey, int] | None = None,
    strict: bool = True,
) -> list[str]:
    """Bring the reserves of `keys` in line with the rows of `kitset` (None — the project is gone: release).
    `keys` None — only the rows that hold a reserve now (no backfill). Each row in its own transaction.
    `strict` raises the first failure; otherwise the failures come back as text and the other rows go on."""
    wants = row_wants(kitset)
    with fulfillment_uow() as uow:
        held = {row_key(reserve.item_index, reserve.article) for reserve in active_kit_reserves(uow, project)}
        # nothing held and nothing to hold → the row is left out
        todo = sorted(
            key for key in (held if keys is None else set(keys))
            if key in held or (key in wants and wants[key].lot is not None)
        )
        kitted = kit_requested_kbs(uow, project) if any(key in wants and wants[key].lot for key in todo) else set()
    errors: list[str] = []
    for key in todo:
        want = wants.get(key)
        try:
            with fulfillment_uow() as uow:
                stock = None
                if want is not None and want.lot is not None and key[0] not in kitted:
                    stock = _resolve_lot(uow, want.lot)
                hold_kit_reserve(HoldKitReserveCommand(
                    project=project, item_index=want.item_index if want else key[0], article=key[1],
                    lot_id=stock.id if stock is not None else None,
                    quantity=want.batch if want else Decimal("0"), unit=want.unit if want else "",
                    actor=actor, now=now, expected_lot_version=(versions or {}).get(key),
                ), uow)
                uow.commit()
        except Exception as error:
            if strict:
                raise
            _log.exception("kit reserve %s %s %s", project, *key)
            errors.append(f"{key[0]} · {key[1]}: {error}")
    return errors


def _binding(lot: KitLot | None) -> tuple[str, str, str] | None:
    if lot is None:
        return None
    return str(lot.lot_id or ""), lot.grn, lot.package


def _picked_versions(current: Kitset, edits: Iterable[dict[str, Any]]) -> tuple[list[RowKey], dict[RowKey, int]]:
    """The edited rows and, for a row whose ГРН changes, the lot version the picker showed (`lot.version`)."""
    before = row_wants(current)
    keys: list[RowKey] = []
    versions: dict[RowKey, int] = {}
    for edit in edits:
        key = row_key(edit.get("index") or "", edit.get("art") or "")
        keys.append(key)
        lot = edit.get("lot")
        if not isinstance(lot, dict) or lot.get("version") in (None, ""):
            continue
        old = before.get(key)
        new = (str(lot.get("lot_id") or ""), str(lot.get("grn") or "").strip(), str(lot.get("package") or "").strip())
        if old is None or _binding(old.lot) != new:
            try:
                versions[key] = int(lot["version"])
            except (TypeError, ValueError):
                continue
    return keys, versions


def save_rows(
    project: str,
    edits: list[dict[str, Any]],
    actor: Actor,
    now: datetime,
    techcards_uow: Callable[[], Any],
    fulfillment_uow: Callable[[], Any] | None,
) -> tuple[Kitset, int]:
    """`portalKitsetSave` with the reserve: the edited rows' reserves first, then the kitset under its version. If the
    reserve or the save fails, the rows' reserves go back to what the stored kitset says, and the failure is raised."""
    with techcards_uow() as uow:
        current = uow.kitsets.get(project)
    if current is None:
        raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
    updated, count = current.with_edits(edits, who=actor.name, now=now)
    keys, versions = _picked_versions(current, edits)
    _store(current, updated, keys, versions, actor, now, techcards_uow, fulfillment_uow)
    return updated, count


def add_row(
    project: str,
    index: str,
    component: KitComponent,
    actor: Actor,
    now: datetime,
    techcards_uow: Callable[[], Any],
    fulfillment_uow: Callable[[], Any] | None,
) -> tuple[Kitset, bool]:
    """Технолог добавил комплектующее в КБ: строка набора без ГРН (резервировать нечего). Второе — КБ уже отправлена на
    комплектацию: живая заявка новую строку не получит, недостающее выдаёт «Довыдать»."""
    with techcards_uow() as uow:
        current = uow.kitsets.get(project)
    if current is None:
        raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
    updated = current.with_component(index, component, who=actor.name, now=now)
    _store(current, updated, [], {}, actor, now, techcards_uow, None)
    kitted = False
    if fulfillment_uow is not None:
        with fulfillment_uow() as uow:
            kitted = item_index_key(index) in kit_requested_kbs(uow, project)
    return updated, kitted


def remove_row(
    project: str,
    index: str,
    article: str,
    actor: Actor,
    now: datetime,
    techcards_uow: Callable[[], Any],
    fulfillment_uow: Callable[[], Any] | None,
) -> Kitset:
    """Убрать строку технолога; её резерв ГРН, если строку успели привязать, снимается."""
    with techcards_uow() as uow:
        current = uow.kitsets.get(project)
    if current is None:
        raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
    updated = current.without_component(index, article, who=actor.name, now=now)
    _store(current, updated, [row_key(index, article)], {}, actor, now, techcards_uow, fulfillment_uow)
    return updated


def _store(
    current: Kitset, updated: Kitset, keys: list[RowKey], versions: dict[RowKey, int], actor: Actor, now: datetime,
    techcards_uow: Callable[[], Any], fulfillment_uow: Callable[[], Any] | None,
) -> None:
    """The rows' reserves first, then the kitset under its version; on a failure the reserves go back to the stored
    kitset and the failure is raised."""
    project = current.project
    try:
        if fulfillment_uow is not None:
            sync_rows(updated.project, updated, keys, actor, now, fulfillment_uow, versions=versions)
        with techcards_uow() as uow:
            fresh = uow.kitsets.get(project)
            if fresh is None:
                raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
            if fresh.version != current.version:
                raise ConcurrentKitsetUpdate("Карточка проекта изменена конкурентно — обновите")
            uow.kitsets.save(updated)
            uow.commit()
    except Exception:
        if fulfillment_uow is not None:
            _restore(current.project, keys, actor, now, techcards_uow, fulfillment_uow)
        raise


def _restore(
    project: str, keys: list[RowKey], actor: Actor, now: datetime,
    techcards_uow: Callable[[], Any], fulfillment_uow: Callable[[], Any],
) -> None:
    """After a failed save: the rows' reserves follow the kitset as stored (gone — released)."""
    try:
        with techcards_uow() as uow:
            stored = uow.kitsets.get(project)
        errors = sync_rows(project, stored, keys, actor, now, fulfillment_uow, strict=False)
        if errors:
            _log.error("kit reserve restore %s: %s", project, "; ".join(errors))
    except Exception:  # noqa: BLE001 — the original failure is what the person sees
        _log.exception("kit reserve restore %s", project)


def resync_project(
    project: str, actor: Actor, now: datetime,
    techcards_uow: Callable[[], Any], fulfillment_uow: Callable[[], Any] | None,
) -> str:
    """After «Обновить из расчёта» / «Отправить на комплектацию»: the rows holding a reserve follow the stored kitset —
    a dropped row releases, a changed run re-counts, a КБ with its kit request gives back what the kit did not take.
    Never fails the step it follows: the failures come back as text ('' — none)."""
    if fulfillment_uow is None:
        return ""
    try:
        with techcards_uow() as uow:
            kitset = uow.kitsets.get(project)
        return "; ".join(sync_rows(
            kitset.project if kitset is not None else project, kitset, None, actor, now, fulfillment_uow, strict=False,
        ))
    except Exception as error:  # noqa: BLE001
        _log.exception("kit reserve resync %s", project)
        return str(error)


def release_project(project: str, actor: Actor, now: datetime, fulfillment_uow: Callable[[], Any] | None) -> str:
    """The project is deleted: every techcard reserve it holds goes back to its lot. Failures come back as text."""
    if fulfillment_uow is None:
        return ""
    try:
        return "; ".join(sync_rows(project, None, None, actor, now, fulfillment_uow, strict=False))
    except Exception as error:  # noqa: BLE001 — the card is already deleted, the answer must arrive
        _log.exception("kit reserve release %s", project)
        return str(error)


def reserves_by_row(project: str, fulfillment_uow: Callable[[], Any] | None) -> dict[RowKey, list[KitReserve]]:
    """The card shows what each row holds: {(КБ, артикул): active reserves}."""
    if fulfillment_uow is None:
        return {}
    with fulfillment_uow() as uow:
        reserves = active_kit_reserves(uow, project)
    rows: dict[RowKey, list[KitReserve]] = {}
    for reserve in reserves:
        rows.setdefault(row_key(reserve.item_index, reserve.article), []).append(reserve)
    return rows
