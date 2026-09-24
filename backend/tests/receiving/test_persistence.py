from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.receiving.application import (
    InspectReceivingCommand,
    PlaceReceivingCommand,
    dispatch_next_receiving_expected,
    inspect_receiving,
    place_receiving,
)
from app.modules.receiving.domain import InspectionInput
from app.modules.receiving.infrastructure import ReceivingLineRow, SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse.application import dispatch_next_warehouse_received
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, StockLotRow
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from tests.factories import Actor


def test_receiving_to_warehouse_is_transactional_and_persistent():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        session.add(
            OutboxMessageRow(
                id=UUID("10000000-0000-0000-0000-000000000001"),
                topic="receiving.expected",
                idempotency_key="procurement:one:receiving",
                created_at=datetime(2026, 9, 21, 10, 0),
                payload={
                    "procurement_line_id": "20000000-0000-0000-0000-000000000001",
                    "legacy_id": "SM-1",
                    "warehouse": "ЦАР",
                    "name": "Разъём",
                    "item_type": "Комплектующие",
                    "purpose": "FRX.001",
                    "supplier": "ООО Поставка",
                    "ordered_quantity": "10",
                    "unit": "шт",
                },
            )
        )
        session.commit()
    receiving_factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    line = dispatch_next_receiving_expected(datetime(2026, 9, 21, 10, 1), receiving_factory())
    actor = Actor(name="Приёмщик", department="ОТК")
    inspected = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=InspectionInput(
                fact_quantity=Decimal("10"),
                document_quantity=Decimal("10"),
                ordered_quantity=Decimal("999"),
                has_shipping_document=True,
                quality_document_required=False,
                quality_document_present=False,
                shipping_document_url="https://files.test/upd.pdf",
                expiry_date=date(2027, 1, 1),
                inspection_date=date(2026, 9, 21),
            ),
            actor=actor,
            now=datetime(2026, 9, 21, 11, 0),
        ),
        receiving_factory(),
    )
    placed = place_receiving(
        PlaceReceivingCommand(
            line_id=line.id,
            expected_version=inspected.version,
            storage_location="1F4",
            actor=actor,
            now=datetime(2026, 9, 21, 11, 1),
        ),
        receiving_factory(),
    )
    lot = dispatch_next_warehouse_received(
        datetime(2026, 9, 21, 11, 2),
        SqlAlchemyWarehouseUnitOfWork(sessions),
    )

    assert placed.warehouse_event_emitted is True
    assert lot.grn == "FRX.26.0000001"
    assert lot.balance == Decimal("10")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ReceivingLineRow)) == 1
        assert session.scalar(select(func.count()).select_from(StockLotRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 5
        assert session.scalar(
            select(func.count()).select_from(OutboxMessageRow).where(
                OutboxMessageRow.published_at.is_not(None)
            )
        ) == 2
    engine.dispose()
