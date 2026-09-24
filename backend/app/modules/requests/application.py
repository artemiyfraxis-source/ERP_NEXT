"""Transactional request use cases expressed against persistence ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage

from .domain import (
    Actor,
    ProcurementRequestedSnapshot,
    Request,
    RequestDraft,
    RequestStatus,
    ValidationError,
    create_request_batch,
    build_procurement_requested_snapshot,
)


class RequestRepository(Protocol):
    def by_creation_key(self, actor_id: UUID, creation_key: str) -> Sequence[Request]: ...
    def all_legacy_ids(self) -> Sequence[str]: ...
    def all_designations(self) -> Sequence[str]: ...
    def add(self, request: Request) -> None: ...
    def get(self, request_id: UUID) -> Request | None: ...
    def recent(self, limit: int) -> Sequence[Request]: ...


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


@dataclass(frozen=True)
class CreateRequestsCommand:
    creation_key: str
    drafts: Sequence[RequestDraft]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class HandoffRequestsCommand:
    request_ids: Sequence[UUID]
    actor: Actor
    now: datetime


def _creation_fingerprint(command: CreateRequestsCommand) -> str:
    payload = {
        "actor": asdict(command.actor),
        "drafts": [asdict(item) for item in command.drafts],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _procurement_requested_payload(
    line: ProcurementRequestedSnapshot,
) -> dict[str, str | None]:
    """Self-contained integration event; Procurement never reads Requests tables."""
    return {
        "source_request_id": str(line.source_request_id),
        "legacy_id": line.legacy_id,
        "request_date": line.request_date.isoformat(),
        "initiator": line.initiator,
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
    }


def create_requests(command: CreateRequestsCommand, uow: UnitOfWork) -> list[Request]:
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
        )
        for request in requests:
            uow.requests.add(request)
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
                    },
                )
            )
        uow.commit()
        return requests


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
            requests[request_id] = request

        messages: dict[UUID, OutboxMessage] = {}
        pending: list[tuple[Request, ProcurementRequestedSnapshot, OutboxMessage]] = []
        for request_id in request_ids:
            request = requests[request_id]
            if request.status is RequestStatus.NEW:
                request = request.start_procurement(command.actor)
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
                    event_type="request.sent_to_procurement",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={"legacy_id": request.legacy_id},
                )
            )
            uow.outbox.add(message)
            messages[request.id] = message
        uow.commit()
        return [messages[request_id] for request_id in request_ids]


def recent_requests(limit: int, uow: UnitOfWork) -> list[Request]:
    with uow:
        return list(uow.requests.recent(limit))
