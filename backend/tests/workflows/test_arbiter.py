"""Арбитр ERP (ARCH-TRACK-3, C2): события модулей → задачи модулям, идемпотентно через outbox; сам закрывает сделанное."""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import outbox_worker
from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    MarkReadyCommand,
    VersionedFulfillmentCommand,
    bind_lot,
    complete_kit,
    create_fulfillment,
    issue_fulfillment,
    mark_ready,
)
from app.modules.fulfillment.domain import FulfillmentLine
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.operations.application import CloseProjectTaskCommand, close_project_task
from app.modules.operations.domain import ProjectTask, ProjectTaskStatus
from app.modules.operations.infrastructure import ProjectTaskRow, SqlAlchemyOperationsUnitOfWork
from app.modules.production.application import LaunchProductionCommand, launch_production
from app.modules.production.domain import ProductionOperation
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.shipping.application import (
    AcceptGoodCommand,
    CreateShipmentCommand,
    RegisterGoodCommand,
    RequestedGood,
    accept_good,
    create_shipment,
    register_good,
)
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.application import (
    ChangeProjectTechcardCommand,
    CreateProjectTechcardCommand,
    RebuildProjectTechcardCommand,
    create_project_techcard,
    issue_project_techcard,
    rebuild_project_techcard,
)
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.events import AuditEvent
from app.shared.persistence import AuditEventRow, OutboxMessageRow, SqlAlchemyAuditRepository, create_schema
from app.workflows.arbiter import (
    AUTO_CLOSER,
    TOPIC,
    PoisonTriggerMessage,
    Trigger,
    apply_trigger,
    close_followed_up,
    dispatch_next_trigger,
    feed_triggers,
)
from app.workflows.project_track import project_track
from tests.factories import Actor

PROJECT = "708К.4"
ACTOR = Actor(name="Технолог", department="Производство")
MASTER = Actor(name="Мастер", department="Производство")


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, 0)


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield SimpleNamespace(
        sessions=sessions,
        techcards=lambda: SqlAlchemyTechcardsUnitOfWork(sessions),
        fulfillment=lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
        production=lambda: SqlAlchemyProductionUnitOfWork(sessions),
        shipping=lambda: SqlAlchemyShippingUnitOfWork(sessions),
        operations=lambda: SqlAlchemyOperationsUnitOfWork(sessions),
    )
    engine.dispose()


# ── Действия модулей — их собственными сценариями, со своими событиями журнала ──


def issue_techcard(erp, index="КБ1", now=at(20)):
    card = create_project_techcard(CreateProjectTechcardCommand(
        project=PROJECT, item_index=index, product_name="Жгут питания", source_calculation="", deadline=None,
        actor=ACTOR, now=now,
    ), erp.techcards())
    card = rebuild_project_techcard(RebuildProjectTechcardCommand(
        card_id=card.id, expected_version=card.version, actor=ACTOR, now=now,
        document={"operations": [{"code": "10", "connector": "A", "name": "Резка"}]},
    ), erp.techcards())
    return issue_project_techcard(ChangeProjectTechcardCommand(
        card_id=card.id, expected_version=card.version, actor=ACTOR, now=now,
    ), erp.techcards())


def issue_kit(erp, purpose=f"{PROJECT} · КБ1 · Жгут питания", now=at(22)):
    lot = StockLot.create(
        warehouse="ЦАР", grn=f"FRX.26.{uuid4().int % 10**7:07d}", package="П1", name="Корпус Molex",
        item_type="Комплектующие", purpose="Склад", unit="шт", quantity=Decimal("10"), storage_location="1F4",
        source_receiving_ids=(f"receiving-{uuid4()}",),
    )
    with erp.fulfillment() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    kit = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"arbiter:{uuid4()}", purpose=purpose, deadline=date(2026, 10, 20),
        lines=(FulfillmentLine.create(article="43025-0400", name="Корпус Molex", unit="шт", requirement=Decimal("4")),),
        actor=ACTOR, now=now,
    ), erp.fulfillment())
    kit = bind_lot(BindLotCommand(
        request_id=kit.id, expected_version=kit.version, line_id=kit.lines[0].id, lot_id=lot.id, actor=ACTOR, now=now,
    ), erp.fulfillment())
    kit = complete_kit(VersionedFulfillmentCommand(kit.id, kit.version, ACTOR, now), erp.fulfillment())
    kit = mark_ready(MarkReadyCommand(
        request_id=kit.id, expected_version=kit.version, handout_location="Полка 1", actor=ACTOR, now=now,
    ), erp.fulfillment())
    return issue_fulfillment(VersionedFulfillmentCommand(kit.id, kit.version, ACTOR, now), erp.fulfillment())


def register(erp, index="КБ1"):
    return register_good(RegisterGoodCommand(
        project=PROJECT, item_index=index, customer="ООО Заказчик", article=f"{PROJECT}{index}", name="Жгут",
        ordered=Decimal("10"), unit="шт", note="", actor=ACTOR, now=at(19),
    ), erp.shipping())


def accept(erp, good, now=at(25)):
    with erp.shipping() as uow:
        good = uow.goods.get(good.id)
    return accept_good(AcceptGoodCommand(
        good_id=good.id, expected_version=good.version, quantity=Decimal("1"), received_at=now.date(),
        actor=ACTOR, now=now,
    ), erp.shipping())


def launch(erp, kit=None, item="КБ1", now=at(23)):
    operation = ProductionOperation.create(
        position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
        seconds_per_unit=Decimal("60"), setup_seconds=Decimal("0"), quantity=2,
    )
    return launch_production(LaunchProductionCommand(
        idempotency_key=f"arbiter:{uuid4()}", project=PROJECT, item=item, name="Жгут", quantity=2,
        fulfillment_request_id=kit.id if kit is not None else None, deadline=date(2026, 10, 20),
        operations=(operation,), actor=MASTER, now=now,
    ), erp.production())


def ship(erp, good, now=at(26)):
    return create_shipment(CreateShipmentCommand(
        customer="ООО Заказчик", receiver="ООО Заказчик", shipping_point="Москва, склад 3", contact="", phone="",
        terms=None, carrier="", deadline=None, upd_type=None, upd_number="", upd_date=None, upd_url="", note="",
        lines=(RequestedGood(good.id, Decimal("1")),), actor=ACTOR, now=now, decide_pack=True,
    ), erp.shipping())


# ── Арбитр ───────────────────────────────────────────────────────────────


def run(erp, now=at(30)):
    """Один проход воркера: события → очередь арбитра → задачи."""
    fed = feed_triggers(now, erp.sessions)
    handled = []
    while (dispatched := dispatch_next_trigger(now, erp.sessions)) is not None:
        handled.append(dispatched)
    return fed, handled


def tasks(erp, **filters):
    with erp.operations() as uow:
        return uow.project_tasks.list(**filters)


def rows(items):
    return [(task.kind, task.module, task.title, task.project, task.item_index, task.status.value) for task in items]


def event_id(erp, event_type):
    with erp.sessions() as session:
        return session.scalar(select(AuditEventRow.id).where(AuditEventRow.event_type == event_type))


def test_each_trigger_creates_exactly_one_task_for_its_module(erp):
    issue_techcard(erp, now=at(20))
    issue_kit(erp, now=at(22))
    accept(erp, register(erp), now=at(25))

    fed, handled = run(erp)

    assert fed == 3 and len(handled) == 3
    assert rows(tasks(erp)) == [
        ("plan_item", "production", "Поставить в план: 708К.4 КБ1", PROJECT, "КБ1", "open"),
        ("launch_task", "production", "Задание можно запускать: 708К.4 КБ1", PROJECT, "КБ1", "open"),
        ("ship_goods", "shipping", "Оформить отгрузку: 708К.4 КБ1", PROJECT, "КБ1", "open"),
    ]
    by_kind = {task.kind: task for task in tasks(erp)}
    for kind, event_type in (("plan_item", "techcards.project_issued"), ("launch_task", "fulfillment.issued"),
                             ("ship_goods", "shipping.good_accepted")):
        assert by_kind[kind].source_event_id == event_id(erp, event_type)
        assert by_kind[kind].source_type == event_type
    # Время задачи — время события, а не обработки.
    assert [task.created_at for task in tasks(erp)] == [at(20), at(22), at(25)]


def test_other_events_of_the_same_modules_are_not_triggers(erp):
    card = create_project_techcard(CreateProjectTechcardCommand(
        project=PROJECT, item_index="КБ1", product_name="Жгут", source_calculation="", deadline=None,
        actor=ACTOR, now=at(20),
    ), erp.techcards())
    assert card is not None
    register(erp)

    assert run(erp) == (0, [])
    assert tasks(erp) == []


def test_replaying_the_same_event_creates_no_task(erp):
    issue_techcard(erp)
    run(erp)
    first = tasks(erp)

    # Повторная подача: событие уже в очереди — второй раз не встаёт.
    assert feed_triggers(at(30), erp.sessions) == 0
    # Сообщение переотправлено (сбой после записи задачи) — задача та же.
    with erp.sessions() as session:
        session.get(OutboxMessageRow, first[0].source_event_id).published_at = None
        session.commit()
    replay = dispatch_next_trigger(at(30), erp.sessions)
    assert replay is not None and replay.task is None
    # Outbox почищен и событие пришло заново под другим ключом — всё равно ни одной новой задачи.
    with erp.sessions() as session:
        row = session.get(AuditEventRow, first[0].source_event_id)
        trigger = Trigger(row.id, row.event_type, row.aggregate_id, row.occurred_at, dict(row.details))
        session.add(OutboxMessageRow(
            id=uuid4(), topic=TOPIC, idempotency_key=f"replay:{row.id}", created_at=at(30), payload=trigger.payload(),
            attempt_count=0,
        ))
        session.commit()
        assert apply_trigger(session, trigger) is None
    assert dispatch_next_trigger(at(30), erp.sessions).task is None

    assert tasks(erp) == first


def test_the_database_refuses_a_second_task_from_the_same_event(erp):
    issue_techcard(erp)
    run(erp)
    task = tasks(erp)[0]

    with erp.sessions() as session:
        session.add(ProjectTaskRow(**{
            **ProjectTask.create(
                project=PROJECT, item_index="КБ2", module="production", kind=task.kind, title="Дубль",
                source_event_id=task.source_event_id, source_type=task.source_type, subject_id=None, created_at=at(30),
            ).__dict__, "status": "open",
        }))
        with pytest.raises(IntegrityError):
            session.commit()


def test_one_open_task_per_item_until_it_is_closed(erp):
    good = register(erp)
    accept(erp, good, now=at(24))
    accept(erp, good, now=at(25))
    run(erp)
    assert len(tasks(erp)) == 1

    close_project_task(CloseProjectTaskCommand(tasks(erp)[0].id, ACTOR, at(26)), erp.operations())
    accept(erp, good, now=at(27, 9))
    run(erp)

    assert [(task.status.value, task.created_at) for task in tasks(erp)] == [("done", at(24)), ("open", at(27, 9))]


def test_tasks_close_themselves_when_the_follow_up_happens(erp):
    issue_techcard(erp, now=at(20))
    kit = issue_kit(erp, now=at(22))
    good = register(erp)
    accept(erp, good, now=at(25))
    run(erp)
    assert close_followed_up(at(30), erp.sessions) == 0

    launch(erp, kit, now=at(26))
    assert close_followed_up(at(30, 11), erp.sessions) == 2

    by_kind = {task.kind: task for task in tasks(erp)}
    assert by_kind["plan_item"].status is ProjectTaskStatus.DONE
    assert by_kind["launch_task"].status is ProjectTaskStatus.DONE
    assert (by_kind["launch_task"].closed_by, by_kind["launch_task"].closed_at) == (AUTO_CLOSER, at(30, 11))
    assert by_kind["ship_goods"].is_open

    ship(erp, good, now=at(27))
    assert close_followed_up(at(30, 12), erp.sessions) == 1
    assert all(not task.is_open for task in tasks(erp))
    assert close_followed_up(at(30, 13), erp.sessions) == 0


def test_launch_for_the_project_and_item_closes_the_task_without_the_kit_link(erp):
    issue_kit(erp, now=at(22))
    run(erp)

    launch(erp, kit=None, item="708К.4.КБ1", now=at(23))

    assert close_followed_up(at(30), erp.sessions) == 1


def test_a_follow_up_before_the_trigger_does_not_close_a_new_task(erp):
    good = register(erp)
    accept(erp, good, now=at(24))
    run(erp)
    ship(erp, good, now=at(25))
    assert close_followed_up(at(30), erp.sessions) == 1

    accept(erp, good, now=at(27))
    run(erp)

    assert close_followed_up(at(30, 11), erp.sessions) == 0
    assert [task.status.value for task in tasks(erp)] == ["done", "open"]


def test_no_task_when_the_follow_up_already_happened_before_the_arbiter_ran(erp):
    kit = issue_kit(erp, now=at(22))
    launch(erp, kit, now=at(23))

    fed, handled = run(erp)

    assert fed == 1 and [item.task for item in handled] == [None]
    assert tasks(erp) == []


def test_a_kit_without_a_project_gets_no_task_and_its_event_is_done(erp):
    issue_kit(erp, purpose="Ремонт стенда", now=at(22))

    fed, handled = run(erp)

    assert fed == 1 and handled[0].task is None
    assert tasks(erp) == []
    with erp.sessions() as session:
        message = session.get(OutboxMessageRow, handled[0].message_id)
        assert message.published_at is not None and message.dead_lettered_at is None


def test_a_box_handed_from_the_shop_floor_also_asks_for_shipping(erp):
    good = register(erp, "КБ2")
    box_id = uuid4()
    with erp.sessions() as session:
        # Событие ровно того вида, что пишет `handoff_box` (production/execution.py): агрегат — коробка.
        SqlAlchemyAuditRepository(session).add(AuditEvent(
            id=uuid4(), aggregate_id=box_id, event_type="production.box_handed_to_finished_goods", actor=MASTER,
            occurred_at=at(28), details={"finished_good_id": str(good.id), "grn": "ГП-1", "quantity": "2"},
        ))
        session.commit()

    run(erp)

    assert rows(tasks(erp)) == [("ship_goods", "shipping", "Оформить отгрузку: 708К.4 КБ2", PROJECT, "КБ2", "open")]
    assert tasks(erp)[0].subject_id == good.id


def test_a_broken_trigger_is_dead_lettered(erp):
    message_id = uuid4()
    with erp.sessions() as session:
        session.add(OutboxMessageRow(
            id=message_id, topic=TOPIC, idempotency_key=f"broken:{message_id}", created_at=at(30),
            payload={"event_type": "fulfillment.issued"}, attempt_count=0,
        ))
        session.commit()

    with pytest.raises(PoisonTriggerMessage) as error:
        dispatch_next_trigger(at(30), erp.sessions)

    assert error.value.message_id == message_id
    with erp.sessions() as session:
        assert session.get(OutboxMessageRow, message_id).dead_lettered_at is not None
    assert dispatch_next_trigger(at(30), erp.sessions) is None


def test_the_worker_feeds_dispatches_and_closes(erp, monkeypatch):
    kit = issue_kit(erp, now=at(22))

    outbox_worker.run_arbiter(erp.sessions)
    outbox_worker.drain_available(erp.sessions)

    assert [task.kind for task in tasks(erp, status="open")] == ["launch_task"]
    with erp.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow).where(
            OutboxMessageRow.topic == TOPIC, OutboxMessageRow.published_at.is_(None),
        )) == 0

    launch(erp, kit, now=at(23))
    # Автозакрытие — не чаще раза в ARBITER_CLOSE_INTERVAL_SECONDS: только что было, ждёт.
    monkeypatch.setattr(outbox_worker, "_last_arbiter_close", outbox_worker.time.monotonic())
    assert outbox_worker.run_arbiter(erp.sessions) == 0
    monkeypatch.setattr(outbox_worker, "_last_arbiter_close", float("-inf"))
    assert outbox_worker.run_arbiter(erp.sessions) == 1
    assert tasks(erp, status="open") == []


def test_the_track_shows_tasks_as_their_own_section(erp):
    issue_techcard(erp, now=at(20))
    run(erp)

    entries = project_track(PROJECT, techcards=erp.techcards, tasks=erp.operations)
    track = [(item.module, item.kind, item.ref, item.status, item.item_index) for item in entries]
    assert track[-2:] == [
        ("Техкарты", "Техкарта выдана", f"{PROJECT}КБ1", "Выдана", "КБ1"),
        ("Задачи", "Поставить в план: 708К.4 КБ1", "Производство", "Открыта", "КБ1"),
    ]

    close_project_task(CloseProjectTaskCommand(tasks(erp)[0].id, MASTER, at(21)), erp.operations())
    entries = project_track("708K.4", tasks=erp.operations)
    assert [(item.module, item.status) for item in entries] == [("Задачи", "Закрыта")]
