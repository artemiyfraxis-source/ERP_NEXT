from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
    my_requests,
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

    def by_initiator(self, actor_id, limit, offset, search=""):
        mine = sorted(
            (
                item
                for item in self.items.values()
                if item.actor.id == actor_id and search.casefold() in item.name.casefold()
            ),
            key=lambda item: (item.created_at, item.creation_index),
            reverse=True,
        )
        return mine[offset : offset + limit]

    def count_by_initiator(self, actor_id, search=""):
        return sum(
            1
            for item in self.items.values()
            if item.actor.id == actor_id and search.casefold() in item.name.casefold()
        )


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
    # Owner, 05.10: the one who pressed «В закупку» gets the line «В работе» — the event carries them.
    buyer = command.actor
    assert {key: first[0].payload[key] for key in ("buyer_id", "buyer_name", "buyer_department")} == {
        "buyer_id": str(buyer.id), "buyer_name": "Петров", "buyer_department": "Закупки",
    }
    assert uow.commits == 2, "create and first handoff each commit exactly once; retry is read-only"


def test_handoff_event_without_a_known_buyer_carries_nobody():
    from dataclasses import replace

    from app.modules.requests.application import _procurement_requested_payload
    from app.modules.requests.domain import build_procurement_requested_snapshot

    uow = FakeUnitOfWork()
    request = create_requests(
        CreateRequestsCommand(
            creation_key="web-form:nobody", drafts=(draft(),), actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow,
    )[0]
    started = request.start_procurement(Actor(name="Петров", department="Закупки"))
    # A row saved before the buyer's id was kept: name only, no id — the line stays «Новый» in Procurement.
    legacy = replace(started, procurement_actor=replace(started.procurement_actor, id=None))

    payload = _procurement_requested_payload(build_procurement_requested_snapshot(legacy, today=date(2026, 9, 15)))

    assert (payload["buyer_id"], payload["buyer_name"], payload["buyer_department"]) == (None, None, None)


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


def test_retransfer_emits_a_new_event_per_call_and_needs_request_in_work():
    uow = FakeUnitOfWork()
    request = create_requests(
        CreateRequestsCommand(
            creation_key="web-form:retransfer",
            drafts=(draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow,
    )[0]
    buyer = Actor(name="Петров", department="Закупки")

    with pytest.raises(ValidationError, match="«В работе»"):
        handoff_requests(
            HandoffRequestsCommand(
                request_ids=(request.id,), actor=buyer, now=datetime(2026, 9, 15, 11, 0), retransfer=True
            ),
            uow,
        )
    assert uow.outbox.items == []

    first = handoff_requests(
        HandoffRequestsCommand(request_ids=(request.id,), actor=buyer, now=datetime(2026, 9, 15, 11, 0)),
        uow,
    )
    resend = HandoffRequestsCommand(
        request_ids=(request.id,), actor=buyer, now=datetime(2026, 9, 16, 9, 0), retransfer=True
    )
    second = handoff_requests(resend, uow)
    third = handoff_requests(resend, uow)

    keys = [message[0].idempotency_key for message in (first, second, third)]
    assert len(set(keys)) == 3, "every repeated transfer is a new revision, as in SUPPLYMAP"
    assert uow.requests.get(request.id).status is RequestStatus.IN_WORK
    assert uow.requests.get(request.id).version == 4
    assert [event.event_type for event in uow.audit.items] == [
        "request.created",
        "request.sent_to_procurement",
        "request.resent_to_procurement",
        "request.resent_to_procurement",
    ]
    assert second[0].payload["legacy_id"] == request.legacy_id


def test_my_requests_filters_by_actor_id_newest_first_and_reports_total():
    """RQ-MINE: server-side filter by initiator id (not a name match), paginated."""
    uow = FakeUnitOfWork()
    ivanov = Actor(name="Иванов", department="РНД")
    petrov = Actor(name="Петров", department="Закупки")
    create_requests(
        CreateRequestsCommand(
            creation_key="mine:1",
            drafts=(draft("Разъём"), draft("Кабель")),
            actor=ivanov,
            now=datetime(2026, 9, 15, 10, 0),
        ),
        uow,
    )
    create_requests(
        CreateRequestsCommand(
            creation_key="mine:2",
            drafts=(draft("Труба"),),
            actor=petrov,
            now=datetime(2026, 9, 15, 11, 0),
        ),
        uow,
    )

    first_page, total = my_requests(ivanov.id, limit=1, offset=0, uow=uow)
    second_page, total_again = my_requests(ivanov.id, limit=1, offset=1, uow=uow)

    assert total == total_again == 2
    assert [item.name for item in first_page] == ["Кабель"]
    assert [item.name for item in second_page] == ["Разъём"]
    assert all(item.actor.id == ivanov.id for item in first_page + second_page)
