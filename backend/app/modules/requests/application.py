"""Transactional request use cases expressed against persistence ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage

from .domain import (
    UNITS,
    Actor,
    ProcurementRequestedSnapshot,
    Request,
    RequestDraft,
    RequestStatus,
    ValidationError,
    create_request_batch,
    build_procurement_requested_snapshot,
    CALC_MAX_COLUMNS,
    CalcItem,
    calc_project,
    parse_zpk,
    spreadsheet_id_from_url,
)


class RequestRepository(Protocol):
    def by_creation_key(self, actor_id: UUID, creation_key: str) -> Sequence[Request]: ...
    def all_legacy_ids(self) -> Sequence[str]: ...
    def all_designations(self) -> Sequence[str]: ...
    def add(self, request: Request) -> None: ...
    def get(self, request_id: UUID) -> Request | None: ...
    def delete(self, request_id: UUID) -> None: ...
    def recent(self, limit: int, search: str = "") -> Sequence[Request]:
        """Newest first; ``search`` — substring of ID, НАЗНАЧЕНИЕ, наименования, проекта, …"""
        ...
    def for_project(self, project: str) -> Sequence[Request]:
        """Every request of one project (by № ПРОЕКТА, ЦЕЛЬ or ОБОЗНАЧЕНИЕ, exact key), newest first."""
        ...
    def by_initiator(
        self, actor_id: UUID, limit: int, offset: int, search: str = ""
    ) -> Sequence[Request]:
        """RQ-MINE: server-side «Мои заявки», newest first, paginated."""
        ...
    def count_by_initiator(self, actor_id: UUID, search: str = "") -> int: ...
    def purposes_by_ids(self, request_ids: Sequence[UUID]) -> dict[UUID, str]:
        """ЦЕЛЬ заявок одним запросом."""
        ...


class ProcurementStageLookup(Protocol):
    """Port to the Procurement module: the pipeline stage a handed-off request has reached.

    ``None``/absent means Procurement has not taken the request yet — Requests' own status stands.
    """

    def stages_for(self, request_ids: Sequence[UUID]) -> dict[UUID, str]: ...

    def details_for(self, request_ids: Sequence[UUID]) -> dict[UUID, ProcurementDetails]:
        """«Мои заявки»: what the initiator sees of the purchase sheet (stage, buyer, supplier, …)."""
        ...


@dataclass(frozen=True)
class ProcurementDetails:
    """Read model of a handed-off request's procurement lines, joined for one «Мои заявки» row."""

    stage: str
    buyer: str | None = None
    supplier: str | None = None
    order_number: str | None = None
    expected_arrival: date | None = None
    sum: Decimal | None = None


class ProjectRegistry(Protocol):
    """Port to the Projects module: open (non-terminal, not «Закрыт») project numbers.

    ``None`` means the registry is not available yet; the format check still applies.
    """

    def open_projects(self) -> Sequence[str] | None: ...

    # Optional (read with getattr): ``purposes() -> list[str] | None`` — the editable purposes directory, None when there is none.


class CalcSheetError(RuntimeError):
    """The calculation spreadsheet could not be read; ``message`` is shown to the user as is.

    ``kind``: ``config`` — reading by link is not configured on this server; ``access`` — the sheet is
    not shared with the service account (or does not exist); ``unsupported`` — the link is not a
    native Google Sheet; ``upstream`` — Google did not answer.
    """

    def __init__(self, message: str, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class CalcSpreadsheet:
    """What `extractFromCalc` reads from a calculation file: its title, ЗКП display values, ЗКЗ!B2.

    ``zpk_grid`` is ``None`` when the file has no «ЗКП» sheet; ``project_cell`` is '' without «ЗКЗ».
    """

    title: str
    zpk_grid: list[list[str]] | None
    project_cell: str = ""


class CalcSpreadsheetReader(Protocol):
    """Port to Google Sheets: reads one calculation spreadsheet by id (display values, as GAS)."""

    def read_calc(self, spreadsheet_id: str) -> CalcSpreadsheet: ...


@dataclass(frozen=True)
class CalcExtract:
    project: str
    items: list[CalcItem]


def extract_from_calc(url: str, reader: CalcSpreadsheetReader) -> CalcExtract:
    """`extractFromCalc(url)`: the ЗКП sheet of the linked calculation → positions + project.

    Wording of the refusals is SUPPLYMAP's; the sheet is read up to 20 columns, the project comes
    from ЗКЗ!B2, falling back to the file name without «Расчёт».
    """
    spreadsheet_id = spreadsheet_id_from_url(url)
    if not spreadsheet_id:
        raise ValidationError("Не похоже на ссылку Google Таблицы")
    sheet = reader.read_calc(spreadsheet_id)
    if sheet.zpk_grid is None:
        raise ValidationError("В таблице нет листа «ЗКП»")
    grid = [[cell or "" for cell in row[:CALC_MAX_COLUMNS]] for row in sheet.zpk_grid]
    return CalcExtract(project=calc_project(sheet.project_cell, sheet.title), items=parse_zpk(grid))


class AppendOnlyRepository(Protocol):
    def add(self, item: AuditEvent | OutboxMessage) -> None: ...


class OutboxRepository(AppendOnlyRepository, Protocol):
    def by_idempotency_key(self, idempotency_key: str) -> OutboxMessage | None: ...


class UnitOfWork(Protocol):
    requests: RequestRepository
    audit: AppendOnlyRepository
    outbox: OutboxRepository

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


class DispatchOutboxRepository(OutboxRepository, Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class DispatchUnitOfWork(UnitOfWork, Protocol):
    outbox: DispatchOutboxRepository


class PoisonRequestMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


@dataclass(frozen=True)
class CreateRequestsCommand:
    creation_key: str
    drafts: Sequence[RequestDraft]
    actor: Actor
    now: datetime
    designation_base: str | None = None  # import from a calculation: one `project.NNN` per batch
    source: str = "form"
    # Requests filed from the techcard/spec (owner, 06.10): handed to Procurement in the same transaction, «В работе»
    # with no buyer — nobody presses «В закупку». Not part of the fingerprint: a retry of a batch filed before the
    # switch still matches its key.
    to_procurement: bool = False


@dataclass(frozen=True)
class HandoffRequestsCommand:
    request_ids: Sequence[UUID]
    actor: Actor
    now: datetime
    retransfer: bool = False
    # «В закупку» is open to everyone (owner, 23.10). A buyer who presses it takes the line «В работе»; anyone else
    # hands it over with nobody's name on it (`queue_for_procurement`: «Неразобранные»), and «Повторно» stays the
    # buyer's. `own_only` — without «Заявки»/«Снабжение» on edit only one's own requests (by initiator id).
    as_buyer: bool = True
    own_only: bool = False


def _creation_fingerprint(command: CreateRequestsCommand) -> str:
    payload = {
        "actor": asdict(command.actor),
        "drafts": [asdict(item) for item in command.drafts],
    }
    if command.designation_base is not None:
        payload["designation_base"] = command.designation_base
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _procurement_requested_payload(
    line: ProcurementRequestedSnapshot,
) -> dict[str, str | None]:
    """Self-contained integration event; Procurement never reads Requests tables."""
    buyer = line.buyer if line.buyer is not None and line.buyer.id else None
    return {
        "source_request_id": str(line.source_request_id),
        "legacy_id": line.legacy_id,
        "request_date": line.request_date.isoformat(),
        "initiator": line.initiator,
        "initiator_id": str(line.initiator_id) if line.initiator_id else None,
        "department": line.department,
        "task_type": line.task_type,
        "designation": line.designation,
        "project_reference": line.project_reference,
        "priority": line.priority,
        "delivery_point": line.delivery_point,
        "planned_date": line.planned_date.isoformat(),
        "link": line.link,
        "item_type": line.item_type,
        "design_article": line.design_article,
        "category": line.category,
        "calculated_name": line.calculated_name,
        "calculated_quantity": str(line.calculated_quantity),
        "calculated_unit": line.calculated_unit,
        "quality_document": line.quality_document,
        "calculated_price": str(line.calculated_price) if line.calculated_price is not None else None,
        "calculated_price_with_vat": (
            str(line.calculated_price_with_vat)
            if line.calculated_price_with_vat is not None
            else None
        ),
        "calculated_sum": str(line.calculated_sum) if line.calculated_sum is not None else None,
        # Owner, 05.10: the line lands «В работе» on the desk of whoever pressed «В закупку» (or cancelled the
        # backorder this reorder replaces). No id — nobody to give it to: the line is «Новый».
        "buyer_id": str(buyer.id) if buyer else None,
        "buyer_name": buyer.name if buyer else None,
        "buyer_department": buyer.department if buyer else None,
    }


def directory_purposes(projects: object | None) -> list[str] | None:
    getter = getattr(projects, "purposes", None)
    found = getter() if callable(getter) else None
    return None if found is None else list(found)


def create_requests(
    command: CreateRequestsCommand,
    uow: UnitOfWork,
    projects: ProjectRegistry | None = None,
) -> list[Request]:
    creation_key = command.creation_key.strip()
    if not creation_key:
        raise ValidationError("Не задан ключ идемпотентности создания")
    fingerprint = _creation_fingerprint(command)

    with uow:
        existing = list(uow.requests.by_creation_key(command.actor.id, creation_key))
        if existing:
            if len(existing) != len(command.drafts) or any(
                item.creation_fingerprint != fingerprint for item in existing
            ):
                raise ValidationError("Ключ идемпотентности уже использован для другой пачки")
            return existing

        requests = create_request_batch(
            command.drafts,
            actor=command.actor,
            now=command.now,
            existing_legacy_ids=uow.requests.all_legacy_ids(),
            existing_designations=uow.requests.all_designations(),
            creation_key=creation_key,
            creation_fingerprint=fingerprint,
            designation_base=command.designation_base,
            directory_purposes=directory_purposes(projects),
        )
        # Ручная заявка: № проекта вписывают руками, пусто — без проекта (владелец, 29.09). Реестр —
        # только подсказка в поле; формат 591К.3 проверяет create_request_batch.
        stored: list[Request] = []
        for request in requests:
            handed = request.queue_for_procurement() if command.to_procurement else request
            uow.requests.add(handed)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=request.id,
                    event_type="request.created",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "legacy_id": request.legacy_id,
                        "designation": request.designation,
                        **({"source": command.source} if command.source != "form" else {}),
                    },
                )
            )
            if command.to_procurement:
                line = build_procurement_requested_snapshot(handed, today=command.now.date())
                uow.audit.add(
                    AuditEvent(
                        id=uuid4(),
                        aggregate_id=handed.id,
                        event_type="request.sent_to_procurement",
                        actor=command.actor,
                        occurred_at=command.now,
                        details={"legacy_id": handed.legacy_id, "source": command.source},
                    )
                )
                uow.outbox.add(
                    OutboxMessage(
                        id=uuid4(),
                        topic="procurement.requested",
                        idempotency_key=line.idempotency_key,
                        created_at=command.now,
                        payload=_procurement_requested_payload(line),
                    )
                )
            stored.append(handed)
        uow.commit()
        return stored


def handoff_requests(
    command: HandoffRequestsCommand,
    uow: UnitOfWork,
) -> list[OutboxMessage]:
    request_ids = list(dict.fromkeys(command.request_ids))
    if not request_ids:
        raise ValidationError("Не выбрано ни одной заявки")

    with uow:
        requests: dict[UUID, Request] = {}
        for request_id in request_ids:
            request = uow.requests.get(request_id)
            if request is None:
                raise KeyError(f"Request not found: {request_id}")
            if command.own_only and request.actor.id != command.actor.id:
                raise PermissionError("Передать в закупку можно только свои заявки")
            requests[request_id] = request

        messages: dict[UUID, OutboxMessage] = {}
        pending: list[tuple[Request, ProcurementRequestedSnapshot, OutboxMessage]] = []
        for request_id in request_ids:
            request = requests[request_id]
            if command.retransfer:
                if not command.as_buyer:
                    raise ValidationError("Повторно в закупку передаёт закупщик")
                request = request.retransfer(command.actor)
            elif request.status is RequestStatus.NEW:
                request = request.start_procurement(command.actor) if command.as_buyer else request.queue_for_procurement()
            line = build_procurement_requested_snapshot(request, today=command.now.date())
            existing_message = uow.outbox.by_idempotency_key(line.idempotency_key)
            if existing_message is not None:
                messages[request_id] = existing_message
                continue
            message = OutboxMessage(
                id=uuid4(),
                topic="procurement.requested",
                idempotency_key=line.idempotency_key,
                created_at=command.now,
                payload=_procurement_requested_payload(line),
            )
            pending.append((request, line, message))

        if not pending:
            return [messages[request_id] for request_id in request_ids]

        for request, line, message in pending:
            uow.requests.add(request)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=request.id,
                    event_type=(
                        "request.resent_to_procurement"
                        if command.retransfer
                        else "request.sent_to_procurement"
                    ),
                    actor=command.actor,
                    occurred_at=command.now,
                    details={"legacy_id": request.legacy_id},
                )
            )
            uow.outbox.add(message)
            messages[request.id] = message
        uow.commit()
        return [messages[request_id] for request_id in request_ids]


def recent_requests(limit: int, uow: UnitOfWork, search: str = "") -> list[Request]:
    with uow:
        return list(uow.requests.recent(limit, search.strip()))


def my_requests(
    actor_id: UUID, limit: int, offset: int, uow: UnitOfWork, search: str = ""
) -> tuple[list[Request], int]:
    """RQ-MINE: replaces the client-side name filter over the latest 200 rows."""
    search = search.strip()
    with uow:
        items = list(uow.requests.by_initiator(actor_id, limit, offset, search))
        total = uow.requests.count_by_initiator(actor_id, search)
    return items, total


def _reorder_draft(original: Request, payload: dict[str, str | None]) -> RequestDraft:
    try:
        quantity = Decimal(str(payload.get("quantity") or ""))
    except DecimalException as error:
        raise ValidationError("requests.reorder_requested: invalid quantity") from error
    if not quantity.is_finite():
        raise ValidationError("requests.reorder_requested: invalid quantity")
    unit = str(payload.get("unit") or "").strip()
    return RequestDraft(
        purpose=original.purpose,
        delivery_point=original.delivery_point,
        name=original.name,
        item_type=original.item_type,
        quantity=quantity,
        unit=unit if unit.casefold() in {item.casefold() for item in UNITS} else original.unit,
        quality_document=original.quality_document,
        procurement_deadline=original.procurement_deadline,
        planned_date=original.planned_date,
        project_reference=original.project_reference,
        category=original.category,
        design_article=original.design_article,
        link=None,  # a new supplier is looked for: SUPPLYMAP clears ССЫЛКА
        price=original.price,
        price_with_vat=original.price_with_vat,
        comment=f"Отмена дозавоза {payload.get('legacy_id') or ''}: новый поставщик".strip(),
    )


def dispatch_next_reorder_requested(now: datetime, uow: DispatchUnitOfWork) -> Request | None:
    """`applyBackorderCancellations_`: a cancelled backorder becomes a new request already in Procurement.

    Same initiator and item, the remaining quantity, a new System ID; SUPPLYMAP puts the row straight into
    Закупка «Новый», so the request is handed off in the same transaction — ERP (owner, 05.10) onto the desk of
    the buyer who cancelled the backorder, «В работе».
    """
    with uow:
        message = uow.outbox.next_unpublished("requests.reorder_requested", now)
        if message is None:
            return None

        def poison(reason: str) -> PoisonRequestMessage:
            uow.outbox.mark_failed(message.id, now, reason)
            uow.commit()
            return PoisonRequestMessage(message.id, reason)

        payload = message.payload
        try:
            original = uow.requests.get(UUID(str(payload.get("source_request_id"))))
            buyer = Actor(
                id=UUID(str(payload.get("buyer_id"))),
                name=str(payload.get("buyer_name") or ""),
                department=str(payload.get("buyer_department") or ""),
            )
        except ValueError:
            original = None
        if original is None:
            raise poison("Исходная заявка для отменённого дозавоза не найдена")
        existing = uow.requests.by_creation_key(original.actor.id, message.idempotency_key)
        if existing:
            uow.outbox.mark_published(message.id, now)
            uow.commit()
            return existing[0]
        try:
            (request,) = create_request_batch(
                [_reorder_draft(original, payload)],
                actor=original.actor,
                now=now,
                existing_legacy_ids=uow.requests.all_legacy_ids(),
                existing_designations=uow.requests.all_designations(),
                creation_key=message.idempotency_key,
                creation_fingerprint=message.idempotency_key,
            )
            handed_off = request.start_procurement(buyer)
            snapshot = build_procurement_requested_snapshot(handed_off, today=now.date())
        except ValidationError as error:
            raise poison(str(error)) from error
        uow.requests.add(handed_off)
        for event_type, actor, details in (
            ("request.created", original.actor, {
                "legacy_id": handed_off.legacy_id,
                "designation": handed_off.designation,
                "reorder_of": str(payload.get("legacy_id") or ""),
            }),
            ("request.sent_to_procurement", buyer, {"legacy_id": handed_off.legacy_id}),
        ):
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=handed_off.id,
                    event_type=event_type,
                    actor=actor,
                    occurred_at=now,
                    details=details,
                )
            )
        uow.outbox.add(
            OutboxMessage(
                id=uuid4(),
                topic="procurement.requested",
                idempotency_key=snapshot.idempotency_key,
                created_at=now,
                payload=_procurement_requested_payload(snapshot),
            )
        )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return handed_off
