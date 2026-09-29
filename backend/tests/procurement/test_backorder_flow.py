"""Дозавоз end to end on SQLite: paid line → short first inspection → `-Д` → resend or new request."""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.procurement.application import (
    CancelBackorderCommand,
    ConfirmBackorderCommand,
    MarkProcurementPaidCommand,
    PoisonOutboxMessage,
    RecordInvoiceCommand,
    SaveProcurementFactsCommand,
    SaveProcurementMoneyCommand,
    BuildSupplierRequestsCommand,
    TakeProcurementInWorkCommand,
    cancel_procurement_backorder,
    confirm_procurement_backorder,
    dispatch_next_backorder_requested,
    dispatch_next_procurement_requested,
    mark_procurement_paid,
    record_invoice,
    save_procurement_facts,
    save_procurement_money,
    build_supplier_requests,
    take_procurement_in_work,
)
from app.modules.procurement.domain import MoneyEntry, ProcurementFacts, ProcurementStatus
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.money import MoneyInput
from app.modules.receiving.application import (
    InspectReceivingCommand,
    dispatch_next_receiving_expected,
    inspect_receiving,
)
from app.modules.receiving.domain import InspectionInput, ReceivingStatus
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    dispatch_next_reorder_requested,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft, RequestStatus
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork as SqlAlchemyRequestsUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.persistence import OutboxMessageRow, create_schema
from app.workflows.production_plan import _supply
from tests.factories import Actor

NOW = datetime(2026, 9, 21, 10, 0)
INITIATOR = Actor(name="Иванов", department="КБ")
BUYER = Actor(name="Петров", department="Закупки")
RECEIVER = Actor(name="Приёмщик", department="ОТК")


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield SimpleNamespace(
        sessions=sessions,
        requests=lambda: SqlAlchemyRequestsUnitOfWork(sessions),
        procurement=lambda: SqlAlchemyProcurementUnitOfWork(sessions),
        receiving=lambda: SqlAlchemyReceivingUnitOfWork(sessions),
    )
    engine.dispose()


def paid_procurement_line(db):
    (request,) = create_requests(
        CreateRequestsCommand(
            creation_key="batch-1",
            drafts=[
                RequestDraft(
                    purpose="RND",
                    delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
                    name="Разъём SMA",
                    item_type="Комплектующие",
                    quantity=Decimal("10"),
                    unit="шт",
                    quality_document="Нет",
                    procurement_deadline=date(2026, 10, 1),
                    planned_date=date(2026, 10, 5),
                ),
            ],
            actor=INITIATOR,
            now=NOW,
        ),
        db.requests(),
    )
    handoff_requests(HandoffRequestsCommand([request.id], BUYER, NOW), db.requests())
    line = dispatch_next_procurement_requested(NOW, db.procurement())
    line = take_procurement_in_work(TakeProcurementInWorkCommand((line.id,), BUYER, NOW), db.procurement())[0]
    line = save_procurement_facts(
        SaveProcurementFactsCommand(
            line.id,
            ProcurementFacts(supplier="ООО Поставка", actual_quantity=Decimal("10"), actual_unit="шт"),
            line.version, BUYER, NOW,
        ),
        db.procurement(),
    )
    line = build_supplier_requests(BuildSupplierRequestsCommand(BUYER, NOW), db.procurement()).lines[0]
    line = save_procurement_money(
        SaveProcurementMoneyCommand(
            line.id, line.version,
            MoneyEntry(
                amounts=MoneyInput(currency="RUB", price=Decimal("100"), vat_rate=Decimal("22")),
                invoice_item_name="Разъём SMA", invoice_number="17",
            ),
            BUYER, NOW,
        ),
        db.procurement(),
    )
    line = record_invoice(
        RecordInvoiceCommand(line.id, line.version, "https://files.test/invoice.pdf", date(2026, 9, 25), BUYER, NOW),
        db.procurement(),
    )
    line = mark_procurement_paid(MarkProcurementPaidCommand(line.id, line.version, BUYER, NOW), db.procurement())
    return request, line


def inspect(db, line, document_quantity: str):
    return inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=InspectionInput(
                fact_quantity=Decimal(document_quantity),
                document_quantity=Decimal(document_quantity),
                ordered_quantity=Decimal("0"),
                has_shipping_document=True,
                quality_document_required=False,
                quality_document_present=False,
                shipping_document_url="https://files.test/upd.pdf",
                expiry_date=date(2027, 1, 1),
                inspection_date=date(2026, 9, 24),
            ),
            actor=RECEIVER,
            now=datetime(2026, 9, 24, 12, 0),
        ),
        db.receiving(),
    )


def backorder_messages(db) -> int:
    with db.sessions() as session:
        return session.scalar(
            select(func.count()).select_from(OutboxMessageRow).where(
                OutboxMessageRow.topic == "procurement.backorder_requested"
            )
        )


def short_delivery(db):
    request, line = paid_procurement_line(db)
    expected = dispatch_next_receiving_expected(NOW, db.receiving())
    inspected = inspect(db, expected, "7.5")
    assert inspected.status is ReceivingStatus.WAITING
    assert inspected.backorder_quantity == Decimal("2.5")
    inspect(db, inspected, "7.5")  # re-inspection of «Ожидание» never records a second backorder
    assert backorder_messages(db) == 1
    backorder = dispatch_next_backorder_requested(datetime(2026, 9, 24, 12, 1), db.procurement())
    return request, line, backorder


def test_short_paid_delivery_becomes_backorder_and_is_expected_again_after_resend(db):
    _, line, backorder = short_delivery(db)

    assert backorder.legacy_id == f"{line.legacy_id}-Д"
    assert backorder.status is ProcurementStatus.APPROVAL
    assert backorder.actual_quantity == Decimal("2.5")
    assert (backorder.invoice_url, backorder.paid_at, backorder.invoice_number) == (
        line.invoice_url, line.paid_at, "17",
    )
    assert (backorder.money.price_with_vat, backorder.money.sum) == (Decimal("122.00"), Decimal("305.00"))
    assert dispatch_next_backorder_requested(datetime(2026, 9, 24, 12, 2), db.procurement()) is None

    confirmed = confirm_procurement_backorder(
        ConfirmBackorderCommand(backorder.id, backorder.version, date(2026, 10, 2), BUYER, NOW),
        db.procurement(),
    )
    resend = dispatch_next_receiving_expected(datetime(2026, 9, 25, 9, 0), db.receiving())

    assert confirmed.status is ProcurementStatus.IN_TRANSIT
    assert (resend.legacy_id, resend.ordered_quantity, resend.procurement_line_id) == (
        backorder.legacy_id, Decimal("2.5"), backorder.id,
    )
    short_again = inspect(db, resend, "2")
    assert short_again.backorder_quantity == Decimal("0.5")
    backorder_of_backorder = dispatch_next_backorder_requested(datetime(2026, 10, 2, 12, 0), db.procurement())
    assert backorder_of_backorder.legacy_id == f"{line.legacy_id}-Д-Д"


def test_cancelled_backorder_becomes_new_request_already_in_procurement(db):
    request, line, backorder = short_delivery(db)

    cancelled = cancel_procurement_backorder(
        CancelBackorderCommand(backorder.id, backorder.version, BUYER, NOW), db.procurement(),
    )
    reorder = dispatch_next_reorder_requested(datetime(2026, 9, 25, 9, 0), db.requests())
    fresh = dispatch_next_procurement_requested(datetime(2026, 9, 25, 9, 1), db.procurement())

    assert cancelled.status is ProcurementStatus.CANCELLED
    assert reorder.legacy_id != request.legacy_id and not reorder.legacy_id.endswith("-Д")
    assert (reorder.quantity, reorder.unit, reorder.status) == (Decimal("2.5"), "шт", RequestStatus.IN_WORK)
    assert (reorder.actor.id, reorder.procurement_actor.id) == (INITIATOR.id, BUYER.id)
    assert reorder.link is None and backorder.legacy_id in (reorder.comment or "")
    assert (fresh.legacy_id, fresh.status, fresh.calculated_quantity) == (
        reorder.legacy_id, ProcurementStatus.NEW, Decimal("2.5"),
    )
    assert fresh.money.sum is None and fresh.invoice_url is None
    assert dispatch_next_reorder_requested(datetime(2026, 9, 25, 9, 2), db.requests()) is None


def test_backorder_event_for_unknown_line_is_quarantined(db):
    with db.sessions() as session:
        session.add(
            OutboxMessageRow(
                id=uuid4(),
                topic="procurement.backorder_requested",
                idempotency_key="receiving:missing:backorder",
                created_at=NOW,
                payload={"procurement_line_id": str(uuid4()), "backorder_quantity": "1"},
            )
        )
        session.commit()

    with pytest.raises(PoisonOutboxMessage, match="не найдена"):
        dispatch_next_backorder_requested(NOW, db.procurement())
    with db.sessions() as session:
        row = session.scalars(select(OutboxMessageRow)).one()
    assert row.published_at is None and row.last_error


def test_supply_summary_counts_backorder_as_its_own_position():
    request = SimpleNamespace(
        id=uuid4(), project_reference="591К.3", purpose="RND", status=RequestStatus.IN_WORK,
        planned_date=date(2026, 10, 5), link=None,
    )
    original = SimpleNamespace(
        source_request_id=request.id, status=ProcurementStatus.IN_TRANSIT, expected_arrival=date(2026, 9, 25),
        planned_date=date(2026, 10, 5),
    )
    backorder = SimpleNamespace(
        source_request_id=request.id, status=ProcurementStatus.APPROVAL, expected_arrival=None,
        planned_date=date(2026, 10, 5),
    )

    summary = _supply([request], [original, backorder])["591К.3"]

    assert summary.total == 2
    assert summary.transit == 1
