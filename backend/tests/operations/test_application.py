from datetime import datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.operations.application import dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.persistence import create_schema


def test_notification_outbox_delivery_is_idempotent():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyOperationsUnitOfWork(sessions)
    message = OutboxMessage(
        id=uuid4(),
        topic="notification.requested",
        idempotency_key="notification:test",
        created_at=datetime(2026, 9, 22, 12, 0),
        payload={
            "recipients": "staff@example.test;STAFF@example.test,second@example.test",
            "title": "Поставка прибыла",
            "body": "Проверьте входной контроль",
            "type": "status",
            "reference": "SM-1",
        },
    )
    with factory() as uow:
        uow.outbox.add(message)
        uow.commit()

    assert dispatch_next_notification(datetime(2026, 9, 22, 12, 1), factory()) == 2
    assert dispatch_next_notification(datetime(2026, 9, 22, 12, 2), factory()) is None
    with factory() as uow:
        assert len(uow.notifications.list_for("staff@example.test", 40)) == 1
        assert len(uow.notifications.list_for("second@example.test", 40)) == 1
    engine.dispose()
