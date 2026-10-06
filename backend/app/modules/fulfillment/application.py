"""Transactional production-kit reservation and handout use cases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.modules.procurement.domain import StockCover, project_key
from app.modules.warehouse.domain import (
    ISSUE_WRITEOFF_AUTHOR,
    StockLot,
    WarehouseValidationError,
    WriteOffEntry,
    issue_writeoff_reason,
)
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.notifications import NotificationOutbox, notification_requested, publish_notification
from app.shared.print_sheet import PrintSheet

from .domain import (
    DEFAULT_HANDOUT_PLACE,
    FulfillmentLine,
    FulfillmentRequest,
    FulfillmentStatus,
    FulfillmentValidationError,
    KitReserve,
    LotSnapshot,
    item_index_key,
    kb_number,
    kb_of,
    project_of,
)


class ConcurrentFulfillmentUpdate(RuntimeError):
    pass


class FulfillmentRepository(Protocol):
    def get(self, request_id: UUID) -> FulfillmentRequest | None: ...
    def by_idempotency_key(self, key: str) -> FulfillmentRequest | None: ...
    def numbers(self, year: int) -> Sequence[str]: ...
    def list(self, limit: int = 200, after_id: UUID | None = None) -> Sequence[FulfillmentRequest]: ...
    def add(self, request: FulfillmentRequest) -> None: ...
    def delete(self, request_id: UUID) -> None: ...
    def open_requests(self) -> Sequence[FulfillmentRequest]: ...


class WarehouseRepository(Protocol):
    def get(self, lot_id: UUID) -> StockLot | None: ...
    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLot]: ...
    def add(self, lot: StockLot) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class StockCoverRepository(Protocol):
    """Procurement «Со склада» reserves; optional on the unit of work (`stock_covers`), see `_project_covers`."""

    def active_on_lots(self, lot_ids: Sequence[UUID]) -> Sequence[StockCover]: ...
    def add(self, cover: StockCover) -> None: ...


class KitReserveRepository(Protocol):
    """Techcard reserves of a project КБ; optional on the unit of work (`kit_reserves`), see `_kit_reserves`."""

    def active_on_lots(self, lot_ids: Sequence[UUID]) -> Sequence[KitReserve]: ...
    def for_project(self, project: str) -> Sequence[KitReserve]: ...
    def add(self, reserve: KitReserve) -> None: ...


class KitSites(Protocol):
    """Where a kit goes on the shop floor; optional on the unit of work (`kit_sites`), see `handout_place` and
    `mark_ready`. Without it a kit is announced to its requester only and offered «Зона выдачи»."""

    def site_of(self, request: FulfillmentRequest) -> str: ...
    def masters(self, site: str) -> Sequence[UUID]: ...
    def last_handout(self, site: str) -> str | None: ...


class UnitOfWork(Protocol):
    fulfillment: FulfillmentRepository
    warehouse: WarehouseRepository
    writeoffs: AppendOnlyRepository  # the warehouse СПИСАНИЕ journal
    audit: AppendOnlyRepository
    outbox: NotificationOutbox

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class CreateFulfillmentCommand:
    idempotency_key: str
    purpose: str
    deadline: date | None
    lines: tuple[FulfillmentLine, ...]
    actor: Actor
    now: datetime
    # The lot each line is picked from (IssueForm → queueIssueRequest items {wh, grn, pkt, qty}); None — no lot,
    # the line waits for a delivery. Empty — no line has a lot.
    lot_ids: tuple[UUID | None, ...] = ()
    # shortNote of queueIssueRequest: shortfalls known already while picking («нет на складе» of the calculation form).
    short_note: tuple[str, ...] = ()


@dataclass(frozen=True)
class VersionedFulfillmentCommand:
    request_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class BindLotCommand(VersionedFulfillmentCommand):
    line_id: UUID
    lot_id: UUID


@dataclass(frozen=True)
class UnbindLotCommand(VersionedFulfillmentCommand):
    line_id: UUID


@dataclass(frozen=True)
class MarkReadyCommand(VersionedFulfillmentCommand):
    handout_location: str


@dataclass(frozen=True)
class MarkReturnedCommand(VersionedFulfillmentCommand):
    return_location: str


@dataclass(frozen=True)
class ReturnDisposition:
    line_id: UUID
    returned_good: Decimal
    defective_returned: Decimal
    defective_missing: Decimal
    storage_location: str | None = None


@dataclass(frozen=True)
class CompleteReturnCommand(VersionedFulfillmentCommand):
    lines: tuple[ReturnDisposition, ...]


@dataclass(frozen=True)
class OffBookCommand(VersionedFulfillmentCommand):
    """«есть, но не заведено» on one waiting line (`off_book=True`) or the mark removed (`off_book=False`)."""

    line_id: UUID
    off_book: bool = True


@dataclass(frozen=True)
class BindProjectLotsCommand:
    """«Запросы на комплектацию»: one item (article + name) gets one or several lots for every КБ of the project at
    once, or «есть, но не заведено» on all its waiting lines. `versions` — the request versions the screen showed."""

    project: str
    article: str
    name: str
    lot_ids: tuple[UUID, ...]
    off_book: bool
    versions: dict[UUID, int]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ProjectBinding:
    request_id: UUID
    number: str
    kb: str
    line_id: UUID
    quantity: Decimal
    lot_id: UUID | None = None
    grn: str | None = None
    off_book: bool = False


@dataclass(frozen=True)
class ProjectBindResult:
    bound: tuple[ProjectBinding, ...]
    short: Decimal  # the item's need the chosen lots did not cover: those lines keep waiting
    unit: str
    requests: tuple[FulfillmentRequest, ...]


def next_request_number(year: int, existing: Sequence[str]) -> str:
    prefix = f"REQ.{year % 100:02d}."
    used = [
        int(value[len(prefix) :])
        for value in existing
        if value.startswith(prefix) and value[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(used, default=0) + 1:07d}"


def _picked_lots(command: CreateFulfillmentCommand) -> tuple[UUID | None, ...]:
    if command.lot_ids and len(command.lot_ids) != len(command.lines):
        raise FulfillmentValidationError("Партии указаны не для каждой позиции")
    return command.lot_ids or (None,) * len(command.lines)


def _creation_fingerprint(command: CreateFulfillmentCommand) -> str:
    payload = {
        "purpose": str(command.purpose).strip(),
        "deadline": command.deadline.isoformat() if command.deadline else None,
        "lines": [
            {
                "article": line.article,
                "name": line.name,
                "unit": line.unit,
                "requirement": str(line.requirement),
                **({"lot_id": str(lot_id)} if lot_id else {}),
            }
            for line, lot_id in zip(command.lines, _picked_lots(command))
        ],
        **({"short_note": list(command.short_note)} if command.short_note else {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _versioned(
    request_id: UUID,
    expected_version: int,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    request = uow.fulfillment.get(request_id)
    if request is None:
        raise KeyError(f"Fulfillment request not found: {request_id}")
    if request.version != expected_version:
        raise ConcurrentFulfillmentUpdate("Заявка изменилась после загрузки формы")
    return request


def _project_covers(uow: UnitOfWork, lot: StockLot, purpose: str) -> list[StockCover]:
    """«Со склада» reserves procurement lines of the kit's own project hold on the lot (owner 2026-10-04): the kit
    takes them over first. A cover always sits in the lot's РЕЗЕРВ, so a lot without one has none to look up. A unit
    of work without the procurement covers port (test fakes) takes none."""
    covers = getattr(uow, "stock_covers", None)
    project = project_of(purpose)
    if covers is None or not project or lot.reserved <= 0:
        return []
    return [cover for cover in covers.active_on_lots([lot.id]) if cover.serves_project(project)]


def _serves_kit(reserve: KitReserve, project: str, kb: str | None) -> bool:
    """A techcard reserve serves the project (any КБ when `kb` is None, else only that КБ)."""
    key = project_key(project)
    if not key or key != project_key(reserve.project):
        return False
    return kb is None or (bool(item_index_key(kb)) and item_index_key(kb) == item_index_key(reserve.item_index))


def _kit_reserves(uow: UnitOfWork, lot: StockLot, purpose: str) -> list[KitReserve]:
    """Techcard reserves the kit's own project + КБ hold on the lot: taken over right after the project's covers.
    A request without a project or a КБ, or a unit of work without the port (test fakes), takes none."""
    reserves = getattr(uow, "kit_reserves", None)
    project, kb = project_of(purpose), kb_of(purpose)
    if reserves is None or not project or not kb or lot.reserved <= 0:
        return []
    return [reserve for reserve in reserves.active_on_lots([lot.id]) if _serves_kit(reserve, project, kb)]


def project_reserve_by_lot(
    uow, lots: Sequence[StockLot], project: str, kb: str | None = None,
) -> dict[UUID, Decimal]:
    """How much of each lot the project's own reserves hold — «Со склада» covers and techcard reserves (only those of
    `kb` when given, else of every КБ) — the part the project's kit takes first (`_reserve_for_kit`). Every preview and
    picker that shows what a kit can get from a lot adds it to ДОСТУПНО (other projects see plain ДОСТУПНО = БАЛАНС −
    РЕЗЕРВ, which already leaves the reserve out). Without a project or the ports → {}."""
    ids = [lot.id for lot in lots if lot.reserved > 0]
    if not project or not ids:
        return {}
    held: dict[UUID, Decimal] = {}
    covers = getattr(uow, "stock_covers", None)
    if covers is not None:
        for cover in covers.active_on_lots(ids):
            if cover.serves_project(project):
                held[cover.lot_id] = held.get(cover.lot_id, Decimal("0")) + cover.quantity
    reserves = getattr(uow, "kit_reserves", None)
    if reserves is not None:
        for reserve in reserves.active_on_lots(ids):
            if _serves_kit(reserve, project, kb):
                held[reserve.lot_id] = held.get(reserve.lot_id, Decimal("0")) + reserve.quantity
    return held


def free_for_project(lot: StockLot, held: dict[UUID, Decimal]) -> Decimal:
    """What a kit of the project can take from the lot: ДОСТУПНО plus the project's own reserve on it."""
    return max(lot.available, Decimal("0")) + held.get(lot.id, Decimal("0"))


# ── Резерв техкарты: ГРН, выбранный в спецификации проекта (T1, 06.10.2026) ────


@dataclass(frozen=True)
class HoldKitReserveCommand:
    """One spec row (project + КБ + article) wants `quantity` («на партию», in the row's `unit`) from `lot_id`.
    `lot_id` None — the row holds nothing (ГРН cleared, units differ, the КБ already went to kitting).
    `expected_lot_version` — the lot version the picker showed: a pick against a lot changed since is refused."""

    project: str
    item_index: str
    article: str
    lot_id: UUID | None
    quantity: Decimal
    unit: str
    actor: Actor
    now: datetime
    expected_lot_version: int | None = None


def same_unit(left: str | None, right: str | None) -> bool:
    """«шт» ≡ «Шт.», «м» ≡ «м»: a spec row and a lot measured differently never reserve each other."""

    def key(value: str | None) -> str:
        return str(value or "").strip().rstrip(".").casefold()

    return bool(key(left)) and key(left) == key(right)


def active_kit_reserves(uow, project: str) -> list[KitReserve]:
    """The project's techcard reserves that still hold something (every КБ). No port or no project → []."""
    reserves = getattr(uow, "kit_reserves", None)
    if reserves is None or not str(project or "").strip():
        return []
    return [reserve for reserve in reserves.for_project(project) if reserve.active]


def _row_reserves(uow, project: str, item_index: str, article: str) -> list[KitReserve]:
    kb, art = item_index_key(item_index), str(article or "").strip()
    return [
        reserve for reserve in active_kit_reserves(uow, project)
        if item_index_key(reserve.item_index) == kb and reserve.article.strip() == art
    ]


def kit_requested_kbs(uow, project: str) -> set[str]:
    """`item_index_key` of every КБ of the project that already has a kit request (any status but «Отменена»): its
    kit holds the lots itself, a techcard reserve next to it would count the same goods twice."""
    key = project_key(project)
    found: set[str] = set()
    if not key:
        return found
    after, size = None, 500
    while True:
        page = list(uow.fulfillment.list(limit=size, after_id=after))
        for request in page:
            kb = kb_of(request.purpose)
            if request.status is not FulfillmentStatus.CANCELLED and kb and project_key(project_of(request.purpose)) == key:
                found.add(item_index_key(kb))
        if len(page) < size:
            return found
        after = page[-1].id


def _reserve_details(reserve: KitReserve, quantity: Decimal) -> dict:
    return {
        "quantity": format(quantity.normalize(), "f"), "unit": reserve.unit, "holder": reserve.label,
        "article": reserve.article, "grn": reserve.grn, "kit_reserve_id": str(reserve.id),
    }


def release_kit_reserves(
    reserves: Sequence[KitReserve], actor: Actor, now: datetime, uow, lots: dict[UUID, StockLot] | None = None,
) -> dict[UUID, StockLot]:
    """Give the reserves back to their lots: РЕЗЕРВ goes down by what each still holds, the reserve keeps its trace
    (`released_at`). The lot is saved under its version, so a concurrent move of the lot refuses the whole step.
    → the lots as saved (a caller reserving next on one of them goes on from that version)."""
    lots = {} if lots is None else lots
    for reserve in reserves:
        if not reserve.active:
            continue
        lot = lots.get(reserve.lot_id) or uow.warehouse.get(reserve.lot_id)
        if lot is not None:
            lot = lot.release_reserve(reserve.quantity)
            lots[lot.id] = lot
            uow.warehouse.add(lot)
        details = _reserve_details(reserve, reserve.quantity)
        uow.kit_reserves.add(reserve.release(now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=reserve.lot_id, event_type="warehouse.kit_reserve_released",
            actor=actor, occurred_at=now, details=details,
        ))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=reserve.id, event_type="fulfillment.kit_reserve_released",
            actor=actor, occurred_at=now, details=details,
        ))
    return lots


def _project_covered(uow, lot: StockLot, project: str) -> Decimal:
    """What the project's procurement «Со склада» covers already hold on the lot: its kit takes them first, so the
    techcard reserve does not hold that part a second time."""
    covers = getattr(uow, "stock_covers", None)
    if covers is None or lot.reserved <= 0:
        return Decimal("0")
    return sum(
        (cover.quantity for cover in covers.active_on_lots([lot.id]) if cover.serves_project(project)), Decimal("0"),
    )


def hold_kit_reserve(command: HoldKitReserveCommand, uow) -> KitReserve | None:
    """The spec row's techcard reserve follows its ГРН: one active reserve per project + КБ + article. Holds
    min(«на партию» − the project's covers on the lot, ДОСТУПНО + what this row already holds there) — partial when
    the lot has less; a lot in another unit holds nothing. A re-pick moves the reserve, `lot_id` None releases it, an
    unchanged row is left alone. The caller commits. → the active reserve, or None."""
    if getattr(uow, "kit_reserves", None) is None:
        return None
    project, item_index = str(command.project or "").strip(), str(command.item_index or "").strip()
    article = str(command.article or "").strip()
    if not project or not item_index or not article:
        raise FulfillmentValidationError("Для резерва техкарты нужны проект, КБ и артикул")
    current = _row_reserves(uow, project, item_index, article)
    lot = None
    if command.lot_id is not None:
        lot = uow.warehouse.get(command.lot_id)
        if lot is None:
            raise FulfillmentValidationError("Партия склада не найдена — выберите ГРН заново")
        if command.expected_lot_version is not None and lot.version != command.expected_lot_version:
            raise ConcurrentFulfillmentUpdate("Партия на складе изменилась после загрузки списка — обновите и выберите снова")
    wanted = Decimal("0")
    if lot is not None and command.quantity > 0 and same_unit(command.unit, lot.unit):
        own = sum((reserve.quantity for reserve in current if reserve.lot_id == lot.id), Decimal("0"))
        need = command.quantity - _project_covered(uow, lot, project)
        wanted = max(min(need, max(lot.available, Decimal("0")) + own), Decimal("0"))
    if lot is not None and len(current) == 1 and current[0].lot_id == lot.id and current[0].quantity == wanted:
        return current[0]
    if not current and wanted <= 0:
        return None
    lots = release_kit_reserves(current, command.actor, command.now, uow, {lot.id: lot} if lot is not None else None)
    if wanted <= 0:
        return None
    held = lots[lot.id].reserve(wanted)
    uow.warehouse.add(held)
    reserve = KitReserve(
        id=uuid4(), project=project, item_index=item_index, article=article, lot_id=held.id,
        warehouse=held.warehouse, grn=held.grn, package=held.package, unit=held.unit, quantity=wanted,
        created_at=command.now, created_by=command.actor.name,
    )
    uow.kit_reserves.add(reserve)
    details = _reserve_details(reserve, wanted)
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=held.id, event_type="warehouse.kit_reserve_held",
        actor=command.actor, occurred_at=command.now, details=details,
    ))
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=reserve.id, event_type="fulfillment.kit_reserve_held",
        actor=command.actor, occurred_at=command.now, details=details,
    ))
    return reserve


def _take_over_covers(
    covers: Sequence[StockCover],
    amount: Decimal,
    request: FulfillmentRequest,
    lot: StockLot,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
) -> Decimal:
    """Move up to `amount` of those reserves to the kit line. The lot's РЕЗЕРВ does not change for that part — it
    only changes holder (procurement cover → kit line); the cover keeps how much and which request took it."""
    left = amount
    for cover in covers:
        if left <= 0:
            break
        take = min(cover.quantity, left)
        uow.stock_covers.add(cover.consume(take, request.number))
        details = {
            "quantity": format(take.normalize(), "f"),
            "unit": cover.unit,
            "holder": cover.label,
            "procurement_line_id": str(cover.line_id),
            "cover_id": str(cover.id),
            "request": request.number,
        }
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=lot.id, event_type="warehouse.procurement_reserve_taken_by_kit",
            actor=actor, occurred_at=now, details=details,
        ))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=cover.line_id, event_type="procurement.stock_cover_taken_by_kit",
            actor=actor, occurred_at=now, details={**details, "legacy_id": cover.legacy_id},
        ))
        left -= take
    return amount - left


def _take_over_kit_reserves(
    reserves: Sequence[KitReserve],
    amount: Decimal,
    request: FulfillmentRequest,
    lot: StockLot,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
) -> Decimal:
    """Move up to `amount` of the КБ's techcard reserves to the kit line — the lot's РЕЗЕРВ keeps that part, only the
    holder changes (techcard → kit line), as with `_take_over_covers`."""
    left = amount
    for reserve in reserves:
        if left <= 0:
            break
        take = min(reserve.quantity, left)
        uow.kit_reserves.add(reserve.consume(take, request.number))
        details = {
            "quantity": format(take.normalize(), "f"),
            "unit": reserve.unit,
            "holder": reserve.label,
            "kit_reserve_id": str(reserve.id),
            "request": request.number,
        }
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=lot.id, event_type="warehouse.kit_reserve_taken_by_kit",
            actor=actor, occurred_at=now, details=details,
        ))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=reserve.id, event_type="fulfillment.kit_reserve_taken",
            actor=actor, occurred_at=now, details=details,
        ))
        left -= take
    return amount - left


def _reserve_for_kit(
    request: FulfillmentRequest,
    lot: StockLot,
    wanted: Decimal,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
) -> tuple[Decimal, StockLot]:
    """How much of `wanted` the kit gets from the lot: the project's «Со склада» reserve first, then the techcard
    reserve of its own project + КБ, then ДОСТУПНО. → (given, the lot to save)."""
    covers = _project_covers(uow, lot, request.purpose)
    reserves = _kit_reserves(uow, lot, request.purpose)
    held = sum((cover.quantity for cover in covers), Decimal("0"))
    held += sum((reserve.quantity for reserve in reserves), Decimal("0"))
    give = min(wanted, max(lot.available, Decimal("0")) + held)
    if give <= 0:
        return Decimal("0"), lot
    taken = _take_over_covers(covers, give, request, lot, actor, now, uow)
    taken += _take_over_kit_reserves(reserves, give - taken, request, lot, actor, now, uow)
    rest = give - taken
    return give, (lot.reserve(rest) if rest > 0 else lot)


def _audit(
    request: FulfillmentRequest,
    event_type: str,
    actor: Actor,
    now: datetime,
    details: dict,
    uow: UnitOfWork,
) -> None:
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=request.id,
            event_type=event_type,
            actor=actor,
            occurred_at=now,
            details=details,
        )
    )


def _audit_auto_complete(
    before: FulfillmentRequest,
    after: FulfillmentRequest,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
) -> None:
    """The pick closed the last open line: «Укомплектован» set by itself gets the same journal entry as the button."""
    if before.status is not FulfillmentStatus.COMPLETE and after.status is FulfillmentStatus.COMPLETE:
        _audit(after, "fulfillment.completed", actor, now, {"auto": True}, uow)


def _kit_sites(uow: UnitOfWork) -> KitSites | None:
    return getattr(uow, "kit_sites", None)


def handout_place(request: FulfillmentRequest, uow: UnitOfWork) -> str:
    """The place «Отправить на выдачу» offers (owner, G1-T3 05.10.2026): the last one used for a kit of the same
    site, otherwise «Зона выдачи». The storekeeper may change it before sending."""
    sites = _kit_sites(uow)
    if sites is None:
        return DEFAULT_HANDOUT_PLACE
    return sites.last_handout(sites.site_of(request)) or DEFAULT_HANDOUT_PLACE


def create_fulfillment(
    command: CreateFulfillmentCommand,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    with uow:
        key = str(command.idempotency_key or "").strip()
        if not key:
            raise FulfillmentValidationError("Idempotency key обязателен")
        fingerprint = _creation_fingerprint(command)
        existing = uow.fulfillment.by_idempotency_key(key)
        if existing is not None:
            if existing.source_fingerprint != fingerprint:
                raise FulfillmentValidationError(
                    "Idempotency key уже использован для другой заявки"
                )
            return existing
        request = FulfillmentRequest.create(
            number=next_request_number(command.now.year, uow.fulfillment.numbers(command.now.year)),
            source_idempotency_key=key,
            source_fingerprint=fingerprint,
            requester=command.actor.name,
            requester_id=command.actor.id,
            purpose=command.purpose,
            deadline=command.deadline,
            lines=command.lines,
            created_at=command.now,
        )
        # issueCreateTasks_: a line with a picked lot reserves min(need, БАЛАНС − РЕЗЕРВ) right away; what the lot
        # does not cover becomes a row without a lot (waits for a delivery, shows in НЕДОБОР); a lot that is gone or
        # empty leaves the whole line waiting. The request stays «Новая».
        reserved = 0
        shortfall = [str(note).strip() for note in command.short_note if str(note).strip()]
        for line, lot_id in zip(command.lines, _picked_lots(command)):
            if lot_id is None:
                continue
            lot = uow.warehouse.get(lot_id)
            if lot is None:
                shortfall.append(f"{line.name}: партия не найдена на складе ({_display_number(line.requirement)})")
                continue
            # The project's «Со склада» reserve on this lot counts as the kit's own (taken over first).
            give, reserved_lot = _reserve_for_kit(request, lot, line.requirement, command.actor, command.now, uow)
            if give < line.requirement:
                package = f"·пак.{lot.package}" if lot.package else ""
                shortfall.append(f"{line.name} ({lot.grn}{package}): −{_display_number(line.requirement - give)}")
            if give <= 0:
                continue
            snapshot = LotSnapshot(
                lot_id=lot.id,
                warehouse=lot.warehouse,
                grn=lot.grn,
                package=lot.package,
                storage_location=lot.storage_location,
            )
            request = request.bind_line(line.id, snapshot, give, lot.unit, touch=False)
            uow.warehouse.add(reserved_lot)
            reserved += 1
        written = bool(command.lot_ids or command.short_note)
        request = replace(request, version=1, shortfall_note="; ".join(shortfall) if written else None)
        uow.fulfillment.add(request)
        _audit(request, "fulfillment.created", command.actor, command.now, {
            "number": request.number, "reserved": reserved, "shortfall": request.shortfall,
        }, uow)
        uow.commit()
        return request


def bind_lot(command: BindLotCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        line = next((item for item in request.lines if item.id == command.line_id), None)
        if line is None:
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lot = uow.warehouse.get(command.lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {command.lot_id}")
        # issueAttachLot_ does not compare units: any lot with a balance can be bound, its ЕД.ИЗМ goes to the line.
        # The project's «Со склада» reserve on the lot is the kit's own: taken over before ДОСТУПНО is touched.
        quantity, reserved_lot = _reserve_for_kit(request, lot, line.requirement, command.actor, command.now, uow)
        if quantity <= 0:
            raise FulfillmentValidationError("У выбранной партии нет доступного остатка")
        snapshot = LotSnapshot(
            lot_id=lot.id,
            warehouse=lot.warehouse,
            grn=lot.grn,
            package=lot.package,
            storage_location=lot.storage_location,
        )
        updated = request.bind_line(line.id, snapshot, quantity, lot.unit).stamped(command.now)
        uow.warehouse.add(reserved_lot)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.lot_bound", command.actor, command.now, {"line_id": str(line.id), "lot_id": str(lot.id), "quantity": str(quantity)}, uow)
        _audit_auto_complete(request, updated, command.actor, command.now, uow)
        uow.commit()
        return updated


def unbind_lot(command: UnbindLotCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        line = next((item for item in request.lines if item.id == command.line_id), None)
        if line is None or line.lot is None or line.reserved <= 0:
            raise FulfillmentValidationError("У позиции нет резерва для снятия")
        lot = uow.warehouse.get(line.lot.lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
        updated = request.unbind_line(line.id).stamped(command.now)
        uow.warehouse.add(lot.release_reserve(line.reserved))
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.lot_unbound", command.actor, command.now, {"line_id": str(line.id), "lot_id": str(lot.id), "quantity": str(line.reserved)}, uow)
        uow.commit()
        return updated


def _off_book_details(line: FulfillmentLine) -> dict:
    return {
        "line_id": str(line.id), "article": line.article, "name": line.name,
        "quantity": _display_number(line.requirement), "unit": line.unit,
    }


def set_off_book(command: OffBookCommand, uow: UnitOfWork) -> FulfillmentRequest:
    """«есть, но не заведено» on a waiting line, or the mark removed. Nothing on the warehouse moves; the audit keeps
    who, when and how much."""
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        line = next((item for item in request.lines if item.id == command.line_id), None)
        if line is None:
            raise FulfillmentValidationError("Позиция заявки не найдена")
        if command.off_book:
            updated = request.mark_off_book(line.id).stamped(command.now)
            event = "fulfillment.off_book_marked"
        else:
            updated = request.clear_off_book(line.id).stamped(command.now)
            event = "fulfillment.off_book_cleared"
        uow.fulfillment.add(updated)
        _audit(updated, event, command.actor, command.now, _off_book_details(line), uow)
        _audit_auto_complete(request, updated, command.actor, command.now, uow)
        uow.commit()
        return updated


def _item_key(article: str, name: str) -> tuple[str, str]:
    """The screen's position key (FulfillmentPage ProjectPanel: «article|name», trimmed, case-insensitive)."""
    return str(article or "").strip().casefold(), str(name or "").strip().casefold()


def _natural_key(value: str) -> tuple:
    """«FRX.26.0000010» after «FRX.26.0000009»; «КБ10» after «КБ2»."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part.casefold())
        for part in re.split(r"(\d+)", str(value or "")) if part
    )


def _kit_order(request: FulfillmentRequest) -> tuple:
    """КБ in natural order (КБ1, КБ2 … КБ10), a request without a КБ last; ties by request number."""
    number = kb_number(request.purpose)
    return (number is None, number or 0, request.number)


def _fill_line_fifo(
    request: FulfillmentRequest,
    line_id: UUID,
    lots: Sequence[StockLot],
    current_lots: dict[UUID, StockLot],
    budget: dict[UUID, Decimal] | None,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
    details: dict,
) -> tuple[FulfillmentRequest, Decimal, list[ProjectBinding]]:
    """One waiting line takes what the lots give, in the order given (`_reserve_for_kit`: the project's covers, then
    the КБ's techcard reserve, then ДОСТУПНО). A lot that falls short splits the line and the next lot takes the
    remainder. `current_lots` holds each lot as reserved so far in this transaction; `budget` (lot id → the most it
    may still give, None — no limit) caps a lot below its ДОСТУПНО. → (request, need left uncovered, bindings)."""
    kb = kb_of(request.purpose)
    line = next(row for row in request.lines if row.id == line_id)
    need, target = line.requirement, line_id
    bound: list[ProjectBinding] = []
    for candidate in lots:
        if need <= 0:
            break
        lot = current_lots[candidate.id]
        wanted = need if budget is None else min(need, budget.get(lot.id, Decimal("0")))
        if wanted <= 0:
            continue
        give, reserved_lot = _reserve_for_kit(request, lot, wanted, actor, now, uow)
        if give <= 0:
            continue
        before = {row.id for row in request.lines}
        request = request.bind_line(target, LotSnapshot(
            lot_id=lot.id, warehouse=lot.warehouse, grn=lot.grn, package=lot.package,
            storage_location=lot.storage_location,
        ), give, lot.unit)
        current_lots[lot.id] = reserved_lot
        uow.warehouse.add(reserved_lot)
        if budget is not None:
            budget[lot.id] = budget.get(lot.id, Decimal("0")) - give
        bound.append(ProjectBinding(
            request_id=request.id, number=request.number, kb=kb, line_id=target,
            quantity=give, lot_id=lot.id, grn=lot.grn,
        ))
        _audit(request, "fulfillment.lot_bound", actor, now, {
            "line_id": str(target), "lot_id": str(lot.id), "quantity": str(give), **details,
        }, uow)
        need -= give
        if need > 0:  # the lot fell short: the split-off remainder waits for the next lot
            target = next(row.id for row in request.lines if row.id not in before)
    return request, max(need, Decimal("0")), bound


def bind_project_lots(command: BindProjectLotsCommand, uow: UnitOfWork) -> ProjectBindResult:
    """One item of a project gets the chosen lots for every КБ at once, in one transaction (owner 05.10.2026).

    КБ go in natural order, lots FIFO by ГРН number. Each waiting line of the item takes what the current lot gives
    (`_reserve_for_kit`: the project's covers, then the КБ's techcard reserve, then ДОСТУПНО); a lot that falls short
    splits the line and the next lot takes the remainder; what no lot covers stays a waiting line. Lines that already
    have a lot (or «есть, но не заведено») are left alone. `off_book` marks every waiting line of the item instead."""
    with uow:
        key = project_key(command.project)
        wanted_item = _item_key(command.article, command.name)
        if not key:
            raise FulfillmentValidationError("Не указан проект")
        if not any(wanted_item):
            raise FulfillmentValidationError("Не указана позиция: артикул или наименование")
        lot_ids = tuple(dict.fromkeys(command.lot_ids))
        if command.off_book and lot_ids:
            raise FulfillmentValidationError("Либо партии, либо «есть, но не заведено» — не вместе")
        if not command.off_book and not lot_ids:
            raise FulfillmentValidationError("Выбери хотя бы одну партию")

        for request_id, version in command.versions.items():
            current = uow.fulfillment.get(request_id)
            if current is None or current.version != version:
                raise ConcurrentFulfillmentUpdate("Заявки проекта изменились после загрузки — обнови экран")

        def waiting(line: FulfillmentLine) -> bool:
            return line.waiting and _item_key(line.article, line.name) == wanted_item

        requests = sorted(
            (
                request for request in uow.fulfillment.open_requests()
                if project_key(project_of(request.purpose)) == key and any(waiting(line) for line in request.lines)
            ),
            key=_kit_order,
        )
        if not requests:
            raise FulfillmentValidationError(
                f"В заявках проекта {command.project} нет позиций «{command.name or command.article}», ждущих партию"
            )
        unseen = [request.number for request in requests if request.id not in command.versions]
        if unseen:
            raise ConcurrentFulfillmentUpdate(
                f"В проекте появились заявки, которых нет на экране ({', '.join(unseen)}) — обнови экран"
            )

        lots: list[StockLot] = []
        for lot_id in lot_ids:
            lot = uow.warehouse.get(lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {lot_id}")
            lots.append(lot)
        lots.sort(key=lambda lot: (_natural_key(lot.grn), _natural_key(lot.package), str(lot.id)))
        current_lots = {lot.id: lot for lot in lots}

        bound: list[ProjectBinding] = []
        saved: list[FulfillmentRequest] = []
        short = Decimal("0")
        unit = ""
        for request in requests:
            original = request
            kb = kb_of(request.purpose)
            for line_id in [line.id for line in request.lines if waiting(line)]:
                line = next(row for row in request.lines if row.id == line_id)
                unit = unit or line.unit
                if command.off_book:
                    request = request.mark_off_book(line_id)
                    bound.append(ProjectBinding(
                        request_id=request.id, number=request.number, kb=kb, line_id=line_id,
                        quantity=line.requirement, off_book=True,
                    ))
                    _audit(request, "fulfillment.off_book_marked", command.actor, command.now, {
                        **_off_book_details(line), "project": command.project,
                    }, uow)
                    continue
                request, need, filled = _fill_line_fifo(
                    request, line_id, lots, current_lots, None, command.actor, command.now, uow,
                    {"project": command.project},
                )
                bound.extend(filled)
                short += need
            if request is not original:
                # Every bind bumped the version; the request changed once — one version step, as the screen expects.
                request = replace(request, version=original.version + 1).stamped(command.now)
                _audit_auto_complete(original, request, command.actor, command.now, uow)
                uow.fulfillment.add(request)
                saved.append(request)
        if not bound:
            raise FulfillmentValidationError("У выбранных партий нет доступного остатка")
        uow.commit()
        return ProjectBindResult(bound=tuple(bound), short=short, unit=unit, requests=tuple(saved))


@dataclass(frozen=True)
class BindArrivedLotCommand:
    """A lot that just arrived, bought for `project`: what it is (КД articles and names of the purchase and the lot)
    and how much arrived — the most the arrival binds, so free stock already on a topped-up lot stays free."""

    project: str
    lot_id: UUID
    quantity: Decimal
    articles: tuple[str, ...]
    names: tuple[str, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ArrivedKit:
    """One kit request of the project the arrival reached: how many of its lines it bound, how many still wait."""

    request: FulfillmentRequest
    kb: str
    lines: int
    quantity: Decimal
    missing: int

    @property
    def complete(self) -> bool:
        return self.missing == 0


@dataclass(frozen=True)
class ArrivedLotResult:
    kits: tuple[ArrivedKit, ...]
    bound: tuple[ProjectBinding, ...]
    short: Decimal  # need of the matching lines the arrival did not cover


def _text_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def arrival_matches(line: FulfillmentLine, articles: Sequence[str], names: Sequence[str]) -> bool:
    """Is the waiting kit line the item that arrived? The КД article is the key kit and purchase share
    (techcards `supply_lines`): a line with an article matches only by article; a line without one — by name."""
    article = _text_key(line.article)
    if article:
        return article in {_text_key(value) for value in articles if _text_key(value)}
    name = _text_key(line.name)
    return bool(name) and name in {_text_key(value) for value in names if _text_key(value)}


def bind_arrived_lot(command: BindArrivedLotCommand, uow: UnitOfWork) -> ArrivedLotResult:
    """An arrival binds itself to the waiting kit lines of the project it was bought for (owner 05.10.2026).

    Runs inside the caller's open unit of work and does not commit: the outbox consumer commits it together with its
    message, so a message is applied once. The lines are those of the project's open requests that still wait for a
    lot and are the arrived item (`arrival_matches`); КБ in natural order, each line takes what the lot gives
    (`_fill_line_fifo` — the project's own reserves, then ДОСТУПНО; never another project's reserve), at most the
    arrived quantity in all. Kits of other projects and lines that already have a lot are not touched."""
    empty = ArrivedLotResult(kits=(), bound=(), short=Decimal("0"))
    key = project_key(command.project)
    if not key or command.quantity <= 0:
        return empty
    lot = uow.warehouse.get(command.lot_id)
    if lot is None:
        return empty

    def waiting(line: FulfillmentLine) -> bool:
        return line.waiting and arrival_matches(line, command.articles, command.names)

    requests = sorted(
        (
            request for request in uow.fulfillment.open_requests()
            if project_key(project_of(request.purpose)) == key and any(waiting(line) for line in request.lines)
        ),
        key=_kit_order,
    )
    current_lots = {lot.id: lot}
    budget = {lot.id: command.quantity}
    kits: list[ArrivedKit] = []
    bound: list[ProjectBinding] = []
    short = Decimal("0")
    for request in requests:
        original = request
        filled_here: list[ProjectBinding] = []
        for line_id in [line.id for line in request.lines if waiting(line)]:
            request, need, filled = _fill_line_fifo(
                request, line_id, (lot,), current_lots, budget, command.actor, command.now, uow,
                {"project": command.project, "source": "arrival"},
            )
            filled_here.extend(filled)
            short += need
        if request is original:
            continue
        request = replace(request, version=original.version + 1).stamped(command.now)
        uow.fulfillment.add(request)
        bound.extend(filled_here)
        kits.append(ArrivedKit(
            request=request,
            kb=kb_of(request.purpose),
            lines=len(filled_here),
            quantity=sum((item.quantity for item in filled_here), Decimal("0")),
            missing=len(request.open_lines()),
        ))
    return ArrivedLotResult(kits=tuple(kits), bound=tuple(bound), short=short)


def _send_to_handout(
    request: FulfillmentRequest,
    handout_location: str,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
    *,
    notify: bool = True,
) -> FulfillmentRequest:
    """«Укомплектован» → «Готов к выдаче» inside the caller's unit of work (no commit). `notify=False` — the kit is
    handed out in the same action, so nobody is told to come and pick it up; the audit keeps the place and the site."""
    updated = request.mark_ready(handout_location).stamped(now)
    uow.fulfillment.add(updated)
    sites = _kit_sites(uow)
    site = sites.site_of(updated) if sites is not None else ""
    details: dict = {"handout_location": updated.handout_location}
    if site:  # `last_handout` of the site reads it back for the next kit
        details["site"] = site
    if not notify:
        details["with_issue"] = True
    _audit(updated, "fulfillment.ready", actor, now, details, uow)
    if notify:
        masters = tuple(sites.masters(site)) if sites is not None else ()
        # Issue.gs:459-474: the requester learns the kit is ready and where to pick it up; the masters of the kit's
        # site learn it too (owner, G1-T3 05.10.2026). One message, recipients de-duplicated: a master who requested
        # the kit himself gets it once.
        publish_notification(uow.outbox, notification_requested(
            key=f"fulfillment:{updated.id}:ready:v{updated.version}",
            now=now,
            title=f"Заявка {updated.number} готова к получению",
            body=(
                f"Заявка {updated.number} укомплектована и ждёт выдачи, место: {updated.handout_location}."
                f"\nНазначение: {updated.purpose}"
            ),
            kind="ready",
            reference=updated.number,
            user_ids=(updated.requester_id, *masters),
        ))
    return updated


def mark_ready(command: MarkReadyCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = _send_to_handout(request, command.handout_location, command.actor, command.now, uow)
        uow.commit()
        return updated


def _hand_out(
    request: FulfillmentRequest,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
    lots: dict[UUID, StockLot],
) -> FulfillmentRequest:
    """«Готов к выдаче» → «КБ Выдана» inside the caller's unit of work (no commit): reserve → списано on every lot,
    one СПИСАНИЕ row per position. `lots` holds each lot as posted so far in this transaction — two kits of one batch
    on the same lot write off one after the other, never from the same stale balance."""
    updated = request.mark_issued().stamped(now)
    posted: dict[UUID, StockLot] = {}
    for line in request.lines:
        if line.off_book:  # «есть, но не заведено»: no lot, no stock movement, no СПИСАНИЕ row
            continue
        if line.lot is None:
            raise FulfillmentValidationError("У позиции не выбрана партия")
        lot = lots.get(line.lot.lot_id) or uow.warehouse.get(line.lot.lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
        lots[lot.id] = posted[lot.id] = lot.issue_reserved(line.reserved)
        uow.warehouse.add(lots[lot.id])
    # Issue.gs:475-502: one СПИСАНИЕ row per issued position, ОСТАТОК ПОСЛЕ = the lot balance once the
    # whole request is posted (two positions on one lot both show the final balance).
    for line in request.lines:
        if line.off_book or line.reserved <= 0:
            continue
        lot = posted[line.lot.lot_id]
        uow.writeoffs.add(WriteOffEntry.for_lot(
            lot,
            kind="issue",
            quantity=line.reserved,
            name=line.name,
            reason=issue_writeoff_reason(request.number),
            purpose="",
            author=ISSUE_WRITEOFF_AUTHOR,
            actor_id=actor.id,
            actor_name=actor.name,
            occurred_at=now,
            remaining_after=lot.balance,
            source_key=f"fulfillment-issue:{line.id}",
        ))
    uow.fulfillment.add(updated)
    off_book = [
        {"line_id": str(line.id), "article": line.article, "name": line.name,
         "quantity": _display_number(line.issued), "unit": line.unit}
        for line in updated.lines if line.off_book
    ]
    _audit(updated, "fulfillment.issued", actor, now, {
        "lines": len(updated.lines), **({"off_book": off_book} if off_book else {}),
    }, uow)
    return updated


def issue_fulfillment(
    command: VersionedFulfillmentCommand,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = _hand_out(request, command.actor, command.now, uow, {})
        uow.commit()
        return updated


# What «Выдать комплект» takes (owner 06.10.2026): a fully picked kit, sent to the handout point or not yet.
ISSUABLE = frozenset({FulfillmentStatus.COMPLETE, FulfillmentStatus.READY})


def kit_label(request: FulfillmentRequest) -> str:
    """«КБ2 (REQ.26.0000020)» — how a batch names the kit it stopped on; a request without a КБ — its number."""
    kb = kb_of(request.purpose)
    return f"{kb} ({request.number})" if kb else request.number


@dataclass(frozen=True)
class IssueBatchCommand:
    """«Выдать выбранные»: the kits of one screen, each with the version the screen showed."""

    versions: tuple[tuple[UUID, int], ...]
    actor: Actor
    now: datetime


def _refuse_status(request: FulfillmentRequest) -> str:
    if request.status in {FulfillmentStatus.NEW, FulfillmentStatus.UNDERFILLED}:
        total = len(request.lines)
        if request.open_lines():
            return f"комплект не собран, набрано {total - len(request.open_lines())} из {total} позиций"
        return "комплект не отмечен «Укомплектован»"
    if request.status in {FulfillmentStatus.ISSUED, FulfillmentStatus.RETURNED, FulfillmentStatus.DONE}:
        return "уже выдана"
    return f"заявку в статусе «{request.status.value}» не выдают"


def issue_batch(command: IssueBatchCommand, uow: UnitOfWork) -> tuple[FulfillmentRequest, ...]:
    """Hand out several kits at once — the whole order or the КБ ticked (owner 06.10.2026) — in one transaction.

    Every kit is checked before anything moves: it exists, still has the version the screen showed (a repeated or
    double-clicked call finds the version moved on and is refused, so nothing is written off twice) and is fully
    picked. A «Укомплектован» kit is sent to the handout point first (place: `handout_place`, «Отправлена на выдачу»
    stamped, no «готов к получению» message) and handed out at once. Any refusal names the КБ and rolls the whole
    batch back: nothing is issued."""
    with uow:
        if not command.versions:
            raise FulfillmentValidationError("Не выбрано ни одной заявки для выдачи")
        if len({request_id for request_id, _ in command.versions}) != len(command.versions):
            raise FulfillmentValidationError("Заявка указана в выдаче дважды")
        requests: list[FulfillmentRequest] = []
        for request_id, version in command.versions:
            request = uow.fulfillment.get(request_id)
            if request is None:
                raise KeyError("Заявка на выдачу не найдена — обнови экран")
            if request.version != version:
                raise ConcurrentFulfillmentUpdate(
                    f"{kit_label(request)}: заявка изменилась после загрузки — обнови экран. Ничего не выдано."
                )
            if request.status not in ISSUABLE or not request.fully_reserved:
                reason = _refuse_status(request) if request.status not in ISSUABLE else "комплект собран не целиком"
                raise FulfillmentValidationError(f"{kit_label(request)}: {reason}. Ничего не выдано.")
            requests.append(request)
        lots: dict[UUID, StockLot] = {}
        issued: list[FulfillmentRequest] = []
        for request in sorted(requests, key=_kit_order):
            try:
                current = request
                if current.status is FulfillmentStatus.COMPLETE:
                    current = _send_to_handout(
                        current, handout_place(current, uow), command.actor, command.now, uow, notify=False,
                    )
                issued.append(_hand_out(current, command.actor, command.now, uow, lots))
            except (FulfillmentValidationError, WarehouseValidationError) as error:
                raise type(error)(f"{kit_label(request)}: {error}. Ничего не выдано.") from error
        uow.commit()
        return tuple(issued)


def mark_returned(command: MarkReturnedCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.mark_returned(command.return_location).stamped(command.now)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.returned", command.actor, command.now, {
            "return_location": updated.return_location,
        }, uow)
        uow.commit()
        return updated


def complete_return(command: CompleteReturnCommand, uow: UnitOfWork) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        if len({line.line_id for line in command.lines}) != len(command.lines):
            raise FulfillmentValidationError("Позиция возврата указана дважды")
        by_id = {line.line_id: line for line in command.lines}
        updated = request.complete_return({
            line.line_id: (
                line.returned_good,
                line.defective_returned,
                line.defective_missing,
            )
            for line in command.lines
        }).stamped(command.now)
        original_by_id = {line.id: line for line in request.lines}
        for disposition in command.lines:
            original = original_by_id[disposition.line_id]
            if original.off_book:  # no lot to put anything back on; «годное» > 0 is refused by settle_return
                continue
            if original.lot is None:
                raise FulfillmentValidationError("У позиции не сохранена выданная партия")
            if disposition.returned_good <= 0 and not str(disposition.storage_location or "").strip():
                continue
            lot = uow.warehouse.get(original.lot.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {original.lot.lot_id}")
            if disposition.returned_good > 0:
                lot = lot.return_writeoff(disposition.returned_good)
            if str(disposition.storage_location or "").strip():
                lot = lot.move(str(disposition.storage_location))
            uow.warehouse.add(lot)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.return_completed", command.actor, command.now, {
            "returned_good": str(sum((line.returned_good for line in command.lines), Decimal("0"))),
            "defective_returned": str(sum((line.defective_returned for line in command.lines), Decimal("0"))),
            "defective_missing": str(sum((line.defective_missing for line in command.lines), Decimal("0"))),
        }, uow)
        uow.commit()
        return updated


def cancel_fulfillment(
    command: VersionedFulfillmentCommand,
    uow: UnitOfWork,
) -> FulfillmentRequest:
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.cancel()
        if updated == request:
            return request
        updated = updated.stamped(command.now)
        for line in request.lines:
            if line.lot is None or line.reserved <= 0:
                continue
            lot = uow.warehouse.get(line.lot.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
            uow.warehouse.add(lot.release_reserve(line.reserved))
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.cancelled", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


def complete_kit(command: VersionedFulfillmentCommand, uow: UnitOfWork) -> FulfillmentRequest:
    """FF-3: «Укомплектован» is pressed by the picker (PortalApp ISS_ACT_LABEL_ «✅ Укомплектован»)."""
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        completed = request.mark_complete()
        if completed is request:  # the last pick completed it already: nothing to write
            return request
        updated = completed.stamped(command.now)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.completed", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


def close_issued(command: VersionedFulfillmentCommand, uow: UnitOfWork) -> FulfillmentRequest:
    """«КБ Выдана» → «Готово» without a return (ISS_MODES_.give.next); the write-off already happened at issue."""
    with uow:
        request = _versioned(command.request_id, command.expected_version, uow)
        updated = request.close_issued().stamped(command.now)
        uow.fulfillment.add(updated)
        _audit(updated, "fulfillment.closed", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


@dataclass(frozen=True)
class DeleteFulfillmentCommand:
    request_id: UUID
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class DeletedFulfillment:
    number: str
    lines: int
    freed: int


def delete_fulfillment(command: DeleteFulfillmentCommand, uow: UnitOfWork) -> DeletedFulfillment:
    """FF-5, Issue.gs issueDelete_: an issued position forbids deleting (it is the write-off document); every
    reserve goes back to its lot; the request and its lines disappear. The administrator gate lives in the API."""
    with uow:
        request = uow.fulfillment.get(command.request_id)
        if request is None:
            raise KeyError(f"Fulfillment request not found: {command.request_id}")
        issued = [line for line in request.lines if line.issued > 0]
        if issued:
            raise FulfillmentValidationError(
                f"Заявка {request.number} уже выдавалась (позиций с выдачей: {len(issued)}). "
                "Удалять её нельзя — это документ проведённого списания. Оформи возврат."
            )
        freed = 0
        for line in request.lines:
            if line.lot is None or line.reserved <= 0:
                continue
            lot = uow.warehouse.get(line.lot.lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {line.lot.lot_id}")
            uow.warehouse.add(lot.release_reserve(line.reserved))
            freed += 1
        uow.fulfillment.delete(request.id)
        _audit(request, "fulfillment.deleted", command.actor, command.now, {
            "number": request.number, "lines": len(request.lines), "freed": freed,
        }, uow)
        uow.commit()
        return DeletedFulfillment(number=request.number, lines=len(request.lines), freed=freed)


def _display_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


# Печатные бланки (браузерная печать — см. app.shared.print_sheet). Портированы из SUPPLYMAP
# Issue.gs: buildIssueDoc_ (295-308) и buildIssueReturnDoc (572-587). Чтение, без транзакции.

def build_issue_print_sheet(request: FulfillmentRequest, now: datetime) -> PrintSheet:
    """«Заявка на выдачу №…»: строка на позицию, «Нужно» (requirement) отдельно от «Набрать»
    (issued, пока не выдано — reserved); позиция без партии ждёт поставки и уходит в «недобор»."""
    columns = (
        "№", "Артикул КД", "Наименование", "ГРН", "Пакет", "Нужно", "Набрать", "Ед.",
        "Место хранения", "✓",
    )
    rows: list[tuple[str, ...]] = []
    waiting_names: list[str] = []
    for index, line in enumerate(request.lines, start=1):
        if line.off_book:
            grn = "есть, не заведено"
        else:
            grn = line.lot.grn if line.lot else "— ждём поставки —"
        package = line.lot.package if line.lot else ""
        place = line.lot.storage_location if line.lot else ""
        if line.issued > 0:
            picked = line.issued
        else:
            picked = line.requirement if line.off_book else line.reserved
        rows.append((
            str(index), line.article or "", line.name, grn, package,
            _display_number(line.requirement), _display_number(picked), line.unit, place, "",
        ))
        if line.lot is None and not line.off_book:
            waiting_names.append(line.name)
    note = f"НЕДОБОР (на складе не хватило): {', '.join(waiting_names)}" if waiting_names else ""
    subtitle = (
        f"Дата: {now.strftime('%d.%m.%Y %H:%M')} · Заявитель: {request.requester or '—'} · "
        f"Назначение: {request.purpose or '—'} · "
        f"Дедлайн: {request.deadline.strftime('%d.%m.%Y') if request.deadline else '—'}"
    )
    return PrintSheet(
        title=f"ЗАЯВКА НА ВЫДАЧУ {request.number}",
        subtitle=subtitle,
        columns=columns,
        rows=tuple(rows),
        note=note,
        sign="Набрал: ______________    Выдал: ______________    Получил: ______________    Дата: ________",
    )


def build_issue_return_sheet(request: FulfillmentRequest, now: datetime) -> PrintSheet:
    """«Лист возврата по заявке №…»: только выданные позиции, столбцы возврата/брака пустые —
    заполняются от руки при физической раскладке, до цифрового `complete_return`."""
    if not any(line.issued > 0 for line in request.lines):
        raise FulfillmentValidationError("По заявке ещё нет выданных позиций")
    columns = (
        "№", "Наименование", "ГРН", "Пакет", "Выдано", "Возвр.", "Брак в.", "Брак н.",
        "Место хранения", "✓ / новое место",
    )
    rows: list[tuple[str, ...]] = []
    index = 0
    for line in request.lines:
        if line.issued <= 0:
            continue
        index += 1
        grn = "есть, не заведено" if line.off_book else (line.lot.grn if line.lot else "")
        package = line.lot.package if line.lot else ""
        place = line.lot.storage_location if line.lot else ""
        rows.append((
            str(index), line.name, grn, package, _display_number(line.issued),
            "", "", "", place, "",
        ))
    return PrintSheet(
        title=f"ЛИСТ ВОЗВРАТА ПО ЗАЯВКЕ {request.number}",
        subtitle=f"Сформирован: {now.strftime('%d.%m.%Y %H:%M')}",
        columns=columns,
        rows=tuple(rows),
        sign="Разложил: ______________________        Дата: ______________",
    )
