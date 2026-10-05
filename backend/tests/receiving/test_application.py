from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.receiving.application import (
    AttachShippingDocumentsCommand,
    ConcurrentReceivingUpdate,
    InspectReceivingCommand,
    OpenVkRevisionCommand,
    PlaceReceivingCommand,
    PoisonReceivingMessage,
    build_putaway_print_sheet,
    attach_shipping_documents,
    consume_receiving_expected,
    dispatch_next_receiving_expected,
    inspect_receiving,
    open_vk_revision,
    place_receiving,
    quarantine_overdue_waiting,
)
from app.modules.receiving.domain import InspectionInput, ReceivingStatus, ReceivingValidationError
from app.shared.events import OutboxMessage
from tests.factories import Actor
from uuid import UUID


class Repository:
    def __init__(self):
        self.items = {}

    def by_source_idempotency_key(self, key):
        return next((item for item in self.items.values() if item.source_idempotency_key == key), None)

    def get(self, line_id):
        return self.items.get(line_id)

    def queue(self, limit=200, after_id=None):
        items = list(self.items.values())
        if after_id is not None:
            position = next(index for index, item in enumerate(items) if item.id == after_id)
            items = items[position + 1 :]
        return items[:limit]

    def waiting_inspected_by(self, day, limit=200):
        return [
            item
            for item in self.items.values()
            if item.status is ReceivingStatus.WAITING and item.inspected_on and item.inspected_on <= day
        ][:limit]

    def awaiting_placement(self, limit=500):
        return [
            item
            for item in self.items.values()
            if item.status is ReceivingStatus.ACCEPTED
            and not item.storage_location
            and not item.warehouse_event_emitted
        ][:limit]

    def all_grns(self):
        return [item.grn for item in self.items.values() if item.grn]

    def legacy_ids_starting_with(self, prefix):
        return [item.legacy_id for item in self.items.values() if item.legacy_id.startswith(prefix)]

    def act_numbers_with_prefixes(self, prefixes):
        prefixes = tuple(prefixes)
        return [
            number
            for item in self.items.values()
            for number in (item.vk_act_number, item.nc_act_number, item.complaint_act_number, item.use_permit_number)
            if number and number.startswith(prefixes)
        ]

    def add(self, line):
        self.items[line.id] = line


class AppendOnly:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class Outbox(AppendOnly):
    def __init__(self):
        super().__init__()
        self.published = set()
        self.failures = []

    def by_idempotency_key(self, key):
        return next((item for item in self.items if item.idempotency_key == key), None)

    def next_unpublished(self, topic, now):
        del now
        return next(
            (
                item
                for item in self.items
                if item.topic == topic
                and item.id not in self.published
                and all(failure[0] != item.id for failure in self.failures)
            ),
            None,
        )

    def mark_published(self, message_id, published_at):
        del published_at
        self.published.add(message_id)

    def mark_failed(self, message_id, failed_at, error):
        self.failures.append((message_id, failed_at, error))


class Uow:
    def __init__(self):
        self.receiving = Repository()
        self.audit = AppendOnly()
        self.outbox = Outbox()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1


def message():
    return OutboxMessage(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        topic="receiving.expected",
        idempotency_key="procurement:one:receiving:v1",
        created_at=datetime(2026, 9, 21, 10, 0),
        payload={
            "procurement_line_id": "20000000-0000-0000-0000-000000000001",
            "legacy_id": "SM-20260921-01",
            "warehouse": "ЦАР",
            "name": "Разъём",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "supplier": "ООО Поставка",
            "ordered_quantity": "10",
            "unit": "шт",
        },
    )


def inspection():
    return InspectionInput(
        fact_quantity=Decimal("10"),
        document_quantity=Decimal("10"),
        ordered_quantity=Decimal("10"),
        has_shipping_document=True,
        quality_document_required=False,
        quality_document_present=False,
        shipping_document_url="https://files.test/upd.pdf",
        expiry_date=date(2027, 1, 1),
        inspection_date=date(2026, 9, 21),
    )


def test_inspection_and_placement_emit_one_atomic_warehouse_event():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    actor = Actor(name="Приёмщик", department="ОТК")
    inspected = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=inspection(),
            actor=actor,
            now=datetime(2026, 9, 21, 11, 0),
        ),
        uow,
    )
    placed = place_receiving(
        PlaceReceivingCommand(
            line_id=line.id,
            expected_version=inspected.version,
            storage_location="1F4",
            actor=actor,
            now=datetime(2026, 9, 21, 11, 5),
        ),
        uow,
    )

    assert inspected.status is ReceivingStatus.ACCEPTED
    assert placed.warehouse_event_emitted is True
    assert [item.topic for item in uow.outbox.items] == ["warehouse.received"]
    assert uow.outbox.items[0].payload["grn"] == "FRX.26.0000001"
    assert [event.event_type for event in uow.audit.items] == [
        "receiving.expected",
        "receiving.inspected",
        "receiving.placed",
        "receiving.sent_to_warehouse",
    ]
    assert uow.commits == 3


def test_expected_event_retry_is_read_only():
    uow = Uow()
    first = consume_receiving_expected(message(), uow)
    retry = consume_receiving_expected(message(), uow)

    assert retry == first
    assert uow.commits == 1


def test_invalid_decimal_event_is_quarantined_instead_of_blocking_worker():
    uow = Uow()
    invalid = replace(
        message(),
        payload={**message().payload, "ordered_quantity": "not-a-number"},
    )
    uow.outbox.add(invalid)

    with pytest.raises(PoisonReceivingMessage):
        dispatch_next_receiving_expected(datetime(2026, 9, 21, 10, 1), uow)

    assert uow.outbox.failures[0][0] == invalid.id
    assert uow.commits == 1


def test_overdue_waiting_is_quarantined_by_the_system_with_notify_targets():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    actor = Actor(name="Приёмщик", department="ОТК")
    waiting = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=replace(
                inspection(),
                inspection_date=date(2026, 9, 24),
                manual_nonconformities=("Механическое повреждение / брак",),
                fact_quantity=Decimal("9"),
            ),
            actor=actor,
            now=datetime(2026, 9, 24, 11, 0),
        ),
        uow,
    )
    assert waiting.status is ReceivingStatus.WAITING
    commits = uow.commits

    assert quarantine_overdue_waiting(datetime(2026, 9, 25, 23, 0), uow) == []
    assert uow.commits == commits

    moved = quarantine_overdue_waiting(datetime(2026, 9, 28, 8, 0), uow)

    assert [item.id for item in moved] == [line.id]
    assert uow.receiving.get(line.id).status is ReceivingStatus.QUARANTINE
    assert uow.receiving.get(line.id).version == waiting.version + 1
    event = uow.audit.items[-1]
    assert event.event_type == "receiving.auto_quarantined"
    assert event.actor.department == "System"
    assert event.details["from_status"] == "Ожидание"
    assert event.details["to_status"] == "Карантин"
    assert event.details["waiting_since"] == "2026-09-24"
    assert event.details["notify"] == ["Технолог", "Снабжение"]
    # ВК «Ожидание» alerted at once; the automatic move alerts the same audiences again (OwnerTick.gs:573-581).
    alert, auto = uow.outbox.items
    assert alert.topic == auto.topic == "notification.requested"
    assert alert.payload["type"] == "nonconformity" and "в течение 2 раб. дн." in alert.payload["body"]
    assert auto.payload["type"] == "quarantine"
    assert auto.payload["title"] == f"Авто-Карантин (нет решения 2 р.д.) — {line.legacy_id}"
    assert "В «Ожидании» с 24.09.2026" in auto.payload["body"]
    assert alert.payload["recipient_audiences"] == auto.payload["recipient_audiences"] == "technologist,supply"
    assert uow.commits == commits + 1
    assert quarantine_overdue_waiting(datetime(2026, 9, 29, 8, 0), uow) == []


def _inspect(uow, line, now, **overrides):
    return inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=replace(inspection(), **overrides),
            actor=Actor(name="Приёмщик", department="ОТК"),
            now=now,
        ),
        uow,
    )


def _revise(uow, line, now):
    return open_vk_revision(
        OpenVkRevisionCommand(
            line_id=line.id,
            expected_version=line.version,
            actor=Actor(name="Приёмщик", department="ОТК"),
            now=now,
        ),
        uow,
    )


def test_new_vk_archives_line_opens_revision_and_audits_both_atomically():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    waiting = _inspect(uow, line, datetime(2026, 9, 21, 11, 0), manual_nonconformities=("Механическое повреждение / брак",))
    assert waiting.status is ReceivingStatus.WAITING

    archived, revision = _revise(uow, waiting, datetime(2026, 9, 22, 9, 0))

    assert uow.receiving.get(line.id).status is ReceivingStatus.VK_ARCHIVE
    assert uow.receiving.get(revision.id) == revision
    assert revision.legacy_id == "SM-20260921-01-В2"
    assert [event.event_type for event in uow.audit.items][-2:] == [
        "receiving.vk_archived",
        "receiving.vk_revision_opened",
    ]
    assert uow.audit.items[-2].details["revision_legacy_id"] == "SM-20260921-01-В2"
    assert uow.commits == 3

    accepted = _inspect(uow, revision, datetime(2026, 9, 22, 10, 0))
    assert accepted.status is ReceivingStatus.ACCEPTED
    assert accepted.grn != archived.grn


def test_new_vk_rejects_a_stale_form():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    inspected = _inspect(uow, line, datetime(2026, 9, 21, 11, 0))

    with pytest.raises(ConcurrentReceivingUpdate):
        _revise(uow, line, datetime(2026, 9, 22, 9, 0))
    assert uow.receiving.get(line.id) == inspected


def test_putaway_sheet_lists_only_accepted_lines_without_a_place():
    """WH-6: «Принято» без места на складе, ещё не переданное туда — и только это."""
    uow = Uow()
    unplaced = consume_receiving_expected(message(), uow)
    unplaced = _inspect(uow, unplaced, datetime(2026, 9, 21, 11, 0))
    assert unplaced.status is ReceivingStatus.ACCEPTED
    assert unplaced.storage_location is None

    waiting_line = consume_receiving_expected(
        replace(message(), id=UUID("10000000-0000-0000-0000-000000000002"),
                idempotency_key="procurement:two:receiving:v1",
                payload={**message().payload, "legacy_id": "SM-20260921-02"}),
        uow,
    )
    _inspect(uow, waiting_line, datetime(2026, 9, 21, 11, 0), manual_nonconformities=("Механическое повреждение / брак",))  # -> «Ожидание»

    already_placed = consume_receiving_expected(
        replace(message(), id=UUID("10000000-0000-0000-0000-000000000003"),
                idempotency_key="procurement:three:receiving:v1",
                payload={**message().payload, "legacy_id": "SM-20260921-03"}),
        uow,
    )
    already_placed = _inspect(uow, already_placed, datetime(2026, 9, 21, 11, 0))
    place_receiving(
        PlaceReceivingCommand(
            line_id=already_placed.id,
            expected_version=already_placed.version,
            storage_location="1F4",
            actor=Actor(name="Приёмщик", department="ОТК"),
            now=datetime(2026, 9, 21, 11, 5),
        ),
        uow,
    )

    sheet = build_putaway_print_sheet(uow, datetime(2026, 9, 21, 12, 0))

    assert len(sheet.rows) == 1
    assert sheet.rows[0][1] == unplaced.name
    assert sheet.rows[0][2] == unplaced.grn


def test_putaway_sheet_rejects_when_nothing_is_waiting_for_a_shelf():
    """WH-6: печатать нечего — тот же контракт, что и у FF-8 (пустая печать невозможна)."""
    uow = Uow()

    with pytest.raises(ReceivingValidationError):
        build_putaway_print_sheet(uow, datetime(2026, 9, 21, 12, 0))


def test_backorder_is_requested_once_per_delivery_across_vk_revisions():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    short = _inspect(
        uow, line, datetime(2026, 9, 21, 11, 0),
        document_quantity=Decimal("6"), fact_quantity=Decimal("6"),
    )
    _, revision = _revise(uow, short, datetime(2026, 9, 22, 9, 0))
    _inspect(
        uow, revision, datetime(2026, 9, 22, 10, 0),
        document_quantity=Decimal("6"), fact_quantity=Decimal("6"),
    )

    backorders = [item for item in uow.outbox.items if item.topic == "procurement.backorder_requested"]
    assert len(backorders) == 1
    assert backorders[0].payload["receiving_line_id"] == str(line.id)


def test_revision_requests_the_backorder_its_source_never_did():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    clean = _inspect(uow, line, datetime(2026, 9, 21, 11, 0), shipping_document_url=None)
    _, revision = _revise(uow, clean, datetime(2026, 9, 22, 9, 0))
    _inspect(
        uow, revision, datetime(2026, 9, 22, 10, 0),
        document_quantity=Decimal("7"), fact_quantity=Decimal("7"),
    )

    backorders = [item for item in uow.outbox.items if item.topic == "procurement.backorder_requested"]
    assert [item.payload["legacy_id"] for item in backorders] == ["SM-20260921-01-В2"]
    assert backorders[0].idempotency_key == f"receiving:{line.id}:backorder"


def _notifications(uow):
    return [item for item in uow.outbox.items if item.topic == "notification.requested"]


def test_missing_upd_accepts_the_line_without_any_alert():
    # Owner 05.10.2026: УПД comes later by ЭДО — «Принято» with a ГРН, a note only, nobody is notified.
    uow = Uow()
    line = consume_receiving_expected(message(), uow)

    inspected = _inspect(uow, line, datetime(2026, 9, 21, 11, 0), shipping_document_url=None)

    assert inspected.status is ReceivingStatus.ACCEPTED
    assert inspected.grn
    assert inspected.nonconformities == ()
    assert inspected.shipping_document_pending is True
    assert _notifications(uow) == []
    assert quarantine_overdue_waiting(datetime(2026, 9, 30, 6, 0), uow) == []


@pytest.mark.parametrize(
    ("change", "audiences"),
    [
        # Receiving.gs:771-788 via quarNotifyTargets_: брак → технолог (аудитория «Технолог»: суперадмин и админ)…
        ({"manual_nonconformities": ("Механическое повреждение / брак",)}, "technologist"),
        # …anything else → снабжение (редактируют «Лист закупки»).
        # A shortfall is accepted now (owner, 05.10.2026) but supply still hears of it.
        ({"fact_quantity": Decimal("9")}, "supply"),
        ({"fact_quantity": Decimal("8"), "document_quantity": Decimal("8")}, "supply"),
    ],
)
def test_nonconformity_notifies_by_nature(change, audiences):
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    inspected = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=replace(inspection(), **change),
            actor=Actor(name="Приёмщик", department="ОТК"),
            now=datetime(2026, 9, 21, 11, 0),
        ),
        uow,
    )
    (note,) = _notifications(uow)
    assert note.payload["recipient_audiences"] == audiences
    assert note.payload["recipient_user_ids"] == ""
    assert note.payload["type"] == "nonconformity"
    assert note.payload["reference"] == inspected.legacy_id
    assert f"статус «{inspected.status.value}»" in note.payload["body"]


@pytest.mark.parametrize(
    "change",
    [{}, {"fact_quantity": Decimal("11")}],  # clean, and «Излишек» alone: accepted, nobody is alerted
)
def test_accepted_inspection_notifies_nobody(change):
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    inspected = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=replace(inspection(), **change),
            actor=Actor(name="Приёмщик", department="ОТК"),
            now=datetime(2026, 9, 21, 11, 0),
        ),
        uow,
    )
    assert inspected.status is ReceivingStatus.ACCEPTED
    assert _notifications(uow) == []


def test_bulk_upd_audits_each_changed_line_in_one_commit():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    commits = uow.commits

    result = attach_shipping_documents(
        AttachShippingDocumentsCommand(
            lines=((line.id, line.version),),
            url=None,
            number="УПД-5",
            document_date=date(2026, 9, 20),
            actor=Actor(name="Приёмщик", department="ОТК"),
            now=datetime(2026, 9, 21, 11, 0),
        ),
        uow,
    )

    assert result[0].version == line.version + 1 and result[0].status is line.status
    event = uow.audit.items[-1]
    assert event.event_type == "receiving.document_attached"
    assert event.details == {
        "legacy_id": line.legacy_id,
        "document_number": "УПД-5",
        "document_date": "2026-09-20",
        "shipping_document_url": None,
    }
    assert uow.commits == commits + 1
