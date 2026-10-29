"""OP-1: producers address users by id / audience; Operations delivers to active users only."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.infrastructure import IdentityLevelAccessRow, IdentityUserRow
from app.modules.operations.application import PoisonNotificationMessage, dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.modules.procurement.application import dispatch_next_procurement_requested, notify_initiators
from app.modules.procurement.domain import ProcurementStatus
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork as SqlAlchemyRequestsUnitOfWork
from app.modules.shipping.application import (
    AcceptGoodCommand,
    CreateShipmentCommand,
    RegisterGoodCommand,
    RequestedGood,
    UpdateDocumentsCommand,
    accept_good,
    create_shipment,
    register_good,
    update_documents,
)
from app.modules.shipping.domain import DeliveryTerms, ProjectAddressee, UpdType
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.shared.notifications import SUPPLY_AUDIENCE, TECHNOLOGIST_AUDIENCE, notification_requested
from app.shared.persistence import OutboxMessageRow, create_schema
from tests.factories import Actor

NOW = datetime(2026, 9, 29, 10, 0)
INITIATOR = Actor(name="Иванов Иван", department="РНД")
MANAGER = Actor(name="Петрова Мария", department="Офис")
BUYER = Actor(name="Домрачев Виктор", department="Офис")


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        for login, actor, role, active in (
            ("ivanov", INITIATOR, "staff", True),
            ("petrova", MANAGER, "pm", True),
            ("victor", BUYER, "buyer", True),
            ("artemiy", Actor(name="Технолог", department="Цех"), "admin", True),
            ("pm2", Actor(name="Второй менеджер", department="Офис"), "pm", True),
            ("left", Actor(name="Уволен", department="Офис"), "buyer", False),
            ("store", Actor(name="Кладовщик", department="Склад"), "receiver", True),
        ):
            session.add(IdentityUserRow(
                id=actor.id, login=login, email=f"{login}@example.test", name=actor.name,
                department=actor.department, role=role, site=None, active=active,
            ))
        # The level matrix the audiences read (the roles above map to levels by default): the buyer's level edits
        # «Лист закупки», the project manager's «Отгрузка», the storekeeper's «Отгрузка — склад».
        for level, section in (
            ("tech_lead", "procurement"), ("commercial", "shipping"), ("foreman", "shipping_goods"),
        ):
            session.add(IdentityLevelAccessRow(level=level, section=section, mode="edit"))
        session.commit()
    yield factory
    engine.dispose()


def _deliver(sessions) -> dict[str, list[str]]:
    """Drains notification.requested; returns recipient e-mail → titles."""
    while dispatch_next_notification(NOW, SqlAlchemyOperationsUnitOfWork(sessions)) is not None:
        pass
    inbox: dict[str, list[str]] = {}
    with SqlAlchemyOperationsUnitOfWork(sessions) as uow:
        for email in ("ivanov", "petrova", "victor", "artemiy", "pm2", "left", "store"):
            titles = [item.title for item in uow.notifications.list_for(f"{email}@example.test", 40)]
            if titles:
                inbox[email] = titles
    return inbox


def _publish(sessions, message) -> UUID:
    with SqlAlchemyOperationsUnitOfWork(sessions) as uow:
        uow.outbox.add(message)
        uow.commit()
    return message.id


def test_audiences_and_ids_resolve_to_active_users_only(sessions):
    _publish(sessions, notification_requested(
        key="t:audiences", now=NOW, title="Аудитории", body="", kind="info",
        audiences=(SUPPLY_AUDIENCE, TECHNOLOGIST_AUDIENCE), user_ids=(INITIATOR.id,),
    ))
    assert _deliver(sessions) == {"victor": ["Аудитории"], "artemiy": ["Аудитории"], "ivanov": ["Аудитории"]}


def test_unmatched_recipient_is_dropped_not_poisoned(sessions):
    """An id that matches nobody (NULL-backfilled rows never even publish) delivers to no one."""
    message_id = _publish(sessions, notification_requested(
        key="t:ghost", now=NOW, title="Никому", body="", kind="info", user_ids=(uuid4(),),
    ))
    assert dispatch_next_notification(NOW, SqlAlchemyOperationsUnitOfWork(sessions)) == 0
    with sessions() as session:
        row = session.get(OutboxMessageRow, message_id)
        assert row.published_at is not None and row.dead_lettered_at is None
    assert notification_requested(key="t:none", now=NOW, title="x", body="", kind="info", user_ids=(None,)) is None


def test_broken_recipient_id_is_poison(sessions):
    message = notification_requested(key="t:bad", now=NOW, title="x", body="", kind="info", audiences=(SUPPLY_AUDIENCE,))
    message = replace(message, payload={**message.payload, "recipient_user_ids": "not-a-uuid"})
    _publish(sessions, message)
    with pytest.raises(PoisonNotificationMessage):
        dispatch_next_notification(NOW, SqlAlchemyOperationsUnitOfWork(sessions))


def _procurement_line(sessions, initiator=INITIATOR) -> UUID:
    request = create_requests(
        CreateRequestsCommand(
            creation_key=f"op1:{initiator.name}",
            drafts=(RequestDraft(
                purpose="RND", delivery_point="г. Москва, ул. 6-я Радиальная, д. 17", name="Разъём", item_type="Комплектующие",
                quantity=Decimal("2"), unit="шт", quality_document="Нет",
                procurement_deadline=date(2026, 10, 5), planned_date=date(2026, 10, 10),
            ),),
            actor=initiator,
            now=NOW,
        ),
        SqlAlchemyRequestsUnitOfWork(sessions),
    )[0]
    handoff_requests(
        HandoffRequestsCommand(request_ids=(request.id,), actor=BUYER, now=NOW),
        SqlAlchemyRequestsUnitOfWork(sessions),
    )
    line = dispatch_next_procurement_requested(NOW, SqlAlchemyProcurementUnitOfWork(sessions))
    assert line.initiator_id == initiator.id
    return line.id


def _set_status(sessions, line_id: UUID, status: ProcurementStatus) -> None:
    with SqlAlchemyProcurementUnitOfWork(sessions) as uow:
        line = uow.procurement.get(line_id)
        uow.procurement.add(replace(line, status=status, version=line.version + 1))
        uow.commit()


def test_procurement_status_change_reaches_the_initiator_once_per_status(sessions):
    line_id = _procurement_line(sessions)
    uow = lambda: SqlAlchemyProcurementUnitOfWork(sessions)

    assert notify_initiators(NOW, uow()) == 0  # «Новый» is internal (NOTIFY_PURCHASE_STATUSES)
    _set_status(sessions, line_id, ProcurementStatus.APPROVAL)
    assert notify_initiators(NOW, uow()) == 1
    assert notify_initiators(NOW, uow()) == 0  # anti-duplicate: last notified status
    _set_status(sessions, line_id, ProcurementStatus.IN_WORK)  # a later buyer save keeps the mark…
    _set_status(sessions, line_id, ProcurementStatus.APPROVAL)
    assert notify_initiators(NOW, uow()) == 0  # …so the same status is not announced twice
    _set_status(sessions, line_id, ProcurementStatus.IN_TRANSIT)
    assert notify_initiators(NOW, uow()) == 1

    inbox = _deliver(sessions)
    legacy_id = sessions().get(ProcurementLineRow, line_id).legacy_id
    assert list(inbox) == ["ivanov"]
    assert sorted(inbox["ivanov"]) == [
        f"Заявка {legacy_id} — статус: В пути",
        f"Заявка {legacy_id} — статус: Согласование",
    ]


def test_procurement_line_without_initiator_user_is_marked_and_skipped(sessions):
    line_id = _procurement_line(sessions)
    with sessions() as session:
        session.get(ProcurementLineRow, line_id).initiator_id = None  # the backfill found no user
        session.commit()
    _set_status(sessions, line_id, ProcurementStatus.CANCELLED)
    assert notify_initiators(NOW, SqlAlchemyProcurementUnitOfWork(sessions)) == 0
    with sessions() as session:
        assert session.get(ProcurementLineRow, line_id).notified_status == "Отменено"
        assert session.scalars(
            select(OutboxMessageRow).where(OutboxMessageRow.topic == "notification.requested")
        ).all() == []


def _shipment(sessions, manager=MANAGER, upd_url="", upd_type=UpdType.PAPER):
    """Упакованная отправка ТК с № УПД и телефоном: трек вписывается, когда УПД и ТК на месте (владелец 29.10.2026)."""
    uow = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    with uow() as card_uow:  # менеджер уже решил «упаковывать»; само решение — не предмет этих тестов
        card_uow.addressees.add(replace(ProjectAddressee.empty("316К").decide_pack(True, manager.name, NOW), version=1))
        card_uow.commit()
    good = register_good(RegisterGoodCommand(
        project="316К", item_index="КБ1", customer="АО Заказчик", article="A-1", name="Жгут",
        ordered=Decimal("10"), unit="шт", note="", actor=manager, now=NOW,
    ), uow())
    accept_good(AcceptGoodCommand(
        good_id=good.id, expected_version=good.version, quantity=Decimal("10"),
        received_at=date(2026, 9, 29), actor=manager, now=NOW,
    ), uow())
    return create_shipment(CreateShipmentCommand(
        customer="АО Заказчик", receiver="Склад заказчика", shipping_point="Москва", contact="",
        phone="+7 900 000-00-00", terms=DeliveryTerms.CARRIER, carrier="СДЭК", deadline=None,
        upd_type=upd_type, upd_number="77", upd_date=None, upd_url=upd_url, note="",
        lines=(RequestedGood(good_id=good.id, quantity=Decimal("4")),), actor=manager, now=NOW,
    ), uow())


def _documents(sessions, shipment, **changes):
    values = {"upd_type": shipment.upd_type, "upd_number": shipment.upd_number,
              "upd_date": shipment.upd_date, "upd_url": shipment.upd_url,
              "track_number": shipment.track_number, **changes}
    return update_documents(UpdateDocumentsCommand(
        shipment_id=shipment.id, expected_version=shipment.version, actor=BUYER, now=NOW, **values,
    ), SqlAlchemyShippingUnitOfWork(sessions))


def test_track_number_goes_to_the_manager_and_upd_to_storekeepers(sessions):
    shipment = _shipment(sessions)
    assert shipment.manager_id == MANAGER.id
    shipment = _documents(sessions, shipment, upd_url="https://files.test/upd.pdf")
    shipment = _documents(sessions, shipment, track_number="TRK-1")
    _documents(sessions, shipment, track_number="TRK-1")  # unchanged track: silent

    inbox = _deliver(sessions)
    assert inbox == {
        "petrova": [f"Трек по отправке {shipment.number}"],
        "store": [f"УПД к отправке {shipment.number}"],
    }


def test_track_without_a_manager_user_goes_to_every_project_manager(sessions):
    shipment = _shipment(sessions, upd_type=UpdType.EDO)  # ЭДО: файл не нужен, УПД-уведомления нет
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        shipment = replace(shipment, manager_id=None)  # the backfill found no user for МЕНЕДЖЕР
        uow.shipments.add(shipment)
        uow.commit()
    _documents(sessions, shipment, track_number="TRK-2")
    title = f"Трек по отправке {shipment.number}"
    assert _deliver(sessions) == {"petrova": [title], "pm2": [title]}


def test_upd_given_with_the_new_shipment_reaches_storekeepers_once(sessions):
    shipment = _shipment(sessions, upd_url="https://files.test/upd-new.pdf")
    _documents(sessions, shipment, track_number="TRK-3")  # the same УПД link stays: no second УПД notice

    inbox = _deliver(sessions)
    assert inbox["store"] == [f"УПД к отправке {shipment.number}"]
    assert inbox["petrova"] == [f"Трек по отправке {shipment.number}"]


def test_shipment_without_upd_tells_nobody_at_creation(sessions):
    _shipment(sessions)
    assert _deliver(sessions) == {}
