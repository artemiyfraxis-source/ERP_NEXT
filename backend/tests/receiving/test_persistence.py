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
    quarantine_overdue_waiting,
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
                mass_100="0,123",
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
    # SK-6: СРОК ГОДНОСТИ and МАССА 1 ШТ (weighed as a hundred at incoming control) reach the stored lot.
    with receiving_factory() as uow:
        assert uow.receiving.get(line.id).unit_mass_g == Decimal("0.00123")
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        stored = uow.warehouse.get(lot.id)
    assert (stored.expiry_date, stored.unit_mass_g) == (date(2027, 1, 1), Decimal("0.00123"))
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


def test_quarantine_timer_reads_the_stored_act_date_and_persists_the_move():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        for number in (1, 2):
            session.add(
                OutboxMessageRow(
                    id=UUID(f"10000000-0000-0000-0000-00000000000{number}"),
                    topic="receiving.expected",
                    idempotency_key=f"procurement:{number}:receiving",
                    created_at=datetime(2026, 9, 21, 10, number),
                    payload={
                        "procurement_line_id": f"20000000-0000-0000-0000-00000000000{number}",
                        "legacy_id": f"SM-{number}",
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
    factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    actor = Actor(name="Приёмщик", department="ОТК")
    lines = [
        dispatch_next_receiving_expected(datetime(2026, 9, 21, 10, 5), factory()) for _ in range(2)
    ]
    for line, act_date in zip(lines, (date(2026, 9, 24), date(2026, 9, 25))):
        inspect_receiving(
            InspectReceivingCommand(
                line_id=line.id,
                expected_version=line.version,
                inspection=InspectionInput(
                    fact_quantity=Decimal("10"),
                    document_quantity=Decimal("10"),
                    ordered_quantity=Decimal("10"),
                    has_shipping_document=False,
                    quality_document_required=False,
                    quality_document_present=False,
                    expiry_date=None,
                    inspection_date=act_date,
                ),
                actor=actor,
                now=datetime.combine(act_date, datetime.min.time()),
            ),
            factory(),
        )

    moved = quarantine_overdue_waiting(datetime(2026, 9, 28, 9, 0), factory())

    assert [line.legacy_id for line in moved] == ["SM-1"]
    with sessions() as session:
        rows = {row.legacy_id: row for row in session.scalars(select(ReceivingLineRow))}
        assert rows["SM-1"].status == "Карантин"
        assert rows["SM-1"].inspected_on == date(2026, 9, 24)
        assert rows["SM-2"].status == "Ожидание"
        assert session.scalar(
            select(func.count()).select_from(AuditEventRow).where(
                AuditEventRow.event_type == "receiving.auto_quarantined"
            )
        ) == 1
    engine.dispose()
