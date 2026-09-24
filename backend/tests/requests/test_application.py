from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft, RequestStatus, ValidationError
from tests.factories import Actor


def draft(name: str = "Разъём") -> RequestDraft:
    return RequestDraft(
        purpose="RND",
        delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
        name=name,
        item_type="Комплектующие",
        quantity=Decimal("2"),
        unit="шт",
        quality_document="Нет",
        procurement_deadline=date(2026, 9, 18),
        planned_date=date(2026, 9, 25),
    )


class FakeRequestRepository:
    def __init__(self):
        self.items = {}

    def by_creation_key(self, actor_id, creation_key):
        return sorted(
            (
                item
                for item in self.items.values()
                if item.actor.id == actor_id and item.creation_key == creation_key
            ),
            key=lambda item: item.creation_index,
        )

    def all_legacy_ids(self):
        return [item.legacy_id for item in self.items.values()]

    def all_designations(self):
        return [item.designation for item in self.items.values()]

    def add(self, request):
        self.items[request.id] = request

    def get(self, request_id):
        return self.items.get(request_id)


class FakeAppendOnlyRepository:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class FakeOutboxRepository(FakeAppendOnlyRepository):
    def by_idempotency_key(self, idempotency_key):
        return next(
            (item for item in self.items if item.idempotency_key == idempotency_key),
            None,
        )


class FakeUnitOfWork:
    def __init__(self):
        self.requests = FakeRequestRepository()
        self.audit = FakeAppendOnlyRepository()
        self.outbox = FakeOutboxRepository()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1


def test_create_is_idempotent_and_writes_audit_in_same_commit():
    uow = FakeUnitOfWork()
    command = CreateRequestsCommand(
        creation_key="web-form:8d892697",
        drafts=(draft(), draft("Кабель")),
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15, 10, 30),
    )

    first = create_requests(command, uow)
    retry = create_requests(command, uow)

    assert [item.id for item in retry] == [item.id for item in first]
    assert len(uow.requests.items) == 2
    assert len(uow.audit.items) == 2
    assert all(event.event_type == "request.created" for event in uow.audit.items)
    assert uow.commits == 1


def test_handoff_is_idempotent_and_writes_procurement_audit_and_outbox_atomically():
    uow = FakeUnitOfWork()
    request = create_requests(
        CreateRequestsCommand(
            creation_key="web-form:one",
            drafts=(draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow,
    )[0]

    command = HandoffRequestsCommand(
        request_ids=(request.id,),
        actor=Actor(name="Петров", department="Закупки"),
        now=datetime(2026, 9, 15, 11, 0),
    )
    first = handoff_requests(command, uow)
    retry = handoff_requests(command, uow)

    assert retry == first
    assert uow.requests.get(request.id).status is RequestStatus.IN_WORK
    assert [event.event_type for event in uow.audit.items] == [
        "request.created",
        "request.sent_to_procurement",
    ]
    assert len(uow.outbox.items) == 1
    assert uow.outbox.items[0].topic == "procurement.requested"
    assert uow.outbox.items[0] == first[0]
    assert first[0].payload["calculated_name"] == "Разъём"
    assert first[0].payload["calculated_quantity"] == "2"
    assert "procurement_deadline" not in first[0].payload
    assert uow.commits == 2, "create and first handoff each commit exactly once; retry is read-only"


def test_create_rejects_reusing_idempotency_key_for_different_payload():
    uow = FakeUnitOfWork()
    base = CreateRequestsCommand(
        creation_key="web-form:same-key",
        drafts=(draft(),),
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15, 10, 30),
    )
    create_requests(base, uow)

    with pytest.raises(ValidationError, match="другой пачки"):
        create_requests(
            CreateRequestsCommand(
                creation_key=base.creation_key,
                drafts=(draft("Другая позиция"),),
                actor=base.actor,
                now=base.now,
            ),
            uow,
        )

    assert len(uow.requests.items) == 1
    assert uow.commits == 1


def test_creation_idempotency_key_is_scoped_to_authenticated_identity():
    uow = FakeUnitOfWork()
    first = create_requests(
        CreateRequestsCommand(
            creation_key="browser:shared-key",
            drafts=(draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow,
    )
    second = create_requests(
        CreateRequestsCommand(
            creation_key="browser:shared-key",
            drafts=(draft(),),
            actor=Actor(name="Сидоров", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow,
    )

    assert first[0].id != second[0].id
    assert len(uow.requests.items) == 2


def test_handoff_rejects_missing_request_without_partial_commit():
    uow = FakeUnitOfWork()

    with pytest.raises(KeyError, match="Request not found"):
        handoff_requests(
            HandoffRequestsCommand(
                request_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
                actor=Actor(name="Петров", department="Закупки"),
                now=datetime(2026, 9, 15, 11, 0),
            ),
            uow,
        )

    assert uow.commits == 0
    assert not uow.audit.items
    assert not uow.outbox.items
